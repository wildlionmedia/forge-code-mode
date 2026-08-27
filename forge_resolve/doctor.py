"""
doctor.py – preflight check. Tells you exactly what's wrong before you script.

    python -m forge_resolve.doctor

Runs a series of checks (env → scripting lib → module import → live handle →
scripting=Local → config → live catalog) and prints a pass/fail report with a
fix for each failure. Exit code 0 if everything a script needs is ready.
"""

from __future__ import annotations

import os
import sys


def _check_env():
    try:
        from . import connection
        connection._bootstrap_env()
        api = os.environ.get("RESOLVE_SCRIPT_API", "")
        lib_ = os.environ.get("RESOLVE_SCRIPT_LIB", "")
        return True, f"RESOLVE_SCRIPT_API={api}", ""
    except Exception as exc:
        return False, str(exc), "Set RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB."


def _check_lib():
    lib_ = os.environ.get("RESOLVE_SCRIPT_LIB", "")
    if lib_ and os.path.exists(lib_):
        return True, lib_, ""
    return (False, f"missing: {lib_}",
            "Install DaVinci Resolve STUDIO (free edition has no scripting).")


def _check_import():
    try:
        import DaVinciResolveScript  # noqa: F401
        return True, "DaVinciResolveScript importable", ""
    except Exception as exc:
        return (False, str(exc),
                "RESOLVE_SCRIPT_API/Modules is wrong, or Studio not installed.")


def _check_handle():
    try:
        from . import connection
        r = connection.get_resolve()
        return True, f"{r.GetProductName()} {r.GetVersionString()}", ""
    except Exception as exc:
        return (False, str(exc),
                "Launch Resolve, and set Preferences > System > General > "
                "External scripting using = Local.")


def _check_config():
    try:
        from . import guard
        cfg = guard.load_config()
        n = len(cfg["allowlist"])
        if n:
            return True, f"{n} project(s) allowlisted; log -> {cfg['log_path']}", ""
        return (True, "config OK but allowlist EMPTY (fail-closed: no real "
                "writes permitted)",
                "Add project names to forge_resolve.toml to permit writes.")
    except Exception as exc:
        return False, str(exc), "Create forge_resolve.toml from the example."


def _check_catalog():
    try:
        from . import lib
        cat = lib.catalog()
        total = sum(len({m['name'] for m in v}) for v in cat.values())
        return True, f"{total} methods across {len(cat)} objects", ""
    except Exception as exc:
        return False, str(exc), "Scripting README not found; check RESOLVE_SCRIPT_API."


CHECKS = [
    ("environment bootstrap", _check_env),
    ("scripting library present", _check_lib),
    ("DaVinciResolveScript import", _check_import),
    ("live Resolve handle", _check_handle),
    ("guard config", _check_config),
    ("live method catalog", _check_catalog),
]


def run() -> list:
    """Return a list of {name, ok, detail, fix} – usable programmatically."""
    out = []
    for name, fn in CHECKS:
        try:
            ok, detail, fix = fn()
        except Exception as exc:
            ok, detail, fix = False, str(exc), ""
        out.append({"name": name, "ok": ok, "detail": detail, "fix": fix})
    return out


def main() -> int:
    # ASCII-only console output: a Windows terminal (cp1252) garbles non-ASCII.
    results = run()
    print("forge_resolve doctor\n" + "-" * 60)
    all_ok = True
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"[{mark}] {r['name']}: {r['detail']}")
        if not r["ok"]:
            all_ok = False
            if r["fix"]:
                print(f"        fix: {r['fix']}")
    print("-" * 60)
    print("READY: you can run scripts." if all_ok
          else "NOT READY: fix the FAIL lines above.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
