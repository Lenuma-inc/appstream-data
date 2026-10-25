import gzip
from pathlib import Path
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import generate


class ReferencePackageTests(unittest.TestCase):
    def test_arch_package_keeps_only_arch_catalogs_and_uses_enriched_xml(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            for repo in ("extra", "core", "multilib"):
                xml_dir = source / "xml"
                xml_dir.mkdir(parents=True, exist_ok=True)
                catalog = ET.Element("components", version="1.0", origin=f"archlinux-arch-{repo}")
                ET.SubElement(ET.SubElement(catalog, "component", type="desktop-application"), "id").text = repo
                with gzip.open(xml_dir / f"{repo}.xml.gz", "wb") as stream:
                    ET.ElementTree(catalog).write(stream, encoding="utf-8", xml_declaration=True)
                icons = source / "icons" / f"archlinux-arch-{repo}" / "64x64"
                icons.mkdir(parents=True, exist_ok=True)
                (icons / f"{repo}.png").write_bytes(b"icon")

                enriched_dir = root / "work/enrichment/lunaos-repo/catalogs"
                enriched_dir.mkdir(parents=True, exist_ok=True)
                enriched = ET.Element("components", version="1.0", origin=f"archlinux-arch-{repo}")
                component = ET.SubElement(enriched, "component", type="desktop-application")
                ET.SubElement(component, "id").text = f"enriched-{repo}"
                with gzip.open(enriched_dir / f"{repo}-enriched.xml.gz", "wb") as stream:
                    ET.ElementTree(enriched).write(stream, encoding="utf-8", xml_declaration=True)

            args = SimpleNamespace(work=root / "work", output=root / "dist", version="20261025")
            generate.package_reference_catalogs(args, source)
            archive_path = args.output / "archlinux-appstream-data/archlinux-appstream-data-20261025.tar.gz"
            with tarfile.open(archive_path, "r:gz") as archive:
                names = {member.name for member in archive.getmembers() if member.isfile()}
                xmls = {name.rsplit("/", 1)[-1] for name in names if "/swcatalog/xml/" in name}
                self.assertEqual(xmls, {"extra.xml.gz", "core.xml.gz", "multilib.xml.gz"})
                self.assertFalse(any("lunaos-repo" in name or "chaotic-aur" in name for name in names))
                member = next(name for name in names if name.endswith("/swcatalog/xml/extra.xml.gz"))
                catalog = ET.fromstring(gzip.decompress(archive.extractfile(member).read()))
                self.assertEqual(catalog.get("origin"), "archlinux-arch-extra")
                self.assertEqual(catalog.findtext("component/id"), "enriched-extra")


if __name__ == "__main__":
    unittest.main()
