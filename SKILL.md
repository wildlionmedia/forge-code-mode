# forge_resolve – code mode for DaVinci Resolve

**Write a script. Don't ask for a tool.**

Resolve has ~325 scripting methods. This package does not wrap them one by one.
You import it, look a method up in a live catalog, compose a loop in Python, and
run it. The loop runs in Python; only the result comes back. A job touching
5,000 clips is one script, not 5,000 round-trips.

## Import

```python
from forge_resolve import connection, guard, lib
```

`connection` gets you to Resolve. `lib` is the library of methods. `guard`
refuses unsafe writes. That's the whole surface.

## The five things you'll actually use

```python
lib.find("relink")            # discover methods – searches names + docs, LIVE
lib.methods("MediaPool")      # all methods on one object, with signatures
lib.describe(handle)          # live dir() vs catalog – spots API drift
lib.scan_offline()            # read-only example: offline clips in a timeline
lib.call(obj, "Method", *a, dry_run=True, project="Name")   # guarded injector
```

## Discovery is live – never hard-code a method list

`lib.catalog()` / `lib.find()` parse Resolve's own README at runtime
(`$RESOLVE_SCRIPT_API/README.txt`), which Resolve overwrites on every update.
The catalog re-parses automatically when that file changes. So the method list
always matches the installed Resolve. When you need a method, `find()` it – do
not guess a signature from memory.

## Reads are free. Writes go through the guard.

- **Reads**: call the method straight on the handle – `project.GetName()`,
  `mpi.GetClipProperty("File Path")`. No ceremony.
- **Writes (anything mutating)**: go through `lib.call(...)` or a `@guarded`
  helper. The guard enforces three things you cannot bypass by argument:
  1. `dry_run=True` is the **default** – a real write is opt-in, never opt-out.
  2. the target project must be on the **allowlist** in `forge_resolve.toml`,
     or the write is **refused** (raised `GuardRefusal`), whatever you passed.
  3. every attempt – executed, dry-run, or refused – is logged to the JSONL
     audit path.

Preview first (`dry_run=True`), read the plan, then commit (`dry_run=False`).

## Getting a handle for `call()`

```python
project  = connection.get_project()          # current, or get_project("Name")
mp       = project.GetMediaPool()             # reads are free
lib.call(mp, "RelinkClips", items, folder,    # the write, guarded
         dry_run=False, project=project)
```

## Worked example

`forge_resolve/examples/relink.py` – scan → dry-run → guarded real relink,
end to end. Read it to see the shape, then write your own job the same way.

## Read the code, not a restatement

- `connection.py` – handles, reconnect, typed errors.
- `guard.py` – `@guarded`, allowlist, JSONL. The refusal lives in code.
- `lib.py` – catalog/find/methods/describe/call + pool primitives + relink.

If a method you need isn't a named helper yet, you don't wait – `lib.call(...)`
reaches any of the ~325 methods, safely.
