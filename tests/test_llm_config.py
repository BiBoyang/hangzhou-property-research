"""LLM 配置契约测试：只测 provider 选择，不发起网络请求。"""
import os
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_llm  # noqa: E402


class LlmConfigTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("LLM_API_KEY", "DEEPSEEK_API_KEY", "LLM_BASE_URL", "LLM_MODEL")}

    def tearDown(self):
        for k, value in self.saved.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value

    def test_deepseek_fallback_uses_existing_key_without_network(self):
        for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
            os.environ.pop(k, None)
        os.environ["DEEPSEEK_API_KEY"] = "test-deepseek-key"
        config = lib_llm.llm_config()
        self.assertEqual(config["provider"], "deepseek")
        self.assertEqual(config["api_key"], "test-deepseek-key")
        self.assertEqual(config["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(config["model"], "deepseek-chat")

    def test_explicit_generic_config_wins(self):
        os.environ["LLM_API_KEY"] = "generic-key"
        os.environ["DEEPSEEK_API_KEY"] = "deepseek-key"
        os.environ["LLM_BASE_URL"] = "https://example.test/v1"
        os.environ["LLM_MODEL"] = "custom-model"
        config = lib_llm.llm_config()
        self.assertEqual(config["provider"], "generic")
        self.assertEqual(config["api_key"], "generic-key")
        self.assertEqual(config["base_url"], "https://example.test/v1")
        self.assertEqual(config["model"], "custom-model")

    def test_missing_keys_is_explicit(self):
        for k in ("LLM_API_KEY", "DEEPSEEK_API_KEY"):
            os.environ.pop(k, None)
        with self.assertRaisesRegex(RuntimeError, "LLM_API_KEY 或 DEEPSEEK_API_KEY"):
            lib_llm.llm_config()


if __name__ == "__main__":
    unittest.main()
