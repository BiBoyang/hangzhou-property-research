"""Step 3D：市场阶段与风险信号（规则型、本地、只读）。

在 Step 3A query_metric_series() / 3B summarize_series_coverage() / 3C
compare_metric_across_cities() 之上，用透明规则把"当前实际观测"整理为市场阶段、
支持/反向证据、置信度、风险信号与下一步观察指标。不维护第二套 SQL、时期解析
或来源分组逻辑，全程复用 lib_metrics_query 的只读（mode=ro）查询。

硬边界：
- 阶段是规则对当前证据的整理，不是事实本身，更不是投资/购房建议；
- 阶段计算只使用实际观测（is_forecast=0）；预测仅在 include_forecasts=True 时
  作为独立前瞻信号展示（机构观点非市场事实），绝不参与阶段计算；
- 至少 2 个时期才谈"变化"，至少 3 个时期才谈"连续收窄/连续上升/连续下降"；
  单点只报告数据不足，绝不包装成趋势或企稳；
- 不同来源不求平均、不静默合并、不取舍；同期多值冲突全部保留并单独标记；
- 不使用黑箱模型或精确总分（无"风险 78 分"）；不输出"应该买/不买"等结论；
- 零外部 API：不 import requests/urllib/http/socket，不调用任何网络服务。

CLI（只读）：python scripts/market_phase.py [--city 杭州] [--as-of 2026-08]
  [--period-start 2026-03 --period-end 2026-08] [--include-forecasts] [--db 路径] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_metrics_query as mq  # noqa: E402
from lib_paths import DB  # noqa: E402

# ---------------------------------------------------------------------------
# 规则常量：阈值、优先级、版本。所有阶段判定只引用这里的常量，不散落在条件里。
# ---------------------------------------------------------------------------

RULE_VERSION = "market-phase-rules/0.1.0 (2026-09-08)"
DISCLAIMER = "阶段判断只整理当前数据证据，不替用户做购房决策"

# 阶段代码与中文标签（只描述证据形态，不含方向性建议）
PHASE_LABELS = {
    "data_insufficient": "数据不足",
    "continued_decline": "持续下行",
    "decline_narrowing": "跌幅收窄",
    "bottom_fluctuation": "底部震荡",
    "initial_stabilization": "初步企稳",
    "recovery_broadening": "修复扩散",
}

# 门评估优先级：从证据要求最严的阶段向下回退；全部不满足 → data_insufficient。
# 该顺序本身就是"条件不充分时选择更保守阶段"的实现。
PHASE_GATE_ORDER = (
    "recovery_broadening",
    "initial_stabilization",
    "decline_narrowing",
    "bottom_fluctuation",
    "continued_decline",
    "data_insufficient",
)

MIN_PERIODS_FOR_CHANGE = 2   # 至少 2 期才谈"变化"
MIN_PERIODS_FOR_RUN = 3      # 至少 3 期才谈"连续"（连续观测 / 连续上升 / 连续下降）
NARROWING_MIN_PERIODS = 3    # "三期连续收窄"的门槛
NON_WORSENING_MIN_PERIODS = 2  # "至少两个时期不再恶化"的门槛
RISING_RUN_FOR_RECOVERY = 3  # 修复扩散要求成交连续上升期数
ADVERSE_RUN_STRONG = 3       # 库存/挂牌连续上升该期数视为强反向信号

# 核心城市联动：上海/北京/深圳/全国，与杭州做严格同口径比较
CORE_CITIES = ("上海", "北京", "深圳", "全国")
CORE_CITY_METRIC_PRIORITY = ("secondary_volume_units", "avg_price", "yoy_change_pct")

# 指标 → 信号家族路由表（家族内再按 segment 分子市场）
PRICE_CHANGE_METRICS = ("mom_change_pct", "yoy_change_pct")   # value 即涨跌幅 pct
PRICE_LEVEL_METRICS = ("avg_price",)                          # comparison_type=None 为水平值
VOLUME_METRICS = ("secondary_volume_units", "new_home_volume_units", "new_home_volume_area")
INVENTORY_METRICS = ("inventory_months",)
LISTING_METRICS = ("listing_units",)
LAND_METRICS = ("land_transaction_value", "premium_rate_pct")
RENT_METRICS = ("rent_level", "rental_yield")

CORE_METRIC_NAMES = tuple(dict.fromkeys(
    PRICE_CHANGE_METRICS + PRICE_LEVEL_METRICS + VOLUME_METRICS
    + INVENTORY_METRICS + LISTING_METRICS))

FAMILY_ORDER = ("price", "volume", "inventory", "listing", "land", "rent",
                "core_city", "forecast")

# 家族信号极性（direction 统一为"对市场修复的指向"，映射关系透明可查）：
#   price/volume/land：涨=improving；inventory/listing：涨=deteriorating；
#   rent_level：涨=improving（租赁需求回暖）；rental_yield：涨=improving。
# 土地与租金信号只代表自身冷暖，不等价于房价上涨/稳定（信号内附 warning）。

LAND_WARNING = "土地信号只代表土地市场冷暖，不等价于房价上涨"
RENT_WARNING = "租金信号不能直接等价于房价稳定"
FORECAST_WARNING = "机构预测观点，非市场事实；仅独立展示，不参与实际阶段计算"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _dedup_extend(target: list[str], source: list[str]) -> None:
    for w in source:
        if w not in target:
            target.append(w)


def _sort_periods(periods) -> list[str]:
    return sorted(set(periods), key=lambda p: (mq._period_sort_key(p), p))


def _evidence_point(p: dict, s: dict) -> dict:
    """信号证据的最小单元：保留来源、时期、数值与原文摘录，可追溯。"""
    return {
        "period": p["period"],
        "value": p["value"],
        "value_text": p["value_text"],
        "comparison_type": p["comparison_type"],
        "comparison_value": p["comparison_value"],
        "institution": s["institution"],
        "source_file": s["source_file"],
        "source_quote": p["source_quote"],
    }


def _series_kind(s: dict) -> str:
    """change：值本身就是涨跌幅（mom/yoy）；level：值为水平量，需看相邻变化。"""
    if s["metric_name"] in PRICE_CHANGE_METRICS:
        return "change"
    return "change" if s.get("comparison_type") in ("mom", "yoy") else "level"


def _numeric_points(s: dict) -> list[tuple[str, float]]:
    """按时期升序的 (period, value) 数值点；非数值点跳过（不插值、不代替）。"""
    if _series_kind(s) == "change" and s.get("comparison_type") in ("mom", "yoy"):
        getter = lambda p: p["comparison_value"]  # noqa: E731
    else:
        getter = lambda p: p["value"]  # noqa: E731
    pts = [(p["period"], getter(p)) for p in s["points"] if getter(p) is not None]
    return sorted(pts, key=lambda t: (mq._period_sort_key(t[0]), t[0]))


# ---------------------------------------------------------------------------
# 单序列事实：所有"变化/连续/收窄"结论的唯一出处
# ---------------------------------------------------------------------------

def _tail_chain(diffs: list[float], pred) -> int:
    """尾部连续满足 pred 的期数（periods 计数）；无任何满足时为 1（仅末期自身）。"""
    k = 0
    for d in reversed(diffs):
        if pred(d):
            k += 1
        else:
            break
    return k + 1 if k else 1


def _series_facts(points: list[tuple[str, float]], kind: str) -> dict:
    n = len(points)
    values = [v for _, v in points]
    facts = {
        "kind": kind,
        "n_periods": n,
        "periods": [p for p, _ in points],
        "values": values,
        "latest_value": values[-1] if n else None,
        "up_run": 0,            # 尾部连续严格上升期数
        "down_run": 0,          # 尾部连续严格下降期数
        "non_down_run": 0,      # 尾部连续不下降期数（"不再恶化"的统一口径）
        "latest_negative": None,      # change：最新一期是否仍为负
        "narrowing_run": 0,     # change：从负值起步的尾部连续收窄期数
        "turned_positive": None,      # change：最新转正（此前存在负值）
        "mixed_sign_readings": None,  # change：序列内正负读数并存（局部转正迹象）
    }
    if n < MIN_PERIODS_FOR_CHANGE:
        return facts  # 单点/空：不构成任何变化结论
    diffs = [values[i] - values[i - 1] for i in range(1, n)]
    facts["up_run"] = _tail_chain(diffs, lambda d: d > 0)
    facts["down_run"] = _tail_chain(diffs, lambda d: d < 0)
    facts["non_down_run"] = _tail_chain(diffs, lambda d: d >= 0)
    if kind == "change":
        facts["latest_negative"] = values[-1] < 0
        k = 0
        for d in reversed(diffs):
            if d > 0:
                k += 1
            else:
                break
        facts["narrowing_run"] = (k + 1) if (k and values[n - 1 - k] < 0) else 0
        facts["turned_positive"] = values[-1] >= 0 and any(v < 0 for v in values[:-1])
        facts["mixed_sign_readings"] = any(v >= 0 for v in values) and any(v < 0 for v in values)
    return facts


# ---------------------------------------------------------------------------
# 序列方向（极性映射，见家族常量注释）
# ---------------------------------------------------------------------------

def _series_direction(s: dict, facts: dict):
    if facts["n_periods"] < MIN_PERIODS_FOR_CHANGE:
        return None
    kind, fam = facts["kind"], _route_series(s)[0]
    if kind == "change":
        latest = facts["latest_value"]
        if fam in ("inventory", "listing"):  # 库存/挂牌增长 = 压力上升
            return "deteriorating" if latest > 0 else ("improving" if latest < 0 else "stable")
        if fam == "price":
            if latest >= 0 or facts["narrowing_run"] >= MIN_PERIODS_FOR_CHANGE:
                return "improving"
            return "deteriorating" if facts["down_run"] >= MIN_PERIODS_FOR_CHANGE else "stable"
        return "improving" if latest >= 0 else "deteriorating"  # volume/land/rent 变化
    up, down = facts["up_run"], facts["down_run"]
    if fam in ("inventory", "listing"):
        return "deteriorating" if up >= MIN_PERIODS_FOR_CHANGE else (
            "improving" if down >= MIN_PERIODS_FOR_CHANGE else "stable")
    return "improving" if up >= MIN_PERIODS_FOR_CHANGE else (
        "deteriorating" if down >= MIN_PERIODS_FOR_CHANGE else "stable")


def _route_series(s: dict):
    """(family, sub_market)；不属于任何分析家族时返回 (None, None)。"""
    m, seg = s["metric_name"], s["segment"]
    if m in PRICE_CHANGE_METRICS:
        if seg == "rental":
            return "rent", "all"
        return "price", (seg if seg in ("new_home", "secondary_home") else "unspecified")
    if m in PRICE_LEVEL_METRICS:
        return "price", (seg if seg in ("new_home", "secondary_home") else "unspecified")
    if m in VOLUME_METRICS:
        return "volume", ("new" if m.startswith("new_home") else "secondary")
    if m in INVENTORY_METRICS:
        return "inventory", "all"
    if m in LISTING_METRICS:
        return "listing", "all"
    if m in LAND_METRICS:
        return "land", "all"
    if m in RENT_METRICS:
        return "rent", "all"
    return None, None


def _aggregate_direction(directions) -> str | None:
    ds = [d for d in directions if d]
    if not ds:
        return None
    if "improving" in ds and "deteriorating" in ds:
        return "mixed"
    for d in ("improving", "deteriorating"):
        if d in ds:
            return d
    return "stable"


# ---------------------------------------------------------------------------
# 家族信号构建
# ---------------------------------------------------------------------------

def _pick_primary(cands: list[dict]):
    """主序列：变化类优先，其次观测期数多者，再按 source_key 稳定排序。"""
    return sorted(cands, key=lambda s: (
        0 if _series_kind(s) == "change" else 1,
        -len(_numeric_points(s)),
        s["source_key"],
    ))[0]


def _observation(s: dict, facts: dict, direction) -> dict:
    return {
        "institution": s["institution"],
        "source_file": s["source_file"],
        "metric_name": s["metric_name"],
        "segment": s["segment"],
        "unit": s["unit"],
        "comparison_type": s.get("comparison_type"),
        "period_granularity": s["period_granularity"],
        "is_forecast": s["is_forecast"],
        "n_periods": facts["n_periods"],
        "periods": facts["periods"],
        "values": facts["values"],
        "direction": direction,
        "analysis": {
            "kind": facts["kind"],
            "latest_value": facts["latest_value"],
            "up_run": facts["up_run"],
            "down_run": facts["down_run"],
            "non_down_run": facts["non_down_run"],
            "narrowing_run": facts["narrowing_run"],
            "latest_negative": facts["latest_negative"],
            "turned_positive": facts["turned_positive"],
            "mixed_sign_readings": facts["mixed_sign_readings"],
        },
        "evidence": [_evidence_point(p, s) for p in s["points"]],
        "conflict_periods": [c["period"] for c in s["conflicts"]],
    }


def _build_sub_market_signal(family: str, sub: str, code: str, cands: list[dict]) -> dict:
    """一个 (家族, 子市场) 信号：主粒度内分析，其余粒度只作并列观察。"""
    sig = {
        "code": code,
        "family": family,
        "sub_market": sub,
        "direction": None,
        "strength": None,
        "status": "unavailable",
        "metric_name": None,
        "segment": None,
        "periods": [],
        "evidence": [],
        "observations": [],
        "other_granularity_observations": [],
        "warnings": [],
        "granularities": [],
        "conflict_series": [],
    }
    if not cands:
        sig["warnings"].append(f"{family}/{sub} 无任何实际观测序列（指标缺失）")
        return sig

    by_gran: dict[str, list[dict]] = {}
    for s in cands:
        by_gran.setdefault(s["period_granularity"], []).append(s)
    sig["granularities"] = sorted(by_gran)
    if len(by_gran) > 1:
        sig["warnings"].append(
            f"存在多种时期粒度 {sig['granularities']}：仅分析数据最多的粒度，其余粒度并列展示，不跨粒度拼接")

    primary_gran = max(by_gran, key=lambda g: (
        sum(len(_numeric_points(s)) for s in by_gran[g]), g))
    primary_pool = by_gran[primary_gran]
    primary = _pick_primary(primary_pool)

    observations, directions = [], []
    for s in sorted(primary_pool, key=lambda x: x["source_key"]):
        facts = _series_facts(_numeric_points(s), _series_kind(s))
        d = _series_direction(s, facts)
        directions.append(d)
        observations.append(_observation(s, facts, d))
        if s["conflicts"]:
            sig["conflict_series"].append({
                "source_key": s["source_key"],
                "institution": s["institution"],
                "source_file": s["source_file"],
                "metric_name": s["metric_name"],
                "periods": [c["period"] for c in s["conflicts"]],
                "values": [[p["value"] for p in c["points"]] for c in s["conflicts"]],
            })
    for g, others in sorted(by_gran.items()):
        if g == primary_gran:
            continue
        for s in sorted(others, key=lambda x: x["source_key"]):
            facts = _series_facts(_numeric_points(s), _series_kind(s))
            sig["other_granularity_observations"].append(_observation(
                s, facts, _series_direction(s, facts)))

    sig["observations"] = observations
    sig["evidence"] = [e for o in observations for e in o["evidence"]]
    p_facts = _series_facts(_numeric_points(primary), _series_kind(primary))
    sig["metric_name"] = primary["metric_name"]
    sig["segment"] = primary["segment"]
    sig["periods"] = _sort_periods(
        p for s in primary_pool for p in (x["period"] for x in s["points"]))
    sig["primary_analysis"] = {
        "source_key": primary["source_key"],
        "institution": primary["institution"],
        "kind": p_facts["kind"],
        "n_periods": p_facts["n_periods"],
        "narrowing_run": p_facts["narrowing_run"],
        "latest_negative": p_facts["latest_negative"],
        "turned_positive": p_facts["turned_positive"],
        "mixed_sign_readings": p_facts["mixed_sign_readings"],
        "up_run": p_facts["up_run"],
        "down_run": p_facts["down_run"],
        "non_down_run": p_facts["non_down_run"],
    }
    if any(s["conflicts"] for s in cands):
        sig["warnings"].append(
            "本信号存在同期多值冲突（见 conflict_series）：冲突点未参与方向计算，全部保留")

    # 状态：无可分析序列→unavailable；主序列不足 2 期→insufficient；
    # 同期多值冲突或来源方向冲突（improving 与 deteriorating 并存）→conflicted；
    # 否则 available。
    if p_facts["n_periods"] < MIN_PERIODS_FOR_CHANGE and all(
            o["n_periods"] < MIN_PERIODS_FOR_CHANGE for o in observations):
        sig["status"] = "insufficient" if any(
            o["n_periods"] for o in observations) else "unavailable"
        if sig["status"] == "insufficient":
            sig["warnings"].append("现有序列均不足 2 个实际时期：只能报告数据不足，不构成趋势")
    elif sig["conflict_series"]:
        sig["status"] = "conflicted"
    elif "improving" in directions and "deteriorating" in directions:
        sig["status"] = "conflicted"
        sig["warnings"].append("不同来源方向冲突（同时存在 improving 与 deteriorating 读数）：不求平均、不取舍")
    else:
        sig["status"] = "available"
    sig["direction"] = _aggregate_direction(directions)

    n_directional = sum(1 for d in directions if d)
    if sig["status"] == "available":
        if n_directional >= 2 and p_facts["n_periods"] >= MIN_PERIODS_FOR_RUN:
            sig["strength"] = "strong"
        elif p_facts["n_periods"] >= MIN_PERIODS_FOR_RUN:
            sig["strength"] = "moderate"
        else:
            sig["strength"] = "weak"
    if family == "land":
        sig["warnings"].append(LAND_WARNING)
    if family == "rent":
        sig["warnings"].append(RENT_WARNING)
    return sig


def _build_core_signals(series: list[dict]) -> list[dict]:
    buckets: dict[tuple, list[dict]] = {}
    for s in series:
        fam, sub = _route_series(s)
        if fam is None:
            continue
        buckets.setdefault((fam, sub), []).append(s)
    codes = {
        ("price", "new_home"): "price_new_home",
        ("price", "secondary_home"): "price_secondary_home",
        ("price", "unspecified"): "price_unspecified",
        ("volume", "new"): "volume_new_home",
        ("volume", "secondary"): "volume_secondary",
        ("inventory", "all"): "inventory_months",
        ("listing", "all"): "listing_units",
        ("land", "all"): "land_market",
        ("rent", "all"): "rent_market",
    }
    order = ["price", "volume", "inventory", "listing", "land", "rent"]
    out = []
    for fam in order:
        subs = sorted(k for k in buckets if k[0] == fam)
        if fam == "price":  # 价格子市场按 secondary → new → unspecified 固定排序
            subs.sort(key=lambda k: {"secondary_home": 0, "new_home": 1}.get(k[1], 2))
        for key in subs:
            out.append(_build_sub_market_signal(
                fam, key[1], codes.get(key, f"{fam}_{key[1]}"), buckets[key]))
        for sub in ("new_home", "secondary_home", "unspecified") if fam == "price" else (
                "new", "secondary") if fam == "volume" else ("all",):
            if (fam, sub) not in buckets:
                out.append(_build_sub_market_signal(fam, sub, codes[(fam, sub)], []))
    return out


# ---------------------------------------------------------------------------
# 核心城市联动（复用 Step 3C compare_metric_across_cities）
# ---------------------------------------------------------------------------

def _linkage_series_direction(metric: str, pts: list[tuple[str, float]], comparison_type=None):
    kind = "change" if (metric in PRICE_CHANGE_METRICS
                        or comparison_type in ("mom", "yoy")) else "level"
    facts = _series_facts(pts, kind)
    if facts["n_periods"] < MIN_PERIODS_FOR_CHANGE:
        return None, facts
    if kind == "change":
        return ("improving" if facts["latest_value"] >= 0 else "deteriorating"), facts
    return ("improving" if facts["up_run"] >= MIN_PERIODS_FOR_RUN else
            "deteriorating" if facts["down_run"] >= MIN_PERIODS_FOR_RUN else "stable"), facts


def _filter_compare_group_by_as_of(group: dict, as_of_key) -> dict:
    """截断跨城市比较组，并基于截断后的城市点重算共同覆盖期。"""
    if as_of_key is None:
        return group
    out = dict(group)
    cities = []
    for city_entry in group["cities"]:
        ce = dict(city_entry)
        ce["points"] = [p for p in ce["points"]
                         if mq._period_sort_key(p["period"]) <= as_of_key]
        ce["conflicts"] = [c for c in ce.get("conflicts", [])
                            if mq._period_sort_key(c["period"]) <= as_of_key]
        observed = [p["period"] for p in ce["points"]]
        observed += [c["period"] for c in ce["conflicts"]]
        ce["observed_periods"] = _sort_periods(observed)
        cities.append(ce)
    out["cities"] = cities
    by_city = {}
    for ce in cities:
        by_city.setdefault(ce["city"], []).append(ce)
    if len(by_city) >= 2:
        periods = [{p for ce in entries for p in ce["observed_periods"]}
                   for entries in by_city.values()]
        common = set.intersection(*periods) if periods else set()
        union = set.union(*periods) if periods else set()
        out["common_periods"] = _sort_periods(common)
        out["common_period_count"] = len(out["common_periods"])
        out["partially_covered_periods"] = _sort_periods(union - common)
        out["insufficient_common_periods"] = len(common) < MIN_PERIODS_FOR_CHANGE
    else:
        out["common_periods"] = []
        out["common_period_count"] = 0
        out["partially_covered_periods"] = []
        out["insufficient_common_periods"] = False
    return out


def _core_city_linkage(city, series, period_start, period_end, as_of_key, db_path, warnings):
    sig = {
        "code": "core_city_linkage",
        "family": "core_city",
        "sub_market": "all",
        "direction": None,
        "strength": None,
        "status": "unavailable",
        "metric_name": None,
        "segment": None,
        "periods": [],
        "evidence": [],
        "observations": [],
        "other_granularity_observations": [],
        "warnings": [],
        "granularities": [],
        "conflict_series": [],
        "limited_only": False,
    }
    counts = {}
    for s in series:
        counts.setdefault(s["metric_name"], []).append(len(_numeric_points(s)))
    metric = next((m for m in CORE_CITY_METRIC_PRIORITY
                   if counts.get(m) and max(counts[m]) >= MIN_PERIODS_FOR_CHANGE), None)
    if metric is None:
        sig["warnings"].append(
            "核心城市联动：杭州缺乏可比较的核心指标序列（secondary_volume_units/avg_price/"
            "yoy_change_pct 均不足 2 个实际时期）")
        return sig
    sig["metric_name"] = metric

    rep = mq.compare_metric_across_cities(
        cities=list(dict.fromkeys([city, *CORE_CITIES])),
        metric_name=metric, period_start=period_start, period_end=period_end,
        include_forecasts=False, db_path=db_path)
    _dedup_extend(warnings, [w for w in rep["warnings"] if "无匹配记录" not in w])

    usable, display_only, limited_seen = [], [], False
    for raw_group in rep["comparison_groups"]:
        g = _filter_compare_group_by_as_of(raw_group, as_of_key)
        if g["comparability"] == "limited":
            limited_seen = True
            continue
        target = next((c for c in g["cities"] if c["city"] == city), None)
        peers = [c for c in g["cities"] if c["city"] != city]
        if target is None or not peers or g["common_period_count"] < MIN_PERIODS_FOR_CHANGE:
            continue
        obs = {"comparability": "comparable",
               "institution": target["institution"],
               "source_file": target["source_file"],
               "common_periods": g["common_periods"],
               "per_city": []}
        city_dirs = []
        cmp_type = g["comparison_key"]["comparison_type"]
        for ce in [target] + peers:
            getter = (lambda p: p["comparison_value"]) if cmp_type in ("mom", "yoy") \
                else (lambda p: p["value"])
            pts = sorted(
                ((p["period"], getter(p)) for p in ce["points"]
                 if getter(p) is not None and (as_of_key is None or
                                               mq._period_sort_key(p["period"]) <= as_of_key)),
                key=lambda t: (mq._period_sort_key(t[0]), t[0]))
            d, facts = _linkage_series_direction(metric, pts, cmp_type)
            obs["per_city"].append({
                "city": ce["city"], "institution": ce["institution"],
                "source_file": ce["source_file"],
                "n_periods": facts["n_periods"], "periods": facts["periods"],
                "values": facts["values"], "direction": d,
                "points": ce["points"], "conflicts": ce.get("conflicts", []),
            })
            if d:
                city_dirs.append((ce["city"], d))
        # 可用组：杭州方向可判且至少一个核心城市方向可判
        if any(c == city for c, _ in city_dirs) and len(city_dirs) >= 2:
            usable.append((obs, city_dirs))
        else:
            display_only.append(obs)

    sig["limited_only"] = limited_seen and not usable
    sig["observations"] = display_only + [obs for obs, _ in usable]
    sig["evidence"] = [
        {"period": p["period"], "value": p["value"],
         "institution": pc["institution"], "source_file": pc["source_file"],
         "source_quote": p.get("source_quote"), "city": pc["city"]}
        for o in sig["observations"] for pc in o.get("per_city", [])
        for p in pc.get("points", [])]
    sig["evidence"] += [
        {"period": p["period"], "value": p["value"],
         "institution": pc["institution"], "source_file": pc["source_file"],
         "source_quote": p.get("source_quote"), "city": pc["city"]}
        for o in sig["observations"] for pc in o.get("per_city", [])
        for conflict in pc.get("conflicts", []) for p in conflict.get("points", [])]
    if not usable:
        sig["status"] = "insufficient"
        sig["warnings"].append(
            "核心城市比较仅有不同来源并列组（limited）：只能并列展示，不进入任何强结论"
            if sig["limited_only"] else
            "核心城市比较缺乏严格同口径（同机构/同来源/同口径）且共同覆盖 ≥2 期的比较组")
        if sig["observations"]:
            sig["strength"] = "weak"
        return sig

    all_dirs = [d for _, ds in usable for _, d in ds]
    group_dirs = [gd for gd in (_aggregate_direction([d for _, d in ds])
                                for _, ds in usable) if gd]
    if "improving" in group_dirs and "deteriorating" in group_dirs:
        sig["status"] = "conflicted"
        sig["direction"] = "mixed"
        sig["strength"] = "weak"
        sig["warnings"].append("不同严格比较组方向冲突：并列保留，不取舍")
    else:
        sig["status"] = "available"
        # 杭州方向 + 至少一个核心城市同向 → 联动方向；否则只并列
        pair_dirs = [(c, d) for _, ds in usable for c, d in ds]
        target_dir = next((d for c, d in pair_dirs if c == city), None)
        peer_dirs = [d for c, d in pair_dirs if c != city]
        if target_dir and any(d == target_dir for d in peer_dirs):
            sig["direction"] = target_dir
        else:
            sig["direction"] = "mixed"
            sig["warnings"].append("杭州与核心城市方向不一致或不可判：只并列，不下联动结论")
        agree = sum(1 for d in all_dirs if d == sig["direction"])
        sig["strength"] = "moderate" if agree >= 3 else "weak"
    sig["periods"] = _sort_periods(
        p for o in sig["observations"] for p in o.get("common_periods", []))
    if limited_seen:
        sig["warnings"].append("另存在 limited 并列组：不同来源未参与方向判断")
    return sig


# ---------------------------------------------------------------------------
# 前瞻信号（可选；预测与实际阶段严格分离）
# ---------------------------------------------------------------------------

def _forecast_signal(city, period_start, period_end, as_of_key, db_path, warnings):
    sig = {
        "code": "forecast_outlook",
        "family": "forecast",
        "sub_market": "all",
        "direction": None,
        "strength": None,
        "status": "unavailable",
        "metric_name": "multiple",
        "segment": None,
        "periods": [],
        "evidence": [],
        "observations": [],
        "other_granularity_observations": [],
        "warnings": [FORECAST_WARNING],
        "granularities": [],
        "conflict_series": [],
    }
    rep = mq.query_metric_series(
        city=city, period_start=period_start, period_end=period_end,
        include_forecasts=True, db_path=db_path)
    fc = [_filter_by_as_of(s, as_of_key) for s in rep["series"] if s["is_forecast"]]
    if not fc:
        return sig
    _dedup_extend(warnings, [w for w in rep["warnings"] if "无匹配记录" not in w])
    sig["status"] = "insufficient"
    dirs = []
    for s in sorted(fc, key=lambda x: x["source_key"]):
        pts = _numeric_points(s)
        facts = _series_facts(pts, _series_kind(s))
        d = None
        if facts["n_periods"] >= MIN_PERIODS_FOR_CHANGE:
            latest = facts["latest_value"]
            d = "improving" if latest > 0 else ("deteriorating" if latest < 0 else "stable")
            dirs.append(d)
            sig["status"] = "available"
        obs = _observation(s, facts, d)
        sig["observations"].append(obs)
    sig["evidence"] = [e for o in sig["observations"] for e in o["evidence"]]
    sig["direction"] = _aggregate_direction(dirs)
    sig["periods"] = _sort_periods(p for s in fc for p in [x["period"] for x in s["points"]])
    sig["granularities"] = sorted({s["period_granularity"] for s in fc})
    sig["strength"] = "weak" if sig["status"] == "available" else None
    return sig


# ---------------------------------------------------------------------------
# 规则状态与阶段门
# ---------------------------------------------------------------------------

def _fam_state(signals: list[dict], family: str) -> dict:
    sigs = [s for s in signals if s["family"] == family]
    return {
        "signals": sigs,
        "codes": [s["code"] for s in sigs],
        "status": ("unavailable" if not sigs or all(s["status"] == "unavailable" for s in sigs)
                   else "conflicted" if any(s["status"] == "conflicted" for s in sigs)
                   else "insufficient" if all(s["status"] in ("insufficient", "unavailable")
                                              for s in sigs) else "available"),
        "direction": _aggregate_direction([s["direction"] for s in sigs]),
        "n_periods": max((s["primary_analysis"]["n_periods"] for s in sigs
                          if s.get("primary_analysis")), default=0),
        "any_series": any(s["observations"] or s["other_granularity_observations"] for s in sigs),
    }


def _build_rule_state(signals, warnings) -> dict:
    price = _fam_state(signals, "price")
    volume = _fam_state(signals, "volume")
    inventory = _fam_state(signals, "inventory")
    listing = _fam_state(signals, "listing")
    land = _fam_state(signals, "land")
    rent = _fam_state(signals, "rent")
    core_city = _fam_state(signals, "core_city")

    def _min_over(fam, field, only_change=False):
        vals = [s["primary_analysis"][field] for s in fam["signals"]
                if s.get("primary_analysis") and s["primary_analysis"]["n_periods"] >= MIN_PERIODS_FOR_CHANGE
                and (not only_change or s["primary_analysis"]["kind"] == "change")]
        return min(vals) if vals else 0

    def _any_over(fam, field):
        return [s["primary_analysis"][field] for s in fam["signals"] if s.get("primary_analysis")]

    price_change_flags = [s["primary_analysis"] for s in price["signals"]
                          if s.get("primary_analysis") and s["primary_analysis"]["kind"] == "change"]
    latest_negs = [a["latest_negative"] for a in price_change_flags
                   if a["latest_negative"] is not None]

    price.update({
        "narrowing_run": _min_over(price, "narrowing_run", only_change=True),
        "narrowing_run_any": max([a["narrowing_run"] for a in price_change_flags], default=0),
        "latest_negative": (True if True in latest_negs else
                            (False if latest_negs else None)),
        "all_non_negative": (all(v is False for v in latest_negs) if latest_negs else None),
        "local_positive": (any(a["turned_positive"] or a["mixed_sign_readings"]
                               for a in price_change_flags) if price_change_flags else None),
        "non_worsening_run": _min_over(price, "non_down_run"),
    })
    volume.update({
        "up_run_min": _min_over(volume, "up_run"),
        "non_down_run_min": _min_over(volume, "non_down_run"),
    })
    for fam, key in ((inventory, "inventory"), (listing, "listing")):
        fam.update({
            "up_run_max": max([s["primary_analysis"].get("up_run", 0) for s in fam["signals"]
                               if s.get("primary_analysis")], default=0),
            "improving": fam["direction"] == "improving",
            "deteriorating": fam["direction"] == "deteriorating",
        })

    # 分化信号：同家族子市场方向相反（新房 vs 二手、价格 vs 成交）
    def _sub_directions(fam):
        return [s["direction"] for s in fam["signals"] if s["direction"]]
    new_secondary = any(
        ("improving" in ds and "deteriorating" in ds)
        for ds in (_sub_directions(price), _sub_directions(volume)))
    price_volume = (price["direction"], volume["direction"]) in (
        ("improving", "deteriorating"), ("deteriorating", "improving"))

    core_institutions = sorted({o["institution"] for s in price["signals"] + volume["signals"]
                                for o in s["observations"] if o.get("institution")})
    return {
        "price": price, "volume": volume, "inventory": inventory, "listing": listing,
        "land": land, "rent": rent, "core_city": core_city,
        "mixed_family_names": [name for name, fam in (
            ("price", price), ("volume", volume), ("inventory", inventory),
            ("listing", listing), ("land", land), ("rent", rent), ("core_city", core_city))
            if fam["direction"] == "mixed"],
        "divergence": {"new_secondary": new_secondary, "price_volume": price_volume,
                       "any": new_secondary or price_volume},
        "granularity_mismatch": any(
            len(s["granularities"]) > 1 for s in signals if s["family"] != "core_city"),
        "core_institutions": core_institutions,
        "conflicted_families": sorted({s["family"] for s in signals
                                       if s["status"] == "conflicted"}),
        "series_conflict_count": sum(len(s["conflict_series"]) for s in signals),
    }


def _adverse_present(state) -> bool:
    return (state["volume"]["direction"] in ("deteriorating", "mixed")
            or state["inventory"]["deteriorating"] or state["listing"]["deteriorating"]
            or bool(state["mixed_family_names"]) or state["divergence"]["any"])


def _gate_recovery_broadening(s):
    p, v, cc = s["price"], s["volume"], s["core_city"]
    conds = {
        "price_improving_3plus_periods": (
            p["n_periods"] >= MIN_PERIODS_FOR_RUN and p["direction"] == "improving"
            and (p["all_non_negative"] is True or p["narrowing_run"] >= NARROWING_MIN_PERIODS)),
        "volume_rising_3_periods": (
            v["up_run_min"] >= RISING_RUN_FOR_RECOVERY and v["direction"] == "improving"),
        "inventory_or_listing_relieved": s["inventory"]["improving"] or s["listing"]["improving"],
        "background_supported": any(
            f["status"] == "available" and f["direction"] in ("improving", "stable")
            for f in (s["land"], s["rent"])),
        "core_city_strict_supportive": cc["status"] == "available" and cc["direction"] == "improving",
        "no_core_conflict": s["price"]["status"] != "conflicted" and s["volume"]["status"] != "conflicted",
        "no_divergence": not s["divergence"]["any"],
    }
    return all(conds.values()), conds


def _gate_initial_stabilization(s):
    p, v = s["price"], s["volume"]
    # "至少两个指标或来源支持"：支持 = 方向 improving 或 stable（改善或维持）
    supporting = sum(1 for f in (p, v, s["inventory"], s["listing"], s["land"], s["rent"],
                                 s["core_city"])
                     if f["status"] == "available" and f["direction"] in ("improving", "stable"))
    conds = {
        "price_2_periods_non_worsening": (
            p["n_periods"] >= MIN_PERIODS_FOR_CHANGE
            and p["non_worsening_run"] >= NON_WORSENING_MIN_PERIODS),
        "volume_2_periods_non_worsening": (
            v["n_periods"] >= MIN_PERIODS_FOR_CHANGE
            and v["non_down_run_min"] >= NON_WORSENING_MIN_PERIODS),
        "one_price_segment_not_worsening": any(
            sg["direction"] in ("improving", "stable") for sg in p["signals"]),
        "two_indicators_supportive": supporting >= 2,
        "no_strong_supply_adverse": not (
            s["inventory"]["up_run_max"] >= ADVERSE_RUN_STRONG
            or s["listing"]["up_run_max"] >= ADVERSE_RUN_STRONG),
        "not_forecast_driven": True,  # 结构性保证：阶段仅由 is_forecast=0 的实际观测计算
        "core_families_not_conflicted": p["status"] != "conflicted" and v["status"] != "conflicted",
    }
    return all(conds.values()), conds


def _gate_decline_narrowing(s):
    p, v = s["price"], s["volume"]
    conds = {
        "price_still_negative": p["latest_negative"] is True,
        "narrowing_3_consecutive_periods": p["narrowing_run"] >= NARROWING_MIN_PERIODS,
        "volume_not_clearly_recovering": v["up_run_min"] < RISING_RUN_FOR_RECOVERY,
        "counter_signal_present": _adverse_present(s),
    }
    return all(conds.values()), conds


def _gate_bottom_fluctuation(s):
    p = s["price"]
    conds = {
        "price_improvement_sign": (
            p["narrowing_run_any"] >= MIN_PERIODS_FOR_CHANGE
            or p["local_positive"] is True),
        "signals_pulling_against": _adverse_present(s),
        "price_or_volume_3_periods": (
            p["n_periods"] >= MIN_PERIODS_FOR_RUN
            or s["volume"]["n_periods"] >= MIN_PERIODS_FOR_RUN),
    }
    return all(conds.values()), conds


def _gate_continued_decline(s):
    p, v = s["price"], s["volume"]
    conds = {
        "price_persistent_negative": (
            (p["latest_negative"] is True
             and p["narrowing_run_any"] < NARROWING_MIN_PERIODS)
            or (p["latest_negative"] is None and p["direction"] == "deteriorating")),
        "volume_not_recovered": v["direction"] != "improving",
        "no_supply_relief": not (s["inventory"]["improving"] or s["listing"]["improving"]),
        "core_series_3_actual_periods": (
            p["n_periods"] >= MIN_PERIODS_FOR_RUN or v["n_periods"] >= MIN_PERIODS_FOR_RUN),
    }
    return all(conds.values()), conds


def _gate_data_insufficient(s):
    conds = {
        "price_has_2_actual_periods": s["price"]["n_periods"] >= MIN_PERIODS_FOR_CHANGE,
        "volume_has_2_actual_periods": s["volume"]["n_periods"] >= MIN_PERIODS_FOR_CHANGE,
        "inventory_or_listing_available": (
            s["inventory"]["status"] == "available" or s["listing"]["status"] == "available"),
        "core_city_comparable": s["core_city"]["status"] == "available",
    }
    return True, conds


_PHASE_GATES = {
    "recovery_broadening": _gate_recovery_broadening,
    "initial_stabilization": _gate_initial_stabilization,
    "decline_narrowing": _gate_decline_narrowing,
    "bottom_fluctuation": _gate_bottom_fluctuation,
    "continued_decline": _gate_continued_decline,
    "data_insufficient": _gate_data_insufficient,
}


def _decide_phase(state) -> tuple[str, list[dict]]:
    """按 PHASE_GATE_ORDER 从证据要求最严的阶段向下评估；全部门完整留痕。

    chosen = 顺序上第一个全条件通过的门；都不通过 → data_insufficient（恒过兜底）。
    """
    trace, chosen = [], None
    for code in PHASE_GATE_ORDER:
        passed, conds = _PHASE_GATES[code](state)
        trace.append({"phase": code, "passed": passed,
                      "conditions": {k: bool(v) for k, v in conds.items()}})
        if passed and chosen is None:
            chosen = code
    return chosen or "data_insufficient", trace


# ---------------------------------------------------------------------------
# 置信度（证据充分程度，不是判断正确的概率；无分数）
# ---------------------------------------------------------------------------

def _confidence(state) -> str:
    p, v = state["price"], state["volume"]
    core_ok = (p["n_periods"] >= MIN_PERIODS_FOR_CHANGE
               and v["n_periods"] >= MIN_PERIODS_FOR_CHANGE
               and p["status"] != "unavailable" and v["status"] != "unavailable")
    wide = (p["n_periods"] >= MIN_PERIODS_FOR_RUN and v["n_periods"] >= MIN_PERIODS_FOR_RUN
            and len(state["core_institutions"]) >= 2)
    complete = ((state["inventory"]["status"] == "available"
                 or state["listing"]["status"] == "available")
                and state["core_city"]["status"] == "available")
    conflicts = len(state["conflicted_families"]) + state["series_conflict_count"]
    if core_ok and wide and complete and conflicts == 0:
        return "high"
    if core_ok and (wide or complete) and conflicts <= 1:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# 风险信号（与阶段分离；无分数，不映射为购房建议）
# ---------------------------------------------------------------------------

def _risk_entry(code, detail, trigger) -> dict:
    return {"code": code, "trigger": trigger, "warning": detail}


def _risk_signals(state, coverage) -> list[dict]:
    risks: list[dict] = []
    p, v, inv, lst, cc = (state["price"], state["volume"], state["inventory"],
                          state["listing"], state["core_city"])

    if p["status"] == "unavailable":
        risks.append(_risk_entry("data_gap", "核心价格指标无任何实际序列，无法评估价格方向", {
            "metrics": list(PRICE_CHANGE_METRICS + PRICE_LEVEL_METRICS)}))
    if v["status"] == "unavailable":
        risks.append(_risk_entry("data_gap", "核心成交指标无任何实际序列，无法评估成交方向", {
            "metrics": list(VOLUME_METRICS)}))
    for fam, name in ((inv, "库存"), (lst, "挂牌")):
        if fam["status"] == "unavailable":
            risks.append(_risk_entry("data_gap", f"{name}指标缺失，供给压力无法评估", {
                "metrics": list(INVENTORY_METRICS if name == "库存" else LISTING_METRICS)}))

    for fam, label in ((p, "价格"), (v, "成交")):
        if fam["status"] != "unavailable" and fam["n_periods"] < MIN_PERIODS_FOR_RUN:
            risks.append(_risk_entry("low_sample_size", f"核心{label}序列实际观测不足 "
            f"{MIN_PERIODS_FOR_RUN} 期（当前最多 {fam['n_periods']} 期）：不足以判断连续性", {
                "metrics": fam["codes"], "n_periods": fam["n_periods"]}))

    for s in (p["signals"] + v["signals"] + inv["signals"] + lst["signals"]
              + state["land"]["signals"] + state["rent"]["signals"]):
        for c in s["conflict_series"]:
            risks.append(_risk_entry("source_conflict", (
                f"序列 [{c['source_key']}] 在 {('、'.join(c['periods']))} 出现同期多值"
                f"{c['values']}：全部保留，未平均、未取舍"), {
                "metric_name": c["metric_name"], "institution": c["institution"],
                "source_file": c["source_file"], "periods": c["periods"],
                "values": c["values"]}))

    if state["divergence"]["price_volume"]:
        risks.append(_risk_entry("price_volume_divergence", (
            f"价格方向 {p['direction']} 与成交方向 {v['direction']} 背离：两类证据互相矛盾，"
            "阶段判断已按更保守处理"), {
            "price_direction": p["direction"], "volume_direction": v["direction"],
            "metrics": p["codes"] + v["codes"]}))
    if state["divergence"]["new_secondary"]:
        risks.append(_risk_entry("new_secondary_divergence", (
            "新房与二手房方向分化（同家族内同时存在 improving 与 deteriorating）："
            "细分市场冷热不一，不做单一结论"), {"families": ["price", "volume"]}))
    if inv["deteriorating"]:
        s0 = inv["signals"][0] if inv["signals"] else {}
        risks.append(_risk_entry("inventory_pressure", "去化周期序列方向为压力上升（deteriorating）", {
            "metrics": inv["codes"], "periods": s0.get("periods", []),
            "direction": inv["direction"]}))
    if lst["deteriorating"]:
        s0 = lst["signals"][0] if lst["signals"] else {}
        risks.append(_risk_entry("listing_pressure", "挂牌量序列方向为压力上升（deteriorating）", {
            "metrics": lst["codes"], "periods": s0.get("periods", []),
            "direction": lst["direction"]}))
    if cc["status"] != "available":
        detail = ("核心城市比较仅有 limited 并列组或无可比数据：跨城信号不进入强结论"
                  if cc["any_series"] or cc.get("limited_only")
                  else "核心城市联动缺少可比较数据")
        risks.append(_risk_entry("city_comparison_limited", detail, {
            "status": cc["status"], "cities": list(CORE_CITIES)}))
    if state["granularity_mismatch"]:
        risks.append(_risk_entry("granularity_mismatch", (
            "核心指标存在多种时期粒度（如月度与半年并存）：仅按单一粒度分析，"
            "其余并列展示，不跨粒度拼接趋势"), {"families": FAMILY_ORDER}))
    p_insts = {o["institution"] for s in p["signals"] for o in s["observations"]}
    v_insts = {o["institution"] for s in v["signals"] for o in s["observations"]}
    if (len(p_insts) >= 2 and len(v_insts) <= 1) or (len(v_insts) >= 2 and len(p_insts) <= 1):
        risks.append(_risk_entry("source_coverage_asymmetry", (
            f"价格来源 {len(p_insts)} 家 vs 成交来源 {len(v_insts)} 家：来源覆盖不对称，"
            "交叉验证能力有限"), {"price_institutions": sorted(p_insts),
                                 "volume_institutions": sorted(v_insts)}))
    return risks


def _forecast_risks(state, forecast_sig) -> list[dict]:
    if forecast_sig is None or forecast_sig["status"] == "unavailable":
        return []
    if (state["price"]["n_periods"] < MIN_PERIODS_FOR_CHANGE
            or state["volume"]["n_periods"] < MIN_PERIODS_FOR_CHANGE):
        return [_risk_entry("forecast_dominant", (
            "存在机构预测但实际观测不足 2 期：阶段判断仅依据实际数据并返回数据不足，"
            "预测未参与计算"), {
            "forecast_metrics": sorted({o["metric_name"] for o in forecast_sig["observations"]}),
            "forecast_series_count": len(forecast_sig["observations"]),
            "actual_price_periods": state["price"]["n_periods"],
            "actual_volume_periods": state["volume"]["n_periods"]})]
    return []


# ---------------------------------------------------------------------------
# 下一步观察指标（只说"看什么"，不说"该不该买"）
# ---------------------------------------------------------------------------

def _watch_next(state) -> list[dict]:
    out = []
    p, v, cc = state["price"], state["volume"], state["core_city"]
    if p["status"] == "available" and 0 < p["narrowing_run_any"] < NARROWING_MIN_PERIODS:
        out.append({"metric": "mom_change_pct/yoy_change_pct", "segment": "杭州价格变化",
                    "reason": f"跌幅收窄仅 {p['narrowing_run_any']} 期，需 ≥{NARROWING_MIN_PERIODS} 期确认连续性"})
    if p["status"] == "insufficient" or 0 < p["n_periods"] < MIN_PERIODS_FOR_RUN:
        out.append({"metric": "mom_change_pct/yoy_change_pct", "segment": "杭州价格变化",
                    "reason": f"价格实际观测仅 {p['n_periods']} 期，需补足 ≥{MIN_PERIODS_FOR_RUN} 期"})
    if v["status"] == "insufficient" or 0 < v["n_periods"] < MIN_PERIODS_FOR_RUN:
        out.append({"metric": "secondary_volume_units", "segment": "杭州二手房成交",
                    "reason": f"成交实际观测仅 {v['n_periods']} 期，需补足 ≥{MIN_PERIODS_FOR_RUN} 期"})
    for fam, m in ((state["inventory"], "inventory_months"), (state["listing"], "listing_units")):
        if fam["status"] == "unavailable":
            out.append({"metric": m, "segment": "杭州",
                        "reason": "该供给指标完全缺失，无法评估库存/挂牌压力"})
        elif fam["deteriorating"]:
            out.append({"metric": m, "segment": "杭州",
                        "reason": "供给压力方向为上升，关注后续是否见顶回落"})
    if cc["status"] != "available":
        out.append({"metric": "/".join(CORE_CITY_METRIC_PRIORITY),
                    "segment": "杭州+核心城市同口径",
                    "reason": "缺少严格同口径且共同覆盖 ≥2 期的跨城比较组（同机构/同来源文件）"})
    if state["granularity_mismatch"]:
        out.append({"metric": "核心指标", "segment": "杭州",
                    "reason": "存在月/季/半年多粒度并存：统一粒度后连续性判断更可靠"})
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _filter_by_as_of(s: dict, as_of_key) -> dict:
    if as_of_key is None:
        return s
    s = dict(s)
    s["points"] = [p for p in s["points"] if mq._period_sort_key(p["period"]) <= as_of_key]
    s["conflicts"] = [c for c in s["conflicts"]
                      if mq._period_sort_key(c["period"]) <= as_of_key]
    return s


def _filter_coverage_by_as_of(cov: dict, as_of_key) -> dict:
    """将覆盖摘要限制到同一个历史快照，避免 coverage 使用未来时期。"""
    if as_of_key is None:
        return cov
    requested = [p for p in cov["requested_periods"]
                 if mq._period_sort_key(p) <= as_of_key]
    records = []
    for old in cov["series"]:
        r = dict(old)
        observed = [p for p in old["observed_periods"]
                    if mq._period_sort_key(p) <= as_of_key]
        conflicts = [p for p in old["conflict_periods"]
                     if mq._period_sort_key(p) <= as_of_key]
        observed = _sort_periods(observed)
        r["first_period"] = observed[0] if observed else None
        r["last_period"] = observed[-1] if observed else None
        r["observed_periods"] = observed
        r["requested_periods"] = list(requested)
        r["covered_count"] = len(observed)
        r["missing_periods"] = [p for p in requested if p not in observed]
        r["coverage_rate"] = (len(observed) / len(requested)) if requested else None
        r["conflict_periods"] = conflicts
        flags = [f for f in old.get("status_flags", [])
                 if f != "has_conflicts"]
        if conflicts:
            flags.append("has_conflicts")
        if not observed:
            status = "no_observation"
        elif len(observed) == 1:
            status = "single_point"
        elif requested:
            status = "full_coverage" if not r["missing_periods"] else "partial_coverage"
        else:
            gaps = mq._internal_gaps(observed, old["period_granularity"])
            if gaps is None:
                flags.append("internal_gap_check_skipped")
                gaps = []
            r["internal_gaps"] = gaps
            status = ("multi_point_with_internal_gaps" if gaps
                      else "multi_point_no_internal_gap")
        r["status"] = status
        r["status_flags"] = list(dict.fromkeys(flags))
        records.append(r)
    groups = mq._city_metric_summary(records, requested)
    summary = dict(cov["summary"])
    summary["overall_status"] = "no_observation" if not records else "ok"
    summary["series_count"] = len(records)
    summary["actual_series_count"] = sum(1 for r in records if not r["is_forecast"])
    summary["forecast_series_count"] = sum(1 for r in records if r["is_forecast"])
    summary["status_counts"] = mq._status_counts(records)
    summary["series_with_conflicts"] = sum(
        1 for r in records if "has_conflicts" in r["status_flags"])
    summary["conflict_period_count"] = sum(len(r["conflict_periods"]) for r in records)
    summary["insufficient_series_count"] = sum(
        1 for r in records if r["status"] in ("single_point", "forecast_only", "no_observation"))
    summary["empty_city_series_count"] = sum(1 for r in records if not r["city"])
    return {
        **cov,
        "filters": {**cov["filters"], "periods": requested or None},
        "period_granularity": cov["period_granularity"],
        "has_explicit_window": bool(requested),
        "requested_periods": requested,
        "series": records,
        "city_metric_summary": groups,
        "summary": summary,
    }


def _validate_window(period_start, period_end):
    if (period_start is None) != (period_end is None):
        raise ValueError("period_start 与 period_end 必须同时给出")
    if period_start is not None:
        g1, g2 = mq._period_granularity(period_start), mq._period_granularity(period_end)
        if not g1 or not g2:
            raise ValueError(f"时期格式非法: {period_start!r}, {period_end!r}"
                             "（支持 2026-03 / 2026Q1 / 2026-H1 / 2026）")
        if g1 != g2:
            raise ValueError(f"period_start({g1}) 与 period_end({g2}) 粒度不一致")


def assess_market_phase(
    *,
    city: str = "杭州",
    as_of: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    include_forecasts: bool = False,
    db_path: Path = DB,
) -> dict:
    """规则型市场阶段评估：阶段 + 支持证据 + 反向证据 + 置信度 + 风险 + 观察项。

    只读（mode=ro）、零外部 API、零黑箱评分；阶段仅由实际观测（is_forecast=0）
    计算；include_forecasts=True 时预测只作为独立前瞻信号展示。
    """
    if not isinstance(city, str) or not city.strip():
        raise ValueError("city 必须是非空字符串")
    _validate_window(period_start, period_end)
    as_of_key = None
    if as_of is not None:
        if not mq._period_granularity(as_of):
            raise ValueError(f"as_of 格式非法: {as_of!r}（支持 2026-03 / 2026Q1 / 2026-H1 / 2026）")
        as_of_key = mq._period_sort_key(mq._normalize_period(as_of))

    warnings: list[str] = []
    res = mq.query_metric_series(
        city=city, period_start=period_start, period_end=period_end,
        include_forecasts=False, db_path=db_path)
    _dedup_extend(warnings, res["warnings"])
    series = [_filter_by_as_of(s, as_of_key) for s in res["series"]]

    signals = _build_core_signals(series)
    signals.append(_core_city_linkage(
        city, series, period_start, period_end, as_of_key, db_path, warnings))
    forecast_sig = None
    if include_forecasts:
        forecast_sig = _forecast_signal(
            city, period_start, period_end, as_of_key, db_path, warnings)
        signals.append(forecast_sig)

    cov = mq.summarize_series_coverage(
        city=city, period_start=period_start, period_end=period_end,
        actual_only=True, db_path=db_path)
    cov = _filter_coverage_by_as_of(cov, as_of_key)
    core_cov = [g for g in cov["city_metric_summary"]
                if g["metric_name"] in CORE_METRIC_NAMES]
    covered_metrics = {g["metric_name"] for g in core_cov}

    effective_as_of = as_of
    if effective_as_of is None:
        all_periods = [p for s in series
                       for p in [x["period"] for x in s["points"]]
                       + [c["period"] for c in s["conflicts"]]]
        if all_periods:
            effective_as_of = max(all_periods, key=lambda p: (mq._period_sort_key(p), p))

    state = _build_rule_state(signals, warnings)
    phase_code, trace = _decide_phase(state)
    confidence = _confidence(state)
    risks = _risk_signals(state, cov) + _forecast_risks(state, forecast_sig)

    supporting = [
        {"signal_code": s["code"], "direction": s["direction"],
         "evidence": [e for o in s["observations"] for e in o.get("evidence", [])][:12]}
        for s in signals if s["direction"] == "improving" and s["family"] != "forecast"]
    counter = [
        {"signal_code": s["code"], "direction": s["direction"],
         "evidence": [e for o in s["observations"] for e in o.get("evidence", [])][:12]}
        for s in signals if s["direction"] in ("deteriorating", "mixed")
        and s["family"] != "forecast"]

    missing_core = [m for m in CORE_METRIC_NAMES if m not in covered_metrics]
    if phase_code == "data_insufficient":
        gaps = []
        if state["price"]["n_periods"] < MIN_PERIODS_FOR_CHANGE:
            gaps.append("价格变化类指标（mom_change_pct/yoy_change_pct）不足 2 个实际时期")
        if state["volume"]["n_periods"] < MIN_PERIODS_FOR_CHANGE:
            gaps.append("成交类指标（secondary_volume_units 等）不足 2 个实际时期")
        if missing_core:
            gaps.append(f"完全缺失的核心指标: {missing_core}")
        if state["series_conflict_count"]:
            gaps.append(f"存在 {state['series_conflict_count']} 条同期多值冲突序列")
        warnings.append("阶段=data_insufficient：" + ("；".join(gaps) or "未满足任何更高阶段的证据门槛"))

    if period_end is not None and as_of is not None:
        effective_period_end = (period_end if mq._period_sort_key(period_end) <= as_of_key
                                else as_of)
    else:
        effective_period_end = period_end or as_of or effective_as_of

    return {
        "city": city,
        "as_of": effective_as_of,
        "filters": {
            "period_start": period_start, "period_end": period_end,
            "effective_period_end": effective_period_end,
            "include_forecasts": include_forecasts, "basis": "actual_only",
        },
        "phase": {
            "code": phase_code,
            "label": PHASE_LABELS[phase_code],
            "confidence": confidence,
            "basis": "rule_based",
            "rule_version": RULE_VERSION,
            "gate_order": list(PHASE_GATE_ORDER),
            "rule_evaluation": trace,
        },
        "signals": signals,
        "supporting_evidence": supporting,
        "counter_evidence": counter,
        "coverage": {
            "requested_periods": cov["requested_periods"],
            "has_explicit_window": cov["has_explicit_window"],
            "core_metrics": [
                {k: g[k] for k in ("metric_name", "status", "series_count",
                                   "actual_series_count", "forecast_series_count",
                                   "first_period", "last_period", "institutions",
                                   "status_flags")}
                for g in core_cov],
            "missing_core_metrics": missing_core,
            "summary": {k: cov["summary"][k] for k in (
                "series_count", "actual_series_count", "forecast_series_count",
                "insufficient_series_count", "series_with_conflicts",
                "conflict_period_count")},
        },
        "risk_signals": risks,
        "watch_next": _watch_next(state),
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# 只读 CLI
# ---------------------------------------------------------------------------

def _render_text(rep: dict) -> str:
    ph = rep["phase"]
    lines = [
        "市场阶段评估（规则型/只读；只整理当前证据，不含投资或购房建议）",
        f"城市: {rep['city']}    as_of: {rep['as_of']}    规则版本: {ph['rule_version']}",
        f"阶段: {ph['code']}（{ph['label']}）    置信度: {ph['confidence']}"
        f"（证据充分程度，非判断正确概率）",
        "",
        "信号明细:",
    ]
    for s in rep["signals"]:
        line = (f"  - [{s['family']}/{s['code']}] {s['status']}"
                f" 方向={s['direction']} 强度={s['strength']}")
        if s["metric_name"] and s["periods"]:
            line += f"  ({s['metric_name']}: {','.join(s['periods'])})"
        lines.append(line)
        for w in s["warnings"]:
            lines.append(f"      warning: {w}")
    if rep["supporting_evidence"]:
        lines.append("")
        lines.append("支持证据: " + "、".join(e["signal_code"] for e in rep["supporting_evidence"]))
    if rep["counter_evidence"]:
        lines.append("反向证据: " + "、".join(e["signal_code"] for e in rep["counter_evidence"]))
    if rep["risk_signals"]:
        lines.append("")
        lines.append("风险信号:")
        lines += [f"  - {r['code']}: {r['warning']}" for r in rep["risk_signals"]]
    if rep["watch_next"]:
        lines.append("")
        lines.append("下一步观察:")
        lines += [f"  - {w['metric']}（{w['segment']}）: {w['reason']}" for w in rep["watch_next"]]
    if rep["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines += [f"  - {w}" for w in rep["warnings"]]
    lines.append("")
    lines.append(f"免责声明: {rep['disclaimer']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="市场阶段评估（规则型/只读）：只整理当前数据证据，不做购房建议")
    ap.add_argument("--city", default="杭州", help="城市（默认 杭州）")
    ap.add_argument("--as-of", dest="as_of", help="截止时期（如 2026-08；晚于该期的数据不参与）")
    ap.add_argument("--period-start", help="时期窗口起点（需与 --period-end 同粒度）")
    ap.add_argument("--period-end", help="时期窗口终点")
    ap.add_argument("--include-forecasts", action="store_true",
                    help="附带独立前瞻信号（机构预测不参与实际阶段计算）")
    ap.add_argument("--db", default=str(DB), help="数据库路径（默认 data/rag.db，只读打开）")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 而非文本摘要")
    args = ap.parse_args(argv)

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.exists():
        ap.error(f"数据库不存在: {db_path}")

    rep = assess_market_phase(
        city=args.city, as_of=args.as_of,
        period_start=args.period_start, period_end=args.period_end,
        include_forecasts=args.include_forecasts, db_path=db_path)
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else _render_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
