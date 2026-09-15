"""Step 3D：market_phase 规则型市场阶段 + market_phase.py CLI 测试。

全部数据场景使用临时 SQLite 库（plain sqlite3 建库，不经 lib_db.connect()），
正式库仅做只读评估与指纹验证。不调用 run_eval.py / ingest 等脚本，
不发起任何网络请求（有 socket 拦截测试与 import 扫描测试）。
"""
import hashlib
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import market_phase as mp  # noqa: E402

CLI = APP / "scripts" / "market_phase.py"

_SCHEMA = """
CREATE TABLE metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,
  metric_name TEXT, value REAL, value_text TEXT, unit TEXT,
  comparison_type TEXT, comparison_value REAL, is_forecast INTEGER DEFAULT 0,
  source_page TEXT, source_quote TEXT, source_url TEXT, source_file TEXT
);
"""


def _row(period, city="杭州", value=100.0, metric_name="secondary_volume_units", **kw):
    row = {
        "doc_id": None, "institution": "贝壳", "period": period, "city": city,
        "segment": "secondary_home", "metric_name": metric_name, "value": value,
        "value_text": None, "unit": "套", "comparison_type": None,
        "comparison_value": None, "is_forecast": 0, "source_page": "p1",
        "source_quote": f"引文-{city}-{period}-{value}", "source_url": None,
        "source_file": "beike.csv",
    }
    row.update(kw)
    return row


def _mom(vals, seg="secondary_home", inst="贝壳", sf="beike.csv", start=1):
    return [_row(f"2026-{m:02d}", value=v, metric_name="mom_change_pct", unit="pct",
                 segment=seg, institution=inst, source_file=sf)
            for m, v in enumerate(vals, start)]


def _vol(vals, inst="贝壳", sf="beike.csv", city="杭州", start=1):
    return [_row(f"2026-{m:02d}", city=city, value=v, institution=inst, source_file=sf)
            for m, v in enumerate(vals, start)]


def _inv(vals, inst="克而瑞", sf="cric.csv"):
    return [_row(f"2026-{m:02d}", value=v, metric_name="inventory_months", unit="months",
                 segment="new_home", institution=inst, source_file=sf)
            for m, v in enumerate(vals, 2)]


def _listing(vals, inst="贝壳", sf="beike.csv"):
    return [_row(f"2026-{m:02d}", value=v, metric_name="listing_units", unit="units",
                 segment="new_home", institution=inst, source_file=sf)
            for m, v in enumerate(vals, 2)]


def _rent_flat(vals=(100.0, 100.0, 100.0)):
    return [_row(f"2026-{m:02d}", value=v, metric_name="rent_level",
                 unit="CNY_per_sqm_month", segment="rental", source_file="beike2.csv")
            for m, v in enumerate(vals, 1)]


def _strict_cities(base_hz=200.0, peers=(("上海", 300.0), ("北京", 400.0)),
                   inst="克而瑞", sf="cric.csv"):
    """杭州+核心城市严格同口径（同机构/同来源/同口径/同粒度）月度成交序列。"""
    rows = _vol([base_hz + i * 10 for i in range(3)], inst=inst, sf=sf)
    for city, base in peers:
        rows += _vol([base + i * 10 for i in range(3)], inst=inst, sf=sf, city=city)
    return rows


class _PhaseBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "phase.db"
        conn = sqlite3.connect(str(self.db))
        conn.executescript(_SCHEMA)
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def seed(self, rows):
        cols = list(rows[0])
        conn = sqlite3.connect(str(self.db))
        conn.executemany(
            f"INSERT INTO metrics ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
            rows,
        )
        conn.commit()
        conn.close()

    def assess(self, **kw):
        return mp.assess_market_phase(db_path=self.db, **kw)

    @staticmethod
    def sig(rep, code):
        return next(s for s in rep["signals"] if s["code"] == code)

    @staticmethod
    def gate(rep, phase):
        return next(t for t in rep["phase"]["rule_evaluation"] if t["phase"] == phase)

    @staticmethod
    def risks(rep):
        return [r["code"] for r in rep["risk_signals"]]


class OutputContractTest(_PhaseBase):
    """返回结构、信号字段、证据可追溯性、免责声明。"""

    def test_top_level_structure(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        rep = self.assess()
        for key in ("city", "as_of", "filters", "phase", "signals",
                    "supporting_evidence", "counter_evidence", "coverage",
                    "risk_signals", "watch_next", "warnings", "disclaimer"):
            self.assertIn(key, rep)
        ph = rep["phase"]
        self.assertEqual(ph["code"], "decline_narrowing")
        self.assertEqual(ph["label"], "跌幅收窄")
        self.assertEqual(ph["basis"], "rule_based")
        self.assertIn(mp.RULE_VERSION, ph["rule_version"])
        self.assertEqual(ph["gate_order"], list(mp.PHASE_GATE_ORDER))
        self.assertEqual([t["phase"] for t in ph["rule_evaluation"]],
                         list(mp.PHASE_GATE_ORDER))
        self.assertIn(mp.DISCLAIMER, rep["disclaimer"])
        self.assertIn("购房", rep["disclaimer"])

    def test_signal_contract_fields(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        rep = self.assess()
        self.assertTrue(rep["signals"])
        for s in rep["signals"]:
            for key in ("code", "direction", "strength", "status", "metric_name",
                        "segment", "periods", "evidence", "warnings"):
                self.assertIn(key, s, msg=s["code"])
            self.assertIn(s["status"], ("available", "unavailable", "conflicted", "insufficient"))
        # 证据可追溯：价格信号证据点带来源与原文
        ps = self.sig(rep, "price_secondary_home")
        self.assertTrue(ps["evidence"])
        for e in ps["evidence"]:
            for key in ("period", "value", "institution", "source_file", "source_quote"):
                self.assertIn(key, e)
        self.assertEqual(ps["evidence"][0]["institution"], "贝壳")
        self.assertTrue(ps["evidence"][0]["source_quote"].startswith("引文-"))

    def test_no_advice_and_no_scores(self):
        """不输出买房结论；不输出数值化评分。"""
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        blob = json.dumps(self.assess(), ensure_ascii=False)
        for banned in ("应该买", "不该买", "建议买", "建议卖", "一定涨", "一定跌", "风险分", "score"):
            self.assertNotIn(banned, blob)
        rep = self.assess()
        self.assertIn(rep["phase"]["confidence"], ("low", "medium", "high"))
        for r in rep["risk_signals"]:
            self.assertNotIsInstance(r.get("score"), (int, float))


class PhaseInsufficientTest(_PhaseBase):
    """数据不足 / 单点 / 两期 / 缺失指标。"""

    def test_single_points_are_data_insufficient(self):
        self.seed([_row("2026-03", value=-1.0, metric_name="mom_change_pct", unit="pct"),
                   _row("2026-03", value=100.0)])
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "data_insufficient")
        ps = self.sig(rep, "price_secondary_home")
        self.assertEqual(ps["status"], "insufficient")
        self.assertIsNone(ps["direction"])  # 单点不生成任何方向/趋势
        vs = self.sig(rep, "volume_secondary")
        self.assertEqual(vs["status"], "insufficient")
        self.assertIsNone(vs["direction"])
        self.assertIn("low_sample_size", self.risks(rep))
        self.assertTrue(any("不足 2 个实际时期" in w for w in rep["warnings"]))

    def test_missing_core_metrics_data_gap(self):
        self.seed([_row("2026-03", value=1.0, metric_name="land_transaction_value",
                        unit="CNY_100m", segment="land")])
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "data_insufficient")
        gaps = [r for r in rep["risk_signals"] if r["code"] == "data_gap"]
        self.assertGreaterEqual(len(gaps), 4)  # 价格/成交/库存/挂牌全缺
        self.assertTrue(rep["coverage"]["missing_core_metrics"])

    def test_two_periods_cannot_claim_consecutive_narrowing(self):
        """两期收窄不构成 decline_narrowing；两期核心数据不足以判断持续性。"""
        self.seed(_mom([-3.0, -2.0]) + _vol([100.0, 90.0]))
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "data_insufficient")
        narrowing = self.gate(rep, "decline_narrowing")
        self.assertFalse(narrowing["passed"])
        self.assertFalse(narrowing["conditions"]["narrowing_3_consecutive_periods"])
        continued = self.gate(rep, "continued_decline")
        self.assertFalse(continued["conditions"]["core_series_3_actual_periods"])
        ps = self.sig(rep, "price_secondary_home")
        self.assertEqual(ps["primary_analysis"]["narrowing_run"], 2)  # 事实如实展示：仅 2 期收窄
        self.assertEqual(ps["strength"], "weak")


class PhaseDeclineTest(_PhaseBase):
    """持续下行 / 跌幅收窄 / 成交量连续升降。"""

    def test_continued_decline(self):
        self.seed(_mom([-1.0, -2.0, -3.0]) + _vol([100.0, 90.0, 80.0]) + _inv([12.0, 13.0, 14.0]))
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "continued_decline")
        g = self.gate(rep, "continued_decline")
        self.assertTrue(all(g["conditions"].values()))
        self.assertFalse(self.gate(rep, "decline_narrowing")["conditions"]
                         ["narrowing_3_consecutive_periods"])
        ps = self.sig(rep, "price_secondary_home")
        self.assertEqual(ps["direction"], "deteriorating")
        self.assertEqual(ps["primary_analysis"]["latest_negative"], True)

    def test_decline_narrowing_three_periods(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]) + _inv([12.0, 13.0]))
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "decline_narrowing")
        ps = self.sig(rep, "price_secondary_home")
        self.assertEqual(ps["direction"], "improving")  # 收窄即改善证据，但阶段仍保守
        self.assertEqual(ps["primary_analysis"]["narrowing_run"], 3)
        self.assertEqual(ps["primary_analysis"]["latest_negative"], True)  # 仍为负
        g = self.gate(rep, "decline_narrowing")
        self.assertTrue(g["passed"])
        self.assertTrue(g["conditions"]["price_still_negative"])
        self.assertTrue(g["conditions"]["narrowing_3_consecutive_periods"])
        # 反向证据可追溯：成交下行进入 counter_evidence
        self.assertIn("volume_secondary",
                      [e["signal_code"] for e in rep["counter_evidence"]])
        self.assertIn("price_secondary_home",
                      [e["signal_code"] for e in rep["supporting_evidence"]])

    def test_volume_consecutive_down_signal(self):
        self.seed(_mom([-1.0, -2.0, -3.0]) + _vol([100.0, 90.0, 80.0]))
        vs = self.sig(self.assess(), "volume_secondary")
        self.assertEqual(vs["direction"], "deteriorating")
        self.assertEqual(vs["primary_analysis"]["down_run"], 3)
        self.assertEqual(vs["primary_analysis"]["up_run"], 1)

    def test_volume_consecutive_up_signal(self):
        self.seed(_mom([-1.0, -2.0, -3.0]) + _vol([100.0, 110.0, 120.0]))
        rep = self.assess()
        vs = self.sig(rep, "volume_secondary")
        self.assertEqual(vs["direction"], "improving")
        self.assertEqual(vs["primary_analysis"]["up_run"], 3)
        self.assertIn("volume_secondary", [e["signal_code"] for e in rep["supporting_evidence"]])


class PhaseBottomTest(_PhaseBase):
    """底部震荡 / 价格成交背离。"""

    def test_bottom_local_turn_positive(self):
        self.seed(_mom([-3.0, -2.0, 0.2]) + _vol([100.0, 90.0, 80.0]))
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "bottom_fluctuation")
        g = self.gate(rep, "bottom_fluctuation")
        self.assertTrue(g["passed"])
        self.assertTrue(g["conditions"]["price_improvement_sign"])
        self.assertTrue(g["conditions"]["signals_pulling_against"])
        ps = self.sig(rep, "price_secondary_home")
        self.assertTrue(ps["primary_analysis"]["turned_positive"])

    def test_bottom_two_period_narrowing_needs_third_series(self):
        """两期收窄 + 三期成交下行：有 ≥3 期核心序列时进入底部震荡而非跌幅收窄。"""
        self.seed(_mom([-3.0, -2.0]) + _vol([100.0, 90.0, 80.0]))
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "bottom_fluctuation")
        self.assertFalse(self.gate(rep, "decline_narrowing")["passed"])

    def test_price_volume_divergence_risk(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        rep = self.assess()
        self.assertIn("price_volume_divergence", self.risks(rep))
        risk = next(r for r in rep["risk_signals"] if r["code"] == "price_volume_divergence")
        self.assertEqual(risk["trigger"]["price_direction"], "improving")
        self.assertEqual(risk["trigger"]["volume_direction"], "deteriorating")


class PhaseStabilizationTest(_PhaseBase):
    """初步企稳 / 新房二手房分化。"""

    def test_initial_stabilization(self):
        self.seed(_mom([-2.0, -1.5, -1.0])
                  + _vol([100.0, 105.0, 105.0], inst="克而瑞", sf="cric.csv")
                  + _inv([12.0, 12.0], sf="cric2.csv"))
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "initial_stabilization")
        g = self.gate(rep, "initial_stabilization")
        self.assertTrue(all(g["conditions"].values()))
        self.assertTrue(g["conditions"]["not_forecast_driven"])  # 结构性：不依赖预测
        self.assertFalse(self.gate(rep, "recovery_broadening")["passed"])

    def test_new_secondary_divergence_risk(self):
        self.seed(_mom([-3.0, -2.0, -1.0])
                  + _mom([-1.0, -2.0, -3.0], seg="new_home", sf="beike_new.csv")
                  + _vol([100.0, 90.0, 80.0]))
        rep = self.assess()
        self.assertIn("new_secondary_divergence", self.risks(rep))
        # 二手改善但新房恶化 → 价格家族混合，阶段按保守处理
        self.assertEqual(self.sig(rep, "price_secondary_home")["direction"], "improving")
        self.assertEqual(self.sig(rep, "price_new_home")["direction"], "deteriorating")
        self.assertIn(rep["phase"]["code"],
                      ("continued_decline", "bottom_fluctuation", "data_insufficient"))
        self.assertNotEqual(rep["phase"]["code"], "initial_stabilization")


class PhaseRecoveryTest(_PhaseBase):
    """修复扩散 / 跨城 limited。"""

    def _recovery_rows(self, peer_inst="克而瑞", peer_sf="cric.csv"):
        rows = (_mom([0.3, 0.6, 0.9])                       # 价格转正且改善
                + _inv([12.0, 11.0, 10.0], sf="cric2.csv")  # 库存去化改善
                + _rent_flat())                             # 租金平稳（背景）
        rows += _vol([200.0 + i * 10 for i in range(3)], inst="克而瑞", sf="cric.csv")
        for city, base in (("上海", 300.0), ("北京", 400.0)):
            rows += _vol([base + i * 10 for i in range(3)],
                         inst=peer_inst, sf=peer_sf, city=city)
        return rows

    def test_recovery_broadening_with_strict_city_support(self):
        self.seed(self._recovery_rows())
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "recovery_broadening")
        self.assertTrue(all(self.gate(rep, "recovery_broadening")["conditions"].values()))
        cc = self.sig(rep, "core_city_linkage")
        self.assertEqual(cc["status"], "available")
        self.assertEqual(cc["direction"], "improving")
        obs_cities = {pc["city"] for o in cc["observations"] for pc in o["per_city"]}
        self.assertTrue({"杭州", "上海", "北京"} <= obs_cities)

    def test_limited_city_comparison_blocks_recovery(self):
        """对手城市来源不同 → 仅 limited 并列：不进入修复扩散强结论。"""
        self.seed(self._recovery_rows(peer_inst="中指", peer_sf="zf.csv"))
        rep = self.assess()
        self.assertNotEqual(rep["phase"]["code"], "recovery_broadening")
        self.assertEqual(rep["phase"]["code"], "initial_stabilization")
        cc = self.sig(rep, "core_city_linkage")
        self.assertNotEqual(cc["status"], "available")
        self.assertIn("city_comparison_limited", self.risks(rep))


class SignalEdgeTest(_PhaseBase):
    """库存/挂牌压力、来源冲突、粒度隔离、预测隔离、as_of/窗口。"""

    def test_inventory_pressure(self):
        self.seed(_mom([-1.0, -2.0, -3.0]) + _vol([100.0, 90.0, 80.0]) + _inv([12.0, 13.0, 14.0]))
        rep = self.assess()
        self.assertIn("inventory_pressure", self.risks(rep))
        inv = self.sig(rep, "inventory_months")
        self.assertEqual(inv["direction"], "deteriorating")  # 去化周期上升=压力
        risk = next(r for r in rep["risk_signals"] if r["code"] == "inventory_pressure")
        self.assertEqual(risk["trigger"]["periods"], ["2026-02", "2026-03", "2026-04"])

    def test_listing_pressure(self):
        self.seed(_mom([-1.0, -2.0, -3.0]) + _vol([100.0, 90.0, 80.0]) + _listing([5.0, 6.0, 7.0]))
        rep = self.assess()
        self.assertIn("listing_pressure", self.risks(rep))
        self.assertEqual(self.sig(rep, "listing_units")["direction"], "deteriorating")

    def test_source_conflict_preserved(self):
        rows = _mom([-1.0, -2.0, -3.0]) + _vol([100.0, 90.0, 80.0])
        rows.append(_row("2026-03", value=-2.5, metric_name="mom_change_pct",
                         unit="pct"))  # 与同期 -3.0 同组同源多值
        self.seed(rows)
        rep = self.assess()
        self.assertIn("source_conflict", self.risks(rep))
        ps = self.sig(rep, "price_secondary_home")
        self.assertEqual(ps["status"], "conflicted")
        risk = next(r for r in rep["risk_signals"] if r["code"] == "source_conflict")
        self.assertEqual(risk["trigger"]["periods"], ["2026-03"])
        self.assertEqual(sorted(risk["trigger"]["values"][0]), [-3.0, -2.5])  # 两个值都保留
        # 冲突期不参与方向计算（2026-03 移出 points）
        obs = next(o for o in ps["observations"] if o["metric_name"] == "mom_change_pct")
        self.assertIn("2026-03", obs["conflict_periods"])

    def test_granularity_isolation(self):
        rows = _mom([-1.0, -2.0, -3.0]) + _vol([100.0, 110.0, 120.0])
        rows.append(_row("2026Q1", value=90.0))  # 季度单点，同指标另一粒度
        self.seed(rows)
        rep = self.assess()
        vs = self.sig(rep, "volume_secondary")
        self.assertEqual(vs["direction"], "improving")  # 只由月度 3 点决定
        self.assertEqual(vs["periods"], ["2026-01", "2026-02", "2026-03"])  # 不并入季度点
        other = [o for o in vs["other_granularity_observations"]
                 if o["period_granularity"] == "quarter"]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["n_periods"], 1)
        self.assertIsNone(other[0]["direction"])
        self.assertIn("granularity_mismatch", self.risks(rep))

    def test_forecast_never_drives_phase(self):
        rows = ([_row("2026-03", value=-1.0, metric_name="mom_change_pct", unit="pct"),
                 _row("2026-03", value=100.0)]
                + [_row("2026", value=8.0, metric_name="price_change_forecast_pct",
                        unit="pct", is_forecast=1, source_file="ms.csv"),
                   _row("2027", value=10.0, metric_name="price_change_forecast_pct",
                        unit="pct", is_forecast=1, source_file="ms.csv")])
        self.seed(rows)
        rep_default = self.assess()  # 默认不含预测
        self.assertEqual(rep_default["phase"]["code"], "data_insufficient")
        self.assertFalse([s for s in rep_default["signals"] if s["family"] == "forecast"])

        rep = self.assess(include_forecasts=True)
        self.assertEqual(rep["phase"]["code"], "data_insufficient")  # 预测不主导
        fc = [s for s in rep["signals"] if s["family"] == "forecast"]
        self.assertEqual(len(fc), 1)
        self.assertEqual(fc[0]["status"], "available")
        self.assertEqual(fc[0]["direction"], "improving")
        self.assertTrue(any(mp.FORECAST_WARNING in w for w in fc[0]["warnings"]))
        self.assertIn("forecast_dominant", self.risks(rep))
        # 预测证据不混入实际支持/反向证据
        self.assertNotIn("forecast_outlook",
                         [e["signal_code"] for e in rep["supporting_evidence"]])

    def test_as_of_snapshot_changes_phase(self):
        rows = _mom([-1.0, -2.0, -3.0, 0.5]) + _vol([100.0, 95.0, 90.0, 95.0])
        self.seed(rows)
        full = self.assess()
        self.assertEqual(full["phase"]["code"], "initial_stabilization")
        self.assertEqual(full["as_of"], "2026-04")  # 无 as_of 时取数据最新期
        snap = self.assess(as_of="2026-03")         # 只看前 3 期
        self.assertEqual(snap["phase"]["code"], "continued_decline")
        self.assertEqual(snap["as_of"], "2026-03")
        ps = self.sig(snap, "price_secondary_home")
        self.assertEqual(ps["periods"], ["2026-01", "2026-02", "2026-03"])  # 2026-04 被排除

    def test_as_of_bounds_coverage_forecast_and_core_linkage(self):
        """历史快照的截止期必须贯穿 coverage、预测和核心城市共同期。"""
        rows = (_mom([-1.0, -2.0, -3.0, 0.5])
                + _vol([100.0, 95.0, 90.0, 95.0])
                + [_row("2026", value=8.0, metric_name="price_change_forecast_pct",
                        unit="pct", is_forecast=1, source_file="forecast.csv"),
                   _row("2027", value=10.0, metric_name="price_change_forecast_pct",
                        unit="pct", is_forecast=1, source_file="forecast.csv")])
        self.seed(rows)
        snap = self.assess(as_of="2026-03", include_forecasts=True,
                           period_start="2026-01", period_end="2026-04")
        self.assertEqual(snap["filters"]["effective_period_end"], "2026-03")
        self.assertTrue(all(mp.mq._period_sort_key(p) <= mp.mq._period_sort_key("2026-03")
                            for p in snap["coverage"]["requested_periods"]))
        for g in snap["coverage"]["core_metrics"]:
            if g["last_period"] is not None:
                self.assertLessEqual(mp.mq._period_sort_key(g["last_period"]),
                                     mp.mq._period_sort_key("2026-03"))
        fc = [s for s in snap["signals"] if s["family"] == "forecast"][0]
        self.assertTrue(all(mp.mq._period_sort_key(p) <= mp.mq._period_sort_key("2026-03")
                            for p in fc["periods"]))
        cc = self.sig(snap, "core_city_linkage")
        self.assertTrue(all(mp.mq._period_sort_key(p) <= mp.mq._period_sort_key("2026-03")
                            for p in cc["periods"]))

    def test_as_of_before_window_returns_empty_effective_coverage(self):
        """截止期早于请求窗口时，不应使用窗口之后的数据。"""
        self.seed(_mom([-1.0, -2.0, -3.0]) + _vol([100.0, 90.0, 80.0]))
        rep = self.assess(as_of="2026-02", period_start="2026-03", period_end="2026-04")
        self.assertEqual(rep["filters"]["effective_period_end"], "2026-02")
        self.assertEqual(rep["coverage"]["requested_periods"], [])
        self.assertTrue(all(not s["periods"] for s in rep["signals"]
                            if s["family"] != "core_city"))

    def test_period_window_filter(self):
        self.seed(_mom([-1.0, -2.0, -3.0, -2.0]) + _vol([100.0, 90.0, 80.0, 85.0]))
        rep = self.assess(period_start="2026-03", period_end="2026-04")
        ps = self.sig(rep, "price_secondary_home")
        self.assertEqual(ps["periods"], ["2026-03", "2026-04"])
        self.assertTrue(rep["coverage"]["has_explicit_window"])

    def test_invalid_arguments(self):
        self.seed(_mom([-1.0, -2.0]))
        with self.assertRaises(ValueError):
            self.assess(period_start="2026-03")  # 缺 period_end
        with self.assertRaises(ValueError):
            self.assess(as_of="2026-13")         # 非法时期
        with self.assertRaises(ValueError):
            self.assess(city="")                 # 空城市


class ConfidenceTest(_PhaseBase):
    """置信度三档（证据充分程度，非判断正确概率）。"""

    def test_confidence_tiers(self):
        # low：核心数据不足
        self.seed([_row("2026-03", value=-1.0, metric_name="mom_change_pct", unit="pct"),
                   _row("2026-03", value=100.0)])
        self.assertEqual(self.assess()["phase"]["confidence"], "low")

        # medium：双机构、价格+成交各 3 期，但缺核心城市可比
        self.tearDown()
        self.setUp()
        self.seed(_mom([-2.0, -1.5, -1.0])
                  + _vol([100.0, 105.0, 105.0], inst="克而瑞", sf="cric.csv")
                  + _inv([12.0, 12.0], sf="cric2.csv"))
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "initial_stabilization")
        self.assertEqual(rep["phase"]["confidence"], "medium")

        # high：双机构 + 库存改善 + 严格跨城可比 + 零冲突
        self.tearDown()
        self.setUp()
        rows = (_mom([0.3, 0.6, 0.9]) + _inv([12.0, 11.0, 10.0], sf="cric2.csv")
                + _rent_flat() + _strict_cities())
        self.seed(rows)
        rep = self.assess()
        self.assertEqual(rep["phase"]["code"], "recovery_broadening")
        self.assertEqual(rep["phase"]["confidence"], "high")


class ApiZeroTest(_PhaseBase):
    """外部 API 调用为 0。"""

    def test_no_network_during_assess(self):
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))
        real_socket, real_conn = socket.socket, socket.create_connection

        def _boom(*a, **kw):
            raise AssertionError("market_phase 不得发起网络请求")

        socket.socket = _boom
        socket.create_connection = _boom
        try:
            rep = self.assess(include_forecasts=True)  # 含预测路径也零网络
            self.assertEqual(rep["phase"]["code"], "decline_narrowing")
        finally:
            socket.socket, socket.create_connection = real_socket, real_conn

    def test_no_network_imports(self):
        import ast
        src = (APP / "scripts" / "market_phase.py").read_text(encoding="utf-8")
        banned_roots = {"requests", "urllib", "http", "socket", "httpx", "aiohttp"}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = {node.module.split(".")[0]}
            else:
                continue
            self.assertFalse(banned_roots & mods, msg=f"禁止的网络 import: {mods}")


class CliTest(_PhaseBase):
    """CLI：项目外 cwd、JSON、参数错误、无写入子命令。"""

    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLI), "--db", str(self.db), *args],
            capture_output=True, text=True, cwd=cwd or str(self._tmp.name),
        )

    def setUp(self):
        super().setUp()
        self.seed(_mom([-3.0, -2.0, -1.0]) + _vol([100.0, 90.0, 80.0]))

    def test_cli_text_output(self):
        p = self.run_cli()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("市场阶段评估", p.stdout)
        self.assertIn("decline_narrowing", p.stdout)
        self.assertIn("免责声明", p.stdout)

    def test_cli_json_serialization_outside_project_cwd(self):
        with tempfile.TemporaryDirectory() as outside:
            p = self.run_cli("--city", "杭州", "--json", cwd=outside)
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)
        self.assertEqual(rep["city"], "杭州")
        self.assertEqual(rep["phase"]["code"], "decline_narrowing")

    def test_cli_bad_arguments(self):
        p = self.run_cli("--period-start", "2026-03")          # 缺 --period-end
        self.assertNotEqual(p.returncode, 0)
        p = self.run_cli("--as-of", "2026-13")                 # 非法时期
        self.assertNotEqual(p.returncode, 0)
        p = self.run_cli("--db", "/nonexistent/x.db")          # 库不存在
        self.assertNotEqual(p.returncode, 0)

    def test_cli_has_no_write_subcommand(self):
        for bogus in (["ingest"], ["write", "x"], ["--write"]):
            p = self.run_cli(*bogus)
            self.assertNotEqual(p.returncode, 0, msg=str(bogus))  # 一律拒绝
        # 评估后库内容不变
        hash_before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.run_cli("--json")
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), hash_before)


class _FormalGuard(unittest.TestCase):
    FORMAL_DB = APP / "data" / "rag.db"

    @classmethod
    def setUpClass(cls):
        st = cls.FORMAL_DB.stat()
        cls.before = (st.st_size, st.st_mtime,
                      hashlib.sha256(cls.FORMAL_DB.read_bytes()).hexdigest())
        h = hashlib.sha256()
        for f in sorted((APP / "metrics").iterdir()):
            if f.suffix in (".jsonl", ".csv"):
                h.update(f.name.encode())
                h.update(hashlib.sha256(f.read_bytes()).digest())
        cls.metrics_hash_before = h.hexdigest()

    @classmethod
    def tearDownClass(cls):
        st = cls.FORMAL_DB.stat()
        after = (st.st_size, st.st_mtime,
                 hashlib.sha256(cls.FORMAL_DB.read_bytes()).hexdigest())
        h = hashlib.sha256()
        for f in sorted((APP / "metrics").iterdir()):
            if f.suffix in (".jsonl", ".csv"):
                h.update(f.name.encode())
                h.update(hashlib.sha256(f.read_bytes()).digest())
        assert after == cls.before, "正式库指纹变化"
        assert h.hexdigest() == cls.metrics_hash_before, "metrics/ 聚合 hash 变化"
        assert not (APP / "data" / "rag.db-wal").exists()
        assert not (APP / "data" / "rag.db-shm").exists()


@unittest.skipUnless(_FormalGuard.FORMAL_DB.exists(), "正式库不存在，跳过只读验证")
class FormalReadonlyTest(_FormalGuard):
    """正式库只读评估 + 指纹不变。"""

    def test_formal_assess_readonly_and_structure(self):
        rep = mp.assess_market_phase(city="杭州", db_path=_FormalGuard.FORMAL_DB)
        self.assertIn(rep["phase"]["code"], mp.PHASE_LABELS)
        self.assertIn(rep["phase"]["confidence"], ("low", "medium", "high"))
        self.assertTrue(rep["signals"])
        for s in rep["signals"]:
            self.assertIn(s["status"], ("available", "unavailable",
                                        "conflicted", "insufficient"))
        self.assertTrue(rep["risk_signals"])  # 真实数据必有数据缺口类风险
        self.assertTrue(all(r["warning"] for r in rep["risk_signals"]))

    def test_formal_cli_readonly(self):
        p = subprocess.run(
            [sys.executable, str(CLI), "--city", "杭州", "--db",
             str(_FormalGuard.FORMAL_DB), "--json"],
            capture_output=True, text=True, cwd="/tmp")
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)
        self.assertIn("phase", rep)


if __name__ == "__main__":
    unittest.main()
