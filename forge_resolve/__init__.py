"""
forge_resolve – a thin library for driving DaVinci Resolve by writing scripts.

Not an MCP server. Not a daemon. Import it, look a method up in the live
catalog, compose a loop, read the result. Only the result comes back.

    from forge_resolve import connection, guard, lib

    lib.find("relink")                       # discover methods, live
    scan = lib.scan_offline()                # read-only
    plan = lib.relink_by_root(OLD, NEW)      # dry_run by default
    done = lib.relink_by_root(OLD, NEW, dry_run=False)   # allowlist-gated

    # any other method, guarded:
    lib.call(project.GetMediaPool(), "RelinkClips", items, folder,
             dry_run=False, project="Scratch_Relink_Test")
"""

from . import connection, guard, lib

from .connection import (
    get_resolve, get_project, get_timeline, current,
    ResolveError, ResolveNotRunning, ProjectNotFound, TimelineNotFound,
)
from .guard import guarded, GuardRefusal, is_allowed, load_config
from .lib import (
    catalog, find, methods, describe, call,
    scan_offline, relink_by_root, relink_with_variations,
)

__all__ = [
    "connection", "guard", "lib",
    "get_resolve", "get_project", "get_timeline", "current",
    "ResolveError", "ResolveNotRunning", "ProjectNotFound", "TimelineNotFound",
    "guarded", "GuardRefusal", "is_allowed", "load_config",
    "catalog", "find", "methods", "describe", "call",
    "scan_offline", "relink_by_root", "relink_with_variations",
]
