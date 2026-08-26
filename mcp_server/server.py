"""
FORGE. Code Mode — MCP front door.

The plug-and-play way to reach code mode from Claude Desktop / Cursor. It is a
SINGLE code-execution server, not one tool per Resolve method — exactly the
"code execution with MCP" pattern. The model writes a script; this runs it
against `forge_resolve` and returns the result.

Three tools:
  resolve_run(code)   — execute Python against forge_resolve, return result
  resolve_find(query) — search the live method catalog (discovery)
  resolve_doctor()    — preflight: is scripting ready?

Requires the MCP SDK (optional extra):  pip install "forge-code-mode[mcp]"
Run:  python -m mcp_server.server        (stdio transport)

Note: resolve_run executes arbitrary Python locally. WRITES still go through the
guard (dry-run default + allowlist + JSONL). Reads via the raw handle are
unrestricted — see the guard's policy options if you need a tighter surface.
"""

from __future__ import annotations

import io
import os
import sys
import json
import contextlib
import traceback

# Make the sibling forge_resolve package importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP   # noqa: E402  (optional dependency)
import forge_resolve                      # noqa: E402
from forge_resolve import connection, lib, guard, doctor   # noqa: E402

mcp = FastMCP("forge-resolve")


def _summarize(value):
    try:
        return json.dumps(value, default=repr)[:4000]
    except Exception:
        return repr(value)[:4000]


@mcp.tool()
def resolve_run(code: str) -> str:
    """
    Execute a Python snippet against DaVinci Resolve via forge_resolve.

    In scope: `fr` (forge_resolve), `connection`, `lib`, `guard`. Reads are
    free; mutations go through the guard (dry_run default + allowlist + JSONL).
    Assign to `result` OR print — both are returned.

    Example:
        scan = lib.scan_offline()
        result = scan
    """
    ns = {"fr": forge_resolve, "connection": connection, "lib": lib,
          "guard": guard}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(code, ns)          # noqa: S102 — code mode is the point
    except Exception:
        return "ERROR:\n" + traceback.format_exc()
    out = buf.getvalue()
    parts = []
    if out.strip():
        parts.append("stdout:\n" + out.rstrip())
    if "result" in ns:
        parts.append("result:\n" + _summarize(ns["result"]))
    return "\n\n".join(parts) if parts else "(no output; set `result` or print)"


@mcp.tool()
def resolve_find(query: str) -> str:
    """Search the live Resolve method catalog by name or doc text."""
    try:
        hits = lib.find(query)
    except Exception:
        return "ERROR:\n" + traceback.format_exc()
    lines = [f"{h['object']}.{h['signature']} -> {h['returns']}  # {h['doc'][:80]}"
             for h in hits[:60]]
    return "\n".join(lines) if lines else f"no methods match {query!r}"


@mcp.tool()
def resolve_doctor() -> str:
    """Preflight: is Resolve scripting reachable and configured?"""
    lines = []
    for r in doctor.run():
        mark = "OK" if r["ok"] else "FAIL"
        lines.append(f"[{mark}] {r['name']}: {r['detail']}"
                     + (f"  (fix: {r['fix']})" if r["fix"] and not r["ok"] else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
