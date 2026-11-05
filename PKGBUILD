# Package catalogs already generated and published by CI.
pkgbase=lunaos-appstream-data
pkgname=('lunaos-appstream-data' 'chaotic-aur-appstream-data' 'lunaos-appstream-data-common' 'archlinux-appstream-data')
pkgver=20261105
pkgrel=2
pkgdesc='AppStream application catalogs for LunaOS, Arch Linux, and Chaotic-AUR'
arch=('any')
url='https://github.com/Lenuma-inc/appstream-data'
license=('LicenseRef-AppStream-Data')
options=('!strip' '!debug')

source=("${url}/releases/download/${pkgver}-${pkgrel}/lunaos-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/chaotic-aur-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/archlinux-appstream-data-${pkgver}.tar.gz")
sha256sums=('eddf3d1540ce175efba8873f70a4e751b686ec16e657ddfb2bfdde5fe0fb01b1'
            '63d33d2a9f4c44ec76c4c2418ebc98e72697a46d494bf780bc9aafec01304cc6'
            '0b8f705f24b869c29e8d5a37c858078ea24521846d10be9009b8fe90be5a6e60')

_install_catalog() {
  install -dm755 "$pkgdir/usr/share"
  cp -a --no-preserve=ownership "$srcdir/$1-$pkgver/usr/share/." "$pkgdir/usr/share/"

  # libpamac constructs this Arch-style path instead of using the catalog origin.
  # Keep the standard AppStream layout and provide a relative compatibility link.
  local icon_root="$pkgdir/usr/share/swcatalog/icons"
  local origins=("$icon_root"/*)
  if [[ ${#origins[@]} -ne 1 || ! -d ${origins[0]} ]]; then
    error "Expected one AppStream icon origin for $1"
    return 1
  fi
  ln -s "${origins[0]##*/}" "$icon_root/archlinux-arch-$2"
}

package_lunaos-appstream-data() {
  pkgdesc='AppStream application catalog and icons for the LunaOS repository'
  _install_catalog "$pkgname" lunaos-repo
}

package_chaotic-aur-appstream-data() {
  pkgdesc='AppStream application catalog and icons for Chaotic-AUR'
  _install_catalog "$pkgname" chaotic-aur
}

package_lunaos-appstream-data-common() {
  pkgdesc='AppStream catalog integration for LunaOS, enriched Arch Linux, and Chaotic-AUR'
  license=('MIT')
  depends=('archlinux-appstream-data' 'lunaos-appstream-data' 'chaotic-aur-appstream-data')
}

package_archlinux-appstream-data() {
  pkgdesc='Arch Linux AppStream catalogs with reviewed presentation metadata updates'
  license=('GPL')
  cp -a --no-preserve=ownership "$srcdir/archlinux-appstream-data-$pkgver/usr" "$pkgdir/"
}
