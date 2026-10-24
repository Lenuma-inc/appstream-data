# Package catalogs already generated and published by CI.
pkgbase=lunaos-appstream-data
pkgname=('lunaos-appstream-data' 'chaotic-aur-appstream-data' 'lunaos-appstream-data-common')
pkgver=20261024
pkgrel=4
pkgdesc='AppStream application catalogs for LunaOS and Chaotic-AUR'
arch=('any')
url='https://github.com/Lenuma-inc/appstream-data'
license=('LicenseRef-AppStream-Data')
options=('!strip' '!debug')

source=("${url}/releases/download/${pkgver}-${pkgrel}/lunaos-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/chaotic-aur-appstream-data-${pkgver}.tar.gz")
sha256sums=('fe88a0755b8e0706d9d82859b68fef239964c3aea7d15bc6270f27093caf8a95'
            '9481c4d3c626da83e57c558282df6c1c4b681df4b5a82cb3cde772bf86768a25')

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
