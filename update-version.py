#!/usr/bin/env python3
"""Give each successful scheduled catalog build a new pacman version."""
import datetime
import hashlib
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parent
path = root / "PKGBUILD"
text = path.read_text()
previous = re.search(r"^pkgver=(\d+)$", text, re.MULTILINE).group(1)
old_release = int(re.search(r"^pkgrel=(\d+)$", text, re.MULTILINE).group(1))
if sys.argv[1:] == ["--finalize"]:
    version, release = previous, old_release
    checksums = []
    for name in ("lunaos-appstream-data", "chaotic-aur-appstream-data", "archlinux-appstream-data"):
        with (root / "dist" / name / f"{name}-{version}.tar.gz").open("rb") as stream:
            checksums.append(hashlib.file_digest(stream, "sha256").hexdigest())
    text = re.sub(r"sha256sums=\([^)]*\)",
                  "sha256sums=(" + "\n            ".join(repr(s) for s in checksums) + ")", text)
elif not sys.argv[1:]:
    version = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    release = old_release + 1 if previous == version else 1
    if int(version) < int(previous):
        raise SystemExit("Clock is behind the published catalog version")
    text = re.sub(r"^pkgver=\d+$", "pkgver=" + version, text, flags=re.MULTILINE)
    text = re.sub(r"^pkgrel=\d+$", "pkgrel=" + str(release), text, flags=re.MULTILINE)
else:
    raise SystemExit("Usage: update-version.py [--finalize]")
path.write_text(text)
with (root / ".SRCINFO").open("w") as stream:
    subprocess.run(["makepkg", "--printsrcinfo"], cwd=root, stdout=stream, check=True)
print(f"Catalog version: {version}-{release}")
