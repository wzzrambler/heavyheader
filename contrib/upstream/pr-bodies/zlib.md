# zutil.h: remove unused `#include <stdlib.h>`

## What

`zutil.h` (line 29) includes `<stdlib.h>` inside the
`#if defined(STDC) && !defined(Z_SOLO)` block. Nothing in `zutil.h` needs it.

## Why it is unused

- `zutil.h` uses no `stdlib.h` symbols directly (no `malloc`/`free`/`getenv`/
  `strtol`/… — the DOS `farmalloc`/`farfree` branches are untouched).
- `zutil.c`, the only file that needs `malloc` (for its `zcalloc`
  implementation), already includes `<stdlib.h>` itself at line 124.
- All zlib allocation in the library goes through the `zcalloc`/`zcfree`
  functions, not through direct `malloc` calls in other `.c` files.
- `zutil.h` is an internal header (not installed by `make install`), so the
  change is not visible to library consumers.

## Measured benefit

Found by [HeavyHeader](https://github.com/wzzrambler/heavyheader), a tool that
verifies "what if this include were gone" by actually removing the line and
recompiling. On zlib's own sources (15 translation units, 9 of which include
`zutil.h`):

| | aggregate build time |
|---|---:|
| baseline | 3314 ms |
| with include removed | 3244 ms (**−2.125%**) |

- All 9 affected translation units compile after the removal (0 failures).
- Confirmed by re-measurement under the tool's formal protocol
  (runs=3, min-of-3 per side, 2% noise floor, 2 confirmation passes).
- Measurement rig: MinGW-w64 gcc on Windows. Raw data and reproduction:
  https://github.com/wzzrambler/heavyheader/tree/main/experiments/results
  (`g2-confirm-picks-zlib-20261018.json`).

## Scope of the verification (stated honestly)

- Covers compilation and wall-clock build time on the Windows/MinGW toolchain
  only. The DOS-era conditional branches in `zutil.h` (`__GO32__`, `_MSC_VER`
  malloc paths, etc.) are untouched, but I cannot exercise those platforms —
  your build farm should confirm.
- Note: the default heuristic of the tool classified this include as "in use"
  (the word `size` in `zutil.h` was mistaken for a `<stdlib.h>` dependency);
  the candidate only surfaced with the heuristic gate disabled, which is why
  the claim here is backed by direct measurement rather than by the tool's
  screening.
