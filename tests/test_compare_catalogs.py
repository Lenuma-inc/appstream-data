import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "compare_catalogs", Path(__file__).resolve().parents[1] / "compare-catalogs.py")
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def catalog(body):
    return {"catalog.xml.gz": compare.normalized_catalog(
        f'<components>{body}</components>'.encode())}


class ReleaseNotesTests(unittest.TestCase):
    def test_component_and_icon_changes(self):
        old = catalog('<component><id>removed</id></component>'
                      '<component><id>updated</id><name>Old</name></component>')
        new = catalog('<component><id>added</id></component>'
                      '<component><id>updated</id><name>New</name></component>')
        notes = compare.release_notes([
            ('test', old, {'removed': 'a', 'updated': 'a'},
             new, {'added': 'b', 'updated': 'b'})], '20261030-1', '20261105-1')
        for expected in ('`20261030-1` → `20261105-1`',
                         '### Added (1)\n\n- `added`',
                         '### Removed (1)\n\n- `removed`',
                         '### Updated (1)\n\n- `updated`',
                         'Icons: 1 added, 1 removed, 1 updated.'):
            self.assertIn(expected, notes)

    def test_first_release_and_unchanged(self):
        current = catalog('<component><id>app</id></component>')
        first = compare.release_notes([('test', {}, {}, current, {})])
        self.assertIn('### Added (1)\n\n- `app`', first)
        unchanged = compare.release_notes([('test', current, {}, current, {})])
        for label in ('Added', 'Removed', 'Updated'):
            self.assertIn(f'### {label} (0)\n\nNone.', unchanged)
