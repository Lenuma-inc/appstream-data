# Package catalogs already generated and published by CI.
pkgbase=lunaos-appstream-data
pkgname=('lunaos-appstream-data' 'chaotic-aur-appstream-data' 'lunaos-appstream-data-common')
pkgver=20261023
pkgrel=1
pkgdesc='AppStream application catalogs for LunaOS and Chaotic-AUR'
arch=('any')
url='https://github.com/Lenuma-inc/appstream-data'
license=('LicenseRef-AppStream-Data')
options=('!strip' '!debug')

source=("${url}/releases/download/${pkgver}-${pkgrel}/lunaos-appstream-data-${pkgver}.tar.gz"
        "${url}/releases/download/${pkgver}-${pkgrel}/chaotic-aur-appstream-data-${pkgver}.tar.gz")
sha256sums=('d87d6bd34a9c5472199f2d56f5629db4014fa0b7211a92613b83a478da8ce2b3'
            'a95f22c7f32cccd3bcb812a63f2b53e970fd52b1a1244050d21fae80881f1bc9')

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
