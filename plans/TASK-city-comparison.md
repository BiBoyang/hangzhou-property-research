# TASK-city-comparison：杭州与北上深横向比较

## Step 3C（2026-09-08，方案 A）：城市比较层 + 只读 CLI

### 范围

在 Step 3A `query_metric_series()` 之上增加城市横向比较：杭州与上海、北京、
深圳、全国之间的同指标原始点并列，输出来源、缺失期、共同覆盖期与可比性
状态。只描述数据条件和原始差异，不判断城市强弱、不做趋势/相关性/领先滞后/
价格预测/市场阶段/购房建议。不进入 3D/3E。

### 修改文件

- `scripts/lib_metrics_query.py`：新增 `compare_metric_across_cities()`（结构化
  核心接口）、`compare_metric_across_cities_nl()`（薄 NL 适配，城市 <2 或指标
  命中 ≠1 时不查询）、内部工具 `_cmp_group_key` / `_series_city_entry` /
  `_build_comparison_group` 与常量 `_STRICT_FIELDS` / `_RELAXED_FIELDS`。
  不改 `query_metrics()` / `query_metric_series()` 既有语义。
- `scripts/coverage_compare.py`（新增，只读 CLI）：`--cities`（≥2，逗号分隔，
  必填）/ `--metric`（必填）/ `--institution` / `--source-file` / `--segment` /
  `--unit` / `--comparison-type` / `--periods` / `--period-start` + `--period-end` /
  `--actual-only`（默认行为，与 `--include-forecasts` 互斥）/ `--db` / `--report` /
  `--json`；报告路径保护复用 `coverage_report._resolve_report_path`（不修改该文件，
  仅 import）；项目外 cwd 可运行。
- `tests/test_coverage_compare.py`（新增，27 项，覆盖派发清单 33 类）。
- `tests/test_metrics_query.py`：未改动。
- `README.md`：新增"城市横向比较（Step 3C）"一节。
- `plans/TASK-city-comparison.md`：本文件。

### 语义要点

- 严格比较组键：`(institution, source_file, segment, unit, comparison_type,
  is_forecast, period_granularity)`；松弛键去掉前两个。每个松弛键下：有严格组
  覆盖 ≥2 城 → 各严格组各自 `comparable`；无严格组跨城但聚合 ≥2 城 → 并列
  `limited`（不计算差值/倍数/排名/方向）；仅 1 城 → `single_city_only`。
- 共同覆盖期 = 各城观测期交集（冲突期计为已覆盖）；`<2` →
  `insufficient_common_periods=true` + warning；`partially_covered_periods`
  记录仅部分城市覆盖的时期。
- 状态枚举：`comparable` / `limited` / `insufficient_common_periods` /
  `uncovered_city` / `unmatched_metric` / `conflicted` / `mixed_granularity`，
  只表示数据条件。组级：comparability + status_flags；全局：
  summary.status_flags + overall_status。
- 冲突全保留（不平均/不取第一条/不丢弃），组与全局各标 conflicted。
- 预测：默认 `include_forecasts=False`（SQL 层过滤 + warning）；显式开启后
  预测单独成组、只和预测比较。
- 空城市输入排除 + warning；`UNSUPPORTED_CITIES` 与无数据城市列入
  `uncovered_cities`，不用全国或其他城市代替。
- 粒度：月/季/半年/年严格隔离，请求混合粒度拒绝 + warning；结果跨组粒度
  不同时全局标 `mixed_granularity`（组内始终一致）。

### 验证

- 测试：`.venv/bin/python -m unittest discover -s tests`，183 项全绿
  （0 失败/0 错误/0 跳过）= 既有 156 项原样回归 + 新增 27 项。
- 正式库只读（cwd=/tmp，均 overall=ok）：
  - `杭州,上海,北京,深圳 secondary_volume_units`：19 组（严格 18 / limited 1），
    flags mixed_granularity/conflicted/insufficient_common_periods；大摩同文件
    四城月度 2026-01~05 全交集可比；克而瑞 H1 三城并列 limited。
  - `杭州,上海,北京,深圳 avg_price`：15 组（10/5）。
  - `杭州,上海,北京 yoy_change_pct`：15 组（15/0）。
  - `杭州,上海,北京,深圳 rent_level`：2 组（2/0）。
- 指纹：data/rag.db size 23244800 / mtime 1788821905.4951766 /
  sha256 1e46e016…094a19fb 前后不变；无 WAL/SHM；metrics/ 61 文件聚合 hash
  89d2b0b4…2607cd 前后不变；正式写入 0；外部 API 调用 0；未运行
  ingest / build_index / load_external_metrics / run_eval。

### 已知边界（留给后续 Step）

- limited 组把不同来源并列展示但不计算任何派生指标；选择"哪个来源更可信"
  不在本层（也不计划在后续自动选择）。
- 同一松弛键下已有跨城严格组时，其余单城严格组保留为 single_city_only，
  不再并入 limited（严格优先）。
- 共同覆盖期交集在 limited 组内按城市并集计算（同城多来源先并后交），
  仅用于展示，不代表口径已对齐。
- 3D 市场阶段、3E 看板未做。
