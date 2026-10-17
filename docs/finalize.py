"""一次性收尾：把占位符替换成真实信息并重新生成 PDF。

大赛提交前必须替换的占位符散在 6 个文件里（PDF 封面姓名 + 5 处 GitHub 仓库
地址），逐个手工改容易漏——而漏一个的后果是评委点开死链，或 PDF 封面写着
「待填写」。这个脚本把 6 处改动收敛成 1 条命令。

用法：
    python docs/finalize.py --author "你的名字" --repo "https://github.com/you/heavyheader"

默认 --dry-run，只列出会改什么；确认无误后加 --apply 才落盘。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 存放 GitHub 仓库地址的文件与其中的占位符
_PLACEHOLDER_URL = "heavyheader/heavyheader"
_URL_FILES = [
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "SUBMISSION.md",
    "SECURITY.md",
]

#: PDF 封面姓名的占位符
_AUTHOR_PLACEHOLDER = "项目负责人：（待填写）"
_AUTHOR_FILE = "docs/make_pdf.py"


def replace_in(path: Path, old: str, new: str) -> int:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count:
        path.write_text(text.replace(old, new), encoding="utf-8")
    return count


def repair_double_scheme(text: str) -> tuple[str, int]:
    """把 `https://github.com/https://github.com/...` 折叠回 `https://github.com/...`。

    曾经的真实事故：占位符是裸的 ``heavyheader/heavyheader``，而它出现在
    ``https://github.com/`` 之后，朴素替换于是拼出了双份前缀，污染了
    README 徽章、pyproject 元数据与 CHANGELOG 链接。这里既用于修补历史
    污染，也作为替换后的兜底校验。
    """
    doubled = "https://github.com/https://github.com/"
    count = text.count(doubled)
    if count:
        text = text.replace(doubled, "https://github.com/")
    return text, count


def substitute_repo_url(text: str, repo: str) -> str:
    """前缀感知地替换仓库地址，保证幂等且不产生重复 scheme。

    先处理「带前缀的完整占位 URL」，再处理裸占位串；最后折叠任何遗留的
    双份前缀。对已替换过的文件重跑不出变化（幂等）。
    """
    full_placeholder = "https://github.com/" + _PLACEHOLDER_URL
    text = text.replace(full_placeholder, repo)
    text = text.replace(_PLACEHOLDER_URL, repo)
    text, _ = repair_double_scheme(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author", required=True, help="项目负责人姓名（PDF 封面用）")
    parser.add_argument(
        "--repo",
        required=True,
        help="GitHub 仓库地址，如 https://github.com/username/heavyheader",
    )
    parser.add_argument("--apply", action="store_true", help="真正落盘（默认只预演）")
    args = parser.parse_args()

    repo = args.repo.rstrip("/")
    if not re.match(r"^https://github\.com/[^/]+/[^/]+$", repo):
        print(f"错误：--repo 看起来不是合法的 GitHub 仓库地址：{repo}", file=sys.stderr)
        return 2

    plan: list[tuple[Path, str, str, str]] = []

    for name in _URL_FILES:
        path = ROOT / name
        if not path.is_file():
            print(f"警告：{name} 不存在，跳过", file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8")
        count = text.count(_PLACEHOLDER_URL)
        if count:
            plan.append((path, f"{_PLACEHOLDER_URL} → {repo}", f"{count} 处", "URL"))

    author_path = ROOT / _AUTHOR_FILE
    if author_path.is_file() and _AUTHOR_PLACEHOLDER in author_path.read_text(encoding="utf-8"):
        plan.append(
            (author_path, f"{_AUTHOR_PLACEHOLDER} → 项目负责人：{args.author}", "1 处", "姓名")
        )

    if not plan:
        print("没有发现待替换的占位符——可能已经收尾过了。")
        return 0

    print(f"{'预演' if not args.apply else '执行'}：共 {len(plan)} 个文件\n")
    for path, change, count, kind in plan:
        rel = path.relative_to(ROOT)
        print(f"  [{kind}] {rel}  ({count})")
        print(f"         {change}")

    if not args.apply:
        print("\n这是预演。确认无误后加 --apply 落盘，然后用下面的命令重新生成 PDF：")
        print('  python docs/make_pdf.py "HeavyHeader-作品介绍文档.pdf"')
        return 0

    print()
    for path, _, _, kind in plan:
        if kind == "URL":
            original = path.read_text(encoding="utf-8")
            updated = substitute_repo_url(original, repo)
            before = original.count(_PLACEHOLDER_URL)
            path.write_text(updated, encoding="utf-8")
            n = before
        else:
            n = replace_in(path, _AUTHOR_PLACEHOLDER, f"项目负责人：{args.author}")
        print(f"  已改 {path.relative_to(ROOT)}：{n} 处")

    # 落盘后校验：双份前缀是唯一出现过的真实故障模式，必须当场拦住。
    leftovers = []
    for path in (ROOT / name for name in _URL_FILES):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            cleaned, count = repair_double_scheme(text)
            if count:
                path.write_text(cleaned, encoding="utf-8")
            if "github.com/https://" in cleaned or "https://github.com/)" in cleaned:
                leftovers.append(path.relative_to(ROOT))
    if leftovers:
        print("\n错误：以下文件仍存在畸形的 GitHub 链接，请手工检查：", file=sys.stderr)
        for rel in leftovers:
            print(f"  {rel}", file=sys.stderr)
        return 2

    print("\n重新生成 PDF...")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "docs" / "make_pdf.py"),
            str(ROOT / "HeavyHeader-作品介绍文档.pdf"),
        ],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print("PDF 生成失败，请检查上面的输出。", file=sys.stderr)
        return result.returncode

    print("\n完成。接下来：")
    print("  1. git add -A && git commit -m 'docs: 替换占位符为真实信息'")
    print("  2. git remote add origin <仓库地址>.git && git push -u origin main")
    print("  3. 确认 PDF 封面姓名与仓库地址正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
