"""上下文优化测试：多轮 history / workflow 跨轮续填 / 历史滚动摘要。

覆盖：
1. workflow 跨轮字段补齐续填（missing_fields / build_draft 带 history）
2. dangling_workflow 无状态续填检测
3. _fmt_history 滚动摘要不丢早期上下文、不爆 token
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "mcp_servers"))

from workflow import (  # noqa: E402
    build_draft,
    dangling_workflow,
    detect_workflow,
    missing_fields,
)
from react_agent import _fmt_history  # noqa: E402


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.notes = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        self.notes.append(f"{'✅' if ok else '❌'} {name} {detail}")


GUIDE_ANS = ("收到，需要走「请假申请」流程。请补齐以下信息后我再帮你生成草稿单：\n"
             "· 起始日期\n· 结束日期\n（姓名/部门可用当前登录人，金额与日期请按实际填写）")


def main():
    t = TestResults()
    hist = [{"q": "我要请假", "a": GUIDE_ANS}]

    # ---- workflow 跨轮续填 ----
    t.check("续填-首轮引导缺假别+起始日期",
            len(missing_fields("我要请假", "请假申请")) >= 2,
            str(missing_fields("我要请假", "请假申请")))
    t.check("续填-补日期后只剩假别",
            missing_fields("5月6日到5月7日", "请假申请", history=hist) == ["假别(年假/事假/病假/调休)"],
            str(missing_fields("5月6日到5月7日", "请假申请", history=hist)))
    t.check("续填-再补假别后字段全齐",
            missing_fields("请年假，5月6日到5月7日", "请假申请", history=hist) == [],
            str(missing_fields("请年假，5月6日到5月7日", "请假申请", history=hist)))

    # ---- dangling_workflow 无状态续填 ----
    t.check("续填-历史有引导语则判续填",
            dangling_workflow(hist, "5月6日到5月7日") == "请假申请")
    t.check("续填-本轮已触发新流程则续填返回None",
            dangling_workflow(hist, "帮我报销") is None)
    t.check("续填-历史无引导语则None",
            dangling_workflow([{"q": "今天天气", "a": "不错"}], "随便聊聊") is None)

    # ---- build_draft 跨轮字段累积出草稿 ----
    draft = build_draft("5月6日到5月7日", "请假申请",
                        {"name": "刘洋", "department": "技术部"}, history=hist)
    t.check("草稿-跨轮生成",
            "已按「请假申请」整理草稿" in draft and "4月" not in draft, draft[:40])
    t.check("草稿-日期已从第二轮累积", "起始日期：5月6日" in draft, "")
    t.check("草稿-假别仍待补", "假别" in draft, "")

    # ---- _fmt_history 滚动摘要 ----
    long_hist = [{"q": f"第{i}轮问技术部预算剩下{1000 + i}元",
                  "a": f"第{i}轮答预算{1000 + i}元"} for i in range(30)]
    msgs = _fmt_history(long_hist)
    t.check("摘要-只产出摘要+最近8轮(消息数≤ 2+16)",
            len(msgs) <= 18, f"messages={len(msgs)}")
    t.check("摘要-早期上下文被摘要保留",
            any("技术部" in m.get("content", "") and "1000元" in m.get("content", "") for m in msgs),
            "")
    t.check("摘要-最近轮保留完整对话",
            any(m.get("content") == "第29轮问技术部预算剩下1029元" for m in msgs), "")

    short = _fmt_history([{"q": "问", "a": "答"}])
    t.check("摘要-短历史无摘要注入", len(short) == 2, str(len(short)))

    print(f"\n结果：{t.passed}/{t.passed + t.failed} 通过，{t.failed} 失败")
    for n in t.notes:
        print(f"  {n}")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()