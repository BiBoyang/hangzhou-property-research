"""claims 观点层:枚举/schema 常量、抽取结果校验、机构名兜底、观点矩阵查询。

观点矩阵按时间轴呈现机构立场,禁止简单多数投票(TASK-agentic-rag 风险 2)。
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter

SUBJECTS = {"房价", "成交量", "库存与去化", "政策", "开发商与房企", "土地市场",
            "租赁", "宏观与金融条件", "购房者情绪", "其他"}
STANCES = {"看多", "看空", "中性"}
SEGMENTS = {"new_home", "secondary_home", "land", "rental", "macro", "developer", ""}


def _norm(s: str) -> str:
    # NFKC:OCR 文档含兼容部首形(⼆/⽐/⼿等 U+2Fxx),统一回标准 CJK;
    # 模型常在句尾补标点,剥掉。数字缺失等实质差异不靠归一化蒙混,照丢
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).strip(".。… ")


def validate(claim: dict, body: str) -> list[str]:
    """抽取结果程序校验(人审兜底前的第一道)。返回问题列表(空 = 通过)。"""
    problems = []
    if claim.get("stance") not in STANCES:
        problems.append(f"stance 非法: {claim.get('stance')!r}")
    if claim.get("subject") not in SUBJECTS:
        problems.append(f"subject 非法: {claim.get('subject')!r}")
    if claim.get("segment", "") not in SEGMENTS:
        problems.append(f"segment 非法: {claim.get('segment')!r}")
    q = claim.get("evidence_quote", "")
    if not q:
        problems.append("evidence_quote 为空")
    elif _norm(q) not in _norm(body):
        problems.append("evidence_quote 非原文子串")
    if not claim.get("claim_text"):
        problems.append("claim_text 为空")
    # 注:prompt 允许 horizon 空串(原文未明说时),预测缺 horizon 不算异常
    return problems


# 机构名兜底:docs.institution 为空时从标题正则推断(M0 spike 发现 25 篇机构为空)
INSTITUTION_PATTERNS: list[tuple[str, str]] = [
    ("J.P. Morgan", r"j\.?\s?p\.?\s?morgan|jpmorgan|摩根大通"),
    ("Goldman Sachs", r"goldman[_ ]sachs|高盛|^gs[-_]"),
    ("Morgan Stanley", r"morgan[_ ]stanley|大摩|^ms[-_]"),
    ("Nomura", r"nomura|野村"),
    ("UBS", r"\bubs\b|瑞银"),
    ("Citi", r"\bciti\b|花旗"),
    ("Bernstein", r"bernstein|伯恩斯坦"),
    ("Deutsche Bank", r"deutsche|德意志"),
    ("BofA", r"bofa|美银"),
    ("Barclays", r"barclays|巴克莱"),
    ("中指院", r"中指"),
    ("克而瑞", r"克而瑞|cric"),
    ("冰山指数", r"冰山指数"),
]


def resolve_institution(db_value: str, title: str) -> str:
    """docs.institution 优先;为空时按标题正则兜底;再空返回 ''。"""
    if db_value:
        return db_value
    for name, pat in INSTITUTION_PATTERNS:
        if re.search(pat, title or "", re.I):
            return name
    return ""


def query_claims(conn: sqlite3.Connection, subject: str | None = None,
                 stance: str | None = None, institution: str | None = None,
                 date_from: str | None = None, date_to: str | None = None,
                 city: str | None = None, segment: str | None = None,
                 topic: str | None = None, limit: int = 200) -> list[dict]:
    """观点查询:多条件过滤,按 report_date 升序(时间轴)。topic 过滤走 docs.topic JSON。"""
    sql = "SELECT c.* FROM claims c"
    conds, params = [], []
    if topic:
        sql += " JOIN docs d ON d.doc_id = c.doc_id"
        conds.append("instr(d.topic, '\"' || ? || '\"') > 0")
        params.append(topic)
    for col, val in [("c.subject", subject), ("c.stance", stance),
                     ("c.institution", institution), ("c.city", city), ("c.segment", segment)]:
        if val:
            conds.append(f"{col} = ?")
            params.append(val)
    if date_from:
        conds.append("c.report_date >= ?")
        params.append(date_from)
    if date_to:
        conds.append("c.report_date <= ?")
        params.append(date_to)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY c.report_date, c.institution LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]


def stance_matrix(conn: sqlite3.Connection, **filters) -> list[dict]:
    """观点矩阵:按 (机构, 日期) 分组,时间轴呈现各机构立场分布。

    返回 [{institution, report_date, stances: {看多: n, ...}, claims: [...]}],
    不做多数投票——立场反转与时间演进是信息本身(TASK 风险 2)。
    """
    rows = query_claims(conn, **filters)
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["institution"] or "未知", r["report_date"] or "未知")
        g = groups.setdefault(key, {"institution": key[0], "report_date": key[1],
                                    "stances": Counter(), "claims": []})
        g["stances"][r["stance"]] += 1
        g["claims"].append({k: r[k] for k in ("subject", "stance", "horizon", "claim_text")})
    out = sorted(groups.values(), key=lambda g: g["report_date"])
    for g in out:
        g["stances"] = dict(g["stances"])
    return out
