# How I built FORGE. Code Mode for DaVinci Resolve

A source document for the blog and LinkedIn posts. It's the whole story: the
problem, the design, what broke, and the numbers at the end. Pull from it freely.

---

## The problem

DaVinci Resolve's scripting API has around 325 methods across 13 objects –
Resolve, ProjectManager, Project, MediaPool, Timeline, TimelineItem, and the
rest. The standard way to hand that to an AI agent today is an MCP server that
registers one tool per method.

That approach has a ceiling. Every tool definition sits in the model's context
whether it's used or not. Every call is a round-trip: the model emits a tool
call, it crosses the MCP transport to Resolve, the result crosses back, and the
whole exchange lands in the conversation. A job that touches 5,000 clips becomes
5,000 requests and 5,000 results sitting in the context window. You run out of
room before you run out of work.

## The idea: code mode

There's a different pattern. Cloudflare named it *Code Mode*; Anthropic writes
about it as *code execution with MCP*. Instead of the model calling one tool at
a time, it writes code that calls the API, runs that code in an execution
environment, and only the filtered result returns to the conversation.

Applied to Resolve, the shape is a thin Python library the agent imports. It
looks a method up, writes a loop, runs it. The loop runs in Python. Only the
answer comes back. Five thousand clips is one script and one result, not five
thousand round-trips.

The library is `forge_resolve`. Three modules, built and reviewed one at a time.

## Module 1 – connection.py: get to Resolve, stay there

The boring layer, on purpose. It bootstraps the scripting environment
(`RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, `PYTHONPATH`) from platform
defaults, imports Blackmagic's scripting module, and caches a single Resolve
handle. It detects a dead handle and reconnects instead of throwing. Lookups
(`get_project`, `get_timeline`) return the real object or raise a typed error –
`ResolveNotRunning`, `ProjectNotFound`, `TimelineNotFound`. They never return
None silently.

One design call worth naming: the import-time check fails only if the scripting
library is missing (which proves Resolve Studio isn't installed). It does not
require Resolve to be *running* at import – that's what the lazy, self-healing
handle is for. So you can import and inspect the package with Resolve closed.

## Module 2 – guard.py: refusal lives in code

This is the safety layer, and it runs regardless of what any caller intended. A
`@guarded` decorator enforces three things at once:

1. Every mutating operation takes `dry_run=True` by default. You opt in to
   writing, never out of it.
2. A project allowlist, read from a config file. A real write to a project not
   on the list is refused – whatever the argument says.
3. An append-only JSONL log. Every attempt (executed, dry-run, or refused) is
   one line: timestamp, operation, project, arguments, outcome.

Refusal is a raised exception, not a return value you can ignore. And it's
fail-closed: no config means an empty allowlist means every real write is
refused until you name the projects you trust. The guard is code that runs, not
a sentence in a prompt or a docstring that a model can talk itself past.

## Module 3 – lib.py: the library of methods, with a live catalog

This is where code mode earns its keep, and where the most important decision
lives: **the method list is never frozen.**

A first instinct is to wrap all 325 methods, or to ship a generated catalog. Both
go stale the moment Resolve updates. So instead, `lib.catalog()` reads Resolve's
own README at runtime, from `$RESOLVE_SCRIPT_API/README.txt` – the file that
ships *inside* the installed Resolve and gets overwritten on every update. It
parses that into a structured catalog and caches it against the file's
modification time. Upgrade Resolve, and the catalog re-parses itself. The list
is always the installed version's own, taken from the installed version, nothing
external, nothing to maintain.

On top of the catalog:

- `lib.find("relink")` – search names and docs, live.
- `lib.methods("MediaPool")` – every method on an object, with signatures.
- `lib.describe(handle)` – cross-check the catalog against the live object's
  `dir()`, so you can see where the README and the running build disagree.
- `lib.call(obj, "Method", ..., dry_run=True, project=...)` – a generic guarded
  injector, so *any* of the 325 methods is reachable and still safe. You're never
  blocked waiting for someone to wrap a method.
- A handful of composed helpers (offline scan, relink) that show the pattern.

Reads are free – you call them straight on the handle. Writes go through the
guard.

## Proving it: exercise every method

A library is a claim. The way to test the claim is to actually invoke the API
against a running Resolve. So I built an exerciser that spins up a throwaway
project, imports a few clips, and drives every documented method, recording each
outcome into one of four buckets: called-and-returned-a-value, called-and-
returned-nothing, errored, or skipped-with-a-reason. No silent gaps.

That's where the honest part of the story is.

## What broke, and what it taught

The first full run hung with no output. Lesson one: I was only printing at the
end, so a single blocking call left me blind. I added per-call progress logging
that writes the method name *before* it runs, so any hang names its own culprit.
The blocker turned out to be a long-running AI method (speech transcription,
which downloads a model and blocks for minutes). Those got an explicit
exclusion list, with reasons.

The next run completed but showed a wave of `'NoneType' is not callable` errors
from one object onward. Root cause: some methods reload the project session
(`LoadProject`, `CloseProject`), which silently invalidates every live object
reference captured earlier. Everything downstream was calling methods on dead
handles. Fix: defer the session-breakers, and rebuild fixtures cleanly.

Then Resolve itself went unresponsive – scripting returned a null handle even
from a fresh process. It looked like a crash. It wasn't. Resolve was running the
whole time. My exerciser's project-lifecycle calls on an unsaved project
(`DeleteProject`, `CloseProject`) pop a GUI confirm modal, and a modal blocks the
entire scripting server until a human clicks it. Nothing a headless script can
do. Lesson: a class of methods is genuinely uncallable unattended – the same
class as `Quit`. They got documented and excluded, not forced.

Two smaller ones. A phantom method (`DeleteRenderJobByIndex`) is in the README
but not in the running 21.0.4.5 build – the live catalog caught it as drift, and
it's recorded as a skip, not an error. And one method I'd skipped as unsafe,
`GetCurrentDatabase`, is actually read-only – over-caution on my part, corrected
so it runs. (It reported that this Resolve runs on a PostgreSQL project library,
exactly the kind of live fact the sweep surfaces.)

None of these were Resolve bugs. They were the exerciser poking the API in ways a
normal script wouldn't, and each one hardened the tool.

## The result

On DaVinci Resolve Studio 21.0.4.5:

- **350 of 375** distinct documented methods invoked, **0 errors**.
- **25** documented as genuinely uncallable in an unattended sweep: long-running
  AI (9), cloud-account features (4), GUI confirm-modals (5), full render (1),
  database switch (1), archive restore (1), `Quit` (1), and the one README-drift
  phantom (1).

So the honest ceiling is not "100% of 375." It's "100% of everything that can
run headless, with zero errors, and a documented reason for each of the 25 that
can't." That nuance is the most credible part of the result, not a footnote to
hide.

Full breakdown in `RESULTS.md`.

## Code mode vs one-tool-per-method

| | Tool mode (per-method MCP) | Code mode (forge_resolve) |
|---|---|---|
| How the agent acts | one registered tool per call | writes a script that calls the API |
| Context cost | every tool schema up front | ~nothing; grep the catalog on demand |
| A 5,000-clip job | 5,000 calls + results in context | one script, loop in Python, one result |
| Coverage | only what was wrapped | everything the build exposes |
| Version updates | re-wrap on API change | catalog re-parses the installed README |
| Safety | per-tool, hand-written | one guard: dry-run + allowlist + JSONL |

Same Resolve calls underneath. Code mode removes the per-call transport and the
conversation round-trip, and it doesn't rot when Resolve updates.

## What it's for, and what it isn't

It's a reference implementation of code mode applied to a real, large, stateful
API – clean, documented, and runnable. It's not a managed product and it doesn't
try to be. The point is the pattern: give the agent a thin library and a live
catalog, keep the loop in code, keep refusal in code, and read the method list
from the software you're actually driving.

MIT licensed. Built by Poul Waligora / wildlion.media / FORGE.
