# 测试输出与 LLM 配置

## 目标

让本地 unittest 输出只保留真正需要关注的结果，并支持在用户主动选择时使用已有 `DEEPSEEK_API_KEY` 做生成评测。

## 约束

- 默认 unittest 不调用外部 API；
- LLM 评测必须显式运行 `scripts/run_eval.py`；
- `LLM_API_KEY` 优先，未设置时回退 `DEEPSEEK_API_KEY`；
- DeepSeek 默认端点 `https://api.deepseek.com/v1`、模型 `deepseek-chat`；
- `run_eval.py --limit N` 用于限制调用题数；
- key 不打印；
- 测试配置只验证 provider 选择，不发网络请求；
- 正式数据库和原始指标文件不受影响。

## 变更

- `scripts/lib_llm.py`：增加不暴露 key 的 `llm_config()`，支持 DeepSeek 回退；
- `scripts/run_eval.py`：增加 `--limit`；
- `tests/test_llm_config.py`：provider、优先级和缺少 key 测试；
- `tests/test_metrics_maintenance.py`：捕获预期 CLI 输出，关闭测试连接；
- `README.md`：补充 DeepSeek 配置方式。

## 验证结果

- unittest：260 项通过；预期的 CLI/导入拒绝输出已由测试捕获；剩余 Starlette 弃用提示来自第三方依赖，不影响结果。
- 环境中的 `DEEPSEEK_API_KEY` 已被 `llm_config()` 识别；一次真实 DeepSeek 最小请求成功，使用 `https://api.deepseek.com/v1` / `deepseek-chat`。
- `run_eval.py --limit 1` 可限制生成调用数量；embedding 已确认可用 `HF_HUB_OFFLINE=1` 离线加载，避免 Hugging Face HEAD 网络检查。
