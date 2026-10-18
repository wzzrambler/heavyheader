# Remove unused `#include <unordered_set>` from prelude

## What

`doctest/parts/private/prelude.h` includes `<unordered_set>`, but nothing in the
doctest sources uses `std::unordered_set`. Removing it:

- `doctest/parts/private/prelude.h` — drop the include;
- `doctest/doctest.h` — regenerated with `scripts/assemble.py` (verified the
  generator is idempotent on the current tree before making the change).

## Why it is unused

- The only doctest test that uses unordered containers,
  `tests/doctest/test_string.cpp`, includes `<unordered_set>` / `<unordered_map>`
  itself (lines 11–12).
- `doctest/extensions/doctest_mpi.h` includes `<unordered_map>` itself (lines 8,
  91).
- No other file under `doctest/` or `tests/` references `unordered_set` /
  `unordered_map`.

## Measured benefit

Found by [HeavyHeader](https://github.com/wzzrambler/heavyheader), a tool that
verifies "what if this include were gone" by actually removing the line and
recompiling. On doctest's own test build (24 translation units):

| | aggregate build time |
|---|---:|
| baseline | 20556 ms |
| with include removed | 19937 ms (**−3.01%**) |

- All 24 translation units compile after the removal (0 failures).
- Result confirmed by re-measurement under an interleaved A/B protocol
  (min-of-3 per side, per-TU noise floor 2%, aggregate noise floor 1%).
- Measurement rig: MinGW-w64 g++ on Windows, single-threaded build, ~4 clean
  comparisons. Raw data and reproduction commands:
  https://github.com/wzzrambler/heavyheader/tree/main/experiments/results
  (`g2-confirm-picks-doctest-20261018.json`, `g2-sites-doctest-all16-20261018.md`).

## Scope of the verification (stated honestly)

- Covers compilation and wall-clock build time of doctest's own 24-TU test
  build only. It does not run doctest's test suite, and it does not cover
  downstream users — a user who relied on getting `<unordered_set>`
  transitively through doctest must now include it themselves. This is the
  IWYU-consistent direction, but it is technically an observable change for
  such users.
- Not measured on other platforms/toolchains. Your CI should confirm.

If you prefer a more conservative step, the same file also carries
`<set>`/`<map>` (used) — this change is strictly limited to the one unused
include.
