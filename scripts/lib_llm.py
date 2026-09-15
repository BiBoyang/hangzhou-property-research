"""生成层：OpenAI 兼容接口，默认 Kimi 开放平台，一行环境变量换 provider。

配置（env）:
  LLM_BASE_URL  默认 https://api.moonshot.cn/v1；使用 DEEPSEEK_API_KEY 时默认 https://api.deepseek.com/v1
  LLM_API_KEY   通用 OpenAI 兼容 key；未设置时回退到 DEEPSEEK_API_KEY
  DEEPSEEK_API_KEY  DeepSeek key（可直接使用已有配置）
  LLM_MODEL     默认 kimi-k2.6；使用 DEEPSEEK_API_KEY 且未显式设置时为 deepseek-chat
"""
from __future__ import annotations

import os

SYSTEM_PROMPT = """你是房地产研究助手，基于检索到的研报片段回答问题。规则：
1. 回答中每个关键事实必须标注来源，格式 [机构 日期]，如 [高盛 2026-04-16]
2. 不同机构观点冲突时，并列呈现各方观点及各自来源，不擅自调和
3. 检索片段里没有的信息，明确说"现有研报未覆盖"，禁止编造数字
4. 数字必须原文照抄，包括单位和统计期
5. 回答用中文，简洁，先结论后论据"""


def llm_config() -> dict[str, str]:
    """解析 LLM 配置；不打印 key。显式 LLM_* 配置优先，DeepSeek 作为回退。"""
    generic_key = os.environ.get("LLM_API_KEY")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if generic_key:
        return {
            "api_key": generic_key,
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.moonshot.cn/v1"),
            "model": os.environ.get("LLM_MODEL", "kimi-k2.6"),
            "provider": "generic",
        }
    if deepseek_key:
        return {
            "api_key": deepseek_key,
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            "model": os.environ.get("LLM_MODEL", "deepseek-chat"),
            "provider": "deepseek",
        }
    raise RuntimeError("未配置 LLM_API_KEY 或 DEEPSEEK_API_KEY")


def build_user_prompt(query: str, chunks: list[dict], metrics_block: str = "") -> str:
    parts = [f"【检索片段 {i+1}】{c['institution']} {c['report_date']} 《{c['title']}》\n{c['text']}"
             for i, c in enumerate(chunks)]
    blocks = []
    if metrics_block:
        blocks.append(metrics_block)
    blocks.append("以下是检索到的研报片段：\n\n" + "\n\n".join(parts))
    return "\n\n".join(blocks) + f"\n\n问题：{query}"


def _client_and_payload(query: str, chunks: list[dict], metrics_block: str):
    from openai import OpenAI

    config = llm_config()
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )
    payload = dict(
        model=config["model"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(query, chunks, metrics_block)},
        ],
        # 思考模式提速开关：默认关（秒回）；LLM_THINKING=1 开启深度推理（慢但引证更稳）
        **({"extra_body": {"thinking": {"type": "disabled"}}}
           if os.environ.get("LLM_THINKING") != "1" else {}),
    )
    return client, payload


def chat(query: str, chunks: list[dict], metrics_block: str = "") -> str:
    client, payload = _client_and_payload(query, chunks, metrics_block)
    resp = client.chat.completions.create(**payload)
    return resp.choices[0].message.content


def chat_stream(query: str, chunks: list[dict], metrics_block: str = ""):
    """流式生成，逐段 yield 文本 delta。"""
    client, payload = _client_and_payload(query, chunks, metrics_block)
    with client.chat.completions.create(**payload, stream=True) as stream:
        for event in stream:
            delta = event.choices[0].delta.content if event.choices else None
            if delta:
                yield delta
