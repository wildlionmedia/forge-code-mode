#!/usr/bin/env python3
"""
examples/relink.py – a worked example of code mode. NOT part of the API.

This is the shape every job takes: import forge_resolve, discover/compose,
run it, read the result. Run it straight from a terminal:

    RESOLVE_SCRIPT_API=... python3 -m forge_resolve.examples.relink \
        /old/root /new/root [ProjectName]

Reads are free; the one real write goes through the guard (dry_run=False),
so it is refused unless ProjectName is on the allowlist in forge_resolve.toml.

Examples CAN print – they are the human-facing demo. Library code never does.
"""

import sys
import json

from forge_resolve import connection, lib


def main(argv):
    if len(argv) < 3:
        print("usage: relink.py OLD_ROOT NEW_ROOT [PROJECT]")
        return 2
    old_root, new_root = argv[1], argv[2]
    project = connection.get_project(argv[3] if len(argv) > 3 else None)

    # 1. Look at the damage (read-only).
    scan = lib.scan_offline(project=project)
    print(f"[scan] {scan['offline_count']}/{scan['total']} clips offline "
          f"in timeline {scan['timeline']!r}")

    # 2. Dry run – what WOULD change, changing nothing.
    plan = lib.relink_by_root(old_root, new_root, project=project)  # dry_run=True
    print(f"[dry ] would relink {len(plan['planned'])}, "
          f"{len(plan['unresolved'])} unresolved")

    # 3. The real write – guarded. Refused unless the project is allowlisted.
    done = lib.relink_by_root(old_root, new_root,
                              project=project, dry_run=False)
    print(f"[real] relinked {done['relinked']}, failed {len(done['failed'])}")

    print(json.dumps(done, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
