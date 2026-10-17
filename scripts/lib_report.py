"""M4: 结构化研判报告——编排器复用 M2 agent 循环。

流程(M4 拍板):
  planner 拆题(1 次 LLM) → 五节各自跑 M2 循环取证(每节 ≤3 次) → 合成(1 次)
  总调用 12-17 次/份;观点矩阵表格规则渲染(stance_matrix,数字不经模型手);
  单节失败降级为占位标注,不阻断整份报告;合成失败降级为规则拼装。

产出: plans/reports/研判-YYYYMMDD-<主题>.md
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI  # noqa: E402  # eager,同 lib_agent

from lib_agent import AgentConfig, run_agent  # noqa: E402
from lib_claims import SUBJECTS, stance_matrix  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_llm import llm_config  # noqa: E402
from lib_paths import APP, DB  # noqa: E402

REPORTS_DIR = APP / "plans" / "reports"
SECTION_MAX_CALLS = 3   # 每节 agent 循环预算(含终答)
SECTION_WORKERS = 3     # 节间并发

SECTIONS: dict[str, str] = {
    "结论": "你在写研判报告的【结论】节:一句话判断 + 置信度 + 关键前提,不超过 120 字。不写论据细节,不用四段式。",
    "数据支撑": "你在写【数据支撑】节:用 query_metrics 拿精确数字,按主题列要点,每条带 [机构/来源 时期],数字原文照抄。不用四段式。",
    "机构观点矩阵": "你在写【机构观点矩阵】节:用 query_claims 梳理各机构立场的时间轴演变(谁何时从什么立场转向什么立场),写一段不超过 200 字的综述。矩阵表格由系统规则渲染,你不用列表格。不用四段式。",
    "分歧分析": "你在写【分歧分析】节:找出机构之间/同一机构不同时期的分歧点,并列呈现各方依据,禁止调和。指出分歧根源(口径/样本/逻辑/时效)。不用四段式。",
    "风险": "你在写【风险与缺口】节:列 ① 结论面临的主要风险 ② 语料覆盖缺口(哪些关键问题现有资料未覆盖,明说)③ 数据口径警示。不用四段式。",
}

PLANNER_PROMPT = """你是研究报告 planner。给定研判主题,输出 JSON:
{"matrix_subjects": [相关主题枚举子集], "sections": {"结论": "子问题", "数据支撑": "...", "机构观点矩阵": "...", "分歧分析": "...", "风险": "..."}}
- matrix_subjects 只能从: """ + "、".join(sorted(SUBJECTS)) + """ 中选 1-3 个最相关的
- 每个 sections 子问题是一句自足的、可直接检索的问题(含主题中的城市/时期/主体)
- 只输出 JSON,不要解释"""


def plan_report(topic: str, client=None) -> dict:
    """planner 拆题;失败/解析失败 → 模板兜底(每节子问题=主题+节导向)。"""
    fallback = {
        "matrix_subjects": ["房价", "成交量"],
        "sections": {name: f"{topic}——{name}相关取证" for name in SECTIONS},
    }
    cfg = llm_config()
    if client is None:
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    try:
        kwargs: dict = {"model": cfg["model"],
                        "messages": [{"role": "system", "content": PLANNER_PROMPT},
                                     {"role": "user", "content": topic}]}
        if os.environ.get("LLM_THINKING") != "1":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**kwargs)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "",
                      (resp.choices[0].message.content or "").strip(), flags=re.S)
        plan = json.loads(text)
        assert isinstance(plan["sections"], dict) and plan["sections"]
        plan["matrix_subjects"] = [s for s in plan.get("matrix_subjects", []) if s in SUBJECTS] or ["房价"]
        for name in SECTIONS:  # 缺的节兜底
            plan["sections"].setdefault(name, fallback["sections"][name])
        return plan
    except Exception:  # noqa: BLE001
        return fallback


def render_matrix(conn, subjects: list[str], topic_filter: str | None) -> str:
    """机构观点矩阵:规则渲染 markdown(数字/立场不经 LLM)。按 subject 分表,时间轴序。"""
    parts = []
    for subj in subjects:
        groups = stance_matrix(conn, subject=subj, topic=topic_filter)
        if not groups:
            continue
        lines = [f"**{subj}**", "", "| 日期 | 机构 | 立场 | 观点 | 时期 |", "|---|---|---|---|---|"]
        n = 0
        for g in groups:
            for c in g["claims"]:
                lines.append(f"| {g['report_date']} | {g['institution']} | {c['stance']} "
                             f"| {c['claim_text']} | {c['horizon'] or '—'} |")
                n += 1
                if n >= 50:
                    lines.append("| … | (截断,更多见 claims 表) | | | |")
                    break
            if n >= 50:
                break
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else "(观点库无相关记录)"


def _run_section(name: str, subquery: str, topic_filter: str | None,
                 db_path: Path) -> dict:
    cfg = AgentConfig(topic_filter=topic_filter, max_llm_calls=SECTION_MAX_CALLS,
                      system_extra=SECTIONS[name])
    try:
        res = run_agent(subquery, cfg, db_path=db_path)
        return {"name": name, "text": res["answer"], "usage": res["usage"],
                "llm_calls": res["llm_calls"], "trace": res["trace_path"], "error": None}
    except Exception as e:  # noqa: BLE001 单节失败不阻断
        return {"name": name, "text": f"(本节生成失败: {type(e).__name__}: {e})",
                "usage": {"in": 0, "out": 0}, "llm_calls": 0, "trace": "", "error": str(e)}


SYNTH_PROMPT = """你是研究报告主编。把以下各节草稿与观点矩阵合成一份正式研判报告(markdown)。
规则:
1. 结构: # 标题 / ## 结论 / ## 数据支撑 / ## 机构观点矩阵 / ## 分歧分析 / ## 风险与缺口
2. 机构观点矩阵一节:保留综述文字,在综述后标注"(矩阵表格见文末附表)"——表格由系统拼,你别编数字
3. 修正各节间的事实矛盾(以数据支撑节的数字为准),统一引用格式 [机构 日期]
4. 禁止新增任何草稿里没有的事实与数字;草稿里标注"未覆盖/生成失败"的如实保留
5. 标题与时间框以"当前最新时点"为准(语料最新报告日期为锚),禁止把标题写成更早的历史区间
6. 直接输出报告全文,不要前言"""


def run_report(topic: str, topic_filter: str | None = "房地产",
               db_path: Path = DB, client=None, out_dir: Path = REPORTS_DIR) -> dict:
    t0 = time.time()
    plan = plan_report(topic, client=client)
    with ThreadPoolExecutor(max_workers=SECTION_WORKERS) as pool:
        sections = list(pool.map(
            lambda kv: _run_section(kv[0], kv[1], topic_filter, db_path),
            plan["sections"].items()))
    sections.sort(key=lambda s: list(SECTIONS).index(s["name"]))  # 恢复模板顺序

    conn = connect(db_path)
    matrix_md = render_matrix(conn, plan["matrix_subjects"], topic_filter)
    conn.close()

    usage = {"in": 0, "out": 0}
    for s in sections:
        usage["in"] += s["usage"]["in"]
        usage["out"] += s["usage"]["out"]

    # 合成(失败降级规则拼装)
    draft = "\n\n".join(f"## {s['name']}\n{s['text']}" for s in sections)
    synthesized = None
    try:
        cfg = llm_config()
        if client is None:
            client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        kwargs: dict = {"model": cfg["model"],
                        "messages": [{"role": "system", "content": SYNTH_PROMPT},
                                     {"role": "user", "content": f"主题: {topic}\n\n{draft}"}]}
        if os.environ.get("LLM_THINKING") != "1":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**kwargs)
        synthesized = (resp.choices[0].message.content or "").strip()
        u = resp.usage
        if u:
            usage["in"] += u.prompt_tokens
            usage["out"] += u.completion_tokens
    except Exception:  # noqa: BLE001
        synthesized = None

    body = synthesized or f"# {topic} 研判\n\n{draft}"
    body += f"\n\n---\n\n## 附表:机构观点矩阵(规则渲染,数据源 claims 表)\n\n{matrix_md}"
    mins = (time.time() - t0) / 60
    header = (f"<!-- 生成: {time.strftime('%Y-%m-%d %H:%M')} · 引擎 {llm_config()['model']} · "
              f"耗时 {mins:.1f} 分钟 · LLM 调用 ~{1 + sum(s['llm_calls'] for s in sections) + 1} 次 · "
              f"tokens in={usage['in']:,} out={usage['out']:,} -->\n\n")
    slug = re.sub(r"[^\w一-鿿-]+", "-", topic)[:40].strip("-")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"研判-{time.strftime('%Y%m%d')}-{slug}.md"
    path.write_text(header + body, encoding="utf-8")
    return {"path": str(path), "usage": usage, "sections": sections,
            "plan": plan, "synthesized": synthesized is not None,
            "llm_calls": 1 + sum(s["llm_calls"] for s in sections) + 1}
