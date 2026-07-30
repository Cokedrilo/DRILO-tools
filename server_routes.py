"""HTTP endpoints used by web/audio_picker.js."""

import asyncio
import os
import shutil

from aiohttp import web

import folder_paths
from server import PromptServer

from . import resolve_bridge
from .nodes import (
    BUFFERS,
    SAVED,
    SENT_TO_RESOLVE,
    SUPPORTED_FORMATS,
    decode_audio,
    display_path,
    encode_audio,
    normalize_waveform,
    _saved_key,
)

routes = PromptServer.instance.routes

EXTENSIONS = {"flac": "flac", "mp3": "mp3", "opus": "opus"}


def _inside(path, root):
    path = os.path.abspath(path)
    root = os.path.abspath(root)
    return path == root or path.startswith(root + os.sep)


def _temp_path(filename, subfolder):
    """Resolve a preview file, refusing anything outside temp/."""
    temp_dir = folder_paths.get_temp_directory()
    path = os.path.abspath(os.path.join(temp_dir, subfolder or "", filename or ""))
    if not _inside(path, temp_dir):
        raise ValueError("path outside the temp folder")
    if not os.path.isfile(path):
        raise FileNotFoundError("the preview has expired or no longer exists")
    return path


def _next_free_path(full_output_folder, filename, counter, ext):
    """Pick the first free `name_00001_.ext`, so nothing gets overwritten."""
    while True:
        candidate = "{}_{:05}_.{}".format(filename, counter, ext)
        path = os.path.join(full_output_folder, candidate)
        if not os.path.exists(path):
            return path, candidate, counter
        counter += 1


def _do_save(source, filename_prefix, fmt, normalize="off", target_db=-1.0):
    ext = EXTENSIONS[fmt]
    output_dir = folder_paths.get_output_directory()

    full_output_folder, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
        filename_prefix, output_dir
    )
    os.makedirs(full_output_folder, exist_ok=True)

    target, target_name, _ = _next_free_path(full_output_folder, filename, counter, ext)

    source_ext = os.path.splitext(source)[1].lstrip(".").lower()
    normalising = normalize not in (None, "", "off")

    norm_info = None
    if source_ext == ext and not normalising:
        # Same container and nothing to change: a byte copy beats a re-encode.
        shutil.copy2(source, target)
    else:
        waveform, sample_rate = decode_audio(source)
        if normalising:
            waveform, norm_info = normalize_waveform(waveform, sample_rate, normalize, target_db)
        encode_audio(waveform, sample_rate, target, fmt)

    return {
        "saved_path": display_path(target),
        "absolute_path": target,
        "filename": target_name,
        "subfolder": subfolder,
        "normalize": norm_info,
    }


@routes.post("/audio_picker/save")
async def audio_picker_save(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid JSON body"}, status=400)

    node_id = str(data.get("node_id", ""))
    key = str(data.get("uid") or data.get("index"))
    fmt = data.get("format", "flac")
    filename_prefix = data.get("filename_prefix") or "audio/pick"

    if fmt not in SUPPORTED_FORMATS:
        return web.json_response({"error": "unsupported format: {}".format(fmt)}, status=400)

    try:
        source = _temp_path(data.get("filename"), data.get("subfolder"))
    except FileNotFoundError as exc:
        return web.json_response({"error": str(exc), "expired": True}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    sample = _find_sample(node_id, key) or {}
    normalize = data.get("normalize") or sample.get("normalize") or "off"
    target_db = data.get("normalize_target_db")
    if target_db is None:
        target_db = sample.get("normalize_target_db", -1.0)

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, _do_save, source, filename_prefix, fmt, normalize, float(target_db)
        )
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": str(exc)}, status=500)

    SAVED[_saved_key(node_id, key)] = result["absolute_path"]

    # _find_sample returns the live dict from BUFFERS, so this updates the row
    # the next execution will serialise.
    if sample:
        sample["saved"] = True
        sample["saved_path"] = result["saved_path"]

    return web.json_response(
        {
            "saved_path": result["saved_path"],
            "filename": result["filename"],
            "subfolder": result["subfolder"],
            "normalize": result["normalize"],
        }
    )


@routes.post("/audio_picker/unsave")
async def audio_picker_unsave(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid JSON body"}, status=400)

    node_id = str(data.get("node_id", ""))
    key = str(data.get("uid") or data.get("index"))
    path = SAVED.pop(_saved_key(node_id, key), None)
    resolve_clip = SENT_TO_RESOLVE.pop(_saved_key(node_id, key), None)

    for sample in BUFFERS.get(node_id, []):
        if sample.get("uid") == key or str(sample.get("index")) == key:
            sample["saved"] = False
            sample["saved_path"] = None
            sample["sent_to_resolve"] = False

    # Resolve keeps a path reference, so deleting the file orphans the clip.
    # We cannot remove it from the media pool safely (the editor may have put it
    # on a timeline already), so the honest move is to say so.
    warning = (
        "clip '{}' is still in the Resolve media pool and now points at a deleted "
        "file; remove it by hand if you do not want it".format(resolve_clip)
        if resolve_clip
        else None
    )

    if not path:
        # Nothing on record: the checkbox was already off as far as we know.
        return web.json_response({"ok": True, "removed": False, "warning": warning})

    output_dir = folder_paths.get_output_directory()
    if not _inside(path, output_dir):
        return web.json_response({"error": "path outside output/"}, status=400)

    try:
        if os.path.isfile(path):
            os.remove(path)
            removed = True
        else:
            removed = False
    except OSError as exc:
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"ok": True, "removed": removed, "warning": warning})


def _find_sample(node_id, key):
    for sample in BUFFERS.get(node_id, []):
        if sample.get("uid") == key or str(sample.get("index")) == key:
            return sample
    return None


def _resolve_tags(sample, absolute):
    """Clip name and metadata for a sample about to land in the media pool."""
    stem = os.path.splitext(os.path.basename(absolute))[0].rstrip("_")
    seed = sample.get("seed")
    prompt_text = (sample.get("prompt_text") or "").strip()
    duration = sample.get("duration")

    clip_name = "{} - seed {}".format(stem, seed) if seed is not None else stem

    comments = " | ".join(
        part
        for part in [
            "seed {}".format(seed) if seed is not None else None,
            sample.get("format"),
            "{}s".format(duration) if duration is not None else None,
            "{} Hz".format(sample.get("sample_rate")) if sample.get("sample_rate") else None,
            "ComfyUI DRILO AudioMultiExport",
        ]
        if part
    )

    return clip_name, {
        # Description carries the prompt so the timeline traces back to what
        # generated the sound. Resolve shows long values fine but the metadata
        # panel is narrow, so it is trimmed.
        "Description": prompt_text[:300] if prompt_text else None,
        "Comments": comments,
        "Keywords": "comfyui, drilo, audiomultiexport",
    }


@routes.get("/audio_picker/resolve_status")
async def audio_picker_resolve_status(request):
    loop = asyncio.get_event_loop()
    # The probe touches the Resolve API, so it must not run on the event loop.
    result = await loop.run_in_executor(None, resolve_bridge.status)
    return web.json_response(result)


@routes.post("/audio_picker/to_resolve")
async def audio_picker_to_resolve(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid JSON body"}, status=400)

    node_id = str(data.get("node_id", ""))
    key = str(data.get("uid") or data.get("index"))
    saved_key = _saved_key(node_id, key)
    absolute = SAVED.get(saved_key)

    if not absolute or not os.path.isfile(absolute):
        # The media pool references files by path, so importing a preview from
        # temp/ would leave a broken clip behind the next time ComfyUI restarts.
        return web.json_response(
            {"error": "tick the checkbox first: only files already saved to output/ can be sent"},
            status=409,
        )

    sample = _find_sample(node_id, key) or {}
    bin_path = data.get("bin") or sample.get("resolve_bin") or "sfx"
    placement = data.get("placement") or sample.get("resolve_placement") or "bin"
    if placement not in resolve_bridge.PLACEMENTS:
        placement = "bin"
    track_index = data.get("track_index") or sample.get("resolve_audio_track") or 1
    clip_name, metadata = _resolve_tags(sample, absolute)

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            resolve_bridge.import_audio,
            absolute,
            bin_path,
            clip_name,
            metadata,
            placement,
            int(track_index),
            sample.get("duration"),
        )
    except resolve_bridge.ResolveUnavailable as exc:
        return web.json_response({"error": str(exc), "resolve_offline": True}, status=503)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": "{}: {}".format(type(exc).__name__, exc)}, status=500)

    SENT_TO_RESOLVE[saved_key] = result["clip_name"]
    if sample:
        sample["sent_to_resolve"] = True

    return web.json_response(result)


@routes.post("/audio_picker/clear")
async def audio_picker_clear(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "invalid JSON body"}, status=400)

    node_id = str(data.get("node_id", ""))
    samples = BUFFERS.pop(node_id, [])

    # Forget the save bookkeeping too; the saved files themselves stay in output/.
    for sample in samples:
        SAVED.pop(_saved_key(node_id, sample.get("uid")), None)
        SENT_TO_RESOLVE.pop(_saved_key(node_id, sample.get("uid")), None)
    for index in range(len(samples)):
        SAVED.pop(_saved_key(node_id, index), None)
        SENT_TO_RESOLVE.pop(_saved_key(node_id, index), None)

    return web.json_response({"ok": True, "cleared": len(samples)})
