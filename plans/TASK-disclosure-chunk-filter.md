# TASK：披露页噪声切分期过滤（治本）——待开工

**状态**：待拍板（2026-09-15 标记，防遗忘）

## 问题

投行 PDF 尾部法律披露页（analyst certification / All rights reserved / 免责声明）被当正文切分进索引，语义百搭、污染 top-k。
当前处理是查询期黑名单（`lib_search.py` `NOISE` / `_is_noise`）——治标，漏一种变体就漏一条噪声。

## 方案方向

切分期按正文特征丢弃：`lib_chunk.py` 切分后、入库前，对命中披露特征的 chunk 直接不落库
（特征可复用 NOISE 关键词 + 位置先验：披露页集中在文档末尾，heading 多为 Disclaimer/Disclosures/附录）。

## 影响面与回滚

- 动 `lib_chunk.py`（增加过滤规则）+ 全量 re-ingest + `build_index.py` 全量重建（MPS 约 20 分钟）；
- 生产库 chunks/vec 表重写——执行前先备份到 `data/backups/`；
- 验证：25 题回归不掉（≥22）+ 抽查 top-k 无披露页混入。

## 完成后动作

文章（article_draft）可补"第五轮调优"小节：披露页污染 → 查询期治标 → 切分期治本的完整闭环。
