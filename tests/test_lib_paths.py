"""Step 3 回归：路径统一推导、默认位置不变、RAG_DATA_ROOT 覆盖、任意 cwd 启动一致。"""
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))

import lib_paths  # noqa: E402


class LibPathsTest(unittest.TestCase):
    def test_app_root_derived_from_file_location(self):
        self.assertEqual(lib_paths.APP, APP)
        self.assertTrue((lib_paths.APP / "web" / "app.py").exists())
        self.assertTrue((lib_paths.APP / "scripts" / "lib_db.py").exists())

    def test_default_locations_unchanged(self):
        self.assertEqual(lib_paths.DB, APP / "data" / "rag.db")
        self.assertEqual(lib_paths.RAW_MD, APP / "data" / "raw_md")
        self.assertEqual(lib_paths.METRICS_DIR, APP / "metrics")
        if not lib_paths.DB.exists():
            self.skipTest("正式库不存在（CI/全新克隆），跳过数据目录存在性检查")
        self.assertTrue(lib_paths.DB.exists())  # 当前数据目录仍能找到

    def test_base_defaults_to_parent_and_env_overrides(self):
        self.assertEqual(lib_paths.BASE, APP.parent)
        os.environ["RAG_DATA_ROOT"] = "/tmp/fake-data-root"
        try:
            mod = importlib.reload(lib_paths)
            self.assertEqual(mod.BASE, Path("/tmp/fake-data-root"))
            self.assertEqual(mod.WEB_DIR, Path("/tmp/fake-data-root") / "web研报")
            self.assertEqual(mod.DB, APP / "data" / "rag.db")  # 应用内路径不受数据根影响
        finally:
            os.environ.pop("RAG_DATA_ROOT", None)
            importlib.reload(lib_paths)  # 还原默认，避免影响后续测试
        self.assertEqual(lib_paths.BASE, APP.parent)

    def test_same_db_from_any_cwd(self):
        """DoD：从项目目录以外启动时路径仍正确（subprocess 隔离验证 cwd 无关性）。"""
        script = (
            "import sys; sys.path.insert(0, r'%s'); "
            "import lib_paths, lib_metrics_query; "
            "assert lib_paths.DB == lib_metrics_query.DB; "
            "print(lib_paths.DB)" % (APP / "scripts")
        )
        with tempfile.TemporaryDirectory() as tmp:  # cwd 在项目外
            r = subprocess.run(
                [sys.executable, "-c", script], cwd=tmp,
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(Path(r.stdout.strip()), APP / "data" / "rag.db")


if __name__ == "__main__":
    unittest.main()
