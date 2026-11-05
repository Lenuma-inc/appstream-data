import gzip
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from generate import reconcile_icons


class IconTests(unittest.TestCase):
    def test_missing_variant_and_component_are_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            catalog = base / 'catalog.gz'
            catalog.write_bytes(gzip.compress(b'''<components>
              <component><id>keep</id><icon type="cached" width="48" height="48">a.png</icon>
              <icon type="cached">a.png</icon></component>
              <component><id>drop</id><icon type="cached">missing.png</icon></component>
              </components>'''))
            supplemental = base / 'supplemental'
            (supplemental / '64x64').mkdir(parents=True)
            (supplemental / '64x64/a.png').write_bytes(b'icon')
            icons = base / 'icons'
            self.assertEqual(reconcile_icons(catalog, icons, supplemental), 1)
            root = ET.fromstring(gzip.decompress(catalog.read_bytes()))
            self.assertEqual([c.findtext('id') for c in root], ['keep'])
            self.assertEqual(len(root.findall('.//icon')), 1)
            self.assertEqual((icons / '64x64/a.png').read_bytes(), b'icon')

    def test_empty_catalog_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            catalog = base / 'catalog.gz'
            catalog.write_bytes(gzip.compress(b'<components><component><id>x</id><icon type="cached">missing.png</icon></component></components>'))
            with self.assertRaisesRegex(ValueError, 'empty after icon'):
                reconcile_icons(catalog, base / 'icons', base / 'supplemental')
