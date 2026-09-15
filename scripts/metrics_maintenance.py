"""metrics 历史数据核对、预览与正式执行逻辑骨架（Step 2B-1 / 2B-2A）。

CLI 保持 100% 只读：子命令 preview / plan / fingerprint / inspect-source /
inspect-db 全部以 SQLite mode=ro 打开数据库，不调用 lib_db.connect()
（它会建表/迁移/commit），没有任何写操作入口。

Step 2B-2A 增加内部执行函数（build_plan / execute_plan / ensure_source_file_column /
backup_database / verify_backup / restore_database），安全边界：
1. CLI 与只读参数（--report/--fingerprint 等）不会调用这些函数；
2. execute_plan 必须显式 confirmed=True，且数据库路径与正式库
   （lib_paths.DB，data/rag.db）为同一文件时无条件拒绝——按文件身份
   （os.path.samefile，同 inode）判定，硬链接/软链接同样命中，路径不存在时
   退回 resolve() 归一化比较；正式库的迁移/归属/清理需后续单独授权；
3. 执行必须绑定 build_plan 生成的计划：执行前重算数据库/源文件指纹
   （含 docs/chunks/FTS/vec 内容摘要，vec 摘要覆盖 chunk_id + embedding
   原始字节；读不出 embedding 时计划直接阻断，不用弱摘要放行），
   事务内重算目标集，与计划不一致即拒绝/回滚；执行后旧计划立即失效，
   必须重新生成（新计划为 no-op，状态稳定）；
4. 事务策略 = 候选一（直接修改 metrics 表）：单事务（BEGIN IMMEDIATE）内
   显式 ALTER（仅缺列时）+ UPDATE source_file + DELETE 同文件重复，
   任何失败整体回滚，不留临时对象，不改 docs/chunks/FTS/vec。

Step 2B-2B-Preflight 增加正式执行前预检与正式备份入口（check_processes /
formal_backup / run_preflight），安全边界：
- formal_backup 是"只读源 → 新备份目标"的最小权限路径：须显式
  formal_backup_confirmed=True；源以 mode=ro 打开；目标必须是不存在的新文件，
  且不得是正式库或源库的本体/硬链接/软链接；同秒冲突拒绝而不覆盖；
  只做备份 + 验证（integrity_check/行数/schema/内容摘要比对），不做迁移、
  回填、删除，不调用 execute_plan；
- formal_backup_confirmed 与 execute_plan 的 confirmed 互不通用：
  正式备份授权绝不构成正式写入授权，execute_plan 对正式库仍无条件拒绝；
- run_preflight 汇总数据库/源文件指纹、进程状态、执行计划与备份验证，
  发现写入进程（ingest/build_index/load_external_metrics/run_eval）时
  停止备份并记为阻断；CLI 仍然没有任何写入或备份子命令。

匹配复用 Step 2A 的 read_rows()/convert_row() 与 BUSINESS_COLS，
完全相等 = 15 个业务字段精确一致（NULL↔NULL、NULL≠''、value≠value_text、
数值按转换后精确值、引文不做任何归一化），metric_id 不参与。

免责声明：source_file 只表示记录与某个本地源文件内容对应，不代表原始机构来源
已核实，不代表文件中的机构字段一定真实，也不代表同内容出现在两个文件就是两条
独立证据，数据统计口径未因此确认。跨文件相同内容的 metric_id 归属是确定性
技术分配，不是从历史数据库恢复的真实来源事实。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from lib_paths import DB, METRICS_DIR  # noqa: E402
from load_external_metrics import BUSINESS_COLS, LoadError, convert_row, read_rows  # noqa: E402

READONLY_SUBCOMMANDS = ("preview", "plan", "fingerprint", "inspect-source", "inspect-db")

DISCLAIMER = ("source_file 只表示记录与本地源文件内容对应，不代表原始机构来源已核实，"
              "不代表同内容出现在两个文件就是两条独立证据；本报告为只读预览，未修改任何数据。")


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读连接（mode=ro）。绝不用于写入。"""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def db_file_fingerprint(db_path: Path) -> dict:
    db_path = Path(db_path)
    st = db_path.stat()
    return {"path": str(db_path), "size": st.st_size, "mtime": st.st_mtime,
            "sha256": _sha256_file(db_path),
            "inode": st.st_ino, "device": st.st_dev,
            "wal_exists": Path(str(db_path) + "-wal").exists(),
            "shm_exists": Path(str(db_path) + "-shm").exists()}


def table_stats(conn: sqlite3.Connection) -> dict:
    stats = {"journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0]}
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("docs", "chunks", "chunks_fts", "metrics"):
        stats[t] = (conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    if t in tables else "absent")
    if "chunks_vec" in tables:
        try:  # chunks_vec 需要 sqlite-vec；只读加载扩展，不可用则如实报告
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            stats["chunks_vec"] = conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
        except Exception as e:
            stats["chunks_vec"] = f"unavailable: {e}"
    else:
        stats["chunks_vec"] = "absent"
    cols = [r[1] for r in conn.execute("PRAGMA table_info(metrics)")]
    stats["metrics_columns"] = cols
    stats["metrics_has_source_file_column"] = "source_file" in cols
    h = hashlib.sha256()
    for row in conn.execute("SELECT * FROM metrics ORDER BY metric_id"):
        h.update(repr(tuple(row)).encode())
    stats["metrics_content_sha256"] = h.hexdigest()
    return stats


def source_fingerprint(metrics_root: Path) -> dict:
    metrics_root = Path(metrics_root)
    files = sorted(metrics_root.glob("*.csv")) + sorted(metrics_root.glob("*.jsonl"))
    agg = hashlib.sha256()
    entries = []
    for f in files:
        fh = _sha256_file(f)
        agg.update(f"{f.name}:{fh}\n".encode())
        st = f.stat()
        entries.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime, "sha256": fh})
    return {"metrics_root": str(metrics_root), "file_count": len(files),
            "aggregate_sha256": agg.hexdigest(), "files": entries}


def load_source_rows(metrics_root: Path):
    """读取并转换全部源文件。返回 (records, file_errors)。

    records: {source_file: [(lineno, content_tuple)]}
    file_errors: {source_file: 错误消息} —— 解析/编码/转换失败的文件整体跳过并报告，
    不使用模糊匹配兜底。
    """
    metrics_root = Path(metrics_root)
    files = sorted(metrics_root.glob("*.csv")) + sorted(metrics_root.glob("*.jsonl"))
    records: dict[str, list] = {}
    errors: dict[str, str] = {}
    for f in files:
        rel = f.relative_to(metrics_root).as_posix()
        try:
            raw = read_rows(f)
            recs = []
            for lineno, r in enumerate(raw, 1):
                if not isinstance(r, dict):
                    raise LoadError(f"第{lineno}条记录不是对象")
                recs.append((lineno, tuple(convert_row(r)[c] for c in BUSINESS_COLS)))
            records[rel] = recs
        except (LoadError, ValueError, OSError) as e:
            errors[rel] = str(e)
    return records, errors


def _fingerprint(content: tuple) -> str:
    return hashlib.sha256(repr(content).encode()).hexdigest()[:12]


def _sample(content: tuple, metric_ids=()) -> dict:
    keys = ("institution", "period", "city", "segment", "metric_name", "value", "value_text")
    d = {k: content[BUSINESS_COLS.index(k)] for k in keys}
    q = content[BUSINESS_COLS.index("source_quote")]
    d["source_quote"] = (q[:40] + "…") if isinstance(q, str) and len(q) > 40 else q
    d["metric_ids"] = list(metric_ids)[:8]
    return d


def analyze(conn: sqlite3.Connection, metrics_root: Path) -> dict:
    """核心核对：DB 只读行 × 源文件转换行，15 字段精确匹配分类。纯计算，不写。"""
    metrics_root = Path(metrics_root)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(metrics)")]
    has_sf = "source_file" in cols
    ci = {c: i for i, c in enumerate(cols)}
    db_rows = conn.execute("SELECT * FROM metrics ORDER BY metric_id").fetchall()

    content_to_dbids: dict[tuple, list] = {}
    for row in db_rows:
        key = tuple(row[ci[c]] for c in BUSINESS_COLS)
        content_to_dbids.setdefault(key, []).append(row[ci["metric_id"]])

    src_records, src_errors = load_source_rows(metrics_root)
    content_to_files: dict[tuple, dict[str, list]] = {}
    src_total = 0
    for sf, recs in src_records.items():
        src_total += len(recs)
        for lineno, content in recs:
            content_to_files.setdefault(content, {}).setdefault(sf, []).append(lineno)

    # DB 侧分类（按 distinct 内容归类，行数单独累计）
    unique_c, ambiguous_c, unmatched_c = [], [], []
    dup_groups = []
    for content, ids in content_to_dbids.items():
        files = content_to_files.get(content, {})
        if not files:
            unmatched_c.append((content, ids))
        elif len(files) == 1:
            unique_c.append((content, ids))
        else:
            ambiguous_c.append((content, ids))
        if len(ids) > 1:
            dup_groups.append((content, ids))

    # B. 同文件内重复候选（源文件视角）
    within_file_dups = []
    for sf, recs in src_records.items():
        seen: dict[tuple, list] = {}
        for lineno, content in recs:
            seen.setdefault(content, []).append(lineno)
        for content, linenos in seen.items():
            if len(linenos) > 1:
                n_db = len(content_to_dbids.get(content, []))
                keep = len(content_to_files.get(content, {})) or 1  # 每个来源文件保留 1 条
                within_file_dups.append({
                    "source_file": sf, "fingerprint": _fingerprint(content),
                    "linenos": linenos, "dup_count_in_file": len(linenos),
                    "db_rows": n_db, "future_keep": keep,
                    "future_deletable": max(n_db - keep, 0),
                    "sample": _sample(content, content_to_dbids.get(content, [])),
                })

    # C. 跨文件相同内容（各来源分别保留，不合并不视为独立证据）
    cross_file = []
    for content, files in content_to_files.items():
        if len(files) > 1:
            ids = content_to_dbids.get(content, [])
            cross_file.append({
                "fingerprint": _fingerprint(content),
                "source_files": {sf: linenos for sf, linenos in sorted(files.items())},
                "db_rows": len(ids),
                "note": "这些来源内容完全相同，不能视为独立证据",
                "sample": _sample(content, ids),
            })

    # F. 源文件记录未进入数据库
    unmatched_source = []
    for sf, recs in src_records.items():
        miss = [lineno for lineno, content in recs if content not in content_to_dbids]
        if miss:
            unmatched_source.append({"source_file": sf, "linenos": miss, "count": len(miss)})

    # G. 已有 source_file 列时的归属状态分析
    if has_sf:
        g = {"sf_match": 0, "sf_mismatch": 0, "sf_empty": 0, "sf_missing_file": 0}
        for row in db_rows:
            sf = row[ci["source_file"]]
            content = tuple(row[ci[c]] for c in BUSINESS_COLS)
            if sf is None or sf == "":
                g["sf_empty"] += 1
            elif not (metrics_root / sf).is_file():
                g["sf_missing_file"] += 1
            elif sf in content_to_files.get(content, {}):
                g["sf_match"] += 1
            else:
                g["sf_mismatch"] += 1
    else:
        g = {"status": "source_file 列不存在（未迁移），全部记录视为未归属"}

    # 异常：DB 行数少于内容出现的文件数（无法满足每来源保留 1 条）
    anomalies = [{"fingerprint": _fingerprint(content),
                  "db_rows": len(ids), "source_files": len(content_to_files.get(content, {}))}
                 for content, ids in content_to_dbids.items()
                 if 0 < len(ids) < len(content_to_files.get(content, {}))]

    # ---- 核算（直接统计 vs 预览推断分别标注）----
    db_total = len(db_rows)
    distinct_contents = len(content_to_dbids)
    extra_rows = sum(len(ids) - 1 for ids in content_to_dbids.values() if len(ids) > 1)
    src_distinct = len({c for recs in src_records.values() for _, c in recs})
    within_extra = sum(g["dup_count_in_file"] - 1 for g in within_file_dups)
    combos = len({(sf, c) for sf, recs in src_records.items() for _, c in recs})
    matched_rows = sum(len(ids) for c, ids in unique_c) + sum(len(ids) for c, ids in ambiguous_c)
    unmatched_db_rows = sum(len(ids) for _, ids in unmatched_c)
    unmatched_src_rows = sum(g["count"] for g in unmatched_source)
    # 理论目标（预览推断，非执行目标）：每内容按出现的源文件数各保留 1 条；未匹配行保留
    keep_matched = sum(len(content_to_files[c]) for c, _ in unique_c) \
        + sum(len(content_to_files[c]) for c, _ in ambiguous_c)
    keep_total = keep_matched + unmatched_db_rows
    accounting = {
        "db_total_rows": (db_total, "DB 直接统计"),
        "db_distinct_contents": (distinct_contents, "DB 直接统计"),
        "exact_dup_groups": (len(dup_groups), "DB 直接统计"),
        "exact_dup_extra_rows": (extra_rows, "DB 直接统计"),
        "source_total_rows": (src_total, "源文件直接统计"),
        "source_distinct_contents": (src_distinct, "源文件转换后统计"),
        "within_file_dup_extra_rows": (within_extra, "源文件转换后统计"),
        "distinct_file_content_combos": (combos, "源文件转换后统计"),
        "cross_file_content_groups": (len(cross_file), "源文件转换后统计"),
        "db_rows_matched": (matched_rows, "预览推断（精确匹配）"),
        "db_rows_unmatched": (unmatched_db_rows, "预览推断（精确匹配）"),
        "source_rows_unmatched": (unmatched_src_rows, "预览推断（精确匹配）"),
        "unique_match_rows": (sum(len(ids) for _, ids in unique_c), "预览推断"),
        "ambiguous_match_rows": (sum(len(ids) for _, ids in ambiguous_c), "预览推断"),
        "theoretical_keep": (keep_total, "预览推断"),
        "theoretical_delete": (db_total - keep_total, "预览推断"),
        "formula": ("保留 = Σ_内容(该内容出现的不同源文件数)，未匹配 DB 行原样保留；"
                    "删除 = db_total_rows − 保留。参考值仅绑定本次快照，执行时须重新计算。"),
    }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(conn.execute("PRAGMA database_list").fetchone()[2]),
        "metrics_root": str(metrics_root),
        "disclaimer": DISCLAIMER,
        "accounting": accounting,
        "matching": {
            "unique_match_contents": len(unique_c),
            "ambiguous_match_contents": len(ambiguous_c),
            "unmatched_db_contents": len(unmatched_c),
            "source_file_errors": src_errors,
            "existing_source_file": g,
        },
        "anomalies": anomalies,
        "within_file_duplicate": within_file_dups,
        "cross_file_same_content": cross_file,
        "unmatched_source_rows": unmatched_source,
        "unmatched_db_rows": [
            {"fingerprint": _fingerprint(c), "metric_ids": ids[:20], "count": len(ids),
             "sample": _sample(c, ids)} for c, ids in unmatched_c[:20]
        ],
        "samples": {
            "exact_dup": [{"fingerprint": _fingerprint(c), "count": len(ids),
                           "metric_ids": ids[:8], "sample": _sample(c, ids)}
                          for c, ids in dup_groups[:3]],
            "cross_file": cross_file[:3],
            "ambiguous_note": "ambiguous_match = 内容出现在多个源文件，不按机构/城市/数值猜归属",
        },
    }


def build_report(db_path: Path, metrics_root: Path) -> dict:
    db_path = Path(db_path)
    report = {"db_file_fingerprint": db_file_fingerprint(db_path),
              "source_fingerprint": source_fingerprint(metrics_root)}
    conn = connect_ro(db_path)
    try:
        report["table_stats"] = table_stats(conn)
        report.update(analyze(conn, metrics_root))
    finally:
        conn.close()
    return report


# ===================== Step 2B-2A：正式执行逻辑骨架（仅临时库） =====================

CODE_VERSION = "step2b2b-preflight-r2-2026-09-08"
PLAN_VERSION = "2b2b-plan/1"
ALLOCATION_VERSION = "det-pinned-sort-v1"
PLAN_DISCLAIMER = ("本计划由只读扫描生成，未修改任何数据。跨文件相同内容的 metric_id 分配是"
                   "确定性技术分配，不是从历史数据库恢复的真实来源事实；source_file 只证明内容"
                   "对应本地文件，不代表来源独立性，也不代表同内容出现在多个文件是多条独立证据。")
TECH_ASSIGN_NOTE = ("技术分配，非历史事实恢复：按 source_file POSIX 升序 × metric_id 升序配对"
                    "（尊重已有一致归属），不代表数据库历史中的真实来源。")
DELETE_REASON = ("same_file_duplicate_excess：该内容在库中的物理行数超过其出现的源文件数，"
                 "每个来源各保留一条后，多余物理行按 metric_id 升序删除。")


class PlanBlocked(Exception):
    """build_plan 无法完成（如 metrics 表不存在）。只读，未写入。"""


class ExecuteRefused(Exception):
    """执行/备份/恢复被拒绝：未确认、正式库保护、状态指纹不一致或计划与库不符。
    拒绝发生时保证零写入。"""


class ExecuteError(Exception):
    """执行失败（事务内异常或提交前核验不过），已完整回滚。"""


def _is_same_file(a: Path, b: Path) -> bool:
    """文件身份比较（同 inode）：硬链接/软链接/大小写变体一律按底层文件判定；
    任一侧不存在（samefile 无法 stat）时退回 resolve() 路径归一化比较。"""
    a, b = Path(a), Path(b)
    try:
        return os.path.samefile(a, b)
    except OSError:
        return a.resolve() == b.resolve()


def _formal_db_guard(db_path: Path, action: str) -> None:
    """正式库写保护：任何指向正式库（本体/硬链接/软链接）的写/恢复操作一律拒绝。"""
    if _is_same_file(db_path, DB):
        raise ExecuteRefused(
            f"{action} 被拒绝：{db_path} 是正式数据库（文件身份与 {DB} 相同，"
            "含硬链接/软链接）。正式库的迁移/归属/清理/覆盖需后续单独授权；"
            "Step 2B-2B-Preflight 仅授权只读预检与 formal_backup 备份。")


def _read_db_rows(conn: sqlite3.Connection) -> tuple:
    """读取 metrics 全部行 → (列名列表, [(metric_id, 15字段内容, 已有 source_file)])。

    缺 source_file 列时已有归属一律视为 None（未归属）。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(metrics)")]
    if not cols:
        raise PlanBlocked("metrics 表不存在")
    has_sf = "source_file" in cols
    ci = {c: i for i, c in enumerate(cols)}
    rows = [(row[ci["metric_id"]], tuple(row[ci[c]] for c in BUSINESS_COLS),
             row[ci["source_file"]] if has_sf else None)
            for row in conn.execute("SELECT * FROM metrics ORDER BY metric_id")]
    return cols, rows


def _content_index(src_records: dict) -> dict:
    """源文件记录 → {内容: {source_file: [行号]}}。"""
    idx: dict = {}
    for sf, recs in src_records.items():
        for lineno, content in recs:
            idx.setdefault(content, {}).setdefault(sf, []).append(lineno)
    return idx


def _compute_targets(db_rows: list, content_to_files: dict) -> tuple:
    """确定性目标集：assignments（保留行含归属）/ deletes / blockers（阻断原因）。

    分配规则（ALLOCATION_VERSION = det-pinned-sort-v1）：
    - 已有 source_file 且属于该内容当前的源文件集合 → 固定保留原归属，
      同一来源多个一致归属按 metric_id 升序保留第一条，多余行进删除候选；
    - 未归属物理行分配给尚无归属的来源：来源按 POSIX 升序 × metric_id 升序依次配对；
    - 物理行不足（无法满足每个来源至少一条）→ 阻断，不删除、不合并；
    - 配对后多余的物理行 → 同文件重复删除候选（metric_id 升序）；
    - 已有归属与内容不一致（含指向不存在的文件）→ 阻断，拒绝静默覆盖；
    - 数据库/源文件存在对方没有的内容 → 阻断。
    """
    blockers, assignments, deletes = [], [], []
    by_content: dict = {}
    for mid, content, sf in db_rows:
        by_content.setdefault(content, []).append((mid, sf))

    for content, rows in by_content.items():
        files = sorted(content_to_files.get(content, {}))
        fp = _fingerprint(content)
        if not files:
            blockers.append(f"db_unmatched: 内容 {fp}（{len(rows)} 行）在当前源文件中不存在，"
                            "不删除、不强行归属")
            continue
        rows_sorted = sorted(rows, key=lambda t: t[0])
        pinned: dict = {}
        unpinned: list = []
        for mid, sf in rows_sorted:
            if sf is None or sf == "":
                unpinned.append(mid)
            elif sf in files:
                pinned.setdefault(sf, []).append(mid)
            else:
                blockers.append(f"sf_inconsistent: metric_id={mid} 已有 source_file={sf!r}，"
                                f"但内容 {fp} 当前只出现在 {files}，归属不一致（含指向不存在的文件），"
                                "拒绝静默覆盖")
        linenos = content_to_files[content]
        unmet = [f for f in files if not pinned.get(f)]
        if len(unpinned) < len(unmet):
            blockers.append(f"insufficient_rows: 内容 {fp} 出现在 {len(files)} 个源文件 {files}，"
                            f"可分配物理行仅 {len(unpinned)}（已有一致归属 "
                            f"{sum(len(v) for v in pinned.values())} 行），不删除、不合并")
            continue
        for sf, mid in zip(unmet, unpinned):
            assignments.append({"metric_id": mid, "source_file": sf, "action": "assign",
                                "note": TECH_ASSIGN_NOTE, "fingerprint": fp,
                                "source_linenos": linenos[sf],
                                "sample": _sample(content, [mid])})
        leftover = list(unpinned[len(unmet):])
        for sf, ids in pinned.items():
            assignments.append({"metric_id": ids[0], "source_file": sf, "action": "keep_existing",
                                "note": "已有一致归属，保留不改动", "fingerprint": fp,
                                "source_linenos": linenos[sf],
                                "sample": _sample(content, [ids[0]])})
            leftover.extend(ids[1:])
        for mid in sorted(leftover):
            deletes.append({"metric_id": mid, "reason": DELETE_REASON, "fingerprint": fp,
                            "content_source_files": files,
                            "source_linenos": {sf: linenos[sf] for sf in files},
                            "sample": _sample(content, [mid])})

    for content, files in content_to_files.items():
        if content not in by_content:
            blockers.append(f"source_unmatched: 内容 {_fingerprint(content)} 在源文件 "
                            f"{sorted(files)} 中存在但数据库没有，阻断全部清理")
    return assignments, deletes, blockers


def _rows_digest(conn: sqlite3.Connection, sql: str) -> dict:
    h = hashlib.sha256()
    n = 0
    for row in conn.execute(sql):
        h.update(repr(tuple(row)).encode())
        n += 1
    return {"rows": n, "sha256": h.hexdigest()}


def _try_load_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:
        return False


def _vec_content_digest(conn: sqlite3.Connection) -> dict | str:
    """chunks_vec 内容摘要：chunk_id + embedding 原始字节，两者都参与 hash。

    只验证 chunk_id 集合不算内容摘要（embedding 变了不会被发现）。读不出
    embedding 原始值时返回 "unavailable…" 字符串，由 build_plan 阻断计划，
    不允许用弱摘要放行。"""
    if not _try_load_vec(conn):
        return "unavailable: sqlite_vec 扩展不可用（embedding 内容不可验证）"
    try:
        h = hashlib.sha256()
        n = 0
        for cid, emb in conn.execute(
                "SELECT chunk_id, embedding FROM chunks_vec ORDER BY chunk_id"):
            h.update(repr((cid, bytes(emb))).encode())
            n += 1
        return {"rows": n, "sha256": h.hexdigest()}
    except Exception as e:
        return f"unavailable: 无法读取 embedding 原始值（{e}），内容不可验证"


def other_tables_digest(conn: sqlite3.Connection) -> dict:
    """docs/chunks/chunks_fts/chunks_vec 内容摘要（行数+hash），用于证明执行前后未变。

    docs/chunks/fts 摘要覆盖全部列；chunks_vec 覆盖 chunk_id + embedding 原始
    字节（见 _vec_content_digest），读不出时如实标记不可验证，不假装校验过。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out = {}
    out["docs"] = (_rows_digest(conn, "SELECT * FROM docs ORDER BY doc_id")
                   if "docs" in tables else "absent")
    out["chunks"] = (_rows_digest(conn, "SELECT * FROM chunks ORDER BY chunk_id")
                     if "chunks" in tables else "absent")
    if "chunks_fts" in tables:
        try:
            out["chunks_fts"] = _rows_digest(
                conn, "SELECT rowid, text_seg FROM chunks_fts ORDER BY rowid")
        except sqlite3.Error as e:
            out["chunks_fts"] = f"unavailable: {e}"
    else:
        out["chunks_fts"] = "absent"
    out["chunks_vec"] = _vec_content_digest(conn) if "chunks_vec" in tables else "absent"
    return out


def build_plan(db_path: Path, metrics_root: Path) -> dict:
    """生成正式执行计划（只读）。阻断状态写入 blockers 且 allowed_to_execute=False。"""
    db_path, metrics_root = Path(db_path), Path(metrics_root)
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    conn = connect_ro(db_path)
    try:
        cols, db_rows = _read_db_rows(conn)
        stats = table_stats(conn)
        other = other_tables_digest(conn)
    finally:
        conn.close()
    src_records, src_errors = load_source_rows(metrics_root)
    content_to_files = _content_index(src_records)
    assignments, deletes, blockers = _compute_targets(db_rows, content_to_files)
    blockers = [f"source_file_error: {name}: {err}"
                for name, err in sorted(src_errors.items())] + blockers
    vec = other["chunks_vec"]
    if isinstance(vec, str) and vec != "absent":  # 表存在但 embedding 不可验证 → 阻断
        blockers.append(f"chunks_vec_unverifiable: {vec}；"
                        "不允许用仅 chunk_id 的弱摘要证明内容未变，计划阻断")
    cross_groups = sum(1 for files in content_to_files.values() if len(files) > 1)
    srcfp = source_fingerprint(metrics_root)
    # 数据面统计（§七 计划字段：重复/匹配/归属现状，全部现场重算）
    by_content: dict = {}
    for _mid, content, sf in db_rows:
        by_content.setdefault(content, []).append(sf)
    src_combos = len({(sf, c) for sf, recs in src_records.items() for _, c in recs})
    within_extra = 0
    for _sf, recs in src_records.items():
        seen = set()
        for _, c in recs:
            if c in seen:
                within_extra += 1
            else:
                seen.add(c)
    if "source_file" in cols:
        attrib: dict = {"sf_empty": 0, "sf_match": 0, "sf_mismatch_or_unknown": 0}
        for content, rows in by_content.items():
            files = set(content_to_files.get(content, {}))
            for sf in rows:
                if sf is None or sf == "":
                    attrib["sf_empty"] += 1
                elif sf in files:
                    attrib["sf_match"] += 1
                else:
                    attrib["sf_mismatch_or_unknown"] += 1
    else:
        attrib = {"status": "source_file 列不存在（未迁移），全部记录视为未归属"}
    data_stats = {
        "db_total_rows": len(db_rows),
        "db_distinct_contents": len(by_content),
        "exact_dup_groups": sum(1 for rows in by_content.values() if len(rows) > 1),
        "exact_dup_extra_rows": sum(len(rows) - 1 for rows in by_content.values()
                                    if len(rows) > 1),
        "source_total_rows": sum(len(recs) for recs in src_records.values()),
        "distinct_file_content_combos": src_combos,
        "within_file_dup_extra_rows": within_extra,
        "cross_file_content_groups": cross_groups,
        "unique_match_contents": sum(1 for c in by_content
                                     if len(content_to_files.get(c, {})) == 1),
        "ambiguous_match_contents": sum(1 for c in by_content
                                        if len(content_to_files.get(c, {})) > 1),
        "db_unmatched_rows": sum(len(rows) for c, rows in by_content.items()
                                 if c not in content_to_files),
        "source_unmatched_contents": sum(1 for c in content_to_files
                                         if c not in by_content),
        "existing_source_file": attrib,
    }
    return {
        "plan_version": PLAN_VERSION,
        "code_version": CODE_VERSION,
        "allocation_algorithm": ALLOCATION_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(db_path.resolve()),
        "db_fingerprint": db_file_fingerprint(db_path),
        "metrics_content_sha256": stats["metrics_content_sha256"],
        "metrics_columns": stats["metrics_columns"],
        "table_row_counts": {k: stats[k] for k in
                             ("docs", "chunks", "chunks_fts", "chunks_vec", "metrics")},
        "source_root": str(metrics_root.resolve()),
        "source_files": [{"name": f["name"], "sha256": f["sha256"]} for f in srcfp["files"]],
        "source_aggregate_sha256": srcfp["aggregate_sha256"],
        "other_tables": other,
        "db_rows": len(db_rows),
        "data_stats": data_stats,
        "targets": {
            "keep": len(assignments),
            "delete": len(deletes),
            "technical_assignments": sum(1 for a in assignments if a["action"] == "assign"),
            "keep_existing": sum(1 for a in assignments if a["action"] == "keep_existing"),
            "cross_file_groups_retained": cross_groups,
        },
        "assignments": assignments,
        "deletes": deletes,
        "blockers": blockers,
        "allowed_to_execute": not blockers,
        "technical_assignment_note": TECH_ASSIGN_NOTE,
        "disclaimer": PLAN_DISCLAIMER,
        "transaction_strategy": ("direct-metrics-v1（候选一）：单事务（BEGIN IMMEDIATE）内"
                                 " 显式 ALTER（仅缺列时）+ UPDATE source_file + DELETE 同文件"
                                 "重复，失败整体回滚，不改 docs/chunks/FTS/vec"),
        "idempotency": ("计划与生成时的库/源文件状态指纹绑定；执行后旧计划立即失效（指纹不一致"
                        "会被拒绝），必须重新生成计划，重新生成的计划为 no-op（0 分配 0 删除），"
                        "执行后状态稳定"),
    }


def _state_diff(plan: dict, db_path: Path, metrics_root: Path) -> list:
    """重算当前状态与计划指纹比对，返回差异描述（空 = 一致）。mtime 不参与比较（内容为准）。"""
    diffs = []
    fp = db_file_fingerprint(db_path)
    if fp["sha256"] != plan["db_fingerprint"]["sha256"]:
        diffs.append(f"db_file_sha256: 计划 {plan['db_fingerprint']['sha256'][:16]}…"
                     f" → 当前 {fp['sha256'][:16]}…")
    if (fp["wal_exists"] != plan["db_fingerprint"]["wal_exists"]
            or fp["shm_exists"] != plan["db_fingerprint"]["shm_exists"]):
        diffs.append(f"wal/shm 状态: 计划 wal={plan['db_fingerprint']['wal_exists']}"
                     f" shm={plan['db_fingerprint']['shm_exists']}"
                     f" → 当前 wal={fp['wal_exists']} shm={fp['shm_exists']}")
    conn = connect_ro(db_path)
    try:
        stats = table_stats(conn)
        other = other_tables_digest(conn)
    finally:
        conn.close()
    if stats["metrics_content_sha256"] != plan["metrics_content_sha256"]:
        diffs.append("metrics_content_sha256 变化")
    if stats["metrics_columns"] != plan["metrics_columns"]:
        diffs.append("metrics 表结构（列）变化")
    for k in ("docs", "chunks", "chunks_fts", "chunks_vec", "metrics"):
        if stats[k] != plan["table_row_counts"][k]:
            diffs.append(f"表行数变化: {k} 计划 {plan['table_row_counts'][k]} → 当前 {stats[k]}")
    if other != plan["other_tables"]:
        changed = [k for k in other if other[k] != plan["other_tables"].get(k)]
        diffs.append(f"其他表内容摘要变化: {sorted(changed)}")
    srcfp = source_fingerprint(metrics_root)
    if srcfp["aggregate_sha256"] != plan["source_aggregate_sha256"]:
        diffs.append(f"源文件聚合指纹: 计划 {plan['source_aggregate_sha256'][:16]}…"
                     f" → 当前 {srcfp['aggregate_sha256'][:16]}…")
    cur = {f["name"]: f["sha256"] for f in srcfp["files"]}
    old = {f["name"]: f["sha256"] for f in plan["source_files"]}
    if cur != old:
        changed = [n for n in set(cur) | set(old) if cur.get(n) != old.get(n)]
        diffs.append(f"源文件 hash 变化: {sorted(changed)}")
    return diffs


def ensure_source_file_column(conn: sqlite3.Connection) -> bool:
    """事务内显式迁移：metrics 缺 source_file 列则 ALTER 补列，返回是否执行了 ALTER。

    只加列：不归属、不删除、不 commit——由调用方的事务统一提交/回滚。幂等。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(metrics)")}
    if "source_file" in cols:
        return False
    conn.execute("ALTER TABLE metrics ADD COLUMN source_file TEXT")
    return True


def _post_execution_checks(conn: sqlite3.Connection, plan: dict, keep: dict) -> None:
    """提交前核验（写事务内）。任何一条不过 → ExecuteError → 整体回滚。"""
    cols, rows = _read_db_rows(conn)
    problems = []
    if "source_file" not in cols:
        problems.append("迁移后仍无 source_file 列")
    actual = {mid: sf for mid, _c, sf in rows}
    if len(rows) != len(plan["assignments"]):
        problems.append(f"行数 {len(rows)} != 目标保留 {len(plan['assignments'])}")
    if actual != keep:
        bad = sorted(set(actual) | set(keep), key=str)
        bad = [m for m in bad if actual.get(m) != keep.get(m)][:10]
        problems.append(f"保留集/归属不符: {bad}")
    if any(sf is None or sf == "" for _m, _c, sf in rows):
        problems.append("存在 source_file 为空的行")
    seen: dict = {}
    for mid, content, sf in rows:
        seen.setdefault((sf, content), []).append(mid)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    if dups:
        problems.append(f"存在 (source_file, 内容) 完全重复: {list(dups.items())[:3]}")
    other = other_tables_digest(conn)
    for k in ("docs", "chunks", "chunks_fts", "chunks_vec"):
        cur, was = other[k], plan["other_tables"].get(k)
        # 两侧都是字符串（absent/unavailable）视为不确定而非相等；确定值不同即拦截
        if cur != was and not (isinstance(cur, str) and isinstance(was, str)):
            problems.append(f"其他表被意外改动: {k}")
    if problems:
        raise ExecuteError("提交前核验失败，已回滚: " + "; ".join(problems))


def execute_plan(db_path: Path, metrics_root: Path, plan: dict, *, confirmed: bool = False) -> dict:
    """在【临时数据库】上执行已确认的计划。正式库路径无条件拒绝（本轮硬边界）。

    成功返回前后核算报告；ExecuteRefused 保证零写入；ExecuteError 已完整回滚。
    本函数没有 CLI 入口，只供测试在 tempfile 数据库上调用。"""
    db_path, metrics_root = Path(db_path), Path(metrics_root)
    if not confirmed:
        raise ExecuteRefused("execute_plan 需要显式 confirmed=True"
                             "（本轮仅临时库测试调用；正式库操作需后续批准）")
    _formal_db_guard(db_path, action="execute_plan")
    if plan.get("plan_version") != PLAN_VERSION or plan.get("code_version") != CODE_VERSION:
        raise ExecuteRefused("计划版本/代码版本不匹配，需用当前代码重新生成计划")
    if not plan.get("allowed_to_execute"):
        raise ExecuteRefused(f"计划存在阻断项，不允许执行: {plan.get('blockers')}")
    if Path(plan["db_path"]) != db_path.resolve():
        raise ExecuteRefused(f"计划绑定的是 {plan['db_path']}，与传入数据库 {db_path} 不符")
    if not db_path.is_file():
        raise ExecuteRefused(f"数据库不存在: {db_path}")  # 防止 connect 意外新建文件
    diffs = _state_diff(plan, db_path, metrics_root)
    if diffs:
        raise ExecuteRefused("状态指纹与计划不一致，拒绝执行（需重新生成计划）: "
                             + "; ".join(diffs))

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 事务内重读数据库与源文件、重算目标集并与计划精确比对（关闭 TOCTOU 窗口）
        cols, db_rows = _read_db_rows(conn)
        src_records, _src_errors = load_source_rows(metrics_root)
        assignments2, deletes2, blockers2 = _compute_targets(
            db_rows, _content_index(src_records))
        keep2 = {a["metric_id"]: a["source_file"] for a in assignments2}
        del2 = {d["metric_id"] for d in deletes2}
        plan_keep = {a["metric_id"]: a["source_file"] for a in plan["assignments"]}
        plan_del = {d["metric_id"] for d in plan["deletes"]}
        if blockers2 or keep2 != plan_keep or del2 != plan_del:
            conn.rollback()
            raise ExecuteRefused("事务内重算目标集与计划不一致，已回滚: "
                                 + "; ".join(blockers2 or ["目标集差异"]))
        if "source_file" not in cols:  # DDL 参与事务，失败随之回滚
            ensure_source_file_column(conn)
        conn.executemany(
            "UPDATE metrics SET source_file=? WHERE metric_id=?",
            [(a["source_file"], a["metric_id"]) for a in plan["assignments"]
             if a["action"] == "assign"])
        conn.executemany(
            "DELETE FROM metrics WHERE metric_id=?",
            [(d["metric_id"],) for d in plan["deletes"]])
        _post_execution_checks(conn, plan, keep2)
        conn.commit()
    except ExecuteRefused:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        if isinstance(e, ExecuteError):
            raise
        raise ExecuteError(f"执行失败，已回滚: {e}") from e
    finally:
        conn.close()
    return _execution_report(db_path, metrics_root, plan)


def _execution_report(db_path: Path, metrics_root: Path, plan: dict) -> dict:
    """执行成功后的前后核算（重新以只读连接打开统计）。"""
    conn = connect_ro(db_path)
    try:
        stats = table_stats(conn)
        other = other_tables_digest(conn)
        cols, rows = _read_db_rows(conn)
    finally:
        conn.close()
    src_records, _ = load_source_rows(metrics_root)
    c2f = _content_index(src_records)
    by_content: dict = {}
    for _m, c, _sf in rows:
        by_content[c] = by_content.get(c, 0) + 1
    combos = {(sf, c) for _m, c, sf in rows}
    cross_retained = sum(1 for files in c2f.values() if len(files) > 1)
    cross_ok = all(by_content.get(c, 0) == len(f) for c, f in c2f.items())
    return {
        "executed_at": datetime.now().isoformat(timespec="seconds"),
        "db_path": str(Path(db_path).resolve()),
        "plan_version": plan["plan_version"],
        "code_version": CODE_VERSION,
        "allocation_algorithm": plan["allocation_algorithm"],
        "assigned": sum(1 for a in plan["assignments"] if a["action"] == "assign"),
        "kept_existing": sum(1 for a in plan["assignments"] if a["action"] == "keep_existing"),
        "deleted": len(plan["deletes"]),
        "before": {"metrics_rows": plan["db_rows"],
                   "metrics_content_sha256": plan["metrics_content_sha256"],
                   "db_file_sha256": plan["db_fingerprint"]["sha256"],
                   "table_row_counts": plan["table_row_counts"],
                   "other_tables": plan["other_tables"]},
        "after": {"metrics_rows": stats["metrics"],
                  "metrics_content_sha256": stats["metrics_content_sha256"],
                  "db_file_sha256": db_file_fingerprint(db_path)["sha256"],
                  "table_row_counts": {k: stats[k] for k in
                                       ("docs", "chunks", "chunks_fts", "chunks_vec", "metrics")},
                  "other_tables": other,
                  "source_file_nonempty": sum(1 for _m, _c, sf in rows
                                              if sf not in (None, "")),
                  "distinct_source_file_content_combos": len(combos),
                  "same_file_duplicates": 0,  # _post_execution_checks 已保证
                  "cross_file_contents_retained": cross_retained,
                  "cross_file_retention_verified": cross_ok},
        "other_tables_unchanged": {k: other.get(k) == plan["other_tables"].get(k)
                                   for k in ("docs", "chunks", "chunks_fts", "chunks_vec")},
        "technical_assignment_note": TECH_ASSIGN_NOTE,
    }


def backup_database(db_path: Path, backup_path: Path) -> dict:
    """sqlite3 在线备份（Connection.backup()）。本轮仅限临时库：正式库路径（任一侧）拒绝。"""
    db_path, backup_path = Path(db_path), Path(backup_path)
    _formal_db_guard(db_path, action="backup（源）")
    _formal_db_guard(backup_path, action="backup（目标）")
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    if backup_path.resolve() == db_path.resolve():
        raise ExecuteRefused("备份目标不能是源数据库本身")
    if backup_path.exists():
        raise ExecuteRefused(f"备份目标已存在，拒绝覆盖: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return {"backup_path": str(backup_path), "source_path": str(db_path),
            "source_sha256": _sha256_file(db_path), "backup_sha256": _sha256_file(backup_path)}


def verify_backup(backup_path: Path, expected: dict | None = None) -> dict:
    """打开备份核验：PRAGMA integrity_check + 关键表统计；expected 提供则逐项比对。"""
    backup_path = Path(backup_path)
    conn = connect_ro(backup_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        stats = table_stats(conn)
        other = other_tables_digest(conn)
    finally:
        conn.close()
    result = {"backup_path": str(backup_path), "integrity_check": integrity,
              "table_stats": stats, "other_tables": other}
    if expected:
        diffs = []
        for k, want in expected.items():
            got = stats.get(k, other.get(k))
            if (isinstance(want, dict) and isinstance(got, dict)
                    and (want.get("sha256") != got.get("sha256")
                         or want.get("rows") != got.get("rows"))):
                diffs.append(k)
            elif got != want:
                diffs.append(k)
        result["expected_mismatches"] = diffs
        result["matches_expected"] = not diffs
    return result


def restore_database(backup_path: Path, target_path: Path, *, overwrite: bool = False) -> dict:
    """从备份恢复到目标路径（同样走 backup API）。正式库路径（任一侧）一律拒绝。"""
    backup_path, target_path = Path(backup_path), Path(target_path)
    _formal_db_guard(backup_path, action="restore（备份源）")
    _formal_db_guard(target_path, action="restore（目标）")
    if not backup_path.is_file():
        raise FileNotFoundError(f"备份不存在: {backup_path}")
    if target_path.exists() and not overwrite:
        raise ExecuteRefused(f"恢复目标已存在且未显式 overwrite=True: {target_path}")
    src = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target_path))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    check = verify_backup(target_path)
    return {"restored_to": str(target_path), "from": str(backup_path),
            "integrity_check": check["integrity_check"],
            "restored_metrics_rows": check["table_stats"]["metrics"]}


# ===================== Step 2B-2B-Preflight：正式预检 + 正式备份 =====================
#
# formal_backup 是唯一的正式库备份入口（"只读源 → 新备份目标"最小权限路径）：
# 不调用 execute_plan/迁移/回填/删除；formal_backup_confirmed 与 execute_plan 的
# confirmed 互不通用。CLI 仍无任何备份/写入子命令。

FORMAL_BACKUP_DISCLAIMER = ("本操作只创建正式数据库备份并验证，未修改正式库内容；"
                            "备份授权不构成正式迁移/回填/删除的写入授权。")

WRITER_SCRIPTS = ("ingest.py", "build_index.py", "load_external_metrics.py", "run_eval.py")


def check_processes() -> dict:
    """只读扫描进程表：Web 状态、四个写入脚本、其他可能触碰 rag.db 的进程。

    纯 ps 快照，不干预任何进程；Web 只读消费不算写入，但如实报告。
    返回结构恒定完整：任何路径（含 ps 扫描失败）都带 writer_running 派生键，
    调用方不应因缺键崩溃。"""
    out = {"web": {"running": False, "pid": None, "command": None},
           "writer_scripts": [], "other_db_related": [], "scan_error": None,
           "writer_running": False}
    try:
        res = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True,
                             text=True, timeout=15)
        lines = res.stdout.splitlines()[1:]
    except Exception as e:  # ps 不可用时如实报告，不假装扫描过
        out["scan_error"] = str(e)
        return out
    me = os.getpid()
    for line in lines:
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, cmd = int(parts[0]), parts[1]
        if pid == me or "ps -axo" in cmd:
            continue
        if "web/app.py" in cmd and not out["web"]["running"]:
            out["web"] = {"running": True, "pid": pid, "command": cmd[:200]}
        for name in WRITER_SCRIPTS:
            if name in cmd:
                out["writer_scripts"].append({"name": name, "pid": pid, "command": cmd[:200]})
        if "rag.db" in cmd and "web/app.py" not in cmd:
            out["other_db_related"].append({"pid": pid, "command": cmd[:200]})
    out["writer_running"] = bool(out["writer_scripts"])
    return out


def formal_backup(db_path: Path, backup_dir: Path, *, formal_backup_confirmed: bool = False,
                  timestamp: str | None = None) -> dict:
    """正式数据库备份（唯一正式备份入口）。只备份 + 验证，不做任何迁移/回填/删除。

    - 必须显式 formal_backup_confirmed=True（该确认与 execute_plan 的 confirmed
      互不通用，不能给 execute_plan 带来任何权限）；
    - 源以 mode=ro 打开，备份全程不写源库；
    - 目标必须是 backup_dir 下不存在的新文件（同秒冲突直接拒绝，不覆盖、
      不自造唯一名）；目标不得是源库或正式库的本体/硬链接/软链接；
    - 备份后立即验证：integrity_check、关键表行数、metrics schema 与内容摘要、
      docs/chunks/FTS/vec 内容摘要（vec 含 embedding 原始字节）与源库比对；
      验证失败保留失败备份文件并在返回值标记不可用，不删除现场。"""
    db_path, backup_dir = Path(db_path), Path(backup_dir)
    if not formal_backup_confirmed:
        raise ExecuteRefused("formal_backup 需要显式 formal_backup_confirmed=True"
                             "（本轮 2B-2B-Preflight 仅授权备份+验证，不授权任何写入）")
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"rag-2b2-preflight-{ts}.db"
    if target.exists():
        raise ExecuteRefused(f"备份目标已存在，拒绝覆盖: {target}")
    _formal_db_guard(target, action="formal_backup（目标）")  # 目标不能是正式库/其链接
    if _is_same_file(target, db_path):
        raise ExecuteRefused(f"备份目标不能是源数据库（含硬/软链接）: {target}")

    src_fp_before = db_file_fingerprint(db_path)
    conn = connect_ro(db_path)
    try:
        src_stats = table_stats(conn)
        src_other = other_tables_digest(conn)
    finally:
        conn.close()
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    problems = []
    v = None
    try:
        v = verify_backup(target)
        if v["integrity_check"] != "ok":
            problems.append(f"integrity_check: {v['integrity_check']}")
        for k in ("docs", "chunks", "chunks_fts", "chunks_vec", "metrics"):
            if v["table_stats"][k] != src_stats[k]:
                problems.append(f"表行数不一致: {k} 源={src_stats[k]} 备份={v['table_stats'][k]}")
        if v["table_stats"]["metrics_columns"] != src_stats["metrics_columns"]:
            problems.append("metrics schema（列）不一致")
        if v["table_stats"]["metrics_content_sha256"] != src_stats["metrics_content_sha256"]:
            problems.append("metrics 内容摘要不一致")
        for k in ("docs", "chunks", "chunks_fts", "chunks_vec"):
            if v["other_tables"][k] != src_other[k]:
                problems.append(f"{k} 内容摘要不一致（含 embedding，若适用）")
    except Exception as e:
        problems.append(f"备份验证异常: {e}")
    src_fp_after = db_file_fingerprint(db_path)
    for k in ("sha256", "size", "mtime", "inode", "wal_exists", "shm_exists"):
        if src_fp_after[k] != src_fp_before[k]:
            problems.append(f"源数据库在备份期间发生变化: {k}")
    # embedding 可验证性：不可验证时不声称"备份已充分验证"（预检据此阻断正式写入）
    src_vec = src_other["chunks_vec"]
    bak_vec = v["other_tables"]["chunks_vec"] if v else None
    warnings = []
    if src_vec == "absent":
        embedding_verified = None  # 无向量表，不适用
    else:
        embedding_verified = isinstance(src_vec, dict) and src_vec == bak_vec
        if not embedding_verified:
            warnings.append("chunks_vec embedding 内容不可验证：不得声明备份已充分验证，"
                            "不进入任何正式写入阶段")
    keys = ("docs", "chunks", "chunks_fts", "chunks_vec", "metrics",
            "metrics_columns", "metrics_content_sha256", "journal_mode")
    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "embedding_verified": embedding_verified,
        "disclaimer": FORMAL_BACKUP_DISCLAIMER,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(db_path),
        "source_fingerprint": src_fp_before,
        "source_after_fingerprint": src_fp_after,
        "backup_path": str(target),
        "backup_fingerprint": db_file_fingerprint(target),
        "integrity_check": v["integrity_check"] if v else "not checked",
        "source_table_stats": {k: src_stats[k] for k in keys},
        "backup_table_stats": ({k: v["table_stats"][k] for k in keys} if v else None),
        "source_other_tables": src_other,
        "backup_other_tables": (v["other_tables"] if v else None),
        "note": ("备份文件 sha256 与源库文件 sha256 不要求相等（SQLite 页面布局可不同），"
                 "一致性以内容摘要与关键表行数为准。"),
    }


def run_preflight(db_path: Path, metrics_root: Path, backup_dir: Path | None = None, *,
                  formal_backup_confirmed: bool = False,
                  plan_report_path: Path | None = None) -> dict:
    """正式预检：指纹 → 进程 → 计划 → （可选）备份+验证 → 复核指纹 → 报告。

    只读 + 备份，不做任何迁移/回填/删除。发现写入进程时跳过备份并记为阻断。
    返回完整预检报告 dict（调用方负责写盘与人工审阅）。"""
    db_path, metrics_root = Path(db_path), Path(metrics_root)
    blockers: list[str] = []

    db_fp_before = db_file_fingerprint(db_path)
    conn = connect_ro(db_path)
    try:
        stats = table_stats(conn)
        other = other_tables_digest(conn)
    finally:
        conn.close()
    srcfp = source_fingerprint(metrics_root)
    src_records, src_errors = load_source_rows(metrics_root)

    proc = check_processes()
    # 稳健兜底：不直接索引派生键，缺失时退回 writer_scripts 推导
    writer_running = proc.get("writer_running", bool(proc.get("writer_scripts") or []))
    if writer_running:
        blockers.append(f"writer_process_running: 检测到写入进程 "
                        f"{[w.get('name') for w in proc.get('writer_scripts', [])]}，"
                        "停止创建备份，等待用户处理")
    if src_errors:
        blockers.append(f"source_file_error: {sorted(src_errors)}")

    plan = build_plan(db_path, metrics_root)
    blockers.extend(plan["blockers"])
    if isinstance(other["chunks_vec"], str) and other["chunks_vec"] != "absent":
        blockers.append(f"chunks_vec_unverifiable: {other['chunks_vec']}；"
                        "备份验证不得声明已充分验证 embedding，不进入正式写入阶段")

    backup_report = None
    if backup_dir is not None and formal_backup_confirmed:
        if writer_running:
            backup_report = {"ok": False, "skipped": "检测到写入进程，未创建备份"}
        else:
            backup_report = formal_backup(db_path, backup_dir,
                                          formal_backup_confirmed=True)
            if not backup_report["ok"]:
                blockers.append(f"backup_failed: {backup_report['problems']}"
                                "（失败备份文件已保留，标记不可用）")

    db_fp_after = db_file_fingerprint(db_path)
    srcfp_after = source_fingerprint(metrics_root)
    fp_diff = []
    for k in ("sha256", "size", "mtime", "inode", "device", "wal_exists", "shm_exists"):
        if db_fp_after[k] != db_fp_before[k]:
            fp_diff.append(f"db.{k}: {db_fp_before[k]} → {db_fp_after[k]}")
    if srcfp_after["aggregate_sha256"] != srcfp["aggregate_sha256"]:
        fp_diff.append(f"metrics.aggregate_sha256 变化")
    if fp_diff:
        blockers.append(f"fingerprint_changed_during_preflight: {fp_diff}")

    if plan_report_path is not None:
        _write_report(plan, plan_report_path, db_path, metrics_root)

    data_dir = db_path.parent
    strays = sorted(p.name for p in data_dir.glob("*")
                    if p.name != db_path.name and not p.is_dir()
                    and any(t in p.name.lower() for t in ("-wal", "-shm", "-journal", ".tmp")))
    return {
        "task": "Step 2B-2B-Preflight（正式预检 + 正式备份，只读+备份，无任何正式写入）",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "code_version": CODE_VERSION,
        "plan_version": PLAN_VERSION,
        "db": {
            "path": str(db_path.resolve()),
            "exists": db_path.is_file(),
            "fingerprint_before": db_fp_before,
            "fingerprint_after": db_fp_after,
            "journal_mode": stats["journal_mode"],
            "data_dir_stray_journal_files": strays,
            "table_row_counts": {k: stats[k] for k in
                                 ("docs", "chunks", "chunks_fts", "chunks_vec", "metrics")},
            "metrics_schema": stats["metrics_columns"],
            "metrics_has_source_file_column": stats["metrics_has_source_file_column"],
            "metrics_content_sha256": stats["metrics_content_sha256"],
            "other_tables_digest": other,
        },
        "sources": {
            "metrics_root": str(metrics_root.resolve()),
            "file_count": srcfp["file_count"],
            "aggregate_sha256_before": srcfp["aggregate_sha256"],
            "aggregate_sha256_after": srcfp_after["aggregate_sha256"],
            "files": srcfp["files"],
            "parse_errors": src_errors,
            "converted_record_count": sum(len(recs) for recs in src_records.values()),
        },
        "processes": proc,
        "plan": {
            "path": str(Path(plan_report_path).resolve()) if plan_report_path else None,
            "plan_version": plan["plan_version"],
            "code_version": plan["code_version"],
            "allocation_algorithm": plan["allocation_algorithm"],
            "generated_at": plan["generated_at"],
            "db_sha256": plan["db_fingerprint"]["sha256"],
            "db_inode": plan["db_fingerprint"]["inode"],
            "db_device": plan["db_fingerprint"]["device"],
            "source_aggregate_sha256": plan["source_aggregate_sha256"],
            "data_stats": plan["data_stats"],
            "targets": plan["targets"],
            "blockers": plan["blockers"],
            "allowed_to_execute": plan["allowed_to_execute"],
            "technical_assignment_note": plan["technical_assignment_note"],
            "transaction_strategy": plan["transaction_strategy"],
            "idempotency": plan["idempotency"],
        },
        "backup": backup_report,
        "fingerprint_diff": fp_diff,
        "blockers": blockers,
        "ready_for_next_step": not blockers and (backup_report is None or backup_report["ok"]),
        "declarations": {
            "no_formal_migration": True,
            "no_source_file_backfill": True,
            "no_metrics_deletion": True,
            "no_docs_chunks_fts_vec_modification": True,
            "no_source_file_modification": True,
            "no_external_api_calls": True,
        },
        "disclaimer": ("allowed_to_execute=True 只表示当前目标集可计算，不表示本轮获得"
                       "正式写入授权；正式迁移/回填/删除需后续单独批准。"
                       + (" " + FORMAL_BACKUP_DISCLAIMER if backup_report else "")),
    }


def render_plan_summary(p: dict) -> str:
    t = p["targets"]
    lines = [
        "== Step 2B-2A 执行计划（只读生成，未修改任何数据）==",
        f"计划版本: {p['plan_version']}  代码版本: {p['code_version']}  生成时间: {p['generated_at']}",
        f"数据库: {p['db_path']}（sha256={p['db_fingerprint']['sha256'][:16]}…，metrics {p['db_rows']} 行）",
        f"源文件: {len(p['source_files'])} 个，聚合 sha256={p['source_aggregate_sha256'][:16]}…",
        f"目标: 保留 {t['keep']}（技术分配 {t['technical_assignments']} + 已有一致归属 "
        f"{t['keep_existing']}），删除 {t['delete']}，跨文件保留组 {t['cross_file_groups_retained']}",
        f"事务策略: {p['transaction_strategy']}",
        f"是否允许执行: {p['allowed_to_execute']}",
    ]
    if p["blockers"]:
        lines.append(f"阻断项 {len(p['blockers'])} 个:")
        lines.extend(f"  - {b}" for b in p["blockers"][:10])
        if len(p["blockers"]) > 10:
            lines.append(f"  …另有 {len(p['blockers']) - 10} 个，见完整报告")
    lines.append(f"技术分配说明: {p['technical_assignment_note']}")
    lines.append("执行入口: 本 CLI 只读；执行需在代码中显式调用 execute_plan(confirmed=True)，"
                 "正式库路径被无条件拒绝（Step 2B-2B 起另行授权）。")
    return "\n".join(lines)


def _fmt_accounting(acc: dict) -> str:
    lines = []
    for k, entry in acc.items():
        if k == "formula":
            continue
        v, origin = entry
        lines.append(f"  {k}: {v}  [{origin}]")
    lines.append(f"  核算公式: {acc['formula']}")
    return "\n".join(lines)


def render_preview_summary(r: dict) -> str:
    m = r["matching"]
    return f"""== Step 2B-1 只读预览（未修改任何数据）==
时间: {r['generated_at']}
DB: {r['db_path']}
metrics 根目录: {r['metrics_root']}
DB 文件: size={r['db_file_fingerprint']['size']} sha256={r['db_file_fingerprint']['sha256'][:16]}…
表行数: docs={r['table_stats']['docs']} chunks={r['table_stats']['chunks']} fts={r['table_stats']['chunks_fts']} vec={r['table_stats']['chunks_vec']} metrics={r['table_stats']['metrics']}
源文件: {r['source_fingerprint']['file_count']} 个，聚合 sha256={r['source_fingerprint']['aggregate_sha256'][:16]}…
source_file 列: {'存在' if r['table_stats']['metrics_has_source_file_column'] else '不存在（未迁移）'}；既有归属: {m['existing_source_file']}
匹配总览: 唯一匹配内容 {m['unique_match_contents']} 种 / 多文件匹配内容 {m['ambiguous_match_contents']} 种 / DB 未匹配内容 {m['unmatched_db_contents']} 种 / 源文件错误 {len(m['source_file_errors'])} 个
重复总览: 完全重复组 {r['accounting']['exact_dup_groups'][0]}，多余行 {r['accounting']['exact_dup_extra_rows'][0]}；同文件内重复组 {len(r['within_file_duplicate'])}；跨文件相同内容组 {len(r['cross_file_same_content'])}
未匹配: DB 侧 {r['accounting']['db_rows_unmatched'][0]} 行；源文件侧 {r['accounting']['source_rows_unmatched'][0]} 行；异常 {len(r['anomalies'])} 个
核算:
{_fmt_accounting(r['accounting'])}
免责声明: {r['disclaimer']}"""


def _write_report(report: dict, path: Path, db_path: Path, metrics_root: Path) -> None:
    path = Path(path)
    resolved = path.resolve()
    if resolved == Path(db_path).resolve():
        raise SystemExit("报告路径不能覆盖数据库文件")
    if resolved.is_relative_to(Path(metrics_root).resolve()):
        raise SystemExit("报告路径不能写入 metrics 源文件目录")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="metrics 历史数据只读核对与预览（无任何写操作）")
    ap.add_argument("command", choices=READONLY_SUBCOMMANDS)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--metrics-root", type=Path, default=METRICS_DIR)
    ap.add_argument("--report", type=Path, default=None,
                    help="完整 JSON 报告/计划输出路径（仅 preview/plan；不覆盖 DB/源文件）")
    args = ap.parse_args(argv)

    if args.command == "preview":
        report = build_report(args.db, args.metrics_root)
        print(render_preview_summary(report))
        if args.report:
            _write_report(report, args.report, args.db, args.metrics_root)
            print(f"完整报告已写入: {args.report}")
        return 0

    if args.command == "plan":  # 只读：生成执行计划，不执行任何写入
        plan = build_plan(args.db, args.metrics_root)
        print(render_plan_summary(plan))
        if args.report:
            _write_report(plan, args.report, args.db, args.metrics_root)
            print(f"执行计划已写入: {args.report}")
        return 0 if plan["allowed_to_execute"] else 1

    conn = connect_ro(args.db)
    try:
        if args.command == "fingerprint":
            print(json.dumps({"db_file": db_file_fingerprint(args.db),
                              "tables": table_stats(conn),
                              "source_aggregate": source_fingerprint(args.metrics_root)["aggregate_sha256"]},
                             ensure_ascii=False, indent=1))
        elif args.command == "inspect-db":
            st = table_stats(conn)
            cols = st["metrics_columns"]
            ci = {c: i for i, c in enumerate(cols)}
            groups: dict[tuple, int] = {}
            for row in conn.execute("SELECT * FROM metrics"):
                key = tuple(row[ci[c]] for c in BUSINESS_COLS)
                groups[key] = groups.get(key, 0) + 1
            dups = {k: v for k, v in groups.items() if v > 1}
            print(f"metrics={st['metrics']} 不同内容={len(groups)} "
                  f"完全重复组={len(dups)} 多余行={sum(v - 1 for v in dups.values())}")
            print(f"source_file 列: {'存在' if st['metrics_has_source_file_column'] else '不存在'}")
        elif args.command == "inspect-source":
            sf = source_fingerprint(args.metrics_root)
            recs, errors = load_source_rows(args.metrics_root)
            total = sum(len(v) for v in recs.values())
            print(f"源文件 {sf['file_count']} 个，记录 {total} 条，"
                  f"聚合 sha256={sf['aggregate_sha256'][:16]}…，解析失败文件 {len(errors)} 个")
            for name, err in errors.items():
                print(f"  解析失败: {name}: {err}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
