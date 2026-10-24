# Package catalogs already generated and published by CI.
pkgbase=lunaos-appstream-data
pkgname=('lunaos-appstream-data' 'chaotic-aur-appstream-data' 'lunaos-appstream-data-common')
pkgver=20261024
pkgrel=2
pkgdesc='AppStream application catalogs for LunaOS and Chaotic-AUR'
arch=('any')
url='https://github.com/Lenuma-inc/appstream-data'
license=('LicenseRef-AppStream-Data')
options=('!strip' '!debug')

source=("${url}/releases/download/${pkgver}-${pkgrel}/lunaos-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/chaotic-aur-appstream-data-${pkgver}.tar.gz")
sha256sums=('5fa5e36198a48215bacf8d3dd5d9ad2dcb7f193e490593712205ebc9c57d7697'
            '924c3330d83a20c31868bff5c76ba57b133cfa89837783355c9219747d1f38c1')

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
  pkgdesc='AppStream catalogs for LunaOS, Arch Linux and Chaotic-AUR'
  license=('MIT')
  depends=('archlinux-appstream-data' 'lunaos-appstream-data' 'chaotic-aur-appstream-data')
}
