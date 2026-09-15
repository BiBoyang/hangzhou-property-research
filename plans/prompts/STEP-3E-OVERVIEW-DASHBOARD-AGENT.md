# Step 3E Agent Handoff：杭州市场总览看板

项目：`/Users/boyang/Desktop/房地产研报/rag-app`

开始前读取并遵守：

1. `/Users/boyang/WORKFLOW.md`
2. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-query.md`
3. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-coverage.md`
4. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-city-comparison.md`
5. `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-market-phase.md`
6. `/Users/boyang/Desktop/房地产研报/rag-app/web/app.py`
7. `/Users/boyang/Desktop/房地产研报/rag-app/web/templates/metrics.html`

用户已批准 Step 3E：新增独立 `/overview` 杭州市场总览页面，复用 Step 3A～3D 的只读结构化接口；不替换现有 `/` 问答首页。完成后提交 Review Package 并停止。

## 目标

让用户打开总览页即可看到杭州当前市场证据：

- 市场阶段、置信度、数据截止期和规则版本；
- 支持证据与反向证据；
- 风险信号及触发原因；
- 杭州核心指标多来源趋势；
- 杭州与上海、北京、深圳、全国的比较；
- 数据覆盖、缺口和下一步观察指标。

页面只展示事实、规则结果和风险提示，不输出“应该买/不买”“最佳入场”等购房结论。

## 允许修改

- `/Users/boyang/Desktop/房地产研报/rag-app/web/app.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/web/templates/overview.html`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_overview.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/README.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-overview-dashboard.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/web/static/echarts.min.js`，只有能使用本地固定资源时才新增；无法联网下载时不要偷偷改回外部 CDN，先报告并实现无图表降级。

需要修改其他文件时先停止，报告绝对路径、原因、影响。

## 硬边界

- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/data/rag.db`。
- 不修改 `/Users/boyang/Desktop/房地产研报/rag-app/metrics/*.jsonl`、`*.csv`、PDF、Markdown。
- 不运行 `ingest.py`、`build_index.py`、`load_external_metrics.py`、`run_eval.py`。
- 不重新解析、切片、抽取、embedding 或重建索引。
- 不调用外部 LLM、embedding、reranker 或付费 API；应用 API 调用必须为 0。
- 不修改 `lib_metrics_query.py`、`market_phase.py`、`coverage_report.py`、`coverage_compare.py`；如底层接口不足，先停止报告，不自行扩大范围。
- 不改变现有 `/`、`/metrics`、`/docs` 的用户行为，除非只是导航增加 `/overview` 链接。
- 页面不能要求 `LLM_API_KEY` 才能打开。
- 新 API 不调用带建表/迁移/commit 副作用的 `lib_db.connect()`。
- 不把缺失数据显示成 0，不插值，不填充，不跨来源替代。
- 不把不同来源静默合并，不对 limited 比较计算差值、排名或强弱结论。

## 推荐实现

新增页面：

`/Users/boyang/Desktop/房地产研报/rag-app/web/templates/overview.html`

新增路由/API：

- `GET /overview`：返回页面；默认 city=杭州；支持可选 `as_of`。
- `GET /api/overview`：调用 `assess_market_phase()`，返回阶段、证据、风险、coverage、watch_next。
- `GET /api/overview/series`：调用 `query_metric_series()`，返回指定杭州指标的原始多来源序列、缺失期和冲突。
- `GET /api/overview/coverage`：调用 `summarize_series_coverage()`，返回杭州覆盖与缺口。
- `GET /api/overview/compare`：调用 `compare_metric_across_cities()`，返回杭州与北上深/全国的可比组和 limited 并列。

如果实现路径不同，必须在 Review Package 解释原因。所有 API 只读、无 LLM、无 embedding 冷启动、无数据库写入。

## 页面结构

### 顶部：市场阶段

同时显示：

- “当前结构化证据更接近：底部震荡”这类保守措辞；
- 置信度 low/medium/high，并说明是证据充分程度；
- 数据截止期；
- 规则版本；
- 明确免责声明。

不得只突出阶段名称而隐藏 low 置信度。

### 支持证据与反向证据

并列展示，每条可展开查看：

- signal code；
- 指标；
- 城市/子市场；
- 时期；
- 原始数值；
- institution；
- source_file；
- source_quote；
- warning。

不要只显示颜色标签而隐藏证据。

### 风险信号

显示触发原因和原始触发数据。可按以下颜色区分：

- 红色：实际反向信号；
- 黄色：数据不足、来源冲突、limited；
- 灰色：指标未覆盖；
- 蓝色：下一步观察项。

不要把“数据缺失”直接画成市场高风险。

支持的风险 code 包括：

`data_gap`、`source_conflict`、`low_sample_size`、`forecast_dominant`、`price_volume_divergence`、`inventory_pressure`、`listing_pressure`、`new_secondary_divergence`、`city_comparison_limited`、`granularity_mismatch`、`source_coverage_asymmetry`。

### 核心指标趋势

第一版优先展示：

- `secondary_volume_units`；
- `mom_change_pct` / `yoy_change_pct`；
- `inventory_months`；
- `listing_units`。

每组：

- 按 institution + source_file 分线；
- 预测与实际分线；
- 显示单位和粒度；
- 缺失期留空且断线，不能用 `connectNulls` 连跨缺口；
- 同期冲突有明显提示并保留全部值；
- 不同来源不求平均。

如果本地 ECharts 不可用，表格和状态卡必须仍然可用；不得偷偷引入外部 CDN 依赖。

### 城市比较

支持指标选择：

- `secondary_volume_units`；
- `avg_price`；
- `yoy_change_pct`；
- `rent_level`。

显示杭州、上海、北京、深圳，可选全国。

- 严格可比组可以进入主图；
- `limited` 只放并列表或单独区域；
- `insufficient_common_periods` 明确提示共同期不足；
- `conflicted` 明确提示同期多值；
- `mixed_granularity` 明确提示粒度不同；
- 不显示城市排名、差值、倍数、相关性、领先滞后或“谁更强”。

### 覆盖与观察项

显示：

- 实际序列数；
- 预测序列数；
- 单点序列数；
- 冲突序列数；
- 核心指标缺失；
- 内部缺口；
- `watch_next`。

观察项只说“继续看什么”，不说“采取什么买房行动”。

## API 数据和安全

所有新 API：

- 使用 Pydantic 或等价参数校验；
- 对 city、metric、as_of、period 参数校验；
- 非法参数返回清晰 4xx；
- 下游查询错误返回结构化 500，不把 traceback 直接泄漏到页面；
- 正确关闭连接；
- 使用已有只读查询层；
- 不触发 embedding 模型加载；
- 不依赖 LLM key；
- 不将数据库字段未经转义地拼入 HTML。

页面渲染：

- 服务器提供的 institution、source_file、source_quote、warning、标题等文本进入 DOM 前必须转义或使用安全渲染；
- Markdown/HTML 不得让数据库内容直接执行脚本；
- 用户输入参数必须 URL 编码；
- 不使用 `innerHTML` 拼接未转义数据库字段。

## 测试最低要求

新建：

`/Users/boyang/Desktop/房地产研报/rag-app/tests/test_overview.py`

测试可以使用 FastAPI TestClient；如果环境缺少依赖，使用现有项目可用的本地测试方式并说明。

所有写入测试使用临时数据库。

至少覆盖：

1. `/overview` 页面返回 200；
2. 导航包含总览入口；
3. `/api/overview` 返回阶段、置信度、证据、风险、coverage、watch_next；
4. `/api/overview/series` 返回多来源、缺失期和冲突；
5. `/api/overview/coverage` 返回覆盖摘要；
6. `/api/overview/compare` 返回 comparable、limited 和共同覆盖期；
7. 默认城市为杭州；
8. `as_of` 贯穿 overview、series、coverage、compare；
9. 非法 city/as_of/period 返回 4xx；
10. 不存在指标返回结构化空结果；
11. 不支持城市不泄露其他城市数据；
12. API 不要求 LLM_API_KEY；
13. API 不调用外部网络；
14. API 不加载 embedding；
15. API 不修改数据库；
16. 正式数据库 size/mtime/sha256 前后不变；
17. metrics 源文件聚合 hash 前后不变；
18. 缺失点不被填 0；
19. 图表配置不连接缺失点；
20. 同期冲突可见且不丢值；
21. limited 不进入强结论；
22. 页面不出现“应该买/建议买/最佳入场”等词；
23. 数据库文本安全转义；
24. JSON 可序列化；
25. 原有 219 项测试不回归；
26. CLI/API 从项目外 cwd 可用（如适用）。

## 正式只读验证

临时测试通过后，允许对正式数据库进行只读验证：

- `/api/overview` 或对应 Python 函数；
- `/api/overview/series?metric=secondary_volume_units`；
- `/api/overview/coverage`；
- `/api/overview/compare?metric=secondary_volume_units`。

验证：

- 返回结构正确；
- 杭州为默认城市；
- 阶段、风险和证据可显示；
- 多来源没有合并；
- 缺失期没有填 0；
- limited 有明确标记；
- 正式库和源文件指纹前后不变；
- API 调用 0；
- 不启动或停止正式 Web，除非用户在人工验收时自行启动。

## 成本边界

- 应用外部模型 API 调用：0；
- 不运行 `run_eval.py`；
- 不重新生成 embedding；
- 不读取全部研报正文；
- 不抓取新数据；
- 不重建索引；
- 不调用外部 CDN 作为必要依赖；
- 如果下载本地 ECharts 需要网络，先报告下载条件，不调用模型 API。

## 允许修改文件

- `/Users/boyang/Desktop/房地产研报/rag-app/web/app.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/web/templates/overview.html`
- `/Users/boyang/Desktop/房地产研报/rag-app/tests/test_overview.py`
- `/Users/boyang/Desktop/房地产研报/rag-app/README.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-overview-dashboard.md`
- `/Users/boyang/Desktop/房地产研报/rag-app/web/static/echarts.min.js`（可选）

禁止修改数据库、指标原始文件、PDF、Markdown、现有后端分析模块和现有模板（除新增导航链接）。

## 硬停止条件

出现以下任一情况停止并报告：

- 需要修改清单外文件；
- 新 API 需要改底层查询层但未获准；
- 页面必须依赖 LLM 或外部 CDN；
- 正式数据库或原始文件指纹变化；
- 需要写数据库、迁移、导入、删除或回填；
- 缺失/冲突数据无法安全展示；
- 需要把 limited 转成强结论；
- 自动测试失败原因超出页面/API 范围；
- 需要外部 API 或重新处理历史材料。

停止报告必须包含当前状态、已修改文件、指纹变化、测试结果、API 调用次数、卡点和需要用户决定的事项。

## Review Package

完成后提交：

1. 修改/新增文件绝对路径和职责；
2. 页面路由和 API 契约；
3. 数据加载和只读保证；
4. 阶段、证据、风险、趋势和比较的展示方式；
5. 缺失、冲突、预测和 limited 的处理；
6. 安全转义说明；
7. 测试命令、总数、通过/失败/跳过和关键输出；
8. 正式库只读验证及指纹前后值；
9. API 调用次数；
10. 人工验收说明：启动服务后打开 `http://localhost:8666/overview`，写明看到什么算通过；
11. 已知风险；
12. 明确没有进入后续个人购房建议或其他未批准功能。

完成后停止等待 Review。
