"""Step 2B-1 / 2B-2A 回归：只读预览工具 + 临时库执行骨架。

全部使用临时目录的数据库与夹具，不触碰 data/rag.db 与 metrics/ 真实数据
（正式库仅在保护性测试中作为"必须被拒绝的路径"出现，只读断言其指纹不变）。
"""
import hashlib
import contextlib
import io
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_db  # noqa: E402
import lib_paths  # noqa: E402
import load_external_metrics as lem  # noqa: E402
import metrics_maintenance as mm  # noqa: E402


def make_row(**kw):
    r = {"institution": "测试机构", "period": "2026-08", "city": "杭州",
         "segment": "new_home", "metric_name": "avg_price", "value": 100.0,
         "unit": "CNY_per_sqm", "source_quote": "引文甲"}
    r.update(kw)
    return r


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "metrics"
        self.root.mkdir()
        self.db = base / "test.db"
        lib_db.connect(self.db).close()  # 新 schema（含 source_file 列）

    # ---------- 夹具与辅助 ----------
    def write_jsonl(self, name, rows, raw_text=None):
        p = self.root / name
        if raw_text is not None:
            p.write_text(raw_text, encoding="utf-8")
        else:
            p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                         encoding="utf-8")
        return p

    def insert_converted(self, fixture_rows, source_file=None):
        conn = sqlite3.connect(str(self.db))
        cols = list(lem.BUSINESS_COLS) + (["source_file"] if source_file is not None else [])
        sql = f"INSERT INTO metrics({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        for fr in fixture_rows:
            c = lem.convert_row(fr)
            vals = [c[k] for k in lem.BUSINESS_COLS] + ([source_file] if source_file is not None else [])
            conn.execute(sql, vals)
        conn.commit()
        conn.close()

    def insert_raw(self, dicts):
        conn = sqlite3.connect(str(self.db))
        cols = list(lem.BUSINESS_COLS)
        sql = f"INSERT INTO metrics({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        for d in dicts:
            conn.execute(sql, [d.get(c) for c in cols])
        conn.commit()
        conn.close()

    def db_sha256(self):
        import hashlib
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def report(self):
        return mm.build_report(self.db, self.root)

    # ---------- 只读保护 ----------
    def test_ro_connection_cannot_write(self):
        conn = mm.connect_ro(self.db)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("INSERT INTO metrics(institution) VALUES ('x')")
        conn.close()

    def test_db_file_unchanged_after_preview(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert_converted([make_row()])
        before = self.db_sha256()
        self.report()
        with contextlib.redirect_stdout(io.StringIO()):
            mm.main(["preview", "--db", str(self.db), "--metrics-root", str(self.root)])
        self.assertEqual(self.db_sha256(), before)

    def test_cli_readonly_no_write_paths(self):
        """CLI 子命令全部只读；main() 不引用任何执行/备份/恢复/预检函数与写 SQL。"""
        self.assertEqual(set(mm.READONLY_SUBCOMMANDS),
                         {"preview", "plan", "fingerprint", "inspect-source", "inspect-db"})
        for bad in ("execute", "backup", "formal-backup", "backup-formal",
                    "migrate", "backfill", "delete", "preflight"):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    mm.main([bad, "--db", str(self.db), "--metrics-root", str(self.root)])
        src = inspect.getsource(mm.main)
        for kw in ("execute_plan", "backup_database", "restore_database", "formal_backup",
                   "run_preflight", "INSERT INTO", "DELETE FROM", "ALTER TABLE",
                   "UPDATE metrics"):
            self.assertNotIn(kw, src)

    # ---------- 匹配分类 ----------
    def test_unique_match(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert_converted([make_row()])
        r = self.report()
        self.assertEqual(r["matching"]["unique_match_contents"], 1)
        self.assertEqual(r["accounting"]["unique_match_rows"][0], 1)
        self.assertEqual(r["accounting"]["db_rows_unmatched"][0], 0)

    def test_cross_file_keeps_both_sources(self):
        """同内容出现在两个文件：两个来源都保留，不合并；理论保留 2 条而非 1 条。"""
        self.write_jsonl("a.jsonl", [make_row()])
        self.write_jsonl("b.jsonl", [dict(make_row())])
        self.insert_converted([make_row(), make_row()])
        r = self.report()
        self.assertEqual(len(r["cross_file_same_content"]), 1)
        group = r["cross_file_same_content"][0]
        self.assertEqual(set(group["source_files"]), {"a.jsonl", "b.jsonl"})
        self.assertEqual(group["db_rows"], 2)
        self.assertIn("独立证据", group["note"])
        self.assertEqual(r["matching"]["ambiguous_match_contents"], 1)
        self.assertEqual(r["accounting"]["theoretical_keep"][0], 2)  # 不合并不只留 1 条
        self.assertEqual(r["accounting"]["theoretical_delete"][0], 0)

    def test_within_file_duplicate(self):
        self.write_jsonl("a.jsonl", [make_row(), dict(make_row())])  # 文件内重复
        self.insert_converted([make_row(), make_row(), make_row()])  # DB 3 行同内容
        r = self.report()
        groups = r["within_file_duplicate"]
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g["source_file"], "a.jsonl")
        self.assertEqual(g["linenos"], [1, 2])
        self.assertEqual(g["dup_count_in_file"], 2)
        self.assertEqual(g["db_rows"], 3)
        self.assertEqual(g["future_keep"], 1)
        self.assertEqual(g["future_deletable"], 2)

    def test_field_differences_not_merged(self):
        """quote/segment/unit/comparison_value/is_forecast/value_text 差异均不合并。"""
        variants = [
            make_row(),
            make_row(source_quote="引文乙"),
            make_row(segment="secondary_home"),
            make_row(unit="pct"),
            make_row(comparison_type="mom", comparison_value=0.5),
            make_row(is_forecast=1),
            make_row(value="2026Q4", unit="text"),  # value→value_text 不混同
        ]
        self.write_jsonl("a.jsonl", variants)
        self.insert_converted(variants)
        r = self.report()
        self.assertEqual(r["accounting"]["exact_dup_groups"][0], 0)
        self.assertEqual(r["accounting"]["db_distinct_contents"][0], 7)
        self.assertEqual(r["matching"]["unique_match_contents"], 7)

    def test_null_vs_empty_string_distinct(self):
        """NULL 与空字符串是不同的内容。"""
        base = lem.convert_row(make_row())
        d1 = dict(base); d1["doc_id"] = None
        d2 = dict(base); d2["doc_id"] = ""
        self.insert_raw([d1, d2])
        r = self.report()
        self.assertEqual(r["accounting"]["exact_dup_groups"][0], 0)
        self.assertEqual(r["accounting"]["db_distinct_contents"][0], 2)

    def test_unmatched_db_row_reported_only(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert_converted([make_row()])           # 匹配
        self.insert_converted([make_row(city="北京")])  # 无源文件对应
        before = self.db_sha256()
        r = self.report()
        self.assertEqual(r["matching"]["unmatched_db_contents"], 1)
        self.assertEqual(r["accounting"]["db_rows_unmatched"][0], 1)
        self.assertEqual(r["unmatched_db_rows"][0]["count"], 1)
        self.assertEqual(self.db_sha256(), before)  # 只报告不删除

    def test_unmatched_source_row_reported(self):
        self.write_jsonl("a.jsonl", [make_row(), make_row(period="2026-07")])
        self.insert_converted([make_row()])
        r = self.report()
        self.assertEqual(r["accounting"]["source_rows_unmatched"][0], 1)
        self.assertEqual(r["unmatched_source_rows"][0]["linenos"], [2])

    # ---------- 源文件异常 ----------
    def test_source_parse_error_reported_db_untouched(self):
        self.write_jsonl("good.jsonl", [make_row()])
        self.write_jsonl("bad.jsonl", [], raw_text='{"a": 1}\nnot-json\n')
        self.insert_converted([make_row()])
        before = self.db_sha256()
        r = self.report()
        self.assertIn("bad.jsonl", r["matching"]["source_file_errors"])
        self.assertEqual(r["matching"]["unique_match_contents"], 1)  # 好文件正常匹配
        self.assertEqual(self.db_sha256(), before)

    def test_bad_encoding_reported_db_untouched(self):
        self.write_jsonl("good.jsonl", [make_row()])
        (self.root / "bad.jsonl").write_bytes(b'{"x": "\xff\xfe"}\n')
        self.insert_converted([make_row()])
        before = self.db_sha256()
        r = self.report()
        self.assertIn("bad.jsonl", r["matching"]["source_file_errors"])
        self.assertEqual(self.db_sha256(), before)

    # ---------- 指纹与报告 ----------
    def test_report_contains_fingerprints(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert_converted([make_row()])
        r = self.report()
        self.assertEqual(r["db_file_fingerprint"]["sha256"], self.db_sha256())
        self.assertIn("aggregate_sha256", r["source_fingerprint"])
        self.assertEqual(r["table_stats"]["metrics"], 1)
        self.assertTrue(r["table_stats"]["metrics_has_source_file_column"])
        self.assertIn("只读预览", r["disclaimer"])
        self.assertIn("免责声明", mm.render_preview_summary(r))

    def test_old_schema_db_preview(self):
        """无 source_file 列的旧库：如实报告列不存在，不报错不修改。"""
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
        self.write_jsonl("a.jsonl", [make_row()])
        before = mm.db_file_fingerprint(old)["sha256"]
        r = mm.build_report(old, self.root)
        self.assertFalse(r["table_stats"]["metrics_has_source_file_column"])
        self.assertIn("不存在", r["matching"]["existing_source_file"]["status"])
        self.assertEqual(r["accounting"]["db_total_rows"][0], 1)
        self.assertEqual(mm.db_file_fingerprint(old)["sha256"], before)

    def test_existing_source_file_categories(self):
        """已有 source_file 的行：分别报告 匹配/不匹配/为空/文件缺失。"""
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert_converted([make_row()], source_file="a.jsonl")          # match
        self.insert_converted([make_row(city="北京")], source_file="a.jsonl")  # mismatch
        self.insert_converted([make_row(city="上海")])                        # empty(NULL)
        self.insert_converted([make_row(city="深圳")], source_file="ghost.jsonl")  # missing
        r = self.report()["matching"]["existing_source_file"]
        self.assertEqual(r["sf_match"], 1)
        self.assertEqual(r["sf_mismatch"], 1)
        self.assertEqual(r["sf_empty"], 1)
        self.assertEqual(r["sf_missing_file"], 1)

    # ---------- CLI ----------
    def test_cli_from_any_cwd(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert_converted([make_row()])
        r = subprocess.run(
            [sys.executable, str(APP / "scripts" / "metrics_maintenance.py"),
             "preview", "--db", str(self.db), "--metrics-root", str(self.root)],
            cwd=self._tmp.name, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("只读预览", r.stdout)

    def test_report_write_guard(self):
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert_converted([make_row()])
        with self.assertRaises(SystemExit):  # 不许覆盖数据库
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                mm.main(["preview", "--db", str(self.db), "--metrics-root", str(self.root),
                         "--report", str(self.db)])
        with self.assertRaises(SystemExit):  # 不许写进源文件目录
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                mm.main(["preview", "--db", str(self.db), "--metrics-root", str(self.root),
                         "--report", str(self.root / "r.json")])
        out = Path(self._tmp.name) / "out" / "report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = mm.main(["preview", "--db", str(self.db), "--metrics-root", str(self.root),
                          "--report", str(out)])
        self.assertEqual(rc, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["accounting"]["db_total_rows"][0], 1)


class ExecuteTestCase(unittest.TestCase):
    """Step 2B-2A：临时库上的计划生成、执行、阻断、回滚、指纹绑定。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "metrics"
        self.root.mkdir()
        self.db = base / "test.db"

    # ---------- 夹具 ----------
    def write_jsonl(self, name, rows):
        (self.root / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    def make_old_db(self, with_docs=False):
        """旧 schema（无 source_file 列）。"""
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE metrics (metric_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,"
                     " metric_name TEXT, value REAL, unit TEXT, comparison_type TEXT,"
                     " comparison_value REAL, is_forecast INTEGER DEFAULT 0,"
                     " source_page TEXT, source_quote TEXT, source_url TEXT, value_text TEXT)")
        if with_docs:
            conn.execute("CREATE TABLE docs (doc_id TEXT PRIMARY KEY, title TEXT)")
            conn.execute("INSERT INTO docs VALUES ('d1', '研报一')")
            conn.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER,"
                         " heading TEXT, text TEXT, text_seg TEXT, is_table INTEGER DEFAULT 0)")
            conn.execute("INSERT INTO chunks VALUES ('d1-0001', 'd1', 1, '标题', '正文', '正文', 0)")
            conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(text_seg, content='chunks',"
                         " content_rowid='rowid')")
            conn.execute("INSERT INTO chunks_fts(rowid) SELECT rowid FROM chunks")
            try:  # vec 表尽力创建（扩展缺失不影响其余断言）
                import sqlite_vec
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                conn.execute("CREATE VIRTUAL TABLE chunks_vec USING vec0("
                             "chunk_id TEXT PRIMARY KEY, embedding float[4])")
                conn.execute("INSERT INTO chunks_vec(chunk_id, embedding) VALUES ('d1-0001', ?)",
                             (sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4]),))
            except Exception:
                pass
        conn.commit()
        conn.close()

    def make_new_db(self):
        lib_db.connect(self.db).close()  # 新 schema（含 source_file 列）

    def insert(self, rows, source_file=None):
        """按 Step 2A 转换后的 15 字段插入；source_file=None 表示不写该列。"""
        conn = sqlite3.connect(str(self.db))
        cols = list(lem.BUSINESS_COLS) + (["source_file"] if source_file is not None else [])
        sql = f"INSERT INTO metrics({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        for fr in rows:
            c = lem.convert_row(fr)
            vals = [c[k] for k in lem.BUSINESS_COLS] + ([source_file] if source_file is not None else [])
            conn.execute(sql, vals)
        conn.commit()
        conn.close()

    def metrics_rows(self):
        conn = sqlite3.connect(str(self.db))
        conn.row_factory = sqlite3.Row
        out = [dict(r) for r in conn.execute("SELECT * FROM metrics ORDER BY metric_id")]
        conn.close()
        return out

    def snapshot(self):
        """逻辑快照：schema + 每张普通表的全部行（证明回滚完整）。

        vec 虚拟表需要加载 sqlite_vec 扩展才能读取；读不出的表如实标记。"""
        conn = sqlite3.connect(str(self.db))
        mm._try_load_vec(conn)
        snap = {"schema": conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()}
        for (t,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            if not t.startswith("sqlite_"):
                try:
                    snap[t] = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                except sqlite3.Error:
                    snap[t] = "unreadable"
        conn.close()
        return snap

    def plan(self):
        return mm.build_plan(self.db, self.root)

    def execute(self, plan=None, **kw):
        return mm.execute_plan(self.db, self.root, plan if plan is not None else self.plan(), **kw)

    def db_sha256(self):
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    # ---------- 迁移层（场景 1） ----------
    def test_migrate_only_adds_column(self):
        """ensure_source_file_column：只加列、不归属、不删除、幂等。"""
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])
        conn = sqlite3.connect(str(self.db))
        self.assertTrue(mm.ensure_source_file_column(conn))
        conn.commit()
        self.assertFalse(mm.ensure_source_file_column(conn))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
        rows = conn.execute("SELECT source_file FROM metrics").fetchall()
        conn.close()
        self.assertIn("source_file", cols)
        self.assertEqual(rows, [(None,)])  # 不自动归属
        self.assertEqual(len(self.metrics_rows()), 1)  # 不自动删除

    # ---------- 成功执行（场景 1/2/3/4/17） ----------
    def test_old_schema_full_execution(self):
        """旧 schema 单事务：迁移+回填+同文件去重+跨文件各保留+其他表不变。"""
        self.make_old_db(with_docs=True)
        x = make_row()
        y = make_row(period="2026-07")
        self.write_jsonl("a.jsonl", [x, y])
        self.write_jsonl("b.jsonl", [dict(x)])
        self.insert([x, x, x, y, y])  # X×3（a、b 各留 1）+ Y×2（仅 a 留 1）
        plan = self.plan()
        self.assertTrue(plan["allowed_to_execute"])
        self.assertEqual(plan["targets"]["keep"], 3)
        self.assertEqual(plan["targets"]["delete"], 2)
        self.assertEqual(plan["targets"]["technical_assignments"], 3)
        self.assertEqual(plan["targets"]["cross_file_groups_retained"], 1)
        self.assertIn("独立证据", plan["disclaimer"])
        self.assertTrue(all("same_file_duplicate" in d["reason"] for d in plan["deletes"]))
        assigns = [a for a in plan["assignments"] if a["action"] == "assign"]
        self.assertTrue(all("技术分配" in a["note"] for a in assigns))
        self.assertTrue(all(a["source_linenos"] for a in assigns))  # 对应源文件行号
        rep = self.execute(plan, confirmed=True)
        rows = self.metrics_rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["source_file"] for r in rows))
        xs = [r for r in rows if r["period"] == "2026-08"]
        self.assertEqual({r["source_file"] for r in xs}, {"a.jsonl", "b.jsonl"})  # 不合并
        self.assertEqual([r["metric_id"] for r in xs], sorted(r["metric_id"] for r in xs))
        self.assertEqual([r["source_file"] for r in rows if r["period"] == "2026-07"],
                         ["a.jsonl"])
        self.assertEqual(rep["after"]["metrics_rows"], 3)
        self.assertEqual(rep["after"]["distinct_source_file_content_combos"], 3)
        self.assertEqual(rep["after"]["source_file_nonempty"], 3)
        self.assertTrue(rep["after"]["cross_file_retention_verified"])
        self.assertTrue(all(rep["other_tables_unchanged"].values()))  # docs/chunks/fts(/vec)

    def test_unique_source_execution(self):
        """唯一来源内容：执行后 source_file 正确，行数不增不减。"""
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])
        self.execute(confirmed=True)
        rows = self.metrics_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_file"], "a.jsonl")

    # ---------- 字段差异保护（场景 5） ----------
    def test_field_differences_not_merged_or_deleted(self):
        """15 字段任一差异都不合并、不删除（含 value/value_text、doc_id NULL vs 值）。"""
        self.make_old_db()
        variants = [
            make_row(),
            make_row(source_quote="引文乙"),
            make_row(segment="secondary_home"),
            make_row(unit="pct"),
            make_row(comparison_type="mom", comparison_value=0.5),
            make_row(is_forecast=1),
            make_row(value="2026Q4", unit="text"),
            make_row(doc_id="d1"),           # doc_id 有值 vs NULL 不同
            make_row(source_url="http://x/1"),
        ]
        self.write_jsonl("a.jsonl", variants)
        self.insert(variants)
        plan = self.plan()
        self.assertTrue(plan["allowed_to_execute"])
        self.assertEqual(plan["targets"]["delete"], 0)
        self.assertEqual(plan["targets"]["keep"], 9)
        self.execute(plan, confirmed=True)
        self.assertEqual(len(self.metrics_rows()), 9)

    def test_null_vs_empty_in_db_blocks_and_preserves(self):
        """库里原始 NULL 与转换后的空串不同：不匹配即阻断，两行都保留。"""
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])  # institution 转换为 ''（空串）
        conn = sqlite3.connect(str(self.db))  # 原始插入 institution=NULL：内容不同
        conn.execute("INSERT INTO metrics(institution, period, city, segment, metric_name,"
                     " value, unit, source_quote) VALUES (NULL, '2026-08', '杭州', 'new_home',"
                     " 'avg_price', 1.0, 'x', 'q')")
        conn.commit()
        conn.close()
        plan = self.plan()
        self.assertFalse(plan["allowed_to_execute"])
        self.assertTrue(any("db_unmatched" in b for b in plan["blockers"]))
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)
        self.assertEqual(len(self.metrics_rows()), 2)  # 未合并、未删除

    # ---------- 物理行不足（场景 6） ----------
    def test_insufficient_rows_blocked(self):
        self.make_old_db()
        x = make_row()
        self.write_jsonl("a.jsonl", [dict(x)])
        self.write_jsonl("b.jsonl", [dict(x)])
        self.insert([x])  # 1 行 vs 2 个来源文件
        plan = self.plan()
        self.assertFalse(plan["allowed_to_execute"])
        self.assertTrue(any("insufficient_rows" in b for b in plan["blockers"]))
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)
        self.assertEqual(self.snapshot(), before)

    # ---------- 未匹配（场景 7/8） ----------
    def test_db_unmatched_blocked_and_preserved(self):
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row(), make_row(city="北京")])  # 北京行无源文件对应
        plan = self.plan()
        self.assertFalse(plan["allowed_to_execute"])
        self.assertTrue(any("db_unmatched" in b for b in plan["blockers"]))
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)
        self.assertEqual(self.snapshot(), before)  # 该行原样保留

    def test_source_unmatched_blocked(self):
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row(), make_row(period="2026-07")])
        self.insert([make_row(), make_row()])  # 库里没有 2026-07 那条
        plan = self.plan()
        self.assertFalse(plan["allowed_to_execute"])
        self.assertTrue(any("source_unmatched" in b for b in plan["blockers"]))
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)
        self.assertEqual(len(self.metrics_rows()), 2)  # 不执行任何清理

    # ---------- 已有归属（场景 9/10/11） ----------
    def test_existing_sf_consistent_preserved(self):
        """已有归属与内容一致：保留原归属，不被排序式技术分配改写。"""
        self.make_new_db()
        x = make_row()
        self.write_jsonl("a.jsonl", [dict(x)])
        self.write_jsonl("b.jsonl", [dict(x)])
        self.insert([x], source_file="b.jsonl")  # id1 已归属 b（非排序首选 a）
        self.insert([dict(x)], source_file="")   # id2 未归属
        plan = self.plan()
        self.assertTrue(plan["allowed_to_execute"])
        got = {a["metric_id"]: a["source_file"] for a in plan["assignments"]}
        self.assertEqual(got, {1: "b.jsonl", 2: "a.jsonl"})  # 不重排 id1
        self.assertEqual(sum(1 for a in plan["assignments"] if a["action"] == "keep_existing"), 1)
        self.execute(plan, confirmed=True)
        self.assertEqual({r["metric_id"]: r["source_file"] for r in self.metrics_rows()},
                         {1: "b.jsonl", 2: "a.jsonl"})

    def test_existing_sf_inconsistent_blocked(self):
        self.make_new_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.write_jsonl("b.jsonl", [make_row(value=200.0)])
        self.insert([make_row()], source_file="b.jsonl")  # 内容在 a、归属在 b
        plan = self.plan()
        self.assertFalse(plan["allowed_to_execute"])
        self.assertTrue(any("sf_inconsistent" in b for b in plan["blockers"]))
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)
        self.assertEqual(self.snapshot(), before)  # 不静默覆盖

    def test_existing_sf_missing_file_blocked(self):
        self.make_new_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()], source_file="ghost.jsonl")
        plan = self.plan()
        self.assertFalse(plan["allowed_to_execute"])
        self.assertTrue(any("ghost.jsonl" in b for b in plan["blockers"]))
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)
        self.assertEqual(self.metrics_rows()[0]["source_file"], "ghost.jsonl")  # 不覆盖

    # ---------- 指纹绑定（场景 12/13） ----------
    def test_db_fingerprint_change_refuses(self):
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])
        plan = self.plan()
        conn = sqlite3.connect(str(self.db))  # 计划生成后修改数据库
        conn.execute("UPDATE metrics SET value=999.0 WHERE metric_id=1")
        conn.commit()
        conn.close()
        with self.assertRaises(mm.ExecuteRefused) as cm:
            self.execute(plan, confirmed=True)
        self.assertIn("状态指纹", str(cm.exception))
        self.assertEqual(self.metrics_rows()[0]["value"], 999.0)  # 拒绝时零写入

    def test_source_fingerprint_change_refuses(self):
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])
        plan = self.plan()
        self.write_jsonl("a.jsonl", [make_row(value=101.0)])  # 计划生成后修改源文件
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteRefused) as cm:
            self.execute(plan, confirmed=True)
        self.assertIn("源文件", str(cm.exception))
        self.assertEqual(self.snapshot(), before)

    # ---------- 中途失败回滚（场景 14） ----------
    def test_update_failure_rolls_back_migration_too(self):
        """旧 schema：UPDATE 中途失败 → ALTER/UPDATE/DELETE 全部回滚。"""
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row(), make_row(period="2026-07")])
        self.insert([make_row(), make_row(period="2026-07")])
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TRIGGER fail_upd BEFORE UPDATE ON metrics"
                     " BEGIN SELECT RAISE(FAIL, 'boom'); END")
        conn.commit()
        conn.close()
        plan = self.plan()  # 计划在触发器就位后生成，指纹含触发器不影响匹配
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteError) as cm:
            self.execute(plan, confirmed=True)
        self.assertIn("已回滚", str(cm.exception))
        self.assertEqual(self.snapshot(), before)  # source_file 列不存在（ALTER 已回滚）
        conn = sqlite3.connect(str(self.db))
        conn.execute("DROP TRIGGER fail_upd")
        conn.commit()
        conn.close()
        self.execute(self.plan(), confirmed=True)  # 重新生成计划后成功
        self.assertEqual(len(self.metrics_rows()), 2)
        self.assertTrue(all(r["source_file"] for r in self.metrics_rows()))

    def test_delete_failure_rolls_back_updates(self):
        """新 schema：DELETE 失败 → 已完成的 source_file UPDATE 一并回滚。"""
        self.make_new_db()
        x = make_row()
        self.write_jsonl("a.jsonl", [x])
        self.insert([x], source_file="")
        self.insert([dict(x)], source_file="")  # 同文件重复，需删 1 条
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TRIGGER fail_del BEFORE DELETE ON metrics"
                     " BEGIN SELECT RAISE(FAIL, 'boom'); END")
        conn.commit()
        conn.close()
        plan = self.plan()
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteError):
            self.execute(plan, confirmed=True)
        self.assertEqual(self.snapshot(), before)  # UPDATE 的 source_file 也被回滚

    # ---------- 重复执行语义（场景 15） ----------
    def test_repeat_execution_semantics(self):
        """执行后旧计划失效（指纹拒绝）；重新生成的计划为 no-op，结果稳定。"""
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row(), make_row()])
        plan = self.plan()
        self.assertEqual(plan["targets"]["delete"], 1)
        self.execute(plan, confirmed=True)
        state1 = self.snapshot()
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)  # 旧计划不可重放
        self.assertEqual(self.snapshot(), state1)
        for _ in range(2):  # 重新生成的计划为 no-op，可重复执行且状态不变
            plan2 = self.plan()
            self.assertEqual(plan2["targets"]["delete"], 0)
            self.assertEqual(plan2["targets"]["technical_assignments"], 0)
            self.assertEqual(plan2["targets"]["keep_existing"], 1)
            self.execute(plan2, confirmed=True)
            self.assertEqual(self.snapshot(), state1)

    # ---------- 执行保护（场景 18 前置） ----------
    def test_execute_requires_confirmed_flag(self):
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])
        plan = self.plan()
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteRefused) as cm:
            self.execute(plan)  # 默认 confirmed=False
        self.assertIn("confirmed=True", str(cm.exception))
        self.assertEqual(self.snapshot(), before)

    def test_plan_refuses_other_db(self):
        self.make_old_db()
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])
        plan = self.plan()
        other = Path(self._tmp.name) / "other.db"
        shutil.copyfile(self.db, other)
        with self.assertRaises(mm.ExecuteRefused) as cm:
            mm.execute_plan(other, self.root, plan, confirmed=True)
        self.assertIn("与传入数据库", str(cm.exception))

    # ---------- 向量内容绑定（Review r2 修复） ----------
    def _make_vec_db(self):
        self.make_old_db(with_docs=True)  # with_docs=True 尽力创建 chunks_vec
        conn = sqlite3.connect(str(self.db))
        has_vec = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='chunks_vec'").fetchone()[0]
        conn.close()
        if not has_vec:
            self.skipTest("sqlite_vec 不可用，无法构造向量表夹具")
        self.write_jsonl("a.jsonl", [make_row()])
        self.insert([make_row()])

    def test_vec_embedding_change_invalidates_plan(self):
        """chunk_id 集合不变、仅改一条 embedding → 计划指纹失效，执行被拒且零写入。"""
        self._make_vec_db()
        plan = self.plan()
        self.assertTrue(plan["allowed_to_execute"])
        self.assertIsInstance(plan["other_tables"]["chunks_vec"], dict)  # 真内容摘要
        import sqlite_vec
        conn = sqlite3.connect(str(self.db))
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("UPDATE chunks_vec SET embedding=? WHERE chunk_id='d1-0001'",
                     (sqlite_vec.serialize_float32([9.9, 9.9, 9.9, 9.9]),))
        conn.commit()
        conn.close()
        before = self.snapshot()
        with self.assertRaises(mm.ExecuteRefused) as cm:
            self.execute(plan, confirmed=True)
        self.assertIn("chunks_vec", str(cm.exception))  # 拒绝原因指向向量内容变化
        self.assertEqual(self.snapshot(), before)  # 拒绝时零写入，metrics/vec 均未动
        conn = sqlite3.connect(str(self.db))
        mm._try_load_vec(conn)
        emb = conn.execute(
            "SELECT embedding FROM chunks_vec WHERE chunk_id='d1-0001'").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
        self.assertEqual(bytes(emb), sqlite_vec.serialize_float32([9.9, 9.9, 9.9, 9.9]))
        self.assertEqual(n, 1)

    def test_vec_unreadable_blocks_plan(self):
        """embedding 读不出（扩展不可用）→ 计划阻断，不用仅 chunk_id 的弱摘要放行。"""
        self._make_vec_db()
        original = mm._try_load_vec
        mm._try_load_vec = lambda conn: False  # 模拟扩展不可用
        try:
            plan = self.plan()
            self.assertFalse(plan["allowed_to_execute"])
            self.assertTrue(any("chunks_vec_unverifiable" in b for b in plan["blockers"]))
        finally:
            mm._try_load_vec = original
        self.assertIsInstance(plan["other_tables"]["chunks_vec"], str)
        self.assertIn("不可验证", plan["other_tables"]["chunks_vec"])
        with self.assertRaises(mm.ExecuteRefused):
            self.execute(plan, confirmed=True)  # 阻断计划不允许执行
        self.assertEqual(len(self.metrics_rows()), 1)


class BackupTestCase(unittest.TestCase):
    """Step 2B-2A：临时库备份 / 校验 / 恢复。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "metrics"
        self.root.mkdir()
        self.db = base / "test.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE metrics (metric_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,"
                     " metric_name TEXT, value REAL, unit TEXT, comparison_type TEXT,"
                     " comparison_value REAL, is_forecast INTEGER DEFAULT 0,"
                     " source_page TEXT, source_quote TEXT, source_url TEXT, value_text TEXT)")
        conn.execute("CREATE TABLE docs (doc_id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO docs VALUES ('d1', '研报一')")
        conn.commit()
        conn.close()
        self.write_jsonl("a.jsonl", [make_row()])
        c = lem.convert_row(make_row())
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO metrics(" + ",".join(lem.BUSINESS_COLS) + ") VALUES ("
                     + ",".join("?" * 15) + ")", [c[k] for k in lem.BUSINESS_COLS])
        conn.commit()
        conn.close()

    def write_jsonl(self, name, rows):
        (self.root / name).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    def metrics_rows(self):
        conn = sqlite3.connect(str(self.db))
        out = conn.execute("SELECT * FROM metrics ORDER BY metric_id").fetchall()
        conn.close()
        return out

    def test_backup_verify_restore(self):
        """备份→校验→变更源库→恢复→旧数据可读，备份全程未被覆盖。"""
        backup = Path(self._tmp.name) / "backup" / "t.bak.db"
        rep = mm.backup_database(self.db, backup)
        self.assertTrue(backup.is_file())
        self.assertEqual(rep["source_sha256"], hashlib.sha256(self.db.read_bytes()).hexdigest())
        v = mm.verify_backup(backup, expected={"metrics": 1, "docs": 1,
                                               "chunks": "absent"})
        self.assertEqual(v["integrity_check"], "ok")
        self.assertTrue(v["matches_expected"], msg=v.get("expected_mismatches"))
        backup_sha = hashlib.sha256(backup.read_bytes()).hexdigest()
        # 对源库执行迁移+清理（源库状态改变）
        mm.execute_plan(self.db, self.root, mm.build_plan(self.db, self.root), confirmed=True)
        conn = sqlite3.connect(str(self.db))
        inst = conn.execute("SELECT institution, source_file FROM metrics").fetchall()
        conn.close()
        self.assertEqual(inst, [("测试机构", "a.jsonl")])
        self.db.rename(Path(self._tmp.name) / "moved.db")  # 源库挪走
        r = mm.restore_database(backup, self.db)
        self.assertEqual(r["integrity_check"], "ok")
        rows = self.metrics_rows()
        self.assertEqual(len(rows), 1)  # 恢复出旧数据
        conn = sqlite3.connect(str(self.db))
        cols = {c[1] for c in conn.execute("PRAGMA table_info(metrics)")}
        conn.close()
        self.assertNotIn("source_file", cols)  # 恢复到迁移前的旧 schema 状态
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), backup_sha)  # 备份未动

    def test_backup_refuses_overwrite_and_self(self):
        target = Path(self._tmp.name) / "b1.db"
        mm.backup_database(self.db, target)
        with self.assertRaises(mm.ExecuteRefused):  # 拒绝覆盖已有备份
            mm.backup_database(self.db, target)
        with self.assertRaises(mm.ExecuteRefused):  # 目标不能是源库本身
            mm.backup_database(self.db, self.db)

    def test_restore_refuses_existing_target_without_overwrite(self):
        backup = Path(self._tmp.name) / "b2.db"
        mm.backup_database(self.db, backup)
        with self.assertRaises(mm.ExecuteRefused):
            mm.restore_database(backup, self.db)  # 目标已存在
        r = mm.restore_database(backup, self.db, overwrite=True)  # 显式覆盖允许
        self.assertEqual(r["integrity_check"], "ok")


class FormalProtectionTestCase(unittest.TestCase):
    """正式库保护：data/rag.db 及其硬链接/软链接在执行/备份/恢复入口一律被拒。

    测试只验证 guard 拒绝，不向正式库或链接写入任何数据；
    建链/撤链只改动临时目录的目录项，正式库内容与 mtime 不受影响（有断言）。"""

    def setUp(self):
        self.formal = Path(lib_paths.DB)
        if not self.formal.is_file():
            self.skipTest("正式数据库不存在（非本项目运行环境）")
        self.before = mm.db_file_fingerprint(self.formal)

    def _assert_formal_untouched(self):
        after = mm.db_file_fingerprint(self.formal)
        self.assertEqual(after["sha256"], self.before["sha256"])
        self.assertEqual(after["size"], self.before["size"])
        self.assertEqual(after["mtime"], self.before["mtime"])
        self.assertFalse(after["wal_exists"] or after["shm_exists"])

    def test_formal_db_refused_and_untouched(self):
        with self.assertRaises(mm.ExecuteRefused):
            mm.execute_plan(self.formal, Path(lib_paths.METRICS_DIR), {}, confirmed=True)
        with self.assertRaises(mm.ExecuteRefused):
            mm.backup_database(self.formal, Path(tempfile.mkdtemp()) / "x.db")
        with self.assertRaises(mm.ExecuteRefused):
            mm.restore_database(self.formal, self.formal)
        with self.assertRaises(mm.ExecuteRefused):
            mm.restore_database(self.formal, Path(tempfile.mkdtemp()) / "t.db")
        self._assert_formal_untouched()

    def test_hardlink_refused_in_all_directions(self):
        """硬链接（同 inode、resolve 不等）在执行/备份/恢复全部方向被拒。"""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        hard = tmp / "hard.db"
        os.link(self.formal, hard)  # 只增加目录项，不写文件内容
        self.addCleanup(hard.unlink)
        self.assertTrue(os.path.samefile(hard, self.formal))
        self.assertNotEqual(hard.resolve(), self.formal.resolve())  # resolve 比较确实失效
        with self.assertRaises(mm.ExecuteRefused):  # execute
            mm.execute_plan(hard, Path(lib_paths.METRICS_DIR), {}, confirmed=True)
        with self.assertRaises(mm.ExecuteRefused):  # backup 源方向
            mm.backup_database(hard, tmp / "out1.db")
        with self.assertRaises(mm.ExecuteRefused):  # backup 目标方向
            mm.backup_database(tmp / "src.db", hard)
        with self.assertRaises(mm.ExecuteRefused):  # restore 备份源方向
            mm.restore_database(hard, tmp / "out2.db")
        with self.assertRaises(mm.ExecuteRefused):  # restore 目标方向
            mm.restore_database(tmp / "src.db", hard)
        self.assertFalse((tmp / "out1.db").exists())
        self.assertFalse((tmp / "out2.db").exists())
        self._assert_formal_untouched()

    def test_symlink_refused(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        soft = tmp / "soft.db"
        os.symlink(self.formal.resolve(), soft)
        self.addCleanup(soft.unlink)
        with self.assertRaises(mm.ExecuteRefused):
            mm.execute_plan(soft, Path(lib_paths.METRICS_DIR), {}, confirmed=True)
        with self.assertRaises(mm.ExecuteRefused):
            mm.backup_database(soft, tmp / "out.db")
        self._assert_formal_untouched()

    def test_guard_fallback_and_no_false_positive(self):
        """候选不存在时走 resolve 兜底：无关路径不误拒；正式库的独立副本不误拒。"""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ghost = tmp / "ghost.db"
        self.assertFalse(ghost.exists())
        self.assertIsNone(mm._formal_db_guard(ghost, action="测试"))  # 不存在且无关 → 放行
        copy = tmp / "copy.db"
        shutil.copyfile(self.formal, copy)  # 内容相同但 inode 不同
        self.assertIsNone(mm._formal_db_guard(copy, action="测试"))  # 独立副本不误拒
        self._assert_formal_untouched()


class PlanCliTestCase(unittest.TestCase):
    """plan 子命令：真实 CLI 进程、项目外 cwd、退出码、只读性。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "metrics"
        self.root.mkdir()
        self.db = base / "test.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE metrics (metric_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,"
                     " metric_name TEXT, value REAL, unit TEXT, comparison_type TEXT,"
                     " comparison_value REAL, is_forecast INTEGER DEFAULT 0,"
                     " source_page TEXT, source_quote TEXT, source_url TEXT, value_text TEXT)")
        c = lem.convert_row(make_row())
        conn.execute("INSERT INTO metrics(" + ",".join(lem.BUSINESS_COLS) + ") VALUES ("
                     + ",".join("?" * 15) + ")", [c[k] for k in lem.BUSINESS_COLS])
        conn.commit()
        conn.close()
        (self.root / "a.jsonl").write_text(json.dumps(make_row(), ensure_ascii=False) + "\n",
                                           encoding="utf-8")

    def _run(self, *extra, cwd="/"):
        return subprocess.run(
            [sys.executable, str(APP / "scripts" / "metrics_maintenance.py"),
             "plan", "--db", str(self.db), "--metrics-root", str(self.root), *extra],
            cwd=cwd, capture_output=True, text=True, timeout=120)

    def test_plan_cli_allowed_exit_zero(self):
        sha = hashlib.sha256(self.db.read_bytes()).hexdigest()
        report = Path(self._tmp.name) / "plan.json"
        r = self._run("--report", str(report), cwd=self._tmp.name)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("执行计划", r.stdout)
        self.assertIn("未修改任何数据", r.stdout)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertTrue(data["allowed_to_execute"])
        self.assertEqual(data["targets"]["keep"], 1)
        self.assertIn("技术分配", data["technical_assignment_note"])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), sha)  # 库未变

    def test_plan_cli_blocked_exit_nonzero(self):
        (self.root / "b.jsonl").write_text(
            json.dumps(make_row(value=300.0), ensure_ascii=False) + "\n", encoding="utf-8")
        sha = hashlib.sha256(self.db.read_bytes()).hexdigest()
        r = self._run()
        self.assertEqual(r.returncode, 1)  # 阻断计划退出码非零
        self.assertIn("阻断", r.stdout)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), sha)


class PreflightTestCase(unittest.TestCase):
    """Step 2B-2B-Preflight：正式备份入口、预检编排、保护与绑定。

    备份/预检全部在临时库验证；正式库只在只读预检测试中出现（不做备份）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.base = base
        self.root = base / "metrics"
        self.root.mkdir()
        self.db = base / "test.db"
        self.bdir = base / "backups"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE metrics (metric_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                     " doc_id TEXT, institution TEXT, period TEXT, city TEXT, segment TEXT,"
                     " metric_name TEXT, value REAL, unit TEXT, comparison_type TEXT,"
                     " comparison_value REAL, is_forecast INTEGER DEFAULT 0,"
                     " source_page TEXT, source_quote TEXT, source_url TEXT, value_text TEXT)")
        conn.execute("CREATE TABLE docs (doc_id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO docs VALUES ('d1', '研报一')")
        conn.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER,"
                     " heading TEXT, text TEXT, text_seg TEXT, is_table INTEGER DEFAULT 0)")
        conn.execute("INSERT INTO chunks VALUES ('c1', 'd1', 1, 'h', '正文', '正文', 0)")
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(text_seg, content='chunks',"
                     " content_rowid='rowid')")
        conn.execute("INSERT INTO chunks_fts(rowid) SELECT rowid FROM chunks")
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("CREATE VIRTUAL TABLE chunks_vec USING vec0("
                         "chunk_id TEXT PRIMARY KEY, embedding float[4])")
            conn.execute("INSERT INTO chunks_vec VALUES ('c1', ?)",
                         (sqlite_vec.serialize_float32([1.0, 2.0, 3.0, 4.0]),))
            self.has_vec = True
        except Exception:
            self.has_vec = False
        conn.commit()
        conn.close()
        (self.root / "a.jsonl").write_text(
            json.dumps(make_row(), ensure_ascii=False) + "\n", encoding="utf-8")
        c = lem.convert_row(make_row())
        conn = sqlite3.connect(str(self.db))
        conn.execute("INSERT INTO metrics(" + ",".join(lem.BUSINESS_COLS) + ") VALUES ("
                     + ",".join("?" * 15) + ")", [c[k] for k in lem.BUSINESS_COLS])
        conn.commit()
        conn.close()

    # ---------- 1. 正式备份入口 ----------
    def test_formal_backup_requires_confirmation(self):
        with self.assertRaises(mm.ExecuteRefused):
            mm.formal_backup(self.db, self.bdir)
        self.assertFalse(self.bdir.exists())  # 未确认时连目录都不创建

    def test_formal_backup_success_and_verification(self):
        """备份+验证一体：integrity ok、行数/schema/内容摘要一致、源库零改动、可恢复。"""
        sha_before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        rep = mm.formal_backup(self.db, self.bdir, formal_backup_confirmed=True,
                               timestamp="20260908-010101")
        self.assertTrue(rep["ok"], msg=rep["problems"])
        self.assertEqual(rep["integrity_check"], "ok")
        self.assertEqual(rep["problems"], [])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), sha_before)
        self.assertEqual(rep["source_fingerprint"]["sha256"], sha_before)
        self.assertEqual(rep["source_table_stats"]["metrics"],
                         rep["backup_table_stats"]["metrics"])
        self.assertEqual(rep["source_table_stats"]["metrics_content_sha256"],
                         rep["backup_table_stats"]["metrics_content_sha256"])
        self.assertEqual(rep["source_other_tables"], rep["backup_other_tables"])
        self.assertEqual(rep["source_other_tables"]["docs"],
                         rep["backup_other_tables"]["docs"])
        if self.has_vec:
            self.assertIs(rep["embedding_verified"], True)  # vec 真内容摘要可比对
            self.assertIsInstance(rep["backup_other_tables"]["chunks_vec"], dict)
        # 备份可恢复到新的临时目标（backup API 路径）
        restored = Path(self._tmp.name) / "restored.db"
        r = mm.restore_database(Path(rep["backup_path"]), restored)
        self.assertEqual(r["integrity_check"], "ok")
        conn = sqlite3.connect(str(restored))
        n = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)
        # 备份不会被第二次操作覆盖（同秒拒绝）
        with self.assertRaises(mm.ExecuteRefused):
            mm.formal_backup(self.db, self.bdir, formal_backup_confirmed=True,
                             timestamp="20260908-010101")

    def test_formal_backup_target_guards(self):
        """目标位置已被占用（含源的硬链接、正式库的硬链接）一律拒绝，不覆盖。"""
        ts = "20260908-020202"
        pre = self.base / f"rag-2b2-preflight-{ts}.db"
        os.link(self.db, pre)  # 在函数将选定的目标位置预置源的硬链接
        try:
            with self.assertRaises(mm.ExecuteRefused):
                mm.formal_backup(self.db, self.base, formal_backup_confirmed=True, timestamp=ts)
        finally:
            pre.unlink()
        formal = Path(lib_paths.DB)
        if formal.is_file():
            ts2 = "20260908-020203"
            pre2 = self.base / f"rag-2b2-preflight-{ts2}.db"
            os.link(formal, pre2)  # 预置正式库硬链接作为目标
            try:
                with self.assertRaises(mm.ExecuteRefused):
                    mm.formal_backup(self.db, self.base, formal_backup_confirmed=True, timestamp=ts2)
            finally:
                pre2.unlink()

    # ---------- 2. 备份授权 ≠ 写入授权 ----------
    def test_formal_backup_grants_no_execute_permission(self):
        """formal_backup 入口不含迁移/执行调用；其确认参数对 execute_plan 无任何作用。"""
        src = inspect.getsource(mm.formal_backup)
        parts = src.split('"""')
        src = parts[0] + "".join(parts[2:])  # 去掉 docstring，只检查代码
        for kw in ("execute_plan", "ensure_source_file_column", "run_preflight",
                   "ALTER TABLE", "INSERT INTO", "DELETE FROM", "UPDATE metrics"):
            self.assertNotIn(kw, src)
        # 备份确认参数不可能被复用为写入确认：execute_plan 签名不接受它
        self.assertNotIn("formal_backup_confirmed",
                         inspect.signature(mm.execute_plan).parameters)
        formal = Path(lib_paths.DB)
        if not formal.is_file():
            self.skipTest("正式数据库不存在")
        # 备份入口只服务备份；execute_plan 的 confirmed 对正式库仍然拒绝
        with self.assertRaises(mm.ExecuteRefused):
            mm.execute_plan(formal, Path(lib_paths.METRICS_DIR), {}, confirmed=True)

    # ---------- 3. 计划版本绑定 ----------
    def test_plan_version_mismatch_refused(self):
        """计划版本或代码版本不一致 → 拒绝执行，零写入。"""
        plan = mm.build_plan(self.db, self.root)
        for field in ("plan_version", "code_version"):
            tampered = dict(plan)
            tampered[field] = "tampered-1"
            with self.assertRaises(mm.ExecuteRefused) as cm:
                mm.execute_plan(self.db, self.root, tampered, confirmed=True)
            self.assertIn("版本", str(cm.exception))
        conn = sqlite3.connect(str(self.db))
        try:
            self.assertEqual(len(conn.execute("SELECT * FROM metrics").fetchall()), 1)
        finally:
            conn.close()

    # ---------- 4. 预检编排（临时库） ----------
    def test_preflight_with_backup_on_temp(self):
        plan_path = Path(self._tmp.name) / "plan.json"
        rep = mm.run_preflight(self.db, self.root, self.bdir,
                               formal_backup_confirmed=True, plan_report_path=plan_path)
        self.assertTrue(rep["ready_for_next_step"], msg=rep["blockers"])
        self.assertEqual(rep["blockers"], [])
        self.assertEqual(rep["fingerprint_diff"], [])
        self.assertTrue(rep["backup"]["ok"])
        self.assertTrue(Path(rep["backup"]["backup_path"]).is_file())
        self.assertTrue(plan_path.is_file())
        self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8"))["db_path"],
                         str(self.db.resolve()))
        self.assertIn("plan_version", rep["plan"])
        self.assertIn("data_stats", rep["plan"])
        self.assertTrue(all(rep["declarations"].values()))
        # 只读复核：再跑一次只读预检，库与源文件状态稳定
        rep2 = mm.run_preflight(self.db, self.root)
        self.assertEqual(rep2["db"]["fingerprint_before"]["sha256"],
                         rep["db"]["fingerprint_before"]["sha256"])
        self.assertEqual(rep2["sources"]["aggregate_sha256_before"],
                         rep["sources"]["aggregate_sha256_before"])
        # 两次运行之间数据库被改动 → 新预检的匹配阻断与指纹对比都能发现状态漂移
        conn = sqlite3.connect(str(self.db))
        conn.execute("UPDATE metrics SET value=123.0 WHERE metric_id=1")
        conn.commit()
        conn.close()
        rep3 = mm.run_preflight(self.db, self.root)
        self.assertFalse(rep3["ready_for_next_step"])
        self.assertTrue(any("unmatched" in b for b in rep3["blockers"]))
        self.assertNotEqual(rep3["db"]["fingerprint_before"]["sha256"],
                            rep["db"]["fingerprint_before"]["sha256"])

    def test_preflight_skips_backup_when_writer_running(self):
        """替身缺 writer_running 键但 writer_scripts 非空 → 预检阻断且不创建备份。"""
        original = mm.check_processes
        mm.check_processes = lambda: {
            "web": {"running": False, "pid": None, "command": None},
            "writer_scripts": [{"name": "ingest.py", "pid": 99999, "command": "python ingest.py"}],
            "other_db_related": [], "scan_error": None}  # 故意不含 writer_running 派生键
        try:
            rep = mm.run_preflight(self.db, self.root, self.bdir,
                                   formal_backup_confirmed=True)
        finally:
            mm.check_processes = original
        self.assertFalse(rep["ready_for_next_step"])
        self.assertTrue(any("writer_process_running" in b for b in rep["blockers"]))
        self.assertEqual(rep["backup"], {"ok": False, "skipped": "检测到写入进程，未创建备份"})
        self.assertFalse(self.bdir.exists() and any(self.bdir.iterdir()))

    def test_preflight_tolerates_missing_writer_running_key(self):
        """替身缺 writer_running 键且 writer_scripts 为空 → 预检正常继续并完成备份。"""
        original = mm.check_processes
        mm.check_processes = lambda: {
            "web": {"running": False, "pid": None, "command": None},
            "writer_scripts": [], "other_db_related": [], "scan_error": None}
        try:
            rep = mm.run_preflight(self.db, self.root, self.bdir,
                                   formal_backup_confirmed=True)
        finally:
            mm.check_processes = original
        self.assertTrue(rep["ready_for_next_step"], msg=rep["blockers"])
        self.assertTrue(rep["backup"]["ok"])
        self.assertEqual(rep["backup"]["problems"], [])

    def test_ps_scan_failure_struct_complete_and_no_keyerror(self):
        """ps 扫描失败：返回结构完整（含 writer_running=False），预检不抛 KeyError。"""
        original_run = mm.subprocess.run

        def boom(*args, **kwargs):
            raise OSError("ps 不可用（模拟）")

        mm.subprocess.run = boom
        try:
            proc = mm.check_processes()
            rep_ro = mm.run_preflight(self.db, self.root)  # 只读预检
            rep_bak = mm.run_preflight(self.db, self.root, self.bdir,
                                       formal_backup_confirmed=True)  # 含备份
        finally:
            mm.subprocess.run = original_run
        self.assertIs(proc["writer_running"], False)
        self.assertTrue(proc["scan_error"])
        self.assertEqual(proc["writer_scripts"], [])
        self.assertIn("web", proc)
        self.assertIn("other_db_related", proc)
        self.assertTrue(rep_ro["processes"]["scan_error"])  # 如实记录，不崩溃
        self.assertTrue(rep_bak["backup"]["ok"])  # 兜底规则：无写入脚本证据则继续
        self.assertEqual(rep_bak["backup"]["problems"], [])

    def test_preflight_vec_unverifiable_not_fully_verified(self):
        """embedding 不可验证 → 备份可创建但不得声称充分验证，预检阻断进入写入。"""
        if not self.has_vec:
            self.skipTest("sqlite_vec 不可用")
        original = mm._try_load_vec
        mm._try_load_vec = lambda conn: False
        try:
            rep = mm.run_preflight(self.db, self.root, self.bdir,
                                   formal_backup_confirmed=True)
        finally:
            mm._try_load_vec = original
        self.assertFalse(rep["ready_for_next_step"])
        self.assertTrue(any("chunks_vec_unverifiable" in b for b in rep["blockers"]))
        self.assertTrue(rep["backup"]["ok"])  # 备份本身成功
        self.assertFalse(rep["backup"]["embedding_verified"])  # 但不声称充分验证
        self.assertTrue(any("不可验证" in w for w in rep["backup"]["warnings"]))

    # ---------- 5. 正式库只读预检 ----------
    def test_preflight_readonly_on_formal(self):
        """对正式库做无备份预检：纯只读，正式库指纹前后不变。"""
        formal = Path(lib_paths.DB)
        if not formal.is_file():
            self.skipTest("正式数据库不存在（非本项目运行环境）")
        before = mm.db_file_fingerprint(formal)
        rep = mm.run_preflight(formal, Path(lib_paths.METRICS_DIR))
        after = mm.db_file_fingerprint(formal)
        self.assertIsNone(rep["backup"])  # 未做备份
        self.assertIn("web", rep["processes"])
        self.assertIn("writer_scripts", rep["processes"])
        self.assertGreater(rep["plan"]["data_stats"]["db_total_rows"], 0)
        self.assertEqual(after["sha256"], before["sha256"])
        self.assertEqual(after["mtime"], before["mtime"])
        self.assertEqual(after["inode"], before["inode"])


if __name__ == "__main__":
    unittest.main()
