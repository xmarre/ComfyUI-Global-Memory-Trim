# ComfyUI Global Memory Trim

Global native heap trimming for ComfyUI on Linux/WSL.

This custom node installs a small global execution patch when ComfyUI loads custom nodes. The patch can run Python garbage collection and glibc `malloc_trim(0)` before and/or after ComfyUI node execution.

It is mainly intended for WSL2 workflows that repeatedly allocate large CPU-side image/video buffers through PyTorch, NumPy, OpenCV, Pillow, or native custom nodes and then start stalling or wedging due to retained native heap memory.

The global patch does **not** require adding any node to your workflow.

## What it does

- Optionally runs `gc.collect()`.
- Calls glibc `malloc_trim(0)` when available.
- Can trim before nodes, after nodes, or both.
- Can skip trims until process RSS reaches a configured threshold.
- Can run every N trim opportunities instead of after every node.
- Provides manual diagnostic nodes.

## What it does not do

This is **not** a VRAM cleanup tool.

It does not directly:

- free CUDA VRAM,
- unload ComfyUI models,
- clear ComfyUI model cache,
- delete workflow outputs,
- fix CUDA allocator fragmentation,
- change image generation math.

It only targets CPU/native heap retention.

## Installation

Clone into ComfyUI's `custom_nodes` folder:

```bash
cd ~/ComfyUI/custom_nodes
git clone https://github.com/xmarre/ComfyUI-Global-Memory-Trim
```

Restart ComfyUI.

On startup, you should see a line similar to:

```text
Installed global memory trim patch: enabled=True before=False after=True ...
```

## Performance-oriented WSL startup script

This is the current performance-oriented WSL setup used for a large ComfyUI workflow with heavy model switching, Flux/SDXL/SeedVR2/detailer passes, and large CPU-side image buffers.

Important details:

- Uses `--highvram`.
- Uses the normal CUDA allocator path.
- Does **not** use `--disable-cuda-malloc`.
- Keeps `PYTORCH_CUDA_ALLOC_CONF` unset.
- Disables async offload and pinned memory, which can be fragile under WSL.
- Uses glibc trim thresholds for CPU/native heap behavior.
- Uses global trim after nodes only.
- Trims every third trim opportunity via `COMFYUI_GLOBAL_TRIM_INTERVAL=3`.
- Keeps trim logging enabled for validation.

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
export COMFYUI_GLOBAL_TRIM_INTERVAL=3
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

## Notes on the startup flags

### Keep the normal CUDA allocator path

Do not add this flag for the performance-oriented profile:

```bash
--disable-cuda-malloc
```

In this WSL setup, forcing the native allocator path caused worse VRAM reservation/overflow behavior. Keeping the normal CUDA allocator path avoided that issue.

Also keep this unset:

```bash
unset PYTORCH_CUDA_ALLOC_CONF
```

### Disable async offload and pinned memory on WSL

The performance-oriented profile keeps:

```bash
--disable-async-offload
--disable-pinned-memory
```

These reduce exposure to WSL-sensitive transfer/offload paths. They can reduce some performance benefits, but in this setup they were part of the stable configuration.

### Trim interval

The profile uses:

```bash
export COMFYUI_GLOBAL_TRIM_INTERVAL=3
```

This trims less often than every node while still keeping regular heap release pressure.

For more aggressive debugging, use:

```bash
export COMFYUI_GLOBAL_TRIM_INTERVAL=1
```

For less overhead, test higher values one at a time:

```bash
export COMFYUI_GLOBAL_TRIM_INTERVAL=4
export COMFYUI_GLOBAL_TRIM_INTERVAL=8
```

### Trim logging

The performance-oriented validation profile uses:

```bash
export COMFYUI_GLOBAL_TRIM_LOG=1
```

This is useful while confirming that the hook is active and trimming at the expected interval.

For normal long-term use, turn it off:

```bash
export COMFYUI_GLOBAL_TRIM_LOG=0
```

### Before-node trim

The performance-oriented profile uses after-node trim only:

```bash
export COMFYUI_GLOBAL_TRIM_BEFORE=0
export COMFYUI_GLOBAL_TRIM_AFTER=1
```

Before-node trimming is more aggressive and can add overhead. Enable it only for diagnostics or extremely fragile workflows:

```bash
export COMFYUI_GLOBAL_TRIM_BEFORE=1
```

## Environment variables

| Variable | Default | Meaning |
|---|---:|---|
| `COMFYUI_GLOBAL_TRIM` | `1` | Enable or disable the global execution patch. |
| `COMFYUI_GLOBAL_TRIM_AFTER` | `1` | Trim after node execution. |
| `COMFYUI_GLOBAL_TRIM_BEFORE` | `0` | Trim before node execution. More aggressive. |
| `COMFYUI_GLOBAL_TRIM_GC` | `1` | Run `gc.collect()` before `malloc_trim(0)`. |
| `COMFYUI_GLOBAL_TRIM_INTERVAL` | `1` | Run trim every N trim opportunities. |
| `COMFYUI_GLOBAL_TRIM_MIN_RSS_MB` | `0` | Only trim when process RSS is at least this many MB. `0` means always. |
| `COMFYUI_GLOBAL_TRIM_LOG` | `0` | Log each trim result. Useful for diagnostics, noisy for normal use. |
| `COMFYUI_GLOBAL_TRIM_WARN_NO_LIBC` | `1` | Warn if glibc `malloc_trim` cannot be loaded. |

## Manual nodes

This extension also provides diagnostic/manual nodes:

- **Global Memory Trim Now**
- **Global Memory Trim Status**

They are optional. The global patch works without placing these nodes in a workflow.

## Conservative diagnostic profile

For isolating CPU/native heap wedges, a stricter profile can be useful. It is slower and should not be treated as the default performance setup.

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

Use this only when trying to reproduce or isolate CPU/native memory stalls. For normal performance-oriented usage, start with the script above instead.

## License

MIT
