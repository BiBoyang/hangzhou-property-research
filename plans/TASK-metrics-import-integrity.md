# TASK-metrics-import-integrity（指标导入可靠性）

状态：Step 2A 已批准（2026-09-07）。Step 2B-1 已实施（只读）。Step 2B-2A 已实施
并通过两轮 Review（2026-09-08）。Step 2B-2B-Preflight 已实施待 Review
（2026-09-08，只读预检+正式备份完成，见 §15）。正式库任何迁移/回填/删除写入
均需后续单独批准。

后续任务：Step 3A（多来源时间序列查询）记录于
/Users/boyang/Desktop/房地产研报/rag-app/plans/TASK-series-query.md。

## 1. 背景与目标

rag-app（/Users/boyang/Desktop/房地产研报/rag-app）的 metrics 表 3369 行中，
完全重复 1273 组 / 3178 行（多重性 2×676、3×562、4×35），根因是旧导入器按
source_url/doc_id 值删除旧行，而 61 个源文件中 50 个（1435 行）这两个字段全空，
删除空转 → 每次导入纯追加。目标：修复未来导入可靠性，为后续来源展示和时间序列打基础。

本任务拆为 2A（导入器修复）/ 2B（历史核对与清理）/ 2C（最小元数据），不打包成大改。

## 2. 已核实基线（2026-09-07，只读核实）

- docs 61；chunks 1671；chunks_fts 1671；chunks_vec 1671 且 chunk_id 集合双向一致。
- metrics 3369 行；is_forecast=1 共 237；doc_id 全 NULL；source_page 全空串；
  source_url 仅 43 行有值（全部来自两个 CSV，9 个不同值）。
- metrics 表列：metric_id + 15 个业务列（doc_id, institution, period, city, segment,
  metric_name, value, unit, comparison_type, comparison_value, is_forecast,
  source_page, source_quote, source_url, value_text）。
- metric_id 除 lib_db.py 定义处外全仓库（含 web 模板）无引用。
- journal_mode=delete，无 WAL/SHM；web/app.py 常态运行中（只读消费方）。
- 源文件 61 个共 1478 行 = 1477 个 distinct（文件+内容） + 1 次同文件内转换后重复
  （report_ms_june_202606.jsonl 第 4 行）；1464 种内容；13 种内容各出现于 2 个文件。
- 全部 DB 行与当前源文件内容全字段精确匹配（0 行无来源）；全部源文件内容在库中。
- 最终校验规则对 1478 行只读兼容性检查零失败（规则见 §5）。

## 3. 数据相等规则（不可省略的硬约束）

完全重复/记录相等按**全部 15 个现有业务字段**精确比较，metric_id 不参与：

- 两个 NULL 视为相同；NULL 与空字符串不视为相同；
- value 与 value_text 不混同；数值按库存精确值比较，不四舍五入、不容差合并；
- source_quote 不去空格、不改标点、不做大小写/模糊匹配；
- segment、unit、is_forecast、comparison_*、doc_id、source_page、source_url
  任一差异都必须保留；
- 内容完全相同不保证真实来源相同（历史来源字段缺失），去重不描述为"已恢复真实来源"。
- 同文件内完全重复行折叠；**跨文件相同内容分别保留**（各挂各 source_file）。
  source_file 只证明内容对应本地文件，不代表原始机构来源已核实，
  也不代表两份文件是独立证据。

## 4. 设计（方案 A：稳定 source_file 身份 + 文件级事务替换）

- 身份 = 相对 metrics 根目录的文件名（posix 形式），项目整体搬目录身份不变；
  文件内容版本变化 → 整文件替换；记录内容 → 15 字段相等判断。
- 导入顺序：解析 → 转换 → 校验 → 同文件精确折叠 → 阻断检查 →
  单事务（BEGIN IMMEDIATE）内复核阻断条件 → 按 source_file 删旧 → 插新 → 提交并报告。
  删除只按 source_file，不再按 doc_id/source_url。
- 阻断条件（拒绝且零写入；CLI 预检 + load() 事务内复核，直接调 load() 无法绕过）：
  a) 数据库文件不存在或 metrics 表不存在；
  b) metrics 表缺 source_file 列（提示显式迁移入口）；
  c) 存在 source_file 为 NULL/空串的历史行（提示 Step 2B 归属未完成）；
  d) 库中 source_file 在 metrics 根目录盘上不存在（缺失来源/疑似重命名，拒绝；
     --prune、自动重命名不实现）；
  e) 文件不存在、为空、类型不支持、不在 metrics 根目录内、解析或校验失败。
- 迁移隔离：lib_db.connect() 的 SCHEMA 含 source_file 列（仅新建库生效）；
  既有库必须走显式入口 lib_db.migrate_add_source_file(conn) /
  CLI --migrate-source-file；connect() 不为该列自动 ALTER。
  导入器一律用普通 sqlite3 连接，不调用 lib_db.connect()，
  被拒绝前不得间接触发建表/迁移/commit。显式迁移只迁移，不连带导入、不归属。
- 事务：删与插同一事务；异常显式 rollback 并关闭连接；单文件事务边界；
  多文件运行逐文件独立事务，失败续跑，报告完成/失败清单，任一失败退出码非零。
- 转换兼容：read_rows()/convert_row() 与旧 loader 逐字节同规则（含 unit='text'
  但值被存成数字的既有行为），抽出为模块级函数供 Step 2B 归属复用。
  源数据行内的 source_file 键一律忽略（身份只来自文件路径）。
- metric_id 行为声明：整文件替换后 metric_id 会变（AUTOINCREMENT 重新分配），
  已验证无代码引用；/api/metrics 仅透传字段。
- 报告：每文件输出 解析数 / 折叠数 / 删旧数 / 插新数；失败带原因与记录序号。

## 5. 校验规则（V2，已对 1478 行现状零失败验证）

- institution / period / metric_name / unit / source_quote 非空；字符串字段必须是字符串；
- JSONL 顶层每条记录必须是对象（null/数组/字符串等 → 带记录位置拒绝，单文件失败不影响批次）；
- value 只接受数字或字符串：缺失/纯空白拒绝；布尔、对象、数组等复合类型拒绝；
  数字须有限（拒绝 NaN/Infinity）；不可转数字的非空字符串 → value_text（保留
  "2026Q4" 文本与 "2027"→2027.0 的既有行为）；
- comparison_value：空 → NULL，否则必须可转有限 float；
- is_forecast 先验证再转换：只接受确切的 0/1——缺省 None/"" → 0，兼容布尔、
  0.0/1.0、"0"/"1"；拒绝 0.9/1.9/-0.5 等会被 int() 截断的值及其他类型；
- period 匹配 `^20\d{2}(-(0[1-9]|1[0-2])|Q[1-4]|-H[12])?$`
  （合法月份 01-12 / 季度 1-4 / 半年 H1-H2 / 裸年份）。
- 校验失败即拒绝该文件（先完成全部校验再删旧行），不悄悄重解释历史值。

## 6. Step 2A 范围与 DoD

修改文件：
- /Users/boyang/Desktop/房地产研报/rag-app/scripts/lib_db.py
- /Users/boyang/Desktop/房地产研报/rag-app/scripts/load_external_metrics.py
- /Users/boyang/Desktop/房地产研报/rag-app/README.md
新建文件：
- /Users/boyang/Desktop/房地产研报/rag-app/tests/test_load_external_metrics.py
- 本任务文件
代码快照（动手前）：/Users/boyang/Desktop/房地产研报/rag-app/plans/snapshots/step2a-20260907-122213/
可审查差异：plans/snapshots/step2a-20260907-122213/diffs/*.diff（实施完成后生成）

DoD：
1. 新 unittest 覆盖批准书十项场景（无键文件重复导入不增；同文件更新/增/删行；
   同文件折叠+跨文件保留；共用 doc_id/URL 不互删；关键字段差异均保留；
   解析失败与事务中写入失败回滚保留旧数据；旧 schema 与 NULL/空 source_file
   阻断且拒绝后库不变；缺失来源与空文件在真实入口拒绝；新库创建与显式迁移
   幂等且迁移不连带导入；旧 CSV/JSONL 兼容）。
2. 测试走真实 load() 与至少一个 CLI 路径（进程内 main(argv)，含参数解析与退出码）。
3. `cd /Users/boyang/Desktop/房地产研报/rag-app && .venv/bin/python -m unittest discover -s tests -v`
   全绿，既有测试不回归。
4. 用实现后的模块对当前 61 个静态源文件做只读内存兼容核查（汇总 + 少量样例），
   不导入正式库。
5. README 同步：新导入行为、阻断态与解除条件、metric_id 不稳定声明、
   source_file 语义免责声明。
6. 正式 rag.db 与 metrics/ 源文件实施前后 sha256 一致；docs/chunks/FTS/vec 未被写入。
7. 外部模型 API 调用 0 次；不运行 ingest.py / build_index.py / run_eval.py。

## 7. 授权边界（本轮）

允许：修改/新建 §6 列出的文件；快照目录；临时目录内的测试数据库与夹具。
禁止：正式库任何写入（迁移/导入/回填/删除）；修改 PDF/Markdown/JSONL/CSV；
启停正式 Web；初始化 Git / 提交 / 推送；实现 2B/2C 或 metrics_maintenance.py；
调用外部模型 API。
需要改动清单外文件时先停下说明，不自行扩大范围。

## 8. 成本限制

应用外部模型 API 调用 0 次；不批量读取研报正文进模型上下文；统计用本地
SQL/Python 只回汇总；不重新抽取/解析/切片/向量化/生成摘要/跑付费评测。
开发与 Review 的模型用量如实存在，不称"零成本"。

## 9. Step 2B / 2C 记录（暂缓，未批准）

2B 修订要点（Reviewer 2026-09-07）：预览同时绑定数据库状态与来源文件状态；
执行在事务内复核数据库，使用已核验的来源文件快照；按"source_file + 内容"核算，
同文件折叠、跨文件保留（当前快照参考值：1477 条来源记录 / 1464 种内容，
仅用于核验，不硬编码）；不跨文件强行只保留一个 metric_id；操作后需
docs/chunks/FTS/vec 内容未变证据而非仅条数；正式库备份、迁移、归属、清理
均需后续明确批准。备份用 sqlite3 在线备份 API 至带时间戳独立路径，
备份须可打开、integrity_check 通过、关键表行数可核对；清理默认只预览，
显式开关才删除，预览与库状态绑定，状态变了重出预览。
2C：候选仅 note（jsonl 现有 note 键，loader 目前丢弃）；source_type /
source_name / scope / measurement_type / release_date / data_status 因无
可验证依据或与 institution 重复，不加。

## 10. 风险

- 2A 落地后正式库指标导入处于阻断态，直到 2B 归属完成——已接受的阶段性限制，
  README 如实说明；月度维护惯例中"更新指标"环节暂停。
- metric_id 不稳定依赖"全仓库无引用"结论（已 grep 验证），提审时显式声明。
- 校验严格化依赖 §5 兼容性核查（1478 行零失败）；未来新源文件不达标将被拒，
  属有意行为。

## 11. 停止条件（硬）

同一修改尝试超 3 次未通过；需改清单外文件；测试失败原因超出任务范围；
校验规则与现有静态数据产生实际冲突；正式库/源文件指纹在实施中非预期变化。
命中即停并报告：当前状态、已执行动作、已修改文件、卡点、需拍板事项。

## 12. Review 历史

- 初版计划 → Changes Requested（保留方案 A；修正来源保留与行数目标；
  隔离正式库迁移；缩小 2A 范围；完整定义幂等与相等；保留事务要求；
  只备 2A 任务单；明确验收重点；记录 2B 修订）。
- 修订计划 → 批准实施 Step 2A（2026-09-07）。
- 2A 首次提审 → Changes Requested（2026-09-07）：三个真实入口复现的校验缺口——
  is_forecast 被 int() 截断（0.9→0）；value 复合类型被 str() 落库；
  JSONL 非对象记录 AttributeError 逃逸使 CLI 批次中断。另更正测试统计口径
  （修复前实为新增 19 + 既有 20 = 39，非此前报告的 15+24）。
- 修复（V2 校验）：is_forecast 先验证后转换（确切 0/1，兼容缺省/布尔/"0"/"1"/0.0/1.0）；
  value 限定数字或字符串（拒绝布尔/对象/数组/纯空白，保留 "2026Q4" 与 "2027"→数值）；
  read_rows 逐行带行号解析、记录先验对象类型；CLI 单文件失败续跑、退出码非零。
- 修复轮 Review → Changes Requested（2026-09-07）：提出 UTF-8 解码失败逃逸 CLI。
  复核证据：`UnicodeDecodeError ⊂ UnicodeError ⊂ ValueError`，现有 except 已覆盖，
  真实 load()/main(argv) 复现确认坏编码文件已被转为 LoadError、批次续跑、退出码 1、
  旧数据不变——崩溃路径不存在。实际修复为消息增强与回归锁定：read_rows 对
  解码失败给出含文件名与字节位置的 LoadError，新增临时夹具测试（JSONL+CSV 两路径）。
  测试总计 43 项全部通过（按文件统计：lib_paths 4 + metrics_query 16 +
  load_external_metrics 23，grep 实测）。

## 13. Step 2B-1 记录（2026-09-07，只读核对与预览）

实施：新增 scripts/metrics_maintenance.py（只读子命令 preview / fingerprint /
inspect-source / inspect-db；数据库一律 mode=ro；不调 lib_db.connect()；无任何写操作入口；
匹配复用 Step 2A 的 read_rows/convert_row/BUSINESS_COLS）与
tests/test_metrics_maintenance.py（17 项，临时库，真实 CLI 调用链）。

正式库只读预览结果（完整报告 plans/reports/step2b1-preview-20260907-200112.json）：

- DB 直接统计：3369 行 / 1464 种内容 / 完全重复组 1273 / 多余行 1905；
- 源文件统计：1478 行 / 1477 个（文件+内容）组合 / 跨文件相同内容 13 组 / 同文件内重复 1 组；
- 匹配（预览推断）：唯一匹配 1451 种内容（3317 行）；多文件匹配 13 种（52 行，
  ambiguous_match，不按机构/数值猜归属）；DB 未匹配 0 行；源文件未匹配 0 行；异常 0；
- 理论参考（绑定本次快照，执行时必须重算，非目标硬编码）：
  保留 = Σ_内容(出现的不同源文件数) = 1477；删除 = 3369 − 1477 = 1892；
- 正式库 source_file 列不存在，全部记录未归属；导入阻断状态不变；
- 前后指纹：rag.db sha256 d6350016…59f52f 不变；metrics/ 聚合指纹不变；无 WAL/SHM。

待 Step 2B-2（均需单独批准，本轮未实现）：正式库在线备份与校验、停 Web 安排、
显式迁移 source_file 列、按内容归属回填（52 行多文件匹配按"每个文件各保留一条"
分配或另行拍板）、完全重复删除（事务 + 预览绑定库状态 + 失败回滚）、
操作后 docs/chunks/FTS/vec 内容未变证据。

Review 历史补充：2B-1 实现自测中发现并修复两处自身缺陷（_fmt_accounting 解包顺序、
table_stats 对缺表旧库的容错），均在临时库测试暴露后修复。

## 14. Step 2B-2A 记录（2026-09-08，安全骨架 + 临时库验证；正式库未动）

实施：metrics_maintenance.py 在保持 CLI 100% 只读（新增 `plan` 子命令）的前提下，
增加内部执行函数：build_plan / execute_plan / ensure_source_file_column /
backup_database / verify_backup / restore_database / other_tables_digest。
未修改 load_external_metrics.py 与 lib_db.py。

- 事务策略取舍：选**候选一（直接修改 metrics 表）**。单事务 BEGIN IMMEDIATE 内：
  显式 ALTER（仅缺 source_file 列时）→ UPDATE source_file（仅技术分配行）→
  DELETE 同文件多余物理行 → 提交前核验 → commit；任何异常整体回滚（DDL 事务性由
  SQLite 保证，触发器注入失败测试覆盖）。不选候选二（临时表替换）：会动 schema、
  AUTOINCREMENT 与索引契约，扩大改动面，无对应收益。
- 分配算法 det-pinned-sort-v1：已有 source_file 且与内容当前源文件集合一致 → 固定保留
  原归属（同来源多条按 metric_id 升序留第一条）；未归属物理行按「来源 POSIX 升序 ×
  metric_id 升序」配给尚无归属的来源；物理行不足/归属不一致/任一侧未匹配 → 阻断。
  每条分配记录带指纹、摘要、metric_id、行号与「技术分配，非历史事实恢复」声明。
- 计划绑定（十二节要求全量落地）：计划 JSON 含版本/时间/DB sha256/metrics 内容
  hash/schema/源文件逐个 hash 与聚合 hash/其他表摘要/目标集数量/免责声明；
  执行前 `_state_diff` 重算比对（mtime 不参与，内容为准），事务内重读库与源文件、
  重算目标集并与计划精确比对，防 TOCTOU。执行后旧计划即失效（指纹拒绝），
  重新生成的计划为 no-op，状态稳定——不允许旧计划重放。
- 正式库防误操作：CLI 无执行入口（测试用 inspect.getsource(mm.main) 证明不可达）；
  execute_plan 默认 refused，须 confirmed=True；且对 lib_paths.DB 解析相等路径
  **无条件拒绝**（比任务书要求多一层：即使测试误传正式路径也拒）；
  backup/restore 双向拒绝正式路径。本轮对正式库的全部操作 = 只读 plan 生成。
- 测试：test_metrics_maintenance.py 17 → 41 项（新增 24，改写 1：原全模块 grep 无写
  SQL 的断言改为「main() 不可达执行函数」的等价强断言）。全仓库 84 项全绿
  （lib_paths 4 + metrics_query 16 + load_external_metrics 23 + maintenance 41），
  0 失败 0 跳过；正式库指纹测试前后 sha256/size/mtime 一致。
- 正式库只读证据：plan 生成于 plans/reports/step2b2a-plan-formal-20260908.json，
  保留 1477 / 删除 1892 / 跨文件组 13，与 2B-1 理论值一致（现场重算，非硬编码）；
  rag.db sha256 d6350016…59f52f 前后不变，无 WAL/SHM/备份残留。

待 Step 2B-2B（均需单独批准，本轮未实现）：正式库在线备份与校验、停 Web 安排、
显式迁移与回填、正式删除执行（用已批准的执行函数 + 解除正式库拒绝的新开关）、
操作后 docs/chunks/FTS/vec 未变证据采集。

### 2B-2A 修复轮（2026-09-08，Review：Changes Requested 两项，均已修复）

1. **正式库硬链接保护**：`_formal_db_guard` 由仅 `Path.resolve()` 比较改为
   `os.path.samefile()` 文件身份（同 inode）比较——硬链接（resolve 不等、
   samefile 为真）与软链接均命中；候选路径不存在（samefile 无法 stat）时退回
   resolve() 归一化兜底。execute/backup/restore 全部方向（backup 源/目标、
   restore 源/目标）共用该 guard。测试覆盖硬链接五方向、软链接、
   不存在路径不误拒、正式库独立副本（内容同 inode 异）不误拒，
   全程只验证拒绝、零写入，正式库 sha256/size/mtime 断言不变。
2. **chunks_vec 内容摘要**：`_vec_content_digest` 改为对
   `SELECT chunk_id, embedding`（embedding 原始字节，sqlite-vec 以 BLOB 返回）
   全量 hash——chunk_id 与 embedding 内容都参与摘要。若扩展不可用或读不出
   embedding 原始值：计划追加 `chunks_vec_unverifiable` blocker、
   `allowed_to_execute=False`（阻断而非弱摘要放行），执行入口对该计划拒绝。
   测试覆盖：chunk_id 不变仅改一条 embedding → execute_plan 拒绝且零写入；
   扩展不可用（模拟）→ 计划阻断。正式库 r2 计划已对真实 1671 行向量
   生成含 embedding 的内容摘要（sha256=32f7509e…bb3）。
3. `CODE_VERSION` 升为 step2b2a-r2-2026-09-08（计划绑定代码版本，行为变更作废
   旧计划）。测试 84 → 89 项全绿（0 失败 0 跳过）；
   rag.db sha256 d6350016…59f52f 前后不变，无 WAL/SHM/备份残留；
   metrics/ 聚合指纹不变。

## 15. Step 2B-2B-Preflight 记录（2026-09-08，只读预检 + 正式备份；正式写入未批准）

实施：metrics_maintenance.py 增加 check_processes（ps 只读扫描 Web 与四个写入
脚本）、formal_backup（唯一正式备份入口：须显式 formal_backup_confirmed=True；
源 mode=ro；目标为 data/backups/rag-2b2-preflight-<秒级时间戳>.db，必须不存在，
不得为正式库/源库本体或硬/软链接；同秒冲突拒绝不覆盖；备份后自动验证
integrity_check/行数/schema/metrics 内容摘要/docs/chunks/FTS/vec 内容摘要；
embedding 不可验证时 warnings 明示、不声称"已充分验证"）、run_preflight
（指纹→进程→计划→备份→复核指纹→报告；写入进程运行时跳过备份并阻断）。
build_plan 增加 data_stats（重复/匹配/归属统计）与 inode/device；
PLAN_VERSION 升 2b2b-plan/1，CODE_VERSION 升 step2b2b-preflight-2026-09-08。
CLI 保持零写入/零备份子命令；_is_same_file 统一文件身份保护；
formal_backup_confirmed 与 execute_plan 的 confirmed 完全独立（execute_plan
签名不含该参数，正式库仍无条件拒绝）。

正式执行结果（本轮唯一一次正式操作，全程只读+备份）：

- 进程：web/app.py 运行中（PID 71847，只读消费方）；ingest/build_index/
  load_external_metrics/run_eval 均未运行；无其他 rag.db 相关进程；
- 计划：plans/reports/step2b2b-plan-formal-20260908-062312.json，
  2b2b-plan/1 / step2b2b-preflight-2026-09-08 / det-pinned-sort-v1，
  allowed_to_execute=True、blockers=[]（仅表示目标集可计算，非写入授权）；
  keep 1477（全部技术分配）/ delete 1892 / 跨文件组 13；
  data_stats 与 2B-1 基线一致（3369 行 / 1464 内容 / 1273 重复组 / 1905 多余行 /
  1478 源记录 / 1477 组合 / 13 跨文件 / 唯一匹配 1451 / 多文件 13 / 双向未匹配 0）；
- 备份：data/backups/rag-2b2-preflight-20260908-062313.db（23150592 字节，
  sha256 257f7648…；integrity_check ok；docs 61/chunks 1671/fts 1671/vec 1671/
  metrics 3369 与源库一致；metrics 内容摘要、docs/chunks/FTS/vec 内容摘要
  （含 1671 行 embedding 原始字节）全部一致；embedding_verified=True）；
  备份已验证可恢复到临时目标（integrity ok、数据可读，恢复副本用后删除）；
- 指纹：rag.db sha256 d6350016…59f52f / size 23150592 / mtime 1788609358 /
  inode 139590186 / device 16777232 预检前后完全不变，无 WAL/SHM；
  metrics/ 聚合 9987476f… 不变；
- 报告：plans/reports/step2b2b-preflight-20260908-062312.json（含指纹/源文件/
  进程/备份/计划/声明，ready_for_next_step=True）；
- 测试：98 项全绿（maintenance 46→55，新增 9 项预检/备份/绑定测试；0 跳过）。

待批准事项（下一步 2B-2B 正式事务，均未执行）：停 Web 安排、用已批准的
execute_plan + 新的正式授权开关执行正式迁移/回填 1477/删除 1892、
操作后 docs/chunks/FTS/vec 未变证据。

### 2B-2B-Preflight 修复轮（2026-09-08，Review：Changes Requested，已修复）

Review 在 ps 不可用的环境中实测 3 项报错（KeyError: writer_running）：根因是
check_processes() 的初始结构与 ps 扫描异常返回路径都缺 writer_running 派生键，
而 run_preflight() 直接索引该键。修复（严格限定在本 Step 范围）：
1. check_processes() 初始结构含 "writer_running": False（异常路径随初始值返回，
   结构恒定完整）；
2. run_preflight() 改稳健兜底 proc.get("writer_running",
   bool(proc.get("writer_scripts") or []))，不直接索引；
3. CODE_VERSION 升 step2b2b-preflight-r2-2026-09-08（代码变更作废旧计划，
   正式执行前必须重新生成计划——本来也是既定要求）；
4. 测试 98 → 100 项：改造写入进程替身（故意缺 writer_running 键）、新增
   缺键+空列表继续预检、缺键+非空阻断、ps 失败结构完整且不抛 KeyError 三项；
   全量 `.venv/bin/python -B -m unittest discover -s tests -v` 100 项全绿
   （0 失败 0 错误 0 跳过）。
本轮修复未创建任何正式文件：现有备份 data/backups/rag-2b2-preflight-
20260908-062313.db（sha256 257f7648…a1e8）未动未新增；rag.db sha256/size/
mtime/inode 不变；metrics/ 聚合不变；正式写入 0。
遗留决策点（下一阶段拍板）：ps 扫描失败（scan_error）时当前按兜底规则
"无写入脚本证据则继续"不阻断备份——正式执行阶段是否将进程扫描失败视为
阻断条件，建议升级为阻断，待用户批准。
