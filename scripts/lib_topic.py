"""docs.topic 规则打标:多标签,零 LLM 成本。

策略(M0 拍板:多标签 JSON array,复用 cities 列模式):
- 标题强信号命中 → 直接打标签(本语料标题多为研报系列名,可靠性高)
- 正文关键词密度兜底 → 标题无信号时补标;也让宏观文档正文涉房时能带上"房地产"标签
- 阈值保守,宁可漏打:漏标的代价是房地产 agent 少看到几篇,错标没有过滤副作用
  (topic 只用于房地产 agent 的过滤,标签多了不影响检索质量)
"""
from __future__ import annotations

import re

TOPICS = ["房地产", "宏观政策", "商品外汇", "行业消费", "其他"]

# 标题强信号:命中即打标签(正则,忽略大小写;英文词带词边界)
TITLE_SIGNALS: dict[str, list[str]] = {
    "房地产": [
        r"property", r"housing", r"home[-_ ]?price", r"real[-_ ]?estate", r"mortgage",
        r"developer.{0,12}sales", r"房地产", r"地产", r"楼市", r"房价", r"百城",
        r"二手房", r"新房", r"土拍", r"物业", r"保障房", r"购房", r"置业",
        r"按揭", r"房贷", r"去化", r"收储", r"限购",
    ],
    "宏观政策": [
        r"\bGDP\b", r"\bCPI\b", r"\bPMI\b", r"politburo", r"政治局", r"两会",
        r"\bNPC\b", r"two[-_ ]?sessions", r"economics?", r"macro", r"宏观",
        r"财政政策", r"货币政策", r"税负", r"老龄化", r"赤字", r"通缩", r"通胀",
        r"tariff", r"关税", r"stimulus",
    ],
    "商品外汇": [
        r"\bgold\b", r"\bsilver\b", r"precious[-_ ]?metal", r"\bFX\b", r"forex",
        r"黄金", r"白银", r"汇率", r"原油", r"\boil\b", r"\bcopper\b", r"铜",
        r"\bCNY\b", r"\bRMB\b", r"\byuan\b", r"\byen\b", r"dollar", r"美元",
        r"大宗", r"避险",
    ],
    "行业消费": [
        r"\bEV\b", r"\bautos?\b", r"汽车", r"consumer", r"消费", r"luxury",
        r"奢侈", r"retail", r"零售", r"\bAI\b", r"人工智能", r"semiconductor",
        r"半导体", r"chip", r"芯片", r"smartphone", r"智能手机",
    ],
}

# 正文关键词:按命中数 + 密度双重门槛(标题无信号时的兜底通道)
# 注意:RMB/美元/人民币 在金融研报中普遍作为计价单位出现,不进正文词表,防误标
BODY_KEYWORDS: dict[str, list[str]] = {
    "房地产": [
        r"房地产", r"楼市", r"房价", r"住宅", r"商品房", r"二手房", r"新房",
        r"土拍", r"土地出让", r"开发商", r"房企", r"去化", r"按揭", r"房贷",
        r"限购", r"保障房", r"收储", r"物业",
        r"property", r"properties", r"housing", r"home[-_ ]?prices?",
        r"home[-_ ]?sales", r"homebuy", r"mortgage", r"real[-_ ]?estate",
        r"residential", r"developers?\b",
    ],
    "宏观政策": [
        r"政治局", r"两会", r"宏观", r"货币政策", r"财政", r"降准", r"降息",
        r"刺激", r"通缩", r"通胀", r"出口", r"关税", r"贸易", r"就业",
        r"\bGDP\b", r"\bCPI\b", r"\bPMI\b", r"politburo", r"\bNPC\b",
        r"fiscal", r"monetary", r"stimulus", r"tariff", r"deflation",
        r"inflation", r"exports?\b",
    ],
    "商品外汇": [
        r"黄金", r"金价", r"白银", r"原油", r"汇率", r"避险",
        r"\bgold\b", r"\bsilver\b", r"\boil\b", r"\bcopper\b", r"bullion",
        r"precious", r"\bforex\b", r"\bCNY\b",
    ],
    "行业消费": [
        r"汽车", r"电动车", r"新能源车", r"智能手机", r"半导体", r"芯片",
        r"奢侈品", r"零售", r"餐饮", r"人工智能",
        r"\bEV\b", r"\bautos?\b", r"consumer", r"retail", r"luxury",
        r"semiconductor", r"\bAI\b", r"smartphone",
    ],
}

# 正文兜底门槛:命中 ≥20 次 且 密度 ≥5 次/万字(约 2.3 万字的中位文档需 ~20 次命中)
BODY_MIN_HITS = 20
BODY_MIN_DENSITY = 5.0

_TITLE_RE = {t: [re.compile(p, re.I) for p in pats] for t, pats in TITLE_SIGNALS.items()}
_BODY_RE = {t: [re.compile(p, re.I) for p in pats] for t, pats in BODY_KEYWORDS.items()}


def count_hits(topic: str, body: str) -> int:
    """正文关键词命中总次数(供 classify 与诊断导出复用)。"""
    return sum(len(p.findall(body)) for p in _BODY_RE.get(topic, []))


def classify(title: str, body: str) -> list[str]:
    """返回标签列表(按 TOPICS 顺序);无任何信号时返回 ["其他"]。"""
    title = title or ""
    body = body or ""
    tags: list[str] = []
    wan_chars = max(len(body) / 10000.0, 0.1)
    for topic in TOPICS[:-1]:  # "其他" 只作兜底,不参与匹配
        if any(p.search(title) for p in _TITLE_RE.get(topic, [])):
            tags.append(topic)
            continue
        hits = count_hits(topic, body)
        if hits >= BODY_MIN_HITS and hits / wan_chars >= BODY_MIN_DENSITY:
            tags.append(topic)
    return tags or ["其他"]
