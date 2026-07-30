"""DRILO MultiPrompt: several text boxes, one per Run.

The point is comparing prompts against the same seed. The index widget uses the
same mechanism seeds do (`control_after_generate`), so setting it to "increment"
advances on every Run, and a Batch count of N walks through N prompts in one
go. Leave the KSampler seed on "fixed" and the text is the only thing that
changes between takes.
"""

import json

MAX_BOXES = 32

# {node_id: text emitted by the last execution}. nodes.py reads this so it can
# tag DRILO AudioMultiExport samples with the prompt that produced them:
# walking the `prompt` dict cannot find it, because the text is computed here.
LAST_PROMPT = {}


def parse_prompts(raw):
    """The hidden widget stores a JSON array. Never blow up on corrupt text."""
    if isinstance(raw, list):
        boxes = raw
    else:
        try:
            boxes = json.loads(raw or "[]")
        except (TypeError, ValueError):
            # With broken JSON, treating the content as a single box beats
            # discarding whatever the user had written.
            return [str(raw or "")]
        if not isinstance(boxes, list):
            return [str(raw or "")]

    out = [("" if b is None else str(b)) for b in boxes[:MAX_BOXES]]
    return out or [""]


def select(boxes, index, skip_empty=True):
    """Return (text, real_box_index, eligible_count).

    `real_box_index` is the box number (0 based) so the UI can highlight the box
    that fired, not its position inside the filtered list.
    """
    candidates = [
        (i, text) for i, text in enumerate(boxes) if (text.strip() or not skip_empty)
    ]
    if not candidates:
        return "", None, 0

    slot = int(index) % len(candidates)
    box_index, text = candidates[slot]
    return text, box_index, len(candidates)


class DriloMultiPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # Managed by web/multiprompt.js; hidden on the canvas.
                "prompts_json": ("STRING", {"default": '["", ""]', "multiline": True}),
                "index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 0xFFFFFFFF,
                        "control_after_generate": True,
                        "tooltip": "Set this to 'increment' so every Run uses the next box.",
                    },
                ),
                "skip_empty": (
                    "BOOLEAN",
                    {"default": True, "tooltip": "Skip empty boxes instead of emitting empty text."},
                ),
            },
            "hidden": {"node_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("prompt", "box_index", "info")
    FUNCTION = "pick_prompt"
    CATEGORY = "utils"
    DESCRIPTION = (
        "Several text boxes; each Run emits the next one. For trying different "
        "prompts against the same seed."
    )

    def pick_prompt(self, prompts_json, index, skip_empty, node_id=None):
        boxes = parse_prompts(prompts_json)
        text, box_index, total = select(boxes, index, skip_empty)

        if total == 0:
            info = "no prompts"
        else:
            info = "box {}/{}".format((box_index or 0) + 1, len(boxes))
            if total != len(boxes):
                info += " ({} with text)".format(total)

        LAST_PROMPT[str(node_id)] = text

        return {
            "ui": {
                "multiprompt": [
                    {
                        "box_index": box_index,
                        "total_boxes": len(boxes),
                        "eligible": total,
                        "index": int(index),
                        "info": info,
                        "preview": text[:200],
                    }
                ]
            },
            "result": (text, box_index if box_index is not None else -1, info),
        }


NODE_CLASS_MAPPINGS = {"DRILO_MultiPrompt": DriloMultiPrompt}
NODE_DISPLAY_NAME_MAPPINGS = {"DRILO_MultiPrompt": "🐊 DRILO MultiPrompt"}
