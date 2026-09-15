"""Step 2A 回归：source_file 幂等导入、阻断保护、显式迁移、CLI 退出码。

全部使用临时目录的数据库与夹具文件，不触碰 data/rag.db 与 metrics/ 真实数据。
"""
import json
import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_db  # noqa: E402
import load_external_metrics as lem  # noqa: E402

ALL_COLS = lem.BUSINESS_COLS + ("source_file",)


def make_row(**kw):
    r = {"institution": "测试机构", "period": "2026-08", "city": "杭州",
         "segment": "new_home", "metric_name": "avg_price", "value": 100.0,
         "unit": "CNY_per_sqm", "source_quote": "引文甲"}
    r.update(kw)
    return r


class LoadTestCase(unittest.TestCase):
    def setUp(self):
        self._real_main = lem.main
        def quiet_main(*args, **kwargs):
            with contextlib.redirect_stdout(io.StringIO()):
                return self._real_main(*args, **kwargs)
        lem.main = quiet_main
        self.addCleanup(lambda: setattr(lem, "main", self._real_main))
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "metrics"
        self.root.mkdir()
        self.db = base / "test.db"
        lib_db.connect(self.db).close()  # 新建库：SCHEMA 自带 source_file 列

    # ---------- 夹具与读取辅助 ----------
    def write_jsonl(self, name, rows, raw_text=None):
        p = self.root / name
        if raw_text is not None:
            p.write_text(raw_text, encoding="utf-8")
        else:
            p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                         encoding="utf-8")
        return p

    def write_csv(self, name, rows):
        p = self.root / name
        cols = ["institution", "period", "city", "segment", "metric_name", "value",
                "unit", "comparison_type", "comparison_value", "is_forecast",
                "source_url", "source_quote"]
        lines = [",".join(cols)]
        for r in rows:
            lines.append(",".join(str(r.get(c, "")) for c in cols))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def load(self, name):
        return lem.load(self.root / name, db_path=self.db, metrics_root=self.root)

    def rows(self):
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        out = [dict(r) for r in conn.execute("SELECT * FROM metrics ORDER BY metric_id")]
        conn.close()
        return out

    def contents(self):
        return sorted((tuple(r[c] for c in ALL_COLS) for r in self.rows()), key=repr)

    @staticmethod
    def db_snapshot(path):
        conn = sqlite3.connect(str(path))
        snap = (conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master"
                             " ORDER BY name").fetchall(),
                conn.execute("SELECT * FROM sqlite_sequence").fetchall()
                if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_sequence'"
                                ).fetchone()[0] else [],
                conn.execute("SELECT * FROM metrics ORDER BY metric_id").fetchall())
        conn.close()
        return snap

    # ---------- 幂等与替换语义 ----------
    def test_reimport_without_keys_no_growth(self):
        """无 doc_id/source_url 的文件重复导入不增长，且内容与条数不变。"""
        rows = [make_row(), make_row(period="2026-07", value=99.0)]
        self.write_jsonl("a.jsonl", rows)
        r1 = self.load("a.jsonl")
        self.assertEqual((r1["parsed"], r1["deleted"], r1["inserted"]), (2, 0, 2))
        before = self.contents()
        r2 = self.load("a.jsonl")
        self.assertEqual((r2["deleted"], r2["inserted"]), (2, 2))
        self.assertEqual(self.contents(), before)

    def test_file_update_add_remove_rows(self):
        """同一文件改值、增行、删行后再导入，库精确反映新文件。"""
        self.write_jsonl("a.jsonl", [make_row(), make_row(period="2026-07", value=99.0)])
        self.load("a.jsonl")
        self.write_jsonl("a.jsonl", [make_row(value=101.5),  # 改值
                                     make_row(period="2026-06", value=98.0)])  # 增删
        r = self.load("a.jsonl")
        self.assertEqual((r["deleted"], r["inserted"]), (2, 2))
        got = {(row["period"], row["value"]) for row in self.rows()}
        self.assertEqual(got, {("2026-08", 101.5), ("2026-06", 98.0)})

    def test_within_file_fold_and_cross_file_keep(self):
        """同文件完全重复折叠；跨文件相同内容各挂各 source_file 保留。"""
        dup = make_row()
        self.write_jsonl("a.jsonl", [dup, dict(dup)])
        r = self.load("a.jsonl")
        self.assertEqual((r["parsed"], r["folded"], r["inserted"]), (2, 1, 1))
        self.write_jsonl("b.jsonl", [dict(dup)])
        self.load("b.jsonl")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["source_file"] for r in rows}, {"a.jsonl", "b.jsonl"})
        self.assertEqual({r["value"] for r in rows}, {100.0})
        self.load("a.jsonl")  # 重复导入不产生第三行
        self.assertEqual(len(self.rows()), 2)

    def test_shared_doc_id_url_no_cross_delete(self):
        """两个文件共用 doc_id/source_url 时互不影响对方记录。"""
        a = make_row(doc_id="d1", source_url="http://x/1", source_quote="A引文")
        b = make_row(doc_id="d1", source_url="http://x/1", source_quote="B引文", value=200.0)
        self.write_jsonl("a.jsonl", [a])
        self.write_jsonl("b.jsonl", [b])
        self.load("a.jsonl")
        self.load("b.jsonl")
        self.assertEqual({r["source_quote"] for r in self.rows()}, {"A引文", "B引文"})
        self.load("b.jsonl")  # 重导 b 不动 a
        self.assertEqual(len(self.rows()), 2)

    def test_field_differences_preserved(self):
        """segment/unit/is_forecast/comparison/value_text/source_quote 差异全部保留。"""
        rows = [
            make_row(),
            make_row(segment="secondary_home"),
            make_row(unit="pct"),
            make_row(is_forecast=1),
            make_row(comparison_type="mom", comparison_value=0.5),
            make_row(value="2026Q4", unit="text"),  # 文本型 → value_text
            make_row(source_quote="引文乙"),
        ]
        self.write_jsonl("a.jsonl", rows)
        r = self.load("a.jsonl")
        self.assertEqual((r["folded"], r["inserted"]), (0, 7))
        got = self.rows()
        self.assertEqual(len(got), 7)
        self.assertEqual({g["segment"] for g in got}, {"new_home", "secondary_home"})
        self.assertEqual({g["unit"] for g in got}, {"CNY_per_sqm", "pct", "text"})
        self.assertEqual({g["is_forecast"] for g in got}, {0, 1})
        self.assertEqual({g["value_text"] for g in got}, {None, "2026Q4"})
        self.assertEqual({g["source_quote"] for g in got}, {"引文甲", "引文乙"})

    # ---------- 失败回滚 ----------
    def test_parse_failure_keeps_old_data(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.load("a.jsonl")
        self.write_jsonl("a.jsonl", [], raw_text='{"institution": "x"\nnot-json\n')
        with self.assertRaises(lem.LoadError) as cm:
            self.load("a.jsonl")
        self.assertIn("解析失败", str(cm.exception))
        self.assertEqual(len(self.rows()), 1)  # 旧数据完整保留

    def test_validation_failure_keeps_old_data(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.load("a.jsonl")
        self.write_jsonl("a.jsonl", [make_row(comparison_value="abc"), make_row()])
        with self.assertRaises(lem.LoadError) as cm:
            self.load("a.jsonl")
        self.assertIn("校验失败", str(cm.exception))
        self.assertIn("第1条记录", str(cm.exception))
        self.assertEqual(len(self.rows()), 1)

    def test_mid_transaction_write_failure_rolls_back(self):
        """删旧后插入中途失败 → 回滚，旧数据完整；连接可复用。"""
        self.write_jsonl("a.jsonl", [make_row(), make_row(period="2026-07")])
        self.load("a.jsonl")
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TRIGGER fail_ins BEFORE INSERT ON metrics"
                     " BEGIN SELECT RAISE(FAIL, 'boom'); END")
        conn.commit()
        conn.close()
        with self.assertRaises(lem.LoadError) as cm:
            self.load("a.jsonl")
        self.assertIn("已回滚", str(cm.exception))
        self.assertEqual(len(self.rows()), 2)  # DELETE 被回滚
        conn = sqlite3.connect(str(self.db))
        conn.execute("DROP TRIGGER fail_ins")
        conn.commit()
        conn.close()
        self.load("a.jsonl")  # 连接正常关闭，后续导入可用
        self.assertEqual(len(self.rows()), 2)

    # ---------- 阻断保护 ----------
    def test_old_schema_refused_and_db_unchanged(self):
        """旧 schema（无 source_file 列）拒绝导入；拒绝后 schema 与数据均不变。"""
        old = Path(self._tmp.name) / "old.db"
        conn = sqlite3.connect(str(old))
        conn.execute("CREATE TABLE metrics (metric_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,"
                     " metric_name TEXT, value REAL, unit TEXT, comparison_type TEXT,"
                     " comparison_value REAL, is_forecast INTEGER DEFAULT 0,"
                     " source_page TEXT, source_quote TEXT, source_url TEXT, value_text TEXT)")
        conn.execute("INSERT INTO metrics(institution, period, metric_name, value, unit,"
                     " source_quote) VALUES ('旧机构', '2026-01', 'avg_price', 1.0, 'x', 'q')")
        conn.commit()
        conn.close()  # 注意：旧库没有 docs/chunks 表，可验证拒绝路径不触发建表
        before = self.db_snapshot(old)
        self.write_jsonl("a.jsonl", [make_row()])
        with self.assertRaises(lem.LoadError) as cm:
            lem.load(self.root / "a.jsonl", db_path=old, metrics_root=self.root)
        self.assertIn("--migrate-source-file", str(cm.exception))
        self.assertEqual(self.db_snapshot(old), before)

    def test_unattributed_history_refused(self):
        """存在 source_file 为 NULL 或空串的历史行 → 阻断，且不追加新行。"""
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO metrics(institution, period, metric_name, value, unit,"
                     " source_quote, source_file) VALUES ('旧', '2026-01', 'm', 1.0, 'x', 'q', NULL)")
        conn.execute("INSERT INTO metrics(institution, period, metric_name, value, unit,"
                     " source_quote, source_file) VALUES ('旧', '2026-02', 'm', 2.0, 'x', 'q', '')")
        conn.commit()
        conn.close()
        before = self.db_snapshot(self.db)
        self.write_jsonl("a.jsonl", [make_row()])
        with self.assertRaises(lem.LoadError) as cm:
            self.load("a.jsonl")
        self.assertIn("Step 2B", str(cm.exception))
        self.assertEqual(self.db_snapshot(self.db), before)

    def test_missing_source_and_empty_file_refused(self):
        """库中来源文件盘上缺失 → 拒绝（不删旧行）；空文件 → 拒绝。"""
        self.write_jsonl("a.jsonl", [make_row()])
        self.load("a.jsonl")
        (self.root / "a.jsonl").unlink()
        self.write_jsonl("b.jsonl", [make_row(value=200.0)])
        with self.assertRaises(lem.LoadError) as cm:
            self.load("b.jsonl")
        self.assertIn("来源文件缺失", str(cm.exception))
        rows = self.rows()  # a 的记录还在，b 未导入
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_file"], "a.jsonl")
        self.write_jsonl("a.jsonl", [make_row()])  # 恢复来源
        self.write_jsonl("empty.jsonl", [], raw_text="")
        with self.assertRaises(lem.LoadError) as cm:
            self.load("empty.jsonl")
        self.assertIn("空文件", str(cm.exception))

    def test_outside_root_and_nonexistent_db_refused(self):
        """根目录外文件、不存在的数据库均拒绝；拒绝不创建数据库文件。"""
        stray = Path(self._tmp.name) / "stray.jsonl"
        stray.write_text(json.dumps(make_row()) + "\n", encoding="utf-8")
        with self.assertRaises(lem.LoadError) as cm:
            lem.load(stray, db_path=self.db, metrics_root=self.root)
        self.assertIn("不在 metrics 根目录内", str(cm.exception))
        ghost = Path(self._tmp.name) / "ghost.db"
        self.write_jsonl("a.jsonl", [make_row()])
        with self.assertRaises(lem.LoadError):
            lem.load(self.root / "a.jsonl", db_path=ghost, metrics_root=self.root)
        self.assertFalse(ghost.exists())

    # ---------- 校验规则 ----------
    def test_period_validation(self):
        valid = ["2026-01", "2026-12", "2026Q1", "2026Q4", "2026-H1", "2026-H2", "2026"]
        for i, p in enumerate(valid):
            self.write_jsonl(f"v{i}.jsonl", [make_row(period=p)])
            self.load(f"v{i}.jsonl")
        self.assertEqual(len(self.rows()), len(valid))
        for bad in ["2026-13", "2026-00", "2026Q5", "2026Q0", "2026-H3", "2026-8", "202608"]:
            self.write_jsonl("bad.jsonl", [make_row(period=bad)])
            with self.assertRaises(lem.LoadError, msg=bad):
                self.load("bad.jsonl")

    def test_value_and_forecast_validation(self):
        """NaN/Infinity 拒绝；is_forecast 限定 0/1；文本型 value 走 value_text。"""
        for bad in [make_row(value="NaN"), make_row(value=float("inf")),
                    make_row(comparison_value="Infinity", comparison_type="mom"),
                    make_row(is_forecast=2)]:
            self.write_jsonl("bad.jsonl", [bad])
            with self.assertRaises(lem.LoadError):
                self.load("bad.jsonl")
        self.write_jsonl("ok.jsonl", [make_row(value="2026Q4", unit="text", is_forecast="1")])
        self.load("ok.jsonl")
        row = self.rows()[0]
        self.assertIsNone(row["value"])
        self.assertEqual(row["value_text"], "2026Q4")
        self.assertEqual(row["is_forecast"], 1)

    # ---------- Review 修复：校验收窄 ----------
    def test_is_forecast_strict(self):
        """is_forecast 先验证再转换：0.9 等截断值拒绝；缺省/布尔/0.0/1.0/"0"/"1" 兼容。"""
        self.write_jsonl("a.jsonl", [make_row(is_forecast=1)])
        self.load("a.jsonl")
        for bad in [0.9, 1.9, -0.5, 2, "2", "yes", [1], float("nan")]:
            self.write_jsonl("a.jsonl", [make_row(is_forecast=bad)])
            with self.assertRaises(lem.LoadError, msg=repr(bad)):
                self.load("a.jsonl")
            rows = self.rows()  # 拒绝后旧数据完整
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["is_forecast"], 1)
        for good, want in [(None, 0), ("", 0), (0, 0), (1, 1), ("0", 0), ("1", 1),
                           (True, 1), (False, 0), (0.0, 0), (1.0, 1)]:
            self.write_jsonl("a.jsonl", [make_row(is_forecast=good)])
            self.load("a.jsonl")
            self.assertEqual(self.rows()[0]["is_forecast"], want, msg=repr(good))

    def test_value_type_restriction(self):
        """value 只接受数字/字符串：对象、数组、布尔、纯空白拒绝；文本值与 "2027"→数值保留。"""
        self.write_jsonl("a.jsonl", [make_row()])
        self.load("a.jsonl")
        for bad in [{"bad": 1}, [100], True, "   "]:
            self.write_jsonl("a.jsonl", [make_row(value=bad)])
            with self.assertRaises(lem.LoadError, msg=repr(bad)):
                self.load("a.jsonl")
            rows = self.rows()  # 拒绝后旧数据完整
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["value"], 100.0)
        self.write_jsonl("a.jsonl", [make_row(value="2027", unit="text")])
        self.load("a.jsonl")
        self.assertEqual(self.rows()[0]["value"], 2027.0)  # 既有行为："2027" 转数值
        self.write_jsonl("a.jsonl", [make_row(value="2026Q4", unit="text")])
        self.load("a.jsonl")
        row = self.rows()[0]
        self.assertIsNone(row["value"])
        self.assertEqual(row["value_text"], "2026Q4")

    def test_non_object_jsonl_record_batch_continues(self):
        """JSONL 顶层 null/数组/字符串 → 带位置的 LoadError；批次续跑、退出码非零、旧数据保留。"""
        self.write_jsonl("a.jsonl", [make_row()])
        self.load("a.jsonl")  # a 先有旧数据
        self.write_jsonl("a.jsonl", [], raw_text='null\n{"institution": "x"}\n')
        self.write_jsonl("b.jsonl", [make_row(period="2026-07")])
        with contextlib.redirect_stdout(io.StringIO()):
            rc = lem.main(["--db", str(self.db), "--metrics-root", str(self.root)])
        self.assertEqual(rc, 1)  # a 失败不中断批次
        got = {r["source_file"]: r for r in self.rows()}
        self.assertEqual(set(got), {"a.jsonl", "b.jsonl"})  # a 旧行保留，b 正常导入
        self.assertEqual(got["a.jsonl"]["value"], 100.0)
        self.assertEqual(got["b.jsonl"]["period"], "2026-07")
        with self.assertRaises(lem.LoadError) as cm:  # load() 直接调用同样受保护
            self.load("a.jsonl")
        self.assertIn("第1条记录", str(cm.exception))
        self.assertIn("null", str(cm.exception))
        for bad_text in ['[1, 2]\n', '"只是一段话"\n', '123\n']:
            self.write_jsonl("c.jsonl", [], raw_text=bad_text)
            with self.assertRaises(lem.LoadError, msg=bad_text.strip()):
                self.load("c.jsonl")

    def test_bad_encoding_file_refused_batch_continues(self):
        """非法 UTF-8 文件 → LoadError 含文件名与字节位置；批次续跑、退出码非零、旧数据保留。"""
        self.write_jsonl("a.jsonl", [make_row()])
        self.load("a.jsonl")
        (self.root / "a.jsonl").write_bytes(b'{"institution": "\xff\xfe\xbd}\n')  # 非法 UTF-8
        (self.root / "c.csv").write_bytes(b'institution,period\n\xff\xfe\n')
        self.write_jsonl("b.jsonl", [make_row(period="2026-07")])
        with self.assertRaises(lem.LoadError) as cm:
            self.load("a.jsonl")
        self.assertIn("a.jsonl", str(cm.exception))
        self.assertIn("UTF-8", str(cm.exception))
        with self.assertRaises(lem.LoadError) as cm:  # CSV 路径同样受保护
            self.load("c.csv")
        self.assertIn("c.csv", str(cm.exception))
        with contextlib.redirect_stdout(io.StringIO()):
            rc = lem.main(["--db", str(self.db), "--metrics-root", str(self.root)])
        self.assertEqual(rc, 1)  # a/c 失败不中断批次
        got = {r["source_file"]: r for r in self.rows()}
        self.assertEqual(set(got), {"a.jsonl", "b.jsonl"})  # a 旧行保留，b 正常导入
        self.assertEqual(got["a.jsonl"]["value"], 100.0)

    # ---------- 迁移 ----------
    def test_explicit_migrate_idempotent_and_no_import(self):
        """显式迁移幂等、只加列；迁移后历史 NULL 行仍阻断导入（不自动归属）。"""
        old = Path(self._tmp.name) / "old.db"
        conn = sqlite3.connect(str(old))
        conn.execute("CREATE TABLE metrics (metric_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,"
                     " metric_name TEXT, value REAL, unit TEXT, comparison_type TEXT,"
                     " comparison_value REAL, is_forecast INTEGER DEFAULT 0,"
                     " source_page TEXT, source_quote TEXT, source_url TEXT, value_text TEXT)")
        conn.execute("INSERT INTO metrics(institution, period, metric_name, value, unit,"
                     " source_quote) VALUES ('旧机构', '2026-01', 'avg_price', 1.0, 'x', 'q')")
        conn.commit()
        conn.close()
        conn = sqlite3.connect(str(old))  # 函数级入口：第一次 True，第二次 False
        self.assertTrue(lib_db.migrate_add_source_file(conn))
        conn.commit()
        self.assertFalse(lib_db.migrate_add_source_file(conn))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
        row = conn.execute("SELECT institution, source_file FROM metrics").fetchall()
        conn.close()
        self.assertIn("source_file", cols)
        self.assertEqual(row, [("旧机构", None)])  # 数据未被归属/改动
        self.write_jsonl("a.jsonl", [make_row()])
        with self.assertRaises(lem.LoadError):
            lem.load(self.root / "a.jsonl", db_path=old, metrics_root=self.root)
        conn = sqlite3.connect(str(old))  # 且未发生任何导入
        n = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    def test_fresh_db_schema_repeatable(self):
        """lib_db.connect 重复执行安全；新库自带 source_file 列。"""
        lib_db.connect(self.db).close()
        conn = sqlite3.connect(str(self.db))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
        finally:
            conn.close()
        self.assertIn("source_file", cols)

    # ---------- 旧格式兼容 ----------
    def test_csv_compat(self):
        """旧 CSV 格式（无 doc_id 列、数字为字符串）正常导入且幂等。"""
        self.write_csv("ext.csv", [
            {"institution": "贝壳", "period": "2026-03", "city": "杭州",
             "segment": "secondary_home", "metric_name": "secondary_volume_units",
             "value": "9356", "unit": "units", "is_forecast": "0",
             "source_url": "http://x/1", "source_quote": "成交9356套"},
            {"institution": "贝壳", "period": "2026-04", "city": "杭州",
             "segment": "secondary_home", "metric_name": "secondary_volume_units",
             "value": "9968", "unit": "units", "comparison_type": "mom",
             "comparison_value": "6.5", "is_forecast": "0",
             "source_url": "http://x/1", "source_quote": "成交9968套"},
        ])
        r = self.load("ext.csv")
        self.assertEqual(r["inserted"], 2)
        rows = self.rows()
        self.assertEqual(rows[0]["value"], 9356.0)
        self.assertIsNone(rows[0]["doc_id"])
        self.assertEqual(rows[1]["comparison_value"], 6.5)
        self.assertEqual(rows[0]["source_url"], "http://x/1")  # 只作数据，不作删除键
        self.load("ext.csv")
        self.assertEqual(len(self.rows()), 2)

    # ---------- CLI 路径 ----------
    def test_cli_end_to_end_and_exit_codes(self):
        """CLI 真实参数解析：目录扫描、幂等、单文件失败续跑、退出码。"""
        self.write_jsonl("a.jsonl", [make_row()])
        self.write_jsonl("b.jsonl", [make_row(period="2026-07")])
        with contextlib.redirect_stdout(io.StringIO()):
            rc = lem.main(["--db", str(self.db), "--metrics-root", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(lem.main(["--db", str(self.db), "--metrics-root", str(self.root)]), 0)
        self.assertEqual(len(self.rows()), 2)  # 重复运行不增长
        self.write_jsonl("b.jsonl", [make_row(period="2026-13")])  # b 变非法
        rc = lem.main(["--db", str(self.db), "--metrics-root", str(self.root)])
        self.assertEqual(rc, 1)  # a 仍完成，b 失败，退出码非零
        self.assertEqual({r["source_file"] for r in self.rows()}, {"a.jsonl", "b.jsonl"})
        self.assertEqual(len(self.rows()), 2)

    def test_cli_blocked_and_migrate(self):
        """CLI 在旧库上拒绝且不改动；--migrate-source-file 只迁移不导入。"""
        old = Path(self._tmp.name) / "old.db"
        conn = sqlite3.connect(str(old))
        conn.execute("CREATE TABLE metrics (metric_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,"
                     " metric_name TEXT, value REAL, unit TEXT, comparison_type TEXT,"
                     " comparison_value REAL, is_forecast INTEGER DEFAULT 0,"
                     " source_page TEXT, source_quote TEXT, source_url TEXT, value_text TEXT)")
        conn.execute("INSERT INTO metrics(institution, period, metric_name, value, unit,"
                     " source_quote) VALUES ('旧机构', '2026-01', 'm', 1.0, 'x', 'q')")
        conn.commit()
        conn.close()
        self.write_jsonl("a.jsonl", [make_row()])
        before = self.db_snapshot(old)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = lem.main(["--db", str(old), "--metrics-root", str(self.root)])
        self.assertEqual(rc, 1)
        self.assertEqual(self.db_snapshot(old), before)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = lem.main(["--db", str(old), "--migrate-source-file"])
        self.assertEqual(rc, 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lem.main(["--db", str(old), "--migrate-source-file"]), 0)  # 幂等
        conn = sqlite3.connect(str(old))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
        n = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
        self.assertIn("source_file", cols)
        self.assertEqual(n, 1)  # 迁移未连带导入
        with contextlib.redirect_stdout(io.StringIO()):
            rc = lem.main(["--db", str(old), "--metrics-root", str(self.root)])
        self.assertEqual(rc, 1)  # 迁移后历史 NULL 行仍阻断


if __name__ == "__main__":
    unittest.main()
