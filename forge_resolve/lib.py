"""
lib.py – the library of methods for code mode.

Model: a live method catalog + a safe injector. The agent writes a script,
looks a method up here, calls it, Resolve does the job. Only the result comes
back.

Discovery is LIVE. The method list is not shipped or frozen – it is read at
runtime from Resolve's own README, which lives at
$RESOLVE_SCRIPT_API/README.txt and is overwritten by Resolve on every update.
Parse results are cached against that file's mtime/size, so a Resolve upgrade
auto-refreshes the catalog with zero maintenance.

  catalog()            -> {object: [ {name, signature, returns, doc}, ... ]}
  find("relink")       -> flat list of matching methods across all objects
  methods("MediaPool") -> that object's methods
  describe(live_obj)   -> live dir() vs catalog: spots new/removed methods

  call(obj, "Method", *a, dry_run=True, project=..., **kw)
      -> the guarded injector. Every mutating method reachable, safely:
         dry_run default, allowlist enforced, every attempt logged.

Reads are free: call the method straight on the handle (obj.GetName()).
Use call() for MUTATIONS – the things the guard must see.
"""

from __future__ import annotations

import os
import re

from . import connection, guard


# --------------------------------------------------------------------------- #
# Live catalog – parsed from Resolve's own README at runtime.
# --------------------------------------------------------------------------- #

# The API objects the README documents. A method line is only recorded while
# we are inside one of these column-0 sections; anything else resets scope,
# so prose sections never get mistaken for methods.
_KNOWN_OBJECTS = [
    "Resolve", "ProjectManager", "Project", "MediaStorage", "MediaPool",
    "Folder", "MediaPoolItem", "Timeline", "TimelineItem", "Gallery",
    "GalleryStillAlbum", "Graph", "ColorGroup",
]

_METHOD_RE = re.compile(r"^\s+([A-Za-z_]\w*)\((.*?)\)\s*-->\s*(.*)$")

_catalog_cache: dict = {}


def _readme_path() -> str:
    """The README that ships with – and is replaced by – this Resolve build."""
    api = os.environ.get("RESOLVE_SCRIPT_API", "")
    return os.path.join(api, "README.txt")


def _parse_readme(path: str) -> dict:
    result: dict[str, list] = {}
    current: str | None = None
    last: dict | None = None

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue

            # Column-0 line: either an object header, or a scope reset.
            if not line[0].isspace():
                token = line.strip()
                current = token if token in _KNOWN_OBJECTS else None
                last = None
                if current is not None:
                    result.setdefault(current, [])
                continue

            if current is None:
                continue

            m = _METHOD_RE.match(line)
            if m:
                name, args, tail = m.group(1), m.group(2), m.group(3)
                ret, _, doc = tail.partition("#")
                entry = {
                    "name": name,
                    "signature": f"{name}({args})",
                    "returns": ret.strip(),
                    "doc": doc.strip(),
                }
                result[current].append(entry)
                last = entry
            elif last is not None:
                # Continuation: an indented '# ...' line extends the last doc.
                cont = line.strip()
                if cont.startswith("#"):
                    extra = cont.lstrip("# ").rstrip()
                    if extra:
                        last["doc"] = (last["doc"] + " " + extra).strip()

    return result


def catalog() -> dict:
    """
    Return the live method catalog, keyed to the installed README.

    Cached against the README's mtime/size, so it re-parses automatically the
    first time you call it after a Resolve update. Needs no live Resolve
    connection – discovery works even with Resolve closed.
    """
    path = _readme_path()
    if not os.path.exists(path):
        raise connection.ResolveNotRunning(
            f"Scripting README not found at {path}. Cannot build the method "
            "catalog. Is Resolve Studio installed / RESOLVE_SCRIPT_API set?"
        )
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    cached = _catalog_cache.get(key)
    if cached is not None:
        return cached
    data = _parse_readme(path)
    _catalog_cache.clear()
    _catalog_cache[key] = data
    return data


def methods(object_name: str) -> list:
    """Methods for one API object (case-insensitive). [] if unknown."""
    cat = catalog()
    for name, ms in cat.items():
        if name.lower() == object_name.lower():
            return list(ms)
    return []


def find(query: str) -> list:
    """
    Search the catalog. Matches object name, method name, or doc text.
    Returns a flat list of {object, name, signature, returns, doc}.
    """
    q = query.lower()
    hits = []
    for obj, ms in catalog().items():
        for m in ms:
            hay = f"{obj} {m['name']} {m['doc']}".lower()
            if q in hay:
                hits.append({"object": obj, **m})
    return hits


def describe(obj) -> dict:
    """
    Live introspection of a Resolve handle, cross-checked against the catalog.

    Guesses which API object `obj` is (by method-name overlap) and reports:
      - live_methods:        what this build actually exposes
      - live_not_documented: present live but not in the README (new/undoc)
      - documented_not_live: in the README but missing here (deprecated/gone)

    The two diff lists are your early warning that a Resolve update shifted the
    API out from under the catalog.
    """
    live = sorted(
        n for n in dir(obj)
        if not n.startswith("_") and callable(getattr(obj, n, None))
    )
    cat = catalog()
    best, best_score = None, -1
    for oname, ms in cat.items():
        overlap = len({m["name"] for m in ms} & set(live))
        if overlap > best_score:
            best, best_score = oname, overlap
    documented = {m["name"] for m in cat.get(best, [])}
    return {
        "guessed_object": best,
        "live_methods": live,
        "documented": sorted(documented),
        "live_not_documented": sorted(set(live) - documented),
        "documented_not_live": sorted(documented - set(live)),
    }


# --------------------------------------------------------------------------- #
# call() – the guarded injector. Any mutating method, safely.
# --------------------------------------------------------------------------- #

def call(target, method_name: str, *args,
         dry_run: bool = True, project=None, **kwargs):
    """
    Invoke a mutating Resolve method under the guard.

    dry_run=True  (default): does NOT run the method. Logs the intent and
                             returns a plan dict describing the call.
    dry_run=False:           allowlist-checked, executed, logged. Returns the
                             method's real return value.

    Reads do not belong here – call them straight on the handle.
    """
    proj_name = guard._project_name(project)
    loggable = {
        "method": method_name,
        "args": [guard._sanitize(a) for a in args],
        "kwargs": {k: guard._sanitize(v) for k, v in kwargs.items()},
    }

    if dry_run:
        guard.log_event(f"call:{method_name}", proj_name, loggable, "dry_run")
        return {"would_call": method_name, "args": args, "kwargs": kwargs,
                "project": proj_name, "executed": False}

    reason = guard.refusal_reason(method_name, proj_name, dry_run)
    if reason:
        guard.log_event(f"call:{method_name}", proj_name, loggable, "refused",
                        detail=reason)
        raise guard.GuardRefusal(
            f"Refused: {reason}. Call with dry_run=True to preview, or adjust "
            f"forge_resolve.toml."
        )

    method = getattr(target, method_name, None)
    if not callable(method):
        guard.log_event(f"call:{method_name}", proj_name, loggable, "error",
                        detail="no such method on target")
        raise AttributeError(
            f"{method_name!r} is not a callable method on {target!r}"
        )

    try:
        result = method(*args, **kwargs)
    except Exception as exc:
        guard.log_event(f"call:{method_name}", proj_name, loggable, "error",
                        detail=f"{type(exc).__name__}: {exc}")
        raise
    guard.log_event(f"call:{method_name}", proj_name, loggable, "executed",
                    detail=guard._sanitize(result))
    return result


# --------------------------------------------------------------------------- #
# Pool primitives – the reusable reads any job composes against.
# --------------------------------------------------------------------------- #

def _walk_folder(folder, out: list) -> None:
    for clip in folder.GetClipList() or []:
        out.append(clip)
    for sub in folder.GetSubFolderList() or []:
        _walk_folder(sub, out)


def media_pool_items(project=None) -> list:
    """Every MediaPoolItem in the project's Media Pool."""
    if project is None:
        project = connection.get_project()
    items: list = []
    root = project.GetMediaPool().GetRootFolder()
    if root is not None:
        _walk_folder(root, items)
    return items


def timeline_items(timeline=None) -> list:
    """Unique MediaPoolItems backing a timeline's clips (video + audio)."""
    if timeline is None:
        timeline = connection.get_timeline()
    seen, items = set(), []
    for track_type in ("video", "audio"):
        for i in range(1, timeline.GetTrackCount(track_type) + 1):
            for ti in timeline.GetItemListInTrack(track_type, i) or []:
                mpi = ti.GetMediaPoolItem()
                if mpi is None:
                    continue
                mid = mpi.GetMediaId()
                if mid not in seen:
                    seen.add(mid)
                    items.append(mpi)
    return items


def clip_path(mpi) -> str:
    """The clip's current File Path property ('' if none)."""
    return mpi.GetClipProperty("File Path") or ""


def is_offline(path: str) -> bool:
    """Offline = no path recorded, or the recorded file is not on disk."""
    return (not path) or (not os.path.exists(path))


# --------------------------------------------------------------------------- #
# Composed helpers – the jobs you do often, built from the primitives above.
# All mutations route through the guard.
# --------------------------------------------------------------------------- #

def scan_offline(timeline=None, project=None) -> dict:
    """Read-only. Report offline clips in a timeline. Primitives only."""
    if project is None:
        project = connection.get_project()
    if timeline is None:
        timeline = connection.get_timeline(project=project)
    items = timeline_items(timeline)
    offline = [{"name": m.GetName(), "path": clip_path(m)}
               for m in items if is_offline(clip_path(m))]
    return {
        "timeline": timeline.GetName(),
        "total": len(items),
        "offline_count": len(offline),
        "online_count": len(items) - len(offline),
        "offline": offline,
    }


def _norm_root(root: str) -> str:
    return root.rstrip("/\\")


def _substitute_root(path: str, old_root: str, new_root: str):
    old = _norm_root(old_root)
    if path == old or path.startswith(old + "/") or path.startswith(old + "\\"):
        return _norm_root(new_root) + path[len(old):]
    return None


def _normkey(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _do_relink(project, by_folder: dict) -> tuple[int, list]:
    """RelinkClips each folder-group, then verify each clip individually."""
    mp = project.GetMediaPool()
    relinked, failed = 0, []
    for folder, items in by_folder.items():
        mp.RelinkClips(items, folder)
        for mpi in items:
            if not is_offline(clip_path(mpi)):
                relinked += 1
            else:
                failed.append({"name": mpi.GetName(), "to": folder,
                               "reason": "still offline after RelinkClips"})
    return relinked, failed


@guard.guarded
def relink_by_root(old_root: str, new_root: str, *,
                   project=None, dry_run: bool = True) -> dict:
    """Relink offline clips by exact path-root substitution. Guarded."""
    if project is None:
        project = connection.get_project()
    offline = [(m, clip_path(m)) for m in media_pool_items(project)
               if is_offline(clip_path(m))]
    planned, unresolved, by_folder = [], [], {}
    for mpi, old_path in offline:
        new_path = _substitute_root(old_path, old_root, new_root)
        if new_path is None:
            unresolved.append({"name": mpi.GetName(), "from": old_path,
                               "reason": "path does not start with old_root"})
        elif not os.path.exists(new_path):
            unresolved.append({"name": mpi.GetName(), "from": old_path,
                               "reason": f"target not on disk: {new_path}"})
        else:
            planned.append({"name": mpi.GetName(), "from": old_path,
                            "to": new_path})
            by_folder.setdefault(os.path.dirname(new_path), []).append(mpi)

    result = {"offline_found": len(offline), "planned": planned,
              "unresolved": unresolved, "relinked": 0, "failed": []}
    if dry_run:
        return result
    result["relinked"], result["failed"] = _do_relink(project, by_folder)
    return result


def _index_tree(root: str) -> dict:
    exact, loose = {}, {}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            exact.setdefault(fn, full)
            loose.setdefault(_normkey(fn), full)
    return {"exact": exact, "loose": loose}


def _find_target(basename: str, index: dict, aliases: dict):
    if basename in index["exact"]:
        return index["exact"][basename]
    key = _normkey(basename)
    if key in index["loose"]:
        return index["loose"][key]
    for was, now in (aliases or {}).items():
        alt = basename.replace(was, now)
        if alt != basename and _normkey(alt) in index["loose"]:
            return index["loose"][_normkey(alt)]
    return None


@guard.guarded
def relink_with_variations(old_root: str, new_root: str, *,
                           project=None, dry_run: bool = True,
                           device_aliases: dict | None = None) -> dict:
    """
    Relink by matching filename under new_root – tolerates device-folder and
    date-format differences in the tree. Guarded. Same return shape as
    relink_by_root.
    """
    if project is None:
        project = connection.get_project()
    if not os.path.isdir(new_root):
        return {"offline_found": 0, "planned": [],
                "unresolved": [{"name": "*", "from": new_root,
                                "reason": "new_root is not a directory"}],
                "relinked": 0, "failed": []}

    index = _index_tree(new_root)
    offline = [(m, clip_path(m)) for m in media_pool_items(project)
               if is_offline(clip_path(m))]
    planned, unresolved, by_folder = [], [], {}
    for mpi, old_path in offline:
        basename = os.path.basename(old_path) or mpi.GetName()
        target = _find_target(basename, index, device_aliases or {})
        if target is None:
            unresolved.append({"name": mpi.GetName(), "from": old_path,
                               "reason": "no matching filename under new_root"})
        else:
            planned.append({"name": mpi.GetName(), "from": old_path,
                            "to": target})
            by_folder.setdefault(os.path.dirname(target), []).append(mpi)

    result = {"offline_found": len(offline), "planned": planned,
              "unresolved": unresolved, "relinked": 0, "failed": []}
    if dry_run:
        return result
    result["relinked"], result["failed"] = _do_relink(project, by_folder)
    return result
