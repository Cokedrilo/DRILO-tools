"""DRILO AudioMultiExport node: previews every sample of an AUDIO batch and lets the user
pick which ones get written to output/ from the frontend."""

import math
import os
import uuid

import torch
import torchaudio

import folder_paths


# ---------------------------------------------------------------------------
# Module level state, shared with server_routes.py
# ---------------------------------------------------------------------------

# {node_id: [sample, ...]} -- the preview list currently shown by each node.
BUFFERS = {}

# {"<node_id>:<key>": absolute path} -- files already copied into output/.
SAVED = {}

# {"<node_id>:<key>": clip name} -- samples already pushed to the Resolve pool.
# Only a UI hint: removing a clip inside Resolve is not something we can see.
SENT_TO_RESOLVE = {}

SUPPORTED_FORMATS = ["flac", "mp3", "opus"]

# Previews are always written as flac: lossless, playable by every browser and
# writable by torchaudio without an external encoder. Saving to mp3/opus
# re-encodes from this file.
PREVIEW_FORMAT = "flac"


def _saved_key(node_id, key):
    return "{}:{}".format(node_id, key)


def compose_prefix(output_folder, filename):
    """Join the two widgets into the single prefix ComfyUI's saver expects.

    folder_paths.get_save_image_path takes one `folder/name` string, so the
    split lives only in the UI. Separators are normalised and the ends trimmed
    so that "audio/", "/audio" and "audio\\" all behave the same; traversal is
    still caught by get_save_image_path itself.
    """
    folder = (output_folder or "").replace("\\", "/").strip().strip("/")
    name = (filename or "").replace("\\", "/").strip().strip("/")
    if not name:
        name = "pick"
    return "{}/{}".format(folder, name) if folder else name


def display_path(absolute):
    """What the UI shows: relative to output/, forward slashes.

    SAVED holds absolute paths because unsave has to delete the file, but
    leaking a full `<output_dir>/audio/pick_00001_.flac` into a node row is
    noise. Never build a filesystem path from this.
    """
    if not absolute:
        return None
    try:
        relative = os.path.relpath(absolute, folder_paths.get_output_directory())
    except ValueError:
        # Different drive on Windows: fall back to the bare filename.
        relative = os.path.basename(absolute)
    return relative.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def _to_2d(waveform):
    """Return a [C, T] float tensor on cpu."""
    wav = waveform.detach().cpu().float()
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)
    elif wav.dim() > 2:
        wav = wav.reshape(-1, wav.shape[-1])
    return wav


def _encode_with_av(waveform, sample_rate, path, fmt):
    """Fallback encoder using PyAV (shipped with ComfyUI) for mp3/opus."""
    import av

    codec = {"mp3": "libmp3lame", "opus": "libopus", "flac": "flac"}[fmt]
    container_fmt = {"mp3": "mp3", "opus": "ogg", "flac": "flac"}[fmt]

    wav = _to_2d(waveform)
    channels = min(wav.shape[0], 2)
    wav = wav[:channels]

    # libopus only accepts a handful of rates; 48k is always valid.
    out_rate = 48000 if fmt == "opus" else sample_rate
    if out_rate != sample_rate:
        wav = torchaudio.functional.resample(wav, sample_rate, out_rate)

    layout = "mono" if channels == 1 else "stereo"

    with av.open(path, mode="w", format=container_fmt) as container:
        stream = container.add_stream(codec, rate=out_rate)
        stream.layout = layout
        frame = av.AudioFrame.from_ndarray(
            wav.contiguous().numpy(), format="fltp", layout=layout
        )
        frame.sample_rate = out_rate
        frame.pts = 0
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


# ---------------------------------------------------------------------------
# Loudness / normalisation
# ---------------------------------------------------------------------------

NORMALIZE_MODES = ["off", "peak", "rms", "lufs"]

# ITU-R BS.1770 K-weighting. These coefficients are only valid at 48 kHz, so
# measurement resamples there; the gain is then applied to the original audio.
_K_STAGE1_B = [1.53512485958697, -2.69169618940638, 1.19839281085285]
_K_STAGE1_A = [1.0, -1.69065929318241, 0.73248077421585]
_K_STAGE2_B = [1.0, -2.0, 1.0]
_K_STAGE2_A = [1.0, -1.99004745483398, 0.99007225036621]

_ABSOLUTE_GATE_LUFS = -70.0
# Below this, a take is treated as silence rather than something to lift.
_SILENCE_FLOOR_DB = -60.0
_RELATIVE_GATE_LU = -10.0
_LOUDNESS_OFFSET = -0.691  # BS.1770 constant


def _amp_to_db(amplitude):
    return 20.0 * math.log10(max(float(amplitude), 1e-12))


def _integrated_lufs(waveform, sample_rate):
    """Gated integrated loudness per BS.1770-4, in LUFS. None if too short."""
    wav = _to_2d(waveform)

    if sample_rate != 48000:
        wav = torchaudio.functional.resample(wav, sample_rate, 48000)
    rate = 48000

    filtered = torchaudio.functional.biquad(wav, *_K_STAGE1_B, *_K_STAGE1_A)
    filtered = torchaudio.functional.biquad(filtered, *_K_STAGE2_B, *_K_STAGE2_A)

    block = int(0.4 * rate)
    step = int(0.1 * rate)
    if filtered.shape[-1] < block:
        return None

    squared = filtered.pow(2)
    # Per-channel mean square for every 400 ms block, hopping 100 ms.
    blocks = squared.unfold(-1, block, step).mean(dim=-1)  # [C, n_blocks]
    # Channel weights are 1.0 for L/R (and for mono); no surround handling here.
    summed = blocks.sum(dim=0)  # [n_blocks]

    loudness = _LOUDNESS_OFFSET + 10.0 * torch.log10(summed.clamp_min(1e-12))

    keep = loudness > _ABSOLUTE_GATE_LUFS
    if not bool(keep.any()):
        return None

    ungated = _LOUDNESS_OFFSET + 10.0 * math.log10(max(float(summed[keep].mean()), 1e-12))
    keep = keep & (loudness > (ungated + _RELATIVE_GATE_LU))
    if not bool(keep.any()):
        return None

    return _LOUDNESS_OFFSET + 10.0 * math.log10(max(float(summed[keep].mean()), 1e-12))


def normalize_waveform(waveform, sample_rate, mode, target_db):
    """Apply `mode` normalisation towards `target_db`.

    Returns (waveform, info). `info` always says what was measured and what
    gain was applied, so the UI can show it instead of the user guessing.
    """
    wav = _to_2d(waveform)
    info = {
        "mode": mode,
        "target_db": float(target_db),
        "measured": None,
        "gain_db": 0.0,
        "peak_after_db": None,
        "gain_limited": False,
        "note": None,
    }

    if mode not in NORMALIZE_MODES or mode == "off":
        info["mode"] = "off"
        info["peak_after_db"] = round(_amp_to_db(wav.abs().max()), 2)
        return wav, info

    if mode in ("peak", "rms"):
        if mode == "peak":
            measured = _amp_to_db(wav.abs().max())
        else:
            measured = _amp_to_db(math.sqrt(max(float(wav.pow(2).mean()), 1e-24)))
        # Without this floor, silence measures -240 dB and asks for +239 dB of
        # gain. On true zeros that is harmless, but on a near-silent take it
        # would blow the noise floor up to full scale.
        if measured < _SILENCE_FLOOR_DB:
            info["measured"] = round(float(measured), 2)
            info["note"] = "practically silent ({:.1f} dB); left untouched".format(measured)
            info["peak_after_db"] = round(_amp_to_db(wav.abs().max()), 2)
            return wav, info
    else:
        measured = _integrated_lufs(wav, sample_rate)
        if measured is None:
            info["note"] = "too short or too quiet to measure LUFS; left untouched"
            info["peak_after_db"] = round(_amp_to_db(wav.abs().max()), 2)
            return wav, info

    info["measured"] = round(float(measured), 2)
    gain_db = float(target_db) - float(measured)

    # Never let a loudness target push the waveform into the clamp inside
    # encode_audio: back the gain off so the peak lands just under full scale.
    peak_db = _amp_to_db(wav.abs().max())
    headroom = -0.1 - peak_db
    if gain_db > headroom:
        info["gain_limited"] = True
        info["note"] = "gain capped from {:+.2f} to {:+.2f} dB to avoid clipping".format(
            gain_db, headroom
        )
        gain_db = headroom

    wav = wav * (10.0 ** (gain_db / 20.0))
    info["gain_db"] = round(gain_db, 2)
    info["peak_after_db"] = round(_amp_to_db(wav.abs().max()), 2)
    return wav, info


def _decode_with_av(path):
    """Decode any container PyAV understands into ([C, T] float tensor, rate)."""
    import av
    import numpy as np

    with av.open(path) as container:
        stream = container.streams.audio[0]
        sample_rate = int(stream.codec_context.sample_rate)
        layout = stream.layout.name
        resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout=layout, rate=sample_rate
        )

        chunks = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())

    if not chunks:
        raise RuntimeError("the file holds no decodable audio")

    data = np.concatenate(chunks, axis=1)
    return torch.from_numpy(data).float(), sample_rate


def decode_audio(path):
    """Read an audio file back as ([C, T] float tensor, sample_rate).

    torchaudio.load needs TorchCodec from torchaudio 2.9 onwards and it is not
    part of the portable build, so PyAV does the work on most installs.
    """
    torchaudio_error = None
    try:
        waveform, sample_rate = torchaudio.load(path)
        return _to_2d(waveform), int(sample_rate)
    except Exception as exc:  # noqa: BLE001
        torchaudio_error = exc

    try:
        return _decode_with_av(path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "could not read '{}': torchaudio said '{}', PyAV said '{}'".format(
                os.path.basename(path), torchaudio_error, exc
            )
        )


def encode_audio(waveform, sample_rate, path, fmt):
    """Write [C, T] audio to `path` in `fmt`. torchaudio first, PyAV as backup."""
    wav = _to_2d(waveform)
    wav = torch.clamp(wav, -1.0, 1.0)

    torchaudio_error = None
    try:
        torchaudio.save(path, wav, sample_rate, format=fmt)
        return
    except Exception as exc:  # noqa: BLE001 - reported back to the client
        torchaudio_error = exc

    try:
        _encode_with_av(wav, sample_rate, path, fmt)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "could not encode to {}: torchaudio said '{}', PyAV said '{}'".format(
                fmt, torchaudio_error, exc
            )
        )


# ---------------------------------------------------------------------------
# Seed lookup
# ---------------------------------------------------------------------------

def _upstream_node_ids(prompt, node_id):
    """Breadth first walk over the inputs of `node_id`, nearest node first."""
    order = []
    seen = {str(node_id)}
    queue = [str(node_id)]

    while queue:
        current = queue.pop(0)
        entry = prompt.get(current)
        if not isinstance(entry, dict):
            continue
        inputs = entry.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for value in inputs.values():
            # A link is serialised as [origin_node_id, origin_slot].
            if isinstance(value, list) and len(value) >= 1:
                origin = str(value[0])
                if origin not in seen:
                    seen.add(origin)
                    order.append(origin)
                    queue.append(origin)
    return order


def _text_behind_link(prompt, link):
    """Follow a conditioning link back to the nearest node carrying text."""
    seen = set()
    queue = [link]
    while queue:
        ref = queue.pop(0)
        if not (isinstance(ref, list) and ref):
            continue
        nid = str(ref[0])
        if nid in seen:
            continue
        seen.add(nid)

        entry = prompt.get(nid)
        if not isinstance(entry, dict):
            continue
        inputs = entry.get("inputs")
        if not isinstance(inputs, dict):
            continue

        text = inputs.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        # DRILO MultiPrompt computes its text from a JSON array plus an index, so it
        # is not visible anywhere in the prompt dict. Ask the node instead.
        if str(entry.get("class_type", "")) == "DRILO_MultiPrompt":
            from .multiprompt import LAST_PROMPT

            emitted = LAST_PROMPT.get(nid)
            if isinstance(emitted, str) and emitted.strip():
                return emitted.strip()

        for value in inputs.values():
            if isinstance(value, list):
                queue.append(value)
    return None


def find_generation_info(prompt, node_id):
    """Seed and positive prompt of the closest upstream sampler.

    Both are best effort: a missing or malformed prompt gives None rather than
    breaking the run.
    """
    empty = {"seed": None, "prompt_text": None}
    if not isinstance(prompt, dict) or node_id is None:
        return empty

    try:
        candidates = _upstream_node_ids(prompt, node_id)
    except Exception:  # noqa: BLE001 - a malformed prompt must never break a run
        return empty

    fallback = None
    for nid in candidates:
        entry = prompt.get(nid)
        if not isinstance(entry, dict):
            continue
        inputs = entry.get("inputs")
        if not isinstance(inputs, dict):
            continue

        seed = inputs.get("seed", inputs.get("noise_seed"))
        if not isinstance(seed, (int, float)) or isinstance(seed, bool):
            continue

        class_type = str(entry.get("class_type", ""))
        if "KSampler" in class_type or "Sampler" in class_type:
            return {
                "seed": int(seed),
                "prompt_text": _text_behind_link(prompt, inputs.get("positive")),
            }
        if fallback is None:
            fallback = int(seed)

    return {"seed": fallback, "prompt_text": None}


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class DriloAudioMultiExport:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "output_folder": (
                    "STRING",
                    {"default": "audio", "tooltip": "Folder inside output/. Empty = the output/ root."},
                ),
                "filename": (
                    "STRING",
                    {"default": "pick", "tooltip": "Base filename. ComfyUI appends _00001_ and the extension."},
                ),
                "format": (SUPPORTED_FORMATS, {"default": "flac"}),
                "accumulate": ("BOOLEAN", {"default": True}),
                # Appended last on purpose: widgets_values is positional, so a
                # new widget at the end leaves older saved workflows aligned.
                "resolve_bin": (
                    "STRING",
                    {"default": "sfx", "tooltip": "DaVinci Resolve bin the -> Resolve button imports into."},
                ),
                "resolve_placement": (
                    ["bin", "playhead", "end"],
                    {
                        "default": "bin",
                        "tooltip": "bin = import only. playhead = drop it at the playhead. end = append to the timeline.",
                    },
                ),
                "resolve_audio_track": (
                    "INT",
                    {
                        "default": 1,
                        "min": 1,
                        "max": 24,
                        "tooltip": "Target audio track, created if missing. Note: playhead placement needs that spot free.",
                    },
                ),
                "normalize": (
                    NORMALIZE_MODES,
                    {
                        "default": "off",
                        "tooltip": "Normalises on save, not the preview. peak/rms use dBFS; lufs uses integrated BS.1770 LUFS.",
                    },
                ),
                "normalize_target_db": (
                    "FLOAT",
                    {
                        "default": -1.0,
                        "min": -70.0,
                        "max": 0.0,
                        "step": 0.5,
                        "tooltip": "Target: dBFS for peak/rms, LUFS for lufs (-14 streaming, -23 broadcast).",
                    },
                ),
            },
            "hidden": {
                "node_id": "UNIQUE_ID",
                "prompt": "PROMPT",
            },
        }

    RETURN_TYPES = ("AUDIO",)
    RETURN_NAMES = ("audio",)
    FUNCTION = "pick"
    CATEGORY = "audio"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Shows every item of an audio batch as a row with its own player and a "
        "checkbox that writes that one sample to output/."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Never cache: every Run of a batch must append a new row.
        return float("NaN")

    def pick(
        self,
        audio,
        output_folder,
        filename,
        format,
        accumulate,
        resolve_bin="sfx",
        resolve_placement="bin",
        resolve_audio_track=1,
        normalize="off",
        normalize_target_db=-1.0,
        node_id=None,
        prompt=None,
    ):
        filename_prefix = compose_prefix(output_folder, filename)
        waveform = audio["waveform"]
        sample_rate = int(audio["sample_rate"])

        if waveform.dim() == 2:
            waveform = waveform.unsqueeze(0)

        temp_dir = folder_paths.get_temp_directory()
        os.makedirs(temp_dir, exist_ok=True)

        info = find_generation_info(prompt, node_id)
        seed = info["seed"]
        prompt_text = info["prompt_text"]
        key = str(node_id)

        new_samples = []
        for index in range(waveform.shape[0]):
            sample = _to_2d(waveform[index])
            uid = uuid.uuid4().hex
            # Not `filename`: that name is a widget parameter now.
            preview_name = "audio_picker_{}_.{}".format(uid, PREVIEW_FORMAT)
            path = os.path.join(temp_dir, preview_name)

            encode_audio(sample, sample_rate, path, PREVIEW_FORMAT)

            new_samples.append(
                {
                    "uid": uid,
                    "filename": preview_name,
                    "subfolder": "",
                    "type": "temp",
                    "sample_rate": sample_rate,
                    "duration": round(sample.shape[-1] / float(sample_rate), 3),
                    "seed": seed,
                    "prompt_text": prompt_text,
                    "channels": int(sample.shape[0]),
                    "format": format,
                    "filename_prefix": filename_prefix,
                    "resolve_bin": resolve_bin,
                    "resolve_placement": resolve_placement,
                    "resolve_audio_track": int(resolve_audio_track),
                    "normalize": normalize,
                    "normalize_target_db": float(normalize_target_db),
                }
            )

        if accumulate:
            BUFFERS.setdefault(key, []).extend(new_samples)
        else:
            # Dropping the old rows also drops their save bookkeeping.
            for old in BUFFERS.get(key, []):
                SAVED.pop(_saved_key(key, old["uid"]), None)
                SENT_TO_RESOLVE.pop(_saved_key(key, old["uid"]), None)
            BUFFERS[key] = list(new_samples)

        buffered = BUFFERS[key]
        for position, sample in enumerate(buffered):
            sample["index"] = position
            # Prefix and format can change between runs; the rows follow the widgets.
            sample["filename_prefix"] = filename_prefix
            sample["format"] = format
            sample["resolve_bin"] = resolve_bin
            sample["resolve_placement"] = resolve_placement
            sample["resolve_audio_track"] = int(resolve_audio_track)
            sample["normalize"] = normalize
            sample["normalize_target_db"] = float(normalize_target_db)
            absolute = SAVED.get(_saved_key(key, sample["uid"]))
            sample["saved_path"] = display_path(absolute)
            sample["saved"] = absolute is not None
            sample["sent_to_resolve"] = _saved_key(key, sample["uid"]) in SENT_TO_RESOLVE

        return {
            "ui": {"audio_picker": list(buffered)},
            "result": (audio,),
        }


NODE_CLASS_MAPPINGS = {"DRILO_AudioMultiExport": DriloAudioMultiExport}
NODE_DISPLAY_NAME_MAPPINGS = {"DRILO_AudioMultiExport": "🐊 DRILO AudioMultiExport"}
