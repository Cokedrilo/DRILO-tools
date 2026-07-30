# DRILO-tools

Two ComfyUI nodes for working with generated audio and with prompts.

**🐊 DRILO AudioMultiExport** turns an audio batch into a list of rows, each with
its own player and a checkbox. Tick a box and that one sample is written to
`output/`. Untick it and the file is deleted. The rest of the batch never
clutters the folder. Optionally normalises on save and pushes takes straight
into the DaVinci Resolve media pool, tagged with the seed and the prompt that
produced them.

**🐊 DRILO MultiPrompt** gives you several text boxes and emits the next one on
every Run, so you can compare prompts against a fixed seed.

*[Documentación en español](README.es.md)*

---

## Install

Clone (or copy) the folder into your ComfyUI `custom_nodes/`:

```bash
git clone https://github.com/Cokedrilo/DRILO-tools.git ComfyUI/custom_nodes/DRILO-tools
```

Restart ComfyUI. There is no `pip install` step: the nodes only use what
ComfyUI already ships (`torch`, `torchaudio`, `aiohttp`, `folder_paths`,
`server.PromptServer`, and PyAV).

The nodes show up as **audio → 🐊 DRILO AudioMultiExport** and
**utils → 🐊 DRILO MultiPrompt**. Their internal identifiers, the ones stored in
workflow JSON, are `DRILO_AudioMultiExport` and `DRILO_MultiPrompt`.

> **Note on PyAV.** From torchaudio 2.9 onwards, `torchaudio.load` and
> `torchaudio.save` both require the separate `torchcodec` package, which the
> ComfyUI portable build does not include. Every read and write tries torchaudio
> first and falls back to PyAV, which ComfyUI does ship. In practice PyAV does
> all of the encoding work on a portable install.

---

## 🐊 DRILO AudioMultiExport

### Inputs

| Input | Type | Default | What it does |
| --- | --- | --- | --- |
| `audio` | `AUDIO` | — | The batch to audition. Works with `B=1` and `B>1`. |
| `output_folder` | `STRING` | `audio` | Folder inside `output/`. Empty = the `output/` root. Nested paths work (`audio/takes/good`). |
| `filename` | `STRING` | `pick` | Base filename. ComfyUI appends `_00001_` and the extension. |
| `format` | combo | `flac` | `flac`, `mp3` or `opus`. Only affects what gets saved. |
| `accumulate` | `BOOLEAN` | `True` | Stack each run's samples instead of replacing the list. |
| `resolve_bin` | `STRING` | `sfx` | Resolve bin the `→ Resolve` button imports into. Nested bins work (`sfx/comfyui`). |
| `resolve_placement` | combo | `bin` | `bin` = import only. `playhead` = drop it at the playhead. `end` = append to the timeline. |
| `resolve_audio_track` | `INT` | `1` | Target audio track, created if missing. |
| `normalize` | combo | `off` | `peak` / `rms` in dBFS, `lufs` = gated integrated BS.1770-4 LUFS. |
| `normalize_target_db` | `FLOAT` | `-1.0` | Target: dBFS for peak/rms, LUFS for lufs. |

Folder and filename are separate fields for convenience; internally they are
joined into the single `filename_prefix` that
`folder_paths.get_save_image_path` expects. `audio`, `audio/`, `/audio` and
`audio\` all behave the same. An empty `filename` falls back to `pick`.

There is an `AUDIO` output, so the node can sit inline without breaking the
graph.

### Usage

Wire your audio generator into the node and hit **Run** with a *Batch count* of
4. After execution the node looks like this:

```
┌─ 🐊 DRILO AudioMultiExport ────────────────────────────────────────────┐
│ audio           ●                                                       │
│ output_folder    [ audio                                         ]      │
│ filename         [ pick                                          ]      │
│ format           [ flac                                        ▾ ]      │
│ accumulate       [ ✔ ]                                                  │
│ resolve_bin      [ sfx                                           ]      │
│                                                                         │
│ 4 samples                                              [ Clear list ]   │
│ ┌─────────────────────────────────────────────────────────────────────┐ │
│ │ ☐ #0  seed: 884120391  6.0s  ▶ ────●──── [→ Resolve]               │ │
│ │ ☑ #1  seed: 884120392  6.0s  ▶ ───────── [✓ Resolve] sfx · pick_00001
│ │ ☐ #2  seed: 884120393  6.0s  ▶ ───────── [→ Resolve]               │ │
│ │ ☐ #3  seed: 884120394  6.0s  ▶ ───────── [→ Resolve]               │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│                                                       audio ●           │
└─────────────────────────────────────────────────────────────────────────┘
```

You audition all four. #1 is the keeper: tick its checkbox, the row shows
`saving...` for a moment and then turns green with the final path
`audio/pick_00001_.flac`. Change your mind and unticking deletes it from
`output/`.

Hit **Run** again. With `accumulate` on, the four new samples stack underneath
(`#4`…`#7`) instead of replacing the list, and #1 stays ticked. Past 8 rows the
node stops growing and the list scrolls. **Clear list** empties the view without
touching anything you already saved.

### Behaviour details

- **Previews live in `temp/`.** Each sample is written there as FLAC under a
  unique name. That is not the final file; the final file only appears when you
  tick the checkbox.
- **Numbering never overwrites.** Saving goes through
  `folder_paths.get_save_image_path`, honours the prefix and takes the first
  free slot (`_00001_`, `_00002_`, …).
- **Re-encode on demand.** Choosing `mp3` or `opus` re-encodes on save. With
  `flac` it is a straight copy.
- **Seed and prompt.** Both are found by walking the prompt graph upstream to
  the nearest sampler. If there is none, the row shows `seed: -` and nothing
  breaks.
- **No caching.** `IS_CHANGED` returns `NaN`, so the node re-runs on every Run of
  a batch instead of reusing the previous result.
- **Expired previews.** `temp/` is cleared when ComfyUI restarts. Reloading a
  saved workflow marks rows whose audio is gone as `preview expired` and
  disables their checkbox, rather than breaking the widget.
- **State survives.** Checkboxes and saved paths live in the node's properties,
  so they survive repaints and collapse/expand.

---

## Sending to DaVinci Resolve

Every row has a `→ Resolve` button that imports the file into the media pool,
into the bin named in `resolve_bin`, and tags it:

| Resolve field | Content |
| --- | --- |
| Clip Name | `pick_00003 - seed 70408609449185` |
| Description | the positive prompt from the upstream sampler (trimmed to 300 chars) |
| Comments | `seed … \| flac \| 19.969s \| 44100 Hz \| ComfyUI DRILO AudioMultiExport` |
| Keywords | `comfyui, drilo, audiomultiexport` |

That traceability is the point: a bin holding 40 generated sfx is otherwise
indistinguishable.

**The button stays disabled until you tick the checkbox.** Not a UI whim: the
media pool references files by path, so importing a `temp/` preview would leave
a broken clip in the bin as soon as ComfyUI restarted. Only files already in
`output/` are sent.

For the same reason, **unticking a row you already sent warns in amber**: the
file is deleted but the clip stays in the media pool pointing at nothing. It is
not removed automatically because you may already have it on a timeline, and
pulling a clip out from under you is worse than a warning.

### Timeline placement

With `resolve_placement = playhead` the clip goes to track
`resolve_audio_track` at the playhead position. With `end` it is appended. The
track is created if it does not exist.

**Careful: `playhead` only works if that stretch of the track is free.** Measured
against Resolve 21, when `recordFrame` lands on existing track material
`AppendToTimeline` does two different unhelpful things: in one case it placed
nothing at all and **returned a truthy item whose `GetStart()` echoed the
requested frame**; in another it placed a 20 s clip **truncated to 5 s**.

So the bridge does not trust the return value. It snapshots the track before and
after the append and compares. If nothing appeared, it warns in amber that the
clip is in the bin but not on the timeline. If it landed truncated, it says so
with the real frame counts. The position shown is always the real one, read back
from the track, never the requested one.

If the track is busy, use `end` or point at an empty track.

Timecode-to-frame conversion is verified at 24 fps non-drop (exact round trip,
and one hour of drop-frame gives the canonical 107892 frames). The drop-frame
branch (29.97/59.94) is **untested against a real timeline**.

### Requirements and fragility

Resolve must be **open**, with a project loaded, and *Preferences > System >
General > External scripting using* set to **Local**. Otherwise the row reports
why and the rest of the node keeps working normally.

The delicate part: `fusionscript` is a C extension, so it only loads into the
Python version it was built for. Verified working: **Resolve 21.0.3 with Python
3.13** (what the ComfyUI portable build ships). The **system Python 3.11 crashes
the process** with an access violation. If you update Resolve or ComfyUI changes
its Python, this may stop loading — which is why the bridge reports import
failures as text instead of letting them propagate.

---

## Normalisation

Applied **on save**, not to the preview: the row plays the raw audio and the
file in `output/` is the normalised one. The row shows what was measured and
what gain was applied, e.g. `(-19.19 LUFS → -3.81 dB)`.

The LUFS meter is BS.1770-4 with K-weighting and both gates (absolute at
-70 LUFS, relative at -10 LU). Calibrated against the standard's reference: a
1 kHz sine at -20 dBFS in a single channel of a stereo pair measures
**-23.004 LUFS** (the standard says -23.000). The filter coefficients are only
valid at 48 kHz, so measurement resamples there; the gain is applied to the
original audio.

Two guards that matter:

- **It never clips.** If the target needs more gain than there is headroom, the
  gain is capped so the peak lands at -0.1 dBFS and the row warns in amber.
- **It does not amplify silence.** Below -60 dB nothing is touched. Without that
  floor, normalising a silent clip asked for +239 dB.

In practice, with generated audio that already peaks near full scale (-0.5 dBFS
is typical), asking for `-14 LUFS` does almost nothing because there is no room:
the gain gets capped to about +0.4 dB. For that material `-23 LUFS` genuinely
attenuates, and `peak` at -3 dBFS is the most predictable way to get uniform
headroom across a reel.

---

## 🐊 DRILO MultiPrompt

Several text boxes; each Run emits the next one.

```
┌─ 🐊 DRILO MultiPrompt ───────────────────┐
│ index            [ 3 ]                   │
│ control_after_g. [ increment          ▾ ] │
│ skip_empty       [ ✔ ]                   │
│                                          │
│ 3 boxes                        [ − ] [ + ]│
│  1 ┌────────────────────────────────┐ ×  │
│    │ deep sub bass drone, slow      │     │
│    │ pulse, dark cinematic          │     │
│  2 ├────────────────────────────────┤ ×  │  ← green border = the box that fired
│    │ bright glass bells, sparkling  │     │
│  3 ├────────────────────────────────┤ ×  │
│    │ gritty analog saw lead         │     │
│    └────────────────────────────────┘     │
│ index 3 -> box 1 - 3 with text           │
│         prompt ● box_index ● info ●      │
└──────────────────────────────────────────┘
```

| Input | Default | What it does |
| --- | --- | --- |
| `index` | `0` | Which box fires. With `control_after_generate` on `increment` it advances on every Run. |
| `skip_empty` | `True` | Skip empty boxes instead of emitting empty text. |

Outputs: `prompt` (STRING, into the CLIPTextEncode `text` input), `box_index`
(INT) and `info` (STRING, like `box 2/3`).

### How to use it

Wire `prompt` into your CLIPTextEncode `text` (convert the widget to an input),
set `index` to `increment`, set the KSampler seed to `fixed`, and run with
*Batch count* = number of prompts. Each take uses a different prompt over the
same starting noise.

Verified: 3 boxes and 4 generations produced one seed (12345), four indices
(0-3), and **three unique audio files out of four** — takes 1 and 4 shared a box
and came out byte-identical. That confirms both halves at once: the prompt
changes the result, and the seed did not move.

### Details

`+` and `−` add and remove boxes (1 to 32), and the `×` on each row deletes that
specific box. Past 5 boxes the node stops growing and the list scrolls.

The text does not live in one widget per box: it is stored as a JSON array in a
`prompts_json` widget that is hidden on the canvas. That is deliberate —
ComfyUI's `widgets_values` are positional, so adding and removing real widgets
would misalign any saved workflow. With a single field, changing the box count
breaks nothing. If that JSON ever arrives corrupt, its contents are treated as a
single box rather than discarding what you wrote.

**The two nodes talk to each other.** The active prompt appears nowhere in
ComfyUI's prompt graph, because it is computed from the array and the index, so
AudioMultiExport's prompt tracking asks the node directly. The result is that a
saved sample reaches Resolve with the real prompt that generated it in its
Description field, instead of empty.

---

## Why the widget content is pinned to the node by hand

The host element ComfyUI hands a DOM widget **is wider than the node** (measured:
a 1272 px host for a 320 px node). A `width: 100%` resolves against that host,
not against the node, and the content spills out to the right: the node body is
painted on the canvas and the widget is a DOM layer on top, so nothing clips the
overflow.

So every repaint pins `max-width` in pixels from `node.size[0]` minus the side
inset, and `onResize` recomputes it when you drag the corner. It is in graph
units, so it holds at any zoom. If ComfyUI ever sizes the host to the node, the
clamp is harmless: `width: 100%` will be smaller and win.

On top of that, rows use CSS grid with `minmax(0, 1fr)` instead of flex. A flex
child defaults to `min-width: auto` and refuses to shrink below its content
width; ComfyUI's own textarea carries `min-width: 0` for exactly that reason.
Both pieces are needed: the clamp sets the available width and the grid
distributes inside it without any column being able to push.

---

## The emoji and the folder name

The 🐊 goes in the nodes' **display names**, not in their internal identifiers,
which stay `DRILO_AudioMultiExport` and `DRILO_MultiPrompt`. An emoji in the
identifier would end up in every saved workflow's JSON and in node search for no
benefit.

The pack badge ComfyUI draws above the node (`#60 DRILO-tools`) comes from
`python_module`, i.e. **from the folder name**. A `pyproject.toml` with
`[tool.comfy] DisplayName` does not change it in the current frontend.

Renaming the folder to `🐊 DRILO-tools` **does not work**: tested, Python loads
fine and the nodes register, but ComfyUI publishes the web dir as
`/extensions/%F0%9F%90%8A%20DRILO-tools/…` and that path 404s under every
encoding tried (raw, single, double, `+` for space). The result is that both
`.js` files fail to load and the nodes lose their widgets. Hence the folder stays
`DRILO-tools`.

---

## HTTP endpoints

All POST with a JSON body:

| Endpoint | Body | Response |
| --- | --- | --- |
| `/audio_picker/save` | `filename`, `subfolder`, `node_id`, `index`, `uid`, `filename_prefix`, `format` | `{saved_path, normalize}` |
| `/audio_picker/unsave` | `node_id`, `index`, `uid` | `{ok, removed, warning}` |
| `/audio_picker/clear` | `node_id` | `{ok, cleared}` |
| `/audio_picker/to_resolve` | `node_id`, `index`, `uid`, `bin`, `placement`, `track_index` | `{clip_name, bin, project, metadata_applied, placed, warnings}` |

Plus `GET /audio_picker/resolve_status`, which returns
`{available, version, project}` or `{available: false, error}`.

The endpoint paths keep an older internal name; renaming them would invalidate
the widget state already serialised inside saved workflows.

### Path safety

`save` refuses any `filename`/`subfolder` that resolves outside `temp/`, and
`unsave` only deletes when the recorded path is inside `output/`. Prefix
traversal (`..`) is rejected by ComfyUI's own `get_save_image_path`, and the
error is surfaced on the row.

---

## Known limitations

- The record of what has been saved lives in server memory. After a ComfyUI
  restart, unticking a row can no longer delete its file. In practice this is
  masked: after a restart the `temp/` preview is gone too, so the row shows as
  expired with its checkbox disabled.
- Resolve clips are never removed automatically, only warned about.
- The drop-frame timecode branch is unverified against a real timeline.
- Loudness channel weighting covers mono and stereo only; no surround handling.

## License

MIT.
