import json
from pathlib import Path
import tempfile
import unittest

from metadata_audit import audit_paths, incomplete_records


class MetadataAuditTests(unittest.TestCase):
    def test_reports_remaining_gaps_and_ignores_complete_or_hidden_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.json"
            path.write_text(json.dumps([
                {"repository": "extra", "package": "needs-both", "id": "a.desktop",
                 "status": "imported", "missing": ["description", "screenshots"],
                 "remaining": ["screenshots"]},
                {"repository": "extra", "package": "complete", "id": "b.desktop",
                 "status": "complete", "missing": [], "remaining": []},
                {"repository": "extra", "package": "hidden", "id": "c.desktop",
                 "status": "excluded-by-policy", "missing": ["screenshots"]},
            ]))

            self.assertEqual(audit_paths([temporary]), [path])
            self.assertEqual([row["package"] for row in incomplete_records([path])], ["needs-both"])
            self.assertEqual([row["package"] for row in incomplete_records([path], "screenshots")],
                             ["needs-both"])
            self.assertEqual(incomplete_records([path], "description"), [])


if __name__ == "__main__":
    unittest.main()
