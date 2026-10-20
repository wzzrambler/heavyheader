"""把实测结论按「证据强度」分层——这是给读者的，不是给开发者的。

为什么单独成一个模块（2026-10-20）：供给侧漏报诊断量出一件事——**8 条落地
收益里 7 条被文本判据判成 ``in_use`` / ``unknown``，却全都过了汇编等价闸门**。
也就是说：判据说不清的地方，编译器说得清。但旧界面把两种来源混在同一句
「建议删除」里，读者分不出哪句是推断、哪句是量出来的。

三档证据（常量在 :mod:`heavyheader.attrib.advice`）。

**范围限定（先说清楚，免得被当成形式化证明）**：汇编指纹是「两侧归一化后的
汇编一致」这一实测事实。它给出**代码生成一致性的强证据**，但**不证明完整语义
等价**——证明范围受编译器、编译选项与指纹归一化规则限制。本工具把它当作
**保守验证门槛**，而不是通用形式化证明。

``verified``
    汇编指纹一致 **且** 实测省时过噪声线 **且** 复测仍成立。这是唯一可以
    直接照着改的一档。

``equivalent``
    汇编指纹一致，但收益没过噪声线（或者根本没测出差值）。**代码生成一致**
    已被证明，**值不值得删**还没被证明——这一档的作用是让「编译通过」不再含糊，
    **不是新增建议**，别当成收益报。

``compiles_only``
    只证明「删了还能编过」。**这一档不许说「代码生成一致」**：实测抓到过删掉
    ``<iomanip>`` 后目标文件从 21,289 字节缩到 10,183 字节——能编过，但生成的
    已经不是同一份代码。

分层的价值集中在 :func:`outside_recommendation`：文本判据没推荐、却被闸门
抬到上两档的那批候选。它们正是「零假阳性是拿漏报换来的」这句话的另一半。

本模块只做纯函数（不编译、不读文件），方便测试，也方便库调用方直接复用。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from .attrib.advice import TIER_COMPILES, TIER_EQUIVALENT, TIER_VERIFIED

#: 文本判据里唯一「敢推荐」的那一档。交叉表用它来分「判据推荐」与「判据没推荐」。
RECOMMENDED_VERDICT = "plausibly_unused"

#: 档位排序键（越小越强），用于输出时把强证据排前面。
TIER_ORDER = {
    TIER_VERIFIED: 0,
    TIER_EQUIVALENT: 1,
    TIER_COMPILES: 2,
    "": 3,
}

TIER_SHORT = {
    TIER_VERIFIED: "已验证",
    TIER_EQUIVALENT: "已证等价",
    TIER_COMPILES: "仅编译通过",
    "": "无证据",
}

TIER_LABELS = {
    TIER_VERIFIED: "已验证：汇编指纹一致、实测省时过噪声线、复测仍成立（保守门槛，非形式化证明）",
    TIER_EQUIVALENT: "已证等价：汇编指纹一致，但收益未过噪声线（代码生成一致已证，值不值得删未证）",
    TIER_COMPILES: "仅编译通过：未做代码等价校验，不能说「代码生成一致」",
    "": "未取得证据：编译失败 / 定位失败 / 出错",
}

#: 上两档才是「编译器给的证据」，``compiles_only`` 不是。
GATED_TIERS = (TIER_VERIFIED, TIER_EQUIVALENT)


def tier_of(result: Any) -> str:
    """取一条结论的证据档。用 ``getattr`` 是为了不逼调用方一定传
    :class:`~heavyheader.verify.VerificationResult`（测试里常用替身对象）。"""
    tier = getattr(result, "evidence_tier", None)
    if tier is None:
        # 老对象 / 替身没有这个属性时，退回状态推断，但**绝不**声称等价。
        status = getattr(result, "status", "")
        if status in ("improved", "no-change", "compiles"):
            return TIER_COMPILES
        return ""
    return str(tier)


def select(results: Iterable[Any], tier: str = "") -> list[Any]:
    """按档筛选。``tier=""`` 或 ``"all"`` 表示不分档全要。

    档位由 :func:`tier_of` 现算，不读缓存字段——筛选和展示必须同一个判据，
    否则「筛出来的」和「看到的」会不是一回事。
    """
    wanted = "" if tier in ("", "all", None) else tier
    if wanted == "":
        return list(results)
    return [r for r in results if tier_of(r) == wanted]


def sort_by_evidence(results: Iterable[Any]) -> list[Any]:
    """强证据在前；同档按省下的秒数降序。"""
    return sorted(
        results,
        key=lambda r: (
            TIER_ORDER.get(tier_of(r), 9),
            -(getattr(r, "seconds_saved", None) or 0.0),
        ),
    )


def tier_counts(results: Iterable[Any]) -> dict[str, int]:
    """各档条数。**包含没取得证据的那一档**（键为 ``""``）——只报成功的
    三档会让读者以为「剩下那些也验过了」。"""
    counts = Counter(tier_of(r) for r in results)
    return {k: counts[k] for k in sorted(counts, key=lambda t: TIER_ORDER.get(t, 9))}


def cross_tab(results: Iterable[Any]) -> dict[str, int]:
    """文本判据 × 证据档。

    这张表回答的是本模块存在的理由：**判据说「在用」的候选里，有多少其实
    站在最高档证据上**。键形如 ``"in_use|verified"``。
    """
    counts = Counter(
        f"{getattr(r.candidate, 'usage_verdict', None) or '?'}|{tier_of(r)}"
        for r in results
    )
    return dict(sorted(counts.items()))


def outside_recommendation(results: Iterable[Any]) -> list[Any]:
    """文本判据**没**推荐、但编译器证据把它抬到上两档的候选。

    这一批是「已验证层级」的全部增量：旧界面因为它们被判 ``in_use`` /
    ``unknown`` 而从不展示，可它们恰恰是实测省时最多的那几条（Lua 上
    ``lgc.c``/``lvm.c → stdio.h`` 分别 −5.29% / −6.74%）。
    """
    return [
        r
        for r in results
        if tier_of(r) in GATED_TIERS
        and (getattr(r.candidate, "usage_verdict", None) or "?")
        != RECOMMENDED_VERDICT
    ]


def gated(results: Iterable[Any]) -> list[Any]:
    """真的跑过汇编等价闸门的条数。没跑过闸门的分层讨论没有意义。"""
    return [r for r in results if getattr(r, "codegen_checked", False)]


def blocked(results: Iterable[Any]) -> list[Any]:
    """被闸门拦下的（汇编变了）。

    必须和放行数一起报：**只放行不拦截的闸门等于没有闸门**。
    """
    return [r for r in results if getattr(r, "status", "") == "codegen-changed"]


def summarise(results: Iterable[Any], pool: int = 0) -> dict:
    """给 CLI / JSON 报告用的一层汇总。全部是实测计数，不含估计。"""
    results = list(results)
    gated_runs = gated(results)
    blocked_runs = blocked(results)
    outside = outside_recommendation(results)
    verified = select(results, TIER_VERIFIED)
    statuses = Counter(getattr(r, "status", "?") for r in results)
    return {
        "pool": pool or len(results),
        "measured": len(results),
        "gate_ran": len(gated_runs),
        "gate_blocked": len(blocked_runs),
        "tiers": tier_counts(results),
        #: 状态分布。没有它，「14 条没取得证据」这句话读不出原因——是编不过、
        #: 定位失败，还是闸门没跑？三种情况的下一步完全不同。
        "statuses": dict(statuses.most_common()),
        "cross": cross_tab(results),
        "verified": len(verified),
        "verified_seconds_saved": round(
            sum(r.seconds_saved or 0.0 for r in verified), 4
        ),
        "outside_recommendation": len(outside),
        "outside_recommendation_seconds_saved": round(
            sum(r.seconds_saved or 0.0 for r in outside), 4
        ),
    }


def describe(tier: str) -> str:
    """档位的一句话说明。未知档位不猜，原样返回。"""
    return TIER_LABELS.get(tier, tier)
