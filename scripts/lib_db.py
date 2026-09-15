"""SQLite 单文件存储：docs / chunks / FTS5 全文索引 / sqlite-vec 向量 / metrics 指标表。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  doc_id TEXT PRIMARY KEY,
  file_path TEXT UNIQUE,
  title TEXT,
  institution TEXT,
  report_date TEXT,
  lang TEXT,
  cities TEXT          -- JSON array
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,   -- {doc_id}-{ord:04d}
  doc_id TEXT REFERENCES docs(doc_id),
  ord INTEGER,
  heading TEXT,
  text TEXT,
  text_seg TEXT,               -- jieba 分词后的检索专用文本
  is_table INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text_seg, content='chunks', content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS metrics (
  metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT,                 -- 外部数据时为 NULL
  institution TEXT,
  period TEXT,                 -- 2026-08 / 2026Q1 / 2026W33
  city TEXT,
  segment TEXT,                -- new_home / secondary_home / land / macro
  metric_name TEXT,
  value REAL,
  value_text TEXT,             -- text 型指标（如 price_bottom_timing "2026Q4"）
  unit TEXT,
  comparison_type TEXT,        -- mom / yoy / NULL
  comparison_value REAL,
  is_forecast INTEGER DEFAULT 0,
  source_page TEXT,
  source_quote TEXT,           -- 原文句子，人审用
  source_url TEXT,             -- 外部数据来源
  source_file TEXT             -- 本地导入来源（相对 metrics/ 根目录），仅新建库自带；
                               -- 既有库须走显式迁移 migrate_add_source_file，connect() 不自动补
);
CREATE INDEX IF NOT EXISTS idx_metrics_query ON metrics(metric_name, city, period);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)  # 写锁期间（如 build_index）读请求排队而非立刻报 locked
    conn.row_factory = sqlite3.Row
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except ImportError:
        pass  # 向量功能在索引构建阶段才需要
    conn.executescript(SCHEMA)
    # 老库升级：metrics 表补 value_text 列
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(metrics)")}
    if "value_text" not in cols:
        conn.execute("ALTER TABLE metrics ADD COLUMN value_text TEXT")
    # 注意：source_file 不在此自动迁移（避免导入被拒前被间接改动），用下面的显式入口
    conn.commit()
    # 向量表单独建（维度在首次写入时确定）
    return conn


def migrate_add_source_file(conn: sqlite3.Connection) -> bool:
    """显式迁移：metrics 表补 source_file 列，返回是否执行了 ALTER。

    幂等，可重复调用；只加列，不归属历史数据、不触发导入。调用方负责 commit/close。
    历史行迁移后 source_file 仍为 NULL，导入器会继续拒绝，直到 Step 2B 完成归属。
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='metrics'")}
    if not tables:
        raise RuntimeError("metrics 表不存在：数据库未初始化，不能迁移")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
    if "source_file" in cols:
        return False
    conn.execute("ALTER TABLE metrics ADD COLUMN source_file TEXT")
    return True


def ensure_vec_table(conn: sqlite3.Connection, dim: int) -> None:
    conn.execute(
        f"""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            chunk_id TEXT PRIMARY KEY, embedding float[{dim}])"""
    )
