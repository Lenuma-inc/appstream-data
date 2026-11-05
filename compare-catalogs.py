#!/usr/bin/env python3
"""Compare packaged AppStream catalogs and icons, ignoring build-only metadata."""

import argparse
import gzip
import hashlib
from pathlib import Path
import re
import tarfile
import xml.etree.ElementTree as ET


def package_name(path):
    name = path.name.removesuffix(".tar.gz")
    return re.sub(r"-\d{8}$", "", name)


def normalized_catalog(data):
    if data.startswith(b"\x1f\x8b"):
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    # AppStream updates this timestamp on every generation, even when no
    # component or icon changed. It should not create a new package release.
    root.attrib.pop("time", None)
    for element in root.iter():
        if element.text is not None and not element.text.strip():
            element.text = None
        if element.tail is not None and not element.tail.strip():
            element.tail = None
    return ET.canonicalize(ET.tostring(root, encoding="unicode"), strip_text=False)


def archive_content(path):
    catalogs, icons = {}, {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            marker = "/usr/share/"
            if marker not in member.name:
                continue
            relative = member.name.split(marker, 1)[1]
            stream = archive.extractfile(member)
            if stream is None:
                continue
            data = stream.read()
            if relative.startswith("swcatalog/xml/") and relative.endswith((".xml", ".xml.gz")):
                catalogs[relative] = normalized_catalog(data)
            elif relative.startswith("swcatalog/icons/"):
                icons[relative] = hashlib.sha256(data).hexdigest()
    if not catalogs:
        raise ValueError(f"No AppStream catalog found in {path}")
    return catalogs, icons


def release_notes(packages, previous_tag=None, current_tag=None):
    lines = ["Generated LunaOS, Chaotic-AUR, and enriched Arch AppStream catalogs.", ""]
    if previous_tag and current_tag:
        lines += [f"Comparison: `{previous_tag}` → `{current_tag}`", ""]
    for name, old_catalogs, old_icons, new_catalogs, new_icons in packages:
        def components(catalogs):
            return {(path, component.findtext("id")): ET.canonicalize(
                ET.tostring(component, encoding="unicode"))
                for path, catalog in catalogs.items()
                for component in ET.fromstring(catalog).findall("component")}

        old, new = components(old_catalogs), components(new_catalogs)
        added = new.keys() - old.keys()
        removed = old.keys() - new.keys()
        updated = {key for key in old.keys() & new.keys() if old[key] != new[key]}
        lines += [f"## {name}", ""]
        for label, keys in (("Added", added), ("Removed", removed), ("Updated", updated)):
            lines += [f"### {label} ({len(keys)})", ""]
            lines += [f"- `{key[1]}`" for key in sorted(keys)] if keys else ["None."]
            lines.append("")
        added_icons = len(new_icons.keys() - old_icons.keys())
        removed_icons = len(old_icons.keys() - new_icons.keys())
        updated_icons = sum(old_icons[key] != new_icons[key]
                            for key in old_icons.keys() & new_icons.keys())
        lines += [f"Icons: {added_icons} added, {removed_icons} removed, {updated_icons} updated.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-dir", type=Path, required=True)
    parser.add_argument("--current", type=Path, action="append", required=True)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--previous-tag")
    parser.add_argument("--current-tag")
    args = parser.parse_args()

    packages = []
    changed = False
    for current_path in args.current:
        name = package_name(current_path)
        previous_files = sorted(args.previous_dir.glob(name + "-*.tar.gz"))
        new_catalogs, new_icons = archive_content(current_path)
        if not previous_files:
            print(f"{name}: no previous release archive")
            old_catalogs, old_icons = {}, {}
        else:
            old_catalogs, old_icons = archive_content(previous_files[-1])
        packages.append((name, old_catalogs, old_icons, new_catalogs, new_icons))
        changed_catalogs = sum(old_catalogs.get(key) != new_catalogs.get(key)
                               for key in old_catalogs.keys() | new_catalogs.keys())
        changed_icons = sum(old_icons.get(key) != new_icons.get(key)
                            for key in old_icons.keys() | new_icons.keys())
        package_changed = bool(changed_catalogs or changed_icons)
        changed |= package_changed
        print(f"{name}: {'changed' if package_changed else 'unchanged'} "
              f"({changed_catalogs} catalog files, {changed_icons} icon files differ)")

    if args.notes_file:
        args.notes_file.write_text(
            release_notes(packages, args.previous_tag, args.current_tag), encoding="utf-8")
    print(f"changed={str(changed).lower()}")


if __name__ == "__main__":
    main()
