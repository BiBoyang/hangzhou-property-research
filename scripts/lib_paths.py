"""统一路径入口：应用根目录由本文件位置推导，数据根目录可用 RAG_DATA_ROOT 覆盖。

APP  = rag-app/    应用根（web/ scripts/ data/ metrics/ eval/ 都在旗下）
BASE = APP 的上级  数据根（源 PDF 与 web研报/ 所在目录），项目数据在别处时用
                   环境变量 RAG_DATA_ROOT 指过去，应用内路径不受影响
项目整体搬目录无需改代码；DB / RAW_MD 的默认位置保持不变。
"""
from __future__ import annotations

import os
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("RAG_DATA_ROOT", str(APP.parent)))
DB = APP / "data" / "rag.db"
RAW_MD = APP / "data" / "raw_md"
WEB_DIR = BASE / "web研报"
METRICS_DIR = APP / "metrics"
SKIP_DIRS = {"rag-app", "房地产研究解决方案"}  # BASE 下扫描源 PDF 时跳过的目录
