# ComfyUI Global Memory Trim

Global native heap trimming for ComfyUI on Linux/WSL.

This custom node installs a small execution patch when ComfyUI loads custom nodes. The patch can run Python `gc.collect()` and glibc `malloc_trim(0)` before and/or after ComfyUI node execution. It is intended for workflows that repeatedly create large CPU/native image or video buffers through PyTorch, NumPy, OpenCV, Pillow, or custom native code and then stall under WSL2 memory pressure.

The global patch works automatically after installation. You do not need to add a node to your workflow.

The package also exposes two optional diagnostic nodes:

- **Global Memory Trim Now**: manually run a trim and return RSS metrics.
- **Global Memory Trim Status**: show current configuration and the last trim result.

## What this does

On Linux, `malloc_trim(0)` asks glibc to return free heap pages back to the OS. This can help WSL2 recover memory after large temporary CPU/native allocations.

This targets **CPU/native heap retention**, not CUDA VRAM.

It does not directly:

- unload ComfyUI models;
- clear CUDA VRAM;
- delete ComfyUI caches;
- change workflow outputs;
- fix actual live Python references or live tensors.

## Installation

From your ComfyUI directory:

```bash
git clone https://github.com/xmarre/ComfyUI-Global-Memory-Trim custom_nodes/ComfyUI-Global-Memory-Trim
```

Or copy the folder manually into:

```text
ComfyUI/custom_nodes/ComfyUI-Global-Memory-Trim
```

Restart ComfyUI. With logging enabled, startup should show a line similar to:

```text
Installed global memory trim patch: enabled=True before=False after=True gc=True interval=1 min_rss_mb=8192 wsl=True
```

## Performance-oriented WSL setup

This is the current performance-oriented WSL2 launch profile used for a large ComfyUI workflow with heavy model switching, Flux/SDXL/SeedVR2/detailer passes, and large CPU image buffers.

The important points are:

- Keep `--highvram` for performance.
- Disable ComfyUI async offload and pinned memory on WSL.
- Do **not** use `--disable-cuda-malloc` in this profile; it caused worse VRAM behavior in this workflow.
- Keep `PYTORCH_CUDA_ALLOC_CONF` unset.
- Use glibc trim thresholds plus the global trim hook for CPU/native heap pressure.
- Keep SeedVR2 BF16 forced on when using the SeedVR2 import-probe workaround and the higher-quality 7B path.

```bash
#!/usr/bin/env bash
set -e

_hold_terminal_on_failure() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    printf '\nComfyUI launcher exited with status %d\n' "$rc" >&2
    printf 'Dropping into interactive shell so the terminal stays open.\n' >&2
    exec bash -i
  fi
}
trap _hold_terminal_on_failure EXIT

source ~/miniconda3/etc/profile.d/conda.sh
conda activate comfy312


export MALLOC_MMAP_THRESHOLD_=65536
export MALLOC_TRIM_THRESHOLD_=65536

export COMFYUI_GLOBAL_TRIM=1
export COMFYUI_GLOBAL_TRIM_AFTER=1
export COMFYUI_GLOBAL_TRIM_BEFORE=0
export COMFYUI_GLOBAL_TRIM_GC=1
export COMFYUI_GLOBAL_TRIM_INTERVAL=1
export COMFYUI_GLOBAL_TRIM_LOG=1
export COMFYUI_GLOBAL_TRIM_MIN_RSS_MB=8192

export SEEDVR2_FORCE_BFLOAT16=1
unset SEEDVR2_IMPORT_BFLOAT16_PROBE

unset PYTORCH_CUDA_ALLOC_CONF

export SUPERBEASTS_SPCA_RETURN_RESIDUALS=false
export SUPERBEASTS_HDR_MALLOC_TRIM=true

export PYTHONFAULTHANDLER=1

cd ~/ComfyUI

set +e
python main.py \
  --listen 0.0.0.0 \
  --port 8188 \
  --fast fp16_accumulation \
  --highvram \
  --use-pytorch-cross-attention \
  --disable-async-offload \
  --disable-pinned-memory \
  "$@"
status=$?
set -e

exit "$status"
```

### After the setup is validated

`COMFYUI_GLOBAL_TRIM_LOG=1` is useful while verifying that the hook is active, but it creates log spam. Once stable, set:

```bash
export COMFYUI_GLOBAL_TRIM_LOG=0
```

Do not change multiple memory-related flags at once. If testing performance changes, change one value per run.

## Conservative diagnostic setup

For isolating CPU/native heap stalls, the stricter setup below can be useful. It clamps thread pools and glibc arenas, which may improve WSL stability but can slow CPU-heavy nodes.

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
export COMFYUI_GLOBAL_TRIM_LOG=0
export COMFYUI_GLOBAL_TRIM_MIN_RSS_MB=8192
```

Use this only when the failure looks like CPU/native memory pressure. For normal performance-oriented use, the previous setup is preferred.

## Configuration

All configuration is done through environment variables.

| Variable | Default | Meaning |
|---|---:|---|
| `COMFYUI_GLOBAL_TRIM` | `1` | Enable or disable the global patch. |
| `COMFYUI_GLOBAL_TRIM_AFTER` | `1` | Trim after node execution. |
| `COMFYUI_GLOBAL_TRIM_BEFORE` | `0` | Trim before node execution. More aggressive; usually leave off unless needed. |
| `COMFYUI_GLOBAL_TRIM_GC` | `1` | Run `gc.collect()` before `malloc_trim(0)`. |
| `COMFYUI_GLOBAL_TRIM_INTERVAL` | `1` | Trim every N trim opportunities. Higher values reduce overhead. |
| `COMFYUI_GLOBAL_TRIM_MIN_RSS_MB` | `0` | Only trim when process RSS is at least this many MB. `0` means no RSS threshold. |
| `COMFYUI_GLOBAL_TRIM_LOG` | `0` | Log every trim. Useful for validation, noisy during normal use. |
| `COMFYUI_GLOBAL_TRIM_WARN_NO_LIBC` | `1` | Warn if glibc `malloc_trim` cannot be loaded. |

## Notes on related ComfyUI flags

`--disable-async-offload` and `--disable-pinned-memory` can be useful on WSL when async offload or pinned transfer paths wedge.

`--disable-cuda-malloc` changes CUDA allocator behavior. It is not part of the performance-oriented setup above because it caused worse VRAM behavior in the tested workflow.

`PYTORCH_CUDA_ALLOC_CONF` should remain unset for this profile unless you are deliberately testing allocator behavior.

## Troubleshooting

### Confirm the patch loaded

Enable logging:

```bash
export COMFYUI_GLOBAL_TRIM_LOG=1
```

Restart ComfyUI and look for:

```text
Installed global memory trim patch
```

During execution, with logging enabled, you should see per-trim status lines.

### If the workflow is stable but logs are noisy

Set:

```bash
export COMFYUI_GLOBAL_TRIM_LOG=0
```

### If the workflow still wedges inside a long-running node

The global hook runs before and/or after ComfyUI node execution. If a node wedges internally before returning, the after-node trim will not run. That node may need its own internal chunk/patch-level trimming.

### If CUDA VRAM overflows

This custom node does not directly free CUDA VRAM. Check ComfyUI model/cache/offload settings and CUDA allocator flags instead.

## License

MIT
