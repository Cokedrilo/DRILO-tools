import { app } from "../../scripts/app.js";

const NODE_NAME = "DRILO_MultiPrompt";

const HEADER_HEIGHT = 26;
const BOX_HEIGHT = 62;
const MAX_VISIBLE = 5;
const MAX_BOXES = 32;
const PADDING = 10;

const ACTIVE_PROP = "multiprompt_active_box";

const STYLE_ID = "multiprompt-styles";
if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
.mp-root { display: flex; flex-direction: column; gap: 4px; width: 100%; height: 100%;
    max-width: 100%; min-width: 0; box-sizing: border-box; overflow: hidden;
    font-family: sans-serif; font-size: 11px; color: var(--descrip-text, #b0b0b0); }
.mp-header { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; height: 20px; }
.mp-count { flex: 1 1 auto; opacity: 0.8; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
.mp-btn { background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #444); border-radius: 4px; width: 22px;
    height: 18px; line-height: 1; font-size: 12px; cursor: pointer; padding: 0; }
.mp-btn:hover:not(:disabled) { filter: brightness(1.35); }
.mp-btn:disabled { opacity: 0.3; cursor: default; }
.mp-list { flex: 1 1 auto; overflow-y: auto; overflow-x: hidden; display: flex;
    flex-direction: column; gap: 4px; padding-right: 2px; min-width: 0; }
/* Grid, not flex: minmax(0, 1fr) is the one column sizing that cannot be
   pushed wider than its container. A flex child defaults to min-width:auto,
   which refuses to shrink below its content width -- that is what made the
   textareas spill past the node border and the x land on the canvas. */
.mp-box { display: grid; grid-template-columns: 18px minmax(0, 1fr) 14px;
    gap: 4px; align-items: start; min-width: 0; width: 100%;
    box-sizing: border-box; }
.mp-num { text-align: right; opacity: 0.55; font-size: 10px; padding-top: 3px;
    overflow: hidden; }
.mp-box textarea { resize: none; min-height: 52px; width: 100%; min-width: 0;
    max-width: 100%; background: var(--comfy-input-bg, #222);
    color: var(--input-text, #ddd);
    border: 1px solid var(--border-color, #383838); border-radius: 4px;
    padding: 3px 5px; font-family: inherit; font-size: 11px; line-height: 1.35;
    box-sizing: border-box; }
.mp-box textarea:focus { outline: none; border-color: #6c9fd0; }
.mp-box.active textarea { border-color: #7fc97f; box-shadow: inset 0 0 0 1px #7fc97f33; }
.mp-box.active .mp-num { opacity: 1; color: #7fc97f; font-weight: bold; }
.mp-del { background: transparent; color: var(--descrip-text, #888); width: 14px;
    border: none; cursor: pointer; font-size: 12px; padding: 0; opacity: 0.5;
    line-height: 1.4; }
.mp-del:hover { opacity: 1; color: #e08585; }
.mp-info { flex: 0 0 auto; height: 13px; font-size: 10px; opacity: 0.75;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`;
    document.head.appendChild(style);
}

function findWidget(node, name) {
    return node.widgets?.find((w) => w.name === name);
}

function hideWidget(node, name) {
    const w = findWidget(node, name);
    if (!w) return;
    // The JSON array is an implementation detail; the textareas are the UI.
    // Keep the widget (the prompt needs its value) but give it no footprint.
    w.hidden = true;
    w.type = "hidden";
    w.computeSize = () => [0, -4];
}

function readBoxes(node) {
    const w = findWidget(node, "prompts_json");
    let boxes = [];
    try {
        const parsed = JSON.parse(w?.value ?? "[]");
        if (Array.isArray(parsed)) boxes = parsed.map((b) => (b == null ? "" : String(b)));
    } catch (e) {
        // Malformed value: keep whatever text was in there as a single box
        // rather than silently discarding the user's writing.
        if (w?.value) boxes = [String(w.value)];
    }
    if (!boxes.length) boxes = ["", ""];
    return boxes.slice(0, MAX_BOXES);
}

function writeBoxes(node, boxes) {
    const w = findWidget(node, "prompts_json");
    if (w) w.value = JSON.stringify(boxes);
}

// LiteGraph insets widgets from the node edges by roughly this much per side.
const NODE_SIDE_INSET = 26;

function clampToNode(node, root) {
    if (!root) return;
    // Belt and braces on top of `width: 100%`. If ComfyUI already sizes the
    // widget host to the node, this is a no-op because 100% resolves smaller.
    // If it ever hands us a host wider than the node -- which is exactly how
    // the rows ended up spilling over the node border -- this is what keeps
    // the content inside. Graph units, so it holds at any zoom.
    root.style.maxWidth = `${Math.max(80, node.size[0] - NODE_SIDE_INSET)}px`;
}

function widgetHeight(node) {
    const count = readBoxes(node).length;
    const visible = Math.min(Math.max(count, 1), MAX_VISIBLE);
    return HEADER_HEIGHT + visible * BOX_HEIGHT + PADDING;
}

function resizeNode(node) {
    if (!node.mpWidget) return;
    node.mpWidget.computedHeight = widgetHeight(node);
    const size = node.computeSize();
    node.setSize([Math.max(node.size[0], size[0], 320), Math.max(size[1], 140)]);
    node.setDirtyCanvas(true, true);
}

function repaint(node) {
    const root = node.mpRoot;
    if (!root) return;

    const boxes = readBoxes(node);
    const list = root.querySelector(".mp-list");
    const count = root.querySelector(".mp-count");
    const info = root.querySelector(".mp-info");
    const minus = root.querySelector(".mp-minus");
    const plus = root.querySelector(".mp-plus");

    count.textContent = `${boxes.length} ${boxes.length === 1 ? "box" : "boxes"}`;
    minus.disabled = boxes.length <= 1;
    plus.disabled = boxes.length >= MAX_BOXES;

    clampToNode(node, root);

    // Explicit pixel cap: the DOM widget host's height is not reliably
    // definite, so flex alone would grow instead of scrolling.
    list.style.maxHeight = `${MAX_VISIBLE * BOX_HEIGHT}px`;

    const active = node.properties?.[ACTIVE_PROP];
    list.replaceChildren();

    boxes.forEach((text, i) => {
        const box = document.createElement("div");
        box.className = "mp-box" + (active === i ? " active" : "");

        const num = document.createElement("span");
        num.className = "mp-num";
        num.textContent = String(i + 1);

        const ta = document.createElement("textarea");
        ta.value = text;
        ta.placeholder = `prompt ${i + 1}`;
        ta.spellcheck = false;
        ta.addEventListener("input", () => {
            const current = readBoxes(node);
            current[i] = ta.value;
            writeBoxes(node, current);
        });
        // Without this the canvas swallows typing as shortcuts.
        for (const ev of ["keydown", "keyup", "keypress", "pointerdown", "wheel"]) {
            ta.addEventListener(ev, (e) => e.stopPropagation());
        }

        const del = document.createElement("button");
        del.className = "mp-del";
        del.textContent = "×";
        del.title = "Remove this box";
        del.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const current = readBoxes(node);
            if (current.length <= 1) return;
            current.splice(i, 1);
            writeBoxes(node, current);
            repaint(node);
        });

        box.append(num, ta, del);
        list.appendChild(box);
    });

    const idx = findWidget(node, "index");
    const eligible = boxes.filter((b) => b.trim()).length;
    const shown = active !== null && active !== undefined ? `box ${active + 1}` : "not run yet";
    info.textContent = `index ${idx?.value ?? 0} -> ${shown} - ${eligible} with text`;

    resizeNode(node);
}

function createWidget(node) {
    const root = document.createElement("div");
    root.className = "mp-root";

    const header = document.createElement("div");
    header.className = "mp-header";

    const count = document.createElement("span");
    count.className = "mp-count";

    const minus = document.createElement("button");
    minus.className = "mp-btn mp-minus";
    minus.textContent = "−";
    minus.title = "Remove the last box";
    minus.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const boxes = readBoxes(node);
        if (boxes.length <= 1) return;
        boxes.pop();
        writeBoxes(node, boxes);
        repaint(node);
    });

    const plus = document.createElement("button");
    plus.className = "mp-btn mp-plus";
    plus.textContent = "+";
    plus.title = "Add a box";
    plus.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const boxes = readBoxes(node);
        if (boxes.length >= MAX_BOXES) return;
        boxes.push("");
        writeBoxes(node, boxes);
        repaint(node);
    });

    header.append(count, minus, plus);

    const list = document.createElement("div");
    list.className = "mp-list";

    const info = document.createElement("div");
    info.className = "mp-info";

    root.append(header, list, info);

    node.mpRoot = root;
    node.mpWidget = node.addDOMWidget("multiprompt_ui", "multiprompt", root, {
        serialize: false,
        hideOnZoom: false,
        getValue: () => readBoxes(node).length,
        setValue: () => {},
    });
    node.mpWidget.computeSize = function (width) {
        return [width, widgetHeight(node)];
    };
    node.mpWidget.computedHeight = widgetHeight(node);
}

app.registerExtension({
    name: "comfyui.multiprompt",

    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);
            this.properties = this.properties || {};
            hideWidget(this, "prompts_json");
            createWidget(this);
            // Synchronously: building the rows needs no layout, and a deferred
            // paint never happens while the tab is hidden (rAF is throttled to
            // zero there), which would leave the node looking empty.
            repaint(this);
            return result;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = onExecuted?.apply(this, arguments);
            const payload = message?.multiprompt?.[0];
            if (payload) {
                this.properties[ACTIVE_PROP] =
                    payload.box_index === null || payload.box_index === undefined
                        ? null
                        : payload.box_index;
                repaint(this);
            }
            return result;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function () {
            const result = onConfigure?.apply(this, arguments);
            hideWidget(this, "prompts_json");
            repaint(this);
            return result;
        };

        // Dragging the node's corner must re-clamp, or the boxes keep the
        // width from before the resize.
        const onResize = nodeType.prototype.onResize;
        nodeType.prototype.onResize = function () {
            const result = onResize?.apply(this, arguments);
            clampToNode(this, this.mpRoot);
            return result;
        };
    },
});
