import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAME = "DRILO_AudioMultiExport";

const HEADER_HEIGHT = 30;
const ROW_HEIGHT = 38;
const MAX_VISIBLE_ROWS = 8;
const WIDGET_PADDING = 12;

const STATE_PROP = "audio_picker_state";
const ROWS_PROP = "audio_picker_rows";

function viewURL(sample) {
    const params = new URLSearchParams({
        filename: sample.filename ?? "",
        subfolder: sample.subfolder ?? "",
        type: sample.type ?? "temp",
    });
    const path = `/view?${params.toString()}`;
    return api.apiURL ? api.apiURL(path) : path;
}

async function post(endpoint, body) {
    const response = await api.fetchApi(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    let data = {};
    try {
        data = await response.json();
    } catch (e) {
        data = {};
    }
    if (!response.ok) {
        const error = new Error(data.error || `HTTP ${response.status}`);
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return data;
}

function formatSeed(seed) {
    if (seed === null || seed === undefined) return "seed: -";
    return `seed: ${seed}`;
}

function formatDuration(seconds) {
    if (typeof seconds !== "number" || !isFinite(seconds)) return "—";
    return `${seconds.toFixed(1)}s`;
}

// ---------------------------------------------------------------------------
// Styles (injected once)
// ---------------------------------------------------------------------------

const STYLE_ID = "audio-picker-styles";
if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
.audio-picker { display: flex; flex-direction: column; gap: 4px; font-size: 11px;
    font-family: sans-serif; color: var(--descrip-text, #b0b0b0); width: 100%;
    height: 100%; max-width: 100%; min-width: 0; box-sizing: border-box;
    overflow: hidden; }
.audio-picker-header { display: flex; align-items: center; justify-content: space-between;
    gap: 8px; flex: 0 0 auto; height: 22px; }
.audio-picker-count { opacity: 0.8; white-space: nowrap; }
.audio-picker-clear { background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #444); border-radius: 4px; padding: 2px 8px;
    font-size: 11px; cursor: pointer; }
.audio-picker-clear:hover { filter: brightness(1.3); }
.audio-picker-list { flex: 1 1 auto; overflow-y: auto; overflow-x: hidden;
    display: flex; flex-direction: column; gap: 3px; padding-right: 2px;
    min-width: 0; }
.audio-picker-empty { opacity: 0.6; font-style: italic; padding: 6px 2px; }
/* Every flexible child below carries min-width:0. Without it a flex item will
   not shrink past its content width, and the row spills outside the node
   border (the node body is canvas-painted, so nothing clips the overflow). */
.audio-picker-row { display: flex; align-items: center; gap: 6px;
    background: var(--comfy-input-bg, #222); border: 1px solid var(--border-color, #383838);
    border-radius: 4px; padding: 3px 6px; min-height: 30px; box-sizing: border-box;
    min-width: 0; width: 100%; }
.audio-picker-row.saved { border-color: #4c8f4c; }
.audio-picker-row.expired { opacity: 0.5; border-style: dashed; }
.audio-picker-row input[type="checkbox"] { flex: 0 0 auto; margin: 0; cursor: pointer; }
.audio-picker-index { flex: 0 0 auto; width: 26px; opacity: 0.7; }
.audio-picker-seed { flex: 0 1 auto; min-width: 0; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
.audio-picker-dur { flex: 0 0 auto; width: 36px; text-align: right; }
.audio-picker-row audio { flex: 1 1 auto; min-width: 0; width: 0; height: 26px; }
.audio-picker-status { flex: 0 1 auto; min-width: 0; max-width: 150px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: 10px; opacity: 0.85; }
.audio-picker-status.ok { color: #7fc97f; }
.audio-picker-status.err { color: #e08585; }
.audio-picker-status.warn { color: #d8b45c; }
.audio-picker-resolve { flex: 0 0 auto; background: var(--comfy-input-bg, #222);
    color: var(--input-text, #ddd); border: 1px solid var(--border-color, #444);
    border-radius: 4px; padding: 2px 6px; font-size: 10px; cursor: pointer;
    white-space: nowrap; }
.audio-picker-resolve:hover:not(:disabled) { filter: brightness(1.35); }
.audio-picker-resolve:disabled { opacity: 0.35; cursor: default; }
.audio-picker-resolve.sent { border-color: #4c7f9f; color: #8fc4e0; }
`;
    document.head.appendChild(style);
}

// ---------------------------------------------------------------------------
// Widget
// ---------------------------------------------------------------------------

function getState(node) {
    if (!node.properties[STATE_PROP] || typeof node.properties[STATE_PROP] !== "object") {
        node.properties[STATE_PROP] = {};
    }
    return node.properties[STATE_PROP];
}

function getRows(node) {
    const rows = node.properties[ROWS_PROP];
    return Array.isArray(rows) ? rows : [];
}

function setRows(node, rows) {
    node.properties[ROWS_PROP] = rows;
}

function sampleKey(sample, index) {
    return sample.uid ?? `idx${index}`;
}

// LiteGraph insets widgets from the node edges by roughly this much per side.
const NODE_SIDE_INSET = 26;

function clampToNode(node, container) {
    if (!container) return;
    // See multiprompt.js: a no-op when the widget host is already node-sized,
    // and the thing that keeps rows inside the border when it is not.
    container.style.maxWidth = `${Math.max(80, node.size[0] - NODE_SIDE_INSET)}px`;
}

function widgetValue(node) {
    const rows = getRows(node);
    const visible = Math.min(Math.max(rows.length, 1), MAX_VISIBLE_ROWS);
    return HEADER_HEIGHT + visible * ROW_HEIGHT + WIDGET_PADDING;
}

function resizeNode(node) {
    if (!node.audioPickerWidget) return;
    node.audioPickerWidget.computedHeight = widgetValue(node);
    const size = node.computeSize();
    node.setSize([Math.max(node.size[0], size[0], 420), Math.max(size[1], 120)]);
    node.setDirtyCanvas(true, true);
}

function buildRow(node, sample, index) {
    const state = getState(node);
    const key = sampleKey(sample, index);
    const entry = state[key] || (state[key] = { checked: false, saved_path: null });

    const row = document.createElement("div");
    row.className = "audio-picker-row";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = !!entry.checked;
    checkbox.title = "Save this sample to output/";

    const idx = document.createElement("span");
    idx.className = "audio-picker-index";
    idx.textContent = `#${index}`;

    const seed = document.createElement("span");
    seed.className = "audio-picker-seed";
    seed.textContent = formatSeed(sample.seed);
    seed.title = `${formatSeed(sample.seed)} · ${sample.sample_rate ?? "?"} Hz`;

    const dur = document.createElement("span");
    dur.className = "audio-picker-dur";
    dur.textContent = formatDuration(sample.duration);

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = viewURL(sample);

    const resolveBtn = document.createElement("button");
    resolveBtn.className = "audio-picker-resolve";
    resolveBtn.textContent = "→ Resolve";
    resolveBtn.disabled = !entry.saved_path;
    resolveBtn.title = entry.saved_path
        ? "Import into the DaVinci Resolve media pool"
        : "Tick the checkbox first: only saved files can be sent";
    if (entry.sent_to_resolve) {
        resolveBtn.classList.add("sent");
        resolveBtn.textContent = "✓ Resolve";
    }

    const status = document.createElement("span");
    status.className = "audio-picker-status";
    if (entry.saved_path) {
        status.textContent = entry.saved_path;
        status.title = entry.saved_path;
        status.classList.add("ok");
        row.classList.add("saved");
    }

    const setStatus = (text, kind) => {
        status.textContent = text;
        status.title = text;
        status.classList.remove("ok", "err", "warn");
        if (kind) status.classList.add(kind);
    };

    resolveBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        resolveBtn.disabled = true;
        const previous = status.textContent;
        setStatus("sending to Resolve...");
        try {
            const data = await post("/audio_picker/to_resolve", {
                node_id: String(node.id),
                index,
                uid: sample.uid,
                bin: sample.resolve_bin,
                placement: sample.resolve_placement,
                track_index: sample.resolve_audio_track,
            });
            entry.sent_to_resolve = true;
            resolveBtn.classList.add("sent");
            resolveBtn.textContent = "✓ Resolve";
            let where = `${data.bin} · ${data.clip_name}`;
            if (data.placed) where += ` → ${data.placed.where}`;
            setStatus(where, "ok");
            if (data.warnings?.length) setStatus(`${where} — ${data.warnings.join("; ")}`, "warn");
        } catch (err) {
            setStatus(err.message, "err");
            // Offline Resolve is a transient condition, so allow a retry.
            resolveBtn.disabled = false;
            return;
        }
        resolveBtn.disabled = false;
        void previous;
    });

    let expired = false;
    const markExpired = () => {
        if (expired) return;
        expired = true;
        row.classList.add("expired");
        checkbox.disabled = true;
        // A restart wipes the save bookkeeping too, so there is nothing to send.
        resolveBtn.disabled = true;
        status.textContent = "preview expired";
        status.title = "The temp file is gone (ComfyUI was restarted).";
        status.classList.add("err");
        status.classList.remove("ok");
    };
    audio.addEventListener("error", markExpired);

    checkbox.addEventListener("change", async () => {
        const wanted = checkbox.checked;
        checkbox.disabled = true;
        status.classList.remove("ok", "err");
        status.textContent = wanted ? "saving..." : "deleting...";

        const body = {
            filename: sample.filename,
            subfolder: sample.subfolder ?? "",
            node_id: String(node.id),
            index,
            uid: sample.uid,
            filename_prefix: sample.filename_prefix ?? "audio/pick",
            format: sample.format ?? "flac",
        };

        try {
            if (wanted) {
                const data = await post("/audio_picker/save", body);
                entry.checked = true;
                entry.saved_path = data.saved_path;
                const n = data.normalize;
                if (n && n.mode !== "off") {
                    const bits = [];
                    if (n.measured !== null && n.measured !== undefined) {
                        bits.push(`${n.measured}${n.mode === "lufs" ? " LUFS" : " dB"}`);
                    }
                    bits.push(`${n.gain_db >= 0 ? "+" : ""}${n.gain_db} dB`);
                    setStatus(`${data.saved_path}  (${bits.join(" → ")})`, n.note ? "warn" : "ok");
                    if (n.note) status.title = `${data.saved_path}\n${n.note}`;
                } else {
                    setStatus(data.saved_path, "ok");
                }
                row.classList.add("saved");
                resolveBtn.disabled = false;
                resolveBtn.title = "Import into the DaVinci Resolve media pool";
            } else {
                const data = await post("/audio_picker/unsave", body);
                entry.checked = false;
                entry.saved_path = null;
                entry.sent_to_resolve = false;
                row.classList.remove("saved");
                resolveBtn.disabled = true;
                resolveBtn.classList.remove("sent");
                resolveBtn.textContent = "→ Resolve";
                // Deleting the file orphans any clip already in the media pool.
                if (data.warning) setStatus(data.warning, "warn");
                else setStatus("");
            }
        } catch (err) {
            checkbox.checked = !wanted;
            entry.checked = !wanted;
            setStatus(err.message, "err");
            if (err.status === 404) markExpired();
        } finally {
            if (!expired) checkbox.disabled = false;
        }
    });

    row.append(checkbox, idx, seed, dur, audio, resolveBtn, status);
    return row;
}

function repaint(node) {
    const container = node.audioPickerContainer;
    if (!container) return;

    const rows = getRows(node);
    const list = container.querySelector(".audio-picker-list");
    const count = container.querySelector(".audio-picker-count");

    count.textContent = rows.length === 1 ? "1 sample" : `${rows.length} samples`;

    clampToNode(node, container);

    // Explicit pixel cap rather than relying on `flex: 1` inside a host whose
    // height ComfyUI controls: if that height is ever indefinite, height:100%
    // collapses and the list grows instead of scrolling.
    list.style.maxHeight = `${MAX_VISIBLE_ROWS * ROW_HEIGHT}px`;

    list.replaceChildren();

    if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "audio-picker-empty";
        empty.textContent = "No samples yet. Run the workflow.";
        list.appendChild(empty);
    } else {
        rows.forEach((sample, index) => list.appendChild(buildRow(node, sample, index)));
    }

    resizeNode(node);
}

function createWidget(node) {
    const container = document.createElement("div");
    container.className = "audio-picker";

    const header = document.createElement("div");
    header.className = "audio-picker-header";

    const count = document.createElement("span");
    count.className = "audio-picker-count";
    count.textContent = "0 samples";

    const clear = document.createElement("button");
    clear.className = "audio-picker-clear";
    clear.textContent = "Clear list";
    clear.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        clear.disabled = true;
        try {
            await post("/audio_picker/clear", { node_id: String(node.id) });
        } catch (err) {
            console.error("[audio-picker] clear:", err);
        } finally {
            clear.disabled = false;
        }
        setRows(node, []);
        node.properties[STATE_PROP] = {};
        repaint(node);
    });

    header.append(count, clear);

    const list = document.createElement("div");
    list.className = "audio-picker-list";

    container.append(header, list);

    node.audioPickerContainer = container;
    node.audioPickerWidget = node.addDOMWidget("audio_picker_ui", "audio_picker", container, {
        serialize: false,
        hideOnZoom: false,
        getValue: () => getRows(node).length,
        setValue: () => {},
    });
    node.audioPickerWidget.computeSize = function (width) {
        return [width, widgetValue(node)];
    };
    node.audioPickerWidget.computedHeight = widgetValue(node);
}

// ---------------------------------------------------------------------------
// Extension
// ---------------------------------------------------------------------------

app.registerExtension({
    name: "comfyui.audio.picker",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);

            this.properties = this.properties || {};
            if (!this.properties[STATE_PROP]) this.properties[STATE_PROP] = {};
            if (!this.properties[ROWS_PROP]) this.properties[ROWS_PROP] = [];

            createWidget(this);
            // Synchronously: a deferred paint never runs while the tab is
            // hidden (rAF is throttled to zero), leaving the node blank.
            repaint(this);
            return result;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = onExecuted?.apply(this, arguments);

            const samples = message?.audio_picker;
            if (Array.isArray(samples)) {
                setRows(this, samples);

                // Drop state for samples that are no longer in the list
                // (accumulate=False, or "Clear list" on the server side).
                const alive = new Set(samples.map((s, i) => sampleKey(s, i)));
                const state = getState(this);
                for (const key of Object.keys(state)) {
                    if (!alive.has(key)) delete state[key];
                }
                // The server is the source of truth for what is already on disk.
                samples.forEach((sample, index) => {
                    const key = sampleKey(sample, index);
                    if (sample.saved) {
                        state[key] = {
                            checked: true,
                            saved_path: sample.saved_path,
                            sent_to_resolve: !!sample.sent_to_resolve,
                        };
                    } else if (!state[key]) {
                        state[key] = { checked: false, saved_path: null, sent_to_resolve: false };
                    }
                });

                repaint(this);
            }
            return result;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            repaint(this);
            return result;
        };

        const onResize = nodeType.prototype.onResize;
        nodeType.prototype.onResize = function () {
            const result = onResize?.apply(this, arguments);
            clampToNode(this, this.audioPickerContainer);
            return result;
        };

        const onCollapse = nodeType.prototype.collapse;
        nodeType.prototype.collapse = function () {
            const result = onCollapse?.apply(this, arguments);
            // Rows live in properties, so expanding just repaints them.
            if (!this.flags?.collapsed) repaint(this);
            return result;
        };
    },
});
