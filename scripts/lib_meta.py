"""文档元数据：从文件名/正文提取机构、日期、语言、城市标签。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

INSTITUTIONS = {
    "Goldman Sachs": ["goldman", "高盛"],
    "UBS": ["ubs", "瑞银"],
    "J.P. Morgan": ["j.p. morgan", "jpmorgan", "j.p", "摩根大通", "小摩"],
    "Morgan Stanley": ["morgan stanley", "摩根士丹利", "大摩"],
    "Nomura": ["nomura", "野村"],
    "Deutsche Bank": ["deutsche", "德意志"],
    "Bernstein": ["bernstein", "伯恩斯坦"],
    "Citi": ["citi", "花旗"],
    "中指院": ["中指", "creis", "百城价格"],
    "CF40": ["cf40"],
}

CITY_PATTERNS = {
    "上海": ["上海", "shanghai"],
    "北京": ["北京", "beijing"],
    "深圳": ["深圳", "shenzhen"],
    "广州": ["广州", "guangzhou"],
    "杭州": ["杭州", "hangzhou"],
    "香港": ["香港", "hong kong"],
}

DATE_FULL = re.compile(r"(20\d{6})")          # 20260416
DATE_SHORT = re.compile(r"(?<!\d)(26[01]\d{3})(?!\d)")  # 260818


@dataclass
class DocMeta:
    doc_id: str
    file_path: str
    title: str
    institution: str = ""
    report_date: str = ""   # YYYY-MM-DD
    lang: str = ""
    cities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_date(name: str) -> str:
    m = DATE_FULL.search(name)
    if m:
        s = m.group(1)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    m = DATE_SHORT.search(name)
    if m:
        s = m.group(1)
        return f"20{s[:2]}-{s[2:4]}-{s[4:6]}"
    return ""


def parse_institution(name: str) -> str:
    low = name.lower()
    hits = [inst for inst, kws in INSTITUTIONS.items() if any(k in low for k in kws)]
    return "&".join(hits)


def detect_lang(text: str) -> str:
    sample = text[:2000]
    cjk = sum(1 for ch in sample if "一" <= ch <= "鿿")
    return "zh" if cjk / max(len(sample), 1) > 0.2 else "en"


def tag_cities(name: str, text: str) -> list[str]:
    haystack = (name + "\n" + text[:20000]).lower()
    return [city for city, kws in CITY_PATTERNS.items() if any(k in haystack for k in kws)]


def parse_front_matter(text: str) -> tuple[dict, str]:
    """剥离开头的 YAML front matter，返回 (字段字典, 正文)；无则原样返回。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fields = {}
    for line in parts[1].strip().splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields, parts[2].lstrip("\n")


def build_meta(pdf: Path, text_sample: str = "", front: dict | None = None) -> DocMeta:
    front = front or {}
    doc_id = hashlib.sha1(str(pdf).encode()).hexdigest()[:12]
    return DocMeta(
        doc_id=doc_id,
        file_path=str(pdf),
        title=front.get("title") or pdf.stem,
        institution=front.get("institution") or parse_institution(pdf.name),
        report_date=front.get("report_date") or parse_date(pdf.name),
        lang=detect_lang(text_sample) if text_sample else "",
        cities=tag_cities(pdf.name, text_sample),
    )
