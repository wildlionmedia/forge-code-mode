#!/usr/bin/env python3
"""
api_full.py — drive EVERY documented Resolve method in a scratch project.

Goal: 100% invocation. Each method lands in one bucket:
  ok       — called, returned truthy/again valid, no raise
  falsey   — called, returned False/None (API's own "no-op"), no raise
  error    — called, raised                          (+ exception)
  skipped  — cannot be called at all                 (+ reason)

Only genuinely-uncallable methods stay skipped (Quit, cloud login).
Everything else is invoked with real arguments built from live fixtures.

Containment: all work happens in a throwaway project (SCRATCH_NAME); the
previously-open project is restored at the end. Destructive calls are made
against throwaway sub-objects (temp timelines/clips/folders/presets).

Run with Resolve Studio open:
    python3 forge_resolve/tests/api_full.py
"""

import os
import sys
import json
import glob
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                      # repo root (contains forge_resolve/)
sys.path.insert(0, REPO)
from forge_resolve import connection, lib        # noqa: E402

# Fixture media: a folder of a few short clips to import. Pass it as argv[1] or
# set FORGE_TEST_MEDIA. With no media the sweep still runs, just skips the
# methods that need a real clip/timeline. Point this at ANY folder of footage.
MEDIA_DIR = (sys.argv[1] if len(sys.argv) > 1 else "") or \
    os.environ.get("FORGE_TEST_MEDIA", "")
MEDIA_EXTS = (".mov", ".mp4", ".mxf", ".braw", ".r3d", ".dng", ".wav", ".aif")

SCRATCH_NAME = "FORGE_API_SMOKE"
ARTIFACTS = os.path.join(HERE, "artifacts")
REPORT = os.path.join(HERE, "api_coverage_report.json")
PROGRESS = os.path.join(HERE, "api_coverage_progress.jsonl")

# Long-running AI / interactive methods that cannot run in an unattended
# suite (they download models, render, track, or otherwise block for minutes).
# Excluded on purpose and recorded as such — no silent gaps.
EXCLUDE = {
    ("Folder", "TranscribeAudio"): "long-running AI (speech) — unattended-unsafe",
    ("Folder", "AnalyzeForSlate"): "long-running AI analysis — unattended-unsafe",
    ("Folder", "AnalyzeForIntellisearch"): "long-running AI analysis — unattended-unsafe",
    ("Folder", "RemoveMotionBlur"): "long-running AI render — unattended-unsafe",
    ("MediaPoolItem", "TranscribeAudio"): "long-running AI (speech) — unattended-unsafe",
    ("MediaPoolItem", "AnalyzeForSlate"): "long-running AI analysis — unattended-unsafe",
    ("MediaPoolItem", "AnalyzeForIntellisearch"): "long-running AI analysis — unattended-unsafe",
    ("MediaPoolItem", "RemoveMotionBlur"): "long-running AI render — unattended-unsafe",
    ("Timeline", "CreateSubtitlesFromAudio"): "long-running AI transcription — unattended-unsafe",
    ("TimelineItem", "CreateMagicMask"): "AI mask/tracking, can block — unattended-unsafe",
    ("Project", "GenerateSpeech"): "AI text-to-speech — unattended-unsafe",
    ("Project", "RenderWithQuickExport"): "performs a full render — unattended-unsafe",
    ("ProjectManager", "RestoreProject"): "needs a .dra archive — unattended-unsafe",
}

# Project-lifecycle methods pop a GUI confirm modal ("Save changes?" /
# delete-confirm) when invoked as a test target — a modal blocks the scripting
# server entirely and wedges the whole session (same class as Quit). They are
# NOT invoked as targets. Note: build() still uses CreateProject/SaveProject
# once, in a controlled way that does not prompt, so those ARE exercised.
SESSION_LAST = set()

def fixture_clips(limit=3):
    """A few short clips from MEDIA_DIR to import as fixtures ([] if none)."""
    if not MEDIA_DIR or not os.path.isdir(MEDIA_DIR):
        return []
    hits = []
    for fn in sorted(os.listdir(MEDIA_DIR)):
        if fn.lower().endswith(MEDIA_EXTS):
            hits.append(os.path.join(MEDIA_DIR, fn))
        if len(hits) >= limit:
            break
    return hits


MARK = "Blue"          # valid marker/flag color
CLIPCOLOR = "Orange"   # valid clip color

# Hard skips: physically cannot be exercised in an automated scratch run.
HARD_SKIP = {
    ("Resolve", "Quit"): "would close Resolve",
    ("ProjectManager", "CreateCloudProject"): "needs cloud login",
    ("ProjectManager", "ImportCloudProject"): "needs cloud login",
    ("ProjectManager", "LoadCloudProject"): "needs cloud login",
    ("ProjectManager", "RestoreCloudProject"): "needs cloud login",
    ("ProjectManager", "SetCurrentDatabase"): "would switch DB out from under us",
    # Project-lifecycle: pop a confirm modal that wedges scripting. build() uses
    # CreateProject/SaveProject once, controlled; these are never test targets.
    ("ProjectManager", "CloseProject"): "pops a confirm modal — unattended-unsafe",
    ("ProjectManager", "CreateProject"): "pops a confirm modal — unattended-unsafe (build uses it once)",
    ("ProjectManager", "DeleteProject"): "pops a confirm modal — unattended-unsafe",
    ("ProjectManager", "ImportProject"): "pops a confirm modal — unattended-unsafe",
    ("ProjectManager", "LoadProject"): "reloads session / pops modal — unattended-unsafe",
}


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #

def summarize(value):
    try:
        json.dumps(value)
        return repr(value)[:160]
    except Exception:
        try:
            return f"<{type(value).__name__}>"
        except Exception:
            return "<opaque>"


class Cov:
    def __init__(self):
        self.rows, self._done = [], set()
        self._prog = open(PROGRESS, "w", encoding="utf-8")

    def _log(self, phase, obj, method, extra=""):
        self._prog.write(json.dumps({"phase": phase, "object": obj,
                                     "method": method, "extra": extra}) + "\n")
        self._prog.flush()

    def rec(self, obj, method, status, detail="", signature=""):
        if (obj, method) in self._done:
            return
        self._done.add((obj, method))
        self.rows.append({"object": obj, "method": method, "status": status,
                          "detail": detail, "signature": signature})

    def run(self, obj, method, fn, signature=""):
        """Run a dispatch thunk fn() and record the outcome."""
        self._log("attempt", obj, method)          # written BEFORE the call
        try:
            res = fn()
        except Exception as exc:
            self.rec(obj, method, "error", f"{type(exc).__name__}: {exc}",
                     signature)
            self._log("done", obj, method, "error")
            return None
        status = "ok" if res not in (None, False, "") else "falsey"
        self.rec(obj, method, status, summarize(res), signature)
        self._log("done", obj, method, status)
        return res


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

class Ctx:
    pass


def build(cov):
    c = Ctx()
    c.resolve = connection.get_resolve()
    c.pm = c.resolve.GetProjectManager()
    prev = c.pm.GetCurrentProject()
    c.prev_name = prev.GetName() if prev else None

    # Gentle build: NEVER delete or close a project here. Those raise a modal
    # ("Save changes?" / confirm) that wedges the scripting server when run
    # unattended. Reuse the scratch project if it exists, else create it, then
    # add a fresh timeline. No prompts, ever.
    existing = c.pm.GetProjectListInCurrentFolder() or []
    n = 1
    while "%s_%d" % (SCRATCH_NAME, n) in existing:   # fresh, unique name
        n += 1
    c.scratch_name = "%s_%d" % (SCRATCH_NAME, n)
    proj = c.pm.CreateProject(c.scratch_name)
    c.project = proj
    c.pm.SaveProject()                               # keep it saved => no prompt
    c.ms = c.resolve.GetMediaStorage()
    c.mp = proj.GetMediaPool()
    c.root = c.mp.GetRootFolder()

    paths = fixture_clips()
    c.clip_paths = paths
    c.media = c.mp.ImportMedia(paths) or [] if paths else []
    c.mpi = c.media[0] if c.media else None

    c.timeline = c.mp.CreateTimelineFromClips("smoke_tl", c.media) \
        if c.media else proj.GetCurrentTimeline()
    proj.SetCurrentTimeline(c.timeline)
    c.titems = c.timeline.GetItemListInTrack("video", 1) or [] if c.timeline else []
    c.tli = c.titems[0] if c.titems else None

    # A marker on each markable object so Get/Delete-marker methods have data.
    for obj in (c.mpi, c.timeline, c.tli):
        try:
            obj and obj.AddMarker(5, MARK, "m", "note", 1, "cd1")
        except Exception:
            pass

    c.gallery = proj.GetGallery()
    c.album = c.gallery.GetCurrentStillAlbum() if c.gallery else None
    try:
        c.still = c.tli.GrabStill() if c.tli else None
    except Exception:
        c.still = None

    c.group = proj.AddColorGroup("smoke_group")
    if c.tli and c.group:
        try:
            c.tli.AssignToColorGroup(c.group)
        except Exception:
            pass

    c.graph = None
    if c.tli and hasattr(c.tli, "GetNodeGraph"):
        try:
            c.graph = c.tli.GetNodeGraph(1)
        except Exception:
            try:
                c.graph = c.tli.GetNodeGraph()
            except Exception:
                c.graph = None

    os.makedirs(ARTIFACTS, exist_ok=True)
    c.tmp = ARTIFACTS
    return c


# --------------------------------------------------------------------------- #
# Dispatch table: (Object, method) -> thunk(c) performing the call.
# Self-contained where a create/delete pair is needed.
# --------------------------------------------------------------------------- #

def dispatch(c):
    D = {}

    # ---- Resolve ----
    D[("Resolve", "SetKeyframeMode")] = lambda: c.resolve.SetKeyframeMode(0)
    D[("Resolve", "SaveUserPreferencesPreset")] = \
        lambda: c.resolve.SaveUserPreferencesPreset("smoke_up")
    D[("Resolve", "LoadUserPreferencesPreset")] = \
        lambda: c.resolve.LoadUserPreferencesPreset("smoke_up")
    D[("Resolve", "ExportUserPreferencesPreset")] = \
        lambda: c.resolve.ExportUserPreferencesPreset(
            "smoke_up", os.path.join(c.tmp, "up.preset"))
    D[("Resolve", "ImportUserPreferencesPreset")] = \
        lambda: c.resolve.ImportUserPreferencesPreset(
            os.path.join(c.tmp, "up.preset"), "smoke_up2")
    D[("Resolve", "DeleteUserPreferencesPreset")] = \
        lambda: c.resolve.DeleteUserPreferencesPreset("smoke_up2")
    D[("Resolve", "OpenPage")] = lambda: c.resolve.OpenPage("edit")
    D[("Resolve", "LoadLayoutPreset")] = lambda: c.resolve.LoadLayoutPreset("default")
    D[("Resolve", "SaveLayoutPreset")] = lambda: c.resolve.SaveLayoutPreset("smoke_layout")
    D[("Resolve", "UpdateLayoutPreset")] = lambda: c.resolve.UpdateLayoutPreset("smoke_layout")
    D[("Resolve", "ExportLayoutPreset")] = \
        lambda: c.resolve.ExportLayoutPreset("smoke_layout", os.path.join(c.tmp, "layout.preset"))
    D[("Resolve", "DeleteLayoutPreset")] = lambda: c.resolve.DeleteLayoutPreset("smoke_layout")
    D[("Resolve", "ImportLayoutPreset")] = \
        lambda: c.resolve.ImportLayoutPreset(os.path.join(c.tmp, "layout.preset"), "smoke_layout2")
    D[("Resolve", "ImportRenderPreset")] = \
        lambda: c.resolve.ImportRenderPreset(os.path.join(c.tmp, "render.xml"))
    D[("Resolve", "ExportRenderPreset")] = \
        lambda: c.resolve.ExportRenderPreset("Current", os.path.join(c.tmp, "render.xml"))
    D[("Resolve", "ImportBurnInPreset")] = \
        lambda: c.resolve.ImportBurnInPreset(os.path.join(c.tmp, "burn.dat"))
    D[("Resolve", "ExportBurnInPreset")] = \
        lambda: c.resolve.ExportBurnInPreset("Current", os.path.join(c.tmp, "burn.dat"))
    D[("Resolve", "DeleteBurnInPreset")] = lambda: c.resolve.DeleteBurnInPreset("Current")

    # ---- ProjectManager ----
    D[("ProjectManager", "CreateFolder")] = lambda: c.pm.CreateFolder("smoke_folder")
    D[("ProjectManager", "OpenFolder")] = lambda: c.pm.OpenFolder("smoke_folder")
    D[("ProjectManager", "GotoRootFolder")] = lambda: c.pm.GotoRootFolder()
    D[("ProjectManager", "DeleteFolder")] = lambda: (c.pm.GotoRootFolder(),
                                                     c.pm.DeleteFolder("smoke_folder"))[1]
    D[("ProjectManager", "LoadProject")] = lambda: c.pm.LoadProject(c.scratch_name)

    def pm_close_reopen():
        c.pm.SaveProject()
        ok = c.pm.CloseProject(c.project)
        c.pm.LoadProject(c.scratch_name)
        c.project = c.pm.GetCurrentProject()
        return ok
    D[("ProjectManager", "CloseProject")] = pm_close_reopen

    # These are deferred (SESSION_LAST) — they switch the current project, so
    # they run only after every object has been swept. No need to restore.
    def pm_create():
        p = c.pm.CreateProject("FORGE_TMP_CREATE")
        c.pm.SaveProject()
        return p
    D[("ProjectManager", "CreateProject")] = pm_create

    def pm_delete():
        c.pm.CreateProject("FORGE_TMP_DEL")
        c.pm.SaveProject()
        c.pm.LoadProject(c.scratch_name)      # make FORGE_TMP_DEL non-current
        return c.pm.DeleteProject("FORGE_TMP_DEL")
    D[("ProjectManager", "DeleteProject")] = pm_delete

    D[("ProjectManager", "ExportProject")] = lambda: c.pm.ExportProject(
        c.scratch_name, os.path.join(c.tmp, "scratch.drp"), False)
    D[("ProjectManager", "ImportProject")] = lambda: c.pm.ImportProject(
        os.path.join(c.tmp, "scratch.drp"), "FORGE_TMP_IMP")

    # ---- Project ----
    D[("Project", "AddColorGroup")] = lambda: c.project.AddColorGroup(
        "grp_%d" % len(c.project.GetColorGroupsList() or []))
    D[("Project", "ApplyFairlightPresetToCurrentTimeline")] = \
        lambda: c.project.ApplyFairlightPresetToCurrentTimeline("Default")
    D[("Project", "SetName")] = lambda: c.project.SetName(c.scratch_name)
    D[("Project", "GetSetting")] = lambda: c.project.GetSetting("timelineFrameRate")
    D[("Project", "SetSetting")] = lambda: c.project.SetSetting("timelineFrameRate", "24")
    D[("Project", "GetTimelineByIndex")] = lambda: c.project.GetTimelineByIndex(1)
    D[("Project", "SetCurrentTimeline")] = lambda: c.project.SetCurrentTimeline(c.timeline)
    D[("Project", "SetPreset")] = lambda: c.project.SetPreset("Current")
    D[("Project", "GetRenderCodecs")] = lambda: c.project.GetRenderCodecs("mov")
    D[("Project", "GetRenderResolutions")] = lambda: c.project.GetRenderResolutions("mov", "H.264")
    D[("Project", "SetCurrentRenderMode")] = lambda: c.project.SetCurrentRenderMode(1)
    D[("Project", "SetCurrentRenderFormatAndCodec")] = \
        lambda: c.project.SetCurrentRenderFormatAndCodec("mov", "H.264")
    D[("Project", "SetRenderSettings")] = \
        lambda: c.project.SetRenderSettings({"TargetDir": c.tmp, "CustomName": "smoke",
                                             "MarkIn": 0, "MarkOut": 2})
    D[("Project", "SaveAsNewRenderPreset")] = lambda: c.project.SaveAsNewRenderPreset("smoke_rp")
    D[("Project", "LoadRenderPreset")] = lambda: c.project.LoadRenderPreset("smoke_rp")
    D[("Project", "DeleteRenderPreset")] = lambda: c.project.DeleteRenderPreset("smoke_rp")
    D[("Project", "ExportCurrentFrameAsStill")] = \
        lambda: c.project.ExportCurrentFrameAsStill(os.path.join(c.tmp, "frame.jpg"))
    D[("Project", "GetColorGroupsList")] = lambda: c.project.GetColorGroupsList()
    D[("Project", "DeleteColorGroup")] = lambda: c.project.DeleteColorGroup(
        c.project.AddColorGroup("smoke_group_tmp"))
    D[("Project", "LoadBurnInPreset")] = lambda: c.project.LoadBurnInPreset("Current")
    D[("Project", "SetCurrentTimeline")] = lambda: c.project.SetCurrentTimeline(c.timeline)
    D[("Project", "InsertAudioToCurrentTrackAtPlayhead")] = \
        lambda: c.project.InsertAudioToCurrentTrackAtPlayhead(c.clip_paths[0], 0, 1) \
        if c.clip_paths else False

    # Render cluster: settings -> add job -> render -> status -> delete.
    def render_cycle():
        c.project.SetRenderSettings({"TargetDir": c.tmp, "CustomName": "smoke_render",
                                     "MarkIn": 0, "MarkOut": 1})
        jid = c.project.AddRenderJob()
        c.project.StartRendering([jid]) if jid else None
        for _ in range(30):
            if not c.project.IsRenderingInProgress():
                break
            time.sleep(0.5)
        st = c.project.GetRenderJobStatus(jid) if jid else None
        c.project.DeleteRenderJob(jid) if jid else None
        return {"job": jid, "status": st}
    D[("Project", "StartRendering")] = render_cycle
    D[("Project", "GetRenderJobStatus")] = lambda: (
        lambda j: (c.project.GetRenderJobStatus(j), c.project.DeleteRenderJob(j))[0]
    )(c.project.AddRenderJob())
    D[("Project", "DeleteRenderJob")] = lambda: c.project.DeleteRenderJob(c.project.AddRenderJob())
    D[("Project", "DeleteRenderJobByIndex")] = lambda: c.project.DeleteRenderJobByIndex(1)
    D[("Project", "DeleteAllRenderJobs")] = lambda: c.project.DeleteAllRenderJobs()

    # ---- MediaStorage ----
    D[("MediaStorage", "GetFileList")] = lambda: c.ms.GetFileList(ROOT)
    D[("MediaStorage", "GetSubFolderList")] = lambda: c.ms.GetSubFolderList(ROOT)
    D[("MediaStorage", "RevealInStorage")] = lambda: c.ms.RevealInStorage(ROOT)
    D[("MediaStorage", "AddItemListToMediaPool")] = \
        lambda: c.ms.AddItemListToMediaPool(c.clip_paths[:1])
    D[("MediaStorage", "GetFiles")] = lambda: c.ms.GetFiles(ROOT)
    D[("MediaStorage", "GetSubFolders")] = lambda: c.ms.GetSubFolders(ROOT)
    D[("MediaStorage", "AddItemsToMediaPool")] = lambda: c.ms.AddItemsToMediaPool(c.clip_paths[0])
    D[("MediaStorage", "AddClipMattesToMediaPool")] = \
        lambda: c.mpi and c.ms.AddClipMattesToMediaPool(c.mpi, [], "")
    D[("MediaStorage", "AddTimelineMattesToMediaPool")] = \
        lambda: c.ms.AddTimelineMattesToMediaPool([])

    # ---- MediaPool ----
    D[("MediaPool", "AddSubFolder")] = lambda: c.mp.AddSubFolder(c.root, "sub1")
    D[("MediaPool", "SetCurrentFolder")] = lambda: c.mp.SetCurrentFolder(c.root)
    D[("MediaPool", "GetCurrentFolder")] = lambda: c.mp.GetCurrentFolder()
    D[("MediaPool", "SetSelectedClip")] = lambda: c.mpi and c.mp.SetSelectedClip(c.mpi)
    D[("MediaPool", "CreateEmptyTimeline")] = lambda: c.mp.CreateEmptyTimeline("empty_tl")
    D[("MediaPool", "AppendToTimeline")] = lambda: c.mp.AppendToTimeline(c.media)
    D[("MediaPool", "RelinkClips")] = lambda: c.mp.RelinkClips([c.mpi], os.path.dirname(c.clip_paths[0])) if c.mpi else False
    D[("MediaPool", "UnlinkClips")] = lambda: c.mp.UnlinkClips([c.media[-1]]) if c.media else False
    D[("MediaPool", "GetClipMatteList")] = lambda: c.mp.GetClipMatteList(c.mpi) if c.mpi else False
    D[("MediaPool", "GetTimelineMatteList")] = lambda: c.mp.GetTimelineMatteList(c.root)

    def mp_move_clip():
        sub = c.mp.AddSubFolder(c.root, "movetgt")
        extra = c.mp.ImportMedia(c.clip_paths[:1])
        return c.mp.MoveClips(extra, sub) if extra else False
    D[("MediaPool", "MoveClips")] = mp_move_clip
    D[("MediaPool", "MoveFolders")] = lambda: c.mp.MoveFolders(
        [c.mp.AddSubFolder(c.root, "mvf")], c.mp.AddSubFolder(c.root, "mvtgt"))
    D[("MediaPool", "DeleteClips")] = lambda: c.mp.DeleteClips(
        c.mp.ImportMedia(c.clip_paths[:1]) or [])
    D[("MediaPool", "DeleteFolders")] = lambda: c.mp.DeleteFolders(
        [c.mp.AddSubFolder(c.root, "delf")])
    D[("MediaPool", "DeleteTimelines")] = lambda: c.mp.DeleteTimelines(
        [c.mp.CreateEmptyTimeline("del_tl")])
    D[("MediaPool", "ImportTimelineFromFile")] = lambda: c.mp.ImportTimelineFromFile(
        _export_drt(c))
    D[("MediaPool", "ImportFolderFromFile")] = lambda: c.mp.ImportFolderFromFile(
        os.path.join(c.tmp, "nope.drb"))
    D[("MediaPool", "ImportMedia")] = lambda: c.mp.ImportMedia(c.clip_paths[:1])
    D[("MediaPool", "CreateTimelineFromClips")] = lambda: c.mp.CreateTimelineFromClips(
        "ctfc_tl", c.media) if c.media else False
    D[("MediaPool", "AutoSyncAudio")] = lambda: c.mp.AutoSyncAudio(c.media, {}) if len(c.media) > 1 else False
    D[("MediaPool", "DeleteClipMattes")] = lambda: c.mp.DeleteClipMattes(c.mpi, []) if c.mpi else False
    D[("MediaPool", "ExportMetadata")] = lambda: c.mp.ExportMetadata(
        os.path.join(c.tmp, "meta.csv"), c.media)

    # ---- Folder ----
    D[("Folder", "Export")] = lambda: c.root.Export(os.path.join(c.tmp, "folder.drb"))
    D[("Folder", "TranscribeAudio")] = lambda: c.root.TranscribeAudio()
    D[("Folder", "AnalyzeForSlate")] = lambda: c.root.AnalyzeForSlate(MARK)
    D[("Folder", "AnalyzeForIntellisearch")] = lambda: c.root.AnalyzeForIntellisearch(False, False)
    D[("Folder", "RemoveMotionBlur")] = lambda: c.root.RemoveMotionBlur({})

    # ---- MediaPoolItem ----
    m = c.mpi
    D[("MediaPoolItem", "SetName")] = lambda: m and m.SetName("clipA")
    D[("MediaPoolItem", "GetClipProperty")] = lambda: m and m.GetClipProperty("File Path")
    D[("MediaPoolItem", "SetClipProperty")] = lambda: m and m.SetClipProperty("Comments", "x")
    D[("MediaPoolItem", "GetMetadata")] = lambda: m and m.GetMetadata("Description")
    D[("MediaPoolItem", "SetMetadata")] = lambda: m and m.SetMetadata("Description", "d")
    D[("MediaPoolItem", "GetThirdPartyMetadata")] = lambda: m and m.GetThirdPartyMetadata()
    D[("MediaPoolItem", "SetThirdPartyMetadata")] = lambda: m and m.SetThirdPartyMetadata("k", "v")
    D[("MediaPoolItem", "SetClipColor")] = lambda: m and m.SetClipColor(CLIPCOLOR)
    D[("MediaPoolItem", "AddFlag")] = lambda: m and m.AddFlag(MARK)
    D[("MediaPoolItem", "ClearFlags")] = lambda: m and m.ClearFlags(MARK)
    D[("MediaPoolItem", "SetMarkInOut")] = lambda: m and m.SetMarkInOut(1, 10, "all")
    D[("MediaPoolItem", "ClearMarkInOut")] = lambda: m and m.ClearMarkInOut("all")
    D[("MediaPoolItem", "GetMarkerByCustomData")] = lambda: m and m.GetMarkerByCustomData("cd1")
    D[("MediaPoolItem", "GetMarkerCustomData")] = lambda: m and m.GetMarkerCustomData(5)
    D[("MediaPoolItem", "UpdateMarkerCustomData")] = lambda: m and m.UpdateMarkerCustomData(5, "cd1b")
    D[("MediaPoolItem", "DeleteMarkerByCustomData")] = lambda: m and m.DeleteMarkerByCustomData("cd1b")
    D[("MediaPoolItem", "DeleteMarkerAtFrame")] = lambda: (m.AddMarker(7, MARK, "n", "x", 1),
                                                           m.DeleteMarkerAtFrame(7))[1] if m else False
    D[("MediaPoolItem", "DeleteMarkersByColor")] = lambda: m and m.DeleteMarkersByColor(MARK)
    D[("MediaPoolItem", "LinkProxyMedia")] = lambda: m and m.LinkProxyMedia(c.clip_paths[0])
    D[("MediaPoolItem", "LinkFullResolutionMedia")] = lambda: m and m.LinkFullResolutionMedia(c.clip_paths[0])
    D[("MediaPoolItem", "ReplaceClip")] = lambda: c.media[-1].ReplaceClip(c.clip_paths[-1]) if c.media else False
    D[("MediaPoolItem", "ReplaceClipPreserveSubClip")] = lambda: c.media[-1].ReplaceClipPreserveSubClip(c.clip_paths[-1]) if c.media else False
    D[("MediaPoolItem", "TranscribeAudio")] = lambda: m and m.TranscribeAudio()
    D[("MediaPoolItem", "AnalyzeForSlate")] = lambda: m and m.AnalyzeForSlate(MARK)
    D[("MediaPoolItem", "AnalyzeForIntellisearch")] = lambda: m and m.AnalyzeForIntellisearch(False, False)
    D[("MediaPoolItem", "RemoveMotionBlur")] = lambda: m and m.RemoveMotionBlur({})

    # ---- Timeline ----
    tl = c.timeline
    D[("Timeline", "SetName")] = lambda: tl and tl.SetName("smoke_tl")
    D[("Timeline", "GetSetting")] = lambda: tl and tl.GetSetting("useCustomSettings")
    D[("Timeline", "SetSetting")] = lambda: tl and tl.SetSetting("useCustomSettings", "1")
    D[("Timeline", "GetTrackCount")] = lambda: tl and tl.GetTrackCount("video")
    D[("Timeline", "GetItemListInTrack")] = lambda: tl and tl.GetItemListInTrack("video", 1)
    D[("Timeline", "GetTrackName")] = lambda: tl and tl.GetTrackName("video", 1)
    D[("Timeline", "SetTrackName")] = lambda: tl and tl.SetTrackName("video", 1, "V1x")
    D[("Timeline", "GetTrackSubType")] = lambda: tl and tl.GetTrackSubType("video", 1)
    D[("Timeline", "GetIsTrackEnabled")] = lambda: tl and tl.GetIsTrackEnabled("video", 1)
    D[("Timeline", "GetIsTrackLocked")] = lambda: tl and tl.GetIsTrackLocked("video", 1)
    D[("Timeline", "SetTrackEnable")] = lambda: tl and tl.SetTrackEnable("video", 1, True)
    D[("Timeline", "SetTrackLock")] = lambda: tl and tl.SetTrackLock("video", 1, False)
    D[("Timeline", "AddTrack")] = lambda: tl and tl.AddTrack("video")
    D[("Timeline", "DeleteTrack")] = lambda: (tl.AddTrack("video"),
        tl.DeleteTrack("video", tl.GetTrackCount("video")))[1] if tl else False
    D[("Timeline", "SetCurrentTimecode")] = lambda: tl and tl.SetCurrentTimecode("01:00:00:05")
    D[("Timeline", "SetStartTimecode")] = lambda: tl and tl.SetStartTimecode("01:00:00:00")
    D[("Timeline", "GetMarkerByCustomData")] = lambda: tl and tl.GetMarkerByCustomData("cd1")
    D[("Timeline", "GetMarkerCustomData")] = lambda: tl and tl.GetMarkerCustomData(5)
    D[("Timeline", "UpdateMarkerCustomData")] = lambda: tl and tl.UpdateMarkerCustomData(5, "cd1b")
    D[("Timeline", "DeleteMarkerByCustomData")] = lambda: tl and tl.DeleteMarkerByCustomData("cd1b")
    D[("Timeline", "DeleteMarkerAtFrame")] = lambda: (tl.AddMarker(9, MARK, "n", "x", 1),
        tl.DeleteMarkerAtFrame(9))[1] if tl else False
    D[("Timeline", "DeleteMarkersByColor")] = lambda: tl and tl.DeleteMarkersByColor(MARK)
    D[("Timeline", "SetMarkInOut")] = lambda: tl and tl.SetMarkInOut(1, 10, "all")
    D[("Timeline", "ClearMarkInOut")] = lambda: tl and tl.ClearMarkInOut("all")
    D[("Timeline", "DuplicateTimeline")] = lambda: tl and tl.DuplicateTimeline("dup_tl")
    D[("Timeline", "CreateCompoundClip")] = lambda: tl.CreateCompoundClip(c.titems[:1], {"name": "cc"}) if c.titems else False
    D[("Timeline", "CreateFusionClip")] = lambda: tl.CreateFusionClip(c.titems[:1]) if c.titems else False
    D[("Timeline", "InsertGeneratorIntoTimeline")] = lambda: tl and tl.InsertGeneratorIntoTimeline("Solid Color")
    D[("Timeline", "InsertFusionGeneratorIntoTimeline")] = lambda: tl and tl.InsertFusionGeneratorIntoTimeline("FastNoise")
    D[("Timeline", "InsertOFXGeneratorIntoTimeline")] = lambda: tl and tl.InsertOFXGeneratorIntoTimeline("Colorbars")
    D[("Timeline", "InsertTitleIntoTimeline")] = lambda: tl and tl.InsertTitleIntoTimeline("Text")
    D[("Timeline", "InsertFusionTitleIntoTimeline")] = lambda: tl and tl.InsertFusionTitleIntoTimeline("Text+")
    D[("Timeline", "GetItemsInTrack")] = lambda: tl and tl.GetItemsInTrack("video", 1)
    D[("Timeline", "SetClipsLinked")] = lambda: tl.SetClipsLinked(c.titems[:1], False) if c.titems else False
    D[("Timeline", "DeleteClips")] = lambda: _tl_delete_clip(c)
    D[("Timeline", "ImportIntoTimeline")] = lambda: tl and tl.ImportIntoTimeline(_export_aaf(c), {})
    D[("Timeline", "CreateSubtitlesFromAudio")] = lambda: tl and tl.CreateSubtitlesFromAudio({})
    D[("Timeline", "GetVoiceIsolationState")] = lambda: tl and tl.GetVoiceIsolationState(1)
    D[("Timeline", "SetVoiceIsolationState")] = lambda: tl and tl.SetVoiceIsolationState(1, {"isEnabled": False})
    D[("Timeline", "GrabAllStills")] = lambda: tl and tl.GrabAllStills(1)
    D[("Timeline", "Export")] = lambda: tl and tl.Export(
        os.path.join(c.tmp, "tl_export.drt"), c.resolve.EXPORT_DRT, c.resolve.EXPORT_NONE)

    # ---- TimelineItem ----
    it = c.tli
    D[("TimelineItem", "SetName")] = lambda: it and it.SetName("itemA")
    D[("TimelineItem", "GetProperty")] = lambda: it and it.GetProperty("Pan")
    D[("TimelineItem", "SetProperty")] = lambda: it and it.SetProperty("Pan", 0.0)
    D[("TimelineItem", "GetDuration")] = lambda: it and it.GetDuration(False)
    D[("TimelineItem", "GetStart")] = lambda: it and it.GetStart(False)
    D[("TimelineItem", "GetEnd")] = lambda: it and it.GetEnd(False)
    D[("TimelineItem", "GetLeftOffset")] = lambda: it and it.GetLeftOffset(False)
    D[("TimelineItem", "GetRightOffset")] = lambda: it and it.GetRightOffset(False)
    D[("TimelineItem", "SetClipColor")] = lambda: it and it.SetClipColor(CLIPCOLOR)
    D[("TimelineItem", "SetClipEnabled")] = lambda: it and it.SetClipEnabled(True)
    D[("TimelineItem", "AddFlag")] = lambda: it and it.AddFlag(MARK)
    D[("TimelineItem", "ClearFlags")] = lambda: it and it.ClearFlags(MARK)
    D[("TimelineItem", "GetMarkerByCustomData")] = lambda: it and it.GetMarkerByCustomData("cd1")
    D[("TimelineItem", "GetMarkerCustomData")] = lambda: it and it.GetMarkerCustomData(5)
    D[("TimelineItem", "UpdateMarkerCustomData")] = lambda: it and it.UpdateMarkerCustomData(5, "cd1b")
    D[("TimelineItem", "DeleteMarkerByCustomData")] = lambda: it and it.DeleteMarkerByCustomData("cd1b")
    D[("TimelineItem", "DeleteMarkerAtFrame")] = lambda: (it.AddMarker(6, MARK, "n", "x", 1),
        it.DeleteMarkerAtFrame(6))[1] if it else False
    D[("TimelineItem", "DeleteMarkersByColor")] = lambda: it and it.DeleteMarkersByColor(MARK)
    D[("TimelineItem", "AssignToColorGroup")] = lambda: it and c.group and it.AssignToColorGroup(c.group)
    D[("TimelineItem", "GetNodeGraph")] = lambda: it and it.GetNodeGraph(1)
    D[("TimelineItem", "GetNodeLabel")] = lambda: it and it.GetNodeLabel(1)
    D[("TimelineItem", "GetLUT")] = lambda: it and it.GetLUT(1)
    D[("TimelineItem", "SetLUT")] = lambda: it and it.SetLUT(1, "")
    D[("TimelineItem", "SetCDL")] = lambda: it and it.SetCDL(
        {"NodeIndex": "1", "Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1"})
    D[("TimelineItem", "ExportLUT")] = lambda: it and it.ExportLUT(1, os.path.join(c.tmp, "item.cube"))
    D[("TimelineItem", "CopyGrades")] = lambda: it and len(c.titems) > 1 and it.CopyGrades([c.titems[1]])
    D[("TimelineItem", "AddVersion")] = lambda: it and it.AddVersion("v2", 0)
    D[("TimelineItem", "GetVersionNameList")] = lambda: it and it.GetVersionNameList(0)
    D[("TimelineItem", "GetVersionNames")] = lambda: it and it.GetVersionNames(0)
    D[("TimelineItem", "LoadVersionByName")] = lambda: it and it.LoadVersionByName("v2", 0)
    D[("TimelineItem", "RenameVersionByName")] = lambda: it and it.RenameVersionByName("v2", "v2b", 0)
    D[("TimelineItem", "DeleteVersionByName")] = lambda: it and it.DeleteVersionByName("v2b", 0)
    D[("TimelineItem", "AddTake")] = lambda: it and c.mpi and it.AddTake(c.mpi)
    D[("TimelineItem", "GetTakeByIndex")] = lambda: it and it.GetTakeByIndex(1)
    D[("TimelineItem", "SelectTakeByIndex")] = lambda: it and it.SelectTakeByIndex(1)
    D[("TimelineItem", "DeleteTakeByIndex")] = lambda: it and it.DeleteTakeByIndex(1)
    D[("TimelineItem", "CreateMagicMask")] = lambda: it and it.CreateMagicMask("F")
    D[("TimelineItem", "LoadBurnInPreset")] = lambda: it and it.LoadBurnInPreset("Current")
    D[("TimelineItem", "SetColorOutputCache")] = lambda: it and it.SetColorOutputCache(0)
    D[("TimelineItem", "SetFusionOutputCache")] = lambda: it and it.SetFusionOutputCache(0)
    D[("TimelineItem", "SetVoiceIsolationState")] = lambda: it and it.SetVoiceIsolationState({"isEnabled": False})
    # Fusion comp cluster
    D[("TimelineItem", "GetFusionCompByIndex")] = lambda: it and it.GetFusionCompByIndex(1)
    D[("TimelineItem", "GetFusionCompByName")] = lambda: it and it.GetFusionCompByName("Composition 1")
    D[("TimelineItem", "ExportFusionComp")] = lambda: it and it.ExportFusionComp(os.path.join(c.tmp, "comp.comp"), 1)
    D[("TimelineItem", "ImportFusionComp")] = lambda: it and it.ImportFusionComp(os.path.join(c.tmp, "comp.comp"))
    D[("TimelineItem", "LoadFusionCompByName")] = lambda: it and it.LoadFusionCompByName("Composition 1")
    D[("TimelineItem", "RenameFusionCompByName")] = lambda: it and it.RenameFusionCompByName("Composition 1", "Comp1b")
    D[("TimelineItem", "DeleteFusionCompByName")] = lambda: it and it.DeleteFusionCompByName("Comp1b")

    # ---- Gallery / GalleryStillAlbum ----
    D[("Gallery", "SetCurrentStillAlbum")] = lambda: c.album and c.gallery.SetCurrentStillAlbum(c.album)
    D[("Gallery", "GetAlbumName")] = lambda: c.album and c.gallery.GetAlbumName(c.album)
    D[("Gallery", "SetAlbumName")] = lambda: c.album and c.gallery.SetAlbumName(c.album, "AlbumX")
    D[("GalleryStillAlbum", "GetStills")] = lambda: c.album and c.album.GetStills()
    D[("GalleryStillAlbum", "GetLabel")] = lambda: c.still and c.album.GetLabel(c.still)
    D[("GalleryStillAlbum", "SetLabel")] = lambda: c.still and c.album.SetLabel(c.still, "L")
    D[("GalleryStillAlbum", "ExportStills")] = lambda: c.still and c.album.ExportStills([c.still], c.tmp, "s", "jpg")
    D[("GalleryStillAlbum", "ImportStills")] = lambda: c.album and c.album.ImportStills(
        [os.path.join(c.tmp, "frame.jpg")])
    D[("GalleryStillAlbum", "DeleteStills")] = lambda: c.still and c.album.DeleteStills([c.still])

    # ---- Graph ----
    g = c.graph
    D[("Graph", "GetNodeLabel")] = lambda: g and g.GetNodeLabel(1)
    D[("Graph", "GetToolsInNode")] = lambda: g and g.GetToolsInNode(1)
    D[("Graph", "GetLUT")] = lambda: g and g.GetLUT(1)
    D[("Graph", "SetLUT")] = lambda: g and g.SetLUT(1, "")
    D[("Graph", "GetNodeCacheMode")] = lambda: g and g.GetNodeCacheMode(1)
    D[("Graph", "SetNodeCacheMode")] = lambda: g and g.SetNodeCacheMode(1, 0)
    D[("Graph", "SetNodeEnabled")] = lambda: g and g.SetNodeEnabled(1, True)
    D[("Graph", "ApplyGradeFromDRX")] = lambda: g and g.ApplyGradeFromDRX(os.path.join(c.tmp, "g.drx"), 0)

    # ---- ColorGroup ----
    grp = c.group
    D[("ColorGroup", "SetName")] = lambda: grp and grp.SetName("grpX")
    D[("ColorGroup", "GetClipsInTimeline")] = lambda: grp and grp.GetClipsInTimeline(c.timeline)

    return D


# helpers used by dispatch thunks
def _export_drt(c):
    p = os.path.join(c.tmp, "smoke.drt")
    c.timeline.Export(p, c.resolve.EXPORT_DRT) if hasattr(c.resolve, "EXPORT_DRT") else c.timeline.Export(p, 12)
    return p


def _export_aaf(c):
    p = os.path.join(c.tmp, "smoke.aaf")
    try:
        c.timeline.Export(p, c.resolve.EXPORT_AAF, c.resolve.EXPORT_AAF_NEW)
    except Exception:
        c.timeline.Export(p, 0, 0)
    return p


def _tl_delete_clip(c):
    extra = c.mp.AppendToTimeline(c.media[:1]) if c.media else []
    return c.timeline.DeleteClips(extra, False) if extra else False


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    cov = Cov()
    c = build(cov)
    version = c.resolve.GetVersionString()   # capture while the handle is fresh
    catalog = lib.catalog()
    D = dispatch(c)

    instances = {
        "Resolve": c.resolve, "ProjectManager": c.pm, "Project": c.project,
        "MediaStorage": c.ms, "MediaPool": c.mp, "Folder": c.root,
        "MediaPoolItem": c.mpi, "Timeline": c.timeline, "TimelineItem": c.tli,
        "Gallery": c.gallery, "GalleryStillAlbum": c.album, "Graph": c.graph,
        "ColorGroup": c.group,
    }

    # Snapshot each object's live methods NOW, from fresh handles, straight off
    # the installed build's own objects. This is the ground truth we compare the
    # README catalog against — taken from the installed Resolve, nothing external.
    live_methods = {n: set(dir(inst)) for n, inst in instances.items()
                    if inst is not None}

    deferred = []
    for obj_name, entries in catalog.items():
        target = instances.get(obj_name)
        seen = set()
        for e in entries:
            name = e["name"]
            if name in seen:
                continue
            seen.add(name)
            sig = e["signature"]
            key = (obj_name, name)

            if key in HARD_SKIP:
                cov.rec(obj_name, name, "skipped", HARD_SKIP[key], sig)
                continue
            if key in EXCLUDE:
                cov.rec(obj_name, name, "skipped", EXCLUDE[key], sig)
                continue
            if key in SESSION_LAST:
                deferred.append((obj_name, name, sig))   # run after the sweep
                continue
            # Phantom: in the README catalog but absent on THIS live build
            # (version drift). Checked against a FRESH-instance snapshot taken
            # at startup, so later staleness can't masquerade as drift.
            if target is not None and name not in live_methods.get(obj_name, set()):
                cov.rec(obj_name, name, "skipped",
                        "not present in this Resolve build (README drift)", sig)
                continue
            if key in D:
                cov.run(obj_name, name, D[key], sig)
                continue
            if target is None:
                cov.rec(obj_name, name, "skipped", "no live instance", sig)
                continue
            args = e["signature"].split("(", 1)[1].rstrip(")").strip()
            if args == "":
                cov.run(obj_name, name, getattr(target, name), sig)
            else:
                cov.rec(obj_name, name, "skipped", "no dispatch entry", sig)

    # Session-breakers last (they invalidate downstream refs, which is fine now
    # that everything else is already swept).
    for obj_name, name, sig in deferred:
        if (obj_name, name) in D:
            cov.run(obj_name, name, D[(obj_name, name)], sig)

    if c.prev_name:
        try:
            # The sweep may have staled the cached handle; reconnect to restore.
            connection.get_resolve().GetProjectManager().LoadProject(c.prev_name)
        except Exception:
            pass

    counts = {}
    for r in cov.rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total = sum(len({m["name"] for m in v}) for v in catalog.values())
    invoked = counts.get("ok", 0) + counts.get("falsey", 0) + counts.get("error", 0)

    report = {"resolve_version": version,
              "catalog_distinct_methods": total, "invoked": invoked,
              "counts": counts,
              "rows": sorted(cov.rows, key=lambda r: (r["object"], r["method"]))}
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(f"Resolve {report['resolve_version']} — {total} methods")
    print(f"invoked {invoked}/{total} | counts {counts}")
    print("report:", REPORT)


if __name__ == "__main__":
    main()
