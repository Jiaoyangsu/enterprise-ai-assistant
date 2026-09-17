"""security_data_safety Skill 通用例（纯标准库，无需安装依赖）。"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from security_skill import redact_pii, risk_review_text, sanitize_for_storage

SKILL_PY = Path(__file__).resolve().parent.parent / "security_skill.py"


class TestRedactPii(unittest.TestCase):
    def test_phone(self):
        r = redact_pii("电话 13812345678，确认")
        self.assertEqual(r["redacted"], "电话 138****5678，确认")
        self.assertEqual(r["masked"], {"phone": 1})

    def test_id_card(self):
        r = redact_pii("身份证 110101199001011234")
        self.assertEqual(r["redacted"], "身份证 110101********1234")

    def test_email(self):
        r = redact_pii("邮箱 zhangsan@example.com")
        self.assertIn("zh***@example.com", r["redacted"])

    def test_bank_card(self):
        r = redact_pii("卡号 6222021234567890123")
        self.assertEqual(r["redacted"], "卡号 6222********0123")

    def test_credit_code(self):
        r = redact_pii("统一社会信用代码 91310000MA1FL20Y6A")
        self.assertIn("91********6A", r["redacted"])

    def test_multi_types(self):
        r = redact_pii("电话13812345678，身份证110101199001011234，邮箱a@b.cn")
        self.assertEqual(r["count"], 3)
        self.assertIn("138****5678", r["redacted"])

    def test_no_pii_unchanged(self):
        r = redact_pii("普通文本没有隐私信息")
        self.assertEqual(r["count"], 0)
        self.assertEqual(r["redacted"], "普通文本没有隐私信息")

    def test_empty(self):
        r = redact_pii("")
        self.assertEqual(r, {"original": "", "redacted": "", "count": 0, "masked": {}})

    def test_plain_number_not_masked(self):
        r = redact_pii("预算数字是 5000000 元")
        self.assertEqual(r["count"], 0)


class TestRiskReview(unittest.TestCase):
    def test_default_hits(self):
        r = risk_review_text("我们拿到了竞品的数据")
        self.assertFalse(r["safe"])
        self.assertEqual(r["hits"], ["竞品"])

    def test_custom_words_override(self):
        r = risk_review_text("讨论薪资倒挂", words=["泄密"])
        self.assertTrue(r["safe"], "自定义词表应替换默认词表")

    def test_custom_words_hit(self):
        r = risk_review_text("含有专利机密", words=["机密"])
        self.assertFalse(r["safe"])

    def test_safe_text(self):
        r = risk_review_text("今天天气不错")
        self.assertTrue(r["safe"])
        self.assertEqual(r["hits"], [])

    def test_empty(self):
        r = risk_review_text("")
        self.assertTrue(r["safe"])


class TestSanitize(unittest.TestCase):
    def test_dirty_not_ready(self):
        r = sanitize_for_storage("电话13800001111，讨论薪资倒挂")
        self.assertFalse(r["ready_for_storage"])
        self.assertEqual(r["pii_removed"], 1)

    def test_clean_ready(self):
        r = sanitize_for_storage("报销单号 BX-2024-001")
        self.assertTrue(r["ready_for_storage"])


class TestCLI(unittest.TestCase):
    def run_cli(self, args, stdin=None):
        p = subprocess.run(
            [sys.executable, str(SKILL_PY), *args],
            input=stdin, capture_output=True, text=True,
        )
        return p.returncode, json.loads(p.stdout)

    def test_cli_redact(self):
        code, out = self.run_cli(["redact_pii", "--text", "电话13812345678"])
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["result"]["redacted"], "电话138****5678")

    def test_cli_risk_with_words(self):
        code, out = self.run_cli(
            ["risk_review_text", "--text", "我们拿到了竞品数据", "--words", "竞品,内幕"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(out["result"]["hits"], ["竞品"])

    def test_cli_input_json(self):
        payload = json.dumps({"tool": "sanitize_for_storage", "text": "电话13800001111，讨论薪资倒挂"})
        code, out = self.run_cli(["sanitize_for_storage", "--input-json"], stdin=payload)
        self.assertEqual(code, 0)
        self.assertFalse(out["result"]["ready_for_storage"])

    def test_cli_unknown_tool(self):
        p = subprocess.run(
            [sys.executable, str(SKILL_PY), "not_a_tool"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()