#!/usr/bin/env python3
"""Prepare ALPM repositories for asgen and package their AppStream catalogs."""

import argparse
from collections import Counter
import concurrent.futures
import datetime
import fcntl
import fnmatch
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

import yaml

from enrich import apply_enrichment, download_catalog, prepare_enrichment

ROOT = Path(__file__).resolve().parent
RESOURCE_PREFIXES = (
    "usr/share/applications/", "usr/share/metainfo/", "usr/share/appdata/",
    "usr/share/icons/", "usr/share/pixmaps/", "usr/share/fonts/",
)
RESOURCE_PATTERNS = (
    ".PKGINFO", ".BUILDINFO", "usr/share/applications/*", "usr/share/metainfo/*",
    "usr/share/appdata/*", "usr/share/icons/*", "usr/share/pixmaps/*", "usr/share/fonts/*",
    "*.desktop", "*.metainfo.xml", "*.appdata.xml",
    "*.png", "*.svg", "*.svgz", "*.xpm", "*.ico", "*.ttf", "*.otf", "*.ttc",
)
RESOURCE_SCHEMA = "resources-v2"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def token(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+@:-]*", value):
        raise ValueError(f"Invalid name: {value!r}")
    return value


def fields(data):
    result, key = {}, None
    for line in data.decode("utf-8").splitlines():
        if line.startswith("%") and line.endswith("%"):
            key = line.strip("%")
            result[key] = []
        elif line and key:
            result[key].append(line)
    return result


def read_database(path):
    records = {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if len(parts) != 2 or parts[1] not in ("desc", "files"):
                continue
            token(parts[0])
            if not member.isfile():
                raise ValueError(f"Invalid database member: {member.name}")
            records.setdefault(parts[0], {})[parts[1]] = archive.extractfile(member).read()
    if not records:
        raise ValueError(f"Empty repository database: {path}")
    for dirname, record in records.items():
        if "desc" not in record or "files" not in record:
            raise ValueError(f"Missing desc/files for {dirname}")
        record["fields"] = fields(record["desc"])
        info = record["fields"]
        for field in ("NAME", "VERSION", "ARCH", "FILENAME", "SHA256SUM", "CSIZE"):
            if len(info.get(field, [])) != 1:
                raise ValueError(f"Missing/ambiguous {field} for {dirname}")
        token(info["NAME"][0])
        token(info["FILENAME"][0])
        if not re.fullmatch(r"[0-9a-fA-F]{64}", info["SHA256SUM"][0]):
            raise ValueError(f"Invalid checksum for {dirname}")
    return records


def select_packages(records, architecture, include=()):
    selected = {}
    for dirname, record in records.items():
        info = record["fields"]
        if info["ARCH"][0] not in (architecture, "any"):
            continue
        paths = fields(record["files"]).get("FILES", [])
        relevant = any(
            not path.endswith("/") and path.removeprefix("./").startswith(RESOURCE_PREFIXES)
            for path in paths
        )
        if relevant or info["NAME"][0] in include:
            selected[dirname] = record
    missing = set(include) - {r["fields"]["NAME"][0] for r in selected.values()}
    if missing:
        raise ValueError(f"Explicitly included packages are missing: {sorted(missing)}")
    if not selected:
        raise ValueError("No packages with AppStream data or resources found")
    return selected


def database_names(path, architecture):
    names = set()
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            if member.isfile() and PurePosixPath(member.name).name == "desc":
                info = fields(archive.extractfile(member).read())
                if info.get("ARCH", [None])[0] in (architecture, "any"):
                    names.update(info.get("NAME", []))
    if not names:
        raise ValueError(f"No package names found in {path}")
    return names


def apply_policy(selected, available, policy, shadowed=None):
    """Filter visibility before asgen can collapse duplicate component IDs."""
    keep = set(policy["keep_packages"])
    excluded = {}
    visible = available - {
        name for name in available
        if name not in keep and any(fnmatch.fnmatchcase(name, p) for p in policy["exclude_packages"])
    }
    result = {}
    for dirname, record in selected.items():
        name = record["fields"]["NAME"][0]
        reason = f"preferred repository: {shadowed[name]}" if shadowed and name in shadowed else None
        if reason is None and name not in keep:
            pattern = next((p for p in policy["exclude_packages"] if fnmatch.fnmatchcase(name, p)), None)
            if pattern:
                reason = f"exclude_packages: {pattern}"
            elif policy["hide_binary_if_base_exists"]:
                for suffix in policy["binary_suffixes"]:
                    base = name.removesuffix(suffix)
                    if base != name and base in visible:
                        reason = f"base package available: {base}"
                        break
        if reason:
            excluded[name] = reason
        else:
            result[dirname] = record
    if not result:
        raise ValueError("Visibility policy excluded all selected packages")
    return result, excluded


def repository_shadows(config, package_sets):
    """Assign overlapping packages to the highest-priority generated repository."""
    ordered = sorted(config, key=lambda repo: config[repo]["priority"], reverse=True)
    owners, result = {}, {}
    for repo in ordered:
        names = package_sets.get(repo, set())
        result[repo] = {name: owners[name] for name in names if name in owners}
        for name in names:
            owners.setdefault(name, repo)
    return result


def load_policy(path):
    policy = json.loads(path.read_text())
    for key in ("exclude_packages", "keep_packages", "binary_suffixes", "exclude_components"):
        if not isinstance(policy[key], list) or not all(isinstance(p, str) and p for p in policy[key]):
            raise ValueError(f"{key} must be a list of nonempty strings")
    if not isinstance(policy["hide_binary_if_base_exists"], bool):
        raise ValueError("hide_binary_if_base_exists must be boolean")
    return policy


def prepare_context(config, args, architecture, download):
    """Arch is read only for package-name comparisons, never for catalog generation."""
    names, package_sets = set(), {}
    sources = list(config["repositories"].items()) + list(config.get("reference_repositories", {}).items())
    for repo, settings in sources:
        token(repo)
        target_repo = repo in config["repositories"]
        extension = "files" if target_repo else "db"
        if download:
            path = args.work / repo / "input.files" if target_repo else args.work / "reference" / f"{repo}.db"
            fetch([f"{s}/{repo}.{extension}" for s in servers(repo, settings, architecture)], path)
        else:
            path = args.database_dir / f"{repo}.{extension}"
        repository_names = database_names(path, architecture)
        names.update(repository_names)
        if target_repo:
            package_sets[repo] = repository_names
    args.shadowed_packages = repository_shadows(config["repositories"], package_sets)
    return names


def fetch(urls, destination, expected=None):
    """Only replace a download after it is complete and its checksum matches."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if expected and destination.is_file() and digest(destination) == expected:
        return
    errors = []
    for url in urls:
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "LunaOS-AppStream/1"})
            with urllib.request.urlopen(request, timeout=120) as source, temporary.open("wb") as out:
                shutil.copyfileobj(source, out)
            if expected and digest(temporary) != expected:
                raise ValueError("SHA-256 mismatch")
            temporary.replace(destination)
            return
        except (OSError, ValueError) as error:
            errors.append(f"{url}: {error}")
        finally:
            temporary.unlink(missing_ok=True)
    raise RuntimeError("All mirrors failed:\n" + "\n".join(errors))


def servers(repo, config, architecture):
    return [s.replace("$repo", repo).replace("$arch", architecture).rstrip("/")
            for s in config["servers"]]


def write_database(path, records, with_files):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with tarfile.open(temporary, "w:gz") as archive:
        for dirname, record in sorted(records.items()):
            for filename in (("desc", "files") if with_files else ("desc",)):
                data = record[filename]
                member = tarfile.TarInfo(f"{dirname}/{filename}")
                member.size, member.mode, member.mtime = len(data), 0o644, 0
                archive.addfile(member, io.BytesIO(data))
    temporary.replace(path)


def resource_package(source, destination, package_name):
    """Keep only metadata/resources, without extracting an archive onto the host."""
    command = ["bsdtar"]
    for pattern in RESOURCE_PATTERNS:
        command.extend(["--include", pattern, "--include", "./" + pattern])
    overlay = ROOT / "metadata" / package_name
    if not overlay.is_dir():
        subprocess.run(command + ["--zstd", "-cf", str(destination), "@" + str(source)], check=True)
        return
    # Icon-only overlays must not suppress a package's own metainfo.
    if any(overlay.glob("usr/share/metainfo/*")) or any(overlay.glob("usr/share/appdata/*")):
        # Replace packaged metainfo to avoid duplicate IDs and stale launchable names.
        for pattern in ("usr/share/metainfo/*", "usr/share/appdata/*", "*.metainfo.xml", "*.appdata.xml"):
            command.extend(["--exclude", pattern, "--exclude", "./" + pattern])
    with tempfile.TemporaryDirectory(prefix="metadata-", dir=destination.parent) as temp:
        filtered = Path(temp) / "resources.tar"
        subprocess.run(command + ["-cf", str(filtered), "@" + str(source)], check=True)
        subprocess.run(["bsdtar", "--zstd", "-cf", str(destination), "@" + str(filtered),
                        "-C", str(overlay), "usr"], check=True)


def staged_record(record, package):
    info = dict(record["fields"])
    info.pop("PGPSIG", None)  # This internal resource archive is not the original signed package.
    info["CSIZE"] = [str(package.stat().st_size)]
    info["SHA256SUM"] = [digest(package)]
    desc = "".join(f"%{key}%\n" + "\n".join(value) + "\n\n" for key, value in info.items())
    listing = subprocess.check_output(["bsdtar", "-tf", str(package)], text=True)
    paths = sorted({line.removeprefix("./") for line in listing.splitlines()})
    files = "%FILES%\n" + "\n".join(paths) + "\n\n"
    return dict(record, desc=desc.encode(), files=files.encode(), fields=info)


def sync_repo(repo, config, args, architecture):
    workspace = args.work / repo
    mirrors = servers(repo, config, architecture)
    snapshot = workspace / "input.files"
    records = read_database(snapshot)
    selected = select_packages(records, architecture, config.get("include_packages", []))
    selected, excluded = apply_policy(selected, args.available, args.policy_data,
                                      args.shadowed_packages.get(repo, {}))
    report_plan(repo, records, selected)
    report_excluded(excluded, args.explain)
    cache = workspace / "packages" / RESOURCE_SCHEMA

    def download(record):
        info = record["fields"]
        filename, checksum = info["FILENAME"][0], info["SHA256SUM"][0].lower()
        overlay = ROOT / "metadata" / info["NAME"][0]
        target = cache / checksum
        if overlay.is_dir():
            metadata_hash = hashlib.sha256("".join(
                f"{p.relative_to(overlay)}:{digest(p)}\n"
                for p in sorted(overlay.rglob("*")) if p.is_file()
            ).encode()).hexdigest()
            target /= metadata_hash
        target /= filename
        marker = target.with_name(filename + ".sha256")
        if target.is_file() and marker.is_file() and digest(target) == marker.read_text().strip():
            return filename, target
        target.parent.mkdir(parents=True, exist_ok=True)
        # Verify the original download, retain a small resource archive, then discard
        # the full package. This avoids caching tens of GiB of application binaries.
        with tempfile.TemporaryDirectory(prefix="download-", dir=workspace) as temp:
            original = Path(temp) / filename
            fetch([f"{s}/{filename}" for s in mirrors], original, checksum)
            temporary = target.with_name(filename + ".part")
            resource_package(original, temporary, info["NAME"][0])
            temporary.replace(target)
        marker.write_text(digest(target) + "\n")
        return filename, target

    # Stage only after every selected package has downloaded successfully.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        downloaded = list(pool.map(download, selected.values()))
    archive_root = workspace / "archive"
    repository = archive_root / repo / "os" / architecture
    repository.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="repo-", dir=repository.parent) as temp:
        stage = Path(temp)
        for filename, package in downloaded:
            (stage / filename).symlink_to(package.resolve())
        # Both databases come from ONE .files snapshot, avoiding mirror update races.
        by_filename = dict(downloaded)
        staged = {name: staged_record(r, by_filename[r["fields"]["FILENAME"][0]])
                  for name, r in selected.items()}
        # Pacman follows the short aliases, but asgen opens the full tarball name.
        write_database(stage / f"{repo}.db.tar.gz", staged, False)
        write_database(stage / f"{repo}.files.tar.gz", staged, True)
        (stage / f"{repo}.db").symlink_to(f"{repo}.db.tar.gz")
        (stage / f"{repo}.files").symlink_to(f"{repo}.files.tar.gz")
        if repository.exists():
            shutil.rmtree(repository)
        stage.rename(repository)
    manifest = {
        "repository": repo, "architecture": architecture,
        "database_sha256": digest(snapshot),
        "policy": args.policy_data,
        "excluded_packages": excluded,
        "resource_schema": RESOURCE_SCHEMA,
        "metadata_overrides": {
            name: source for name, source in json.loads((ROOT / "metadata/sources.json").read_text()).items()
            if name in {r["fields"]["NAME"][0] for r in selected.values()}
        },
        "packages": [{"name": r["fields"]["NAME"][0],
                      "version": r["fields"]["VERSION"][0],
                      "sha256": r["fields"]["SHA256SUM"][0],
                      "url": r["fields"].get("URL", []),
                      "licenses": r["fields"].get("LICENSE", [])}
                     for r in selected.values()],
    }
    (workspace / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def report_plan(repo, records, selected):
    size = sum(int(r["fields"]["CSIZE"][0]) for r in selected.values())
    print(f"{repo}: {len(selected)}/{len(records)} packages, "
          f"{size / 1024**3:.2f} GiB before cache", flush=True)


def report_excluded(excluded, explain):
    print(f"  hidden by policy: {len(excluded)}", flush=True)
    if explain:
        for name, reason in sorted(excluded.items()):
            print(f"  {name}: {reason}")


def generator_config(repo, workspace, architecture):
    return {
        "ProjectName": repo,
        "Backend": "archlinux",
        "MetadataType": "XML",
        "ArchiveRoot": str((workspace / "archive").resolve()),
        "MediaBaseUrl": "https://localhost.invalid/appstream/media",
        "ImageFormat": "png",
        "Features": {"processDesktop": True, "validateMetainfo": True,
                     "createScreenshotsStore": False, "processLocale": False,
                     "screenshotVideos": False},
        "Suites": {"rolling": {"sections": [repo], "architectures": [architecture]}},
        "Icons": {"48x48": {"cached": True, "remote": False},
                  "64x64": {"cached": True, "remote": False},
                  "128x128": {"cached": True, "remote": False}},
    }


def generate(repo, args, architecture):
    version = subprocess.check_output(["appstream-generator", "--version"], text=True)
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (0, 10, 2):
        raise ValueError("appstream-generator >= 0.10.2 is required (Arch archive layout fix)")
    workspace = args.work / repo
    if not (workspace / "manifest.json").is_file():
        raise ValueError(f"Run sync first for {repo}")
    manifest = json.loads((workspace / "manifest.json").read_text())
    if manifest["policy"] != args.policy_data:
        raise ValueError("Policy changed; run sync again before generation")
    stamp = workspace / "generation.json"
    stamp.unlink(missing_ok=True)
    repository = workspace / "archive" / repo / "os" / architecture
    # Also repair workspaces cached by the original implementation.
    for extension in ("db", "files"):
        tarball = repository / f"{repo}.{extension}.tar.gz"
        short = repository / f"{repo}.{extension}"
        if not tarball.exists() and short.is_file() and not short.is_symlink():
            tarball.symlink_to(short.name)
        if not tarball.is_file():
            raise ValueError(f"Missing Arch backend database: {tarball}; run sync first")
    path = workspace / "asgen-config.json"
    path.write_text(json.dumps(generator_config(repo, workspace, architecture), indent=2) + "\n")
    # asgen caches by name/version/architecture, not by archive checksum. Local
    # metainfo overrides can change while the upstream package version stays put.
    # Forget both metadata and contents so a cached desktop-only result is replaced.
    for name in sorted(manifest.get("metadata_overrides", {})):
        subprocess.run(["appstream-generator", "--workspace", str(workspace),
                        "forget", token(name)], check=True)
    subprocess.run(["appstream-generator", "--workspace", str(workspace),
                    "--force", "run", "rolling"], check=True)
    if len(list((workspace / "export/data").rglob("Components-*.xml.gz"))) != 1:
        raise ValueError(f"asgen did not export a catalog for {repo}; inspect its logs and hints")
    stamp.write_text(json.dumps({"manifest_sha256": digest(workspace / "manifest.json"),
                                 "generator": version.strip()}, indent=2) + "\n")


def unpack_icons(archive_path, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts:
                raise ValueError(f"Unsafe icon name: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(f"Unexpected icon archive member: {member.name}")
            # asgen releases have shipped both flat tarballs and directory-prefixed
            # tarballs. The catalog refers to leaf names within a size directory.
            target = destination / parts[-1]
            content = archive.extractfile(member).read()
            if target.exists() and target.read_bytes() != content:
                raise ValueError(f"Conflicting cached icons: {target.name}")
            target.write_bytes(content)
            target.chmod(0o644)


def write_catalog(source, destination, priority, package_names, excluded_components=(),
                  repo=None, reference_names=(), enrichment_report=None, enrichment_dir=None,
                  excluded_packages=()):
    with gzip.open(source, "rb") as stream:
        root = ET.parse(stream).getroot()
    if root.tag != "components":
        raise ValueError("Expected an AppStream catalog with <components> root")
    # Filter cached asgen results as well as the packages selected during sync.
    for component in list(root.findall("component")):
        if any(p.text in excluded_packages for p in component.findall("pkgname")):
            root.remove(component)
    if repo is not None:
        applied = apply_enrichment(root, repo, package_names, reference_names, enrichment_dir)
        if enrichment_report is not None:
            enrichment_report.extend(applied)
    origin = token(root.get("origin", ""))
    # The generator requires a media base even though this deployment uses cached
    # icons and original upstream screenshot URLs. Do not ship the unused base.
    root.attrib.pop("media_baseurl", None)
    for element in root.iter():
        if element.tag in ("image", "video") or (element.tag == "icon" and element.get("type") == "remote"):
            url = element.text or ""
            if not url.startswith(("https://", "http://")):
                raise ValueError(f"Generated media needs hosting; expected an absolute upstream URL: {url}")
    components = root.findall("component")
    counts = Counter(c.findtext("id") for c in components)
    duplicates = {cid for cid, count in counts.items() if cid and count > 1}
    for cid in sorted(duplicates):
        print(f"Excluded duplicate component ID: {cid}", flush=True)
    for component in components:
        cid = component.findtext("id", "")
        if cid in duplicates or any(fnmatch.fnmatchcase(cid, p) for p in excluded_components):
            root.remove(component)
    components = root.findall("component")
    if not components:
        raise ValueError("Generated catalog is empty; inspect asgen hints before publishing")
    ids = set()
    for component in components:
        cid = component.findtext("id")
        if not cid or cid in ids:
            raise ValueError(f"Missing/duplicate component ID: {cid}")
        ids.add(cid)
        names = [p.text for p in component.findall("pkgname")]
        if not names or any(p not in package_names for p in names):
            raise ValueError(f"Unknown package mapping for {cid}: {names}")
        component.set("priority", str(priority))
        for summary in component.findall("summary"):
            if summary.text and any(c in summary.text for c in "\t\r\n"):
                summary.text = " ".join(summary.text.split())
    validation_reports = []
    while True:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
            ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
        text = ET.tostring(root, encoding="unicode")
        if "localhost.invalid" in text:
            raise ValueError("Catalog references unpublished generated media")
        validation = subprocess.run(["appstreamcli", "validate", "--no-net", "--format", "yaml",
                                     str(destination)], capture_output=True, text=True)
        validation_reports.append(validation.stdout)
        destination.with_suffix(".validation.yaml").write_text("".join(validation_reports))
        # appstreamcli emits unquoted hints that can be invalid inside YAML flow mappings.
        severity_report = re.sub(r'(?m)^([ \t]*)hint:[^\n]*(?:\n\1[ \t]+[^\n]*)*',
                                 lambda m: m[1] + 'hint: ""' + (',' if m[0].endswith(',') else ''),
                                 validation.stdout)
        reports = list(yaml.safe_load_all(severity_report))
        issues = [issue for report in reports if isinstance(report, dict)
                  for issue in report.get("Issues", [])]
        errors = [issue for issue in issues if issue.get("severity") == "error"]
        if validation.returncode == 3 and errors and all(i.get("component") in ids for i in errors):
            invalid = {i["component"] for i in errors}
            for issue in errors:
                print(f"Excluded invalid component: {issue['component']}: {issue.get('tag', '?')}", flush=True)
            for component in components:
                if component.findtext("id") in invalid:
                    root.remove(component)
            components = root.findall("component")
            if not components:
                raise ValueError("Generated catalog is empty after validation")
            ids -= invalid
            continue
        # Sparse .desktop-only metadata often has warnings. Invalid catalogs must fail,
        # but a missing homepage must not prevent publishing all other applications.
        if validation.returncode and (
            not issues or errors
            or validation.returncode != 3
        ):
            details = "\n".join(
                f"{i.get('component', '?')}:{i.get('line', '?')}: {i.get('tag', '?')} — "
                f"{i.get('explanation', '')}" for i in errors
            )
            raise RuntimeError(f"AppStream validation failed (exit {validation.returncode}):\n"
                               + (details or validation.stdout) + validation.stderr)
        return origin, len(components)


def source_archive(stage, output, prefix):
    """Produce stable tar/gzip headers for the same generated data."""
    with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for path in sorted(stage.rglob("*")):
                info = archive.gettarinfo(str(path), f"{prefix}/{path.relative_to(stage)}")
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                if path.is_file():
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
                else:
                    archive.addfile(info)


def package_repo(repo, config, args):
    workspace = args.work / repo
    exported = workspace / "export" / "data"
    catalogs = list(exported.rglob("Components-*.xml.gz"))
    if len(catalogs) != 1:
        raise ValueError(f"Expected one generated catalog for {repo}, found {catalogs}")
    manifest = json.loads((workspace / "manifest.json").read_text())
    stamp = json.loads((workspace / "generation.json").read_text())
    if stamp["manifest_sha256"] != digest(workspace / "manifest.json"):
        raise ValueError("Inputs changed since the last generation; run generate again")
    if manifest["policy"] != args.policy_data:
        raise ValueError("Policy changed; run sync and generate again before packaging")
    project_urls = {p["name"]: p["url"][0] for p in manifest["packages"] if p.get("url")}
    enrichment_dir = prepare_enrichment(repo, catalogs[0], args.work / "enrichment" / repo,
                                        args.enrichment_catalog, args.work / "reference", project_urls)
    pkgname = config["package"]
    output = args.output / pkgname
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stage-", dir=workspace) as temp:
        stage = Path(temp)
        share = stage / "usr/share"
        catalog = share / "swcatalog/xml" / f"{repo}.xml.gz"
        names = {p["name"] for p in manifest["packages"]}
        reference_names = set()
        if repo == "lunaos-repo":
            for database in sorted((args.work / "reference").glob("*.db")):
                reference_names.update(database_names(database, manifest["architecture"]))
            reference_names = {
                name for name in reference_names
                if name in args.policy_data["keep_packages"] or not any(
                    fnmatch.fnmatchcase(name, pattern) for pattern in args.policy_data["exclude_packages"])
            }
        enrichment_report = []
        try:
            origin, count = write_catalog(catalogs[0], catalog, config["priority"], names,
                                          args.policy_data["exclude_components"], repo,
                                          reference_names, enrichment_report, enrichment_dir,
                                          args.shadowed_packages.get(repo, {}))
        finally:
            report = catalog.with_suffix(".validation.yaml")
            if report.is_file():
                shutil.copyfile(report, output / "validation.yaml")
        if origin in ("archlinux", "archlinux-arch-extra", "archlinux-arch-core"):
            raise ValueError("Catalog origin must not replace Arch Linux metadata")
        archives = []
        for icons in sorted((workspace / "export").rglob("*")):
            match = re.fullmatch(r"icons-(\d+x\d+(?:@\d+)?)\.tar(?:\.(?:gz|xz|zst))?",
                                 icons.name, flags=re.IGNORECASE)
            if not match or not icons.is_file():
                continue
            size = match.group(1)
            archives.append(str(icons.relative_to(workspace)))
            unpack_icons(icons, share / "swcatalog/icons" / origin / size)
        icon_root = share / "swcatalog/icons" / origin
        for icon in ET.fromstring(gzip.decompress(catalog.read_bytes())).iter("icon"):
            if icon.get("type") != "cached":
                continue
            size = f"{icon.get('width', '64')}x{icon.get('height', '64')}"
            if icon.get("scale", "1") != "1":
                size += "@" + icon.get("scale")
            filename = icon.text or ""
            if PurePosixPath(filename).name == filename and not (icon_root / size / filename).is_file():
                supplemental_icon = enrichment_dir / "icons" / size / filename
                if supplemental_icon.is_file():
                    (icon_root / size).mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(supplemental_icon, icon_root / size / filename)
            if PurePosixPath(filename).name != filename or not (icon_root / size / filename).is_file():
                layout = [str(p.relative_to(workspace)) for p in (workspace / "export").rglob("*") if p.is_file()]
                raise ValueError(f"Missing cached icon: {size}/{filename}; "
                                 f"icon archives={archives}; exported files={layout}")
        manifest.update({"origin": origin, "components": count, "priority": config["priority"]})
        enrichment_sources = enrichment_dir / "sources.json"
        if enrichment_sources.is_file():
            sources = json.loads(enrichment_sources.read_text())
            manifest["catalog_enrichment"] = {
                "catalog_sha256": sources["catalog_sha256"],
                "components": {f"{item['repository']}/{item['id']}":
                               sources["components"].get(f"{item['repository']}/{item['id']}", {})
                               for item in enrichment_report},
            }
        manifest["generator"] = stamp["generator"]
        doc = share / "doc" / pkgname
        doc.mkdir(parents=True)
        shutil.move(catalog.with_suffix(".validation.yaml"), doc / "validation.yaml")
        (doc / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        license_dir = share / "licenses" / pkgname
        license_dir.mkdir(parents=True)
        (license_dir / "LICENSE").write_text(
            "This package aggregates AppStream metadata and icons from " + repo + ".\n"
            "These retain their respective upstream licenses; the generator's MIT license\n"
            "does not relicense them. Package source URLs and declared package licenses\n"
            "are recorded in /usr/share/doc/" + pkgname + "/manifest.json.\n"
            "Supplemental metadata sources, credits, changes and licenses are recorded\n"
            "in the metadata_overrides and catalog_enrichment sections of the same manifest.\n"
        )
        filename = f"{pkgname}-{args.version}.tar.gz"
        source_archive(stage, output / filename, f"{pkgname}-{args.version}")
    print(f"Prepared {output}: {count} components, origin={origin}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "sync", "generate", "package", "all"))
    parser.add_argument("--repo", action="append", help="Repeat for multiple repositories; default: both")
    parser.add_argument("--config", type=Path, default=ROOT / "repositories.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "policy.json")
    parser.add_argument("--explain", action="store_true", help="List packages hidden by the policy")
    parser.add_argument("--work", type=Path, default=ROOT / "work")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--enrichment-catalog", type=Path,
                        help="Use a local upstream AppStream snapshot instead of downloading it")
    parser.add_argument("--database-dir", type=Path, default=Path("/var/lib/pacman/sync"),
                        help="Local .files databases for offline plan")
    parser.add_argument("--jobs", type=int, default=4, help="Concurrent downloads")
    parser.add_argument("--version", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d"))
    args = parser.parse_args()
    if args.jobs < 1 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", args.version):
        parser.error("jobs must be positive; version must be numeric, e.g. 20261022")
    config = json.loads(args.config.read_text())
    args.policy_data = load_policy(args.policy)
    architecture = token(config["architecture"])
    repos = args.repo or list(config["repositories"])
    for repo in repos:
        if repo not in config["repositories"]:
            parser.error(f"Unknown repository: {repo}")
        token(repo)
        token(config["repositories"][repo]["package"])
    args.work, args.output = args.work.resolve(), args.output.resolve()
    if args.command == "plan":
        args.available = prepare_context(config, args, architecture, download=False)
        for repo in repos:
            records = read_database(args.database_dir / f"{repo}.files")
            selected = select_packages(records, architecture,
                                       config["repositories"][repo].get("include_packages", []))
            selected, excluded = apply_policy(selected, args.available, args.policy_data,
                                              args.shadowed_packages.get(repo, {}))
            report_plan(repo, records, selected)
            report_excluded(excluded, args.explain)
        return
    args.work.mkdir(parents=True, exist_ok=True)
    with (args.work / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command in ("sync", "all"):
            args.available = prepare_context(config, args, architecture, download=True)
        elif args.command == "package":
            package_sets = {}
            for name in config["repositories"]:
                snapshot = args.work / name / "input.files"
                if not snapshot.is_file():
                    raise ValueError(f"Missing repository snapshot {snapshot}; run sync first")
                package_sets[name] = database_names(snapshot, architecture)
            args.shadowed_packages = repository_shadows(config["repositories"], package_sets)
        if args.command in ("package", "all"):
            if args.enrichment_catalog is None:
                args.enrichment_catalog = args.work / "enrichment/flathub.xml.gz"
                download_catalog(args.enrichment_catalog)
            else:
                args.enrichment_catalog = args.enrichment_catalog.resolve()
        for repo in repos:
            settings = config["repositories"][repo]
            if args.command in ("sync", "all"):
                sync_repo(repo, settings, args, architecture)
            if args.command in ("generate", "all"):
                generate(repo, args, architecture)
            if args.command in ("package", "all"):
                package_repo(repo, settings, args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, tarfile.TarError, ET.ParseError,
            subprocess.CalledProcessError, yaml.YAMLError) as error:
        sys.exit(f"Error: {error}")
