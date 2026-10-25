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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-dir", type=Path, required=True)
    parser.add_argument("--current", type=Path, action="append", required=True)
    args = parser.parse_args()

    changed = False
    for current_path in args.current:
        name = package_name(current_path)
        previous_files = sorted(args.previous_dir.glob(name + "-*.tar.gz"))
        if not previous_files:
            print(f"{name}: no previous release archive")
            changed = True
            continue
        previous_path = previous_files[-1]
        old_catalogs, old_icons = archive_content(previous_path)
        new_catalogs, new_icons = archive_content(current_path)
        changed_catalogs = sum(old_catalogs.get(key) != new_catalogs.get(key)
                               for key in old_catalogs.keys() | new_catalogs.keys())
        changed_icons = sum(old_icons.get(key) != new_icons.get(key)
                            for key in old_icons.keys() | new_icons.keys())
        package_changed = bool(changed_catalogs or changed_icons)
        changed |= package_changed
        print(f"{name}: {'changed' if package_changed else 'unchanged'} "
              f"({changed_catalogs} catalog files, {changed_icons} icon files differ)")

    print(f"changed={str(changed).lower()}")


if __name__ == "__main__":
    main()
