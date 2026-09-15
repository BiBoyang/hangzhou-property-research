# TASK-metrics-dashboard-labels：指标看板中文化

## Step（2026-09-09，方案 A）：metrics.html 展示层中文化

### 范围

只改 `/metrics` 指标看板展示层：把程序内部英文 code 翻译成中文名称、单位、
市场类型、环比/同比、实际/预测。数据库字段、`/api/metrics` API 契约、指标查询
（仍用英文 `metric_name`）、原有问答/总览/报告库均不变；未知 code 原值回退；
不调用 LLM 或外部 API。用户已批准方案 A。

### 修改文件

- `web/templates/metrics.html`：
  - 展示层映射（纯数据、JSON 纯格式，`LABELS-MAPS-BEGIN/END` 标记供契约测试
    提取）：`METRIC_LABELS`（19 个指标 code）、`METRIC_DESCRIPTIONS`（19 条
    一句解释）、`UNIT_LABELS`（9 个单位）、`SEGMENT_LABELS`（6 个 segment）、
    `COMPARISON_LABELS`（mom/yoy）、`FORECAST_LABELS`（0/1）；
  - 回退函数：`metricLabel` / `metricDescription` / `unitLabel` /
    `segmentLabel` / `comparisonLabel` / `forecastLabel` 未知值一律 `?? code`
    原值回退；`cityLabel('')` → `"未归一化城市"`（空城市不再写成"全国"）；
  - 下拉框：`option value` 保持英文 code（查询用），显示文本由 JS 改写为
    `"中文名称（code）"`；
  - 图表：标题显示中文指标名（code），图例 `城市·机构(预测)`；
  - 表格：新增"市场类型""类型（实际观测/预测）"列；单位/市场类型/环比同比/
    类型均中文，原始 code 放 `title` 提示；`data-metric` 属性保留当前英文
    code；当前指标附一句解释（`#minfo`）；
  - ECharts 从 jsdelivr CDN 切到本地 `web/static/echarts.min.js`（Step 3E 已
    本地化的同一份固定资源，页面不再有外部依赖；`/static` 挂载与该文件均为
    既有产物，本轮零新增静态资源）；
  - 原有行为保持：月度过滤、(series,period) 去重、`connectNulls`、预测虚线、
    `.fc` 橙色预测行、`esc()` 转义、导航四链接。
- `tests/test_metrics_dashboard_labels.py`（新增，19 项）：映射真值契约
  （以 STEP 展示映射表为准）、中文 label 非 code 断言、回退函数模式、option
  value 保留 code、表格 `esc()` 全量扫描（仅数值插值白名单）、`data-*`/查询
  参数保留 code、无外部 CDN、导航保留、`api_metrics`/`metrics_page` 源码契约、
  node 行为验证（无 node 跳过）+ `node --check` 整段语法、正式库只读渲染 +
  指纹（`_FormalGuard` 模式，无正式库跳过）。
- `README.md`：新增"指标看板中文化（2026-09-09）"一节。
- `plans/TASK-metrics-dashboard-labels.md`：本文件。

### 边界与语义要点

- 不修改 `web/app.py`、`lib_metrics_query.py`、`market_phase.py`、
  `coverage_report.py`、`coverage_compare.py`、数据库、`metrics/` 源文件；
  `/api/metrics` 返回结构与 `/metrics` 路由签名原样（有源码契约测试锁定）。
- 不运行 ingest / build_index / load_external_metrics / run_eval；不调用外部
  LLM / embedding / reranker / 付费 API；不初始化 Git、不提交推送。
- 页面显示中文，内部查询与 `option value` 仍为英文 code；原始 code 保留在
  下拉框括号小字、图表标题括号、单元格 `title`、`data-metric` 属性中。
- 空城市显示"未归一化城市"：原页面图例把空城市写成"全国"，属未归一化数据
  误标（与 3A `city_label="未归一化城市"` 约定冲突），本轮按 STEP 页面要求
  统一改为"未归一化城市"（图例 + 表格），并标为页面行为变化供 review。

### 验证（2026-09-09 实测）

- 测试：`.venv/bin/python -m unittest discover -s tests -v`，279 项全绿
  （0 失败 / 0 错误 / 0 跳过）= 原有 260 项原样回归 + 新增 19 项。
- 正式库只读渲染（TestClient，不启动正式服务）：`GET /metrics` 200 且含
  `METRIC_LABELS`、`成交均价`、`<option value="avg_price">`；`GET
  /api/metrics?metric=avg_price&city=杭州` 200 且行键完整。
- node v25.4.0 实测回退行为（未知指标/单位/segment/comparison 原值回退、
  forecast 0/1/2/null、空城市"未归一化城市"）与整段脚本 `node --check`。
- 指纹：data/rag.db size 23244800 / mtime 1788821905 /
  sha256 1e46e016…094a19fb 前后不变；无 WAL/SHM；metrics/ 61 文件聚合 hash
  89d2b0b4…2607cd 前后不变；正式写入 0；外部模型 API 调用 0；外部 CDN 引用
  0（模板无任何 http/https 外链）。

### 已知边界

- `<option>` 内无法渲染真正的"小字"样式，code 以全角括号跟在中文名后。
- 文本型指标（`price_bottom_timing`，`value` 为文本）仍被原有
  `typeof value === 'number'` 过滤挡在图表与表格外（原有行为，未改）。
- `index`、`new_home_price_index`、`secondary_home_price_index` 当前正式库
  无数据，映射先行覆盖（schema 已定义，未来入库即生效）。
