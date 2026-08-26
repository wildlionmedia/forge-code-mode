"""
connection.py — get to DaVinci Resolve and stay there.

This is the boring layer. No cleverness. Its only job is:
  - bootstrap the scripting environment (env vars),
  - import the Blackmagic scripting module and get a handle,
  - hand back projects and timelines by name (or the current one),
  - reconnect a dead handle instead of dying,
  - raise a typed, explanatory exception when it cannot.

Requires DaVinci Resolve *Studio* with external scripting set to Local
(Preferences > System > General > External scripting using = Local).
The free edition does not expose external scripting at all.
"""

from __future__ import annotations

import os
import sys
import platform


# --------------------------------------------------------------------------- #
# Typed exceptions — this module never returns None to signal failure.
# --------------------------------------------------------------------------- #

class ResolveError(Exception):
    """Base class for every failure in this package's connection layer."""


class ResolveNotRunning(ResolveError):
    """Resolve is not reachable: not launched, free edition, or scripting off."""


class ProjectNotFound(ResolveError):
    """A project by that name is not open / not in the current database."""


class TimelineNotFound(ResolveError):
    """A timeline by that name does not exist in the current project."""


# --------------------------------------------------------------------------- #
# Platform defaults for the scripting environment.
# Blackmagic's own README ships these exact paths. Env vars override.
# --------------------------------------------------------------------------- #

def _platform_defaults() -> tuple[str, str]:
    """Return (RESOLVE_SCRIPT_API, RESOLVE_SCRIPT_LIB) for this OS."""
    system = platform.system()
    if system == "Darwin":
        api = ("/Library/Application Support/Blackmagic Design/"
               "DaVinci Resolve/Developer/Scripting")
        lib = ("/Applications/DaVinci Resolve/DaVinci Resolve.app/"
               "Contents/Libraries/Fusion/fusionscript.so")
    elif system == "Windows":
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        api = (program_data + r"\Blackmagic Design\DaVinci Resolve"
                              r"\Support\Developer\Scripting")
        lib = (r"C:\Program Files\Blackmagic Design"
               r"\DaVinci Resolve\fusionscript.dll")
    else:  # Linux and anything else
        api = "/opt/resolve/Developer/Scripting"
        lib = "/opt/resolve/libs/Fusion/fusionscript.so"
    return api, lib


def _bootstrap_env() -> None:
    """
    Populate RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB / PYTHONPATH.

    An existing env var always wins — this only fills the gaps with the
    platform default. Also makes the API's Modules/ folder importable so
    `import DaVinciResolveScript` works.
    """
    api_default, lib_default = _platform_defaults()

    api = os.environ.get("RESOLVE_SCRIPT_API") or api_default
    lib = os.environ.get("RESOLVE_SCRIPT_LIB") or lib_default

    os.environ["RESOLVE_SCRIPT_API"] = api
    os.environ["RESOLVE_SCRIPT_LIB"] = lib

    modules = os.path.join(api, "Modules")
    if modules not in sys.path:
        sys.path.append(modules)

    # Keep PYTHONPATH consistent for any child processes too.
    existing = os.environ.get("PYTHONPATH", "")
    if modules not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            modules + os.pathsep + existing if existing else modules
        )


# --------------------------------------------------------------------------- #
# Import-time check: fail loudly if Studio's scripting library is absent.
# We do NOT require Resolve to be *running* at import — that is what the
# lazy handle + reconnect below is for. We only prove the library exists,
# i.e. that Resolve Studio is installed and the paths are right.
# --------------------------------------------------------------------------- #

def _verify_environment() -> None:
    _bootstrap_env()
    lib = os.environ["RESOLVE_SCRIPT_LIB"]
    if not os.path.exists(lib):
        raise ResolveNotRunning(
            "DaVinci Resolve scripting library not found at:\n"
            f"    {lib}\n"
            "This package requires DaVinci Resolve *Studio* installed, with\n"
            "External scripting set to Local (Preferences > System > General).\n"
            "If Resolve lives elsewhere, set RESOLVE_SCRIPT_API and "
            "RESOLVE_SCRIPT_LIB in your environment and re-import."
        )


_verify_environment()


# --------------------------------------------------------------------------- #
# The single module-level handle. Lazy, reused, self-healing.
# --------------------------------------------------------------------------- #

_resolve = None  # the cached Resolve handle


def _new_handle():
    """Import the scripting module and ask it for a fresh Resolve handle."""
    try:
        import DaVinciResolveScript as dvr  # noqa: N813  (BMD's own name)
    except ImportError as exc:
        raise ResolveNotRunning(
            "Could not import DaVinciResolveScript. The scripting Modules "
            "path is wrong or Resolve Studio is not installed.\n"
            f"    RESOLVE_SCRIPT_API = {os.environ.get('RESOLVE_SCRIPT_API')}"
        ) from exc

    handle = dvr.scriptapp("Resolve")
    if handle is None:
        raise ResolveNotRunning(
            "Got a null Resolve handle. Resolve is not running, or External "
            "scripting is not set to Local "
            "(Preferences > System > General > External scripting using)."
        )
    return handle


def _is_alive(handle) -> bool:
    """A cheap round-trip that fails if the handle is stale."""
    if handle is None:
        return False
    try:
        # GetProductName() is a trivial call that touches the live app.
        return handle.GetProductName() is not None
    except Exception:
        return False


def get_resolve():
    """
    Return the shared Resolve handle, creating or reconnecting as needed.

    Never returns None. Raises ResolveNotRunning if it cannot connect.
    """
    global _resolve
    if _is_alive(_resolve):
        return _resolve
    _resolve = _new_handle()
    return _resolve


# --------------------------------------------------------------------------- #
# Projects and timelines: by name, or the current one. Never None.
# --------------------------------------------------------------------------- #

def get_project(name: str | None = None):
    """
    Resolve a project.

    name=None -> the currently open project.
    name=str  -> that project must already be open in the Project Manager's
                 current database; we do not create or search folders.
    """
    resolve = get_resolve()
    pm = resolve.GetProjectManager()

    if name is None:
        project = pm.GetCurrentProject()
        if project is None:
            raise ProjectNotFound("No project is currently open in Resolve.")
        return project

    # Try to load it by name from the current folder.
    project = pm.LoadProject(name)
    if project is None:
        # Maybe it is already the open one.
        current = pm.GetCurrentProject()
        if current is not None and current.GetName() == name:
            return current
        raise ProjectNotFound(
            f"Project {name!r} could not be loaded. It must exist in the "
            "current database/folder in the Project Manager."
        )
    return project


def get_timeline(name: str | None = None, project=None):
    """
    Resolve a timeline within a project.

    name=None -> the current timeline of the project.
    name=str  -> matched by name against the project's timelines.
    project=None -> uses the current project.
    """
    if project is None:
        project = get_project()

    if name is None:
        timeline = project.GetCurrentTimeline()
        if timeline is None:
            raise TimelineNotFound(
                f"Project {project.GetName()!r} has no current timeline."
            )
        return timeline

    count = project.GetTimelineCount()
    for i in range(1, count + 1):  # Resolve indexes timelines from 1.
        tl = project.GetTimelineByIndex(i)
        if tl is not None and tl.GetName() == name:
            return tl

    raise TimelineNotFound(
        f"Timeline {name!r} not found in project {project.GetName()!r} "
        f"({count} timelines present)."
    )


def current():
    """Convenience: return (project, timeline) for whatever is open now."""
    project = get_project()
    timeline = get_timeline(project=project)
    return project, timeline
