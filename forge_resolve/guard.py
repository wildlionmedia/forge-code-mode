"""
guard.py – the refusal layer. It runs regardless of what any caller intended.

Three things, enforced together by the @guarded decorator:

  1. dry_run defaults to True. Callers opt IN to writing, never out of it.
  2. A project allowlist, read from config. A real write (dry_run=False) to a
     project not on the list is refused, whatever the argument says.
  3. An append-only JSONL log. Every attempt – executed, dry-run, or refused – 
     gets one line: timestamp, operation, project, arguments, outcome.

Refusal is a raised GuardRefusal, never a return value that can be ignored.

Fail-closed: if there is no config file, the allowlist is empty, so every
real write is refused until you name the projects you trust.

Config (TOML, read with stdlib tomllib) is searched at:
  1. $FORGE_RESOLVE_CONFIG
  2. forge_resolve.toml next to the package directory (repo root)

  # forge_resolve.toml
  allowlist = ["Scratch_Relink_Test"]   # project names writable for real
  log_path  = "forge_resolve.guard.jsonl"  # optional; relative to this file
"""

from __future__ import annotations

import os
import json
import functools
import inspect
import tomllib
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# Exception
# --------------------------------------------------------------------------- #

class GuardRefusal(Exception):
    """A guarded operation was refused. Raised, never returned."""


# --------------------------------------------------------------------------- #
# Paths – config and log both live at the repo root, next to the package dir.
# --------------------------------------------------------------------------- #

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_PACKAGE_DIR)

_DEFAULT_CONFIG = os.path.join(_ROOT_DIR, "forge_resolve.toml")
_DEFAULT_LOG = os.path.join(_ROOT_DIR, "forge_resolve.guard.jsonl")


def _config_path() -> str:
    return os.environ.get("FORGE_RESOLVE_CONFIG") or _DEFAULT_CONFIG


def _empty_policy() -> dict:
    return {"mode": "normal", "method_allowlist": [], "method_denylist": [],
            "block_destructive": False}


def load_config() -> dict:
    """
    Read the TOML config. Missing file -> empty allowlist (fail-closed).

    Shaped: {"allowlist": [...], "log_path": str, "policy": {...}}.
    """
    path = _config_path()
    if not os.path.exists(path):
        return {"allowlist": [], "log_path": _DEFAULT_LOG,
                "policy": _empty_policy()}

    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    allowlist = list(raw.get("allowlist", []))

    log_path = raw.get("log_path")
    if log_path:
        # Relative log paths are resolved against the config file's folder.
        if not os.path.isabs(log_path):
            log_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                                    log_path)
    else:
        log_path = _DEFAULT_LOG

    pol_raw = raw.get("policy", {})
    policy = {
        "mode": pol_raw.get("mode", "normal"),
        "method_allowlist": list(pol_raw.get("method_allowlist", [])),
        "method_denylist": list(pol_raw.get("method_denylist", [])),
        "block_destructive": bool(pol_raw.get("block_destructive", False)),
    }

    return {"allowlist": allowlist, "log_path": log_path, "policy": policy}


# --------------------------------------------------------------------------- #
# Allowlist
# --------------------------------------------------------------------------- #

def is_allowed(project_name: str | None) -> bool:
    """True if this project name may be written to for real."""
    if not project_name:
        return False
    return project_name in load_config()["allowlist"]


# --------------------------------------------------------------------------- #
# Method policy – an optional second gate for constraining the surface, useful
# when writes may be composed freely (e.g. the resolve_run MCP tool).
# --------------------------------------------------------------------------- #

# Clearly-destructive Resolve method names. block_destructive refuses these
# unless the method is on the method_allowlist.
DESTRUCTIVE = {
    "DeleteTimelines", "DeleteClips", "DeleteFolders", "DeleteProject",
    "DeleteFolder", "CloseProject", "DeleteMarkersByColor", "DeleteAllRenderJobs",
    "DeleteRenderPreset", "DeleteColorGroup", "ReplaceClip",
    "ReplaceClipPreserveSubClip", "UnlinkClips", "DeleteVersionByName",
    "DeleteTakeByIndex", "DeleteFusionCompByName", "Quit", "SetCurrentDatabase",
}


def refusal_reason(op_name: str, project_name: str | None,
                   dry_run: bool) -> str | None:
    """
    Return a reason string if a REAL call should be refused, else None.
    Central gate used by both @guarded helpers and lib.call().
    Previews (dry_run) are never refused here – they change nothing.
    """
    if dry_run:
        return None
    pol = load_config()["policy"]
    if pol["mode"] == "readonly":
        return f"read-only mode is on; {op_name!r} is a write"
    if op_name in pol["method_denylist"]:
        return f"{op_name!r} is on the method denylist"
    if pol["method_allowlist"] and op_name not in pol["method_allowlist"]:
        return f"{op_name!r} is not on the method allowlist"
    if (pol["block_destructive"] and op_name in DESTRUCTIVE
            and op_name not in pol["method_allowlist"]):
        return f"{op_name!r} is destructive and block_destructive is on"
    if not is_allowed(project_name):
        return f"project {project_name!r} is not on the allowlist"
    return None


# --------------------------------------------------------------------------- #
# Append-only JSONL log
# --------------------------------------------------------------------------- #

def _sanitize(value):
    """Make an argument value safe to JSON-serialize; fall back to repr."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def log_event(op: str, project: str | None, args: dict, outcome: str,
              detail=None) -> None:
    """Append one line to the JSONL log. Every attempt is logged."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "op": op,
        "project": project,
        "args": {k: _sanitize(v) for k, v in args.items()},
        "outcome": outcome,
    }
    if detail is not None:
        record["detail"] = _sanitize(detail)

    log_path = load_config()["log_path"]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# --------------------------------------------------------------------------- #
# Project-name resolution – accept a name, None (current), or a live handle.
# --------------------------------------------------------------------------- #

def _project_name(project) -> str | None:
    """Best-effort project name from whatever was passed as `project`."""
    if project is None:
        # Resolve the current project name. Import locally so guard.py does
        # not force a live Resolve connection just to be imported.
        try:
            from . import connection
            return connection.get_project().GetName()
        except Exception:
            return None
    if isinstance(project, str):
        return project
    # A live project handle.
    getter = getattr(project, "GetName", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


# --------------------------------------------------------------------------- #
# The decorator. Wraps a function to enforce all three rules.
# --------------------------------------------------------------------------- #

def guarded(func):
    """
    Enforce dry_run default, allowlist, and logging on a mutating function.

    The wrapped function MUST accept `dry_run` (bool) and SHOULD accept
    `project` (name | None | handle). Contract:

      dry_run=True  (default) -> function runs; it must change nothing;
                                 logged as outcome "dry_run".
      dry_run=False           -> project must be on the allowlist, else
                                 GuardRefusal is raised and logged "refused".
                                 Otherwise runs; logged "executed" or "error".
    """
    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        params = bound.arguments

        dry_run = params.get("dry_run", True)
        project_name = _project_name(params.get("project"))

        # Arguments to log – drop live handles / self, keep the plain stuff.
        loggable = {
            k: v for k, v in params.items()
            if k not in ("self", "cls") and not hasattr(v, "GetName")
        }

        # Rule 2 + policy: refuse a real write barred by the allowlist or by
        # the method policy (read-only mode / method allow-deny / destructive).
        reason = refusal_reason(func.__name__, project_name, dry_run)
        if reason:
            log_event(func.__name__, project_name, loggable, "refused",
                      detail=reason)
            raise GuardRefusal(
                f"Refused: {reason}. Call with dry_run=True to preview, or "
                f"adjust forge_resolve.toml."
            )

        outcome = "dry_run" if dry_run else "executed"
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            log_event(func.__name__, project_name, loggable, "error",
                      detail=f"{type(exc).__name__}: {exc}")
            raise
        else:
            log_event(func.__name__, project_name, loggable, outcome)
            return result

    return wrapper
