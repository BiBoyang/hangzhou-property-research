"""Web 层：FastAPI 页面 —— 问答 / 市场总览 / 指标看板 / 报告浏览。

启动: python web/app.py  (默认绑 0.0.0.0:8666，局域网可访问)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from lib_db import connect  # noqa: E402
from lib_search import search  # noqa: E402
from lib_paths import APP, DB, RAW_MD  # noqa: E402
import lib_metrics_query as mq  # noqa: E402
import market_phase  # noqa: E402

WARM_READY = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时预载 embedding 模型，消除首个查询的 30s 冷启动。"""
    import threading

    def _w():
        global WARM_READY
        try:
            from lib_search import _get_model

            _get_model()
            WARM_READY = True
            print("[warmup] 预热完成，可以提问了", flush=True)
        except Exception as e:
            print(f"[warmup] 预热失败：{e}", flush=True)

    threading.Thread(target=_w, daemon=True).start()
    yield


app = FastAPI(title="房地产研报研判系统", docs_url="/api-docs", lifespan=lifespan)
templates = Jinja2Templates(directory=str(APP / "web" / "templates"))
app.mount("/static", StaticFiles(directory=str(APP / "web" / "static")), name="static")


@app.get("/api/health")
def api_health():
    return {"warm": WARM_READY}


@app.get("/", response_class=HTMLResponse)
def ask_page(request: Request):
    return templates.TemplateResponse(request, "ask.html")


@app.post("/api/ask")
def api_ask(q: str = Query(..., min_length=2)):
    chunks = search(q, k=8)
    from lib_metrics_query import format_metrics, query_metrics

    metrics_block = format_metrics(query_metrics(q))
    if not chunks and not metrics_block:
        return {"answer": "未检索到相关内容。", "sources": []}
    try:
        from lib_llm import chat

        answer = chat(q, chunks, metrics_block)
    except Exception as e:  # key 未配/网络错误时降级为只返回检索结果
        answer = f"（生成层不可用：{e}。以下为原始检索结果）"
    return {
        "answer": answer,
        "sources": [
            {"institution": c["institution"], "date": c["report_date"],
             "title": c["title"], "heading": c["heading"], "text": c["text"][:600]}
            for c in chunks
        ],
    }


@app.post("/api/ask/stream")
def api_ask_stream(q: str = Query(..., min_length=2)):
    """NDJSON 流式问答：阶段进度 → 生成 delta → 完成带引用。"""
    import json as _json

    from fastapi.responses import StreamingResponse

    def gen():
        t0 = time.time()
        if not WARM_READY:
            yield _json.dumps({"stage": "模型首次加载中（约 30 秒，仅此一次）…"}) + "\n"
        yield _json.dumps({"stage": "检索中（向量 + 全文索引）…"}) + "\n"
        chunks = search(q, k=8)
        from lib_metrics_query import format_metrics, query_metrics

        rows = query_metrics(q)
        metrics_block = format_metrics(rows)
        yield _json.dumps({
            "stage": f"检索完成：文档 {len(chunks)} 段 + 指标 {len(rows)} 条"
                     f"（{time.time()-t0:.0f}s），生成回答中…"
        }) + "\n"
        if not chunks and not metrics_block:
            yield _json.dumps({"done": True, "answer": "未检索到相关内容。", "sources": []}) + "\n"
            return
        answer_parts = []
        try:
            from lib_llm import chat_stream

            for delta in chat_stream(q, chunks, metrics_block):
                answer_parts.append(delta)
                yield _json.dumps({"delta": delta}) + "\n"
        except Exception as e:
            yield _json.dumps({"delta": f"（生成层不可用：{e}）"}) + "\n"
        sources = [
            {"institution": c["institution"], "date": c["report_date"],
             "title": c["title"], "heading": c["heading"], "text": c["text"][:600]}
            for c in chunks
        ]
        yield _json.dumps({"done": True, "answer": "".join(answer_parts), "sources": sources}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    conn = connect(DB)
    try:
        names = [r["metric_name"] for r in conn.execute(
            "SELECT DISTINCT metric_name FROM metrics ORDER BY metric_name")]
        cities = [r["city"] for r in conn.execute(
            "SELECT DISTINCT city FROM metrics WHERE city != '' ORDER BY city")]
    finally:
        conn.close()
    return templates.TemplateResponse(request, "metrics.html", {"metric_names": names, "cities": cities})


@app.get("/api/metrics")
def api_metrics(metric: str = "", city: str = ""):
    conn = connect(DB)
    try:
        sql = "SELECT * FROM metrics WHERE 1=1"
        params: list = []
        if metric:
            sql += " AND metric_name=?"
            params.append(metric)
        if city:
            sql += " AND city=?"
            params.append(city)
        sql += " ORDER BY period"
        rows = [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()
    return {"rows": rows}


@app.get("/docs", response_class=HTMLResponse)
def docs_page(request: Request):
    conn = connect(DB)
    docs = [dict(r) for r in conn.execute(
        "SELECT * FROM docs ORDER BY report_date DESC")]
    for d in docs:
        d["cities"] = json.loads(d["cities"] or "[]")
    return templates.TemplateResponse(request, "docs.html", {"docs": docs})


@app.get("/docs/{doc_id}", response_class=HTMLResponse)
def doc_view(request: Request, doc_id: str):
    conn = connect(DB)
    doc = conn.execute("SELECT * FROM docs WHERE doc_id=?", (doc_id,)).fetchone()
    if not doc:
        return HTMLResponse("not found", status_code=404)
    md_file = RAW_MD / (doc["title"] + ".md")
    md = md_file.read_text(encoding="utf-8") if md_file.exists() else "（原文未解析）"
    return templates.TemplateResponse(request, "doc_view.html", {"doc": dict(doc), "md": md})


# ---------------------------------------------------------------------------
# Step 3E：市场总览页 + 4 个只读 API
#
# 全部复用 Step 3A~3D 的结构化只读接口（内部 mode=ro 连接，不经
# lib_db.connect()，无 schema/迁移/commit）：不写库、不加载 embedding、
# 不调用 LLM 或外部网络、不要求 LLM_API_KEY；页面只展示事实、规则结果
# 与风险提示，缺失期断线不填 0，同期冲突全保留，limited 不进强结论。
# as_of 历史快照语义与 Step 3D 一致：直接复用 market_phase 的过滤实现，
# 不在此重复第二套时期截断逻辑。
# ---------------------------------------------------------------------------

# 只读查询所用的库路径；测试可替换为临时库（正式库只读，指纹不变）
OVERVIEW_DB = DB
OVERVIEW_DEFAULT_CITIES = ["杭州", "上海", "北京", "深圳"]


def _query_failed(e: Exception) -> JSONResponse:
    """下游查询错误 → 结构化 500；traceback 只进服务端日志，不进响应体。"""
    print(f"[overview] 只读查询失败: {type(e).__name__}: {e}", flush=True)
    return JSONResponse(status_code=500, content={
        "error": {"code": "overview_query_failed",
                  "message": "只读查询失败：数据层暂时不可用，请稍后重试（详情见服务端日志）"}})


def _clean_city(city: str | None, default: str | None = "杭州") -> str:
    if city is None and default:
        city = default  # 参数缺省 → 默认城市；显式空串/空白仍是非法参数
    city = (city or "").strip()
    if not city:
        raise HTTPException(status_code=422, detail="city 不能为空（默认可用 city=杭州）")
    if len(city) > 40:
        raise HTTPException(status_code=422, detail="city 过长（≤40 字符）")
    return city


def _clean_metric(metric: str | None) -> str:
    metric = (metric or "").strip()
    if not metric:
        raise HTTPException(status_code=422, detail="metric 不能为空")
    if len(metric) > 80:
        raise HTTPException(status_code=422, detail="metric 过长（≤80 字符）")
    return metric


def _clean_as_of(as_of: str | None) -> str | None:
    if as_of is None or as_of.strip() == "":
        return None
    as_of = as_of.strip()
    if not mq._period_granularity(as_of):
        raise HTTPException(status_code=422, detail=(
            f"as_of 格式非法: {as_of!r}（支持 2026-03 / 2026Q1 / 2026-H1 / 2026）"))
    return mq._normalize_period(as_of)


def _clean_window(period_start: str | None, period_end: str | None):
    ps, pe = (period_start or "").strip(), (period_end or "").strip()
    if not ps and not pe:
        return None, None
    if not ps or not pe:
        raise HTTPException(status_code=422, detail="period_start 与 period_end 必须同时给出")
    g1, g2 = mq._period_granularity(ps), mq._period_granularity(pe)
    if not g1 or not g2:
        raise HTTPException(status_code=422, detail=(
            f"period_start/period_end 格式非法: {ps!r}, {pe!r}"
            "（支持 2026-03 / 2026Q1 / 2026-H1 / 2026）"))
    if g1 != g2:
        raise HTTPException(status_code=422, detail=(
            f"period_start({g1}) 与 period_end({pe}) 粒度不一致，范围不得跨粒度"))
    return mq._normalize_period(ps), mq._normalize_period(pe)


def _clean_cities(cities: str | None) -> list[str]:
    raw = (cities if cities is not None else ",".join(OVERVIEW_DEFAULT_CITIES)).split(",")
    raw = [c.strip() for c in raw if c.strip()]
    if len(raw) < 2:
        raise HTTPException(status_code=422, detail=(
            "cities 至少需要 2 个城市（逗号分隔，如 杭州,上海,北京,深圳）"))
    if any(len(c) > 40 for c in raw):
        raise HTTPException(status_code=422, detail="city 过长（≤40 字符）")
    return list(dict.fromkeys(raw))  # 去重保序


def _apply_as_of_to_series(res: dict, as_of: str | None) -> dict:
    """as_of 截断序列结果：点/冲突/缺失期统一 ≤ as_of，覆盖计数同步重算。"""
    if not as_of:
        return res
    key = mq._period_sort_key(as_of)
    series = []
    for s in res["series"]:
        s = market_phase._filter_by_as_of(s, key)
        s["missing_periods"] = [p for p in s["missing_periods"]
                                if mq._period_sort_key(p) <= key]
        series.append(s)
    covered = {p["period"] for s in series for p in s["points"]}
    covered |= {c["period"] for s in series for c in s["conflicts"]}
    requested = [p for p in res["coverage"]["requested_periods"]
                 if mq._period_sort_key(p) <= key]
    returned_points = sum(len(s["points"]) for s in series)
    conflict_points = sum(len(c["points"])
                          for s in series for c in s["conflicts"])
    coverage = {**res["coverage"], "requested_periods": requested,
                "returned_points": returned_points,
                "conflict_points": conflict_points,
                "missing_period_count": sum(1 for p in requested if p not in covered)}
    return {**res, "series": series, "coverage": coverage,
            "filters": {**res["filters"], "effective_as_of": as_of}}


def _apply_as_of_to_compare(rep: dict, as_of: str | None) -> dict:
    """as_of 截断比较组（含共同覆盖期重算），并同步重算汇总状态位。"""
    if not as_of:
        return rep
    key = mq._period_sort_key(as_of)
    groups = [market_phase._filter_compare_group_by_as_of(g, key)
              for g in rep["comparison_groups"]]
    flags = [f for f in rep["summary"]["status_flags"]
             if f not in ("conflicted", "insufficient_common_periods")]
    if any(g["has_conflicts"] for g in groups):
        flags.append("conflicted")
    if any(g["insufficient_common_periods"] for g in groups):
        flags.append("insufficient_common_periods")
    return {**rep, "comparison_groups": groups,
            "requested_periods": [p for p in rep["requested_periods"]
                                  if mq._period_sort_key(p) <= key],
            "filters": {**rep["filters"], "effective_as_of": as_of},
            "summary": {**rep["summary"], "status_flags": flags}}


@app.get("/overview", response_class=HTMLResponse)
def overview_page(request: Request):
    cities = ["杭州"] + [c for c in mq.CITIES if c != "杭州"]
    return templates.TemplateResponse(request, "overview.html", {"cities": cities})


@app.get("/api/overview")
def api_overview(city: str | None = Query(None),
                 as_of: str | None = Query(None),
                 include_forecasts: bool = Query(False)):
    """市场阶段评估：阶段 + 支持证据 + 反向证据 + 风险 + coverage + watch_next。"""
    city = _clean_city(city)
    as_of_n = _clean_as_of(as_of)
    try:
        rep = market_phase.assess_market_phase(
            city=city, as_of=as_of_n, include_forecasts=include_forecasts,
            db_path=OVERVIEW_DB)
    except ValueError as e:  # 规则层参数校验（时期格式等）
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        return _query_failed(e)
    return rep


@app.get("/api/overview/series")
def api_overview_series(metric: str = Query(...),
                        city: str | None = Query(None),
                        as_of: str | None = Query(None),
                        period_start: str | None = Query(None),
                        period_end: str | None = Query(None),
                        include_forecasts: bool = Query(False)):
    """单指标多来源原始序列：来源分线、缺失期、同期冲突，不平均不合并。"""
    m = _clean_metric(metric)
    city = _clean_city(city)
    as_of_n = _clean_as_of(as_of)
    ps, pe = _clean_window(period_start, period_end)
    try:
        res = mq.query_metric_series(
            city=city, metric_name=m, period_start=ps, period_end=pe,
            include_forecasts=include_forecasts, db_path=OVERVIEW_DB)
    except Exception as e:
        return _query_failed(e)
    return _apply_as_of_to_series(res, as_of_n)


@app.get("/api/overview/coverage")
def api_overview_coverage(city: str | None = Query(None),
                          metric_name: str | None = Query(None),
                          as_of: str | None = Query(None),
                          period_start: str | None = Query(None),
                          period_end: str | None = Query(None),
                          actual_only: bool = Query(True)):
    """覆盖与缺口摘要：序列/城市-指标/全局三层，只描述数据存在性。"""
    city = _clean_city(city)
    m = _clean_metric(metric_name) if (metric_name or "").strip() else None
    as_of_n = _clean_as_of(as_of)
    ps, pe = _clean_window(period_start, period_end)
    try:
        cov = mq.summarize_series_coverage(
            city=city, metric_name=m, period_start=ps, period_end=pe,
            actual_only=actual_only, db_path=OVERVIEW_DB)
    except Exception as e:
        return _query_failed(e)
    if as_of_n:
        cov = market_phase._filter_coverage_by_as_of(cov, mq._period_sort_key(as_of_n))
    return cov


@app.get("/api/overview/compare")
def api_overview_compare(metric: str = Query(...),
                         cities: str | None = Query(None),
                         as_of: str | None = Query(None),
                         period_start: str | None = Query(None),
                         period_end: str | None = Query(None),
                         include_forecasts: bool = Query(False)):
    """城市横向比较：严格可比组 + limited 并列，不计算差值/倍数/排名。"""
    m = _clean_metric(metric)
    city_list = _clean_cities(cities)
    as_of_n = _clean_as_of(as_of)
    ps, pe = _clean_window(period_start, period_end)
    try:
        rep = mq.compare_metric_across_cities(
            cities=city_list, metric_name=m, period_start=ps, period_end=pe,
            include_forecasts=include_forecasts, db_path=OVERVIEW_DB)
    except Exception as e:
        return _query_failed(e)
    return _apply_as_of_to_compare(rep, as_of_n)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("RAG_PORT", "8666"))
    uvicorn.run(app, host="0.0.0.0", port=port)
