# Package catalogs already generated and published by CI.
pkgbase=lunaos-appstream-data
pkgname=('lunaos-appstream-data' 'chaotic-aur-appstream-data' 'lunaos-appstream-data-common' 'archlinux-appstream-data')
pkgver=20261030
pkgrel=1
pkgdesc='AppStream application catalogs for LunaOS, Arch Linux, and Chaotic-AUR'
arch=('any')
url='https://github.com/Lenuma-inc/appstream-data'
license=('LicenseRef-AppStream-Data')
options=('!strip' '!debug')

source=("${url}/releases/download/${pkgver}-${pkgrel}/lunaos-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/chaotic-aur-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/archlinux-appstream-data-${pkgver}.tar.gz")
sha256sums=('87c7b752c7b53b95ce36f93f0df47759fc2d4ec01f87949ccf2abb2207ed5857'
            'ed5c31bcf96e64cde3277839543c60d3f8afd877b3efa7129a766a3fd23185a3'
            '07525487e635cec4884faa8a6bfd6304c65f842ddcd7b7bcb04e6c30e3aa25f7')

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
