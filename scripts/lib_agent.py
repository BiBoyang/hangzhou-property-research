"""M2: agent 循环(单轮深研)——手写 ReAct over OpenAI 原生 tools API。

拍板(TASK-agentic-rag 决策 1/2/3 + M2 确认):
- 手写循环,不上 LangGraph;每问 LLM 调用上限 4 次(含终答)
- 单引擎双配置:房地产 agent = topic_filter="房地产" + 专用 prompt;全量 = None
- 首轮必查(tool_choice="required" 强制至少一轮工具);之后模型自足判断
- 同一轮多个 tool_calls 并行执行;最后一轮强制无工具合成(保证 ≤ 上限)
- trace 逐轮落 data/traces/*.jsonl
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI  # noqa: E402  # eager:线程内懒加载会撞部分初始化竞态

from lib_claims import query_claims, stance_matrix  # noqa: E402
from lib_db import connect  # noqa: E402
from lib_llm import llm_config  # noqa: E402
from lib_metrics_query import format_metrics, query_metrics  # noqa: E402
from lib_paths import APP, DB, RAW_MD  # noqa: E402
from lib_search import search  # noqa: E402

TRACES_DIR = APP / "data" / "traces"
TOOL_RESULT_MAX_CHARS = 4000   # 单个工具结果截断,控 token
READ_DOC_MAX_CHARS = 6000      # read_doc 默认返回开头 6k 字符 + 大纲

_search_lock = threading.Lock()  # embedding 模型非线程安全保险:search_docs 串行,其余并行


@dataclass
class AgentConfig:
    topic_filter: str | None = None  # "房地产" = 房地产 agent;None = 全量 agent
    max_llm_calls: int = 4           # 拍板:每问上限 4 次(含终答)
    min_tool_rounds: int = 1         # 首轮必查;2 = 固定两轮检索保底(自足判断关闭)
    model: str | None = None         # None → llm_config() 默认
    trace: bool = True
    system_extra: str = ""           # M4 节级指令注入(追加到 system prompt 末尾)
    capture_contexts: bool = False   # YanbaoEval 集成:收集工具结果原文(citations/contexts)
    temperature: float | None = None  # None=不传(kimi 锁 1);flash 评测传 0 降采样方差
    on_event: object = None           # 可选回调 dict 事件(web 流式进度):llm_round/tool_calls/final


# 双配置注册表(TASK 决策 3:单引擎双配置)
AGENTS = {
    "property": AgentConfig(topic_filter="房地产"),   # 房地产 agent
    "all": AgentConfig(topic_filter=None),            # 全量 agent
}


# ---------- 工具定义(原生 tools API schema) ----------

def _props(**kw):
    return {"type": "object", "properties": kw}

TOOLS = [
    {"type": "function", "function": {
        "name": "search_docs",
        "description": "研报全文混合检索(语义+关键词+文档先验)。找具体数据、原文表述、事件细节用我。query 用自然语言,可含机构/城市/时间词。",
        "parameters": {**_props(query={"type": "string", "description": "检索 query:用关键词短语(主体+指标+时间),不要完整问句"},
                                 k={"type": "integer", "description": "返回条数,默认8"}),
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "query_metrics",
        "description": "结构化指标库:成交量/均价/库存去化/挂牌量/房贷利率等精确数字。问题涉及具体数值、某月/某季度数据时必须用我。",
        "parameters": {**_props(query={"type": "string", "description": "自然语言,含城市+时期+指标词"}),
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "query_claims",
        "description": "机构观点库:各投行/机构的方向性判断与预测,带立场(看多/看空/中性)和报告日期。涉及'机构怎么看/谁支持谁反对/态度变化'时用我。",
        "parameters": {**_props(
            subject={"type": "string", "description": "房价/成交量/库存与去化/政策/开发商与房企/土地市场/租赁/宏观与金融条件/购房者情绪/其他"},
            stance={"type": "string", "description": "看多/看空/中性,不填=全部"},
            institution={"type": "string", "description": "如 Goldman Sachs/UBS/中指院"},
            date_from={"type": "string", "description": "YYYY-MM-DD"},
            date_to={"type": "string", "description": "YYYY-MM-DD"},
            city={"type": "string"}),
            "required": []}}},
    {"type": "function", "function": {
        "name": "read_doc",
        "description": "按 doc_id 读某份报告的标题、元数据和正文开头+章节大纲。需要深挖某一份报告时用我。",
        "parameters": {**_props(doc_id={"type": "string"}), "required": ["doc_id"]}}},
]


# ---------- 工具执行(薄封装既有查询层) ----------

def _doc_id_of(chunk_id: str) -> str:
    return chunk_id.rsplit("-", 1)[0]


def _tool_search_docs(args: dict, config: AgentConfig, db_path: Path) -> str:
    from lib_search import normalize_query
    k = min(int(args.get("k") or 8), 12)
    query = args["query"]
    nq = normalize_query(query)
    with _search_lock:
        rows = search(query, k=k * 2 if config.topic_filter else k, db_path=db_path)
        if nq != query:  # 双路:规范化版本补充召回,原文结果优先
            extra = search(nq, k=k, db_path=db_path)
            seen = {r["chunk_id"] for r in rows}
            rows += [r for r in extra if r["chunk_id"] not in seen]
    if config.topic_filter:
        conn = connect(db_path)
        keep = set()
        for r in rows:
            did = _doc_id_of(r["chunk_id"])
            if did in keep:
                continue
            t = conn.execute("SELECT topic FROM docs WHERE doc_id=?", (did,)).fetchone()
            if t and t["topic"] and f'"{config.topic_filter}"' in t["topic"]:
                keep.add(did)
        conn.close()
        rows = [r for r in rows if _doc_id_of(r["chunk_id"]) in keep][:k]
    out = [{"chunk_id": r["chunk_id"], "doc_id": _doc_id_of(r["chunk_id"]),
            "来源": f"{r['institution']} {r['report_date']} 《{r['title']}》",
            "章节": r["heading"], "text": r["text"][:1200]} for r in rows]
    return json.dumps(out, ensure_ascii=False)


def _tool_query_metrics(args: dict, config: AgentConfig, db_path: Path) -> str:
    rows = query_metrics(args["query"], limit=30)
    if not rows:
        return json.dumps({"结果": "指标库未覆盖该查询(城市/指标不在库内),改用 search_docs"}, ensure_ascii=False)
    return format_metrics(rows)


def _tool_query_claims(args: dict, config: AgentConfig, db_path: Path) -> str:
    conn = connect(db_path)
    try:
        topic = config.topic_filter or None
        if args.get("stance") or args.get("subject") or args.get("institution"):
            rows = query_claims(conn, subject=args.get("subject") or None,
                                stance=args.get("stance") or None,
                                institution=args.get("institution") or None,
                                date_from=args.get("date_from") or None,
                                date_to=args.get("date_to") or None,
                                city=args.get("city") or None, topic=topic, limit=25)
            out = [{"机构": r["institution"], "日期": r["report_date"], "立场": r["stance"],
                    "主题": r["subject"], "时期": r["horizon"], "观点": r["claim_text"],
                    "原文": r["evidence_quote"][:300], "doc_id": r["doc_id"]} for r in rows]
        else:
            # 无具体过滤条件 → 给立场矩阵(机构×时间),防止一次拉 200 条刷屏
            out = stance_matrix(conn, topic=topic)
            for g in out:
                g.pop("claims", None)
    finally:
        conn.close()
    return json.dumps(out, ensure_ascii=False)


def _tool_read_doc(args: dict, config: AgentConfig, db_path: Path) -> str:
    conn = connect(db_path)
    d = conn.execute("SELECT doc_id, title, institution, report_date, file_path FROM docs WHERE doc_id=?",
                     (args.get("doc_id") or "",)).fetchone()
    if not d:
        conn.close()
        return json.dumps({"error": f"doc_id 不存在: {args.get('doc_id')}"}, ensure_ascii=False)
    headings = [r["heading"] for r in conn.execute(
        "SELECT DISTINCT heading FROM chunks WHERE doc_id=? AND heading != '' ORDER BY ord LIMIT 40",
        (d["doc_id"],))]
    conn.close()
    md = RAW_MD / (Path(d["file_path"]).stem + ".md")
    head = md.read_text(encoding="utf-8")[:READ_DOC_MAX_CHARS] if md.exists() else ""
    return json.dumps({"doc_id": d["doc_id"], "title": d["title"], "机构": d["institution"],
                       "日期": d["report_date"], "章节大纲": headings, "正文开头": head},
                      ensure_ascii=False)


_DISPATCH = {"search_docs": _tool_search_docs, "query_metrics": _tool_query_metrics,
             "query_claims": _tool_query_claims, "read_doc": _tool_read_doc}


def _exec_tool(name: str, args: dict, config: AgentConfig, db_path: Path) -> str:
    fn = _DISPATCH.get(name)
    if not fn:
        return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
    try:
        return fn(args, config, db_path)[:TOOL_RESULT_MAX_CHARS]
    except Exception as e:  # noqa: BLE001 工具失败反馈给模型,循环继续
        return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


# ---------- system prompt(R2 四段式) ----------

def _system_prompt(config: AgentConfig) -> str:
    scope = ("你只回答房地产相关问题,工具检索已自动限定在房地产语料内。"
             if config.topic_filter else "你可回答语料库覆盖的任意主题(房地产/宏观/商品/行业)。")
    return f"""你是研报研判 agent。{scope}

铁律:
1. 禁止凭记忆回答,必须先调用工具取证(首轮系统已强制)
2. 复杂问题拆成子问题,分别从 query_metrics(精确数字)/ query_claims(机构观点)/ search_docs(原文细节)交叉取证;同一轮可并行发多个工具调用
3. 数字原文照抄(含单位与统计期);机构观点冲突时并列呈现,禁止调和
4. 工具没查到的信息明说"现有资料未覆盖",禁止编造
5. 引用格式 [机构 日期],如 [Goldman Sachs 2026-04-09]

回答格式(严格遵守,四段式):
【我的判断】一句话结论
【支持的机构】各机构观点+依据,带引用
【不同意见的机构】各机构观点+依据,带引用(没有就写"语料中未见明确反对")
【我为什么这么选】取舍理由(数据口径/时效/样本/逻辑)

直接输出四段正文,不要"根据检索结果我现在可以…"这类前言。""" + (("\n\n" + config.system_extra) if config.system_extra else "")


# ---------- 主循环 ----------

def run_agent(query: str, config: AgentConfig | None = None, client=None,
              db_path: Path = DB) -> dict:
    """单轮深研:返回 {answer, llm_calls, tool_calls, usage, trace_path, rounds}。"""
    config = config or AgentConfig()
    cfg = llm_config()
    if client is None:
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    model = config.model or cfg["model"]
    thinking_on = os.environ.get("LLM_THINKING") == "1"

    trace: list[dict] = []
    contexts: list[dict] = []        # capture_contexts 时收集 {tool, args, content}
    messages = [{"role": "system", "content": _system_prompt(config)},
                {"role": "user", "content": query}]
    usage = {"in": 0, "out": 0}
    n_tool_calls = 0

    for rnd in range(1, config.max_llm_calls + 1):
        if config.on_event:
            config.on_event({"type": "llm_round", "round": rnd, "max": config.max_llm_calls})
        is_last = rnd == config.max_llm_calls
        if is_last:
            # 预算耗尽的强制合成轮:明确告知模型必须立即作答(实测:不告知会在终答里复读"让我继续搜索")
            messages = messages + [{"role": "user", "content": (
                "(系统提示:检索预算已用完。必须基于已获得的信息立即给出最终答案;"
                "确实未覆盖就按格式明说,禁止说'让我继续搜索'类话头。)")}]
        kwargs: dict = {"model": model, "messages": messages}
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if not is_last:
            kwargs["tools"] = TOOLS
            if rnd <= config.min_tool_rounds:
                kwargs["tool_choice"] = "required"  # 首轮必查(保底轮数内强制)
        if not thinking_on:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception:
            if kwargs.get("tool_choice") == "required":
                # provider 不支持 tool_choice=required 的兼容回退:降级 auto 继续
                kwargs["tool_choice"] = "auto"
                trace.append({"round": rnd, "kind": "compat_fallback",
                              "note": "tool_choice=required 被拒,降级 auto"})
                resp = client.chat.completions.create(**kwargs)
            else:
                raise
        u = getattr(resp, "usage", None)
        if u:
            usage["in"] += u.prompt_tokens
            usage["out"] += u.completion_tokens
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        trace.append({"round": rnd, "kind": "llm_call", "tool_calls": len(tool_calls),
                      "content_chars": len(msg.content or ""),
                      "tokens": {"in": u.prompt_tokens, "out": u.completion_tokens} if u else None})

        if not tool_calls:
            if config.on_event:
                config.on_event({"type": "final", "llm_calls": rnd, "tool_calls": n_tool_calls})
            answer = (msg.content or "").strip()
            trace.append({"round": rnd, "kind": "final", "chars": len(answer)})
            return {"answer": answer, "llm_calls": rnd, "tool_calls": n_tool_calls,
                    "usage": usage, "trace_path": _dump_trace(query, config, trace),
                    "rounds": trace, "contexts": contexts}

        # 有工具调用:并行执行,结果按序塞回
        messages.append({"role": "assistant", "content": msg.content,
                         "tool_calls": [tc.model_dump() if hasattr(tc, "model_dump") else tc
                                        for tc in tool_calls]})
        jobs = [(tc.id, tc.function.name,
                 json.loads(tc.function.arguments or "{}")) for tc in tool_calls]
        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
            results = dict(zip([j[0] for j in jobs],
                               pool.map(lambda j: _exec_tool(j[1], j[2], config, db_path), jobs)))
        for tc_id, name, args in jobs:
            n_tool_calls += 1
            trace.append({"round": rnd, "kind": "tool_call", "tool": name, "args": args,
                          "result_chars": len(results[tc_id]),
                          "result_head": results[tc_id][:200]})
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": results[tc_id]})
            if config.on_event:
                config.on_event({"type": "tool_done", "tool": name,
                                 "result_chars": len(results[tc_id])})
            if config.capture_contexts:
                contexts.append({"tool": name, "args": args, "content": results[tc_id]})

    raise RuntimeError("unreachable")  # 最后一轮强制无工具,必有终答


REWRITE_PROMPT = """把用户的新问题改写成脱离对话历史也能独立理解的完整问题。
规则:
1. 补全指代与省略(如"那瑞银呢"→结合历史展开成完整问句;实体以历史中的注册实体为准)
2. 保留原意与原问题范围,不新增子问题,不回答问题
3. 问题本身已自足、或无历史时,原样输出
4. 只输出改写后的问题本身,一行,不要任何解释"""


def resolve_query(query: str, context: str, client=None, model: str | None = None) -> str:
    """指代消解:context 为空(首轮)直接返回原问题,零调用;
    改写失败/超时 → 原样返回原问题(多轮退化单轮,不阻断)。"""
    if not context:
        return query
    cfg = llm_config()
    if client is None:
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    try:
        kwargs: dict = {"model": model or cfg["model"],
                        "messages": [{"role": "system", "content": REWRITE_PROMPT},
                                     {"role": "user", "content": f"{context}\n\n【新问题】{query}"}]}
        if os.environ.get("LLM_THINKING") != "1":
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        resp = client.chat.completions.create(**kwargs)
        rewritten = (resp.choices[0].message.content or "").strip()
        return rewritten or query
    except Exception:  # noqa: BLE001 兜底:多轮退化单轮
        return query


def _dump_trace(query: str, config: AgentConfig, trace: list[dict]) -> str:
    if not config.trace:
        return ""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    import uuid
    path = TRACES_DIR / f"agent-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jsonl"  # 并发防同名
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "meta", "query": query, "topic_filter": config.topic_filter,
                            "max_llm_calls": config.max_llm_calls}, ensure_ascii=False) + "\n")
        for t in trace:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return str(path)
