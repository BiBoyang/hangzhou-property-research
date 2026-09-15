# TASK-series-query：多来源时间序列查询

## Step 3A（2026-09-08）：时间序列查询后端 + 本地测试

### 范围

在 Step 2B-2 完成的 1477 条已归属 metrics 上，建立多来源时间序列查询能力。
本轮只实现查询与本地测试；不进入 3B/3C/3D/3E，不动 Web、不做市场阶段判断。

### 修改文件

- `scripts/lib_metrics_query.py`：新增 `query_metric_series()`（结构化时间序列
  查询）、`query_metric_series_nl()`（薄 NL 适配层）、`_connect_readonly()`
  （mode=ro 只读连接）与时期粒度工具（`_period_granularity` /
  `_normalize_period` / `_expand_period_range` / `_resolve_requested_periods`）。
  复用既有 `parse_periods()` / `parse_years()` / `_period_sort_key()` /
  `CITIES` / `METRIC_KEYWORDS`；`query_metrics()` 既有行为不变。
- `tests/test_metrics_query.py`：新增 35 项测试（100 → 135），既有测试未删改。
- `README.md`：新增"多来源时间序列查询（Step 3A）"契约与边界一节。
- `plans/TASK-series-query.md`：本文件。

### 查询语义要点

- 分组键：`(institution, source_file, city, metric_name, segment, unit,
  comparison_type, is_forecast, period_granularity)`；不平均、不取舍、不合并。
- 同组同期多值 → 移入 `conflicts` + warning；该期不在 `points` 但计为已覆盖
  （`missing_periods` 不含它）；此约定已写入 README 与测试。
- 范围按粒度展开（月/季/半年/年各自展开，上限 240 期）；粒度混合的请求拒绝
  并 warning；裸年份不展开为月度；小写 `2026q1` 归一为 `2026Q1`。
- `include_forecasts=False` 只回实际观测并 warning；`True` 时预测单独成组。
- 空城市不自动归属；`city=None` 不过滤，空城市带 `city_label="未归一化城市"`。
- 旧 schema（无 source_file 列）降级 `source_file=None`；给了 source_file 筛选
  则返回空结果 + warning；绝不迁移临时库。
- 只读连接 `file:...?mode=ro`，不经 `lib_db.connect()`，无 schema/commit；
  SQL 全参数化；Python 中分组，不用 AVG/MIN/MAX/DISTINCT 抹掉多值。
- `coverage.missing_period_count` 语义：所有 series 都未覆盖的请求时期数
  （并集口径）；`coverage.conflict_points` 单列冲突点数。

### 验证

- 测试：`.venv/bin/python -m unittest discover -s tests -v`，135 项全绿
  （0 失败 / 0 错误 / 0 跳过），原 100 项回归通过，新增 35 项。
- 正式库只读：`杭州 secondary_volume_units`（6 条 series / 16 点，贝壳与
  Morgan Stanley、中指云各自成组）与 `杭州 inventory_months`（3 条 series，
  克而瑞三个来源文件分开）可运行；杭州贝壳同文件因 segment/口径差异分成
  多条 series（符合分组规则）。
- 指纹：data/rag.db size 23244800 / mtime 1788821905 /
  sha256 1e46e016…094a19fb 前后不变；无 WAL/SHM；metrics/ 聚合 hash
  b3ed8d31…587fbb 不变；正式写入 0；外部 API 调用 0；未运行
  ingest / build_index / load_external_metrics / run_eval。

### 已知边界（留给后续 Step）

- 时间序列覆盖不完整，单点不伪装连续序列；source_file 只代表本地文件对应，
  不代表机构口径已核实或来源独立性；空城市 251 条未归一。
- 3A 不做"方向一致/趋势改善/市场企稳"等结论（3B/3D），不做购房建议。
