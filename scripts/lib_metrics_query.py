"""指标层查询：问答时把指标库中相关行带给生成层。

策略：识别问题中的城市 + 时期（月份 / 季度 / 上下半年 / 月份范围 / 裸年份）+ 指标关键词，
只拉取匹配的指标行；无任何时间条件时取筛选后数据最近 6 个不同 period，行数上限 40。
城市和指标关键词都没有、或点名了指标库不覆盖的城市时返回空列表，交给文档检索。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from urllib.parse import quote as _urlquote

from lib_db import connect
from lib_paths import DB

RECENT_PERIODS = 6  # 无任何时间条件时取筛选后数据最近的 N 个不同 period
CITIES = ["上海", "北京", "深圳", "广州", "杭州", "香港", "全国", "一线", "二线"]

# 查询明确点名、但指标库暂不覆盖的城市：命中即返回空，让位给文档检索回答“未覆盖”，
# 避免其他城市的数据被当成该城的答案。与 CITIES 一样需人工维护。
UNSUPPORTED_CITIES = [
    "成都", "南京", "天津", "武汉", "重庆", "西安", "长沙", "郑州", "合肥",
    "苏州", "宁波", "厦门", "青岛", "济南", "福州", "昆明", "沈阳", "大连",
    "哈尔滨", "长春", "石家庄", "太原", "南昌", "贵阳", "南宁", "兰州",
    "乌鲁木齐", "海口", "三亚", "无锡", "常州", "温州", "徐州", "烟台",
    "佛山", "东莞", "珠海", "中山", "惠州",
]

# 指标关键词 → metric_name：命中任一关键词的指标才会进入查询。
# 关键词必须足够特异（用“新房成交”而非“新房”），宁可漏掉交给文档检索，
# 也不要让无关指标混进 LLM prompt。
METRIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "avg_price": ("均价", "平均价格", "成交均价", "成交价", "房价", "价格指数", "百城价格", "价格中位数"),
    "mom_change_pct": ("环比",),
    "yoy_change_pct": ("同比",),
    "new_home_volume_units": ("新房成交", "新房套数", "新房销量", "一手房成交", "新盘成交"),
    "new_home_volume_area": ("成交面积", "销售面积", "新房面积"),
    "secondary_volume_units": ("二手房成交", "二手成交", "二手房套数", "二手房销量", "二手住宅成交"),
    "inventory_months": ("库存", "去化"),
    "land_transaction_value": ("土地成交金额", "土地出让金", "土地市场", "土地成交", "土拍", "卖地"),
    "premium_rate_pct": ("溢价率", "溢价"),
    "listing_units": ("挂牌",),
    "price_bottom_timing": ("触底", "见底", "底部", "止跌回稳"),
    "price_change_forecast_pct": ("房价预测", "价格预测", "涨跌幅预测", "房价走势", "涨多少", "跌多少", "会涨", "会跌"),
    "mortgage_rate": ("房贷利率", "按揭利率", "抵押贷款利率", "房贷成本"),
    "provident_fund_rate": ("公积金",),
    "rent_level": ("租金",),
    "rental_yield": ("租售比", "租金回报", "租金收益", "租金收益率", "回报率"),
    "developer_sales_amount": ("开发商销售", "房企销售", "房企销售额", "百强房企", "销售额"),
}

_CN_QUARTER = {"一": "1", "二": "2", "三": "3", "四": "4"}
# 月份范围：“2026年3月到8月”“2026年3月-8月”“2025年11月至2026年3月”
_RE_MONTH_RANGE = re.compile(
    r"(?<!\d)(20\d{2})\s*年?\s*(\d{1,2})\s*月?份?\s*[到至~—–-]\s*(?:(20\d{2})\s*年?\s*)?(\d{1,2})\s*月"
)
# 单个时期：年月 / 中文或 ASCII 季度 / 上下半年（“2026年3月”“2026年一季度”“2026Q1”“2026年下半年”）
_RE_PERIOD = re.compile(
    r"(?<!\d)(20\d{2})\s*年?\s*"
    r"(?:(?P<half>上|下)半年|第?(?P<cq>[一二三四1-4])季度|[Qq](?P<aq>[1-4])|(?P<m>\d{1,2})月)"
)


def _expand_month_range(y1: int, m1: int, y2: int, m2: int) -> list[str]:
    out = []
    y, m = y1, m1
    while (y, m) <= (y2, m2) and len(out) < 24:  # 上限防御：异常输入不展开成巨列表
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def parse_periods(query: str) -> list[str]:
    """从问题中解析时期，归一成库内格式：2026-03 / 2026Q1 / 2026-H1 / 2026-H2。"""
    rm = _RE_MONTH_RANGE.search(query)
    if rm:
        y1, m1 = int(rm.group(1)), int(rm.group(2))
        y2, m2 = int(rm.group(3) or rm.group(1)), int(rm.group(4))
        if 1 <= m1 <= 12 and 1 <= m2 <= 12:
            if (y1, m1) > (y2, m2):
                y1, m1, y2, m2 = y2, m2, y1, m1
            return _expand_month_range(y1, m1, y2, m2)
    out: list[str] = []
    for m in _RE_PERIOD.finditer(query):
        y = m.group(1)
        if m.group("half"):
            out.append(f"{y}-H{1 if m.group('half') == '上' else 2}")
        elif m.group("cq"):
            out.append(f"{y}Q{_CN_QUARTER.get(m.group('cq'), m.group('cq'))}")
        elif m.group("aq"):
            out.append(f"{y}Q{m.group('aq')}")
        elif m.group("m") and 1 <= int(m.group("m")) <= 12:
            out.append(f"{y}-{int(m.group('m')):02d}")
    return list(dict.fromkeys(out))


# 裸年份：年份后不跟月/季度/半年（Q 形式无“年”字，不会命中本式）
_RE_BARE_YEAR = re.compile(
    r"(?<!\d)(20\d{2})\s*年(?!\s*(?:\d{1,2}\s*月|第?[一二三四1-4]\s*季度|[上下]半年|[Qq][1-4]))"
)


def parse_years(query: str) -> list[str]:
    """解析未绑定到月/季度/半年的裸年份，如“2027年杭州房价会涨多少”→ ["2027"]。

    年份已附着具体 period（“2026年8月”）时由 parse_periods 表达，这里返回空。
    """
    return list(dict.fromkeys(m.group(1) for m in _RE_BARE_YEAR.finditer(query)))


def _period_sort_key(period: str) -> tuple[int, int]:
    """混合格式 period 的排序键 (截止年, 截止月)，值越大越新。

    2026-03 ≈ 2026Q1 → (2026, 3)；2026-H1 ≈ 2026Q2 → (2026, 6)；
    2026 ≈ 2026Q4 ≈ 2026-H2 ≈ 2026-12 → (2026, 12)；无法解析 → (0, 0) 排最老。
    """
    try:
        if "Q" in period:
            y, q = period.split("Q")
            return int(y), int(q) * 3
        if "-H" in period:
            y, h = period.split("-H")
            return int(y), int(h) * 6
        y, _, m = period.partition("-")
        return int(y), int(m or 12)
    except ValueError:
        return 0, 0


def _keyword_haystack(query: str) -> str:
    """指标关键词的匹配文本：去掉时间表达式和“的”。

    “杭州二手房2026年3月到8月的成交量走势” 中“二手房”与“成交”被时间表达隔开，
    直接子串匹配会漏；剥掉时间表达和“的”后恢复成“二手房成交量走势”。
    """
    q = _RE_MONTH_RANGE.sub("", query)
    q = _RE_PERIOD.sub("", q)
    return q.replace("的", "")


def query_metrics(query: str, limit: int = 40) -> list[dict]:
    if any(c in query for c in UNSUPPORTED_CITIES):
        return []  # 点名了指标库不覆盖的城市：交给文档检索回答“未覆盖”
    cities = [c for c in CITIES if c in query]
    periods = parse_periods(query)
    years = parse_years(query)
    haystack = _keyword_haystack(query)
    metric_names = [n for n, kws in METRIC_KEYWORDS.items() if any(kw in haystack for kw in kws)]
    if not cities and not metric_names:
        return []  # 既无城市也无指标关键词：交给文档检索
    sql = "SELECT * FROM metrics WHERE 1=1"
    params: list = []
    if cities:
        sql += f" AND city IN ({','.join('?' * len(cities))})"
        params += cities
    if metric_names:
        sql += f" AND metric_name IN ({','.join('?' * len(metric_names))})"
        params += metric_names
    period_clauses = []
    if periods:
        period_clauses.append(f"period IN ({','.join('?' * len(periods))})")
        params += periods
    if years:  # 裸年份按前缀匹配，覆盖 2027 / 2027-05 / 2027Q2 / 2027-H1 等格式
        period_clauses.append(" OR ".join("period LIKE ?" for _ in years))
        params += [f"{y}%" for y in years]
    if period_clauses:
        sql += f" AND ({' OR '.join(period_clauses)})"
    conn = connect(DB)
    try:
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()
    if not periods and not years:  # 无任何时间条件：取筛选后数据最近 RECENT_PERIODS 个不同 period
        ranked = sorted({r["period"] for r in rows}, key=lambda p: (_period_sort_key(p), p), reverse=True)
        keep = set(ranked[:RECENT_PERIODS])
        rows = [r for r in rows if r["period"] in keep]
    rows.sort(key=lambda r: (r["city"] or "", r["metric_name"]))  # 次级键（稳定排序）
    rows.sort(key=lambda r: _period_sort_key(r["period"]), reverse=True)  # 主键：时期新 → 旧
    return rows[:limit]


def format_metrics(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = []
    for r in rows:
        val = r["value"] if r["value"] is not None else r["value_text"]
        comp = f"，{r['comparison_type']} {r['comparison_value']}%" if r["comparison_type"] else ""
        fc = "（预测）" if r["is_forecast"] else ""
        lines.append(
            f"- {r['period']} {r['city']} {r['metric_name']}: {val} {r['unit']}{comp} "
            f"[{r['institution']}{fc}] 原文: {r['source_quote'][:100]}"
        )
    return "以下是指标库中的结构化数据（比研报正文更精确，优先采用）：\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 3A：多来源时间序列查询
#
# 与 query_metrics()（面向 LLM prompt 的行拉取）不同，query_metric_series()
# 面向未来的 Web 看板 / 数据覆盖报告 / 市场阶段模块：按来源分组返回原始点
# 序列、缺失时期与同期多值冲突。本层不做平均、不取舍来源、不输出趋势或
# 市场阶段结论（属于 Step 3B/3D）。
#
# 连接安全：全程 file:...?mode=ro 只读 URI 连接，不经过 lib_db.connect()，
# 不触发 schema 创建 / 迁移 / commit；旧 schema（metrics 无 source_file 列）
# 降级为 source_file=None，绝不改写数据库，查询结束即关闭连接。
#
# 冲突约定：同一 series 分组内同一 period 出现多条记录时，该 period 的所有
# 点从 points 移入 conflicts，并在 warnings 标明——不平均、不取第一条、
# 不覆盖、不丢弃。
# ---------------------------------------------------------------------------

_RE_P_MONTH = re.compile(r"^20\d{2}-(?:0[1-9]|1[0-2])$")
_RE_P_QUARTER = re.compile(r"^20\d{2}[Qq][1-4]$")
_RE_P_HALF = re.compile(r"^20\d{2}-H[12]$")
_RE_P_YEAR = re.compile(r"^20\d{2}$")

MAX_RANGE_PERIODS = 240  # 时期范围展开的防御上限
EMPTY_CITY_LABEL = "未归一化城市"


class _PeriodRequestError(ValueError):
    """时期请求非法：由 query_metric_series 转成空结果 + warning，不向外抛。"""


def _period_granularity(period: str) -> str | None:
    """严格识别库内时期格式的粒度：month / quarter / half / year；非法返回 None。"""
    if _RE_P_MONTH.match(period):
        return "month"
    if _RE_P_QUARTER.match(period):
        return "quarter"
    if _RE_P_HALF.match(period):
        return "half"
    if _RE_P_YEAR.match(period):
        return "year"
    return None


def _normalize_period(period: str) -> str:
    # 季度统一大写 Q（库内格式）；其余格式原样。须先通过 _period_granularity 校验。
    return period.upper() if _RE_P_QUARTER.match(period) else period


def _expand_period_range(start: str, end: str, granularity: str) -> list[str]:
    """同粒度范围展开（升序，含两端）。月/季/半年/年各自展开，不跨粒度转换。"""
    def idx(p: str) -> int:
        if granularity == "month":
            return int(p[:4]) * 12 + int(p[5:7]) - 1
        if granularity == "quarter":
            return int(p[:4]) * 4 + int(p[-1]) - 1
        if granularity == "half":
            return int(p[:4]) * 2 + int(p[-1]) - 1
        return int(p)  # year

    def fmt(i: int) -> str:
        if granularity == "month":
            return f"{i // 12}-{(i % 12) + 1:02d}"
        if granularity == "quarter":
            return f"{i // 4}Q{(i % 4) + 1}"
        if granularity == "half":
            return f"{i // 2}-H{(i % 2) + 1}"
        return str(i)  # year

    a, b = idx(start), idx(end)
    if a > b:
        a, b = b, a
    if b - a >= MAX_RANGE_PERIODS:
        return []
    return [fmt(i) for i in range(a, b + 1)]


def _resolve_requested_periods(periods, period_start, period_end, warnings):
    """归一时期请求 → (requested list[str] | None, granularity | None)。

    None 表示无时期条件（不按时期过滤）。显式 periods 校验格式、去重后按时期
    排序（稳定，不依赖字符串偶然顺序）；非法项进 warning，全部非法或粒度混合
    抛 _PeriodRequestError。裸年份不展开为月度。
    """
    if periods and (period_start or period_end):
        warnings.append("periods 与 period_start/period_end 同时给出：优先使用显式 periods，范围参数被忽略")
    if periods:
        norm, bad = [], []
        for p in periods:
            p = p.strip() if isinstance(p, str) else ""
            g = _period_granularity(p)
            if g:
                norm.append((_normalize_period(p), g))
            else:
                bad.append(p)
        if bad:
            warnings.append(f"非法时期格式已忽略: {bad}（支持 2026-03 / 2026Q1 / 2026-H1 / 2026）")
        if not norm:
            raise _PeriodRequestError("periods 全部非法，无有效时期可查询")
        grans = {g for _, g in norm}
        if len(grans) > 1:
            raise _PeriodRequestError(
                f"periods 混合了多种粒度 {sorted(grans)}：月/季/半年/年不能放在同一次查询")
        dedup = sorted({p for p, _ in norm}, key=lambda p: (_period_sort_key(p), p))
        return dedup, next(iter(grans))
    if period_start or period_end:
        if not (period_start and period_end):
            raise _PeriodRequestError("period_start 与 period_end 必须同时给出")
        g1, g2 = _period_granularity(period_start), _period_granularity(period_end)
        if not g1 or not g2:
            raise _PeriodRequestError(
                f"period_start/period_end 格式非法: {period_start!r}, {period_end!r}")
        if g1 != g2:
            raise _PeriodRequestError(
                f"period_start({g1}) 与 period_end({g2}) 粒度不一致：范围不得跨越不同粒度")
        expanded = _expand_period_range(_normalize_period(period_start), _normalize_period(period_end), g1)
        if not expanded:
            raise _PeriodRequestError(
                f"时期范围过大或非法: {period_start} ~ {period_end}（上限 {MAX_RANGE_PERIODS} 期）")
        return expanded, g1
    return None, None


def _connect_readonly(db_path) -> sqlite3.Connection:
    """mode=ro 只读连接：不建表、不迁移、不 commit；调用方负责关闭。

    刻意不走 lib_db.connect()——后者会执行 schema/迁移/commit，对只读查询
    是不可接受的副作用。
    """
    path = Path(db_path)
    if not path.is_absolute():
        path = path.resolve()
    conn = sqlite3.connect(f"file:{_urlquote(str(path), safe='/')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _series_point(r: dict) -> dict:
    return {
        "period": r["period"],
        "value": r["value"],
        "value_text": r["value_text"],
        "comparison_type": r["comparison_type"],
        "comparison_value": r["comparison_value"],
        "source_page": r["source_page"],
        "source_quote": r["source_quote"],
        "is_forecast": bool(r["is_forecast"] or 0),
    }


def _series_result(filters, granularity, series, warnings, requested):
    covered, returned, conflict_pts = set(), 0, 0
    for s in series:
        returned += len(s["points"])
        conflict_pts += sum(len(c["points"]) for c in s["conflicts"])
        covered.update(p["period"] for p in s["points"])
        covered.update(c["period"] for c in s["conflicts"])
    return {
        "filters": filters,
        "period_granularity": granularity,
        "series": series,
        "coverage": {
            "requested_periods": list(requested or []),
            "returned_points": returned,
            "conflict_points": conflict_pts,
            "series_count": len(series),
            # 所有 series 都未覆盖（points 与 conflicts 均无）的请求时期数
            "missing_period_count": len([p for p in requested or [] if p not in covered]),
        },
        "warnings": warnings,
    }


def query_metric_series(
    *,
    city: str | None = None,
    metric_name: str | None = None,
    institution: str | None = None,
    source_file: str | None = None,
    periods: list[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    include_forecasts: bool = True,
    db_path: Path = DB,
) -> dict:
    """多来源时间序列查询：按来源分组返回原始点，不做平均/取舍/趋势结论。

    series 分组键：(institution, source_file, city, metric_name, segment, unit,
    comparison_type, is_forecast, period_granularity)——在任务要求的最小键之外
    增加 comparison_type（环比/同比语义不同不可合并）与粒度（月/季/半年/年
    不混在同一 series）；不含 comparison_value（逐期合法变化）。

    city 精确匹配；city=None 不过滤（可能含 city 为空的记录，其 city_label 为
    "未归一化城市"，绝不自动归为杭州/全国）。source_file 精确匹配；旧 schema
    无该列时降级为 None，给了 source_file 筛选则返回空结果 + warning。
    """
    warnings: list[str] = []
    filters = {
        "city": city,
        "metric_name": metric_name,
        "institution": institution,
        "source_file": source_file,
        "periods": None,
        "period_start": period_start,
        "period_end": period_end,
        "include_forecasts": include_forecasts,
    }
    try:
        requested, req_granularity = _resolve_requested_periods(periods, period_start, period_end, warnings)
    except _PeriodRequestError as e:
        warnings.append(str(e))
        return _series_result(filters, None, [], warnings, requested=[])
    filters["periods"] = requested

    conn = _connect_readonly(db_path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(metrics)")}
        if not cols:
            warnings.append("metrics 表不存在，返回空结果")
            return _series_result(filters, req_granularity, [], warnings, requested)
        has_source_file = "source_file" in cols
        if source_file is not None and not has_source_file:
            warnings.append("当前数据库 schema 无 source_file 列，source_file 筛选不可用，返回空结果")
            return _series_result(filters, req_granularity, [], warnings, requested)
        select_sf = "source_file" if has_source_file else "NULL AS source_file"
        sql = (
            f"SELECT institution, {select_sf}, period, city, segment, metric_name,"
            " value, value_text, unit, comparison_type, comparison_value, is_forecast,"
            " source_page, source_quote FROM metrics WHERE 1=1"
        )
        params: list = []
        if city is not None:
            sql += " AND city = ?"
            params.append(city)
        if metric_name is not None:
            sql += " AND metric_name = ?"
            params.append(metric_name)
        if institution is not None:
            sql += " AND institution = ?"
            params.append(institution)
        if source_file is not None:
            sql += " AND source_file = ?"
            params.append(source_file)
        if requested:
            sql += f" AND period IN ({','.join('?' * len(requested))})"
            params += requested
        if not include_forecasts:
            sql += " AND COALESCE(is_forecast, 0) = 0"
            warnings.append("include_forecasts=False：预测记录（is_forecast=1）已过滤，仅返回实际观测")
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()

    if not rows:
        warnings.append("无匹配记录：请核对城市/指标/时期取值与 institution/source_file 拼写（均为精确匹配）")

    # Python 中分组：结果语义不依赖数据库物理行顺序，不使用 AVG/MIN/MAX/DISTINCT
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        gran = _period_granularity(r["period"]) or "unknown"
        key = (
            r["institution"], r["source_file"], r["city"], r["metric_name"],
            r["segment"], r["unit"], r["comparison_type"],
            1 if r["is_forecast"] else 0, gran,
        )
        groups.setdefault(key, []).append(r)

    series = []
    for key in sorted(groups, key=lambda k: tuple("" if v is None else str(v) for v in k)):
        inst, sf, c, mn, seg, unit, ct, fc, gran = key
        by_period: dict[str, list[dict]] = {}
        for r in groups[key]:
            by_period.setdefault(r["period"], []).append(_series_point(r))
        conflicts, points = [], []
        for p in sorted(by_period, key=lambda p: (_period_sort_key(p), p)):
            pts = by_period[p]
            if len(pts) > 1:
                conflicts.append({"period": p, "points": pts})  # 移出 points，不平均不取舍
            else:
                points.append(pts[0])
        source_key = "|".join("" if v is None else str(v) for v in key)
        if conflicts:
            cps = "、".join(c["period"] for c in conflicts)
            warnings.append(
                f"series [{source_key}] 存在同期多值（{cps}）：相关时期已移入 conflicts，"
                "未平均、未取第一条、未丢弃")
        series.append({
            "source_key": source_key,
            "institution": inst,
            "source_file": sf,
            "city": c,
            "city_label": c if c else EMPTY_CITY_LABEL,
            "metric_name": mn,
            "segment": seg,
            "unit": unit,
            "comparison_type": ct,
            "is_forecast": bool(fc),
            "period_granularity": gran,
            "points": points,
            "missing_periods": [p for p in requested or [] if p not in by_period],
            "conflicts": conflicts,
        })

    if include_forecasts and any(s["is_forecast"] for s in series):
        warnings.append("结果包含预测数据：is_forecast=true 的 series 为预测，非实际观测趋势")
    grans = {s["period_granularity"] for s in series}
    granularity = req_granularity or (next(iter(grans)) if len(grans) == 1 else ("mixed" if grans else None))
    return _series_result(filters, granularity, series, warnings, requested)


def query_metric_series_nl(query: str, *, include_forecasts: bool = True, db_path: Path = DB) -> dict:
    """薄自然语言适配层：只负责从问题解析城市/指标/时期，转交结构化接口。

    不负责来源合并、不做平均、不生成市场判断、不调用 LLM。多城市/多指标
    命中时不擅自选择，放弃该维筛选并进 warning；裸年份不在此展开，需要时
    请调用方用结构化 periods 显式传入。
    """
    cities = [c for c in CITIES if c in query]
    haystack = _keyword_haystack(query)
    metric_names = [n for n, kws in METRIC_KEYWORDS.items() if any(kw in haystack for kw in kws)]
    result = query_metric_series(
        city=cities[0] if len(cities) == 1 else None,
        metric_name=metric_names[0] if len(metric_names) == 1 else None,
        periods=parse_periods(query) or None,
        include_forecasts=include_forecasts,
        db_path=db_path,
    )
    if len(cities) > 1:
        result["warnings"].append(f"问题提及多个城市 {cities}：未做城市筛选，返回全部匹配城市")
    if len(metric_names) > 1:
        result["warnings"].append(f"命中多个指标 {metric_names}：未做指标筛选，返回全部匹配指标")
    return result


# ---------------------------------------------------------------------------
# Step 3B：数据覆盖与缺口摘要
#
# 在 query_metric_series() 之上做纯函数式汇总：不维护第二套 SQL、时期解析
# 或来源分组逻辑，只消费其结构化结果。本层只描述数据存在性与完整性
# （覆盖了哪些时期、缺哪些、是否单点/纯预测/有冲突），不判断上涨/下跌、
# 市场阶段、风险等级或购房时机；不插值、不填 0、不合并来源。
#
# 状态定义：
#   有明确请求窗口（requested_periods 非空）：
#     full_coverage     覆盖全部请求期（且无缺失）
#     partial_coverage  覆盖 ≥2 个请求期但有缺失
#     single_point      仅覆盖 1 个请求期（优先于 full_coverage：单点不伪装连续）
#     forecast_only     整条 series 为预测（is_forecast=True），预测不当实际观测
#     no_observation    没有任何覆盖（防御性兜底；底层 SQL 已过滤空组，正常不可达）
#   无明确窗口：
#     single_point / forecast_only / no_observation 同上
#     multi_point_no_internal_gap    多个观测期且最小~最大期之间无内部缺口
#     multi_point_with_internal_gaps 最小~最大期之间存在内部缺口
#   无窗口时绝不把已有最小/最大期当成完整请求窗口：不给 full_coverage，
#   coverage_rate 为 None，missing_periods 为空。
#   冲突期计为已覆盖（不算缺失），以 has_conflicts 状态位标记。
#   城市-指标汇总层复用同一口径，另加 mixed_granularity（组内粒度混合或
#   不可识别时无法诚实判断内部缺口，不谎称无缺口）。
# ---------------------------------------------------------------------------

INSUFFICIENT_STATUSES = ("single_point", "forecast_only", "no_observation")


def _observed_periods(s: dict) -> list[str]:
    """series 实际覆盖的时期（points + 冲突期，冲突期计为已覆盖），按时期排序。"""
    periods = [p["period"] for p in s["points"]]
    periods += [c["period"] for c in s["conflicts"]]
    return sorted(periods, key=lambda p: (_period_sort_key(p), p))


def _internal_gaps(observed: list[str], granularity: str) -> list[str] | None:
    """无窗口时的内部缺口：最小~最大观测期按自身粒度展开后缺失的时期。

    粒度无法识别（unknown）或多格式混杂时返回 None——表示"无法计算"，
    由调用方加状态位，不得谎报为无缺口。
    """
    if len(observed) < 2:
        return []
    if granularity not in ("month", "quarter", "half", "year"):
        return None
    expanded = _expand_period_range(observed[0], observed[-1], granularity)
    if not expanded:  # 超出展开上限等异常：不谎报
        return None
    seen = set(observed)
    return [p for p in expanded if p not in seen]


def _coverage_series_record(s: dict, requested: list[str]) -> dict:
    observed = _observed_periods(s)
    conflict_periods = [c["period"] for c in s["conflicts"]]
    flags: list[str] = []
    if conflict_periods:
        flags.append("has_conflicts")  # 冲突期保留在 conflicts，不算缺失

    if requested:
        missing = list(s["missing_periods"])
        gaps: list[str] | None = []  # 窗口内缺口即 missing_periods，不重复计算
        rate = len(observed) / len(requested)
    else:
        missing = []  # 无窗口：不把观测最小/最大期当成请求窗口
        gaps = _internal_gaps(observed, s["period_granularity"])
        rate = None
        if gaps is None:
            flags.append("internal_gap_check_skipped")  # 粒度不可识别，未谎报
            gaps = []

    if s["is_forecast"]:
        status = "forecast_only"
    elif not observed:
        status = "no_observation"
    elif len(observed) == 1:
        status = "single_point"
    elif requested:
        status = "full_coverage" if not missing else "partial_coverage"
    else:
        status = "multi_point_with_internal_gaps" if gaps else "multi_point_no_internal_gap"

    return {
        "source_key": s["source_key"],
        "city": s["city"],
        "city_label": s["city_label"],
        "metric_name": s["metric_name"],
        "institution": s["institution"],
        "source_file": s["source_file"],
        "segment": s["segment"],
        "unit": s["unit"],
        "comparison_type": s.get("comparison_type"),
        "is_forecast": s["is_forecast"],
        "period_granularity": s["period_granularity"],
        "first_period": observed[0] if observed else None,
        "last_period": observed[-1] if observed else None,
        "observed_periods": observed,
        "requested_periods": list(requested),
        "covered_count": len(observed),
        "missing_periods": missing,
        "coverage_rate": rate,
        "internal_gaps": gaps,
        "conflict_periods": conflict_periods,
        "status": status,
        "status_flags": flags,
    }


def _status_counts(records) -> dict:
    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts


def _group_status(records, requested) -> str:
    """城市-指标汇总层状态：按各 series 覆盖的并集计算，口径与序列层一致。"""
    if any(r["is_forecast"] for r in records) and all(r["is_forecast"] for r in records):
        return "forecast_only"
    covered = set()
    for r in records:
        if not r["is_forecast"]:
            covered.update(r["observed_periods"])
    if not covered:
        return "no_observation" if not records else "forecast_only"
    if requested:
        if len(covered) == 1:
            return "single_point"
        missing = [p for p in requested if p not in covered]
        return "full_coverage" if not missing else "partial_coverage"
    if len(covered) == 1:
        return "single_point"
    grans = {r["period_granularity"] for r in records if not r["is_forecast"]}
    if len(grans) == 1:
        gaps = _internal_gaps(sorted(covered, key=lambda p: (_period_sort_key(p), p)),
                              next(iter(grans)))
        if gaps:
            return "multi_point_with_internal_gaps"
        if gaps == []:
            return "multi_point_no_internal_gap"
    # 粒度混合或不可识别：无法诚实判断内部缺口，不谎称无缺口
    return "mixed_granularity"


def _city_metric_summary(records, requested) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault((r["city_label"], r["metric_name"], r["city"]), []).append(r)
    out = []
    for (label, mn, city), rs in sorted(groups.items()):
        covered = set()
        for r in rs:
            covered.update(r["observed_periods"])
        flags: list[str] = []
        if any("has_conflicts" in r["status_flags"] for r in rs):
            flags.append("has_conflicts")
        if all(r["status"] == "forecast_only" for r in rs):
            flags.append("forecast_only")
        if not city:
            flags.append("empty_city")
        union_missing = [p for p in requested if p not in covered] if requested else []
        observed_all = sorted(covered, key=lambda p: (_period_sort_key(p), p))
        out.append({
            "city": city,
            "city_label": label,
            "metric_name": mn,
            "series_count": len(rs),
            "actual_series_count": sum(1 for r in rs if not r["is_forecast"]),
            "forecast_series_count": sum(1 for r in rs if r["is_forecast"]),
            "institutions": sorted({str(r["institution"]) for r in rs if r["institution"]}),
            "source_files": sorted({str(r["source_file"]) for r in rs if r["source_file"]}),
            "first_period": observed_all[0] if observed_all else None,
            "last_period": observed_all[-1] if observed_all else None,
            "union_covered_count": len(covered) if requested else None,
            "union_missing_periods": union_missing,
            "union_coverage_rate": (len(covered) / len(requested)) if requested else None,
            "series_with_conflicts": sum(1 for r in rs if "has_conflicts" in r["status_flags"]),
            "status_counts": _status_counts(rs),
            "status": _group_status(rs, requested),
            "status_flags": flags,
        })
    return out


def summarize_series_coverage(
    *,
    city: str | None = None,
    metric_name: str | None = None,
    institution: str | None = None,
    source_file: str | None = None,
    periods: list[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    actual_only: bool = False,
    db_path: Path = DB,
) -> dict:
    """数据覆盖与缺口摘要：三层输出（序列层 / 城市-指标汇总层 / 全局汇总层）。

    纯消费 query_metric_series() 的结果；actual_only=True 时只统计实际观测
    （预测在底层已过滤并带 warning）。只读，不写库，不调用任何外部服务。
    """
    res = query_metric_series(
        city=city, metric_name=metric_name, institution=institution,
        source_file=source_file, periods=periods,
        period_start=period_start, period_end=period_end,
        include_forecasts=not actual_only, db_path=db_path,
    )
    requested = list(res["coverage"]["requested_periods"])
    warnings = list(res["warnings"])

    records = [_coverage_series_record(s, requested) for s in res["series"]]
    groups = _city_metric_summary(records, requested)

    status_counts = _status_counts(records)
    empty_city_n = sum(1 for r in records if not r["city"])
    conflict_n = sum(1 for r in records if "has_conflicts" in r["status_flags"])
    insufficient = [r for r in records if r["status"] in INSUFFICIENT_STATUSES]

    # “缺失最多”= 覆盖不足排序，仅供定位数据缺口，不代表风险最大
    def _lack(r):
        return len(r["missing_periods"]) if requested else len(r["internal_gaps"])

    least_covered = [
        {
            "source_key": r["source_key"], "city_label": r["city_label"],
            "metric_name": r["metric_name"], "institution": r["institution"],
            "status": r["status"],
            "missing_count": len(r["missing_periods"]) if requested else None,
            "internal_gap_count": None if requested else len(r["internal_gaps"]),
        }
        for r in sorted(records, key=lambda r: (-_lack(r), r["source_key"]))
        if _lack(r) > 0
    ][:5]

    if empty_city_n:
        warnings.append(
            f"存在 {empty_city_n} 条 city 为空的序列（未归一化城市）：已单独标记，未归入杭州/全国或任何城市")
    if least_covered:
        warnings.append("“缺失最多”列表仅表示数据覆盖不足，不代表市场风险最大")
    if insufficient:
        warnings.append(
            f"{len(insufficient)} 条序列状态为 single_point/forecast_only/no_observation："
            "数据不足以支持时期对比或趋势类后续分析（仅陈述数据完整性，非市场判断）")

    return {
        "filters": {
            "city": city, "metric_name": metric_name, "institution": institution,
            "source_file": source_file, "periods": requested or None,
            "period_start": period_start, "period_end": period_end,
            "actual_only": actual_only,
        },
        "period_granularity": res["period_granularity"],
        "has_explicit_window": bool(requested),
        "requested_periods": requested,
        "series": records,
        "city_metric_summary": groups,
        "summary": {
            "overall_status": "no_observation" if not records else "ok",
            "series_count": len(records),
            "actual_series_count": sum(1 for r in records if not r["is_forecast"]),
            "forecast_series_count": sum(1 for r in records if r["is_forecast"]),
            "city_metric_count": len(groups),
            "status_counts": status_counts,
            "series_with_conflicts": conflict_n,
            "conflict_period_count": sum(len(r["conflict_periods"]) for r in records),
            "empty_city_series_count": empty_city_n,
            "insufficient_series_count": len(insufficient),
            "least_covered": least_covered,
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Step 3C：城市横向比较（杭州 vs 上海/北京/深圳/全国等）
#
# 复用 query_metric_series()（逐城只读查询），不维护第二套 SQL、时期解析、
# 连接或来源分组逻辑。只描述数据条件与原始差异：各城市原始点、来源、缺失期、
# 共同覆盖期（交集）与可比性状态。不计算差值、倍数、排名、涨跌幅、相关性、
# 领先滞后，不判断城市优劣、市场阶段，不做购房建议。
#
# 可比性规则：
#   严格比较组 = (institution, source_file, segment, unit, comparison_type,
#   is_forecast, period_granularity) 全一致的跨城 series——不同机构/来源文件/
#   口径/粒度/预测标记不静默合并。
#   每个松弛键 (segment, unit, comparison_type, is_forecast, period_granularity)
#   下：存在覆盖 ≥2 城的严格组 → 各严格组各自 comparable；无严格组跨城但
#   聚合覆盖 ≥2 城 → 并列为一个 limited 组（不同来源仅并列，不计算任何派生
#   指标）；仅 1 城有数据 → 保留并标 single_city_only。
#   共同覆盖期 = 各城观测期交集；< 2 个 → insufficient_common_periods=true
#   + warning（仍展示原始点）。冲突期全部保留并标 conflicted，不平均、不取
#   第一条、不丢弃。粒度不可识别或请求混合粒度 → 拒绝/标记 mixed_granularity。
# ---------------------------------------------------------------------------


def _cmp_group_key(s: dict, fields: tuple) -> tuple:
    return tuple("" if s.get(f) is None else str(s[f]) for f in fields)


_RELAXED_FIELDS = ("segment", "unit", "comparison_type", "is_forecast", "period_granularity")
_STRICT_FIELDS = ("institution", "source_file") + _RELAXED_FIELDS


def _series_city_entry(city: str, s: dict) -> dict:
    return {
        "city": s["city"],
        "city_label": s["city_label"],
        "institution": s["institution"],
        "source_file": s["source_file"],
        "segment": s["segment"],
        "unit": s["unit"],
        "comparison_type": s.get("comparison_type"),
        "is_forecast": s["is_forecast"],
        "period_granularity": s["period_granularity"],
        "points": s["points"],
        "observed_periods": _observed_periods(s),
        "missing_periods": list(s["missing_periods"]),
        "conflicts": s["conflicts"],
    }


def _build_comparison_group(relaxed_key, members, comparability, requested) -> dict:
    """members: [(requested_city, series_dict)]；共同覆盖期 = 各城观测期交集。"""
    by_city: dict[str, list[dict]] = {}
    for c, s in members:
        by_city.setdefault(c, []).append(s)
    cities = [_series_city_entry(c, s) for c, ss in by_city.items() for s in ss]

    warnings: list[str] = []
    flags: list[str] = []
    conflicted = any(s["conflicts"] for _, s in members)
    if conflicted:
        flags.append("conflicted")
        cps = sorted({c["period"] for _, s in members for c in s["conflicts"]},
                     key=lambda p: (_period_sort_key(p), p))
        warnings.append(
            f"本比较组存在同期多值冲突（{('、'.join(cps))}）：全部保留在 conflicts，"
            "未平均、未取第一条、未丢弃")
    if comparability == "limited":
        warnings.append("不同机构/来源仅并列展示（comparability=limited）："
                        "不计算差值、倍数、排名、方向或强弱结论")

    common: list[str] = []
    partial: list[str] = []
    insufficient = False
    if len(by_city) >= 2:
        per_city = [{p for s in ss for p in _observed_periods(s)} for ss in by_city.values()]
        common_set = set.intersection(*per_city)
        union_set = set.union(*per_city)
        common = sorted(common_set, key=lambda p: (_period_sort_key(p), p))
        partial = sorted(union_set - common_set, key=lambda p: (_period_sort_key(p), p))
        insufficient = len(common) < 2
        if insufficient:
            warnings.append("共同覆盖期不足以支持时间变化比较（< 2 期）："
                            "仅展示原始点，不计算涨跌幅、差值或排名")
    else:
        flags.append("single_city_only")

    return {
        "comparison_key": {f: members[0][1].get(f) for f in _RELAXED_FIELDS},
        "comparability": comparability,
        "cities": cities,
        "common_periods": common,
        "common_period_count": len(common),
        "partially_covered_periods": partial,
        "insufficient_common_periods": insufficient,
        "has_conflicts": conflicted,
        "status_flags": flags,
        "warnings": warnings,
    }


def compare_metric_across_cities(
    *,
    cities: list[str],
    metric_name: str,
    institution: str | None = None,
    source_file: str | None = None,
    segment: str | None = None,
    unit: str | None = None,
    comparison_type: str | None = None,
    periods: list[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    include_forecasts: bool = False,
    db_path: Path = DB,
) -> dict:
    """城市横向比较：同指标跨城原始点并列 + 共同覆盖期 + 可比性状态。

    逐城调用 query_metric_series()（mode=ro 只读、参数化、查完即关），
    严格来源模式为默认；不同来源仅在无法满足严格比较时并列并标 limited。
    空城市输入排除并 warning；不支持/无数据城市列入 uncovered_cities，
    不用全国或其他城市代替。
    """
    warnings: list[str] = []
    excluded: list[str] = []
    valid: list[str] = []
    for c in cities or []:
        c = c.strip() if isinstance(c, str) else ""
        if not c:
            excluded.append(repr(c))
        elif c not in valid:
            valid.append(c)
    if excluded:
        warnings.append(f"空城市输入 {excluded} 已排除：未归一化城市不纳入城市比较")
    unsupported = [c for c in valid if c in UNSUPPORTED_CITIES]
    if unsupported:
        warnings.append(f"城市 {unsupported} 指标库暂不覆盖（uncovered_city）：不查询、不用其他城市代替")

    requested: list[str] = []
    members: list[tuple[str, dict]] = []
    conflict_warn: dict[str, str] = {}
    for c in valid:
        if c in unsupported:
            continue
        res = query_metric_series(
            city=c, metric_name=metric_name, institution=institution,
            source_file=source_file, periods=periods,
            period_start=period_start, period_end=period_end,
            include_forecasts=include_forecasts, db_path=db_path,
        )
        if res["coverage"]["requested_periods"] and not requested:
            requested = list(res["coverage"]["requested_periods"])
        for w in res["warnings"]:
            if w.startswith("无匹配记录"):
                continue  # 由 uncovered_cities 结构化表达
            if "同期多值" in w:
                key = w.split("]", 1)[0].removeprefix("series [")
                conflict_warn[key] = w
                continue  # 冲突 warning 归入比较组
            if w not in warnings:
                warnings.append(w)
        for s in res["series"]:
            if segment is not None and s["segment"] != segment:
                continue
            if unit is not None and s["unit"] != unit:
                continue
            if comparison_type is not None and s.get("comparison_type") != comparison_type:
                continue
            members.append((c, s))

    covered = {c for c, _ in members}
    uncovered = [c for c in valid if c not in covered]
    for c in uncovered:
        warnings.append(f"城市 {c} 在当前筛选下无匹配数据（uncovered_city）：不用全国或其他城市代替")

    # 严格键分组 → 按松弛键决定 comparable / limited / single_city_only
    strict: dict[tuple, list[tuple[str, dict]]] = {}
    for c, s in members:
        strict.setdefault(_cmp_group_key(s, _STRICT_FIELDS), []).append((c, s))
    by_relaxed: dict[tuple, list[tuple]] = {}
    for key, ms in strict.items():
        by_relaxed.setdefault(key[2:], []).append((key, ms))

    groups = []
    for relaxed_key in sorted(by_relaxed):
        subs = by_relaxed[relaxed_key]
        cross = [(key, ms) for key, ms in subs
                 if len({c for c, _ in ms}) >= 2]
        if cross:
            for key, ms in sorted(cross):
                groups.append(_build_comparison_group(relaxed_key, ms, "comparable", requested))
            for key, ms in sorted(subs):
                if (key, ms) not in cross:
                    groups.append(_build_comparison_group(relaxed_key, ms, "comparable", requested))
        elif len({c for _, ms in subs for c, _ in ms}) >= 2:
            merged = [m for _, ms in subs for m in ms]
            groups.append(_build_comparison_group(relaxed_key, merged, "limited", requested))
        else:
            for key, ms in sorted(subs):
                groups.append(_build_comparison_group(relaxed_key, ms, "comparable", requested))

    summary_flags: list[str] = []
    if not members:
        summary_flags.append("unmatched_metric")
        warnings.append(f"指标 {metric_name!r} 在请求城市范围内无任何匹配序列（unmatched_metric）")
    if uncovered:
        summary_flags.append("uncovered_city")
    grans = {g["comparison_key"]["period_granularity"] for g in groups}
    if len(grans) > 1:
        summary_flags.append("mixed_granularity")
        warnings.append(f"结果包含多种时期粒度 {sorted(grans)}：各比较组内粒度一致，跨组不转换不混比")
    if any(g["has_conflicts"] for g in groups):
        summary_flags.append("conflicted")
    if any(g["insufficient_common_periods"] for g in groups):
        summary_flags.append("insufficient_common_periods")

    return {
        "filters": {
            "cities": valid, "metric_name": metric_name, "institution": institution,
            "source_file": source_file, "segment": segment, "unit": unit,
            "comparison_type": comparison_type, "periods": requested or None,
            "period_start": period_start, "period_end": period_end,
            "include_forecasts": include_forecasts,
        },
        "requested_periods": requested,
        "comparison_groups": groups,
        "uncovered_cities": uncovered,
        "summary": {
            "overall_status": "unmatched_metric" if not members else "ok",
            "requested_cities": valid,
            "excluded_cities": excluded,
            "cities_with_data": sorted(covered),
            "uncovered_cities": uncovered,
            "group_count": len(groups),
            "comparable_group_count": sum(1 for g in groups if g["comparability"] == "comparable"),
            "limited_group_count": sum(1 for g in groups if g["comparability"] == "limited"),
            "status_flags": summary_flags,
        },
        "warnings": warnings,
    }


def compare_metric_across_cities_nl(
    query: str, *, include_forecasts: bool = False, db_path: Path = DB,
) -> dict:
    """薄自然语言适配层：只解析城市/指标/时期，不判断来源可信度，不输出市场结论。"""
    cities = sorted((c for c in CITIES if c in query), key=query.find)  # 按出现顺序
    haystack = _keyword_haystack(query)
    metric_names = [n for n, kws in METRIC_KEYWORDS.items() if any(kw in haystack for kw in kws)]
    warnings: list[str] = []
    if len(metric_names) != 1:
        warnings.append(f"指标命中 {len(metric_names)} 个（{metric_names}）：需恰好 1 个才可比较，未执行查询")
    if len(cities) < 2:
        warnings.append("识别到的城市少于 2 个：无法构成城市比较")
    if warnings:
        return {"filters": {"raw_query": query}, "requested_periods": [],
                "comparison_groups": [], "uncovered_cities": [],
                "summary": {"overall_status": "unmatched_metric"}, "warnings": warnings}
    return compare_metric_across_cities(
        cities=cities, metric_name=metric_names[0],
        periods=parse_periods(query) or None,
        include_forecasts=include_forecasts, db_path=db_path,
    )
