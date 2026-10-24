#!/usr/bin/env python3
"""Automatically audit and enrich native catalogs using upstream AppStream data.

Native package IDs, launchers, icons, licenses and release histories are retained.
Generated catalogs and audit reports belong in work/, not in metadata/.
"""

import argparse
from collections import defaultdict
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
from urllib.parse import urlsplit
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
FLATHUB_URL = "https://dl.flathub.org/repo/appstream/x86_64/appstream.xml.gz"


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", value.lower().removesuffix(".desktop"))


def project_url(value):
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/").removesuffix(".git").lower()
    if not host or (not path and host in ("github.com", "gitlab.com", "sourceforge.net", "gnome.org", "kde.org")):
        return ""
    return host + path


def package_urls(database_dir):
    result = {}
    for path in database_dir.glob("*.db"):
        with tarfile.open(path, "r:*") as archive:
            for entry in archive:
                if not entry.isfile() or not entry.name.endswith("/desc"):
                    continue
                data = archive.extractfile(entry).read().decode()
                name = re.search(r"(?m)^%NAME%\n([^\n]+)", data)
                url = re.search(r"(?m)^%URL%\n([^\n]+)", data)
                if name and url:
                    result[name[1]] = url[1]
    return result


def description_length(component):
    description = next((e for e in component.findall("description") if not e.get(LANG)), None)
    return len(" ".join(description.itertext()).strip()) if description is not None else 0


def missing_fields(component):
    result = []
    if description_length(component) < 160:
        result.append("description")
    if not has_screenshots(component):
        result.append("screenshots")
    if not component.findall("icon"):
        result.append("icons")
    return result


def has_screenshots(component):
    return any((image.text or "").strip().startswith(("https://", "http://"))
               for image in component.findall(".//screenshots/screenshot/image"))


def presentation(component):
    """Copy portable UI data, excluding Flatpak-specific instructions and thumbnails."""
    result = ET.Element("component", type="desktop-application")
    for source in component:
        if source.tag not in ("description", "screenshots"):
            continue
        element = copy.deepcopy(source)
        if element.tag == "description":
            for parent in list(element.iter()):
                for child in list(parent):
                    text = " ".join(child.itertext()).lower()
                    if child.tag in ("p", "li") and any(s in text for s in ("flatpak", "flathub")):
                        parent.remove(child)
            for child in list(element):
                if not "".join(child.itertext()).strip():
                    element.remove(child)
        else:
            for screenshot in list(element):
                for image in list(screenshot):
                    if image.tag in ("image", "video"):
                        if image.tag == "video" or image.get("type", "source") != "source":
                            screenshot.remove(image)
                        elif not (image.text or "").strip().startswith(("https://", "http://")):
                            screenshot.remove(image)
                        else:
                            image.text = image.text.strip()
                if not screenshot.findall("image"):
                    element.remove(screenshot)
            # Some upstream files put several full-size images in one screenshot.
            # AppStream permits only one source image per language per screenshot.
            for screenshot in list(element):
                seen = set()
                for image in list(screenshot.findall("image")):
                    language = image.get(LANG, "")
                    if language in seen:
                        screenshot.remove(image)
                        additional = ET.Element("screenshot")
                        additional.extend(copy.deepcopy(screenshot.findall("caption")))
                        additional.append(image)
                        element.append(additional)
                    seen.add(language)
            for index, screenshot in enumerate(element):
                screenshot.attrib.pop("type", None)
                if index == 0:
                    screenshot.set("type", "default")
        if len(element):
            result.append(element)
    # MetaInfo translates individual paragraphs/list items; catalogs translate
    # entire description sections. Convert local overrides before merging them.
    descriptions = {}
    for description in result.findall("description"):
        default_language = description.get(LANG, "")
        for child in description:
            language = child.get(LANG, default_language)
            if child.tag in ("ul", "ol"):
                lists = {}
                for item in child:
                    item_language = item.get(LANG, language)
                    translated_list = lists.setdefault(item_language, ET.Element(child.tag))
                    translated_list.append(copy.deepcopy(item))
                blocks = lists.items()
            else:
                blocks = [(language, copy.deepcopy(child))]
            for locale, block in blocks:
                for descendant in block.iter():
                    descendant.attrib.pop(LANG, None)
                section = descriptions.setdefault(locale, ET.Element("description", **({LANG: locale} if locale else {})))
                section.append(block)
        result.remove(description)
    result.extend(descriptions.values())
    return result


def merge_presentation(native, supplement):
    changed = []
    data = presentation(supplement)
    if description_length(native) < 160 and description_length(data) > description_length(native):
        for element in native.findall("description"):
            native.remove(element)
        native.extend(copy.deepcopy(data.findall("description")))
        changed.append("description")
    if not has_screenshots(native) and data.find("screenshots") is not None:
        for element in native.findall("screenshots"):
            native.remove(element)
        native.append(copy.deepcopy(data.find("screenshots")))
        changed.append("screenshots")
    return changed


def apply_enrichment(root, repo, package_names, reference_names=(), directory=None):
    """Apply reviewed snapshots; reference apps are served by the LunaOS catalog."""
    if directory is None:
        return []
    by_id = {c.findtext("id"): c for c in root.findall("component")}
    applied = []
    repositories = [repo] + (["extra", "multilib", "core"] if repo == "lunaos-repo" else [])
    for source_repo in repositories:
        path = directory / f"{source_repo}.xml"
        if not path.is_file():
            continue
        for supplement in ET.parse(path).getroot().findall("component"):
            name, cid = supplement.findtext("pkgname"), supplement.findtext("id")
            if not name or not cid:
                raise ValueError(f"Missing package/component ID in {path}")
            native = by_id.get(cid)
            if source_repo == repo:
                if native is None or name not in package_names or native.findtext("pkgname") != name:
                    continue
                changed = merge_presentation(native, supplement)
            else:
                if name not in reference_names or native is not None:
                    continue
                native = copy.deepcopy(supplement)
                root.append(native)
                by_id[cid] = native
                package_names.add(name)
                changed = ["reference-supplement"]
            if changed:
                applied.append({"repository": source_repo, "package": name, "id": cid, "fields": changed})
    return applied


def import_catalogs(catalog_dir, flathub_path, output, database_dir=Path("/var/lib/pacman/sync"),
                    icon_dir=None, project_urls=None):
    flatpak = ET.fromstring(gzip.decompress(flathub_path.read_bytes()))
    indexes = {key: defaultdict(dict) for key in ("id", "desktop", "name", "url")}
    native_urls = package_urls(database_dir)
    native_urls.update(project_urls or {})
    icon_dir = icon_dir or catalog_dir.parent / "icons"
    local_sources = json.loads((ROOT / "metadata/sources.json").read_text())
    matches_path = ROOT / "metadata/matches.json"
    explicit_matches = json.loads(matches_path.read_text()) if matches_path.is_file() else {}
    for component in flatpak:
        if component.get("type") != "desktop-application":
            continue
        cid = component.findtext("id", "")
        indexes["id"][normalize(cid)][cid] = component
        for element in component.findall("launchable"):
            if element.get("type") == "desktop-id":
                indexes["desktop"][normalize(element.text or "")][cid] = component
        for element in component.findall("name"):
            if not element.get(LANG):
                indexes["name"][normalize(element.text or "")][cid] = component
        for element in component.findall("url"):
            if element.get("type") in ("homepage", "vcs-browser"):
                url = project_url(element.text or "")
                if url:
                    indexes["url"][url][cid] = component
    output.mkdir(parents=True, exist_ok=True)
    audit, sources = [], {}
    for path in sorted(catalog_dir.glob("*.xml.gz")):
        repo = path.name.removesuffix(".xml.gz")
        if repo not in ("lunaos-repo", "chaotic-aur", "extra", "core", "multilib"):
            continue
        catalog = ET.fromstring(gzip.decompress(path.read_bytes()))
        origin = catalog.get("origin", "")
        imported = ET.Element("components", version="1.0")
        for native in catalog.findall("component"):
            if native.get("type") != "desktop-application" or not native.findtext("pkgname"):
                continue
            missing = missing_fields(native)
            if not missing:
                continue
            name, cid = native.findtext("pkgname"), native.findtext("id", "")
            local_metadata = sorted((ROOT / "metadata" / name / "usr/share/metainfo").glob("*.xml"))
            candidates = dict(indexes["id"].get(normalize(cid), {}))
            for launchable in native.findall("launchable"):
                if launchable.get("type") == "desktop-id":
                    candidates.update(indexes["desktop"].get(normalize(launchable.text or ""), {}))
            reason = "component-or-desktop-id"
            if not candidates:
                for element in native.findall("name"):
                    if not element.get(LANG):
                        candidates.update(indexes["name"].get(normalize(element.text or ""), {}))
                base = normalize(re.sub(r"(-bin|-appimage)$", "", name))
                candidates = {key: value for key, value in candidates.items()
                              if len(base) >= 5 and base in (normalize(key.split(".")[-1]),
                                                           normalize(value.findtext("name", "")))}
                reason = "unique-name-and-package"
            if not candidates:
                urls = [e.text or "" for e in native.findall("url") if e.get("type") == "homepage"]
                urls.append(native_urls.get(name, ""))
                for url in urls:
                    candidates.update(indexes["url"].get(project_url(url), {}))
                # A homepage may belong to an entire suite (KDE, Maui, Simon
                # Tatham's games), or to both Community and Professional editions.
                # Corroborate the URL with the application name, not just the host.
                native_names = {normalize(e.text or "") for e in native.findall("name") if not e.get(LANG)}
                candidates = {key: value for key, value in candidates.items()
                              if native_names.intersection(normalize(e.text or "")
                                  for e in value.findall("name") if not e.get(LANG))}
                reason = "unique-project-url"
            if name == "discord-ptb":
                candidates = indexes["id"].get(normalize("com.discordapp.Discord"), {})
                reason = "explicit-discord-variant"
            if name in explicit_matches:
                candidates = indexes["id"].get(normalize(explicit_matches[name]), {})
                reason = "explicit-native-package"
            if len(local_metadata) == 1:
                local = ET.parse(local_metadata[0]).getroot()
                candidates = {local.findtext("id", cid): local}
                reason = "reviewed-native-metainfo"
            record = {"repository": repo, "package": name, "id": cid, "missing": missing}
            if len(candidates) != 1:
                record["status"] = "needs-upstream-review"
                audit.append(record)
                continue
            flat_id, upstream = next(iter(candidates.items()))
            supplement = copy.deepcopy(native)
            changed = merge_presentation(supplement, upstream)
            record.update(source_id=flat_id, match=reason, added=changed)
            if not changed:
                record["status"] = "source-has-no-additional-data"
                audit.append(record)
                continue
            if repo in ("extra", "core", "multilib"):
                if any(re.search(r"https?://", element.text or "") for element in supplement.findall("summary")):
                    for element in supplement.findall("summary"):
                        supplement.remove(element)
                    supplement.extend(copy.deepcopy(upstream.findall("summary")))
                clean_screenshots = presentation(supplement).find("screenshots")
                for screenshots in supplement.findall("screenshots"):
                    supplement.remove(screenshots)
                if clean_screenshots is not None:
                    supplement.append(clean_screenshots)
                # Reference components are appended to a different catalog origin.
                # Bundle their native icons under that origin as well.
                cached = []
                for icon in supplement.findall("icon"):
                    if icon.get("type") != "cached":
                        continue
                    size = f"{icon.get('width', '64')}x{icon.get('height', '64')}"
                    if icon.get("scale", "1") != "1":
                        size += "@" + icon.get("scale")
                    filename = icon.text or ""
                    if Path(filename).name != filename:
                        raise ValueError(f"Unsafe icon filename: {filename}")
                    source = icon_dir / origin / size / filename
                    if source.is_file():
                        filename = f"{repo}_{filename}"
                        target = output / "icons" / size / filename
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
                        icon.text = filename
                        cached.append(icon)
                    else:
                        supplement.remove(icon)
                if not any(i.get("height") == "64" and i.get("scale", "1") == "1" for i in cached):
                    record["status"] = "needs-native-icon"
                    audit.append(record)
                    continue
            # Drop old timestamps and releases from reference snapshots: native
            # package versions continue to come from the live pacman database.
            for release in supplement.findall("releases"):
                supplement.remove(release)
            imported.append(supplement)
            record["status"] = "imported"
            record["remaining"] = missing_fields(supplement)
            audit.append(record)
            sources[f"{repo}/{cid}"] = {"package": name, "source_id": flat_id,
                "url": f"https://flathub.org/apps/{flat_id.removesuffix('.desktop')}",
                "catalog_url": FLATHUB_URL, "match": reason, "fields": changed,
                "licenses": "Upstream metadata and media retain their respective licenses."}
            if reason == "reviewed-native-metainfo":
                sources[f"{repo}/{cid}"].update(local_sources.get(name, {}))
                sources[f"{repo}/{cid}"].pop("catalog_url", None)
        ET.indent(imported, space="  ")
        ET.ElementTree(imported).write(output / f"{repo}.xml", encoding="utf-8", xml_declaration=True)
    sources_doc = {"catalog_sha256": hashlib.sha256(flathub_path.read_bytes()).hexdigest(), "components": sources}
    (output / "sources.json").write_text(json.dumps(sources_doc, indent=2) + "\n")
    (output / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    counts = defaultdict(int)
    for record in audit:
        counts[record["status"]] += 1
    print(json.dumps(dict(counts), indent=2))


def download_catalog(destination):
    """Keep incomplete network downloads out of the last usable snapshot."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        request = urllib.request.Request(FLATHUB_URL, headers={"User-Agent": "LunaOS-AppStream/1"})
        with urllib.request.urlopen(request, timeout=120) as source, temporary.open("wb") as out:
            shutil.copyfileobj(source, out)
        root = ET.fromstring(gzip.decompress(temporary.read_bytes()))
        if root.tag != "components" or root.find("component") is None:
            raise ValueError("Downloaded upstream catalog is empty or invalid")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_enrichment(repo, native_catalog, directory, flathub_path, database_dir,
                       project_urls=None, reference_root=Path("/usr/share/swcatalog")):
    inputs = directory / "input"
    inputs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(native_catalog, inputs / f"{repo}.xml.gz")
    if repo == "lunaos-repo":
        for reference in ("extra", "core", "multilib"):
            source = reference_root / "xml" / f"{reference}.xml.gz"
            if not source.is_file():
                raise ValueError(f"Missing reference catalog {source}; install archlinux-appstream-data")
            shutil.copyfile(source, inputs / source.name)
    output = directory / "catalogs"
    import_catalogs(inputs, flathub_path, output, database_dir,
                    reference_root / "icons", project_urls)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-dir", type=Path, default=Path("/usr/share/swcatalog/xml"))
    parser.add_argument("--flathub-catalog", type=Path, help="Use a local snapshot for an offline import")
    parser.add_argument("--output", type=Path, default=ROOT / "work/enrichment/catalogs")
    parser.add_argument("--database-dir", type=Path, default=Path("/var/lib/pacman/sync"))
    args = parser.parse_args()
    if args.flathub_catalog is None:
        args.flathub_catalog = args.output.parent / "flathub.xml.gz"
        download_catalog(args.flathub_catalog)
    import_catalogs(args.catalog_dir, args.flathub_catalog, args.output, args.database_dir)


if __name__ == "__main__":
    main()
