"""Markdown 切分：按标题层级切段，表格整块不拆，丢弃免责声明段。

切分规则（沿用 2026-04 方案的经验）：
- H1/H2 切大段，段内 H3 切小段
- 连续的表格行（| 开头）是原子块，任何情况下不拆
- 目标 800-1500 字符，句子级重叠 1-2 句
- Disclaimer/Disclosure/法律声明 段直接丢弃
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIN_CHARS = 200
MAX_CHARS = 1500

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_LINE = re.compile(r"^\s*\|")
SENT_END = re.compile(r"(?<=[。！？.!?；;])\s*")

DROP_HEADINGS = re.compile(
    r"disclaimer|disclosure|analyst certification|法律声明|免责声明|重要声明|appendix",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    ord: int
    heading: str
    text: str
    is_table: bool = False


def _flush(buf: list[str], heading: str, out: list[Chunk], is_table: bool) -> None:
    text = "\n".join(buf).strip()
    if len(text) >= MIN_CHARS or (is_table and text):
        out.append(Chunk(ord=len(out), heading=heading, text=text, is_table=is_table))


def split_markdown(md: str) -> list[Chunk]:
    # 1) 按标题切成 section
    sections: list[tuple[str, list[str]]] = []
    cur_head, cur_lines = "", []
    for line in md.splitlines():
        m = HEADING.match(line)
        if m:
            if cur_lines:
                sections.append((cur_head, cur_lines))
            cur_head = m.group(2).strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    if cur_lines:
        sections.append((cur_head, cur_lines))

    # 2) section 内：表格原子化 + 长度聚合
    out: list[Chunk] = []
    for head, lines in sections:
        if DROP_HEADINGS.search(head):
            continue
        buf: list[str] = []
        table_buf: list[str] = []
        for line in lines:
            if TABLE_LINE.match(line):
                if buf:
                    _flush(buf, head, out, False)
                    buf = []
                table_buf.append(line)
            else:
                if table_buf:
                    _flush(table_buf, head, out, True)
                    table_buf = []
                buf.append(line)
                if sum(len(x) for x in buf) >= MAX_CHARS:
                    _flush(buf, head, out, False)
                    # 句子级重叠：保留末尾 1-2 句
                    tail = SENT_END.split("\n".join(buf))[-2:]
                    buf = [t for t in tail if t.strip()]
        if table_buf:
            _flush(table_buf, head, out, True)
        if buf:
            _flush(buf, head, out, False)
    return out
