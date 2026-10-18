# Session 2026-10-18(交接任务:文章与开源)

交接来源:`plans/handover/2026-10-18-文章与开源.md`。三件任务全部完成,两仓零提交,零 push。

## 方案记录(plan-first,用户不在线,方案直接执行)

**任务 1 篇三初稿**:通读篇一/篇二对齐笔调(第一人称、失败实录、数字说话);结构按交接书七段式微调合并(循环工程细节并入架构节、复读 bug 并入评测节);红线=不引研报原文,机构名沿用前两篇口径;数字全部出自 `plans/reports/yanbaoeval-integration.md` 与 SESSION-2026-10-16。

**任务 2 public-mini**:官方公开出版物 6-10 篇(统计局公报/房地产市场月度、央行金融统计/货币政策执行报告)→ manifest + docs/ → `yanbaoeval gen`(ds-flash)出题 → 手工 N 类 3 道 → validate 全过 → 人工抽查 → 尝试 ragapp 隔离副本实战(scripts/config 拷贝到 /tmp + 独立空库 + RAG_DATA_ROOT 指独立数据根,主库零接触),跑不通就只交付数据集。

**任务 3 开源检查单**:先取证(git ls-files / git grep 密钥 / pytest / demo 实测)再列清单;历史隔离方案写清不执行。

## 执行实录

### 1. 篇三初稿 → `rag-app/article3_draft.md`
- 标题用默认《贵一倍的模型,正确率打平:我把研报 RAG 改成 agent 后的实测账单》+ 2 个备选。
- 数字核对:A/B(85/35/8 元,20s vs 9min)、M1(4.9min/4元/2126条)、PUA(9篇,+76条,2126→2185)、13题3元、复读 bug、44% 拒答、-4pp 方差事件(57.85 vs 57.8)、查询规范化 64.8%→70.4%(+5.6pp,retrieval 33→27,8好2坏)、表格三轮 66.7/60.2/64.8、mini 74.1 vs 70.4(±17pp)、full 64.4 vs 56.8(+7.6pp,漏答 -11pp)、引用精确率 38.9 vs 27.6、单轮 ¥12.30 vs ¥5.62(2.2倍)。
- 自审修正:总账单无法用已有记录证实(r1-r4 轮次成本无记录),删去总计句式,只列有据明账;多轮例子改为实录"那瑞银呢?"。

### 2. 公开数据集 → `YanbaoEval/datasets/public-mini/`
- 7 篇官方公开出版物(下载均 `env -u *_proxy`):2025 统计公报(统计局)、2026 1-6/1-8 月全国房地产市场基本情况×2、前三季度金融统计数据报告(央行)、人民币汇率政策立场(央行)、货币政策执行报告 2026Q1/Q2(PDF,央行)。HTML→Markdown 转制保留数据表格(公报 232 行表),PDF 保留原格式;来源 URL 写入每篇文头与 README。
- 出题:`yanbaoeval gen --num 3` 出 19 题(闭卷过滤剔 2 题)+ 手工 N 类 3 题 = 22 题;**validate 全部通过**。
- 人工抽查 4 题发现 1 题概念误读(pm-004:"待售3年以下"被写成"竣工不足三年"),已修正题干并复验通过——gen 出题人工终审的必要性再次证实。
- **实战跑通**:隔离副本方案成功(/tmp/ragapp-pm + /tmp/pm-data,120 chunks,flash/temp=0/all 配置,22 题):正确率 84.2% [62.4,94.5]、幻觉 10.5%、**诚实拒答 100%(3/3)**、漏答 5.3%、引用命中 68.8%/精确 39.3%、归因 retrieval×2 generation×1;报告 `YanbaoEval/runs/public-mini-flash/report.html`。已知口径:2 篇 PDF 在隔离库以 pypdf 转 md 入库,文档名与数据集 .pdf 不一致,引用精确率被结构性拉低(答案指标不受影响)。
- **发现 rag-app 真实 bug(未修,供主会话决策)**:全新空库上 `ingest.py` 必炸——lib_db SCHEMA 的 docs 表无 topic 列(topic 靠 backfill 迁移添加),而 store_doc 的 INSERT 已带 topic 列。隔离副本里打了补丁才跑通;主仓修法建议:lib_db SCHEMA 的 docs 建表语句补 topic 列(migration 保留无害)。

### 3. 开源检查单 → `YanbaoEval/docs/open-source-checklist.md`
取证结论:
- **阻断项:datasets/ragapp-mini/docs(15 篇)与 ragapp-full/docs(68 篇)版权研报已 commit 进 git 历史**(git ls-files 实证);方案=新仓重来(推荐,列了干净文件清单)或 filter-repo,先整目录 tar 备份;不执行。
- configs/ragapp-*.yaml 4 个含本机绝对路径;adapters/ragapp.py 为私有适配器——建议开源版不带入。
- 无 sk- 密钥硬编码;runs/ 从未进历史;.gitignore 覆盖 OK;pytest 30 passed(YanbaoEval/.venv);mock+demo-mini 全链路实测通过。
- README 待补:定位声明("轻量命令行工具"+"不对标 KnowEval 功能")、双 demo 分工;CI/issue/PR 模板只给建议不实施。

## 成本与纪律

- LLM 成本:gen 出题 21 次调用 + 实战 22 题×≤4 次调用 + demo 验证 9 题,全 ds-flash,合计约 1-2 元(预算 ¥20 内)。
- rag-app 测试:`unittest discover -s tests` → **357 项全绿**。
- 两仓零 commit、零 push;rag-app 主库 `data/rag.db` 零接触(隔离副本方案);yanbaoeval 主仓只新增文件未改既有代码。
- 遗留:/tmp/ragapp-pm 与 /tmp/pm-data 为一次性隔离产物,可删;`YanbaoEval/runs/demo-verify` 为验证产物(gitignored)。

## 改动清单(全部未提交,等主会话审核)

rag-app:
- 新增 `article3_draft.md`(篇三初稿)
- 新增 `sessions/SESSION-2026-10-18-handover.md`(本文件)

YanbaoEval:
- 新增 `datasets/public-mini/`(manifest.json / README.md / docs×7 / questions-gen.jsonl / questions-n.jsonl / questions.jsonl)
- 新增 `configs/publicmini-flash.yaml`(指向 /tmp 隔离副本;若开源则不入仓——已写入检查单 0.2 同类处置)
- 新增 `docs/open-source-checklist.md`
- 新增 `runs/public-mini-flash/`、`runs/demo-verify/`(gitignored,不影响仓库)
