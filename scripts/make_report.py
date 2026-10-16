"""M4 CLI: 结构化研判报告。

用法:
  .venv/bin/python scripts/make_report.py "上海房价是否已见底"
  .venv/bin/python scripts/make_report.py "……" --all   # 全量语料(不限房地产)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_report import run_report  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    print(f"生成研判报告: {args.topic} ...", flush=True)
    res = run_report(args.topic, topic_filter=None if args.all else "房地产")
    print(f"完成: {res['path']}")
    print(f"LLM 调用 {res['llm_calls']} 次 · tokens in={res['usage']['in']:,} "
          f"out={res['usage']['out']:,} · 合成{'成功' if res['synthesized'] else '失败(规则拼装)'}")
    failed = [s['name'] for s in res['sections'] if s['error']]
    if failed:
        print(f"⚠️ 失败节: {'、'.join(failed)}")


if __name__ == "__main__":
    main()
