# Coverage results

Produced by `tests/api_coverage.py` against **DaVinci Resolve Studio 21.0.4.5**
(macOS). The exerciser drives every documented method in a throwaway project and
records the outcome. Every method lands in exactly one bucket — no silent gaps.

## Headline

| Metric | Value |
|---|---:|
| Distinct documented methods (from the installed README) | **375** |
| Invoked (executed without raising) | **350** |
| — returned a real value (`ok`) | 171 |
| — called cleanly, returned None/False for the test state (`falsey`) | 179 |
| Errors | **0** |
| Skipped (genuinely uncallable unattended) | **25** |

"Falsey" means the method ran fine but returned nothing meaningful given the
scratch state (e.g. a getter for a value that isn't set) — it was still invoked
successfully.

## Per object

| Object | ok | falsey | skipped |
|---|--:|--:|--:|
| Resolve | 20 | 11 | 1 |
| ProjectManager | 13 | 3 | 11 |
| Project | 34 | 11 | 3 |
| MediaStorage | 9 | 2 | 0 |
| MediaPool | 19 | 7 | 0 |
| Folder | 10 | 1 | 4 |
| MediaPoolItem | 2 | 36 | 4 |
| Timeline | 48 | 9 | 1 |
| TimelineItem | 0 | 85 | 1 |
| Gallery | 8 | 0 | 0 |
| GalleryStillAlbum | 1 | 5 | 0 |
| Graph | 2 | 9 | 0 |
| ColorGroup | 5 | 0 | 0 |

## The 25 skips — the honest floor

Every one is genuinely uncallable in an unattended sweep, with its reason:

| Reason | Count | Methods |
|---|--:|---|
| Long-running AI analysis | 4 | Folder/MediaPoolItem `AnalyzeForSlate`, `AnalyzeForIntellisearch` |
| Needs cloud login | 4 | ProjectManager `Create/Import/Load/RestoreCloudProject` |
| Pops a GUI confirm modal | 3 | ProjectManager `CloseProject`, `DeleteProject`, `ImportProject` |
| Long-running AI render | 2 | Folder/MediaPoolItem `RemoveMotionBlur` |
| Long-running AI (speech) | 2 | Folder/MediaPoolItem `TranscribeAudio` |
| Pops a modal (build uses it once) | 1 | ProjectManager `CreateProject` |
| Reloads session / pops modal | 1 | ProjectManager `LoadProject` |
| Needs a `.dra` archive | 1 | ProjectManager `RestoreProject` |
| Would switch DB / close project | 1 | ProjectManager `SetCurrentDatabase` |
| AI text-to-speech | 1 | Project `GenerateSpeech` |
| Performs a full render | 1 | Project `RenderWithQuickExport` |
| AI transcription | 1 | Timeline `CreateSubtitlesFromAudio` |
| AI mask/tracking | 1 | TimelineItem `CreateMagicMask` |
| Would close Resolve | 1 | Resolve `Quit` |
| **Not present in this build (README drift)** | 1 | Project `DeleteRenderJobByIndex` |

## Drift the sweep found

Because discovery is a live catalog cross-checked against the running build, the
sweep surfaces where BMD's shipped README and the actual build disagree on
21.0.4.5:

- **Documented but absent:** `Project.DeleteRenderJobByIndex` — in the README,
  not exposed by the live object. It's the single "drift" skip above.
- **Live but undocumented:** methods the objects expose that the README doesn't
  list — e.g. `MediaPool.CreateStereoClip`, `ProjectManager.ArchiveProject`,
  `Timeline.AnalyzeDolbyVision`, plus the internal `Print` on every object.

This is exactly why the method list is never frozen: it's read from the
installed version's own files, and `lib.describe()` reports the diff.

## Reproduce

```bash
python tests/api_coverage.py /path/to/a/folder/of/short/clips
```

Outputs `tests/api_coverage_report.json` (full per-method results) and a live
`tests/api_coverage_progress.jsonl` (one line per attempt, so a hang names its
culprit). All work happens in a throwaway `FORGE_API_SMOKE_N` project.
