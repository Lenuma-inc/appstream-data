import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import enrich


def component(description="Short", screenshot=None, name="Example", cid="org.example.App", package="example"):
    root = ET.Element("component", type="desktop-application")
    for tag, text in (("id", cid), ("name", name), ("pkgname", package)):
        ET.SubElement(root, tag).text = text
    ET.SubElement(root, "launchable", type="desktop-id").text = cid + ".desktop"
    ET.SubElement(ET.SubElement(root, "description"), "p").text = description
    if screenshot:
        image = ET.SubElement(ET.SubElement(ET.SubElement(root, "screenshots"), "screenshot"), "image", type="source")
        image.text = screenshot
    return root


class EnrichmentTests(unittest.TestCase):
    def test_readme_finds_ui_images_with_neutral_filenames(self):
        paragraphs, images = enrich.readme_presentation(
            "# App\n\n![Modern style](repository/main-modern.png)\n\n"
            "![Settings][settings]\n\n[settings]: repository/settings-classic.png\n")
        self.assertEqual(paragraphs, [])
        self.assertEqual(images, [
            ("repository/main-modern.png", "Modern style"),
            ("repository/settings-classic.png", "Settings"),
        ])

    def test_resolver_accepts_large_unlabelled_readme_interface_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = enrich.UpstreamResolver(temporary)
            readme = b"# Example\n\n![Image](repository/main-modern.png)\n"
            resolver.fetch = lambda url, limit=512 * 1024: readme if url.endswith("README.md") else None
            resolver.image = lambda url, caption: {
                "url": url, "caption": caption, "width": 1280, "height": 720,
            }

            result = resolver.resolve(
                component(name="example"), "https://github.com/example/example")

            self.assertEqual(result["status"], "found")
            self.assertEqual(result["images"][0]["url"],
                             "https://raw.githubusercontent.com/example/example/HEAD/repository/main-modern.png")

    def test_resolver_rejects_small_unlabelled_readme_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            resolver = enrich.UpstreamResolver(temporary)
            resolver.fetch = lambda url, limit=512 * 1024: (
                b"# Example\n\n![Image](assets/illustration.png)\n" if url.endswith("README.md") else None)
            resolver.image = lambda url, caption: {
                "url": url, "caption": caption, "width": 500, "height": 500,
            }

            result = resolver.resolve(
                component(name="example"), "https://github.com/example/example")

            self.assertEqual(result["status"], "upstream-has-no-presentation-data")
            self.assertEqual(result["images"], [])

    def test_native_identity_and_complete_data_are_preserved(self):
        native = component("Native description. " * 20, "https://native.example/screenshot.png")
        ET.SubElement(native, "project_license").text = "MIT"
        before = ET.tostring(native)
        upstream = component("Different upstream description. " * 30, "https://upstream.example/shot.png", package="flatpak-package")
        self.assertEqual(enrich.merge_presentation(native, upstream), [])
        self.assertEqual(ET.tostring(native), before)

    def test_missing_data_is_added_without_replacing_launchers_or_packages(self):
        native = component()
        upstream = component("Detailed upstream description. " * 10, "https://upstream.example/shot.png", cid="other.App", package="other-package")
        self.assertEqual(enrich.merge_presentation(native, upstream), ["description", "screenshots"])
        self.assertEqual(native.findtext("pkgname"), "example")
        self.assertEqual(native.findtext("launchable"), "org.example.App.desktop")

    def test_mirrored_screenshots_can_replace_stale_branch_links(self):
        native = component("Native description. " * 20,
                           "https://raw.githubusercontent.com/owner/app/master/screenshot.png")
        upstream = component("Different description. " * 20,
                             "https://dl.flathub.org/media/org/example/App/hash/screenshots/image-1_orig.png")
        self.assertTrue(enrich.volatile_screenshots(native))
        changed = enrich.merge_presentation(native, upstream, replace_screenshots=True)
        self.assertEqual(changed, ["refreshed-screenshots"])
        self.assertEqual(native.findtext(".//image"), upstream.findtext(".//image"))
        self.assertTrue(native.findtext("description/p").startswith("Native description"))

    def test_repaired_screenshots_are_applied_even_when_native_urls_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root = ET.Element("components")
            root.append(component(screenshot="https://example.org/old.png"))
            supplement = ET.Element("components")
            supplement.append(component(screenshot="https://example.org/working.png"))
            ET.ElementTree(supplement).write(directory / "chaotic-aur.xml")
            sources = {"components": {"chaotic-aur/org.example.App": {"fields": ["refreshed-screenshots"]}}}
            (directory / "sources.json").write_text(json.dumps(sources))
            enrich.apply_enrichment(root, "chaotic-aur", {"example"}, directory=directory)
            self.assertEqual(root.findtext("component/.//image"), "https://example.org/working.png")

    def test_flatpak_instructions_and_thumbnails_are_not_imported(self):
        upstream = component("Useful portable description. " * 10, "https://example.org/original.png")
        ET.SubElement(upstream.find("description"), "p").text = "Install a Flatpak permission override."
        shot = upstream.find(".//screenshot")
        ET.SubElement(shot, "image", type="thumbnail").text = "https://example.org/thumb.png"
        data = enrich.presentation(upstream)
        self.assertNotIn("Flatpak", ET.tostring(data, encoding="unicode"))
        self.assertEqual(len(data.findall(".//image")), 1)

    def test_native_catalog_description_drops_flatpak_only_caveats(self):
        native = component("Useful introduction")
        ET.SubElement(native.find("description"), "p").text = "The Flatpak version has limitations."
        self.assertTrue(enrich.remove_flatpak_instructions(native))
        self.assertEqual([p.text for p in native.findall("description/p")], ["Useful introduction"])

    def test_flatpak_only_description_is_preserved_when_cleaning_would_empty_it(self):
        native = component("The Flatpak version is sandboxed.")
        self.assertFalse(enrich.remove_flatpak_instructions(native))
        self.assertEqual(native.findtext("description/p"), "The Flatpak version is sandboxed.")

    def test_upstream_description_does_not_replace_native_translations(self):
        native = component("Краткое описание", name="Example")
        native.find("description").set(enrich.LANG, "ru")
        supplement = component("A longer upstream description. " * 10)
        ET.SubElement(supplement.find("description"), "p", **{enrich.LANG: "fr"}).text = "Traduction source"
        self.assertEqual(enrich.merge_presentation(native, supplement), ["description"])
        self.assertEqual(native.findtext("description[@{http://www.w3.org/XML/1998/namespace}lang='ru']/p"), "Краткое описание")
        self.assertTrue(any(not d.get(enrich.LANG) for d in native.findall("description")))
        self.assertFalse(any(d.get(enrich.LANG) == "fr" for d in native.findall("description")))

    def test_manual_screenshots_replace_dead_catalog_urls(self):
        native = component(screenshot="https://example.org/dead.png")
        self.assertTrue(enrich.apply_manual_screenshots(native, [{
            "url": "https://example.org/current.png", "caption": "Current application view"
        }]))
        self.assertEqual(native.findtext(".//image"), "https://example.org/current.png")
        self.assertEqual(native.findtext(".//caption"), "Current application view")

    def test_invalid_source_urls_and_duplicate_source_images_are_normalized(self):
        upstream = component(screenshot="https://example.org/one.png")
        shot = upstream.find(".//screenshot")
        ET.SubElement(shot, "image", type="source").text = "https://example.org/two.png"
        ET.SubElement(shot, "image", type="source").text = "${}appstream_screenshot_url"
        data = enrich.presentation(upstream)
        self.assertEqual(len(data.findall(".//screenshot")), 2)
        self.assertTrue(all(len(s.findall("image")) == 1 for s in data.findall(".//screenshot")))

    def test_metainfo_translations_become_catalog_description_sections(self):
        upstream = component("English description")
        desc = upstream.find("description")
        ET.SubElement(desc, "p", **{enrich.LANG: "ru"}).text = "Русское описание"
        items = ET.SubElement(desc, "ul")
        ET.SubElement(items, "li").text = "Feature"
        ET.SubElement(items, "li", **{enrich.LANG: "ru"}).text = "Возможность"
        data = enrich.presentation(upstream)
        translated = data.find("description[@{http://www.w3.org/XML/1998/namespace}lang='ru']")
        self.assertEqual(translated.findtext("p"), "Русское описание")
        self.assertEqual(translated.findtext("ul/li"), "Возможность")
        self.assertTrue(all(enrich.LANG not in child.attrib for description in data.findall("description")
                            for child in description.iter() if child is not description))

    def test_reference_supplement_requires_available_package_and_keeps_native_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            extra = ET.Element("components")
            extra.append(component(package="discord", cid="discord.desktop"))
            ET.ElementTree(extra).write(directory / "extra.xml")
            native = ET.Element("components")
            self.assertEqual(enrich.apply_enrichment(native, "lunaos-repo", set(), set(), directory), [])
            names = set()
            result = enrich.apply_enrichment(native, "lunaos-repo", names, {"discord"}, directory)
            self.assertEqual(len(result), 1)
            self.assertEqual(native.findtext("component/id"), "discord.desktop")
            self.assertEqual(names, {"discord"})
            self.assertEqual(enrich.apply_enrichment(native, "lunaos-repo", names, {"discord"}, directory), [])

    def test_reference_does_not_add_second_component_for_native_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            extra = ET.Element("components")
            extra.append(component(package="steam", cid="com.valvesoftware.Steam.desktop"))
            ET.ElementTree(extra).write(directory / "multilib.xml")
            native = ET.Element("components")
            native.append(component(package="steam", cid="steam.desktop"))
            result = enrich.apply_enrichment(native, "lunaos-repo", {"steam"}, {"steam"}, directory)
            self.assertEqual(result, [])
            self.assertEqual(len(native), 1)

    def test_common_suite_homepage_does_not_match_different_apps(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalogs = directory / "xml"
            catalogs.mkdir()
            native = component(name="Different Editor", cid="different.desktop", package="different-editor")
            upstream = component("Complete description. " * 20, "https://example.org/shot.png", name="Suite Viewer", cid="org.suite.Viewer")
            for c in (native, upstream):
                ET.SubElement(c, "url", type="homepage").text = "https://suite.example.org"
            for filename, c in ((catalogs / "chaotic-aur.xml.gz", native), (directory / "flathub.xml.gz", upstream)):
                root = ET.Element("components")
                root.append(c)
                filename.write_bytes(gzip.compress(ET.tostring(root)))
            enrich.import_catalogs(catalogs, directory / "flathub.xml.gz", directory / "output", directory / "no-databases")
            audit = json.loads((directory / "output/audit.json").read_text())
            self.assertEqual(audit[0]["status"], "needs-upstream-review")

    def test_vscodium_wayland_uses_the_appstream_match_instead_of_its_readme(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalogs = directory / "xml"
            catalogs.mkdir()
            native = component(
                "This is not a fork. " * 4,
                cid="vscodium-wayland.desktop",
                package="vscodium",
                name="VSCodium - Wayland",
            )
            upstream = component(
                "VSCodium combines the simplicity of a code editor with tools for the core edit-build-debug cycle. " * 4,
                "https://example.org/vscodium.png",
                cid="com.vscodium.codium",
                package="com.vscodium.codium",
                name="VSCodium",
            )
            for filename, item in (
                (catalogs / "chaotic-aur.xml.gz", native),
                (directory / "flathub.xml.gz", upstream),
            ):
                root = ET.Element("components", version="1.0")
                root.append(item)
                filename.write_bytes(gzip.compress(ET.tostring(root)))

            enrich.import_catalogs(
                catalogs,
                directory / "flathub.xml.gz",
                directory / "output",
                directory / "no-databases",
            )

            result = ET.parse(directory / "output/chaotic-aur.xml").getroot().find("component")
            audit = json.loads((directory / "output/audit.json").read_text())[0]
            self.assertEqual(result.findtext("id"), "vscodium-wayland.desktop")
            self.assertEqual(result.findtext("pkgname"), "vscodium")
            self.assertIn("core edit-build-debug cycle", result.findtext("description/p"))
            self.assertEqual(result.findtext(".//image"), "https://example.org/vscodium.png")
            self.assertEqual(audit["source_id"], "com.vscodium.codium")
            self.assertEqual(audit["match"], "explicit-native-package")


if __name__ == "__main__":
    unittest.main()
