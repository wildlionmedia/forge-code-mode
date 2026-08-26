# How I built FORGE. Code Mode for DaVinci Resolve

I gave an AI agent every one of DaVinci Resolve's 325 scripting methods without
wrapping a single one as a tool. It drove 350 of them against a live project,
made zero errors, and told me which 25 it couldn't touch and why. Here's how it
works, and everything that broke on the way there.

*(This doc is the source for the blog and LinkedIn posts. Pull from it freely.)*

## The problem

Resolve's scripting API has around 325 methods across 13 objects – Resolve,
ProjectManager, Project, MediaPool, Timeline, TimelineItem, and the rest. The
usual way to hand that to an AI agent is an MCP server that registers one tool
per method.

That has a ceiling. Every tool definition sits in the model's context whether it
gets used or not. It's like loading the mule with every tool in the shop before
you know which job you're walking to. And every call is a round trip: the model
emits a tool call, it crosses the transport to Resolve, the result crosses back,
and the whole exchange lands in the conversation. A job that touches 5,000 clips
becomes 5,000 requests and 5,000 results in the context window. You run out of
room before you run out of work.

## The other path: code mode

There's a different shape. Cloudflare calls it Code Mode. Anthropic writes about
it as code execution with MCP. The model writes code that calls the API, runs
that code in an execution environment, and only the result comes back.

Point it at Resolve and it becomes a thin Python library the agent imports. It
looks a method up, writes a loop, runs it. The loop runs in Python. Only the
answer returns. Five thousand clips is one script and one result.

The library is `forge_resolve`. Three modules, built and reviewed one at a time.

## Module 1 – connection.py: get to Resolve, stay there

The boring layer, on purpose. It bootstraps the scripting environment
(`RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`, `PYTHONPATH`) from platform
defaults, imports Blackmagic's scripting module, and caches a single Resolve
handle. It detects a dead handle and reconnects. Lookups like `get_project` and
`get_timeline` return the real object or raise a typed error – `ResolveNotRunning`,
`ProjectNotFound`, `TimelineNotFound`. They never return None and leave you
guessing.

One call worth naming: the import-time check fails only when the scripting
library is missing, which means Resolve Studio isn't installed. It does not
require Resolve to be running at import. That's what the lazy, self-healing
handle is for. You can import and read the package with Resolve closed.

## Module 2 – guard.py: refusal lives in code

This is the safety layer, and it runs no matter what the caller intended. A
`@guarded` decorator enforces three things at once.

One. Every mutating operation takes `dry_run=True` by default. You opt in to
writing. You never opt out.

Two. A project allowlist, read from a config file. A real write to a project
that isn't on the list is refused, whatever the argument says.

Three. An append-only JSONL log. Every attempt – executed, dry-run, or refused –
is one line: timestamp, operation, project, arguments, outcome.

Refusal is a raised exception, not a return value you can ignore. And it's
fail-closed. No config means an empty allowlist means every real write is
refused until you name the projects you trust. The guard is code that runs, not
a sentence in a prompt a model can talk itself past.

## Module 3 – lib.py: the library of methods, with a live catalog

This is where the important decision lives. The method list is never frozen.

The lazy instinct is to wrap all 325 methods, or to ship a generated catalog.
Both go stale the moment Resolve updates. So instead `lib.catalog()` reads
Resolve's own README at runtime, from `$RESOLVE_SCRIPT_API/README.txt` – the file
that ships inside the installed Resolve and gets overwritten on every update. It
parses that into a structured catalog and caches it against the file's
modification time. Upgrade Resolve, and the catalog re-parses itself. The list
is always the installed version's own, read from the installed version, nothing
external, nothing to maintain.

On top of the catalog:

- `lib.find("relink")` searches names and docs, live.
- `lib.methods("MediaPool")` lists every method on an object, with signatures.
- `lib.describe(handle)` cross-checks the catalog against the live object's
  `dir()`, so you see where the README and the running build disagree.
- `lib.call(obj, "Method", ..., dry_run=True, project=...)` is a generic guarded
  injector, so any of the 325 methods is reachable and still safe. You're never
  blocked waiting for someone to wrap a method.
- A few composed helpers – offline scan, relink – show the pattern.

Reads are free. You call them straight on the handle. Writes go through the
guard.

## Proving it: exercise every method

A library is a claim. The way to test the claim is to invoke the API against a
running Resolve. So I built an exerciser that spins up a throwaway project,
imports a few clips, and drives every documented method, recording each outcome
into one of four buckets: called and returned a value, called and returned
nothing, errored, or skipped with a reason. No silent gaps.

That's where the honest part of the story is.

## What broke, and what it taught

The first full run hung with no output. Lesson one: I was only printing at the
end, so a single blocking call left me blind. I added per-call progress logging
that writes the method name before it runs, so any hang names its own culprit.
The blocker was a long-running AI method – speech transcription, which downloads
a model and blocks for minutes. Those got an explicit exclusion list, each with a
reason.

The next run finished but threw a wave of `'NoneType' is not callable` errors
from one object onward. Root cause: some methods reload the project session –
`LoadProject`, `CloseProject` – and that silently invalidates every live object
reference captured earlier. Everything downstream was calling methods on dead
handles. Fix: defer the session-breakers, and rebuild fixtures clean.

Then Resolve went unresponsive. Scripting returned a null handle even from a
fresh process. It looked like a crash. It wasn't. Resolve was running the whole
time. My exerciser's project-lifecycle calls on an unsaved project –
`DeleteProject`, `CloseProject` – pop a GUI confirm modal, and a modal blocks the
whole scripting server until a human clicks it. Nothing a headless script can do.
Lesson: a class of methods is genuinely uncallable unattended, the same class as
`Quit`. They got documented and excluded, not forced.

Two smaller ones. A phantom method, `DeleteRenderJobByIndex`, is in the README
but not in the running 21.0.4.5 build. The live catalog caught it as drift and
recorded it as a skip, not an error. And one method I'd skipped as unsafe,
`GetCurrentDatabase`, turned out to be read-only – over-caution on my part,
corrected so it runs. It reported that this Resolve is on a PostgreSQL project
library, the kind of live fact the sweep surfaces.

None of these were Resolve bugs. They were the exerciser poking the API in ways a
normal script wouldn't, and each one hardened the tool.

## The result

On DaVinci Resolve Studio 21.0.4.5:

- 350 of 375 distinct documented methods invoked, 0 errors.
- 25 documented as genuinely uncallable in an unattended sweep: long-running AI
  (9), cloud-account features (4), GUI confirm-modals (5), full render (1),
  database switch (1), archive restore (1), `Quit` (1), and the one README-drift
  phantom (1).

Here's the honest ceiling. Everything that can run headless, runs. Zero errors.
Each of the 25 that can't has a reason sitting next to it in the report. That
number is not 100% of 375, and I won't dress it up as one. The 25 are the most
credible part of the result. They don't belong in a footnote.

Full breakdown in `RESULTS.md`.

## Code mode against one-tool-per-method

| | Tool mode (per-method MCP) | Code mode (forge_resolve) |
|---|---|---|
| How the agent acts | one registered tool per call | writes a script that calls the API |
| Context cost | every tool schema up front | almost nothing; grep the catalog on demand |
| A 5,000-clip job | 5,000 calls and results in context | one script, loop in Python, one result |
| Coverage | only what was wrapped | everything the build exposes |
| Version updates | re-wrap on API change | catalog re-parses the installed README |
| Safety | per-tool, hand-written | one guard: dry-run, allowlist, JSONL |

Same Resolve calls underneath. Code mode drops the per-call transport and the
conversation round trip, and it doesn't rot when Resolve updates.

There's a fair question here: doesn't the model need all 325 methods in front of
it? No. You tell it what you want. It writes a script. It has SKILL.md for the
pattern, `lib.find()` for discovery, and when a script fails it reads the real
traceback and fixes itself. That's a feedback loop, not a lookup table. A static
tool menu can't tell the model why a call was wrong in context. A traceback can.

## What it's for, and what it isn't

It's a reference implementation of code mode against a real, large, stateful API.
Clean, documented, runnable. It isn't a managed product and doesn't pretend to
be. I've run it on one machine, macOS, Resolve 21.0.4.5. Windows and other
versions are untested. The paths are in `connection.py` and should work, but I
haven't proven it, and I'd rather say so than imply a matrix I don't have.

The point is the pattern. Give the agent a thin library and a live catalog. Keep
the loop in code. Keep refusal in code. Read the method list from the software
you're actually driving.

MIT licensed. Built by Poul Waligora / wildlion.media / FORGE.
