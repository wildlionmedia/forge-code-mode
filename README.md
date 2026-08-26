# FORGE. Code Mode — for DaVinci Resolve

**Drive DaVinci Resolve by writing scripts, not by calling one tool per API method.**

Resolve's scripting API has ~325 methods. The common way to expose it to an AI
agent is an MCP server with one registered tool per method. That doesn't scale:
every tool definition costs context, every call round-trips through the
conversation, and a job touching 5,000 clips becomes 5,000 requests and 5,000
results in the context window.

`forge_resolve` takes the other path — the pattern Cloudflare calls *Code Mode*
and Anthropic calls *code execution with MCP*. The agent imports a thin library,
looks a method up in a **live catalog**, and composes a loop in Python. The loop
runs in Python; only the result comes back.

```python
from forge_resolve import connection, lib

lib.find("relink")                       # discover methods — live, from the installed build
scan = lib.scan_offline()                # read-only
plan = lib.relink_by_root(OLD, NEW)      # dry-run by default
done = lib.relink_by_root(OLD, NEW, dry_run=False)   # allowlist-gated write
```

Not an MCP server. Not a daemon. A library you import.

---

## Why this instead of one-tool-per-method

| | Tool mode (per-method MCP) | Code mode (`forge_resolve`) |
|---|---|---|
| How the agent acts | calls one registered tool per operation | writes a script that calls the API |
| Surface in context | every tool schema, loaded up front | ~nothing; grep the catalog on demand |
| A 5,000-clip job | 5,000 calls + 5,000 results in context | one script, loop in Python, one result |
| Coverage | only what the author wrapped | everything the installed Resolve exposes |
| Version updates | re-wrap tools when the API changes | catalog re-parses the installed README — zero upkeep |
| Safety | per-tool, hand-written | one guard: dry-run default + allowlist + JSONL |

Same underlying Resolve calls — code mode removes the per-call transport and
conversation hops, and never goes stale on a Resolve update.

---

## Requirements

- **DaVinci Resolve Studio** (the free edition has no external scripting).
- External scripting set to **Local**: *Preferences → System → General →
  External scripting using = Local*.
- **Python 3.10+** (the guard's TOML config uses `tomllib`, 3.11+; on 3.10 the
  guard still imports — just supply config via the env var, or use 3.11+).
- Run with the Python that can import `DaVinciResolveScript` (Resolve ships it).

The library auto-bootstraps `RESOLVE_SCRIPT_API` / `RESOLVE_SCRIPT_LIB` /
`PYTHONPATH` from platform defaults; set those env vars to override.

## Install

```bash
git clone https://github.com/USERNAME/forge-code-mode
cd forge-code-mode
pip install -e .            # or just put the folder on PYTHONPATH
cp forge_resolve.example.toml forge_resolve.toml   # then edit the allowlist
```

## The five things you'll actually use

```python
lib.find("relink")            # discover methods — searches names + docs, LIVE
lib.methods("MediaPool")      # all methods on one object, with signatures
lib.describe(handle)          # live dir() vs catalog — spots API drift
lib.scan_offline()            # read-only example: offline clips in a timeline
lib.call(obj, "Method", *a, dry_run=True, project="Name")   # guarded injector
```

Reads are free — call methods straight on the handle (`project.GetName()`).
Writes go through `lib.call(...)` or a `@guarded` helper.

## Discovery is live — never a frozen method list

`lib.catalog()` / `lib.find()` parse Resolve's own README at runtime
(`$RESOLVE_SCRIPT_API/README.txt`), which Resolve overwrites on every update.
Results are cached against that file's mtime, so a Resolve upgrade auto-refreshes
the catalog. The method list is always the installed version's own — nothing
external, nothing to maintain.

## Safety — refusal lives in code, not a prompt

Every mutating call goes through the guard, which enforces three things you
cannot bypass by argument:

1. `dry_run=True` is the **default** — a real write is opt-in, never opt-out.
2. The target project must be on the **allowlist** in `forge_resolve.toml`, or
   the write is **refused** (raised `GuardRefusal`), whatever you passed.
3. Every attempt — executed, dry-run, or refused — is logged to append-only
   JSONL: `{ts, op, project, args, outcome}`.

Fail-closed: no config = empty allowlist = every real write refused.

## Coverage — proven live

The included exerciser (`tests/api_coverage.py`) drives every documented method
in a throwaway project and records the outcome. On **DaVinci Resolve Studio
21.0.4.5**:

- **350 / 375** distinct methods invoked, **0 errors**
- **25** documented as genuinely uncallable unattended (long-running AI, cloud
  login, GUI confirm-modals, full render, DB switch, `Quit`) — no silent gaps
- The live catalog matched the build and flagged real drift (e.g. a documented
  method absent in this version, and live-but-undocumented methods)

Run it yourself (point it at a folder of a few short clips):

```bash
python tests/api_coverage.py /path/to/some/media
# writes tests/api_coverage_report.json + a live progress log
```

See [`docs/RESULTS.md`](docs/RESULTS.md) for the full breakdown and
[`docs/how-we-built-it.md`](docs/how-we-built-it.md) for the build story.

## Layout

```
forge_resolve/
  connection.py   # get to Resolve, reconnect, typed errors
  guard.py        # @guarded + allowlist + JSONL — refusal in code
  lib.py          # live catalog + call() + pool primitives + relink
  examples/relink.py   # a worked job: scan → dry-run → guarded relink
SKILL.md          # how an agent should drive this (write a script, not a tool)
tests/api_coverage.py  # the live API exerciser
```

## Status & support

Reference implementation, community-supported. It's a demonstration of the code
mode pattern applied to Resolve — clean, documented, and runnable — not a
managed product. Issues and PRs welcome; no support SLA.

## License

MIT — see [LICENSE](LICENSE). Built by **Poul Waligora / wildlion.media / FORGE.**
