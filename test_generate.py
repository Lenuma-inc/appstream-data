import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import struct
import zlib

import generate as gen


POLICY = {
    "exclude_packages": ["*-git"], "keep_packages": [],
    "binary_suffixes": ["-bin"], "hide_binary_if_base_exists": True,
    "exclude_components": [],
}


def package_content():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress((b"\0" + b"\x55\x88\xcc" * 64) * 64)) + chunk(b"IEND", b"")
    metadata = b'''<component type="desktop-application">
      <id>org.example.demo</id><metadata_license>CC0-1.0</metadata_license>
      <project_license>MIT</project_license><name>Demo</name><summary>Test application</summary>
      <description><p>A demonstration application used to verify that repository metadata,
      icons and package names survive the complete AppStream generation pipeline.</p></description>
      <url type="homepage">https://example.org/</url>
      <launchable type="desktop-id">org.example.demo.desktop</launchable>
      <provides><binary>demo</binary></provides>
      <content_rating type="oars-1.1"/>
    </component>'''
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name, content in {
            ".PKGINFO": b"pkgname = demo\npkgver = 1.0-1\narch = any\n",
            "usr/share/metainfo/org.example.demo.metainfo.xml": metadata,
            "usr/share/applications/org.example.demo.desktop": b"[Desktop Entry]\nType=Application\nName=Demo\nComment=Test application\nExec=demo\nIcon=demo\nCategories=Utility;\n",
            "usr/share/icons/hicolor/64x64/apps/demo.png": png,
            "usr/bin/demo": b"not needed by the metadata generator",
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return data.getvalue()


PACKAGE_CONTENT = package_content()


def record(name, paths=None, architecture="any", content=PACKAGE_CONTENT, epoch=False):
    version = "1:1.0-1" if epoch else "1.0-1"
    info = {"NAME": [name], "VERSION": [version], "ARCH": [architecture],
            "FILENAME": [f"{name}-1.0-1-any.pkg.tar.zst"],
            "SHA256SUM": [hashlib.sha256(content).hexdigest()], "CSIZE": [str(len(content))],
            "URL": ["https://example.org/"], "LICENSE": ["MIT"]}
    desc = "".join(f"%{k}%\n" + "\n".join(v) + "\n\n" for k, v in info.items()).encode()
    paths = paths if paths is not None else [
        f"usr/share/applications/org.example.{name}.desktop",
        f"usr/share/metainfo/org.example.{name}.metainfo.xml",
        f"usr/share/icons/hicolor/64x64/apps/{name}.png",
    ]
    files = ("%FILES%\n" + "\n".join(paths) + "\n\n").encode()
    return f"{name}-{version}", {"desc": desc, "files": files, "fields": info}


CATALOG = b'''<?xml version="1.0" encoding="UTF-8"?>
<components version="1.0" origin="lunaos-repo">
  <component type="desktop-application">
    <id>org.example.Demo</id><pkgname>demo</pkgname>
    <name>Demo</name><summary>Test application</summary>
    <description><p>A small demonstration application for checking the catalog pipeline.</p></description>
    <launchable type="desktop-id">org.example.Demo.desktop</launchable>
    <icon type="cached" width="64" height="64">demo.png</icon>
    <categories><category>Utility</category></categories>
  </component>
</components>'''


class PolicyTests(unittest.TestCase):
    def test_git_and_binary_policy_with_cross_repository_base(self):
        records = dict(record(n) for n in ("foo-git", "foo-bin", "only-bin", "ordinary"))
        result, excluded = gen.apply_policy(records, {"foo", "foo-git", "foo-bin", "only-bin", "ordinary"}, POLICY)
        self.assertEqual({r["fields"]["NAME"][0] for r in result.values()}, {"only-bin", "ordinary"})
        self.assertIn("foo-git", excluded)
        self.assertIn("base package", excluded["foo-bin"])

    def test_keep_overrides_exclusions_and_binary_policy(self):
        policy = dict(POLICY, keep_packages=["foo-git", "foo-bin"])
        records = dict(record(n) for n in ("foo-git", "foo-bin"))
        result, excluded = gen.apply_policy(records, {"foo", "foo-git", "foo-bin"}, policy)
        self.assertEqual(result, records)
        self.assertEqual(excluded, {})

    def test_selects_icons_metainfo_and_fonts_but_not_libraries(self):
        records = dict([
            record("app"), record("icons", ["usr/share/icons/hicolor/64x64/apps/demo.png"]),
            record("font", ["usr/share/fonts/demo.ttf"]),
            record("meta", ["usr/share/metainfo/org.example.Demo.metainfo.xml"]),
            record("library", ["usr/lib/demo.so"]),
            record("wrongarch", architecture="aarch64"),
        ])
        result = gen.select_packages(records, "x86_64")
        self.assertEqual({r["fields"]["NAME"][0] for r in result.values()}, {"app", "icons", "font", "meta"})


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.args = argparse.Namespace(work=self.base / "work", output=self.base / "dist",
                                      jobs=2, policy_data=POLICY, explain=False,
                                      version="20261022", release=1, available={"demo", "demo-git"})

    def test_database_epoch_roundtrip(self):
        path = self.base / "repo.files"
        records = dict([record("demo", epoch=True)])
        gen.write_database(path, records, True)
        self.assertEqual(gen.read_database(path), records)

    def test_real_fixture_file_index_matches_package(self):
        _, entry = record("demo")
        with tarfile.open(fileobj=io.BytesIO(PACKAGE_CONTENT)) as archive:
            contents = set(archive.getnames())
        declared = set(gen.fields(entry["files"])["FILES"])
        self.assertTrue(declared <= contents, declared - contents)

    def test_checksum_failure_does_not_replace_existing_download(self):
        source, destination = self.base / "source", self.base / "destination"
        source.write_bytes(b"corrupt")
        destination.write_bytes(b"old")
        with self.assertRaises(RuntimeError):
            gen.fetch([source.as_uri()], destination, "a" * 64)
        self.assertEqual(destination.read_bytes(), b"old")
        self.assertFalse((self.base / "destination.part").exists())

    @unittest.skipUnless(shutil.which("bsdtar"), "Needs bsdtar")
    def test_resource_package_keeps_metadata_symlink_targets_outside_usr_share(self):
        source = self.base / "source.pkg.tar.gz"
        destination = self.base / "resources.pkg.tar.zst"
        targets = {
            "opt/demo/demo.desktop": b"[Desktop Entry]\nName=Demo\nComment=Test app\n",
            "opt/demo/demo.metainfo.xml": b"<component><id>org.example.Demo</id></component>",
            "opt/demo/demo.appdata.xml": b"<component><id>org.example.Other</id></component>",
            "opt/demo/demo.png": b"icon",
        }
        links = {
            "usr/share/applications/demo.desktop": "/opt/demo/demo.desktop",
            "usr/share/metainfo/demo.metainfo.xml": "../../../opt/demo/demo.metainfo.xml",
            "usr/share/appdata/demo.appdata.xml": "/opt/demo/demo.appdata.xml",
            "usr/share/icons/hicolor/64x64/apps/demo.png": "/opt/demo/demo.png",
        }
        with tarfile.open(source, "w:gz") as archive:
            for name, target in links.items():
                member = tarfile.TarInfo(name)
                member.type, member.linkname = tarfile.SYMTYPE, target
                archive.addfile(member)
            for name, content in dict(targets, **{"opt/demo/demo": b"application binary"}).items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        gen.resource_package(source, destination)
        with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(
                ["bsdtar", "-cf", "-", "@" + str(destination)])), mode="r:") as archive:
            self.assertNotIn("opt/demo/demo", archive.getnames())
            for name, content in targets.items():
                self.assertEqual(archive.extractfile(name).read(), content)
            for name, target in links.items():
                self.assertEqual(archive.getmember(name).linkname, target)

    def test_mirror_fallback_and_verified_cache(self):
        source, destination = self.base / "source", self.base / "destination"
        source.write_bytes(b"valid")
        checksum = gen.digest(source)
        gen.fetch([(self.base / "missing").as_uri(), source.as_uri()], destination, checksum)
        source.unlink()
        gen.fetch([source.as_uri()], destination, checksum)
        self.assertEqual(destination.read_bytes(), b"valid")

    def test_icon_archive_rejects_traversal_and_symlinks(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)):
            path = self.base / "icons.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "/tmp/escape"
                archive.addfile(member, io.BytesIO(b""))
            with self.assertRaises(ValueError):
                gen.unpack_icons(path, self.base / "icons")
        self.assertFalse((self.base / "escape").exists())

    def test_duplicate_ids_excluded(self):
        source = self.base / "Components-x86_64.xml.gz"
        destination = self.base / "catalog.xml.gz"
        root = ET.fromstring(CATALOG)
        root.append(ET.fromstring(ET.tostring(root[0])))
        unique = ET.fromstring(ET.tostring(root[0]))
        unique.find("id").text = "org.example.Unique"
        root.append(unique)
        source.write_bytes(gzip.compress(ET.tostring(root)))
        with patch.object(gen.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            _, count = gen.write_catalog(source, destination, 20, {"demo"})
        self.assertEqual(count, 1)
        exported = ET.fromstring(gzip.decompress(destination.read_bytes()))
        self.assertEqual([c.findtext("id") for c in exported], ["org.example.Unique"])
        root.remove(unique)
        source.write_bytes(gzip.compress(ET.tostring(root)))
        with self.assertRaisesRegex(ValueError, "empty"):
            gen.write_catalog(source, destination, 20, {"demo"})

    def test_catalog_normalizes_multiline_summaries_and_applies_component_policy(self):
        source = self.base / "Components-x86_64.xml.gz"
        destination = self.base / "catalog.xml.gz"
        root = ET.fromstring(CATALOG)
        root[0].find("summary").text = "Test\n  application\twith tabs"
        translated = ET.SubElement(root[0], "summary", {"{http://www.w3.org/XML/1998/namespace}lang": "ru"})
        translated.text = "Тестовое\n  приложение"
        addon = ET.SubElement(root, "component", {"type": "addon"})
        ET.SubElement(addon, "id").text = "codeblocks-contrib"
        ET.SubElement(addon, "pkgname").text = "demo"
        source.write_bytes(gzip.compress(ET.tostring(root)))
        with patch.object(gen.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            _, count = gen.write_catalog(source, destination, 20, {"demo"}, ["codeblocks-contrib"])
        self.assertEqual(count, 1)
        exported = ET.fromstring(gzip.decompress(destination.read_bytes()))
        self.assertEqual([s.text for s in exported[0].findall("summary")],
                         ["Test application with tabs", "Тестовое приложение"])
        self.assertEqual(exported[0].findtext("id"), "org.example.Demo")

    def test_invalid_components_are_removed_and_catalog_is_revalidated(self):
        source = self.base / "Components-x86_64.xml.gz"
        destination = self.base / "catalog.xml.gz"
        root = ET.fromstring(CATALOG)
        addon = ET.SubElement(root, "component", {"type": "addon"})
        ET.SubElement(addon, "id").text = "broken-addon"
        ET.SubElement(addon, "pkgname").text = "demo"
        source.write_bytes(gzip.compress(ET.tostring(root)))
        bad = "---\nIssues: [{severity: error, component: broken-addon, tag: cid-is-not-rdns}]\n"
        good = "---\nIssues: [{severity: warning, component: org.example.Demo, tag: url-homepage-missing}]\n"
        results = [subprocess.CompletedProcess([], 3, report, "") for report in (bad, good)]
        with patch.object(gen.subprocess, "run", side_effect=results) as run:
            self.assertEqual(gen.write_catalog(source, destination, 20, {"demo"}),
                             ("lunaos-repo", 1))
        self.assertEqual(run.call_count, 2)
        exported = ET.fromstring(gzip.decompress(destination.read_bytes()))
        self.assertEqual([c.findtext("id") for c in exported], ["org.example.Demo"])
        self.assertEqual(destination.with_suffix(".validation.yaml").read_text(), bad + good)

    def test_validation_cannot_remove_every_component(self):
        source = self.base / "Components-x86_64.xml.gz"
        source.write_bytes(gzip.compress(CATALOG))
        report = "---\nIssues: [{severity: error, component: org.example.Demo}]\n"
        result = subprocess.CompletedProcess([], 3, report, "")
        with patch.object(gen.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "empty after validation"):
                gen.write_catalog(source, self.base / "catalog.xml.gz", 20, {"demo"})

    def test_missing_id_and_unknown_package_rejected(self):
        source = self.base / "Components-x86_64.xml.gz"
        destination = self.base / "catalog.xml.gz"
        root = ET.fromstring(CATALOG)
        root[0].remove(root[0].find("id"))
        source.write_bytes(gzip.compress(ET.tostring(root)))
        with self.assertRaisesRegex(ValueError, "Missing"):
            gen.write_catalog(source, destination, 20, {"demo"})
        source.write_bytes(gzip.compress(CATALOG))
        with self.assertRaisesRegex(ValueError, "Unknown package"):
            gen.write_catalog(source, destination, 20, {"other"})

    def test_validation_with_unquoted_hint_preserves_severity_checks(self):
        source = self.base / "Components-x86_64.xml.gz"
        destination = self.base / "catalog.xml.gz"
        source.write_bytes(gzip.compress(CATALOG))
        cases = (
            "Do you like audio books? Then lets get cozy!,",
            '"Use a virtual reality\n        headset such as Gear VR or Oculus Rift.",',
            "Use a virtual reality\n        headset such as Gear VR or Oculus Rift,",
        )
        for hint, severity in ((hint, severity) for hint in cases
                               for severity in ("warning", "error")):
            with self.subTest(hint=hint, severity=severity):
                report = (
                    "---\nIssues:\n  - {\n"
                    f"      hint: {hint}\n"
                    f"      severity: {severity},\n"
                    "    }\n"
                )
                result = subprocess.CompletedProcess([], 3, report, "")
                with patch.object(gen.subprocess, "run", return_value=result):
                    if severity == "error":
                        with self.assertRaisesRegex(RuntimeError, "AppStream validation failed"):
                            gen.write_catalog(source, destination, 20, {"demo"})
                    else:
                        self.assertEqual(gen.write_catalog(source, destination, 20, {"demo"}),
                                         ("lunaos-repo", 1))
                        self.assertEqual(destination.with_suffix(".validation.yaml").read_text(),
                                         report)

    def test_validation_failure_shows_errors_and_saves_full_report(self):
        source = self.base / "Components-x86_64.xml.gz"
        destination = self.base / "catalog.xml.gz"
        source.write_bytes(gzip.compress(CATALOG))
        report = gen.yaml.safe_dump({"Issues": [
            {"severity": "warning", "tag": "url-homepage-missing"},
            {"severity": "error", "component": "unknown.component", "line": 8,
             "tag": "invalid-description", "explanation": "Invalid description markup."},
        ]})
        result = subprocess.CompletedProcess([], 3, report, "")
        with patch.object(gen.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as caught:
                gen.write_catalog(source, destination, 20, {"demo"})
        self.assertIn("exit 3", str(caught.exception))
        self.assertIn("unknown.component:8: invalid-description", str(caught.exception))
        self.assertNotIn("url-homepage-missing", str(caught.exception))
        self.assertEqual(destination.with_suffix(".validation.yaml").read_text(), report)

    @unittest.skipUnless(shutil.which("appstreamcli") and shutil.which("makepkg"), "Needs Arch tools")
    def test_sync_export_and_real_makepkg(self):
        mirror = self.base / "mirror"
        mirror.mkdir()
        records = dict([record("demo"), record("demo-git")])
        gen.write_database(mirror / "lunaos-repo.files", records, True)
        for r in records.values():
            (mirror / r["fields"]["FILENAME"][0]).write_bytes(PACKAGE_CONTENT)
        workspace = self.args.work / "lunaos-repo"
        workspace.mkdir(parents=True)
        shutil.copyfile(mirror / "lunaos-repo.files", workspace / "input.files")
        settings = {"package": "lunaos-appstream-data", "priority": 20, "servers": [mirror.as_uri()]}
        gen.sync_repo("lunaos-repo", settings, self.args, "x86_64")
        repo = workspace / "archive/lunaos-repo/os/x86_64"
        names = gen.database_names(repo / "lunaos-repo.db", "x86_64")
        self.assertEqual(names, {"demo"})
        self.assertTrue((repo / "lunaos-repo.files.tar.gz").is_file())
        self.assertTrue((repo / "lunaos-repo.db.tar.gz").is_file())
        self.assertEqual((repo / "lunaos-repo.files").resolve(),
                         (repo / "lunaos-repo.files.tar.gz").resolve())
        self.assertTrue((repo / "demo-1.0-1-any.pkg.tar.zst").is_symlink())
        listing = subprocess.check_output(["bsdtar", "-tf", str(repo / "demo-1.0-1-any.pkg.tar.zst")], text=True)
        self.assertIn("usr/share/metainfo/org.example.demo.metainfo.xml", listing)
        self.assertNotIn("usr/bin/demo", listing)
        staged = gen.read_database(repo / "lunaos-repo.files")
        staged_sha = next(iter(staged.values()))["fields"]["SHA256SUM"][0]
        self.assertEqual(staged_sha, gen.digest(repo / "demo-1.0-1-any.pkg.tar.zst"))
        manifest = json.loads((workspace / "manifest.json").read_text())
        self.assertEqual(manifest["packages"][0]["sha256"], hashlib.sha256(PACKAGE_CONTENT).hexdigest())

        # Export fixture models asgen's catalog/icon boundary; validation and makepkg are real.
        exported = workspace / "export/data/rolling/lunaos-repo"
        exported.mkdir(parents=True)
        (exported / "Components-x86_64.xml.gz").write_bytes(gzip.compress(CATALOG))
        with tarfile.open(exported / "icons-64x64.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo("64x64/demo.png")
            member.size = 4
            archive.addfile(member, io.BytesIO(b"icon"))
        (workspace / "generation.json").write_text(json.dumps({
            "manifest_sha256": gen.digest(workspace / "manifest.json"), "generator": "fixture"}))
        gen.package_repo("lunaos-repo", settings, self.args)
        output = self.args.output / "lunaos-appstream-data"
        pkgbuild = (output / "PKGBUILD").read_text()
        self.assertNotIn("@SHA256@", pkgbuild)
        source = output / "lunaos-appstream-data-20261022.tar.gz"
        self.assertIn(gen.digest(source), pkgbuild)
        self.assertTrue((output / ".SRCINFO").is_file())
        if not __import__("os").getuid() == 0:
            subprocess.run(["makepkg", "--nodeps", "--nocheck", "--noconfirm", "--nosign",
                            "--config", str(gen.ROOT / "makepkg-test.conf")], cwd=output, check=True)
            packages = list(output.glob("*.pkg.tar.gz"))
            self.assertEqual(len(packages), 1)
            listing = subprocess.check_output(["bsdtar", "-tf", str(packages[0])], text=True)
            self.assertIn("usr/share/swcatalog/xml/lunaos-repo.xml.gz", listing)
            self.assertIn("usr/share/swcatalog/icons/lunaos-repo/64x64/demo.png", listing)
            self.assertNotIn("archlinux.xml", listing)

    @unittest.skipUnless(shutil.which("appstream-generator"), "Needs appstream-generator >= 0.10.2")
    def test_real_asgen_arch_backend(self):
        workspace = self.args.work / "lunaos-repo"
        workspace.mkdir(parents=True)
        mirror = self.base / "mirror"
        mirror.mkdir()
        records = dict([record("demo")])
        gen.write_database(workspace / "input.files", records, True)
        (mirror / "demo-1.0-1-any.pkg.tar.zst").write_bytes(PACKAGE_CONTENT)
        settings = {"package": "lunaos-appstream-data", "priority": 20, "servers": [mirror.as_uri()]}
        gen.sync_repo("lunaos-repo", settings, self.args, "x86_64")
        gen.generate("lunaos-repo", self.args, "x86_64")
        gen.package_repo("lunaos-repo", settings, self.args)
        self.assertTrue((self.args.output / "lunaos-appstream-data/PKGBUILD").is_file())

    def test_generation_uses_fixed_arch_layout_and_cached_icons(self):
        config = gen.generator_config("chaotic-aur", self.base, "x86_64")
        self.assertEqual(config["Backend"], "archlinux")
        self.assertEqual(config["Suites"]["rolling"]["sections"], ["chaotic-aur"])
        self.assertFalse(config["Features"]["createScreenshotsStore"])
        self.assertTrue(all(not p["remote"] for p in config["Icons"].values()))


if __name__ == "__main__":
    unittest.main()
