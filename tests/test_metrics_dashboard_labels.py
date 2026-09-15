"""指标看板中文化（metrics.html 展示层标签）契约测试。

只做静态模板契约 + 可选 node 行为验证 + 正式库只读指纹比对：
不启动正式 Web 服务、不写数据库、不调外部网络、不调 LLM。
映射真值取自 plans/prompts/STEP-METRICS-DASHBOARD-LABELS-AGENT.md 的展示映射表。
"""
import hashlib
import inspect
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))
sys.path.insert(0, str(APP / "web"))

import app as web_app  # noqa: E402

TEMPLATE = APP / "web" / "templates" / "metrics.html"

# STEP 展示映射表的真值（19 个指标 code）
EXPECTED_METRIC_LABELS = {
    "secondary_home_price_index": "二手房价格指数",
    "new_home_price_index": "新房价格指数",
    "avg_price": "成交均价",
    "mom_change_pct": "环比涨跌幅",
    "yoy_change_pct": "同比涨跌幅",
    "secondary_volume_units": "二手房成交套数",
    "new_home_volume_units": "新房成交套数",
    "new_home_volume_area": "新房成交面积",
    "developer_sales_amount": "房企销售额",
    "inventory_months": "新房库存去化周期",
    "listing_units": "挂牌量",
    "rent_level": "租金水平",
    "rental_yield": "租金回报率",
    "land_transaction_value": "土地成交金额",
    "premium_rate_pct": "土地溢价率",
    "mortgage_rate": "房贷利率",
    "provident_fund_rate": "公积金贷款利率",
    "price_bottom_timing": "房价触底时点预测",
    "price_change_forecast_pct": "房价涨跌幅预测",
}
EXPECTED_UNIT_LABELS = {
    "CNY_per_sqm": "元/平方米",
    "CNY_100m": "亿元",
    "10k_sqm": "万平方米",
    "units": "套",
    "months": "个月",
    "pct": "%",
    "index": "指数",
    "CNY_per_sqm_month": "元/平方米·月",
    "text": "文本",
}
EXPECTED_SEGMENT_LABELS = {
    "new_home": "新房",
    "secondary_home": "二手房",
    "land": "土地",
    "rental": "租赁",
    "macro": "宏观",
    "developer": "房企",
}
EXPECTED_COMPARISON_LABELS = {"mom": "环比", "yoy": "同比"}


def _html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _extract_map(name: str) -> dict:
    """提取模板内 JSON 纯格式的 const 映射（双引号键值，无注释无尾逗号）。"""
    m = re.search(rf"const {name} = (\{{.*?\}});", _html(), re.S)
    assert m, f"metrics.html 缺少 const {name}"
    return m.group(1)


class TemplateContractTest(unittest.TestCase):
    """模板静态契约：映射内容、回退规则、code 保留、转义、CDN、API 源码。"""

    def test_metric_labels_cover_step_codes(self):
        import json
        got = json.loads(_extract_map("METRIC_LABELS"))
        for code, label in EXPECTED_METRIC_LABELS.items():
            self.assertEqual(got.get(code), label, f"{code} 中文 label 不符")

    def test_core_metric_labels_exact(self):
        import json
        got = json.loads(_extract_map("METRIC_LABELS"))
        self.assertEqual(got["avg_price"], "成交均价")
        self.assertEqual(got["secondary_volume_units"], "二手房成交套数")
        self.assertEqual(got["inventory_months"], "新房库存去化周期")

    def test_all_labels_chinese_and_not_code(self):
        import json
        got = json.loads(_extract_map("METRIC_LABELS"))
        self.assertTrue(got)
        for code, label in got.items():
            self.assertNotEqual(label, code, f"{code} 不能用原 code 当显示名")
            self.assertRegex(label, r"[\u4e00-\u9fff]", f"{code} label 需含中文")

    def test_unit_labels_exact(self):
        import json
        self.assertEqual(json.loads(_extract_map("UNIT_LABELS")), EXPECTED_UNIT_LABELS)

    def test_segment_labels_exact(self):
        import json
        self.assertEqual(json.loads(_extract_map("SEGMENT_LABELS")),
                         EXPECTED_SEGMENT_LABELS)

    def test_comparison_labels_exact(self):
        import json
        self.assertEqual(json.loads(_extract_map("COMPARISON_LABELS")),
                         EXPECTED_COMPARISON_LABELS)

    def test_forecast_labels(self):
        import json
        got = json.loads(_extract_map("FORECAST_LABELS"))
        self.assertEqual(got["0"], "实际观测")
        self.assertEqual(got["1"], "预测")

    def test_metric_descriptions_present(self):
        import json
        got = json.loads(_extract_map("METRIC_DESCRIPTIONS"))
        for code in EXPECTED_METRIC_LABELS:
            self.assertTrue(got.get(code), f"{code} 缺指标解释")
        self.assertIn("消化库存所需月份", got["inventory_months"])

    def test_fallback_functions_present(self):
        """未知 code 一律原值回退；空城市显示"未归一化城市"。"""
        h = _html()
        for fn, table in (("metricLabel", "METRIC_LABELS"), ("unitLabel", "UNIT_LABELS"),
                          ("segmentLabel", "SEGMENT_LABELS"),
                          ("comparisonLabel", "COMPARISON_LABELS")):
            self.assertRegex(
                h, rf"const {fn} = \(code\) => {table}\[code\] \?\? code;",
                f"{fn} 缺少未知 code 原值回退")
        self.assertRegex(
            h, r"const forecastLabel = \(v\) => FORECAST_LABELS\[String\(v\)\] \?\? String\(v \?\? ''\);")
        self.assertIn("const cityLabel = (c) => (c ? c : '未归一化城市');", h)

    def test_option_value_keeps_code_and_display_rewritten(self):
        """下拉框 value 仍是英文 code（查询用），显示文本改写为 中文（code）。"""
        h = _html()
        self.assertIn('<option value="{{n}}">', h)
        self.assertIn("o.textContent = metricLabel(o.value) + '（' + o.value + '）';", h)

    def test_table_escapes_db_text(self):
        """表格模板字面量中除数值外的插值一律经 esc()；esc 覆盖五类字符。"""
        h = _html()
        self.assertRegex(
            h, r"const esc = \(s\) => String\(s \?\? ''\)\.replace\(/\[&<>\"'\]/g")
        m = re.search(r"rows\.map\(x => `(.*?)`\)\.join", h, re.S)
        assert m, "表格行模板缺失"
        allow = {"x.value", "x.is_forecast ? ' class=\"fc\"' : ''"}
        exprs = re.findall(r"\$\{([^}]*)\}", m.group(1))
        self.assertTrue(exprs)
        for e in exprs:
            if e in allow:
                continue
            self.assertTrue(e.startswith("esc("), f"表格插值未经 esc(): {e}")

    def test_code_preserved_in_data_attribute_and_query(self):
        """原始 code 保留在 data-* 与查询参数中，查询仍用英文 code。"""
        h = _html()
        self.assertIn('data-metric=""', h)
        self.assertIn("setAttribute('data-metric', m)", h)
        self.assertIn("encodeURIComponent(m)", h)
        self.assertIn("encodeURIComponent(c)", h)

    def test_no_external_cdn(self):
        h = _html()
        self.assertIn('src="/static/echarts.min.js"', h)
        self.assertNotIn("jsdelivr", h)
        self.assertNotIn("http://", h)
        self.assertNotIn("https://", h)

    def test_nav_links_preserved(self):
        h = _html()
        for href in ('href="/"', 'href="/overview"', 'href="/metrics"', 'href="/docs"'):
            self.assertIn(href, h)

    def test_api_metrics_source_unchanged(self):
        src = inspect.getsource(web_app.api_metrics)
        self.assertIn("SELECT * FROM metrics WHERE 1=1", src)
        self.assertIn("AND metric_name=?", src)
        self.assertIn("AND city=?", src)
        self.assertIn("ORDER BY period", src)
        self.assertIn('return {"rows": rows}', src)

    def test_metrics_page_source_unchanged(self):
        src = inspect.getsource(web_app.metrics_page)
        self.assertIn("SELECT DISTINCT metric_name FROM metrics", src)
        self.assertIn("SELECT DISTINCT city FROM metrics", src)
        self.assertIn('"metrics.html"', src)
        self.assertIn('"metric_names": names', src)
        self.assertIn('"cities": cities', src)


@unittest.skipUnless(shutil.which("node"), "无 node，跳过 JS 行为验证")
class NodeBehaviorTest(unittest.TestCase):
    """用 node 实际执行模板中的映射与回退函数（提取 LABELS-MAPS 标记间纯 JS）。"""

    def _extract_script(self) -> str:
        m = re.search(r"LABELS-MAPS-BEGIN[^\n]*\n(.*?)LABELS-MAPS-END", _html(), re.S)
        assert m, "模板缺少 LABELS-MAPS 标记"
        lines = [ln for ln in m.group(1).splitlines()
                 if ln.strip() and not ln.strip().startswith("//")]
        return "\n".join(lines)

    def test_inline_script_syntax(self):
        """整段内联脚本过 node --check 语法校验（不执行）。"""
        blocks = re.findall(r"<script>(.*?)</script>", _html(), re.S)
        self.assertTrue(blocks, "模板缺少内联脚本")
        p = subprocess.run(["node", "--check"], input="\n;\n".join(blocks),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_label_functions_fallback_behavior(self):
        program = self._extract_script() + r"""
const assert = require('assert');
assert.strictEqual(metricLabel('nonexistent_code'), 'nonexistent_code');
assert.strictEqual(metricLabel('avg_price'), '成交均价');
assert.strictEqual(metricLabel('secondary_volume_units'), '二手房成交套数');
assert.strictEqual(metricLabel('inventory_months'), '新房库存去化周期');
assert.strictEqual(metricDescription('nonexistent_code'), '');
assert.strictEqual(unitLabel('weird_unit'), 'weird_unit');
assert.strictEqual(unitLabel('CNY_per_sqm'), '元/平方米');
assert.strictEqual(unitLabel('pct'), '%');
assert.strictEqual(unitLabel('10k_sqm'), '万平方米');
assert.strictEqual(segmentLabel('odd_segment'), 'odd_segment');
assert.strictEqual(segmentLabel('secondary_home'), '二手房');
assert.strictEqual(comparisonLabel('qoq'), 'qoq');
assert.strictEqual(comparisonLabel('mom'), '环比');
assert.strictEqual(comparisonLabel('yoy'), '同比');
assert.strictEqual(forecastLabel(0), '实际观测');
assert.strictEqual(forecastLabel(1), '预测');
assert.strictEqual(forecastLabel(2), '2');
assert.strictEqual(forecastLabel(null), '');
assert.strictEqual(cityLabel(''), '未归一化城市');
assert.strictEqual(cityLabel('杭州'), '杭州');
console.log('NODE-OK');
"""
        p = subprocess.run(["node"], input=program, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("NODE-OK", p.stdout)


class _FormalGuard(unittest.TestCase):
    """正式库只读指纹保护：测试前后 size/mtime/sha256 与 metrics/ 聚合 hash 不变。"""

    FORMAL_DB = APP / "data" / "rag.db"

    @classmethod
    def setUpClass(cls):
        if not cls.FORMAL_DB.exists():
            raise unittest.SkipTest("正式库不存在，跳过只读验证")
        st = cls.FORMAL_DB.stat()
        cls.before = (st.st_size, st.st_mtime,
                      hashlib.sha256(cls.FORMAL_DB.read_bytes()).hexdigest())
        cls.metrics_before = cls._metrics_hash()
        from fastapi.testclient import TestClient

        cls.client = TestClient(web_app.app)

    @classmethod
    def _metrics_hash(cls) -> str:
        h = hashlib.sha256()
        for f in sorted((APP / "metrics").iterdir()):
            if f.suffix in (".jsonl", ".csv"):
                h.update(f.name.encode())
                h.update(hashlib.sha256(f.read_bytes()).digest())
        return h.hexdigest()

    @classmethod
    def tearDownClass(cls):
        st = cls.FORMAL_DB.stat()
        after = (st.st_size, st.st_mtime,
                 hashlib.sha256(cls.FORMAL_DB.read_bytes()).hexdigest())
        assert after == cls.before, f"正式库指纹变化: {cls.before} -> {after}"
        assert cls._metrics_hash() == cls.metrics_before, "metrics/ 聚合 hash 变化"
        assert not (APP / "data" / "rag.db-wal").exists(), "出现 WAL 文件"
        assert not (APP / "data" / "rag.db-shm").exists(), "出现 SHM 文件"


@unittest.skipUnless(_FormalGuard.FORMAL_DB.exists(), "正式库不存在，跳过只读验证")
class FormalReadonlyTest(_FormalGuard):
    """正式库上渲染指标页与查询 API：只读、中文化脚本随页下发、code 仍为查询值。"""

    def test_metrics_page_and_api_readonly(self):
        r = self.client.get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("指标看板", r.text)
        self.assertIn("METRIC_LABELS", r.text)      # 中文化映射随页下发
        self.assertIn("成交均价", r.text)
        self.assertIn('<option value="avg_price">', r.text)  # option value 仍是英文 code

        r2 = self.client.get("/api/metrics",
                             params={"metric": "avg_price", "city": "杭州"})
        self.assertEqual(r2.status_code, 200)
        rows = r2.json()["rows"]
        self.assertGreaterEqual(len(rows), 1)
        for key in ("metric_name", "period", "city", "value", "unit",
                    "comparison_type", "is_forecast", "institution"):
            self.assertIn(key, rows[0])
        self.assertEqual(rows[0]["metric_name"], "avg_price")


if __name__ == "__main__":
    unittest.main()
