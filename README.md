# 房地产研报研判系统（rag-app）

基于 61 份中英文投行/机构研报（53 PDF + 8 网页采集）+ 1517 条结构化指标的本地 RAG 系统：研报问答 + 结构化指标看板。

## 架构

```
PDF ──Docling(本地)──► Markdown 文档库 ──► 规则切分(表格不拆) ──► SQLite
                                                               ├─ FTS5(jieba分词) BM25
                                                               ├─ sqlite-vec(Qwen3-Embedding-0.6B 本地)
                                                               └─ metrics 指标表(LLM抽取+人审+外部数据)
查询 ──► RRF 混合检索 ──► Kimi K2.6 生成(OpenAI兼容,可一行切换) ──► 带引用回答
```

## 使用

```bash
cd rag-app
# 首次/增量入库（有新 PDF 放进研报目录后跑一次）
env -u http_proxy -u https_proxy -u all_proxy HF_ENDPOINT=https://hf-mirror.com .venv/bin/python scripts/ingest.py
# 构建检索索引（FTS + 向量）
.venv/bin/python scripts/build_index.py
# 载入外部指标数据（按 source_file 整文件事务替换，幂等）
# 既有库首次使用前需显式迁移（只加列不导入；正式库执行前需先批准，见"指标导入可靠性"节）
.venv/bin/python scripts/load_external_metrics.py --migrate-source-file
.venv/bin/python scripts/load_external_metrics.py
# 启动 Web（局域网可访问）
export LLM_API_KEY=你的通用 OpenAI 兼容 key
# 如果已配置 DEEPSEEK_API_KEY，可省略 LLM_API_KEY；默认使用 DeepSeek deepseek-chat
.venv/bin/python web/app.py   # http://localhost:8666
# 总览页 http://localhost:8666/overview 只读展示，不需要 LLM_API_KEY
# 低成本生成评测：先跑 1 题；完整 25 题会产生相应次数的 DeepSeek 请求
HF_HUB_OFFLINE=1 .venv/bin/python scripts/run_eval.py --limit 1
```

## 克隆后怎么跑（BYOD）

仓库不含研报原文与数据库（数据来源敏感，见 .gitignore），clone 后两条路：

**指标层开箱即用**（指标看板 / 市场总览 / 阶段评估，不需要任何 PDF）：

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
# 初始化空库 schema（不会包含任何数据）
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); from lib_db import connect; from lib_paths import DB; connect(DB).close()"
# 从 metrics/ 幂等导入全部指标（来源文件身份替换，可重复执行）
.venv/bin/python scripts/load_external_metrics.py
.venv/bin/python web/app.py   # /overview 与 /metrics 直接可用，无需 LLM_API_KEY
```

**文档层自带数据（Bring Your Own Data）**：管线对语料无假设——把任意研报 PDF 放进数据根目录（rag-app 的上级，或用 `RAG_DATA_ROOT` 指定），照"使用"一节跑 `ingest.py` + `build_index.py` 即可开始问答。

## 目录

- `scripts/` ingest(解析入库，PDF+web研报双通道) / build_index(索引) / lib_search(混合检索) / lib_llm(生成，流式) / lib_meta / lib_chunk / lib_db / lib_metrics_query / lib_paths(统一路径推导) / load_external_metrics(指标导入，source_file 幂等替换) / metrics_maintenance(历史指标只读核对预览)
- `config/metrics_schema.yaml` 指标库 schema；`config/extraction_prompt_template.md` 抽取规范
- `metrics/*.csv|*.jsonl` 指标数据文件（外部采集 + 研报抽取，带来源）
- `eval/questions.yaml` 25 题验收集；`eval/report_*.json` 历次评测报告
- `data/raw_md/` 解析出的 Markdown；`data/rag.db` 单文件数据库
- 网页采集研报源文件在上一级 `web研报/` 目录；所有脚本和 Web 的路径由 `scripts/lib_paths.py` 统一推导（项目根=rag-app，数据根默认为其上一级），项目整体搬目录无需改代码。源 PDF / `web研报/` 挪到别处时，设 `RAG_DATA_ROOT` 指向新数据根即可

## 设计决策

1. 双层架构：语义问题走文档层，数据问题走指标层——向量检索不擅长"某指标某月是多少"
2. 本地解析 Docling：投行表格还原实测可用（spike 先行验证 3 份代表性 PDF）
3. 单文件 SQLite：FTS5 + sqlite-vec + 指标表，无 Docker 无外部服务
4. 开发时 LLM 与工作时分层：抽取/标注在开发期由 agent 完成，运行时只需一个生成 key
5. 指标库生命线是溯源：每条指标带原文摘录 + 页码，抽样人审

## 踩坑记录（实测）

- **图片型 PDF**：53 份中 14 份无文本层，Docling 默认配置解析为 `<!-- image -->` 占位符；macOS 下用 `OcrMacOptions`（Vision 框架）救回全部 14 份
- **代理坑**：本机 socks/http 代理导致 HF 模型下载 SSL EOF；`env -u *_proxy + HF_ENDPOINT=hf-mirror.com` 解决
- **向量化性能**：Qwen3-Embedding-0.6B 在 CPU 上约 8 chunks/min（3 小时量级），切 MPS + `max_seq_length=1024` 后 ~85 chunks/min（20 分钟）
- **检索质量三个关键改进**：① 索引文本必须带报告标题+章节标题（否则"公积金报告"类查询沉底）；② 中英同义词扩展（瑞银↔UBS、北京↔Beijing、新政↔easing）；③ 文档级先验 boost（标题命中机构/城市 + 日期对齐）
- **披露页噪声**：投行报告尾部的 disclosure 段会污染 top-k，查询期过滤只算治标，2026-09-16 已下沉到切分期按强特征整段丢弃治本（清出 39 段，chunks 1671→1632），run_eval 期望词同步做同义归一
- **累计期混入趋势图**：H1/Q1 累计值会让 ECharts 月度趋势线失真，前端过滤 `YYYY-MM` 以外的 period

## 指标导入可靠性（Step 2A，2026-09）

- 导入器以 source_file（相对 `metrics/` 的文件名）为稳定身份，单事务整文件替换：同文件重复导入结果不变；同文件内完全重复行自动折叠并报告；不同文件内容相同也各自保留（各挂各的来源）。
- 整文件替换后 metric_id 会重新分配——不要把 metric_id 当稳定标识。
- source_file 只证明内容对应本地文件，不代表原始机构来源已核实，也不代表同内容的两份文件是独立证据。
- 阻断保护（拒绝且零写入）：旧 schema、存在 source_file 为空的历史行、来源文件在盘上缺失、空文件、校验失败。文件须为合法 UTF-8（不做编码探测，解码失败按文件拒绝并报文件名与字节位置）；校验契约：必填字段非空；period 合法（`2026-08` / `2026Q3` / `2026-H1` / `2026`）；value 只接受数字或字符串（拒绝布尔、对象、数组、纯空白、NaN·Infinity；文本值存 value_text，"2027" 这类数字字符串仍转数值）；is_forecast 只接受确切的 0/1（缺省视为 0，兼容布尔与 "0"/"1"，拒绝 0.9 之类会被截断的值）；JSONL 顶层每条必须是对象，单文件失败不影响批次其余文件。
- 当前正式库状态：Step 2B 历史归属与清理已于 2026-09-08 完成执行（保留 1477 行 / 删除重复 1892 行，source_file 全部回填，执行与预检报告见 `plans/reports/`），指标导入正常工作；此后的二次治理与口径补充（2026-09-16：冰山 CNY 量/价归位、中指院月度渠道文件、schema 量价分家）后，指标库现为 1517 条（2026-09-21 核对）。
- 历史核对与清理（Step 2B，已完成）：`.venv/bin/python scripts/metrics_maintenance.py preview [--report 路径]`，另有 `fingerprint` / `inspect-source` / `inspect-db` 子命令；全部以 mode=ro 只读打开数据库，无任何写操作入口。治理结论：历史 3369 行全部按 15 字段匹配到现有源文件，其中 1905 行为同内容重复导入的重复行，清理后指标库为 1477 条。
- 正式迁移与清理的安全骨架（Step 2B-2A，2026-09-08，临时库验证完成，正式库未动）：`metrics_maintenance.py` 增加只读 `plan` 子命令与内部执行函数（`execute_plan` 须显式 `confirmed=True` 且按文件身份（os.path.samefile，同 inode）拒绝正式库本体/硬链接/软链接；单事务内显式 ALTER + UPDATE source_file + DELETE 同文件重复，失败整体回滚；执行前重算指纹、事务内重算目标集；执行后旧计划失效须重新生成）。指纹含 docs/chunks/FTS/vec 内容摘要，chunks_vec 覆盖 chunk_id + embedding 原始字节，读不出时计划阻断。跨文件相同内容的 metric_id 归属是确定性技术分配（文件 POSIX 升序 × metric_id 升序，尊重已有一致归属），不代表真实来源，也不代表独立证据。
- 正式执行前预检与备份（Step 2B-2B-Preflight，2026-09-08，**只读+备份，正式写入仍未批准**）：新增正式备份入口 `formal_backup`（须显式 `formal_backup_confirmed=True`；源 mode=ro；目标必须是不存在的新文件且不得为正式库/源库本体或链接；同秒冲突拒绝不覆盖；备份后自动验证 integrity_check/行数/schema/内容摘要，embedding 不可验证时不声称"已充分验证"）与预检编排 `run_preflight`（指纹→进程→计划→备份→复核→报告；检测到 ingest/build_index/load_external_metrics/run_eval 运行时停止备份并阻断）。本轮产物：正式备份 `data/backups/rag-2b2-preflight-20260908-062313.db`（integrity ok，全部内容摘要与源库一致，含 1671 行 embedding 内容摘要）、最新计划 `plans/reports/step2b2b-plan-formal-20260908-062312.json`（保留 1477/删除 1892/跨文件 13，无阻断）、预检报告 `plans/reports/step2b2b-preflight-20260908-062312.json`；正式库 sha256/mtime/inode 前后不变。备份授权≠写入授权：正式迁移/回填/删除仍需单独批准，`execute_plan` 对正式库依旧无条件拒绝。

## 多来源时间序列查询（Step 3A，2026-09-08）

`scripts/lib_metrics_query.py` 新增 `query_metric_series()`，为后续杭州市场总览、数据覆盖报告和市场阶段规则提供按来源分组的时间序列数据。

- **接口**：`query_metric_series(*, city, metric_name, institution, source_file, periods, period_start, period_end, include_forecasts=True, db_path=DB) -> dict`，全部精确匹配；另有薄自然语言适配 `query_metric_series_nl()`（只解析城市/指标/时期，不做任何合并或判断）。核心接口不依赖 LLM 或 HTTP。
- **分组键**：`(institution, source_file, city, metric_name, segment, unit, comparison_type, is_forecast, period_granularity)`——不同来源、不同口径、预测与实际各自成组；绝不求平均、不选"真实值"、不合并曲线。同组同期多值移入 `conflicts` 并进 `warnings`（不平均、不取第一条、不丢弃），该时期不再出现在 `points` 但仍计为已覆盖。
- **缺失时期**：请求范围（如 2026-03~2026-08）按粒度展开，缺失期列入每条 series 的 `missing_periods`；不插值、不填 0、不用相邻月代替。
- **粒度**：月/季/半年/裸年份严格区分，互不转换、不混组、范围不跨粒度；请求与库内格式不匹配时返回结构化空结果 + warning；排序用 `_period_sort_key` 稳定排序。
- **预测**：`include_forecasts=True` 时预测单独成组（series 与 point 层均有 `is_forecast`）；`False` 时只回实际观测并进 warning。
- **空城市**：`city="杭州"` 精确匹配，空城市不自动归属任何城市；未指定城市时空城市记录带 `city_label="未归一化城市"` 返回；不修改数据库字段。
- **只读连接**：全程 `file:...?mode=ro` URI 只读连接，不经过 `lib_db.connect()`，无 schema 创建/迁移/commit，查询结束即关闭；旧 schema（无 source_file 列）降级为 `source_file=None`，不自动迁移；SQL 全部参数化。
- **边界**：本层只返回原始分组、缺口与冲突，不计算"方向一致/趋势改善/市场企稳"，不做市场阶段判断与购房建议（属 Step 3B/3D）。任务记录：`plans/TASK-series-query.md`。

## 数据覆盖与缺口报告（Step 3B，2026-09-08）

`scripts/lib_metrics_query.py` 新增 `summarize_series_coverage()`，纯消费 `query_metric_series()` 的结构化结果（不维护第二套 SQL/时期解析/分组逻辑），输出三层覆盖摘要：序列层 → 城市-指标汇总层 → 全局汇总层。只描述数据存在性与完整性，不判断涨跌、市场阶段、风险等级或购房时机。

- **CLI（只读）**：`.venv/bin/python scripts/coverage_report.py [--city 杭州] [--metric secondary_volume_units] [--institution 贝壳] [--source-file beike.csv] [--periods 2026-03,2026-04 | --period-start 2026-03 --period-end 2026-08] [--actual-only] [--db 路径] [--report 路径] [--json]`。默认输出文本摘要；`--report` 写 JSON（路径受保护：不得覆盖数据库、不得写入 `metrics/`）。
- **序列状态**（有明确请求窗口）：`full_coverage` / `partial_coverage` / `single_point`（仅覆盖 1 期，优先于 full）/ `forecast_only`（整条为预测，满窗也不叫 full）/ `no_observation`；无窗口：`single_point` / `multi_point_no_internal_gap` / `multi_point_with_internal_gaps` / `forecast_only` / `no_observation`——无窗口时绝不把已有最小/最大期当成完整请求窗口（不给 full_coverage，`coverage_rate=None`）。城市-指标汇总层另加 `mixed_granularity`（粒度混合时不谎称无缺口）。
- **冲突**：同期多值全部保留在 `conflicts`，冲突期计为已覆盖（不算缺失），以 `has_conflicts` 状态位标记并给 warning。
- **actual 与 forecast 分开统计**；`--actual-only` 只统计实际观测。空城市单独标记"未归一化城市"并在全库报告提示，不归入任何城市。
- **"缺失最多"列表仅表示覆盖不足，不代表风险最大**；状态为 single_point / forecast_only / no_observation 的序列标记为"不足以支持时期对比/趋势类分析"（仅陈述数据完整性）。
- **只读**：沿用 Step 3A 的 `mode=ro` 连接，无 schema/commit，SQL 全参数化；旧 schema（无 source_file 列）降级运行不迁移。任务记录：`plans/TASK-series-coverage.md`。

## 城市横向比较（Step 3C，2026-09-08）

`scripts/lib_metrics_query.py` 新增 `compare_metric_across_cities()`：同指标跨城（杭州/上海/北京/深圳/全国等）原始点并列，输出各城市来源、缺失期、共同覆盖期（交集）与可比性状态。逐城调用 `query_metric_series()`（mode=ro 只读），不维护第二套 SQL/分组逻辑；只描述数据条件与原始差异，不计算差值/倍数/排名/涨跌幅/相关性，不判断城市优劣。

- **CLI（只读）**：`.venv/bin/python scripts/coverage_compare.py --cities 杭州,上海,北京,深圳 --metric secondary_volume_units [--institution/--source-file/--segment/--unit/--comparison-type] [--periods ...|--period-start ... --period-end ...] [--include-forecasts|--actual-only(默认)] [--db] [--report 路径(路径保护同 3B)] [--json]`。
- **可比性**：严格比较组要求 institution/source_file/segment/unit/comparison_type/is_forecast/粒度全一致，不同来源不静默合并；某口径下无严格组跨城但聚合覆盖 ≥2 城时并列返回并标 `comparability="limited"`（仅并列，无任何派生计算）；仅 1 城有数据标 `single_city_only`。
- **共同覆盖期**：各城观测期交集 `common_periods`，少于 2 期标 `insufficient_common_periods` + warning（仍展示原始点）；`partially_covered_periods` 记录只被部分城市覆盖的时期。
- **状态**：`comparable` / `limited` / `insufficient_common_periods` / `uncovered_city` / `unmatched_metric` / `conflicted` / `mixed_granularity`——只表示数据条件，不表示城市优劣。冲突期全保留计为已覆盖；预测默认排除，显式开启后预测只和预测比较；空城市排除并 warning，不支持城市标 `uncovered_city`，不用全国或其他城市代替缺失城市。
- 另有薄 NL 适配 `compare_metric_across_cities_nl()`（只解析城市/指标/时期，城市 <2 或指标命中 ≠1 时不查询）。任务记录：`plans/TASK-city-comparison.md`。

## 市场阶段与风险信号（Step 3D，2026-09-08）

`scripts/market_phase.py` 新增 `assess_market_phase()`：规则型市场阶段判断层，复用 3A/3B/3C 三个只读接口（无第二套 SQL/时期解析/分组逻辑），把当前实际观测整理为"当前证据更接近哪个阶段、依据是什么、有哪些反向证据和风险"。阶段是透明规则对证据的整理，不是事实本身，不输出"应该买/不买"等购房结论，不使用黑箱评分。

- **接口**：`assess_market_phase(*, city="杭州", as_of=None, period_start=None, period_end=None, include_forecasts=False, db_path=DB) -> dict`。另有只读 CLI：`.venv/bin/python scripts/market_phase.py [--city 杭州] [--as-of 2026-08] [--period-start 2026-03 --period-end 2026-08] [--include-forecasts] [--db 路径] [--json]`，项目外 cwd 可运行，无任何写入子命令。
- **阶段**：`data_insufficient` / `continued_decline` / `decline_narrowing` / `bottom_fluctuation` / `initial_stabilization` / `recovery_broadening`。门评估顺序从证据要求最严到最保守（前者全过才选，否则回落），六门条件完整留痕在 `phase.rule_evaluation`；规则版本 `market-phase-rules/0.1.0`，阈值全部在模块常量区（2 期才谈变化、3 期才谈连续/收窄、单点只报数据不足）。
- **信号**：家族 × 子市场（价格新房/二手、成交新房/二手、库存、挂牌、土地、租金、核心城市联动），每个信号带 direction（improving/deteriorating/stable/mixed）、strength、status（available/unavailable/conflicted/insufficient）和可追溯原始证据（period/value/institution/source_file/source_quote）。不同来源不求平均、不取舍，方向冲突标 conflicted；同期多值冲突双值保留；多粒度只分析最丰粒度、其余并列 + 风险标记。
- **预测隔离**：阶段仅由实际观测（is_forecast=0）计算；`include_forecasts=True` 时机构预测单独成 `forecast_outlook` 前瞻信号（附"非市场事实"警示），不混入阶段与支持/反向证据。
- **置信度**：只有 low/medium/high，表示证据充分程度（序列数/覆盖期数/来源数/冲突数/核心指标齐全度），不是判断正确的概率。
- **风险信号与阶段分离**：`data_gap`、`source_conflict`、`low_sample_size`、`forecast_dominant`、`price_volume_divergence`、`inventory_pressure`、`listing_pressure`、`new_secondary_divergence`、`city_comparison_limited`、`granularity_mismatch`、`source_coverage_asymmetry`——每条带触发指标/时期/原始值/来源，无分数，不映射为买房建议。
- **当前正式库实况**（2026-09-21 实跑，as_of 2026-08）：杭州 = `continued_decline`（持续下行）/ 置信度 low——二手房成交 2026-01~08 连续走弱（strong）、核心城市联动走弱、新房价格环比微升但核心价格序列连续观测不足 3 期；输出随数据变化，以当次运行为准。
- **历史快照**：传入 `as_of` 后，阶段信号、coverage、核心城市共同覆盖期和可选预测前瞻统一截断到该时期；返回 `filters.effective_period_end`，避免历史复盘混入截止期之后的数据。
- 只读：全程 mode=ro，零外部 API（socket 拦截 + import AST 扫描双测试）。任务记录：`plans/TASK-market-phase.md`。

## 市场总览看板（Step 3E，2026-09-08）

`/overview` 杭州市场总览页：打开即见当前市场证据——市场阶段（保守措辞"当前结构化证据更接近：X"）、置信度、数据截止期、规则版本与免责声明；支持/反向证据可展开到原始数值与原文摘录；风险信号按红（实际反向）/黄（数据不足、来源冲突、limited）/灰（指标未覆盖，缺失≠高风险）/蓝（下一步观察项）分色；核心指标（二手房成交量、环比/同比、库存去化、挂牌量）多来源趋势线；杭州与上海/北京/深圳（可选全国）横向比较；覆盖缺口与观察项。页面只展示事实、规则结果与风险提示，不输出购房结论。默认城市杭州，不要求 `LLM_API_KEY`，不影响现有 `/`、`/metrics`、`/docs`（仅导航增加"总览"入口）。

- **路由**：`GET /overview`（页面，可带 `?city=上海&as_of=2026-08`）+ 4 个只读 API：`/api/overview`（3D 阶段评估）、`/api/overview/series?metric=`（3A 多来源序列）、`/api/overview/coverage`（3B 覆盖摘要）、`/api/overview/compare?metric=`（3C 城市比较，`cities` 逗号分隔默认杭州,上海,北京,深圳）。
- **复用不复制**：API 直接调用 Step 3A~3D 结构化接口（mode=ro，不经 `lib_db.connect()`，无写入）；`as_of` 历史快照复用 3D 的过滤实现，贯穿全部 4 个 API；web 层只做参数校验（非法 city/as_of/period/metric → 422）与结构化 500（不泄漏 traceback）。
- **缺失与冲突**：缺失期在 x 轴占位、线上断线（`connectNulls:false`），不填 0、不插值；同期冲突以红色散点标出并全部保留在表格，不求平均；预测虚线单独成线，不参与阶段计算。
- **比较边界**：严格可比组（同机构/同来源/同口径/同粒度）进主图；`limited` 只进并列区表格，不计算差值、倍数、排名、方向或"谁更强"；`insufficient_common_periods` / `conflicted` / `mixed_granularity` 均有明确提示；无数据城市标 `uncovered_city`，不用其他城市代替。
- **图表降级**：ECharts 5.5.1 固定在 `web/static/echarts.min.js`（本地静态资源，无外部 CDN）；加载失败时自动降级为纯表格 + 状态卡，信息不缺。
- **安全**：数据库文本（institution/source_file/source_quote/warning 等）进 DOM 前一律 `esc()` 转义，服务端 Jinja 自动转义开启；用户输入 URL 编码；API 全程零外部网络、零 embedding 加载、零 LLM 依赖（均有测试断言）。
- 测试：`tests/test_overview.py` 36 项（页面/导航、四 API 契约、as_of 贯穿、非法参数 422、跨城不泄露、零网络/零 embedding/零 LLM、不改库、正式库指纹、XSS 转义、违禁措辞、项目外 cwd）。任务记录：`plans/TASK-overview-dashboard.md`。

## 指标看板中文化（2026-09-09）

`/metrics` 展示层中文化：只改 `web/templates/metrics.html`，数据库字段、`/api/metrics` 契约与指标查询（仍用英文 `metric_name`）不变。指标下拉框显示"中文名称（code）"，图表标题/图例用中文名称·机构·来源，表格单位、市场类型、环比/同比、实际观测/预测均为中文，当前指标附一句解释；原始英文 code 保留在下拉框 value、`data-metric` 属性、title 提示与括号小字中供排查。未知指标/单位/segment/comparison_type 一律原值回退不报错；空城市显示"未归一化城市"，不再写成"全国"；数据库文本进 DOM 前一律 `esc()` 转义。同时 ECharts 从 jsdelivr CDN 切到本地 `web/static/echarts.min.js`（与总览页同一份固定资源），页面不再有外部依赖。测试：`tests/test_metrics_dashboard_labels.py`（静态契约 + node 行为验证 + 正式库只读指纹，无 node 时相应项跳过）。任务记录：`plans/TASK-metrics-dashboard-labels.md`。

## 已知边界 / TODO

- 0.6B embedding 跨语言能力一般：中英混合长查询召回仍可能混入无关中文报告，生成层靠 top-k 冗余 + 强模型兜底
- 指标库抽样人审已完成（185 条 / 约 13%，错误率 4.9%，四类系统性错误已修复，终审记录见 `data/metrics_review_adjudication.md`）；全量人审未做，日常仍靠程序化断言（quote 必须是原文子串且含数字）兜底
- rerank 已探针否决：bge-reranker-base 在 6 题对比中为负优化（中文中心模型压英文 chunk 分、不理解机构/日期约束），维持现有管线；重试条件与 Ollama 损坏情况见 `plans/rerank-spike.md`

## 数据更新

- **新增研报**：PDF 放入数据根目录后跑 `ingest.py` + `build_index.py`（增量，已入库的自动跳过）；
- **更新指标**：编辑 `metrics/` 下对应 CSV/JSONL 后跑 `load_external_metrics.py`（按来源文件幂等替换，可重复执行）；
- **回归验证**：`run_eval.py` 跑 25 题验收集，通过数不应低于 22——低于则检查入库或索引是否出了问题。

**口径备忘（多来源并存是有意的）**：贝壳/中指/克而瑞/冰山指数口径互不相同（如杭州 3 月成交贝壳 9356 vs 大摩 8728），看板按机构分系列展示，不求平均、不合并、不选"真实值"。
