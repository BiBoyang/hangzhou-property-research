"""Step 3B：summarize_series_coverage 覆盖摘要 + coverage_report.py CLI 测试。

全部使用临时 SQLite 库（plain sqlite3 建库，不经 lib_db.connect()），
正式库仅做只读指纹验证。不调用任何外部 API。
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

CLI = APP / "scripts" / "coverage_report.py"

_SERIES_SCHEMA = """
CREATE TABLE metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,
  metric_name TEXT, value REAL, value_text TEXT, unit TEXT,
  comparison_type TEXT, comparison_value REAL, is_forecast INTEGER DEFAULT 0,
  source_page TEXT, source_quote TEXT, source_url TEXT, source_file TEXT
);
"""
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


class _CoverageBase(unittest.TestCase):
    schema = _SERIES_SCHEMA

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "cov.db"
        conn = sqlite3.connect(str(self.db))
        conn.executescript(self.schema)
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def seed(self, rows):
        if self.schema == _SERIES_SCHEMA_LEGACY:
            for r in rows:
                r.pop("source_file", None)
        cols = list(rows[0])
        conn = sqlite3.connect(str(self.db))
        conn.executemany(
            f"INSERT INTO metrics ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
            rows,
        )
        conn.commit()
        conn.close()

    def cov(self, **kw):
        return mq.summarize_series_coverage(db_path=self.db, **kw)

    def db_sha256(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest()


class CoverageFilterTest(_CoverageBase):
    """全库 / 城市 / 指标 / 机构 / 来源文件筛选。"""

    def setUp(self):
        super().setUp()
        self.seed([
            _srow("2026-03", value=1.0),
            _srow("2026-04", value=2.0),
            _srow("2026-03", value=3.0, city="北京", metric_name="inventory_months",
                  unit="月", institution="克而瑞", source_file="cric.jsonl"),
            _srow("2026-05", value=4.0, city="北京", metric_name="inventory_months",
                  unit="月", institution="克而瑞", source_file="cric.jsonl"),
        ])

    def test_full_db_report(self):
        rep = self.cov()
        self.assertEqual(rep["summary"]["series_count"], 2)
        self.assertEqual(rep["summary"]["city_metric_count"], 2)
        self.assertFalse(rep["has_explicit_window"])
        self.assertIsNone(rep["series"][0]["coverage_rate"])  # 无窗口不给覆盖率
        self.assertEqual(rep["series"][0]["missing_periods"], [])  # 不推断完整窗口
        for r in rep["series"]:  # 每条序列保留契约字段
            for k in ("city", "city_label", "metric_name", "institution", "source_file",
                      "segment", "unit", "comparison_type", "is_forecast",
                      "period_granularity", "first_period", "last_period",
                      "observed_periods", "requested_periods", "covered_count",
                      "missing_periods", "coverage_rate", "internal_gaps",
                      "conflict_periods", "status", "status_flags"):
                self.assertIn(k, r)

    def test_city_and_metric_filter(self):
        rep = self.cov(city="杭州")
        self.assertEqual(rep["summary"]["series_count"], 1)
        self.assertEqual(rep["series"][0]["city"], "杭州")
        rep = self.cov(metric_name="inventory_months")
        self.assertEqual({r["metric_name"] for r in rep["series"]}, {"inventory_months"})
        rep = self.cov(city="杭州", metric_name="inventory_months")  # 无交集
        self.assertEqual(rep["summary"]["series_count"], 0)
        self.assertEqual(rep["summary"]["overall_status"], "no_observation")

    def test_institution_and_source_file_filter(self):
        rep = self.cov(institution="克而瑞")
        self.assertEqual({r["institution"] for r in rep["series"]}, {"克而瑞"})
        rep = self.cov(source_file="cric.jsonl")
        self.assertEqual({r["source_file"] for r in rep["series"]}, {"cric.jsonl"})
        rep = self.cov(source_file="cric")  # 前缀不命中：精确匹配
        self.assertEqual(rep["series"], [])


class CoverageStatusTest(_CoverageBase):
    """有窗口：full / partial / single / forecast_only / no_observation / 冲突。"""

    def test_full_and_partial_and_single(self):
        self.seed([
            *[_srow(f"2026-{m:02d}", value=float(m), source_file="full.csv")
              for m in range(3, 7)],
            _srow("2026-03", value=1.0, source_file="part.csv", institution="中指云"),
            _srow("2026-05", value=2.0, source_file="part.csv", institution="中指云"),
            _srow("2026-06", value=3.0, source_file="part.csv", institution="中指云"),
            _srow("2026-04", value=4.0, source_file="single.csv", institution="克而瑞"),
        ])
        rep = self.cov(city="杭州", period_start="2026-03", period_end="2026-06")
        by_sf = {r["source_file"]: r for r in rep["series"]}
        full = by_sf["full.csv"]
        self.assertEqual(full["status"], "full_coverage")
        self.assertEqual(full["covered_count"], 4)
        self.assertEqual(full["missing_periods"], [])
        self.assertEqual(full["coverage_rate"], 1.0)
        part = by_sf["part.csv"]
        self.assertEqual(part["status"], "partial_coverage")
        self.assertEqual(part["missing_periods"], ["2026-04"])
        self.assertEqual(part["coverage_rate"], 0.75)
        self.assertEqual(by_sf["single.csv"]["status"], "single_point")  # 单点不算 full
        # 全局状态分布与“缺失最多”
        self.assertEqual(rep["summary"]["status_counts"],
                         {"full_coverage": 1, "partial_coverage": 1, "single_point": 1})
        least = rep["summary"]["least_covered"]
        self.assertEqual(least[0]["source_key"].split("|")[1], "single.csv")  # 缺 3 期最多
        self.assertEqual(least[0]["missing_count"], 3)
        self.assertEqual({e["source_key"].split("|")[1] for e in least},
                         {"part.csv", "single.csv"})
        self.assertTrue(any("覆盖不足" in w for w in rep["warnings"]))

    def test_no_observation_overall(self):
        self.seed([_srow("2026-03")])
        rep = self.cov(city="杭州", periods=["2027-01", "2027-02"])  # 窗口内无任何数据
        self.assertEqual(rep["series"], [])
        self.assertEqual(rep["summary"]["overall_status"], "no_observation")
        self.assertEqual(rep["summary"]["series_count"], 0)
        self.assertTrue(rep["has_explicit_window"])

    def test_forecast_only_series(self):
        self.seed([
            _srow("2026-03", value=1.0),
            _srow("2026-04", value=2.0, is_forecast=1, source_quote="预计"),
            _srow("2026-05", value=3.0, is_forecast=1, source_quote="预计"),
        ])
        rep = self.cov(city="杭州", period_start="2026-03", period_end="2026-05")
        by_fc = {r["is_forecast"]: r for r in rep["series"]}
        self.assertEqual(by_fc[True]["status"], "forecast_only")  # 满窗也不叫 full_coverage
        self.assertEqual(by_fc[False]["status"], "single_point")
        self.assertEqual(rep["summary"]["forecast_series_count"], 1)
        self.assertEqual(rep["summary"]["actual_series_count"], 1)

    def test_conflicts_flagged_not_missing(self):
        self.seed([
            _srow("2026-03", value=9356.0, source_quote="口径A"),
            _srow("2026-03", value=9400.0, source_quote="口径B"),
            _srow("2026-04", value=9000.0),
        ])
        rep = self.cov(city="杭州", periods=["2026-03", "2026-04"])
        r = rep["series"][0]
        self.assertEqual(r["conflict_periods"], ["2026-03"])
        self.assertIn("has_conflicts", r["status_flags"])
        self.assertEqual(r["missing_periods"], [])  # 冲突期不算缺失
        self.assertEqual(r["covered_count"], 2)  # 冲突期计为已覆盖
        self.assertEqual(rep["summary"]["series_with_conflicts"], 1)
        self.assertTrue(any("同期多值" in w for w in rep["warnings"]))  # 底层 warning 透传


class CoverageUnwindowedTest(_CoverageBase):
    """无窗口：单点 / 无内部缺口 / 有内部缺口；最小最大期不冒充完整窗口。"""

    def test_internal_gap_statuses(self):
        self.seed([
            _srow("2026-03", value=1.0, source_file="gap.csv"),
            _srow("2026-06", value=2.0, source_file="gap.csv"),
            _srow("2026-03", value=1.0, source_file="nogap.csv", institution="中指云"),
            _srow("2026-04", value=2.0, source_file="nogap.csv", institution="中指云"),
            _srow("2026-05", value=3.0, source_file="nogap.csv", institution="中指云"),
            _srow("2026-08", value=9.0, source_file="dot.csv", institution="克而瑞"),
        ])
        rep = self.cov(city="杭州")
        by_sf = {r["source_file"]: r for r in rep["series"]}
        gap = by_sf["gap.csv"]
        self.assertEqual(gap["status"], "multi_point_with_internal_gaps")
        self.assertEqual(gap["internal_gaps"], ["2026-04", "2026-05"])
        self.assertEqual(gap["first_period"], "2026-03")
        self.assertEqual(gap["last_period"], "2026-06")
        self.assertEqual(by_sf["nogap.csv"]["status"], "multi_point_no_internal_gap")
        self.assertEqual(by_sf["nogap.csv"]["internal_gaps"], [])
        self.assertEqual(by_sf["dot.csv"]["status"], "single_point")
        for r in rep["series"]:  # 无窗口：不推断完整窗口、不给覆盖率、不给 full_coverage
            self.assertIsNone(r["coverage_rate"])
            self.assertEqual(r["missing_periods"], [])
            self.assertNotEqual(r["status"], "full_coverage")
        # “缺失最多”按内部缺口排序
        self.assertEqual(rep["summary"]["least_covered"][0]["internal_gap_count"], 2)


class CoverageGranularityTest(_CoverageBase):
    """月 / 季 / 半年粒度隔离：内部缺口在各自粒度内计算，不互相转换。"""

    def test_granularity_isolation(self):
        self.seed([
            _srow("2026-03", value=1.0),
            _srow("2026-05", value=2.0),
            _srow("2026Q1", value=10.0, metric_name="developer_sales_amount",
                  unit="亿元", segment="macro"),
            _srow("2026Q3", value=30.0, metric_name="developer_sales_amount",
                  unit="亿元", segment="macro"),
            _srow("2026-H1", value=100.0, metric_name="land_transaction_value",
                  unit="亿元", segment="land", institution="克而瑞"),
        ])
        rep = self.cov(city="杭州")
        self.assertEqual(rep["summary"]["series_count"], 3)
        by_mn = {r["metric_name"]: r for r in rep["series"]}
        self.assertEqual(by_mn["secondary_volume_units"]["period_granularity"], "month")
        self.assertEqual(by_mn["secondary_volume_units"]["internal_gaps"], ["2026-04"])
        q = by_mn["developer_sales_amount"]
        self.assertEqual(q["period_granularity"], "quarter")
        self.assertEqual(q["internal_gaps"], ["2026Q2"])  # 按季度展开，不含月份
        self.assertEqual(by_mn["land_transaction_value"]["status"], "single_point")
        # 城市-指标汇总互不串组
        self.assertEqual(rep["summary"]["city_metric_count"], 3)


class CoverageActualOnlyTest(_CoverageBase):
    """actual_only=True：预测被过滤，只统计实际观测。"""

    def test_actual_only(self):
        self.seed([
            _srow("2026-03", value=1.0),
            _srow("2026-04", value=2.0, is_forecast=1, source_quote="预计"),
        ])
        rep = self.cov(city="杭州", actual_only=True)
        self.assertEqual(rep["summary"]["series_count"], 1)
        self.assertEqual(rep["summary"]["forecast_series_count"], 0)
        self.assertFalse(rep["series"][0]["is_forecast"])
        self.assertTrue(any("已过滤" in w for w in rep["warnings"]))
        rep2 = self.cov(city="杭州")  # 对照：默认含预测，分开统计
        self.assertEqual(rep2["summary"]["forecast_series_count"], 1)


class CoverageEmptyCityTest(_CoverageBase):
    """空城市：单独标记“未归一化城市”，全库报告提示其存在，不归入任何城市。"""

    def test_empty_city_labeled(self):
        self.seed([
            _srow("2026-03", city="杭州"),
            _srow("2026-03", city="", institution="未知机构", source_file="unknown.csv"),
            _srow("2026-04", city="", institution="未知机构", source_file="unknown.csv"),
        ])
        rep = self.cov()
        empty = [r for r in rep["series"] if r["city"] == ""]
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0]["city_label"], "未归一化城市")
        self.assertEqual(rep["summary"]["empty_city_series_count"], 1)
        self.assertTrue(any("未归一化城市" in w for w in rep["warnings"]))
        g = [g for g in rep["city_metric_summary"] if g["city"] == ""][0]
        self.assertIn("empty_city", g["status_flags"])
        rep_hz = self.cov(city="杭州")  # 城市筛选不混入空城市
        self.assertEqual(rep_hz["summary"]["empty_city_series_count"], 0)
        self.assertEqual(rep_hz["summary"]["series_count"], 1)


class CoverageSqlInjectionTest(_CoverageBase):
    """SQL 注入输入被参数化挡住，不泄露数据、不改库。"""

    def test_injection_attempts(self):
        self.seed([_srow("2026-03", city="杭州"), _srow("2026-03", city="北京")])
        hash_before = self.db_sha256()
        for atk in [{"city": "杭州' OR 1=1 --"},
                    {"institution": "贝壳' OR '1'='1"},
                    {"metric_name": "x'; DROP TABLE metrics;--"},
                    {"source_file": "' OR 1=1 --"}]:
            rep = self.cov(**atk)
            self.assertEqual(rep["series"], [], msg=str(atk))
            self.assertEqual(rep["summary"]["overall_status"], "no_observation", msg=str(atk))
        self.assertEqual(self.db_sha256(), hash_before)
        self.assertEqual(self.cov()["summary"]["series_count"], 2)  # 表仍在


class CoverageLegacySchemaTest(_CoverageBase):
    """旧 schema（无 source_file 列）：报告可运行、不迁移、不改库。"""

    schema = _SERIES_SCHEMA_LEGACY

    def test_legacy_schema_runs(self):
        self.seed([_srow("2026-03"), _srow("2026-04")])
        hash_before = self.db_sha256()
        rep = self.cov(city="杭州")
        self.assertEqual(rep["summary"]["series_count"], 1)
        self.assertIsNone(rep["series"][0]["source_file"])
        self.assertEqual(rep["series"][0]["status"], "multi_point_no_internal_gap")
        self.assertEqual(self.db_sha256(), hash_before)
        rep2 = self.cov(source_file="beike.csv")  # 旧 schema 无该列：空结果 + warning
        self.assertEqual(rep2["series"], [])
        self.assertTrue(any("source_file" in w for w in rep2["warnings"]))


class CoverageResourceTest(_CoverageBase):
    """只读连接：查询后连接关闭、文件可改名、内容零变化。"""

    def test_connection_closed_and_readonly(self):
        self.seed([_srow("2026-03")])
        hash_before = self.db_sha256()
        self.cov(city="杭州")
        self.assertEqual(self.db_sha256(), hash_before)
        renamed = self.db.with_name("renamed.db")  # 有未关闭句柄/锁时改名会失败
        self.db.rename(renamed)
        rep = mq.summarize_series_coverage(city="杭州", db_path=renamed)
        self.assertEqual(rep["summary"]["series_count"], 1)
        renamed.unlink()

    def test_underlying_connection_is_readonly(self):
        self.seed([_srow("2026-03")])
        conn = mq._connect_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO metrics (period) VALUES ('2026-01')")
        finally:
            conn.close()


class CoverageCliTest(_CoverageBase):
    """CLI：参数透传、项目外 cwd、报告路径保护、JSON 可序列化。"""

    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLI), "--db", str(self.db), *args],
            capture_output=True, text=True, cwd=cwd or str(self._tmp.name),
        )

    def setUp(self):
        super().setUp()
        self.seed([
            _srow("2026-03", value=1.0),
            _srow("2026-05", value=2.0),
            _srow("2026-04", value=3.0, is_forecast=1, source_quote="预计"),
        ])

    def test_cli_text_and_filters(self):
        p = self.run_cli("--city", "杭州", "--metric", "secondary_volume_units")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("数据覆盖与缺口报告", p.stdout)
        self.assertIn("secondary_volume_units", p.stdout)
        self.assertIn("仅预测", p.stdout)
        p = self.run_cli("--period-start", "2026-03", "--period-end", "2026-05",
                         "--actual-only")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("请求窗口", p.stdout)
        self.assertNotIn("仅预测(forecast_only)", p.stdout)  # actual-only 后无预测序列

    def test_cli_periods_list(self):
        p = self.run_cli("--periods", "2026-03,2026-05", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)  # JSON 可序列化
        self.assertEqual(rep["requested_periods"], ["2026-03", "2026-05"])
        self.assertTrue(rep["has_explicit_window"])

    def test_cli_works_outside_project_cwd(self):
        with tempfile.TemporaryDirectory() as outside:
            p = subprocess.run([sys.executable, str(CLI), "--db", str(self.db), "--json"],
                               capture_output=True, text=True, cwd=outside)
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(p.stdout)
        self.assertEqual(rep["summary"]["series_count"], 2)

    def test_cli_report_write_and_path_protection(self):
        target = Path(self._tmp.name) / "out" / "coverage.json"
        p = self.run_cli("--report", str(target))
        self.assertEqual(p.returncode, 0, p.stderr)
        rep = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(rep["summary"]["series_count"], 2)
        p = self.run_cli("--report", str(self.db))  # 不得覆盖数据库
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("数据库", p.stderr)
        p = self.run_cli("--report", str(APP / "metrics" / "x.json"))  # 不得写入 metrics/
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("metrics", p.stderr)
        self.assertFalse((APP / "metrics" / "x.json").exists())

    def test_cli_missing_db_rejected(self):
        p = subprocess.run([sys.executable, str(CLI), "--db",
                            str(Path(self._tmp.name) / "nope.db")],
                           capture_output=True, text=True, cwd=str(self._tmp.name))
        self.assertNotEqual(p.returncode, 0)


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
class CoverageFormalReadonlyTest(unittest.TestCase):
    """正式库只读验证：库函数与 CLI 均可运行，指纹与 metrics/ 聚合 hash 不变。"""

    def test_formal_summary_and_fingerprint_unchanged(self):
        db_fp_before = _file_fingerprint(_FORMAL_DB)
        metrics_hash_before = _metrics_dir_hash()

        rep = mq.summarize_series_coverage(city="杭州", db_path=_FORMAL_DB)
        for key in ("filters", "series", "city_metric_summary", "summary", "warnings"):
            self.assertIn(key, rep)
        self.assertGreater(rep["summary"]["series_count"], 0)
        for r in rep["series"]:
            self.assertEqual(r["city"], "杭州")
            self.assertIn(r["status"], (
                "full_coverage", "partial_coverage", "single_point", "forecast_only",
                "no_observation", "multi_point_no_internal_gap",
                "multi_point_with_internal_gaps"))
        p = subprocess.run([sys.executable, str(CLI), "--city", "杭州", "--json"],
                           capture_output=True, text=True, cwd="/tmp")
        self.assertEqual(p.returncode, 0, p.stderr)
        json.loads(p.stdout)

        self.assertEqual(_file_fingerprint(_FORMAL_DB), db_fp_before)
        self.assertEqual(_metrics_dir_hash(), metrics_hash_before)
        self.assertFalse((APP / "data" / "rag.db-wal").exists())
        self.assertFalse((APP / "data" / "rag.db-shm").exists())


if __name__ == "__main__":
    unittest.main()
