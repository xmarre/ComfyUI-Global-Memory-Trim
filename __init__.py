from __future__ import annotations

# Install the global execution patch as soon as ComfyUI imports this custom node.
# Failures are intentionally non-fatal: a memory helper must never prevent ComfyUI
# from starting.
try:
    from .global_trim import install_execution_patch

    install_execution_patch()
except Exception as exc:  # pragma: no cover - defensive startup path
    import logging

    logging.getLogger("ComfyUI-Global-Memory-Trim").warning(
        "Failed to install global memory trim patch: %r", exc
    )

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
