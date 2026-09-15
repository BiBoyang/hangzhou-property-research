# TASK-overview-dashboard：杭州市场总览看板

## Step 3E（2026-09-08）：/overview 总览页 + 4 个只读 API

### 范围

在 Step 3A `query_metric_series()`、3B `summarize_series_coverage()`、3C
`compare_metric_across_cities()`、3D `assess_market_phase()` 之上，新增独立
`/overview` 页面与 4 个只读 API，让用户打开总览页即可看到杭州当前市场证据：
阶段、置信度、数据截止期、规则版本、支持/反向证据、风险信号、核心指标多来源
趋势、城市比较、覆盖缺口与观察项。页面只展示事实、规则结果与风险提示，不输出
购房结论；不替换现有 `/` 问答首页。

### 修改文件

- `web/app.py`：新增 `GET /overview`（页面，默认 city=杭州，Jinja 传固定城市表）
  与 `GET /api/overview` / `/api/overview/series` / `/api/overview/coverage` /
  `/api/overview/compare`。参数校验（`_clean_city` / `_clean_metric` /
  `_clean_as_of` / `_clean_window` / `_clean_cities`，非法 → 422）；下游错误 →
  结构化 500（traceback 只进服务端日志）；只读查询库经模块变量 `OVERVIEW_DB`
  （默认 `lib_paths.DB`，测试可替换）。`as_of` 截断复用 `market_phase` 的
  `_filter_by_as_of` / `_filter_coverage_by_as_of` / `_filter_compare_group_by_as_of`，
  不重复实现第二套快照逻辑；series/compare 结果的覆盖计数与状态位随截断重算。
- `web/templates/overview.html`（新增）：市场阶段卡（保守措辞 + 置信度同显）、
  支持/反向证据（`<details>` 展开到 institution/source_file/source_quote）、
  风险信号四色卡（红=实际反向 / 黄=数据不足·来源冲突·limited / 灰=指标未覆盖 /
  蓝=观察项）、核心指标趋势（来源分线 + `connectNulls:false` 断线 + x 轴缺失期
  占位 `expandAxis`（只补类别不补数据）+ 冲突红菱散点 + 全量表格）、城市比较
  （严格可比组进主图，limited 只进并列区表格）、覆盖与观察项。全部数据库文本经
  `esc()` 转义后才进 innerHTML；ECharts 5.5.1 固定在 `web/static/echarts.min.js`，
  不可用时自动降级为表格 + 状态卡。
- `web/static/echarts.min.js`（新增，1,030,855 字节，sha256
  `e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf`，Apache
  ECharts 5.5.1 官方 dist）：本地固定资源，页面不依赖外部 CDN；下载为一次性
  静态资源获取（非模型 API，应用 API 调用仍为 0）。
- `web/templates/ask.html` / `metrics.html` / `docs.html` / `doc_view.html`：
  仅导航增加 `<a href="/overview">总览</a>`（任务允许的唯一定制），其余未动。
- `tests/test_overview.py`（新增，36 项）：页面 200 与导航、四 API 契约、默认
  杭州、as_of 贯穿四 API、非法参数 422、未知指标结构化空结果、跨城不泄露、
  缺失不填 0、冲突全保留、可比/limited/共同覆盖期、零网络（connect/
  getaddrinfo/connect 拦截）、零 embedding（`_get_model` 注入失败）、零 LLM
  （无 key + `lib_llm` 不入 sys.modules）、临时库 sha256 前后不变、正式库与
  metrics/ 指纹（`_FormalGuard`）、XSS 转义（服务端 autoescape + 模板 esc()
  全量扫描）、违禁措辞（页面 + 四 API 响应）、JSON 可序列化、项目外 cwd 子进程。
- `README.md`：新增"市场总览看板（Step 3E）"一节，使用节注明总览页无需 key。
- `plans/TASK-overview-dashboard.md`：本文件。

### 边界与语义要点

- 不修改 `lib_metrics_query.py` / `market_phase.py` / `coverage_report.py` /
  `coverage_compare.py`；web 层只 import 复用。不修改数据库与 metrics/ 原始
  文件；不运行 ingest / build_index / load_external_metrics / run_eval。
- API 参数：city 非空 ≤40 字符（缺省→杭州，显式空串→422）；metric 必填；
  as_of / period 格式复用 3A 时期粒度校验；compare cities ≥2（默认
  杭州,上海,北京,深圳）。未知城市/指标不是参数错误：返回结构化空结果 + warning。
- 页面趋势默认 `include_forecasts=true`（预测虚线单独成线）；阶段 API 始终
  actual-only（3D 默认）。
- 风险四色映射：红 `price_volume_divergence` / `new_secondary_divergence` /
  `inventory_pressure` / `listing_pressure`；黄 `source_conflict` /
  `low_sample_size` / `forecast_dominant` / `city_comparison_limited` /
  `granularity_mismatch` / `source_coverage_asymmetry`；灰 `data_gap`（缺失
  只表示无法评估，不画成市场高风险）；蓝 `watch_next`。未知 code 归黄（保守）。
- TestClient 不进 lifespan（不触发 app 启动预热线程），测试不加载 embedding。

### 验证（2026-09-08 实测）

- 测试：`.venv/bin/python -m unittest discover -s tests`，255 项全绿
  （0 失败 / 0 错误）= 原有 219 项原样回归 + 新增 36 项。
- 正式库只读验证（cwd=/tmp，TestClient，不启动正式服务）：
  - `/api/overview`：city=杭州（默认），阶段 `bottom_fluctuation`（底部震荡）/
    置信度 low / as_of=2026-08 / 规则 market-phase-rules/0.1.0，与 3D 记录一致；
    风险 granularity_mismatch、source_conflict；watch_next 可显示。
  - `/api/overview/series?metric=secondary_volume_units`：6 条 series
    （杭州贝壳研究院 / 中指云 / Morgan Stanley 各自成组，多来源未合并），
    粒度 mixed。
  - `/api/overview/coverage`：58 条序列（52 单点 / 4 有内部缺口 / 2 无缺口），
    冲突序列 2 条。
  - `/api/overview/compare?metric=secondary_volume_units`：19 组 = 18 可比 +
    1 limited，flags mixed_granularity / conflicted /
    insufficient_common_periods，与 3C 记录一致；limited 组 warning 明示
    不计算差值/倍数/排名。
- 指纹：data/rag.db size 23244800 / mtime 1788821905.4951766 /
  sha256 1e46e016…094a19fb 前后不变；无 WAL/SHM；metrics/ 61 文件聚合 hash
  89d2b0b4…2607cd 前后不变；正式写入 0；外部模型 API 调用 0；未运行
  ingest / build_index / load_external_metrics / run_eval。

### 已知边界（留给后续）

- 比较指标选择、趋势指标清单为第一版固定集合（任务指定），未做动态指标发现。
- ECharts 为整包 min.js（约 1MB），未做按需裁剪；页面首次加载多 ~1MB 本地流量。
- `expandAxis` 的 x 轴补位上限 240 期，超出时不展开（超长序列直接用原始并集，
  断线可能不明显，但数据表格仍完整）。
- 风险四色映射是展示层约定，web 层维护一份 code→颜色表；3D 新增风险 code 时
  需同步（未知 code 默认按黄色"数据条件类"处理，不会误标红）。

### Review 修复（2026-09-09）

- 修复 `/api/overview/series` 的 `as_of` 快照统计：截断未来点后同步重算
  `coverage.returned_points`、`coverage.conflict_points` 和缺失期统计，避免图表/表格
  与覆盖数字来自不同时间范围。
- 新增“快照早于请求窗口”回归测试；全套测试由 255 项增至 257 项并保持全绿。
