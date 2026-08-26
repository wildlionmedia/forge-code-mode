# MCP front door (optional)

The plug-and-play way to reach FORGE. Code Mode from Claude Desktop or Cursor.

It is **one** code-execution server, not one tool per Resolve method — the
"code execution with MCP" pattern. The model writes a script; the server runs it
against `forge_resolve` and returns the result. You get code mode's reach and
context efficiency without wiring 325 tools into the model.

## Tools

| Tool | Does |
|---|---|
| `resolve_run(code)` | execute Python against `forge_resolve`, return `result`/stdout |
| `resolve_find(query)` | search the live method catalog (discovery) |
| `resolve_doctor()` | preflight: is Resolve scripting reachable and configured? |

Reads are free; writes go through the guard (dry-run default + allowlist +
JSONL). `resolve_run` executes arbitrary local Python — see the guard for
tightening the surface if you need it.

## Install

```bash
pip install -e ".[mcp]"     # pulls in the MCP SDK
forge-resolve-doctor        # confirm scripting is ready first
```

## Wire into Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "forge-resolve": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/forge-code-mode"
    }
  }
}
```

Restart Claude Desktop. Then ask it to, e.g., "scan the current timeline for
offline clips" — it will call `resolve_run` with a short script.

## Wire into Cursor

Point Cursor's MCP settings at the same command
(`python -m mcp_server.server`, `cwd` = the repo root).

## Why a single tool, not 325

Loading every method as a tool floods the context and degrades the model's
selection. Here the model discovers with `resolve_find` and composes real loops
inside `resolve_run` — so a 5,000-clip job is one call, not 5,000.
