# ComfyUI Global Memory Trim

Global native heap trimming for ComfyUI on Linux/WSL.

This custom node repo installs a small global execution patch when ComfyUI loads
custom nodes. The patch calls Python `gc.collect()` and glibc `malloc_trim(0)`
after each node execution. It is meant for workflows that repeatedly create large
CPU image/video buffers through PyTorch, NumPy, OpenCV, Pillow, or custom native
extensions and then wedge or stall under WSL2 memory pressure.

It also provides two optional workflow nodes:

- **Global Memory Trim Now**: manually run a trim and return RSS metrics.
- **Global Memory Trim Status**: return current config and last trim result.

The global patch does not require adding either node to your workflow.

## Why this exists

Some WSL2 workloads can stall when native libraries repeatedly allocate and free
large CPU buffers. Python objects may be gone, but glibc arenas can retain the
pages. Under a WSL memory cap, that can trigger heavy reclaim or a hard-looking
VM stall. `malloc_trim(0)` asks glibc to return free heap pages to the OS.

This repo is intentionally CPU/native-heap focused. It does not synchronize CUDA,
unload models, delete ComfyUI caches, or change workflow outputs.

## Installation

From your ComfyUI directory:

```bash
git clone https://github.com/xmarre/ComfyUI-Global-Memory-Trim custom_nodes/ComfyUI-Global-Memory-Trim
```

Or copy this folder into:

```text
ComfyUI/custom_nodes/ComfyUI-Global-Memory-Trim
```

Restart ComfyUI. On startup you should see a log line similar to:

```text
Installed global memory trim patch: enabled=True before=False after=True ...
```

## Recommended WSL launch environment

Use these before starting ComfyUI:

```bash
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export OPENCV_OPENCL_RUNTIME=disabled

export MALLOC_ARENA_MAX=1
export MALLOC_MMAP_THRESHOLD_=65536
export MALLOC_TRIM_THRESHOLD_=65536

export COMFYUI_GLOBAL_TRIM=1
export COMFYUI_GLOBAL_TRIM_AFTER=1
export COMFYUI_GLOBAL_TRIM_BEFORE=0
export COMFYUI_GLOBAL_TRIM_GC=1
export COMFYUI_GLOBAL_TRIM_INTERVAL=1
```

## Configuration

All configuration is via environment variables.

| Variable | Default | Meaning |
|---|---:|---|
| `COMFYUI_GLOBAL_TRIM` | `1` | Enable/disable the global patch. |
| `COMFYUI_GLOBAL_TRIM_AFTER` | `1` | Trim after each node execution. |
| `COMFYUI_GLOBAL_TRIM_BEFORE` | `0` | Also trim before each node execution. Usually not needed. |
| `COMFYUI_GLOBAL_TRIM_GC` | `1` | Run `gc.collect()` before `malloc_trim(0)`. |
| `COMFYUI_GLOBAL_TRIM_INTERVAL` | `1` | Trim every N trim calls. Use `2`, `4`, etc. to reduce overhead. |
| `COMFYUI_GLOBAL_TRIM_MIN_RSS_MB` | `0` | Only trim when process RSS is at least this value. `0` means always. |
| `COMFYUI_GLOBAL_TRIM_LOG` | `0` | Log every trim with RSS before/after. Very noisy. |

## Suggested diagnostic settings

Most aggressive/stable:

```bash
export MALLOC_ARENA_MAX=1
export COMFYUI_GLOBAL_TRIM_INTERVAL=1
```

Less aggressive after stability is confirmed:

```bash
export MALLOC_ARENA_MAX=2
export COMFYUI_GLOBAL_TRIM_INTERVAL=2
```

## Notes

- Linux/WSL only. On non-Linux platforms the patch becomes a no-op.
- `malloc_trim(0)` only returns already-free native heap pages. It does not free
  live tensors, ComfyUI outputs, model weights, or Python objects that are still
  referenced.
- There can be a performance cost, especially with `COMFYUI_GLOBAL_TRIM_INTERVAL=1`.
- This is not an OOM fixer for VRAM. It targets CPU/native heap retention.

## License

MIT
