# Package catalogs already generated and published by CI.
pkgbase=lunaos-appstream-data
pkgname=('lunaos-appstream-data' 'chaotic-aur-appstream-data' 'lunaos-appstream-data-common' 'archlinux-appstream-data')
pkgver=20261118
pkgrel=1
pkgdesc='AppStream application catalogs for LunaOS, Arch Linux, and Chaotic-AUR'
arch=('any')
url='https://github.com/Lenuma-inc/appstream-data'
license=('LicenseRef-AppStream-Data')
options=('!strip' '!debug')

source=("${url}/releases/download/${pkgver}-${pkgrel}/lunaos-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/chaotic-aur-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/archlinux-appstream-data-${pkgver}.tar.gz")
sha256sums=('01ee19223f00c2c69ed6ab5166d6a23f8f55e26e41083a69883774751bddbe27'
            '120d356df83c46fd7091905a2ffb94678497ac9ccdb45a0bcb9a0d3caa2b19db'
            'f86d5b27da2bb8ffe982d4b3c5f33dbd240d3fe6e68df0d0610a983264751a03')

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
