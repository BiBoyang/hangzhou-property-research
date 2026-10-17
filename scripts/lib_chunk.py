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
    r"disclaimer|disclosure|analyst certification|法律声明|免责声明|重要声明",
    re.IGNORECASE,
)
# 2026-10-17 治本:appendix 从丢弃名单移除(投行附录全是数据表,扩容评测集实测此处丢内容)

# 披露页噪声（文本级，2026-09-16 治本）：投行尾页 boilerplate 强特征。
# 这些模式出现在正文里的概率极低，命中 2 个即判定整段为披露页；
# 短段（<500 字符）命中 1 个也判——长正文段里单个页脚行不会误伤。
# lib_search 的查询期过滤保留作兜底，与本表保持一致。
NOISE_STRONG = (
    "all rights reserved", "analyst certification", "disclosure statements",
    "com/disclosures", "attributed to a third party", "global research through",
    "you are permitted to store", "conflict management policy",
    "法律声明", "免责声明", "sac registration",
)


def _noise_hits(text: str) -> int:
    low = text.lower()
    return sum(1 for m in NOISE_STRONG if m in low)


def is_boilerplate(text: str, heading: str = "") -> bool:
    """整段判定为披露/法律 boilerplate：强特征 ≥2，或短段命中 ≥1。
    页眉版权行（© 2026 xxx）直接计 1 次强命中。"""
    hits = _noise_hits(text)
    if heading.lstrip().startswith("©"):
        hits += 1
    return hits >= 2 or (hits >= 1 and len(text) < 500)


@dataclass
class Chunk:
    ord: int
    heading: str
    text: str
    is_table: bool = False


def _flush(buf: list[str], heading: str, out: list[Chunk], is_table: bool) -> None:
    text = "\n".join(buf).strip()
    if not text:
        return
    # 巨型表格按行组拆分,表头(首行+分隔行)重复进每个子块(2026-10-17:
    # 18k 字符整表一块,单元格根本检索不到——扩容评测集错题归因实锤)
    if is_table and len(text) > MAX_CHARS:
        header_n = 2 if len(buf) > 1 and set(buf[1].strip()) <= set("|-+ :") else 1
        header = buf[:header_n]
        cur = list(header)
        for row in buf[header_n:]:
            cur.append(row)
            if sum(len(x) for x in cur) >= MAX_CHARS:
                out.append(Chunk(ord=len(out), heading=heading, text="\n".join(cur), is_table=True))
                cur = list(header)
        if len(cur) > header_n:
            out.append(Chunk(ord=len(out), heading=heading,
                             text="\n".join(cur).strip(), is_table=True))
        return
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
    # 3) 披露页噪声整段丢弃（治本；查询期过滤兜底）
    out = [c for c in out if not is_boilerplate(c.text, c.heading)]
    out = _merge_short(out)
    for i, c in enumerate(out):
        c.ord = i
    return out


def _merge_short(out: list[Chunk]) -> list[Chunk]:
    """短段(<MIN_CHARS)合并进相邻段,不丢弃(2026-10-17 治本:
    原实现短段直接扔,172/228 篇文档覆盖率不足 80%,Nomura 快评类低至 9%)。
    表格保持原子不合并;实在没邻居可并的短段保留(总比丢了好)。"""
    res: list[Chunk] = []
    pending: Chunk | None = None
    for c in out:
        if pending is not None:
            if not c.is_table:
                c = Chunk(ord=c.ord, heading=c.heading or pending.heading,
                          text=pending.text + "\n" + c.text, is_table=False)
            else:
                # 表格原子:pending 并入前一个文本块;没有则保留
                if res and not res[-1].is_table:
                    res[-1].text += "\n" + pending.text
                else:
                    res.append(pending)
            pending = None
        if not c.is_table and len(c.text) < MIN_CHARS:
            pending = c
        else:
            res.append(c)
    if pending is not None:
        if res and not res[-1].is_table:
            res[-1].text += "\n" + pending.text
        else:
            res.append(pending)
    return res
