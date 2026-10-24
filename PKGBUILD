# Build all three packages from a git checkout of this repository.
# Catalog versions are refreshed by .github/workflows/appstream.yml.
pkgbase=lunaos-appstream-data
pkgname=('lunaos-appstream-data' 'chaotic-aur-appstream-data' 'lunaos-appstream-data-common')
pkgver=20261024
pkgrel=1
pkgdesc='AppStream application catalogs for LunaOS and Chaotic-AUR'
arch=('any')
url='https://github.com/Lenuma-inc/appstream-data'
license=('LicenseRef-AppStream-Data')
makedepends=('appstream-generator>=0.10.2' 'appstream' 'python' 'python-yaml' 'libarchive'
             'libheif' 'libjxl' 'openslide' 'poppler-glib' 'imagemagick' 'svt-av1' 'rav1e' 'ffmpeg')
options=('!strip' '!debug')

build() {
  # The buildbot clones the whole repository. Run from that checkout so templates
  # and policy are available. Cache only resources, never executable package content.
  python "$startdir/generate.py" all \
    --work "${APPSTREAM_WORK_DIR:-$srcdir/work}" \
    --output "$srcdir/catalogs" --version "$pkgver" --release "$pkgrel"
  local catalog
  for catalog in lunaos-appstream-data chaotic-aur-appstream-data; do
    bsdtar -xf "$srcdir/catalogs/$catalog/$catalog-$pkgver.tar.gz" -C "$srcdir"
  done
}

_install_catalog() {
  install -dm755 "$pkgdir/usr/share"
  cp -a --no-preserve=ownership "$srcdir/$1-$pkgver/usr/share/." "$pkgdir/usr/share/"
}

package_lunaos-appstream-data() {
  pkgdesc='AppStream application catalog and icons for the LunaOS repository'
  _install_catalog "$pkgname"
}

package_chaotic-aur-appstream-data() {
  pkgdesc='AppStream application catalog and icons for Chaotic-AUR'
  _install_catalog "$pkgname"
}

package_lunaos-appstream-data-common() {
  pkgdesc='AppStream catalogs for LunaOS, Arch Linux and Chaotic-AUR'
  license=('MIT')
  depends=('archlinux-appstream-data' 'lunaos-appstream-data' 'chaotic-aur-appstream-data')
}
