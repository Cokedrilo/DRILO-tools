"""DRILO-tools: audio and prompt nodes for ComfyUI.

DRILO AudioMultiExport (audition a batch, keep the good takes, send them to
DaVinci Resolve) and DRILO MultiPrompt (several text boxes, one per Run).
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .multiprompt import (
    NODE_CLASS_MAPPINGS as _MP_CLASSES,
    NODE_DISPLAY_NAME_MAPPINGS as _MP_NAMES,
)

NODE_CLASS_MAPPINGS.update(_MP_CLASSES)
NODE_DISPLAY_NAME_MAPPINGS.update(_MP_NAMES)

# Importing the module registers the aiohttp routes on PromptServer.
from . import server_routes  # noqa: F401

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
