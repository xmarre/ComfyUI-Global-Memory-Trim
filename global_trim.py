"""
Global native heap trimming for ComfyUI.

This module monkey-patches ComfyUI's execution.get_output_data so native heap
cleanup can run around every node execution without requiring a workflow node.
It is designed for WSL/Linux workloads that repeatedly allocate large CPU image
buffers through PyTorch, NumPy, OpenCV, Pillow, or other native extensions.
"""

from __future__ import annotations

import asyncio
import ctypes
import functools
import gc
import logging
import os
import platform
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

LOG = logging.getLogger("ComfyUI-Global-Memory-Trim")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except Exception:
        return default
    return max(minimum, value)


@dataclass(frozen=True)
class TrimConfig:
    enabled: bool
    trim_before: bool
    trim_after: bool
    gc_collect: bool
    interval: int
    min_rss_mb: int
    log: bool
    warn_no_libc: bool

    @classmethod
    def from_env(cls) -> "TrimConfig":
        return cls(
            enabled=_env_bool("COMFYUI_GLOBAL_TRIM", True),
            trim_before=_env_bool("COMFYUI_GLOBAL_TRIM_BEFORE", False),
            trim_after=_env_bool("COMFYUI_GLOBAL_TRIM_AFTER", True),
            gc_collect=_env_bool("COMFYUI_GLOBAL_TRIM_GC", True),
            interval=_env_int("COMFYUI_GLOBAL_TRIM_INTERVAL", 1, minimum=1),
            min_rss_mb=_env_int("COMFYUI_GLOBAL_TRIM_MIN_RSS_MB", 0, minimum=0),
            log=_env_bool("COMFYUI_GLOBAL_TRIM_LOG", False),
            warn_no_libc=_env_bool("COMFYUI_GLOBAL_TRIM_WARN_NO_LIBC", True),
        )


_CONFIG = TrimConfig.from_env()
_LIBC: Optional[Any] = None
_MALLOC_TRIM: Optional[Callable[[int], int]] = None
_LIBC_PROBED = False
_PATCHED = False
_COUNTER = 0
_LOCK = threading.RLock()
_LAST_RESULT: dict[str, Any] = {
    "enabled": _CONFIG.enabled,
    "patched": False,
    "platform": sys.platform,
    "phase": "never",
    "reason": "not run yet",
    "rss_before_mb": -1,
    "rss_after_mb": -1,
    "delta_mb": 0,
    "malloc_trim_rc": None,
    "duration_ms": 0.0,
    "count": 0,
}


def is_linux_like() -> bool:
    return sys.platform.startswith("linux")


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        release = platform.uname().release.lower()
        version = platform.uname().version.lower()
        return "microsoft" in release or "microsoft" in version or "wsl" in release
    except Exception:
        return False


def rss_mb() -> int:
    """Return process RSS in MiB on Linux, or -1 if unavailable."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return -1
    return -1


def mem_available_mb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return -1
    return -1


def _probe_libc() -> None:
    global _LIBC, _MALLOC_TRIM, _LIBC_PROBED
    if _LIBC_PROBED:
        return
    _LIBC_PROBED = True

    if not is_linux_like():
        return

    try:
        _LIBC = ctypes.CDLL("libc.so.6")
        trim = getattr(_LIBC, "malloc_trim", None)
        if trim is not None:
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            _MALLOC_TRIM = trim
    except Exception as exc:
        _LIBC = None
        _MALLOC_TRIM = None
        if _CONFIG.warn_no_libc:
            LOG.warning("Could not load libc malloc_trim: %r", exc)


def trim_native_heap(reason: str = "manual", phase: str = "manual", force: bool = False) -> dict[str, Any]:
    """Run gc.collect() and libc malloc_trim(0) when enabled.

    Returns a small metrics dict. The function is intentionally exception-safe:
    it should never break ComfyUI node execution.
    """
    global _COUNTER, _LAST_RESULT

    start = time.perf_counter()
    with _LOCK:
        _COUNTER += 1
        count = _COUNTER

    before = rss_mb()
    rc: Optional[int] = None

    result = {
        "enabled": _CONFIG.enabled,
        "patched": _PATCHED,
        "platform": sys.platform,
        "phase": phase,
        "reason": reason,
        "rss_before_mb": before,
        "rss_after_mb": before,
        "mem_available_mb": mem_available_mb(),
        "delta_mb": 0,
        "malloc_trim_rc": rc,
        "duration_ms": 0.0,
        "count": count,
    }

    try:
        if not _CONFIG.enabled and not force:
            result["reason"] = f"disabled: {reason}"
            return result

        if not is_linux_like():
            result["reason"] = f"non-linux no-op: {reason}"
            return result

        if not force and _CONFIG.interval > 1 and (count % _CONFIG.interval) != 0:
            result["reason"] = f"interval skip: {reason}"
            return result

        if not force and _CONFIG.min_rss_mb > 0 and before >= 0 and before < _CONFIG.min_rss_mb:
            result["reason"] = f"rss below threshold: {reason}"
            return result

        if _CONFIG.gc_collect or force:
            gc.collect()

        _probe_libc()
        if _MALLOC_TRIM is not None:
            rc = int(_MALLOC_TRIM(0))

        after = rss_mb()
        result.update(
            {
                "rss_after_mb": after,
                "mem_available_mb": mem_available_mb(),
                "delta_mb": (after - before) if before >= 0 and after >= 0 else 0,
                "malloc_trim_rc": rc,
            }
        )
    except Exception as exc:
        result["reason"] = f"trim failed: {reason}: {exc!r}"
    finally:
        result["duration_ms"] = round((time.perf_counter() - start) * 1000.0, 3)
        _LAST_RESULT = result
        if _CONFIG.log:
            LOG.info(
                "trim phase=%s reason=%s rss_before=%s rss_after=%s delta=%s rc=%s duration_ms=%s",
                result["phase"],
                result["reason"],
                result["rss_before_mb"],
                result["rss_after_mb"],
                result["delta_mb"],
                result["malloc_trim_rc"],
                result["duration_ms"],
            )
        return result


def last_result() -> dict[str, Any]:
    return dict(_LAST_RESULT)


def _node_label_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    unique_id = None
    obj = None
    try:
        if len(args) > 1:
            unique_id = args[1]
        else:
            unique_id = kwargs.get("unique_id")
        if len(args) > 2:
            obj = args[2]
        else:
            obj = kwargs.get("obj")
        cls = obj.__class__.__name__ if obj is not None else "unknown"
        return f"node={unique_id} class={cls}"
    except Exception:
        return "node=unknown class=unknown"


def install_execution_patch() -> bool:
    """Patch execution.get_output_data.

    This is intentionally narrower and more stable than replacing the whole
    execution.execute function, whose signature changes more often.
    """
    global _PATCHED

    if _PATCHED:
        return True

    if not _CONFIG.enabled:
        LOG.info("Global memory trim disabled by COMFYUI_GLOBAL_TRIM=0")
        return False

    try:
        import execution  # ComfyUI root module
    except Exception as exc:
        LOG.warning("Could not import ComfyUI execution module: %r", exc)
        return False

    original = getattr(execution, "get_output_data", None)
    if original is None or not asyncio.iscoroutinefunction(original):
        LOG.warning("execution.get_output_data is missing or not async; not patching")
        return False

    if getattr(original, "_comfyui_global_memory_trim_patched", False):
        _PATCHED = True
        return True

    @functools.wraps(original)
    async def wrapped_get_output_data(*args: Any, **kwargs: Any) -> Any:
        label = _node_label_from_args(args, kwargs)

        if _CONFIG.trim_before:
            trim_native_heap(reason=label, phase="before")

        try:
            return await original(*args, **kwargs)
        finally:
            if _CONFIG.trim_after:
                trim_native_heap(reason=label, phase="after")

    setattr(wrapped_get_output_data, "_comfyui_global_memory_trim_patched", True)
    setattr(wrapped_get_output_data, "_comfyui_global_memory_trim_original", original)
    execution.get_output_data = wrapped_get_output_data
    _PATCHED = True
    _LAST_RESULT["patched"] = True

    LOG.info(
        "Installed global memory trim patch: enabled=%s before=%s after=%s gc=%s interval=%s min_rss_mb=%s wsl=%s",
        _CONFIG.enabled,
        _CONFIG.trim_before,
        _CONFIG.trim_after,
        _CONFIG.gc_collect,
        _CONFIG.interval,
        _CONFIG.min_rss_mb,
        is_wsl(),
    )
    return True


def config_summary() -> dict[str, Any]:
    return {
        "enabled": _CONFIG.enabled,
        "patched": _PATCHED,
        "trim_before": _CONFIG.trim_before,
        "trim_after": _CONFIG.trim_after,
        "gc_collect": _CONFIG.gc_collect,
        "interval": _CONFIG.interval,
        "min_rss_mb": _CONFIG.min_rss_mb,
        "log": _CONFIG.log,
        "linux_like": is_linux_like(),
        "wsl": is_wsl(),
        "rss_mb": rss_mb(),
        "mem_available_mb": mem_available_mb(),
    }
