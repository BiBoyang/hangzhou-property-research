"""M3: 多轮 session + 历史上下文构造(只带摘要+实体,不带原文 chunk)。

设计(M3 拍板):
- 存储走 DB 两张小表(agent_sessions/agent_turns),沿用单文件 SQLite 哲学
- 历史上下文规则拼装,零额外 LLM 调用:
  ① 实体注册表(机构/城市/时期,规则抽取) ② 最近 2 轮问答截断 ③ 更早轮次一行式摘要
- 指代消解改写在 lib_agent.resolve_query,与循环解耦(循环内部不带历史)
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid

from lib_claims import INSTITUTION_PATTERNS  # noqa: E402

RECENT_TURNS = 2          # 最近几轮带截断全文
ANSWER_SNIP = 300         # 答案截断字符
OLDER_SNIP = 30           # 更早轮次问题一行摘要字符

# 城市词表与 lib_metrics_query.CITIES 保持同源性(那里是查询用,这里是实体注册用)
CITIES = ["上海", "北京", "深圳", "广州", "杭州", "香港", "全国", "一线", "二线", "三线", "四线"]
PERIOD_RE = re.compile(r"\d{4}\s*年([上下]半年|\d{1,2}\s*月)?|\d{4}[-/]\d{1,2}|\d{4}\s*[Qq][1-4]")


def new_session(conn: sqlite3.Connection, agent_topic: str | None) -> str:
    sid = f"{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    conn.execute("INSERT INTO agent_sessions(session_id, agent_topic, created_at) VALUES(?,?,?)",
                 (sid, agent_topic, time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return sid


def latest_session(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT session_id FROM agent_sessions ORDER BY created_at DESC LIMIT 1").fetchone()
    return r["session_id"] if r else None


def append_turn(conn: sqlite3.Connection, session_id: str, question: str,
                standalone: str, answer: str, llm_calls: int, tool_calls: int,
                trace_path: str) -> int:
    turn = (conn.execute("SELECT IFNULL(MAX(turn),0)+1 FROM agent_turns WHERE session_id=?",
                         (session_id,)).fetchone()[0])
    conn.execute("INSERT INTO agent_turns VALUES(?,?,?,?,?,?,?,?)",
                 (session_id, turn, question, standalone, answer, llm_calls, tool_calls, trace_path))
    conn.commit()
    return turn


def list_turns(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM agent_turns WHERE session_id=? ORDER BY turn", (session_id,))]


def extract_entities(text: str) -> dict[str, list[str]]:
    """规则实体抽取:机构(复用 claims 机构正则)/城市/时期。保序去重。"""
    found: dict[str, list[str]] = {"机构": [], "城市": [], "时期": []}
    for name, pat in INSTITUTION_PATTERNS:
        if re.search(pat, text, re.I) and name not in found["机构"]:
            found["机构"].append(name)
    # 按文中出现顺序去重
    for _, c in sorted((text.find(c), c) for c in CITIES if c in text):
        found["城市"].append(c)
    for m in PERIOD_RE.findall(text):
        pass
    periods = []
    for m in PERIOD_RE.finditer(text):
        p = re.sub(r"\s+", "", m.group(0))
        if p not in periods:
            periods.append(p)
    found["时期"] = periods
    return found


def build_context(turns: list[dict]) -> str:
    """历史上下文:实体注册表 + 最近 2 轮截断问答 + 更早轮次一行摘要。无历史返回空串。"""
    if not turns:
        return ""
    ents: dict[str, list[str]] = {"机构": [], "城市": [], "时期": []}
    for t in turns:
        for k, v in extract_entities(t["question"] + "\n" + t["answer"]).items():
            ents[k] += [x for x in v if x not in ents[k]]
    parts = ["【对话历史】"]
    ent_line = " / ".join(f"{k}: {'、'.join(v)}" for k, v in ents.items() if v)
    if ent_line:
        parts.append(f"涉及实体 — {ent_line}")
    recent, older = turns[-RECENT_TURNS:], turns[:-RECENT_TURNS]
    if older:
        parts.append("更早话题: " + "；".join(f"第{t['turn']}轮问「{t['question'][:OLDER_SNIP]}」" for t in older))
    for t in recent:
        ans = re.sub(r"\s+", " ", t["answer"])[:ANSWER_SNIP]
        parts.append(f"第{t['turn']}轮 问: {t['question']}\n第{t['turn']}轮 答(截断): {ans}")
    return "\n".join(parts)
