#!/usr/bin/env python3
"""List desktop-app catalog entries that still need presentation metadata."""

import argparse
from collections import Counter
import json
from pathlib import Path


FIELDS = ("description", "screenshots", "icons")
DEFAULT_AUDITS = tuple(Path("work/enrichment").glob("*/catalogs/audit.json"))


def audit_paths(inputs):
    paths = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            paths.extend(path.rglob("audit.json"))
        elif path.is_file():
            paths.append(path)
        else:
            raise FileNotFoundError(f"Audit file or directory not found: {path}")
    return sorted(set(paths))


def incomplete_records(paths, field=None):
    records = []
    for path in paths:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            raise ValueError(f"Expected an audit record list in {path}")
        for record in data:
            missing = record.get("remaining", record.get("missing", []))
            if record.get("status") == "excluded-by-policy" or not missing:
                continue
            if field and field not in missing:
                continue
            records.append({**record, "audit_file": str(path), "remaining": missing})
    return sorted(records, key=lambda row: (
        row.get("repository", ""), row.get("package", ""), row.get("id", "")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="audit.json files or directories containing them")
    parser.add_argument("--field", choices=FIELDS, help="show only entries missing this field")
    args = parser.parse_args(argv)

    paths = audit_paths(args.paths) if args.paths else list(DEFAULT_AUDITS)
    if not paths:
        parser.error("no audit.json found; pass a file/directory or run catalog generation first")
    records = incomplete_records(paths, args.field)
    if not records:
        print(f"No incomplete app metadata in {len(paths)} audit file(s).")
        return 0

    counts = Counter(field for row in records for field in row["remaining"])
    print(f"{len(records)} incomplete app(s) across {len(paths)} audit file(s): " +
          ", ".join(f"{name}={counts[name]}" for name in FIELDS if counts[name]))
    for row in records:
        probe = row.get("upstream_check", {})
        source_id = f"; source={row['source_id']} ({row['match']})" if row.get("source_id") else ""
        source = f"; upstream={probe['status']}" if probe.get("status") else ""
        url = f"; {probe['url']}" if probe.get("url") else ""
        print("- {repository}/{package} ({id}): missing {fields}; status={status}{source_id}{source}{url}".format(
            repository=row.get("repository", "?"), package=row.get("package", "?"),
            id=row.get("id", "?"), fields=", ".join(row["remaining"]),
            status=row.get("status", "unknown"), source_id=source_id, source=source, url=url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
