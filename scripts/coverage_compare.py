"""Step 3C：城市横向比较 CLI（只读）。

同指标跨城（杭州/上海/北京/深圳/全国等）原始点并列：各城市来源、缺失期、
共同覆盖期（交集）与可比性状态。只描述数据条件与原始差异，不计算差值/
倍数/排名/涨跌幅/相关性，不判断城市优劣，不做市场阶段或购房建议。

只读保证：逐城 mode=ro 连接（lib_metrics_query 的 _connect_readonly），不建表、
不迁移、不 commit，不调用任何外部 API。--report 路径保护复用 coverage_report
的 _resolve_report_path：不得覆盖数据库、不得写入 metrics/ 源目录。

用法：
  .venv/bin/python scripts/coverage_compare.py --cities 杭州,上海,北京,深圳 --metric secondary_volume_units
  .venv/bin/python scripts/coverage_compare.py --cities 杭州,上海 --metric avg_price --period-start 2026-02 --period-end 2026-03
  .venv/bin/python scripts/coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --actual-only
  .venv/bin/python scripts/coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --institution 贝壳
  .venv/bin/python scripts/coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --json
  .venv/bin/python scripts/coverage_compare.py --cities 杭州,上海 --metric secondary_volume_units --report /absolute/path/report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_metrics_query as mq  # noqa: E402
from coverage_report import _resolve_report_path  # noqa: E402  复用同一路径保护
from lib_paths import DB  # noqa: E402


def _render_text(rep: dict) -> str:
    s = rep["summary"]
    lines = [
        "城市横向比较报告（只读；仅描述数据条件与原始差异，不含强弱/趋势/市场判断）",
        f"筛选: {json.dumps(rep['filters'], ensure_ascii=False)}",
        ("请求窗口: " + f"{rep['requested_periods'][0]} ~ {rep['requested_periods'][-1]}"
         f"（{len(rep['requested_periods'])} 期）") if rep["requested_periods"] else "请求窗口: 无",
        f"总体状态: {s['overall_status']}；比较组 {s['group_count']} 个"
        f"（严格可比 {s['comparable_group_count']} / limited 并列 {s['limited_group_count']}）",
    ]
    if s.get("excluded_cities"):
        lines.append(f"已排除输入: {s['excluded_cities']}（空城市不纳入比较）")
    if s.get("uncovered_cities"):
        lines.append(f"无数据城市（uncovered_city）: {s['uncovered_cities']}（不以其他城市代替）")
    if s.get("status_flags"):
        lines.append(f"状态标记: {','.join(s['status_flags'])}")
    lines.append("")
    for i, g in enumerate(rep["comparison_groups"], 1):
        k = g["comparison_key"]
        lines.append(
            f"比较组 {i}: comparability={g['comparability']}"
            f"（segment={k['segment']} unit={k['unit']} comparison_type={k['comparison_type']}"
            f" is_forecast={k['is_forecast']} 粒度={k['period_granularity']}）")
        for c in g["cities"]:
            line = (f"  - {c['city_label']} [{c['institution']}"
                    f"{('/' + c['source_file']) if c['source_file'] else ''}] "
                    f"{len(c['observed_periods'])} 期: {','.join(c['observed_periods'])}")
            if c["missing_periods"]:
                line += f"；缺失: {','.join(c['missing_periods'])}"
            if c["conflicts"]:
                line += f"；冲突期: {','.join(x['period'] for x in c['conflicts'])}"
            lines.append(line)
        if g["common_periods"]:
            lines.append(f"  共同覆盖期（{g['common_period_count']}）: {','.join(g['common_periods'])}")
        elif "single_city_only" not in g["status_flags"]:
            lines.append("  共同覆盖期: 无")
        if g["partially_covered_periods"]:
            lines.append(f"  仅部分城市覆盖: {','.join(g['partially_covered_periods'])}")
        if g["insufficient_common_periods"]:
            lines.append("  共同覆盖期 < 2：不足以支持时间变化比较（仅展示原始点）")
        if g["status_flags"]:
            lines.append(f"  标记: {','.join(g['status_flags'])}")
        for w in g["warnings"]:
            lines.append(f"  warning: {w}")
        lines.append("")
    if rep["warnings"]:
        lines.append("Warnings:")
        lines += [f"  - {w}" for w in rep["warnings"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="城市横向比较（只读）：原始点并列 + 共同覆盖期 + 可比性，不做强弱判断")
    ap.add_argument("--cities", required=True, help="城市列表，逗号分隔（如 杭州,上海,北京,深圳）")
    ap.add_argument("--metric", dest="metric_name", required=True,
                    help="指标名精确匹配（如 secondary_volume_units）")
    ap.add_argument("--institution", help="机构精确匹配（如 贝壳）")
    ap.add_argument("--source-file", help="来源文件精确匹配（如 beike.csv）")
    ap.add_argument("--segment", help="segment 精确匹配（如 secondary_home）")
    ap.add_argument("--unit", help="单位精确匹配（如 套）")
    ap.add_argument("--comparison-type", help="比较口径精确匹配（如 yoy）")
    ap.add_argument("--periods", help="时期列表，逗号分隔（需同粒度）")
    ap.add_argument("--period-start", help="时期范围起点（需与 --period-end 同粒度）")
    ap.add_argument("--period-end", help="时期范围终点")
    ap.add_argument("--actual-only", action="store_true",
                    help="只比较实际观测（默认行为，显式声明用）")
    ap.add_argument("--include-forecasts", action="store_true",
                    help="纳入预测：预测只和预测比较，单独成组并 warning")
    ap.add_argument("--db", default=str(DB), help="数据库路径（默认 data/rag.db，只读打开）")
    ap.add_argument("--report", help="把 JSON 报告写入该路径（不得覆盖数据库或写入 metrics/）")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON 而非文本摘要")
    args = ap.parse_args(argv)

    if args.actual_only and args.include_forecasts:
        ap.error("--actual-only 与 --include-forecasts 互斥")

    cities = [c.strip() for c in args.cities.split(",") if c.strip()]
    if len(cities) < 2:
        ap.error("--cities 至少需要 2 个城市")
    periods = None
    if args.periods:
        periods = [p.strip() for p in args.periods.split(",") if p.strip()]
        if not periods:
            ap.error("--periods 解析后为空")

    db_path = Path(args.db).expanduser()
    if not db_path.is_absolute():
        db_path = (Path.cwd() / db_path).resolve()
    if not db_path.exists():
        ap.error(f"数据库不存在: {db_path}")

    report_path = None
    if args.report:
        try:
            report_path = _resolve_report_path(args.report, db_path)
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 2

    rep = mq.compare_metric_across_cities(
        cities=cities, metric_name=args.metric_name, institution=args.institution,
        source_file=args.source_file, segment=args.segment, unit=args.unit,
        comparison_type=args.comparison_type, periods=periods,
        period_start=args.period_start, period_end=args.period_end,
        include_forecasts=args.include_forecasts, db_path=db_path,
    )

    payload = json.dumps(rep, ensure_ascii=False, indent=2)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload + "\n", encoding="utf-8")
        print(f"报告已写入: {report_path}")
    if args.json or not report_path:
        print(payload if args.json else _render_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
