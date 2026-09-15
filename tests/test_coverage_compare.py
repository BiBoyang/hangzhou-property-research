"""Step 3C：compare_metric_across_cities 城市横向比较 + coverage_compare.py CLI 测试。

全部使用临时 SQLite 库（plain sqlite3 建库，不经 lib_db.connect()），
正式库仅做只读指纹验证。不调用 run_eval.py 或任何外部 API。
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_metrics_query as mq  # noqa: E402

CLI = APP / "scripts" / "coverage_compare.py"

_SCHEMA = """
CREATE TABLE metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,
  metric_name TEXT, value REAL, value_text TEXT, unit TEXT,
  comparison_type TEXT, comparison_value REAL, is_forecast INTEGER DEFAULT 0,
  source_page TEXT, source_quote TEXT, source_url TEXT, source_file TEXT
);
"""


def _row(period, city, value=1000.0, metric_name="secondary_volume_units", **kw):
    row = {
        "doc_id": None, "institution": "贝壳", "period": period, "city": city,
        "segment": "secondary_home", "metric_name": metric_name, "value": value,
        "value_text": None, "unit": "套", "comparison_type": None,
        "comparison_value": None, "is_forecast": 0, "source_page": "p1",
        "source_quote": f"引文-{city}-{period}", "source_url": None,
        "source_file": "shared.csv",
    }
    row.update(kw)
    return row


def _assert_no_derived_keys(tc, obj):
    """递归断言输出结构中不存在差值/倍数/排名等派生指标字段。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            tc.assertNotIn(str(k).lower(), ("diff", "ratio", "rank", "delta"))
            _assert_no_derived_keys(tc, v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_derived_keys(tc, v)


class _CompareBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "cmp.db"
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

    def cmp(self, **kw):
        return mq.compare_metric_across_cities(db_path=self.db, **kw)

    def db_sha256(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest()


class CompareBasicTest(_CompareBase):
    """四城市 / 杭州-上海 / 杭州-全国 / 多城市列表 / 同指标同单位严格可比。"""

    def setUp(self):
        super().setUp()
        self.seed([
            *[_row(f"2026-{m:02d}", "杭州", value=9000.0 + m) for m in (3, 4, 5)],
            *[_row(f"2026-{m:02d}", "上海", value=15000.0 + m) for m in (3, 4, 5)],
            *[_row(f"2026-{m:02d}", "北京", value=13000.0 + m) for m in (3, 4)],
            *[_row(f"2026-{m:02d}", "深圳", value=8000.0 + m) for m in (4, 5)],
            _row("2026-04", "全国", value=50000.0, unit="套"),
        ])

    def test_four_cities_one_comparable_group(self):
        rep = self.cmp(cities=["杭州", "上海", "北京", "深圳"],
                       metric_name="secondary_volume_units")
        self.assertEqual(rep["summary"]["overall_status"], "ok")
        g = rep["comparison_groups"][0]
        self.assertEqual(g["comparability"], "comparable")
        self.assertEqual({c["city"] for c in g["cities"]}, {"杭州", "上海", "北京", "深圳"})
        for c in g["cities"]:  # 契约字段齐全
            for k in ("city", "city_label", "institution", "source_file", "segment",
                      "unit", "comparison_type", "is_forecast", "period_granularity",
                      "points", "observed_periods", "missing_periods", "conflicts"):
                self.assertIn(k, c)
        # 共同覆盖期 = 交集：杭州{3,4,5} ∩ 上海{3,4,5} ∩ 北京{3,4} ∩ 深圳{4,5}
        self.assertEqual(g["common_periods"], ["2026-04"])
        self.assertEqual(g["common_period_count"], 1)
        self.assertTrue(g["insufficient_common_periods"])
        # 只被部分城市覆盖的时期
        self.assertEqual(g["partially_covered_periods"], ["2026-03", "2026-05"])
        self.assertTrue(any("共同覆盖期不足" in w for w in g["warnings"]))
        self.assertIn("insufficient_common_periods", rep["summary"]["status_flags"])

    def test_hangzhou_vs_shanghai_full_common(self):
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                       period_start="2026-03", period_end="2026-05")
        g = rep["comparison_groups"][0]
        self.assertEqual(g["common_periods"], ["2026-03", "2026-04", "2026-05"])
        self.assertFalse(g["insufficient_common_periods"])

    def test_hangzhou_vs_national(self):
        rep = self.cmp(cities=["杭州", "全国"], metric_name="secondary_volume_units")
        g = rep["comparison_groups"][0]
        self.assertEqual({c["city"] for c in g["cities"]}, {"杭州", "全国"})
        nat = [c for c in g["cities"] if c["city"] == "全国"][0]
        self.assertEqual(nat["observed_periods"], ["2026-04"])  # 全国数据不冒充城市

    def test_city_list_dedup_and_missing_periods(self):
        rep = self.cmp(cities=["杭州", "杭州", "北京"], metric_name="secondary_volume_units",
                       periods=["2026-03", "2026-04", "2026-05"])
        self.assertEqual(rep["filters"]["cities"], ["杭州", "北京"])  # 去重
        by_city = {c["city"]: c for c in rep["comparison_groups"][0]["cities"]}
        self.assertEqual(by_city["北京"]["missing_periods"], ["2026-05"])
        self.assertEqual(by_city["杭州"]["missing_periods"], [])
        self.assertEqual(rep["requested_periods"], ["2026-03", "2026-04", "2026-05"])


class CompareStrictGroupingTest(_CompareBase):
    """segment / unit / comparison_type / forecast 差异不合并；同来源严格、不同来源 limited。"""

    def test_field_differences_split_groups(self):
        self.seed([
            _row("2026-03", "杭州"), _row("2026-03", "上海"),
            _row("2026-03", "北京", segment="new_home"),          # segment 不同
            _row("2026-03", "北京", unit="万平方米"),               # unit 不同
            _row("2026-03", "深圳", comparison_type="yoy", comparison_value=1.0),  # 口径不同
        ])
        rep = self.cmp(cities=["杭州", "上海", "北京", "深圳"],
                       metric_name="secondary_volume_units")
        keys = {(g["comparison_key"]["segment"], g["comparison_key"]["unit"],
                 g["comparison_key"]["comparison_type"]) for g in rep["comparison_groups"]}
        self.assertEqual(len(keys), 4)  # 四种差异各成组，不合并
        main = [g for g in rep["comparison_groups"]
                if g["comparison_key"]["segment"] == "secondary_home"
                and g["comparison_key"]["unit"] == "套"
                and not g["comparison_key"]["comparison_type"]][0]
        self.assertEqual({c["city"] for c in main["cities"]}, {"杭州", "上海"})

    def test_actual_forecast_not_mixed(self):
        self.seed([
            _row("2026-03", "杭州"), _row("2026-03", "上海"),
            _row("2026-04", "杭州", is_forecast=1, source_quote="预计"),
            _row("2026-04", "上海", is_forecast=1, source_quote="预计"),
        ])
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units")
        # 默认 include_forecasts=False：预测被过滤
        self.assertEqual(rep["summary"]["group_count"], 1)
        self.assertTrue(all(not c["is_forecast"]
                            for g in rep["comparison_groups"] for c in g["cities"]))
        # 显式开启：预测只和预测比较，单独成组
        rep2 = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                        include_forecasts=True)
        self.assertEqual(rep2["summary"]["group_count"], 2)
        fc = {g["comparison_key"]["is_forecast"] for g in rep2["comparison_groups"]}
        self.assertEqual(fc, {False, True})
        for g in rep2["comparison_groups"]:
            self.assertEqual({c["is_forecast"] for c in g["cities"]},
                             {g["comparison_key"]["is_forecast"]})
        self.assertTrue(any("预测" in w for w in rep2["warnings"]))

    def test_same_source_strict_comparison(self):
        self.seed([
            _row("2026-03", "杭州", source_file="a.csv"),
            _row("2026-04", "杭州", source_file="a.csv"),
            _row("2026-03", "北京", source_file="a.csv"),
            _row("2026-04", "北京", source_file="a.csv"),
            _row("2026-03", "上海", source_file="b.csv", institution="克而瑞"),
        ])
        rep = self.cmp(cities=["杭州", "上海", "北京"], metric_name="secondary_volume_units")
        comp = [g for g in rep["comparison_groups"] if g["comparability"] == "comparable"]
        cross = [g for g in comp if len({c["city"] for c in g["cities"]}) >= 2][0]
        # 同来源 a.csv：杭州+北京 严格可比；上海（b.csv）不混入
        self.assertEqual({c["city"] for c in cross["cities"]}, {"杭州", "北京"})
        self.assertEqual({c["source_file"] for c in cross["cities"]}, {"a.csv"})
        self.assertEqual(cross["common_periods"], ["2026-03", "2026-04"])

    def test_cross_source_limited(self):
        self.seed([
            _row("2026-03", "杭州", source_file="a.csv"),
            _row("2026-04", "杭州", source_file="a.csv"),
            _row("2026-03", "上海", source_file="b.csv", institution="克而瑞"),
            _row("2026-04", "上海", source_file="b.csv", institution="克而瑞"),
        ])
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units")
        # 无严格组跨城、聚合覆盖 2 城 → 并列 limited
        self.assertEqual(rep["summary"]["group_count"], 1)
        g = rep["comparison_groups"][0]
        self.assertEqual(g["comparability"], "limited")
        self.assertEqual({c["city"] for c in g["cities"]}, {"杭州", "上海"})
        self.assertTrue(any("limited" in w for w in g["warnings"]))
        _assert_no_derived_keys(self, g)

    def test_limited_no_derived_metrics(self):
        self.seed([_row("2026-03", "杭州", source_file="a.csv"),
                   _row("2026-03", "上海", source_file="b.csv", institution="克而瑞")])
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units")
        g = rep["comparison_groups"][0]
        self.assertEqual(g["comparability"], "limited")
        _assert_no_derived_keys(self, g)  # 不计算差值/倍数/排名

    def test_institution_and_source_file_filters(self):
        self.seed([
            _row("2026-03", "杭州", source_file="a.csv"),
            _row("2026-03", "上海", source_file="a.csv"),
            _row("2026-03", "杭州", source_file="b.csv", institution="克而瑞"),
            _row("2026-03", "上海", source_file="b.csv", institution="克而瑞"),
        ])
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                       institution="克而瑞")
        self.assertEqual({c["institution"] for g in rep["comparison_groups"]
                          for c in g["cities"]}, {"克而瑞"})
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                       source_file="a.csv")
        self.assertEqual({c["source_file"] for g in rep["comparison_groups"]
                          for c in g["cities"]}, {"a.csv"})
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                       source_file="a")  # 前缀不命中：精确匹配
        self.assertEqual(rep["comparison_groups"], [])
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                       institution="不存在机构")
        self.assertEqual(rep["summary"]["overall_status"], "unmatched_metric")


class CompareConflictTest(_CompareBase):
    """同源同期多值：全部保留，标记 conflicted，不平均不取舍不丢弃。"""

    def test_conflict_preserved_and_flagged(self):
        self.seed([
            _row("2026-03", "杭州", value=9356.0, source_quote="口径A"),
            _row("2026-03", "杭州", value=9400.0, source_quote="口径B"),
            _row("2026-04", "杭州", value=9000.0),
            _row("2026-03", "上海", value=15000.0),
            _row("2026-04", "上海", value=15200.0),
        ])
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units")
        g = rep["comparison_groups"][0]
        hz = [c for c in g["cities"] if c["city"] == "杭州"][0]
        self.assertEqual(len(hz["conflicts"]), 1)
        vals = sorted(p["value"] for p in hz["conflicts"][0]["points"])
        self.assertEqual(vals, [9356.0, 9400.0])  # 两值都保留，未平均
        self.assertTrue(g["has_conflicts"])
        self.assertIn("conflicted", g["status_flags"])
        self.assertIn("conflicted", rep["summary"]["status_flags"])
        # 冲突期计为已覆盖：交集仍含 2026-03
        self.assertIn("2026-03", g["common_periods"])


class CompareGranularityTest(_CompareBase):
    """月 / 季 / 半年各自比较，不转换；混合粒度请求拒绝。"""

    def setUp(self):
        super().setUp()
        self.seed([
            _row("2026-03", "杭州"), _row("2026-04", "杭州"),
            _row("2026-03", "上海"), _row("2026-04", "上海"),
            _row("2026Q1", "杭州", metric_name="developer_sales_amount", unit="亿元",
                 segment="macro"),
            _row("2026Q2", "杭州", metric_name="developer_sales_amount", unit="亿元",
                 segment="macro"),
            _row("2026Q1", "上海", metric_name="developer_sales_amount", unit="亿元",
                 segment="macro"),
            _row("2026Q2", "上海", metric_name="developer_sales_amount", unit="亿元",
                 segment="macro"),
            _row("2026-H1", "杭州", metric_name="land_transaction_value", unit="亿元",
                 segment="land"),
            _row("2026-H2", "杭州", metric_name="land_transaction_value", unit="亿元",
                 segment="land"),
            _row("2026-H1", "深圳", metric_name="land_transaction_value", unit="亿元",
                 segment="land"),
            _row("2026-H2", "深圳", metric_name="land_transaction_value", unit="亿元",
                 segment="land"),
        ])

    def test_month_quarter_half_comparison(self):
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units")
        g = rep["comparison_groups"][0]
        self.assertEqual(g["comparison_key"]["period_granularity"], "month")
        self.assertEqual(g["common_periods"], ["2026-03", "2026-04"])
        rep = self.cmp(cities=["杭州", "上海"], metric_name="developer_sales_amount")
        g = rep["comparison_groups"][0]
        self.assertEqual(g["comparison_key"]["period_granularity"], "quarter")
        self.assertEqual(g["common_periods"], ["2026Q1", "2026Q2"])
        rep = self.cmp(cities=["杭州", "深圳"], metric_name="land_transaction_value")
        g = rep["comparison_groups"][0]
        self.assertEqual(g["comparison_key"]["period_granularity"], "half")
        self.assertEqual(g["common_periods"], ["2026-H1", "2026-H2"])

    def test_quarterly_request_misses_monthly(self):
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                       periods=["2026Q1"])  # 库里是月度：格式不一致不转换
        self.assertEqual(rep["comparison_groups"], [])
        self.assertEqual(rep["summary"]["overall_status"], "unmatched_metric")

    def test_mixed_granularity_request_rejected(self):
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units",
                       periods=["2026Q1", "2026-03"])
        self.assertEqual(rep["comparison_groups"], [])
        self.assertTrue(any("粒度" in w for w in rep["warnings"]))

    def test_multi_granularity_result_flagged(self):
        # 同指标下月度与季度序列并存：各组粒度一致，跨组标 mixed_granularity
        self.seed([
            _row("2026Q1", "北京", metric_name="secondary_volume_units"),
            _row("2026Q2", "北京", metric_name="secondary_volume_units"),
            _row("2026Q1", "深圳", metric_name="secondary_volume_units"),
            _row("2026Q2", "深圳", metric_name="secondary_volume_units"),
        ])
        rep = self.cmp(cities=["杭州", "上海", "北京", "深圳"],
                       metric_name="secondary_volume_units")
        grans = {g["comparison_key"]["period_granularity"]
                 for g in rep["comparison_groups"]}
        self.assertEqual(grans, {"month", "quarter"})
        self.assertIn("mixed_granularity", rep["summary"]["status_flags"])
        for g in rep["comparison_groups"]:
            self.assertEqual(len({c["period_granularity"] for c in g["cities"]}), 1)


class CompareCityHandlingTest(_CompareBase):
    """空城市排除、不支持城市、不存在指标。"""

    def setUp(self):
        super().setUp()
        self.seed([_row("2026-03", "杭州"), _row("2026-03", "上海"),
                   _row("2026-03", "", institution="未知机构", source_file="u.csv")])

    def test_empty_city_input_excluded(self):
        rep = self.cmp(cities=["杭州", "", "上海"], metric_name="secondary_volume_units")
        self.assertEqual(rep["filters"]["cities"], ["杭州", "上海"])
        self.assertEqual(rep["summary"]["excluded_cities"], ["''"])
        self.assertTrue(any("空城市" in w for w in rep["warnings"]))
        for g in rep["comparison_groups"]:  # 空城市记录不纳入任何比较组
            self.assertNotIn("", {c["city"] for c in g["cities"]})

    def test_unsupported_city(self):
        rep = self.cmp(cities=["杭州", "成都"], metric_name="secondary_volume_units")
        self.assertIn("成都", rep["uncovered_cities"])
        self.assertIn("uncovered_city", rep["summary"]["status_flags"])
        self.assertTrue(any("暂不覆盖" in w for w in rep["warnings"]))
        for g in rep["comparison_groups"]:
            self.assertNotIn("成都", {c["city"] for c in g["cities"]})

    def test_city_without_data_not_substituted(self):
        rep = self.cmp(cities=["杭州", "广州"], metric_name="secondary_volume_units")
        self.assertIn("广州", rep["uncovered_cities"])
        all_cities = {c["city"] for g in rep["comparison_groups"] for c in g["cities"]}
        self.assertEqual(all_cities, {"杭州"})  # 不用全国/其他城市代替

    def test_unmatched_metric(self):
        rep = self.cmp(cities=["杭州", "上海"], metric_name="nonexistent_metric")
        self.assertEqual(rep["comparison_groups"], [])
        self.assertEqual(rep["summary"]["overall_status"], "unmatched_metric")
        self.assertIn("unmatched_metric", rep["summary"]["status_flags"])


class CompareSecurityResourceTest(_CompareBase):
    """SQL 注入、只读连接、连接关闭、内容零变化。"""

    def test_sql_injection_blocked(self):
        self.seed([_row("2026-03", "杭州"), _row("2026-03", "上海")])
        hash_before = self.db_sha256()
        attacks = [
            {"cities": ["杭州' OR 1=1 --", "上海"]},
            {"cities": ["杭州", "上海"], "institution": "贝壳' OR '1'='1"},
            {"cities": ["杭州", "上海"], "metric_name": "x'; DROP TABLE metrics;--"},
            {"cities": ["杭州", "上海"], "source_file": "' OR 1=1 --"},
        ]
        for atk in attacks:
            kw = {"metric_name": "secondary_volume_units", **atk}
            rep = self.cmp(**kw)
            leaked = {c["city"] for g in rep["comparison_groups"] for c in g["cities"]}
            if "cities" in atk:
                # 注入串精确匹配落空：杭州数据不得因 OR 1=1 泄露
                self.assertNotIn("杭州", leaked, msg=str(atk))
                self.assertIn(atk["cities"][0], rep["uncovered_cities"], msg=str(atk))
            else:
                self.assertEqual(rep["comparison_groups"], [], msg=str(atk))
        self.assertEqual(self.db_sha256(), hash_before)
        rep = self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units")
        self.assertEqual(rep["summary"]["overall_status"], "ok")  # 表仍在

    def test_readonly_and_connection_closed(self):
        self.seed([_row("2026-03", "杭州"), _row("2026-03", "上海")])
        conn = mq._connect_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO metrics (period) VALUES ('2026-01')")
        finally:
            conn.close()
        hash_before = self.db_sha256()
        self.cmp(cities=["杭州", "上海"], metric_name="secondary_volume_units")
        self.assertEqual(self.db_sha256(), hash_before)
        renamed = self.db.with_name("renamed.db")  # 有未关闭句柄时改名会失败
        self.db.rename(renamed)
        rep = mq.compare_metric_across_cities(
            cities=["杭州", "上海"], metric_name="secondary_volume_units", db_path=renamed)
        self.assertEqual(rep["summary"]["overall_status"], "ok")
        renamed.unlink()


class CompareNlTest(_CompareBase):
    """薄 NL 适配层：只解析城市/指标/时期。"""

    def test_nl_adapter(self):
        self.seed([_row(f"2026-{m:02d}", "杭州") for m in (3, 4)])
        self.seed_called = True
        rep = mq.compare_metric_across_cities_nl(
            "杭州和上海2026年3月到4月的二手房成交对比", db_path=self.db)
        self.assertEqual(rep["filters"]["cities"], ["杭州", "上海"])
        self.assertEqual(rep["requested_periods"], ["2026-03", "2026-04"])
        rep2 = mq.compare_metric_across_cities_nl("杭州房价如何", db_path=self.db)
        self.assertEqual(rep2["comparison_groups"], [])  # 城市 <2 不查询
        self.assertTrue(rep2["warnings"])


class CompareCliTest(_CompareBase):
    """CLI：参数透传、项目外 cwd、报告路径保护、JSON 序列化。"""

    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLI), "--db", str(self.db), *args],
            capture_output=True, text=True, cwd=cwd or str(self._tmp.name),
        )

    def setUp(self):
        super().setUp()
        self.seed([
            _row("2026-03", "杭州"), _row("2026-04", "杭州"),
            _row("2026-03", "上海"), _row("2026-04", "上海"),
        ])

    def test_cli_text_output(self):
        p = self.run_cli("--cities", "杭州,上海", "--metric", "secondary_volume_units")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("城市横向比较报告", p.stdout)
        self.assertIn("共同覆盖期", p.stdout)
        p = self.run_cli("--cities", "杭州", "--metric", "secondary_volume_units")
        self.assertNotEqual(p.returncode, 0)  # 少于 2 城报错
        p = self.run_cli("--cities", "杭州,上海", "--metric", "secondary_volume_units",
                         "--actual-only", "--include-forecasts")
        self.assertNotEqual(p.returncode, 0)  # 互斥参数

    def test_cli_json_serialization(self):
        p = self.run_cli("--cities", "杭州,上海", "--metric", "secondary_volume_units",
                         "--period-start", "2026-03", "--period-end", "2026-04", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)
        self.assertEqual(rep["requested_periods"], ["2026-03", "2026-04"])
        self.assertEqual(rep["comparison_groups"][0]["comparability"], "comparable")

    def test_cli_works_outside_project_cwd(self):
        with tempfile.TemporaryDirectory() as outside:
            p = subprocess.run(
                [sys.executable, str(CLI), "--db", str(self.db),
                 "--cities", "杭州,上海", "--metric", "secondary_volume_units", "--json"],
                capture_output=True, text=True, cwd=outside)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["summary"]["overall_status"], "ok")

    def test_cli_report_write_and_path_protection(self):
        target = Path(self._tmp.name) / "out" / "cmp.json"
        p = self.run_cli("--cities", "杭州,上海", "--metric", "secondary_volume_units",
                         "--report", str(target))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))
                         ["summary"]["overall_status"], "ok")
        p = self.run_cli("--cities", "杭州,上海", "--metric", "secondary_volume_units",
                         "--report", str(self.db))  # 不得覆盖数据库
        self.assertNotEqual(p.returncode, 0)
        p = self.run_cli("--cities", "杭州,上海", "--metric", "secondary_volume_units",
                         "--report", str(APP / "metrics" / "x.json"))  # 不得写入 metrics/
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((APP / "metrics" / "x.json").exists())


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
class CompareFormalReadonlyTest(unittest.TestCase):
    """正式库只读冒烟：四指标比较可运行，指纹与 metrics/ 聚合 hash 前后不变。"""

    def test_formal_comparisons_and_fingerprint_unchanged(self):
        db_fp_before = _file_fingerprint(_FORMAL_DB)
        metrics_hash_before = _metrics_dir_hash()

        cases = [
            (["杭州", "上海", "北京", "深圳"], "secondary_volume_units"),
            (["杭州", "上海", "北京", "深圳"], "avg_price"),
            (["杭州", "上海", "北京"], "yoy_change_pct"),
            (["杭州", "上海", "北京", "深圳"], "rent_level"),
        ]
        for cities, metric in cases:
            rep = mq.compare_metric_across_cities(
                cities=cities, metric_name=metric, db_path=_FORMAL_DB)
            for key in ("filters", "comparison_groups", "uncovered_cities",
                        "summary", "warnings"):
                self.assertIn(key, rep, msg=metric)
            for g in rep["comparison_groups"]:
                self.assertIn(g["comparability"], ("comparable", "limited"), msg=metric)
                for c in g["cities"]:
                    self.assertIn(c["city"], cities, msg=metric)  # 不返回请求外城市
            # 输出不含派生指标字段（递归键检查）
            _assert_no_derived_keys(self, rep)

        p = subprocess.run([sys.executable, str(CLI), "--cities", "杭州,上海,北京,深圳",
                            "--metric", "secondary_volume_units", "--json"],
                           capture_output=True, text=True, cwd="/tmp")
        self.assertEqual(p.returncode, 0, p.stderr)
        json.loads(p.stdout)

        self.assertEqual(_file_fingerprint(_FORMAL_DB), db_fp_before)
        self.assertEqual(_metrics_dir_hash(), metrics_hash_before)
        self.assertFalse((APP / "data" / "rag.db-wal").exists())
        self.assertFalse((APP / "data" / "rag.db-shm").exists())


if __name__ == "__main__":
    unittest.main()
