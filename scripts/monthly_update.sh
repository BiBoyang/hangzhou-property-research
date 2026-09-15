#!/usr/bin/env bash
# 月度维护一键化：增量入库 → 索引 → 指标导入 → 冒烟评测
# 用法：./scripts/monthly_update.sh
# 注意：build_index 为全量向量重建（MPS 约 20 分钟）；冒烟评测 1 题需 LLM_API_KEY 或 DEEPSEEK_API_KEY
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python

echo "==> [1/4] 增量入库（PDF + web研报，无新增则秒退）"
env -u http_proxy -u https_proxy -u all_proxy HF_ENDPOINT=https://hf-mirror.com "$PY" scripts/ingest.py

echo "==> [2/4] 构建检索索引（FTS 重建 + 向量全量重建，耗时较长）"
"$PY" scripts/build_index.py

echo "==> [3/4] 指标导入（source_file 幂等替换）"
"$PY" scripts/load_external_metrics.py

echo "==> [4/4] 冒烟评测（1 题）"
HF_HUB_OFFLINE=1 "$PY" scripts/run_eval.py --limit 1

echo "完成。完整 25 题回归：$PY scripts/run_eval.py"
