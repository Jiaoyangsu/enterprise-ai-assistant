"""路由器：根据问题文本判断应调用哪个工具（规则引擎）。

作用：
- 验证测试集里每题标明的 expected_tool 与规则判断一致
- 模拟 persona 的「混合路由」逻辑，自动化跑 6×5 测试集
- 后续可替换为模型路由（让 LLM 判断），但规则版可做 baseline
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_servers"))


def route(question: str) -> str:
    """返回问题应路由到的工具名。若无法判断返回 'unknown'。"""
    q = question

    # 写入操作优先判断（副作用铁律字段）
    triggers_write_leave = ["提交", "申请年假", "申请事假", "请假的申请"]
    if any(t in q for t in triggers_write_leave) and ("请假" in q or "年假" in q or "事假" in q):
        if any(t in q for t in ["月", "日", "-", "到"]):
            return "create_leave_request"

    if any(w in q for w in ["开单", "报障", "创建工单", "开个工单", "报个工单"]):
        return "create_ticket"

    # 合同
    if "合同" in q or "HT-" in q:
        if "状态" in q or "审批" in q or "HT-" in q or "负责" in q or "签" in q:
            return "query_contract"

    # 知识库优先于预算：涉及制度/细则文档
    if "研发预算细则" in q or "能查" in q:
        return "search_knowledge_base"

    # 客户信息
    if ("客户" in q or "华宇" in q or "天穹" in q or "蓝海" in q or "恒信" in q or "云帆" in q) and (
        "行业" in q or "等级" in q or "信用" in q or "客户信息" in q or "是什么客户" in q
    ):
        return "get_customer_info"

    # 预算
    if "预算" in q or ("花了" in q and "部门" in q) or ("剩余" in q and "预算" in q):
        return "query_budget"

    # 年假余额（明确 年假/假期/剩余几天的查询意图；含"请一个月假期"这类超时判断）
    if ("年假" in q or "假期" in q or "请假" in q or "余额" in q) and any(
        w in q for w in ["还剩", "剩", "多少", "几天", "余额", "不够", "怎么办", "一个月"]
    ):
        return "lookup_employee"

    # 部门列表/负责人
    if ("哪些部门" in q or "部门有" in q or "经理是谁" in q or "负责人" in q or "谁负责" in q) and (
        "客户" not in q and "合同" not in q
    ):
        return "list_departments"

    # 员工信息
    if ("部门" in q and "哪个部" in q) or ("职位" in q) or ("职级" in q) or ("入职" in q) or (
        "谁" in q and "部门" in q
    ):
        return "lookup_employee"

    # 规章制度
    if any(w in q for w in ["制度", "流程", "标准", "规定", "报销", "请假流程", "加班", "调休", "保密", "网盘", "外发"]):
        return "search_knowledge_base"

    # 脱敏 + 再检查 -> 存储清洗（需在 redact 之前判断）
    if "脱敏" in q and "再检查" in q:
        return "sanitize_for_storage"

    # 脱敏
    if "脱敏" in q or ("隐藏" in q and ("手机号" in q or "身份证" in q)) or "打码" in q:
        return "redact_pii"

    # 风险审查
    if "风险" in q or "敏感词" in q or "检查这句话" in q:
        return "risk_review_text"

    # 存储清洗
    if "再检查" in q and "脱敏" in q:
        return "sanitize_for_storage"

    return "unknown"


if __name__ == "__main__":
    for q in ["技术部预算多少?", "我年假还剩几天?", "HT-2024-002审批到哪了?"]:
        print(f"{q} -> {route(q)}")