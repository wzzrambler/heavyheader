# 构建配置矩阵

同一棵源码树，按多套配置各扫一遍；只列判定发生变化的候选。

| 配置 | 追加参数 | 单元 | 候选 | 疑似未使用 | 无法判定 | 在用 |
|---|---|---:|---:|---:|---:|---:|
| `single` | `—` | 39 | 90 | 7 | 8 | 75 |
| `mt` | `-DZSTD_MULTITHREAD` | 39 | 91 | 8 | 5 | 78 |
| `mt_legacy` | `-DZSTD_MULTITHREAD -DZSTD_LEGACY_SUPPORT=1` | 39 | 93 | 8 | 6 | 79 |

## 相对 `single` 的判定变化

### `single` → `mt`：3 条不同

| 单元 | 头文件 | 行 | 基准 | 本配置 |
|---|---|---:|---|---|
| `pool.c` | `allocations.h` | 13 | unknown | in_use |
| `pool.c` | `debug.h` | 15 | unknown | （不再是候选） |
| `zstd_compress_superblock.c` | `zstd_compress_superblock.h` | 14 | unknown | plausibly_unused |

### `single` → `mt_legacy`：3 条不同

| 单元 | 头文件 | 行 | 基准 | 本配置 |
|---|---|---:|---|---|
| `pool.c` | `allocations.h` | 13 | unknown | in_use |
| `pool.c` | `debug.h` | 15 | unknown | （不再是候选） |
| `zstd_compress_superblock.c` | `zstd_compress_superblock.h` | 14 | unknown | plausibly_unused |
