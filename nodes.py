from __future__ import annotations

from .global_trim import config_summary, last_result, trim_native_heap


class GlobalMemoryTrimNow:
    """Manual trim node.

    The global execution patch runs automatically when the custom node is loaded.
    This node exists for manual testing and for workflows where the user wants a
    visible trim/checkpoint step.
    """

    CATEGORY = "utils/memory"
    RETURN_TYPES = ("STRING", "INT", "INT", "INT")
    RETURN_NAMES = ("status", "rss_before_mb", "rss_after_mb", "delta_mb")
    FUNCTION = "trim"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "trigger": ("BOOLEAN", {"default": True}),
                "force_even_if_disabled": ("BOOLEAN", {"default": False}),
            }
        }

    def trim(self, trigger: bool = True, force_even_if_disabled: bool = False):
        if not trigger:
            result = last_result()
            status = f"not triggered; last={result}"
            return (status, int(result.get("rss_before_mb", -1)), int(result.get("rss_after_mb", -1)), int(result.get("delta_mb", 0)))

        result = trim_native_heap(reason="manual node", phase="manual", force=force_even_if_disabled)
        status = str(result)
        return (status, int(result.get("rss_before_mb", -1)), int(result.get("rss_after_mb", -1)), int(result.get("delta_mb", 0)))


class GlobalMemoryTrimStatus:
    CATEGORY = "utils/memory"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "status"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    def status(self):
        return (str({"config": config_summary(), "last_trim": last_result()}),)


NODE_CLASS_MAPPINGS = {
    "GlobalMemoryTrimNow": GlobalMemoryTrimNow,
    "GlobalMemoryTrimStatus": GlobalMemoryTrimStatus,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "GlobalMemoryTrimNow": "Global Memory Trim Now",
    "GlobalMemoryTrimStatus": "Global Memory Trim Status",
}
