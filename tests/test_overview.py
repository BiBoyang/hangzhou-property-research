"""Step 3E：/overview 总览页 + 4 个只读 API 测试。

全部数据场景使用临时 SQLite 库（plain sqlite3 建库，不经 lib_db.connect()）；
FastAPI TestClient 不进 lifespan（不触发 app 启动预热线程，不加载 embedding）。
正式库只做只读验证与指纹比对（_FormalGuard）。不调用任何外部网络（socket 拦截）。
"""
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))
sys.path.insert(0, str(APP / "web"))

import app as web_app  # noqa: E402

_SCHEMA = """
CREATE TABLE metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,
  metric_name TEXT, value REAL, value_text TEXT, unit TEXT,
  comparison_type TEXT, comparison_value REAL, is_forecast INTEGER DEFAULT 0,
  source_page TEXT, source_quote TEXT, source_url TEXT, source_file TEXT
);
"""

BANNED_WORDS = ("应该买", "不该买", "建议买", "建议卖", "最佳入场", "一定涨", "一定跌", "风险分")


def _row(period, city="杭州", value=100.0, metric_name="secondary_volume_units", **kw):
    row = {
        "doc_id": None, "institution": "贝壳", "period": period, "city": city,
        "segment": "secondary_home", "metric_name": metric_name, "value": value,
        "value_text": None, "unit": "套", "comparison_type": None,
        "comparison_value": None, "is_forecast": 0, "source_page": "p1",
        "source_quote": f"引文-{city}-{period}", "source_url": None,
        "source_file": "beike.csv",
    }
    row.update(kw)
    return row


def _mom(vals, city="杭州", seg="secondary_home", inst="贝壳", sf="beike.csv"):
    return [_row(f"2026-{m:02d}", city=city, value=v, metric_name="mom_change_pct",
                 unit="pct", segment=seg, institution=inst, source_file=sf,
                 comparison_type="mom", comparison_value=v)
            for m, v in enumerate(vals, 1)]


def _vol(vals, city="杭州", inst="贝壳", sf="beike.csv"):
    return [_row(f"2026-{m:02d}", city=city, value=v, institution=inst, source_file=sf)
            for m, v in enumerate(vals, 1)]


def _peer(vals, city, inst="克而瑞", sf="cric.csv"):
    return [_row(f"2026-{m:02d}", city=city, value=v, institution=inst, source_file=sf)
            for m, v in enumerate(vals, 1)]


class _OverviewBase(unittest.TestCase):
    """每个测试独立临时库（避免类内数据串场）；TestClient 不进 lifespan。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "overview.db"
        conn = sqlite3.connect(str(self.db))
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()
        self._saved_db = web_app.OVERVIEW_DB
        web_app.OVERVIEW_DB = self.db
        from fastapi.testclient import TestClient

        self.client = TestClient(web_app.app)  # 不用 with：不触发 startup 预热

    def tearDown(self):
        web_app.OVERVIEW_DB = self._saved_db
        self._tmp.cleanup()

    def seed(self, rows):
        conn = sqlite3.connect(str(self.db))
        cols = list(rows[0])
        conn.executemany(
            f"INSERT INTO metrics ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
            rows)
        conn.commit()
        conn.close()

    def db_sha256(self) -> str:
        return hashlib.sha256(self.db.read_bytes()).hexdigest()


class PageTest(_OverviewBase):
    """1./2. 页面 200、导航入口；本地 ECharts、无外部 CDN。"""

    def test_overview_page_returns_200(self):
        r = self.client.get("/overview")
        self.assertEqual(r.status_code, 200)
        self.assertIn("市场总览", r.text)
        self.assertIn("总览", r.text)

    def test_nav_links_include_overview(self):
        r = self.client.get("/overview")
        self.assertIn('href="/overview"', r.text)
        for tpl in ("ask.html", "metrics.html", "docs.html", "doc_view.html"):
            src = (APP / "web" / "templates" / tpl).read_text(encoding="utf-8")
            self.assertIn('href="/overview"', src, msg=tpl)
            self.assertIn('href="/"', src, msg=tpl)  # 原有导航未被移除

    def test_page_uses_local_echarts_no_external_cdn(self):
        src = (APP / "web" / "templates" / "overview.html").read_text(encoding="utf-8")
        self.assertIn('src="/static/echarts.min.js"', src)
        self.assertNotIn("cdn.jsdelivr", src)
        self.assertNotRegex(src, r"""src=["']https?://""")
        self.assertTrue((APP / "web" / "static" / "echarts.min.js").exists())

    def test_default_city_is_hangzhou(self):
        """7. 页面默认杭州：下拉首选项与 JS 兜底均为杭州。"""
        r = self.client.get("/overview")
        first = re.search(r'<select id="city">(.*?)</select>', r.text, re.S).group(1)
        self.assertTrue(first.strip().startswith('<option value="杭州"'), msg=first[:120])
        src = (APP / "web" / "templates" / "overview.html").read_text(encoding="utf-8")
        self.assertIn("|| '杭州'", src)


class OverviewApiTest(_OverviewBase):
    """3. /api/overview 返回阶段、置信度、证据、风险、coverage、watch_next。"""

    def test_overview_structure(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        r = self.client.get("/api/overview")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        for key in ("city", "as_of", "phase", "supporting_evidence", "counter_evidence",
                    "risk_signals", "coverage", "watch_next", "disclaimer", "signals",
                    "warnings", "filters"):
            self.assertIn(key, d)
        self.assertEqual(d["city"], "杭州")
        self.assertEqual(d["phase"]["code"], "decline_narrowing")
        self.assertIn(d["phase"]["confidence"], ("low", "medium", "high"))
        self.assertIn("rule_version", d["phase"])
        self.assertTrue(d["counter_evidence"])          # 成交连续下降在反向证据
        self.assertTrue(any(e["signal_code"] == "price_secondary_home"
                            for e in d["supporting_evidence"]))
        for risk in d["risk_signals"]:
            self.assertIn("code", risk)
            self.assertIn("trigger", risk)
            self.assertIn("warning", risk)
        self.assertTrue(d["coverage"]["summary"])
        self.assertTrue(d["watch_next"])

    def test_phase_card_conservative_wording_in_template(self):
        """顶部措辞保守、置信度与阶段同显，不允许只突出阶段。"""
        src = (APP / "web" / "templates" / "overview.html").read_text(encoding="utf-8")
        self.assertIn("当前结构化证据更接近", src)
        self.assertIn("证据充分程度", src)
        self.assertIn("conf-low", src)  # 低置信度有显著样式


class SeriesApiTest(_OverviewBase):
    """4./10./18./20. 序列 API：多来源、缺失期、冲突、结构化空结果。"""

    def test_multi_source_series_not_merged(self):
        self.seed(_vol([100.0, 90.0], inst="贝壳", sf="beike.csv")
                  + _vol([110.0, 95.0], inst="Morgan Stanley", sf="ms_note.pdf"))
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["filters"]["city"], "杭州")
        self.assertEqual(d["filters"]["metric_name"], "secondary_volume_units")
        keys = {s["source_key"] for s in d["series"]}
        self.assertEqual(len(keys), 2)  # 两个来源各自成线，不合并
        self.assertEqual({s["institution"] for s in d["series"]},
                         {"贝壳", "Morgan Stanley"})

    def test_missing_periods_not_filled_with_zero(self):
        self.seed(_vol([100.0, 90.0]) + [_row("2026-04", value=70.0)])  # 2026-03 缺失
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units"})
        d = r.json()
        pts = d["series"][0]["points"]
        self.assertEqual([p["period"] for p in pts], ["2026-01", "2026-02", "2026-04"])
        self.assertNotIn("2026-03", {p["period"] for p in pts})
        blob = json.dumps(d)
        self.assertNotIn('"period": "2026-03"', blob)  # 缺失期不出现在响应中，更没有被填 0
        for p in pts:
            self.assertNotEqual(p["value"], 0)  # 不存在被填 0 的伪点

    def test_conflicts_kept_all_values(self):
        rows = _vol([100.0, 90.0, 80.0])
        rows.append(_row("2026-02", value=95.0))  # 与 90.0 同组同源同期多值
        self.seed(rows)
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units"})
        d = r.json()
        s = d["series"][0]
        self.assertEqual(len(s["conflicts"]), 1)
        self.assertEqual(s["conflicts"][0]["period"], "2026-02")
        self.assertEqual(sorted(p["value"] for p in s["conflicts"][0]["points"]),
                         [90.0, 95.0])  # 两个值都保留
        self.assertNotIn("2026-02", [p["period"] for p in s["points"]])
        self.assertTrue(any("同期多值" in w for w in d["warnings"]))

    def test_unknown_metric_structured_empty(self):
        self.seed(_vol([100.0]))
        r = self.client.get("/api/overview/series", params={"metric": "no_such_metric"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["coverage"]["series_count"], 0)
        self.assertEqual(d["series"], [])
        self.assertTrue(d["warnings"])

    def test_forecast_lines_are_separate(self):
        rows = _vol([100.0, 90.0]) + [
            _row("2026-03", value=85.0, is_forecast=1, source_file="ms_forecast.csv",
                 institution="Morgan Stanley")]
        self.seed(rows)
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units",
                                    "include_forecasts": "true"})
        d = r.json()
        actuals = [s for s in d["series"] if not s["is_forecast"]]
        forecasts = [s for s in d["series"] if s["is_forecast"]]
        self.assertTrue(actuals and forecasts)
        self.assertNotIn(forecasts[0]["source_key"], {s["source_key"] for s in actuals})


class CoverageApiTest(_OverviewBase):
    """5. 覆盖摘要。"""

    def test_coverage_summary(self):
        # 同一序列内部缺口（01,02,04 → 缺 03）+ 另一来源的单点序列
        self.seed(_vol([100.0, 90.0]) + [_row("2026-04", value=80.0),
                                         _row("2026-05", value=1.0, source_file="other.csv")])
        r = self.client.get("/api/overview/coverage")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["filters"]["city"], "杭州")
        self.assertEqual(d["summary"]["overall_status"], "ok")
        self.assertEqual(d["summary"]["series_count"], 2)
        self.assertEqual(d["summary"]["actual_series_count"], 2)
        self.assertEqual(d["summary"]["status_counts"]["multi_point_with_internal_gaps"], 1)
        self.assertEqual(d["summary"]["status_counts"]["single_point"], 1)
        self.assertTrue(d["series"] and d["city_metric_summary"])


class CompareApiTest(_OverviewBase):
    """6./21. 比较 API：comparable、limited、共同覆盖期；limited 不进强结论。"""

    def test_strict_comparable_group_with_common_periods(self):
        self.seed(_vol([100.0, 110.0, 120.0], inst="克而瑞", sf="cric.csv")
                  + _peer([300.0, 310.0, 320.0], "上海")
                  + _peer([400.0, 410.0, 420.0], "北京"))
        r = self.client.get("/api/overview/compare",
                            params={"metric": "secondary_volume_units"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["filters"]["cities"], ["杭州", "上海", "北京", "深圳"])
        comps = [g for g in d["comparison_groups"] if g["comparability"] == "comparable"]
        self.assertTrue(comps)
        g = comps[0]
        self.assertEqual(g["common_periods"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(g["common_period_count"], 3)
        self.assertFalse(g["insufficient_common_periods"])
        cities_in_group = {c["city"] for c in g["cities"]}
        self.assertEqual(cities_in_group, {"杭州", "上海", "北京"})

    def test_limited_group_side_by_side_only(self):
        self.seed(_vol([100.0, 90.0]) + _peer([300.0, 310.0], "上海", inst="中指", sf="zz.csv"))
        r = self.client.get("/api/overview/compare",
                            params={"metric": "secondary_volume_units"})
        d = r.json()
        limited = [g for g in d["comparison_groups"] if g["comparability"] == "limited"]
        self.assertEqual(d["summary"]["limited_group_count"], len(limited))
        self.assertTrue(limited)
        self.assertTrue(any("不计算差值" in w and "排名" in w
                            for w in limited[0]["warnings"]))
        for g in d["comparison_groups"]:
            for ce in g["cities"]:
                for p in ce["points"]:
                    self.assertIsInstance(p["value"], (int, float))  # 只有原始值，无派生差值

    def test_default_cities_hangzhou_first(self):
        self.seed(_vol([100.0]))
        r = self.client.get("/api/overview/compare",
                            params={"metric": "secondary_volume_units"})
        d = r.json()
        self.assertEqual(d["filters"]["cities"], ["杭州", "上海", "北京", "深圳"])
        self.assertIn("深圳", d["uncovered_cities"])  # 无数据城市明确标注，不用别的城市代替

    def test_unmatched_metric_structured_empty(self):
        self.seed(_vol([100.0]))
        r = self.client.get("/api/overview/compare", params={"metric": "no_such_metric"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["summary"]["overall_status"], "unmatched_metric")
        self.assertEqual(d["comparison_groups"], [])


class AsOfTest(_OverviewBase):
    """8. as_of 贯穿 overview、series、coverage、compare。"""

    def setUp(self):
        super().setUp()
        rows = (_mom([-1.0, -2.0, -3.0, 0.5]) + _vol([100.0, 95.0, 90.0, 95.0])
                + _peer([300.0, 310.0, 320.0, 330.0], "上海"))
        self.seed(rows)

    def test_as_of_changes_overview_phase(self):
        full = self.client.get("/api/overview").json()
        snap = self.client.get("/api/overview", params={"as_of": "2026-03"}).json()
        self.assertEqual(snap["as_of"], "2026-03")
        self.assertEqual(snap["filters"]["effective_period_end"], "2026-03")
        self.assertNotEqual(full["phase"]["code"], snap["phase"]["code"])
        price = next(s for s in snap["signals"] if s["code"] == "price_secondary_home")
        self.assertEqual(price["periods"], ["2026-01", "2026-02", "2026-03"])

    def test_as_of_bounds_series(self):
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units", "as_of": "2026-03"})
        d = r.json()
        self.assertEqual(d["filters"]["effective_as_of"], "2026-03")
        for s in d["series"]:
            self.assertTrue(all(p["period"] <= "2026-03" for p in s["points"]))
            self.assertTrue(all(c["period"] <= "2026-03" for c in s["conflicts"]))

    def test_as_of_recomputes_series_coverage_counts(self):
        """快照截断后 coverage 统计只计算截断后的点和冲突。"""
        conn = sqlite3.connect(str(self.db))
        conn.execute("DELETE FROM metrics")
        conn.commit()
        conn.close()
        self.seed([
            _row("2026-01", value=100.0),
            _row("2026-02", value=90.0),
            _row("2026-03", value=80.0),
            _row("2026-03", value=81.0, source_quote="冲突值"),
        ])
        d = self.client.get("/api/overview/series", params={
            "metric": "secondary_volume_units",
            "as_of": "2026-02",
            "period_start": "2026-01",
            "period_end": "2026-03",
        }).json()
        self.assertEqual(d["coverage"]["returned_points"], 2)
        self.assertEqual(d["coverage"]["conflict_points"], 0)
        self.assertEqual(d["coverage"]["requested_periods"], ["2026-01", "2026-02"])
        self.assertEqual(d["coverage"]["missing_period_count"], 0)

    def test_as_of_before_series_window_zeroes_coverage_counts(self):
        """as_of 早于请求窗口时不保留窗口之后的覆盖统计。"""
        self.seed([_row("2026-03", value=80.0), _row("2026-04", value=70.0)])
        d = self.client.get("/api/overview/series", params={
            "metric": "secondary_volume_units",
            "as_of": "2026-02",
            "period_start": "2026-03",
            "period_end": "2026-04",
        }).json()
        self.assertEqual(d["coverage"]["returned_points"], 0)
        self.assertEqual(d["coverage"]["conflict_points"], 0)
        self.assertEqual(d["coverage"]["missing_period_count"], 0)
        self.assertTrue(all(not s["points"] and not s["conflicts"] for s in d["series"]))

    def test_as_of_bounds_coverage(self):
        r = self.client.get("/api/overview/coverage", params={"as_of": "2026-03"})
        d = r.json()
        for s in d["series"]:
            self.assertLessEqual(s["last_period"], "2026-03")

    def test_as_of_bounds_compare_common_periods(self):
        r = self.client.get("/api/overview/compare",
                            params={"metric": "secondary_volume_units", "as_of": "2026-03"})
        d = r.json()
        for g in d["comparison_groups"]:
            for p in g["common_periods"]:
                self.assertLessEqual(p, "2026-03")


class ValidationTest(_OverviewBase):
    """9. 非法 city / as_of / period / metric / cities → 4xx。"""

    def test_invalid_params_return_4xx(self):
        cases = [
            ("/api/overview", {"city": ""}),
            ("/api/overview", {"city": "   "}),
            ("/api/overview", {"city": "杭" * 41}),
            ("/api/overview", {"as_of": "2026-13"}),
            ("/api/overview", {"as_of": "abc"}),
            ("/api/overview/series", {"metric": "x", "as_of": "2026-99"}),
            ("/api/overview/series", {"metric": "x", "period_start": "2026-01"}),
            ("/api/overview/series", {"metric": "x", "period_end": "2026-02"}),
            ("/api/overview/series", {"metric": "x",
                                      "period_start": "2026-01", "period_end": "2026Q2"}),
            ("/api/overview/series", {"metric": "x",
                                      "period_start": "2026-01", "period_end": "2026-77"}),
            ("/api/overview/series", {"metric": ""}),
            ("/api/overview/series", {}),
            ("/api/overview/compare", {"metric": "x", "cities": "杭州"}),
            ("/api/overview/compare", {"metric": "x", "cities": "杭州,,"}),
            ("/api/overview/compare", {"metric": "x", "cities": "杭" * 41 + ",上海"}),
            ("/api/overview/coverage", {"period_start": "2026-01"}),
        ]
        for url, params in cases:
            r = self.client.get(url, params=params)
            self.assertEqual(r.status_code, 422, msg=f"{url} {params}: {r.status_code}")
            self.assertTrue(r.json())  # 有结构化错误体

    def test_metric_required_by_fastapi(self):
        r = self.client.get("/api/overview/series")
        self.assertEqual(r.status_code, 422)


class IsolationTest(_OverviewBase):
    """11. 不支持/未请求城市不泄露其他城市数据。"""

    def test_no_cross_city_leak(self):
        self.seed(_vol([100.0, 90.0])
                  + [_row("2026-01", city="上海", value=777.7),
                     _row("2026-02", city="上海", value=888.8)])
        # 请求深圳：不返回任何上海/杭州数据
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units", "city": "深圳"})
        self.assertEqual(r.status_code, 200)
        blob = json.dumps(r.json(), ensure_ascii=False)
        self.assertNotIn("777.7", blob)
        self.assertNotIn("888.8", blob)
        self.assertTrue(r.json()["warnings"])
        # 请求上海：只返回上海
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units", "city": "上海"})
        for s in r.json()["series"]:
            self.assertEqual(s["city"], "上海")
        # overview 请求深圳：证据与覆盖里不出现上海数值
        r = self.client.get("/api/overview", params={"city": "深圳"})
        blob = json.dumps(r.json(), ensure_ascii=False)
        self.assertNotIn("777.7", blob)
        # compare 请求杭州+深圳：上海根本不在结果里
        r = self.client.get("/api/overview/compare",
                            params={"metric": "secondary_volume_units",
                                    "cities": "杭州,深圳"})
        d = r.json()
        self.assertIn("深圳", d["uncovered_cities"])
        for g in d["comparison_groups"]:
            for ce in g["cities"]:
                self.assertIn(ce["city"], ("杭州", "深圳"))


class SafetyTest(_OverviewBase):
    """12.–15./22.–24. 无 LLM key、零网络、不加载 embedding、不改库、转义与措辞。"""

    def test_no_llm_key_required_and_no_llm_import(self):
        self.seed(_vol([100.0, 90.0]))
        old_key = os.environ.get("LLM_API_KEY")
        if old_key is not None:
            del os.environ["LLM_API_KEY"]
        sys.modules.pop("lib_llm", None)
        try:
            for url, params in (
                    ("/api/overview", {}),
                    ("/api/overview/series", {"metric": "secondary_volume_units"}),
                    ("/api/overview/coverage", {}),
                    ("/api/overview/compare", {"metric": "secondary_volume_units"}),
                    ("/overview", {})):
                r = self.client.get(url, params=params)
                self.assertEqual(r.status_code, 200, msg=url)
            self.assertNotIn("lib_llm", sys.modules)  # 全程未导入 LLM 层
        finally:
            if old_key is not None:
                os.environ["LLM_API_KEY"] = old_key

    def test_no_network_during_api_calls(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        # 只拦“向外建连/解析”，不动 socket.socket 本体——TestClient 内部
        # anyio portal 需要 socketpair（进程内，不是外部网络）。
        real = (socket.create_connection, socket.getaddrinfo, socket.socket.connect)

        def _boom(*a, **kw):
            raise AssertionError("总览 API 不得发起网络请求")

        socket.create_connection, socket.getaddrinfo, socket.socket.connect = _boom, _boom, _boom
        try:
            for url, params in (
                    ("/api/overview", {}),
                    ("/api/overview/series", {"metric": "mom_change_pct"}),
                    ("/api/overview/series", {"metric": "secondary_volume_units"}),
                    ("/api/overview/coverage", {}),
                    ("/api/overview/compare", {"metric": "secondary_volume_units"})):
                r = self.client.get(url, params=params)
                self.assertEqual(r.status_code, 200, msg=url)
        finally:
            socket.create_connection, socket.getaddrinfo, socket.socket.connect = real

    def test_no_embedding_load(self):
        self.seed(_vol([100.0]))
        lib_search_mod = sys.modules["lib_search"]  # 导入 app 时已随加载，但模型是懒加载
        original = lib_search_mod._get_model

        def _boom(*a, **kw):
            raise AssertionError("总览 API 不得加载 embedding 模型")

        lib_search_mod._get_model = _boom
        try:
            for url, params in (
                    ("/api/overview", {}),
                    ("/api/overview/series", {"metric": "secondary_volume_units"}),
                    ("/api/overview/coverage", {}),
                    ("/api/overview/compare", {"metric": "secondary_volume_units"})):
                self.assertEqual(self.client.get(url, params=params).status_code, 200, msg=url)
        finally:
            lib_search_mod._get_model = original

    def test_api_never_modifies_database(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        before = self.db_sha256()
        for url, params in (
                ("/api/overview", {}),
                ("/api/overview", {"as_of": "2026-02"}),
                ("/api/overview/series", {"metric": "secondary_volume_units"}),
                ("/api/overview/series", {"metric": "mom_change_pct", "as_of": "2026-02"}),
                ("/api/overview/coverage", {}),
                ("/api/overview/compare", {"metric": "secondary_volume_units"}),
                ("/api/overview", {"city": ""}),           # 非法参数也不落盘
                ("/api/overview/series", {"metric": "nope"})):
            self.client.get(url, params=params)
        self.assertEqual(self.db_sha256(), before)
        self.assertFalse((self.db.parent / "overview.db-wal").exists())
        self.assertFalse((self.db.parent / "overview.db-shm").exists())

    def test_metrics_routes_close_connections(self):
        """旧指标页/API 的 SQLite 连接必须在响应前关闭。"""
        self.seed([_row("2026-01", value=100.0)])
        saved_db = web_app.DB
        web_app.DB = self.db
        self.addCleanup(lambda: setattr(web_app, "DB", saved_db))
        self.assertEqual(self.client.get("/metrics").status_code, 200)
        self.assertEqual(self.client.get("/api/metrics",
                                         params={"metric": "secondary_volume_units"}).status_code, 200)
        renamed = self.db.with_name("metrics-routes-closed.db")
        self.db.rename(renamed)
        self.db = renamed
        self.assertTrue(renamed.exists())

    def test_db_text_is_escaped_into_html(self):
        """23. XSS 文本不得以未转义形式进入页面；渲染层统一 esc()。"""
        self.seed([_row("2026-01", value=1.0, institution="<script>alert(1)</script>",
                        source_quote="<img src=x onerror=alert(1)>",
                        source_file="<b>bad</b>.csv")])
        r = self.client.get("/overview")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", r.text)
        self.assertNotIn("<img src=x onerror", r.text)
        # 服务端渲染开启自动转义；客户端所有 DB 字段经 esc() 才进 innerHTML
        self.assertTrue(web_app.templates.env.autoescape)
        src = (APP / "web" / "templates" / "overview.html").read_text(encoding="utf-8")
        self.assertIn("const esc = (s)", src)
        for m in re.finditer(r"\$\{([^}]*)\}", src):
            expr = m.group(1)
            for field in ("source_quote", "institution", "source_file", "value_text"):
                if field in expr:
                    self.assertIn("esc(", expr, msg=f"未转义表达式: ${{{expr}}}")
            # 单数 .warning 字段必须转义；.warnings（复数集合）由 warningsBox 内部转义
            if re.search(r"\.warning(?![\w])", expr):
                self.assertIn("esc(", expr, msg=f"未转义表达式: ${{{expr}}}")

    def test_no_advice_words_on_page_and_api(self):
        """22. 页面与 API 响应不出现购房结论类措辞。"""
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0])
                  + _peer([300.0, 310.0, 320.0], "上海", inst="中指", sf="zz.csv"))
        r = self.client.get("/overview")
        for banned in BANNED_WORDS:
            self.assertNotIn(banned, r.text)
        blobs = []
        for url, params in (
                ("/api/overview", {}),
                ("/api/overview/series", {"metric": "secondary_volume_units",
                                          "include_forecasts": "true"}),
                ("/api/overview/coverage", {}),
                ("/api/overview/compare", {"metric": "secondary_volume_units"})):
            resp = self.client.get(url, params=params)
            self.assertEqual(resp.status_code, 200, msg=url)
            blobs.append(json.dumps(resp.json(), ensure_ascii=False))
        for banned in BANNED_WORDS:
            for blob in blobs:
                self.assertNotIn(banned, blob)

    def test_responses_are_json_serializable(self):
        """24. 四个 API 响应全部可 JSON 序列化。"""
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0])
                  + _peer([300.0, 310.0, 320.0], "上海"))
        for url, params in (
                ("/api/overview", {"as_of": "2026-02"}),
                ("/api/overview/series", {"metric": "secondary_volume_units", "as_of": "2026-02"}),
                ("/api/overview/coverage", {"as_of": "2026-02"}),
                ("/api/overview/compare", {"metric": "secondary_volume_units"})):
            r = self.client.get(url, params=params)
            self.assertEqual(r.status_code, 200, msg=url)
            json.loads(r.text)  # 响应体本身是合法 JSON

    def test_chart_config_never_connects_missing_points(self):
        """19. 图表配置不连接缺失点；与任务 18（不填 0）共同生效。"""
        src = (APP / "web" / "templates" / "overview.html").read_text(encoding="utf-8")
        self.assertIn("connectNulls: false", src)
        self.assertNotIn("connectNulls: true", src)
        self.assertIn("function expandAxis", src)  # 缺失期在 x 轴占位（只补类别、不补数据）

    def test_conflict_and_limited_rendering_present(self):
        """20./21. 页面模板包含冲突展示与 limited 并列区。"""
        src = (APP / "web" / "templates" / "overview.html").read_text(encoding="utf-8")
        self.assertIn("同期冲突", src)
        self.assertIn("limited 并列区", src)
        self.assertIn("不计算差值", src)
        self.assertIn("connectNulls", src)

    def test_api_works_from_outside_project_cwd(self):
        """26. 从项目外 cwd 调用 API（导入与路径全部按 __file__ 推导）。"""
        script = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "app_root = Path(os.environ['OVERVIEW_APP'])\n"
            "sys.path.insert(0, str(app_root / 'web'))\n"
            "sys.path.insert(0, str(app_root / 'scripts'))\n"
            "import app as wa\n"
            "wa.OVERVIEW_DB = Path(os.environ['OVERVIEW_DB'])\n"
            "from fastapi.testclient import TestClient\n"
            "c = TestClient(wa.app)\n"
            "r = c.get('/api/overview')\n"
            "assert r.status_code == 200, r.status_code\n"
            "assert r.json()['city'] == '杭州'\n"
            "print('outside-cwd OK:', r.json()['phase']['code'])\n"
        )
        with tempfile.TemporaryDirectory() as outside:
            env = {**os.environ, "OVERVIEW_APP": str(APP), "OVERVIEW_DB": str(self.db)}
            p = subprocess.run([sys.executable, "-c", script], cwd=outside,
                               capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("outside-cwd OK", p.stdout)


class _FormalGuard(unittest.TestCase):
    FORMAL_DB = APP / "data" / "rag.db"

    @classmethod
    def setUpClass(cls):
        if not cls.FORMAL_DB.exists():
            raise unittest.SkipTest("正式库不存在，跳过只读验证")
        st = cls.FORMAL_DB.stat()
        cls.before = (st.st_size, st.st_mtime,
                      hashlib.sha256(cls.FORMAL_DB.read_bytes()).hexdigest())
        h = hashlib.sha256()
        for f in sorted((APP / "metrics").iterdir()):
            if f.suffix in (".jsonl", ".csv"):
                h.update(f.name.encode())
                h.update(hashlib.sha256(f.read_bytes()).digest())
        cls.metrics_hash_before = h.hexdigest()
        cls._saved_db = web_app.OVERVIEW_DB
        web_app.OVERVIEW_DB = cls.FORMAL_DB
        from fastapi.testclient import TestClient

        cls.client = TestClient(web_app.app)

    @classmethod
    def tearDownClass(cls):
        web_app.OVERVIEW_DB = cls._saved_db
        st = cls.FORMAL_DB.stat()
        after = (st.st_size, st.st_mtime,
                 hashlib.sha256(cls.FORMAL_DB.read_bytes()).hexdigest())
        h = hashlib.sha256()
        for f in sorted((APP / "metrics").iterdir()):
            if f.suffix in (".jsonl", ".csv"):
                h.update(f.name.encode())
                h.update(hashlib.sha256(f.read_bytes()).digest())
        assert after == cls.before, f"正式库指纹变化: {cls.before} -> {after}"
        assert h.hexdigest() == cls.metrics_hash_before, "metrics/ 聚合 hash 变化"
        assert not (APP / "data" / "rag.db-wal").exists()
        assert not (APP / "data" / "rag.db-shm").exists()


@unittest.skipUnless(_FormalGuard.FORMAL_DB.exists(), "正式库不存在，跳过只读验证")
class FormalReadonlyTest(_FormalGuard):
    """16./17. 正式库只读验证：结构正确、杭州默认、多来源不合并、指纹不变。"""

    def test_formal_overview_structure_and_default_city(self):
        r = self.client.get("/api/overview")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["city"], "杭州")  # 默认城市
        self.assertIn(d["phase"]["code"],
                      ("data_insufficient", "continued_decline", "decline_narrowing",
                       "bottom_fluctuation", "initial_stabilization", "recovery_broadening"))
        self.assertIn(d["phase"]["confidence"], ("low", "medium", "high"))
        self.assertTrue(d["risk_signals"])
        self.assertTrue(d["coverage"]["summary"])
        for w in d["watch_next"]:
            self.assertIn("metric", w)

    def test_formal_series_multi_source_no_merge(self):
        r = self.client.get("/api/overview/series",
                            params={"metric": "secondary_volume_units"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertGreaterEqual(d["coverage"]["series_count"], 2)  # 多来源
        keys = [s["source_key"] for s in d["series"]]
        self.assertEqual(len(keys), len(set(keys)))
        for s in d["series"]:
            self.assertTrue(all(p["value"] is not None for p in s["points"]))

    def test_formal_coverage_and_compare(self):
        cov = self.client.get("/api/overview/coverage").json()
        self.assertEqual(cov["filters"]["city"], "杭州")
        self.assertEqual(cov["summary"]["overall_status"], "ok")
        cmp = self.client.get("/api/overview/compare",
                              params={"metric": "secondary_volume_units"}).json()
        self.assertGreater(cmp["summary"]["group_count"], 0)
        limited = [g for g in cmp["comparison_groups"] if g["comparability"] == "limited"]
        for g in limited:
            self.assertTrue(any("不计算差值" in w for w in g["warnings"]))
            self.assertNotEqual(g["comparability"], "comparable")


if __name__ == "__main__":
    unittest.main()
