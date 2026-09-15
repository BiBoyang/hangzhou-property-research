"""Step 1 回归：period 解析契约 + query_metrics 筛选 / 默认最近期策略 / 边界。

用临时 SQLite 库隔离测试数据，不依赖 data/rag.db 的实际内容。

Step 3A 追加：query_metric_series 多来源时间序列查询测试（临时库 +
正式库只读指纹验证）。
"""
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_metrics_query as mq  # noqa: E402
from lib_db import connect  # noqa: E402


class ParsePeriodsTest(unittest.TestCase):
    def test_contract(self):
        cases = {
            "2026年3月": ["2026-03"],
            "2026Q1": ["2026Q1"],
            "2026年一季度": ["2026Q1"],
            "2026年第2季度": ["2026Q2"],
            "2026年上半年": ["2026-H1"],
            "2026年下半年": ["2026-H2"],
            "2026年3月到8月": ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"],
            "杭州二手房2026年3月到8月的成交量走势？": [
                "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
            ],
            "2025年11月至2026年3月": ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03"],
            "杭州2026年8月新房成交均价多少？": ["2026-08"],
        }
        for q, want in cases.items():
            self.assertEqual(mq.parse_periods(q), want, msg=q)

    def test_no_false_positive_and_edge(self):
        # “2026年一线城市”的“一”不应误判成季度；无时间 / 非法月份 / 重复提及
        self.assertEqual(mq.parse_periods("高盛和瑞银对2026年一线城市房价的判断"), [])
        self.assertEqual(mq.parse_periods("摩根大通对全国房价触底的判断是什么？"), [])
        self.assertEqual(mq.parse_periods("2026年13月"), [])
        self.assertEqual(mq.parse_periods("2026年3月和2026年3月"), ["2026-03"])

    def test_period_sort_key(self):
        self.assertEqual(mq._period_sort_key("2026-03"), (2026, 3))
        self.assertEqual(mq._period_sort_key("2026Q1"), (2026, 3))
        self.assertEqual(mq._period_sort_key("2026-H1"), (2026, 6))
        self.assertEqual(mq._period_sort_key("2026Q4"), (2026, 12))
        self.assertEqual(mq._period_sort_key("2026-H2"), (2026, 12))
        self.assertEqual(mq._period_sort_key("2026"), (2026, 12))
        self.assertEqual(mq._period_sort_key("乱码"), (0, 0))
        self.assertGreater(mq._period_sort_key("2026-H2"), mq._period_sort_key("2026-08"))
        self.assertGreater(mq._period_sort_key("2027"), mq._period_sort_key("2026Q4"))

    def test_parse_years_bare_vs_attached(self):
        self.assertEqual(mq.parse_years("2027年杭州房价会涨多少？"), ["2027"])
        self.assertEqual(mq.parse_years("高盛和瑞银对2026年一线城市房价的判断"), ["2026"])
        self.assertEqual(mq.parse_years("2026年8月北京楼市新政"), [])   # 年份附着月份
        self.assertEqual(mq.parse_years("2026年一季度中国GDP增速"), [])  # 附着季度
        self.assertEqual(mq.parse_years("2026年上半年土地市场"), [])    # 附着半年
        self.assertEqual(mq.parse_years("2026年Q1点评"), [])            # 附着 ASCII 季度
        self.assertEqual(mq.parse_years("杭州二手房2026年3月到8月的走势"), [])  # 附着范围
        self.assertEqual(mq.parse_years("没有年份的问题"), [])


def _row(period, city, metric_name, value=1.0, value_text=None, unit="月", institution="测试机构"):
    return {
        "institution": institution, "period": period, "city": city, "segment": "new_home",
        "metric_name": metric_name, "value": value, "value_text": value_text, "unit": unit,
        "comparison_type": None, "comparison_value": None, "is_forecast": 0,
        "source_quote": "测试引文",
    }


class QueryMetricsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db = mq.DB
        mq.DB = Path(self._tmp.name) / "test.db"
        conn = connect(mq.DB)
        rows = (
            # 杭州 inventory_months：8 个月度 period，用于验证“最近 6 期”截断
            *[_row(f"2026-{m:02d}", "杭州", "inventory_months", value=8.0 + m) for m in range(1, 9)],
            # DoD 问题涉及的无关指标（不应混入库存问题的结果）
            _row("2026-08", "杭州", "avg_price", value=40924.0, unit="元/平米"),
            _row("2026-08", "杭州", "developer_sales_amount", value=100.0, unit="亿元"),
            # 下半年房价问题（H2 只有 avg_price 一行）
            _row("2026-H2", "杭州", "avg_price", value=41000.0, unit="元/平米"),
            # 二手房成交：3-8 月，供范围查询用
            *[_row(f"2026-{m:02d}", "杭州", "secondary_volume_units", value=9000.0 - m, unit="套")
              for m in range(3, 9)],
            # 无城市问题：全国环比（2026-03）
            _row("2026-03", "全国", "mom_change_pct", value=0.34, unit="%"),
            # 仅城市、无指标关键词的北京问题
            _row("2026-08", "北京", "inventory_months", value=20.0),
            # 文本型指标：底部时点
            _row("2026", "上海", "price_bottom_timing", value=None, value_text="2027Q4"),
            # 裸年份过滤：2026 历史 + 2026/2027/2027Q1 预测并存
            _row("2026-08", "杭州", "avg_price", value=40924.0, unit="元/平米"),
            _row("2026", "杭州", "price_change_forecast_pct", value=-3.0, unit="%"),
            _row("2027", "杭州", "price_change_forecast_pct", value=5.0, unit="%"),
            _row("2027Q1", "杭州", "price_change_forecast_pct", value=4.0, unit="%"),
        )
        conn.executemany(
            "INSERT INTO metrics (institution, period, city, segment, metric_name, value, value_text,"
            " unit, source_quote) VALUES (:institution, :period, :city, :segment, :metric_name,"
            " :value, :value_text, :unit, :source_quote)",
            rows,
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        mq.DB = self._orig_db
        self._tmp.cleanup()

    def test_metric_keyword_filters_unrelated_and_recent_periods(self):
        rows = mq.query_metrics("杭州目前新房库存去化周期多少个月？")
        self.assertTrue(rows)
        self.assertEqual({r["metric_name"] for r in rows}, {"inventory_months"})
        periods = {r["period"] for r in rows}
        self.assertEqual(periods, {f"2026-{m:02d}" for m in range(3, 9)})  # 8 期里取最近 6 期
        self.assertEqual(rows[0]["period"], "2026-08")  # 新 → 旧排序

    def test_explicit_half_year_period(self):
        rows = mq.query_metrics("2026年下半年杭州房价如何？")
        self.assertEqual([r["period"] for r in rows], ["2026-H2"])
        self.assertEqual(rows[0]["metric_name"], "avg_price")

    def test_month_range_query(self):
        rows = mq.query_metrics("杭州二手房2026年3月到8月的成交量走势？")
        self.assertEqual({r["metric_name"] for r in rows}, {"secondary_volume_units"})
        self.assertEqual({r["period"] for r in rows}, {f"2026-{m:02d}" for m in range(3, 9)})

    def test_nonexistent_period_returns_empty(self):
        self.assertEqual(mq.query_metrics("2026年9月杭州二手房成交多少套？"), [])

    def test_no_city_and_no_metric_returns_empty(self):
        self.assertEqual(mq.query_metrics("2026年4月政治局会议对房地产的定调是什么？"), [])
        self.assertEqual(mq.query_metrics("高盛对长鑫存储的目标价是多少？"), [])

    def test_no_city_but_metric_matches(self):
        rows = mq.query_metrics("2026年3月百城价格指数环比涨跌多少？")
        self.assertEqual([(r["period"], r["metric_name"]) for r in rows], [("2026-03", "mom_change_pct")])

    def test_city_only_without_metric_keywords(self):
        rows = mq.query_metrics("2026年8月北京楼市新政的主要内容是什么？")
        self.assertEqual([(r["period"], r["metric_name"]) for r in rows], [("2026-08", "inventory_months")])

    def test_limit_is_respected(self):
        rows = mq.query_metrics("杭州目前新房库存去化周期多少个月？", limit=3)
        self.assertEqual(len(rows), 3)

    def test_bare_year_filters_out_other_years(self):
        rows = mq.query_metrics("2027年杭州房价会涨多少？")
        self.assertTrue(rows)
        self.assertEqual({r["period"] for r in rows}, {"2027", "2027Q1"})  # 前缀匹配，含 2026 数据被排除
        self.assertEqual({r["metric_name"] for r in rows}, {"price_change_forecast_pct"})
        self.assertNotIn("2026-08", {r["period"] for r in rows})  # 不回退“最近 6 期”混入历史行

    def test_unsupported_city_returns_empty(self):
        self.assertEqual(mq.query_metrics("成都在这些研报里的房价数据？"), [])
        self.assertEqual(mq.query_metrics("成都的房价数据"), [])
        self.assertEqual(mq.query_metrics("杭州和成都楼市对比怎么样？"), [])  # 混提城市保守让位

    def test_connection_closed_after_query(self):
        orig_connect = mq.connect
        closed = []

        class SpyConn:
            def __init__(self, real):
                self._real = real

            def execute(self, *a):
                return self._real.execute(*a)

            def close(self):
                closed.append(True)
                self._real.close()

        def spy_connect(path):
            return SpyConn(orig_connect(path))

        mq.connect = spy_connect
        try:
            mq.query_metrics("杭州目前新房库存去化周期多少个月？")
        finally:
            mq.connect = orig_connect
        self.assertEqual(closed, [True])

    def test_format_metrics_numeric_and_text(self):
        rows = mq.query_metrics("上海房价底部时点是什么时候？")
        self.assertEqual(rows[0]["value_text"], "2027Q4")
        text = mq.format_metrics(rows)
        self.assertIn("2027Q4", text)
        self.assertNotIn("None", text)
        numeric = mq.format_metrics([_row("2026-08", "杭州", "inventory_months", value=8.5)])
        self.assertIn("8.5", numeric)
        self.assertEqual(mq.format_metrics([]), "")


# ---------------------------------------------------------------------------
# Step 3A：query_metric_series 多来源时间序列查询
# ---------------------------------------------------------------------------

# 与正式库一致的 metrics schema（含 source_file）；临时库由 plain sqlite3
# 创建，不经过 lib_db.connect()，证明查询路径不依赖带写副作用的连接。
_SERIES_SCHEMA = """
CREATE TABLE metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,
  metric_name TEXT, value REAL, value_text TEXT, unit TEXT,
  comparison_type TEXT, comparison_value REAL, is_forecast INTEGER DEFAULT 0,
  source_page TEXT, source_quote TEXT, source_url TEXT, source_file TEXT
);
"""

# Step 2B 之前的旧 schema：无 source_file 列
_SERIES_SCHEMA_LEGACY = _SERIES_SCHEMA.replace(", source_file TEXT", "")


def _srow(period, city="杭州", metric_name="secondary_volume_units", value=1000.0, **kw):
    row = {
        "doc_id": None, "institution": "贝壳", "period": period, "city": city,
        "segment": "secondary_home", "metric_name": metric_name, "value": value,
        "value_text": None, "unit": "套", "comparison_type": None,
        "comparison_value": None, "is_forecast": 0, "source_page": "p1",
        "source_quote": f"引文-{period}", "source_url": None, "source_file": "beike.csv",
    }
    row.update(kw)
    return row


class _SeriesQueryBase(unittest.TestCase):
    schema = _SERIES_SCHEMA

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "series.db"
        conn = sqlite3.connect(str(self.db))
        conn.executescript(self.schema)
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

    def q(self, **kw):
        return mq.query_metric_series(db_path=self.db, **kw)

    def db_sha256(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def db_schema_sql(self):
        conn = sqlite3.connect(str(self.db))
        try:
            return conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='metrics'").fetchone()[0]
        finally:
            conn.close()


class SeriesMultiSourceTest(_SeriesQueryBase):
    """场景 1：杭州二手房多来源——贝壳与 Morgan Stanley 各自成组，不合并。"""

    def test_multi_source_split_and_missing(self):
        self.seed([
            _srow("2026-03", value=9356.0),
            _srow("2026-04", value=9000.0),
            _srow("2026-05", value=9100.0),
            _srow("2026-03", value=8728.0, institution="Morgan Stanley",
                  source_file="report_jpm_ms_202603.jsonl"),
            _srow("2026-05", value=8800.0, institution="Morgan Stanley",
                  source_file="report_jpm_ms_202603.jsonl"),
        ])
        res = self.q(city="杭州", metric_name="secondary_volume_units",
                     period_start="2026-03", period_end="2026-05")
        self.assertEqual(res["period_granularity"], "month")
        self.assertEqual(res["coverage"]["series_count"], 2)
        by_sf = {s["source_file"]: s for s in res["series"]}
        self.assertEqual(set(by_sf), {"beike.csv", "report_jpm_ms_202603.jsonl"})
        beike, ms = by_sf["beike.csv"], by_sf["report_jpm_ms_202603.jsonl"]
        self.assertEqual([p["value"] for p in beike["points"]], [9356.0, 9000.0, 9100.0])
        self.assertEqual(beike["missing_periods"], [])
        self.assertEqual([p["period"] for p in ms["points"]], ["2026-03", "2026-05"])
        self.assertEqual(ms["missing_periods"], ["2026-04"])  # 缺口明确，不插值不填 0
        self.assertEqual(ms["institution"], "Morgan Stanley")  # 机构名不覆盖 source_file
        for s in res["series"]:  # 证据字段齐全
            for p in s["points"]:
                for k in ("value", "value_text", "comparison_type", "comparison_value",
                          "source_page", "source_quote", "is_forecast"):
                    self.assertIn(k, p)
        self.assertEqual(res["coverage"]["missing_period_count"], 0)  # 03-05 并集全覆盖

    def test_coverage_missing_union_semantics(self):
        # coverage.missing_period_count = 所有 series 都未覆盖的请求时期数
        self.seed([_srow("2026-03", value=9356.0),
                   _srow("2026-03", value=8728.0, institution="Morgan Stanley",
                         source_file="ms.jsonl")])
        res = self.q(city="杭州", periods=["2026-03", "2026-04"])
        self.assertEqual(res["coverage"]["missing_period_count"], 1)  # 2026-04 两来源都缺
        self.assertEqual(res["coverage"]["returned_points"], 2)


class SeriesSameInstitutionTest(_SeriesQueryBase):
    """场景 2：同机构不同 source_file 默认仍分组；精确筛选互不覆盖。"""

    def setUp(self):
        super().setUp()
        self.seed([
            _srow("2026-03", value=9356.0, source_file="beike_a.csv"),
            _srow("2026-04", value=9000.0, source_file="beike_a.csv"),
            _srow("2026-03", value=9356.0, source_file="beike_b.csv", source_quote="转载引文"),
        ])

    def test_default_groups_by_source_file(self):
        res = self.q(city="杭州", metric_name="secondary_volume_units")
        self.assertEqual({s["source_file"] for s in res["series"]}, {"beike_a.csv", "beike_b.csv"})
        self.assertEqual({s["institution"] for s in res["series"]}, {"贝壳"})

    def test_institution_filter_keeps_multiple_files(self):
        res = self.q(institution="贝壳", city="杭州")
        self.assertEqual(res["coverage"]["series_count"], 2)

    def test_source_file_filter_exact_match(self):
        res = self.q(city="杭州", source_file="beike_a.csv")
        self.assertEqual([s["source_file"] for s in res["series"]], ["beike_a.csv"])
        self.assertEqual(res["coverage"]["returned_points"], 2)
        miss = self.q(city="杭州", source_file="beike_a")  # 前缀不命中：精确匹配
        self.assertEqual(miss["series"], [])
        self.assertTrue(miss["warnings"])
        miss2 = self.q(city="杭州", source_file="nonexistent.csv")  # 不存在：空结果 + warning
        self.assertEqual(miss2["series"], [])
        self.assertTrue(miss2["warnings"])


class SeriesConflictTest(_SeriesQueryBase):
    """场景 3：同一来源同一时期多个值——移入 conflicts，不平均不取舍不丢弃。"""

    def test_same_period_multi_value_conflict(self):
        self.seed([
            _srow("2026-03", value=9356.0, source_quote="贝壳口径 9356 套"),
            _srow("2026-03", value=9400.0, source_quote="修正稿 9400 套"),
            _srow("2026-04", value=9000.0),
        ])
        res = self.q(city="杭州", periods=["2026-03", "2026-04"])
        self.assertEqual(res["coverage"]["series_count"], 1)
        s = res["series"][0]
        self.assertEqual([p["period"] for p in s["points"]], ["2026-04"])  # 冲突期移出 points
        self.assertEqual(len(s["conflicts"]), 1)
        self.assertEqual(s["conflicts"][0]["period"], "2026-03")
        vals = sorted(p["value"] for p in s["conflicts"][0]["points"])
        self.assertEqual(vals, [9356.0, 9400.0])  # 两个值都保留
        self.assertNotIn(9378.0, vals)  # 未平均
        self.assertEqual(s["missing_periods"], [])  # 冲突期仍算已覆盖
        self.assertTrue(any("同期多值" in w for w in res["warnings"]))
        self.assertEqual(res["coverage"]["conflict_points"], 2)
        self.assertEqual(res["coverage"]["returned_points"], 1)


class SeriesFieldDifferenceTest(_SeriesQueryBase):
    """场景 4：segment / unit / is_forecast / comparison_type 不同 → 不同 series；
    comparison_value 逐期变化 → 同 series 不同点，均不被覆盖或消除。"""

    def test_field_differences_split(self):
        self.seed([
            _srow("2026-03", value=1.0),  # 基线
            _srow("2026-04", value=2.0, segment="new_home"),
            _srow("2026-05", value=3.0, unit="万平方米"),
            _srow("2026-06", value=4.0, is_forecast=1),
            _srow("2026-07", value=5.0, comparison_type="yoy", comparison_value=5.0),
            _srow("2026-08", value=6.0, comparison_type="yoy", comparison_value=6.0),
        ])
        res = self.q(city="杭州", metric_name="secondary_volume_units")
        self.assertEqual(res["coverage"]["series_count"], 5)
        self.assertEqual(res["coverage"]["returned_points"], 6)  # 6 行全部保留
        yoy = [s for s in res["series"] if s["points"][0]["comparison_type"] == "yoy"]
        self.assertEqual(len(yoy), 1)  # comparison_value 不同不拆组
        self.assertEqual([p["comparison_value"] for p in yoy[0]["points"]], [5.0, 6.0])
        keys = {(s["segment"], s["unit"], s["is_forecast"]) for s in res["series"]}
        self.assertEqual(len(keys), 4)  # 前四种差异各成组


class SeriesMissingMonthsTest(_SeriesQueryBase):
    """场景 5：月份缺口如实返回，不插值、不填 0、不用相邻月代替。"""

    def test_month_gap(self):
        self.seed([_srow("2026-03", value=100.0), _srow("2026-05", value=200.0),
                   _srow("2026-08", value=300.0)])
        res = self.q(city="杭州", period_start="2026-03", period_end="2026-08")
        self.assertEqual(res["coverage"]["requested_periods"],
                         ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"])
        s = res["series"][0]
        self.assertEqual(s["missing_periods"], ["2026-04", "2026-06", "2026-07"])
        self.assertEqual([p["value"] for p in s["points"]], [100.0, 200.0, 300.0])
        self.assertNotIn(0.0, [p["value"] for p in s["points"]])


class SeriesGranularityTest(_SeriesQueryBase):
    """场景 6：季度 / 半年 / 月互不混组，不自动转换，粒度排序稳定。"""

    def setUp(self):
        super().setUp()
        self.seed([
            _srow("2026Q1", value=10.0, unit="亿元", metric_name="developer_sales_amount",
                  segment="macro"),
            _srow("2026Q2", value=20.0, unit="亿元", metric_name="developer_sales_amount",
                  segment="macro"),
            _srow("2026-H1", value=30.0, unit="亿元", metric_name="developer_sales_amount",
                  segment="macro"),
            _srow("2026-03", value=40.0, unit="亿元", metric_name="developer_sales_amount",
                  segment="macro"),
        ])

    def test_quarter_query(self):
        res = self.q(city="杭州", periods=["2026Q2", "2026Q1"])  # 乱序输入 → 按时期排序
        self.assertEqual(res["period_granularity"], "quarter")
        self.assertEqual(res["coverage"]["series_count"], 1)
        self.assertEqual([p["period"] for p in res["series"][0]["points"]], ["2026Q1", "2026Q2"])

    def test_quarter_range_expands_quarters(self):
        res = self.q(city="杭州", period_start="2026Q1", period_end="2026Q2")
        self.assertEqual(res["coverage"]["requested_periods"], ["2026Q1", "2026Q2"])
        self.assertEqual(res["period_granularity"], "quarter")

    def test_half_year_query(self):
        res = self.q(city="杭州", periods=["2026-H1"])
        self.assertEqual(res["period_granularity"], "half")
        self.assertEqual([p["value"] for p in res["series"][0]["points"]], [30.0])

    def test_no_filter_splits_by_granularity(self):
        res = self.q(city="杭州")  # 无时期条件：月/季/半年各成 series
        self.assertEqual(res["coverage"]["series_count"], 3)
        self.assertEqual({s["period_granularity"] for s in res["series"]},
                         {"month", "quarter", "half"})
        self.assertEqual(res["period_granularity"], "mixed")

    def test_mixed_granularity_request_rejected(self):
        res = self.q(city="杭州", periods=["2026Q1", "2026-03"])
        self.assertEqual(res["series"], [])
        self.assertTrue(any("粒度" in w for w in res["warnings"]))

    def test_month_request_misses_quarterly_data(self):
        res = self.q(city="杭州", periods=["2026-04"])  # 库里只有季度/半年/03 月
        self.assertEqual(res["series"], [])
        self.assertEqual(res["coverage"]["missing_period_count"], 1)
        self.assertTrue(res["warnings"])

    def test_cross_granularity_range_rejected(self):
        res = self.q(city="杭州", period_start="2026Q1", period_end="2026-06")
        self.assertEqual(res["series"], [])
        self.assertTrue(any("粒度" in w for w in res["warnings"]))


class SeriesForecastTest(_SeriesQueryBase):
    """场景 7：预测与实际分两条 series；include_forecasts=False 只回实际。"""

    def setUp(self):
        super().setUp()
        self.seed([
            _srow("2026-03", value=9356.0),
            _srow("2026-04", value=9000.0),
            _srow("2026-05", value=9500.0, is_forecast=1, source_quote="预计 5 月 9500 套"),
            _srow("2026-06", value=9600.0, is_forecast=1, source_quote="预计 6 月 9600 套"),
        ])

    def test_forecast_split_series(self):
        res = self.q(city="杭州", metric_name="secondary_volume_units")
        self.assertEqual(res["coverage"]["series_count"], 2)
        flags = {s["is_forecast"] for s in res["series"]}
        self.assertEqual(flags, {False, True})
        for s in res["series"]:
            self.assertEqual({p["is_forecast"] for p in s["points"]}, {s["is_forecast"]})
        self.assertTrue(any("预测" in w for w in res["warnings"]))

    def test_exclude_forecasts(self):
        res = self.q(city="杭州", include_forecasts=False)
        self.assertEqual(res["coverage"]["series_count"], 1)
        s = res["series"][0]
        self.assertFalse(s["is_forecast"])
        self.assertEqual([p["period"] for p in s["points"]], ["2026-03", "2026-04"])
        self.assertTrue(any("已过滤" in w for w in res["warnings"]))


class SeriesEmptyCityTest(_SeriesQueryBase):
    """场景 8：空城市不自动归为杭州/全国；未指定城市时带明确标签返回。"""

    def setUp(self):
        super().setUp()
        self.seed([
            _srow("2026-03", value=9356.0, city="杭州"),
            _srow("2026-03", value=500.0, city="", source_file="unknown.csv",
                  institution="未知机构"),
            _srow("2026-04", value=600.0, city="", source_file="unknown.csv",
                  institution="未知机构"),
        ])

    def test_city_exact_match_excludes_empty(self):
        res = self.q(city="杭州")
        self.assertEqual(res["coverage"]["series_count"], 1)
        self.assertEqual(res["series"][0]["city"], "杭州")
        self.assertEqual(res["coverage"]["returned_points"], 1)  # 空城市记录不混入

    def test_no_city_filter_returns_labeled_empty_city(self):
        res = self.q()
        self.assertEqual(res["coverage"]["series_count"], 2)
        labels = {s["city_label"] for s in res["series"]}
        self.assertEqual(labels, {"杭州", mq.EMPTY_CITY_LABEL})
        empty = [s for s in res["series"] if s["city"] == ""][0]
        self.assertEqual(empty["city_label"], "未归一化城市")

    def test_database_city_not_rewritten(self):
        self.q(city="杭州")
        self.q()
        conn = sqlite3.connect(str(self.db))
        try:
            cities = sorted(r[0] for r in conn.execute("SELECT DISTINCT city FROM metrics"))
        finally:
            conn.close()
        self.assertEqual(cities, ["", "杭州"])  # 查询不改写数据库字段


class SeriesLegacySchemaTest(_SeriesQueryBase):
    """场景 9：旧 schema（无 source_file 列）——查询可运行、不迁移、不改 schema。"""

    schema = _SERIES_SCHEMA_LEGACY

    def seed(self, rows):
        for r in rows:
            r.pop("source_file", None)
        super().seed(rows)

    def test_query_runs_with_none_source_file(self):
        self.seed([_srow("2026-03", value=9356.0)])
        schema_before, hash_before = self.db_schema_sql(), self.db_sha256()
        res = self.q(city="杭州")
        self.assertEqual(res["coverage"]["series_count"], 1)
        self.assertIsNone(res["series"][0]["source_file"])
        self.assertEqual(res["series"][0]["source_key"].split("|")[1], "")  # source_file 段为空
        self.assertEqual(self.db_schema_sql(), schema_before)  # 未自动迁移
        self.assertEqual(self.db_sha256(), hash_before)  # 内容零变化

    def test_source_file_filter_unavailable(self):
        self.seed([_srow("2026-03")])
        res = self.q(city="杭州", source_file="beike.csv")
        self.assertEqual(res["series"], [])
        self.assertTrue(any("source_file" in w for w in res["warnings"]))


class SeriesSqlInjectionTest(_SeriesQueryBase):
    """场景 10：SQL 注入输入被参数化挡住，不泄露数据、不改库。"""

    def test_injection_attempts(self):
        self.seed([_srow("2026-03", city="杭州"), _srow("2026-03", city="北京")])
        hash_before = self.db_sha256()
        attacks = [
            {"city": "杭州' OR 1=1 --"},
            {"institution": "贝壳' OR '1'='1"},
            {"metric_name": "x'; DROP TABLE metrics;--"},
            {"source_file": "' OR 1=1 --"},
            {"city": "杭州\" UNION SELECT * FROM metrics --"},
        ]
        for atk in attacks:
            res = self.q(**atk)
            self.assertEqual(res["series"], [], msg=str(atk))  # 精确匹配：无泄露
            self.assertTrue(res["warnings"], msg=str(atk))
        self.assertEqual(self.db_sha256(), hash_before)
        res = self.q()  # 表仍在，数据完整
        self.assertEqual(res["coverage"]["returned_points"], 2)


class SeriesResourceTest(_SeriesQueryBase):
    """场景 11：查询后连接关闭、文件可删可改名、内容零变化。"""

    def test_connection_closed_file_reusable(self):
        self.seed([_srow("2026-03")])
        hash_before = self.db_sha256()
        self.q(city="杭州")
        self.assertEqual(self.db_sha256(), hash_before)
        renamed = self.db.with_name("renamed.db")  # 有未关闭句柄/锁时改名会失败
        self.db.rename(renamed)
        res = mq.query_metric_series(city="杭州", db_path=renamed)
        self.assertEqual(res["coverage"]["series_count"], 1)
        renamed.unlink()
        self.assertFalse(renamed.exists())

    def test_readonly_connection_rejects_writes(self):
        self.seed([_srow("2026-03")])
        conn = mq._connect_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO metrics (period) VALUES ('2026-01')")
        finally:
            conn.close()


class SeriesPeriodRequestTest(_SeriesQueryBase):
    """时期请求契约：校验、去重、排序、粒度、范围展开、裸年份不展开。"""

    def setUp(self):
        super().setUp()
        self.seed([_srow("2026-03", value=1.0)])

    def test_invalid_periods_warn_and_skip(self):
        res = self.q(city="杭州", periods=["2026-13", "2026-3", "2026-03"])
        self.assertEqual(res["coverage"]["requested_periods"], ["2026-03"])
        self.assertTrue(any("非法时期" in w for w in res["warnings"]))
        self.assertEqual(res["coverage"]["returned_points"], 1)

    def test_all_invalid_periods_empty_with_warning(self):
        res = self.q(city="杭州", periods=["三月", "2026/03"])
        self.assertEqual(res["series"], [])
        self.assertTrue(any("全部非法" in w for w in res["warnings"]))

    def test_dedup_stable_sorted(self):
        res = self.q(city="杭州", periods=["2026-05", "2026-03", "2026-05"])
        self.assertEqual(res["coverage"]["requested_periods"], ["2026-03", "2026-05"])

    def test_only_start_rejected(self):
        res = self.q(city="杭州", period_start="2026-03")
        self.assertEqual(res["series"], [])
        self.assertTrue(any("同时给出" in w for w in res["warnings"]))

    def test_periods_override_range_with_warning(self):
        res = self.q(city="杭州", periods=["2026-03"],
                     period_start="2026-01", period_end="2026-02")
        self.assertEqual(res["coverage"]["requested_periods"], ["2026-03"])
        self.assertTrue(any("优先使用显式 periods" in w for w in res["warnings"]))

    def test_bare_year_not_expanded(self):
        res = self.q(city="杭州", periods=["2026"])
        self.assertEqual(res["period_granularity"], "year")
        self.assertEqual(res["coverage"]["requested_periods"], ["2026"])
        self.assertEqual(res["series"], [])  # 年度格式与月度数据不匹配：空 + warning
        res2 = self.q(city="杭州", period_start="2025", period_end="2027")
        self.assertEqual(res2["coverage"]["requested_periods"], ["2025", "2026", "2027"])

    def test_lowercase_quarter_normalized(self):
        res = self.q(city="杭州", periods=["2026q1"])
        self.assertEqual(res["coverage"]["requested_periods"], ["2026Q1"])

    def test_empty_result_is_structured_not_none(self):
        res = self.q(city="不存在城市")
        self.assertIsInstance(res, dict)
        self.assertEqual(res["series"], [])
        self.assertEqual(res["coverage"]["series_count"], 0)
        self.assertTrue(res["warnings"])
        self.assertEqual(res["filters"]["city"], "不存在城市")


class SeriesNlAdapterTest(_SeriesQueryBase):
    """薄自然语言适配层：只解析城市/指标/时期。"""

    def test_nl_adapter(self):
        self.seed([_srow(f"2026-{m:02d}", value=float(m)) for m in range(3, 6)])
        res = mq.query_metric_series_nl("杭州二手房2026年3月到5月的成交量走势", db_path=self.db)
        self.assertEqual(res["filters"]["city"], "杭州")
        self.assertEqual(res["filters"]["metric_name"], "secondary_volume_units")
        self.assertEqual(len(res["coverage"]["requested_periods"]), 3)
        self.assertEqual(res["coverage"]["returned_points"], 3)


_FORMAL_DB = APP / "data" / "rag.db"


def _file_fingerprint(path: Path):
    st = path.stat()
    return (st.st_size, st.st_mtime, hashlib.sha256(path.read_bytes()).hexdigest())


def _metrics_dir_hash():
    h = hashlib.sha256()
    for f in sorted((APP / "metrics").iterdir()):
        if f.suffix in (".jsonl", ".csv"):
            h.update(f.name.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


@unittest.skipUnless(_FORMAL_DB.exists(), "正式库不存在，跳过只读验证")
class SeriesFormalReadonlyTest(_SeriesQueryBase):
    """场景 12：正式库只读验证——查询可运行，size/mtime/sha256 与源文件聚合 hash 不变。

    不调用 setUp 的临时库逻辑，直接对 data/rag.db 做 mode=ro 查询。
    """

    def setUp(self):
        self.db = _FORMAL_DB
        self._tmp = None

    def tearDown(self):
        pass

    def test_formal_queries_and_fingerprint_unchanged(self):
        db_fp_before = _file_fingerprint(_FORMAL_DB)
        metrics_hash_before = _metrics_dir_hash()

        r1 = mq.query_metric_series(city="杭州", metric_name="secondary_volume_units",
                                    db_path=_FORMAL_DB)
        r2 = mq.query_metric_series(city="杭州", metric_name="inventory_months",
                                    db_path=_FORMAL_DB)
        for res, label in ((r1, "杭州二手房成交"), (r2, "杭州库存去化")):
            self.assertIsInstance(res, dict, msg=label)
            for key in ("filters", "period_granularity", "series", "coverage", "warnings"):
                self.assertIn(key, res, msg=label)
            for s in res["series"]:
                for key in ("institution", "source_file", "city", "metric_name", "segment",
                            "unit", "is_forecast", "points", "missing_periods", "conflicts"):
                    self.assertIn(key, s, msg=label)
                self.assertEqual(s["city"], "杭州")  # 不返回其他城市冒充
                self.assertTrue(s["source_file"], msg="正式库 source_file 应已归属")

        self.assertGreaterEqual(r1["coverage"]["series_count"], 2,  # 贝壳+大摩多来源
                                msg="杭州二手房成交应有多个来源分组")

        self.assertEqual(_file_fingerprint(_FORMAL_DB), db_fp_before)
        self.assertEqual(_metrics_dir_hash(), metrics_hash_before)
        self.assertFalse((APP / "data" / "rag.db-wal").exists())
        self.assertFalse((APP / "data" / "rag.db-shm").exists())


if __name__ == "__main__":
    unittest.main()
