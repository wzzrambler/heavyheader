"""HeavyHeader -- C/C++ 构建效能治理平台。

用编译器自己的输出来归因头文件成本，定位可精简的 ``#include``，并在 CI
中门禁构建成本回归。

设计原则只有一条：**不自己解析 C++**。工具向真实编译器提问并读取它的回答，
因此分析结果在构造上是精确的——不存在一个会出细微差错的手写预处理器。
"""

from __future__ import annotations

__version__ = "0.1.0"

# 项目地址。曾经散落在各个渲染器里，且 markdown 报告页脚那一处是空链接
# （"https://github.com/"），用户点开只能看到 GitHub 首页。统一成常量后由
# tests/test_repo_links.py 守住：既防空链接，也防 finalize.py 式的重复前缀。
PROJECT_URL = "https://github.com/wzzrambler/heavyheader"

__all__ = ["__version__", "PROJECT_URL"]
