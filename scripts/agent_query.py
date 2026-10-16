"""M2 CLI: agent 单轮深研。

用法:
  .venv/bin/python scripts/agent_query.py "高盛对一线城市房价何时见底的判断?"
  .venv/bin/python scripts/agent_query.py "……" --all        # 全量 agent(不过滤房地产)
  LLM_THINKING=1 .venv/bin/python scripts/agent_query.py "……"  # 开思考(慢、贵、更稳)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_agent import AgentConfig, run_agent  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--all", action="store_true", help="全量 agent(默认房地产 agent)")
    ap.add_argument("--max-calls", type=int, default=4)
    args = ap.parse_args()

    config = AgentConfig(topic_filter=None if args.all else "房地产", max_llm_calls=args.max_calls)
    agent_name = "全量" if args.all else "房地产"
    print(f"[{agent_name} agent] {args.query}\n", flush=True)
    res = run_agent(args.query, config)
    print(res["answer"])
    print(f"\n---\nLLM 调用 {res['llm_calls']} 次 · 工具调用 {res['tool_calls']} 次 · "
          f"tokens in={res['usage']['in']:,} out={res['usage']['out']:,}\ntrace: {res['trace_path']}")


if __name__ == "__main__":
    main()
