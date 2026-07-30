"""Thin bridge to a running DaVinci Resolve.

Everything here is best-effort: Resolve may be closed, may have external
scripting disabled, or may ship a fusionscript built for a different Python
than the one ComfyUI runs on. Any of those must surface as a readable message,
never as a traceback that kills a workflow run.

Note on Python versions: fusionscript.dll is a C extension, so it only loads
into the interpreter it was built for. Resolve 21 pairs with Python 3.13 (what
the ComfyUI portable build ships); loading it from 3.11 segfaults the process
outright. That is why this module refuses to guess and simply reports what the
import did.
"""

import os
import platform
import sys
import threading

# The scripting API is not documented as thread safe and aiohttp hands us
# requests from a pool, so every Resolve conversation is serialised.
_LOCK = threading.RLock()
_MODULE = None
_MODULE_ERROR = None


class ResolveUnavailable(RuntimeError):
    """Resolve could not be reached. The message is meant for the user."""


def _candidate_paths():
    system = platform.system()
    if system == "Windows":
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        api = os.path.join(
            program_data, "Blackmagic Design", "DaVinci Resolve", "Support", "Developer", "Scripting"
        )
        lib = os.path.join(program_files, "Blackmagic Design", "DaVinci Resolve", "fusionscript.dll")
    elif system == "Darwin":
        api = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        lib = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    else:
        api = "/opt/resolve/Developer/Scripting"
        lib = "/opt/resolve/libs/Fusion/fusionscript.so"
    return api, lib


def _load_module():
    """Import DaVinciResolveScript once, remembering any failure."""
    global _MODULE, _MODULE_ERROR

    if _MODULE is not None:
        return _MODULE
    if _MODULE_ERROR is not None:
        raise ResolveUnavailable(_MODULE_ERROR)

    api, lib = _candidate_paths()
    api = os.environ.get("RESOLVE_SCRIPT_API", api)
    lib = os.environ.get("RESOLVE_SCRIPT_LIB", lib)

    if not os.path.isdir(api):
        _MODULE_ERROR = "cannot find the Resolve scripting folder at '{}'. Is Resolve installed?".format(api)
        raise ResolveUnavailable(_MODULE_ERROR)

    os.environ["RESOLVE_SCRIPT_API"] = api
    os.environ["RESOLVE_SCRIPT_LIB"] = lib
    modules = os.path.join(api, "Modules")
    if modules not in sys.path:
        sys.path.append(modules)

    try:
        import DaVinciResolveScript as dvr
    except Exception as exc:  # noqa: BLE001
        _MODULE_ERROR = "cannot load DaVinciResolveScript ({}: {})".format(
            type(exc).__name__, exc
        )
        raise ResolveUnavailable(_MODULE_ERROR)

    _MODULE = dvr
    return _MODULE


def connect():
    """Return the Resolve app object, or raise ResolveUnavailable."""
    dvr = _load_module()
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveUnavailable(
            "Resolve is not answering. Open it and set Preferences > System > General > "
            "'External scripting using' to Local."
        )
    return resolve


def status():
    """Read-only health probe for the UI. Never raises."""
    try:
        resolve = connect()
        project = resolve.GetProjectManager().GetCurrentProject()
        return {
            "available": True,
            "version": resolve.GetVersionString(),
            "project": project.GetName() if project else None,
        }
    except ResolveUnavailable as exc:
        return {"available": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": "{}: {}".format(type(exc).__name__, exc)}


def _find_or_create_bin(media_pool, bin_path):
    """Resolve a bin by name, creating it if needed. 'a/b' nests."""
    folder = media_pool.GetRootFolder()
    parts = [p for p in (bin_path or "").replace("\\", "/").split("/") if p.strip()]

    for part in parts:
        part = part.strip()
        existing = None
        for sub in folder.GetSubFolderList() or []:
            if sub.GetName() == part:
                existing = sub
                break
        if existing is None:
            existing = media_pool.AddSubFolder(folder, part)
            if not existing:
                raise ResolveUnavailable("could not create bin '{}'".format(part))
        folder = existing
    return folder


PLACEMENTS = ["bin", "playhead", "end"]


def timecode_to_frame(timecode, fps):
    """'01:00:33:06' -> absolute frame number.

    Drop-frame timecode (29.97/59.94, written with ';') needs the dropped-frame
    correction or the result drifts by ~3.6 s per hour. Untested here: the only
    timeline available while writing this was 24 fps non-drop.
    """
    drop = ";" in timecode or "," in timecode
    parts = timecode.replace(";", ":").replace(",", ":").split(":")
    if len(parts) != 4:
        raise ValueError("timecode no reconocido: {!r}".format(timecode))
    hours, minutes, seconds, frames = (int(p) for p in parts)

    nominal = int(round(float(fps)))
    total = ((hours * 3600) + (minutes * 60) + seconds) * nominal + frames

    if drop:
        dropped = int(round(float(fps) * 0.066666))
        total_minutes = hours * 60 + minutes
        total -= dropped * (total_minutes - total_minutes // 10)
    return total


def _ensure_audio_track(timeline, index):
    """Make sure audio track `index` exists, adding tracks if needed."""
    count = timeline.GetTrackCount("audio") or 0
    while count < index:
        if not timeline.AddTrack("audio"):
            raise ResolveUnavailable("could not add an audio track")
        count = timeline.GetTrackCount("audio") or 0
    return index


def frame_to_timecode(frame, fps):
    """Inverse of timecode_to_frame, non-drop only (callers guard for that)."""
    nominal = max(1, int(round(float(fps))))
    frames = int(frame) % nominal
    total_seconds = int(frame) // nominal
    return "{:02d}:{:02d}:{:02d}:{:02d}".format(
        total_seconds // 3600, (total_seconds // 60) % 60, total_seconds % 60, frames
    )


def _place_on_timeline(
    project, media_pool, item, placement, track_index, warnings, expected_seconds=None
):
    """Append `item` to the current timeline. Returns a description or None."""
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        warnings.append("no active timeline; the clip stayed in the bin only")
        return None

    try:
        track_index = _ensure_audio_track(timeline, max(1, int(track_index)))
    except ResolveUnavailable as exc:
        warnings.append(str(exc))
        return None

    fps = None
    drop_frame = False
    record_frame = None
    requested_timecode = None

    if placement == "playhead":
        try:
            fps = float(project.GetSetting("timelineFrameRate"))
            # Captured before appending: Resolve moves the playhead to the end
            # of the new clip, so reading it afterwards reports the wrong spot.
            requested_timecode = timeline.GetCurrentTimecode()
            drop_frame = ";" in requested_timecode or "," in requested_timecode
            record_frame = timecode_to_frame(requested_timecode, fps)
        except Exception as exc:  # noqa: BLE001
            # Landing in the wrong place is worse than landing at the end, so
            # say so rather than guess.
            warnings.append(
                "could not read the playhead ({}); appending to the end instead".format(exc)
            )
            record_frame = None

    def track_snapshot():
        return [
            (it.GetStart(), it.GetEnd(), it.GetName())
            for it in timeline.GetItemListInTrack("audio", track_index) or []
        ]

    before = set(track_snapshot())

    clip_info = {"mediaPoolItem": item, "mediaType": 2, "trackIndex": track_index}
    if record_frame is not None:
        clip_info["recordFrame"] = record_frame

    media_pool.AppendToTimeline([clip_info])

    # AppendToTimeline's return value cannot be trusted here. Observed with
    # Resolve 21 when recordFrame lands on occupied track material: it returns a
    # truthy item whose GetStart() echoes the *requested* frame while nothing is
    # added to the track at all, and in another case it placed a 20 s clip
    # truncated to 5 s. So the track itself is the only source of truth.
    added = [entry for entry in track_snapshot() if entry not in before]

    if not added:
        warnings.append(
            "Resolve did not place it on A{} (the requested spot was occupied). "
            "The clip IS in the bin: drag it in by hand, or use placement='end'".format(track_index)
        )
        return None

    actual_frame, actual_end, _ = added[0]
    placed_frames = actual_end - actual_frame

    if fps and not drop_frame:
        shown = frame_to_timecode(actual_frame, fps)
    else:
        shown = "frame {}".format(actual_frame)

    if record_frame is not None and actual_frame != record_frame:
        warnings.append(
            "asked for {} but Resolve placed it at {}".format(requested_timecode, shown)
        )

    if expected_seconds and fps:
        expected_frames = int(round(float(expected_seconds) * float(fps)))
        # A tolerance of a couple of frames absorbs rounding at the boundary.
        if placed_frames < expected_frames - 2:
            warnings.append(
                "the clip landed truncated: {} of {} frames ({:.1f}s of {:.1f}s)".format(
                    placed_frames, expected_frames, placed_frames / fps, float(expected_seconds)
                )
            )

    return {
        "timeline": timeline.GetName(),
        "track": track_index,
        "requested_frame": record_frame,
        "actual_frame": actual_frame,
        "placed_frames": placed_frames,
        "where": "A{} @ {}".format(track_index, shown),
    }


def import_audio(
    file_path,
    bin_path="sfx",
    clip_name=None,
    metadata=None,
    placement="bin",
    track_index=1,
    expected_seconds=None,
):
    """Import one audio file into `bin_path` and tag it.

    Returns a dict describing what happened. Raises ResolveUnavailable if
    Resolve is not reachable; other failures are reported in the result so a
    clip that imported but could not be tagged is not reported as a failure.
    """
    if not os.path.isfile(file_path):
        raise ResolveUnavailable("file does not exist: {}".format(file_path))

    with _LOCK:
        resolve = connect()
        project = resolve.GetProjectManager().GetCurrentProject()
        if project is None:
            raise ResolveUnavailable("no project is open in Resolve")

        media_pool = project.GetMediaPool()
        previous_folder = media_pool.GetCurrentFolder()
        target = _find_or_create_bin(media_pool, bin_path)

        try:
            media_pool.SetCurrentFolder(target)
            items = media_pool.ImportMedia([file_path])
        finally:
            if previous_folder:
                media_pool.SetCurrentFolder(previous_folder)

        if not items:
            raise ResolveUnavailable(
                "Resolve rejected the file (format the media pool does not accept?)"
            )

        item = items[0]
        warnings = []

        if clip_name:
            try:
                item.SetClipProperty("Clip Name", clip_name)
            except Exception as exc:  # noqa: BLE001
                warnings.append("could not rename the clip: {}".format(exc))

        applied = []
        for key, value in (metadata or {}).items():
            if value in (None, ""):
                continue
            try:
                if item.SetMetadata(key, str(value)):
                    applied.append(key)
                else:
                    warnings.append("Resolve refused metadata field '{}'".format(key))
            except Exception as exc:  # noqa: BLE001
                warnings.append("metadata field '{}' failed: {}".format(key, exc))

        placed = None
        if placement in ("playhead", "end"):
            try:
                placed = _place_on_timeline(
                    project, media_pool, item, placement, track_index, warnings, expected_seconds
                )
            except Exception as exc:  # noqa: BLE001
                # The clip is already in the bin; a timeline failure must not
                # turn the whole send into an error.
                warnings.append("failed to place it on the timeline: {}".format(exc))

        return {
            "clip_name": item.GetName(),
            "bin": bin_path,
            "project": project.GetName(),
            "metadata_applied": applied,
            "placed": placed,
            "warnings": warnings,
        }
