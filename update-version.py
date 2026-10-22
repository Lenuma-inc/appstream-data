#!/usr/bin/env python3
"""Give each successful scheduled catalog build a new pacman version."""
import datetime
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parent
path = root / "PKGBUILD"
text = path.read_text()
version = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
previous = re.search(r"^pkgver=(\d+)$", text, re.MULTILINE).group(1)
old_release = int(re.search(r"^pkgrel=(\d+)$", text, re.MULTILINE).group(1))
release = old_release + 1 if previous == version else 1
if int(version) < int(previous):
    raise SystemExit("Clock is behind the published catalog version")
text = re.sub(r"^pkgver=\d+$", "pkgver=" + version, text, flags=re.MULTILINE)
text = re.sub(r"^pkgrel=\d+$", "pkgrel=" + str(release), text, flags=re.MULTILINE)
path.write_text(text)
with (root / ".SRCINFO").open("w") as stream:
    subprocess.run(["makepkg", "--printsrcinfo"], cwd=root, stdout=stream, check=True)
print(f"Catalog version: {version}-{release}")
