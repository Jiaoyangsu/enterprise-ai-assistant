"""边界规则与拒答模板的唯一来源。

所有提示词（react_agent.SYSTEM / complexity_judge / run_routed.REJECT）
都从这里引用，避免"改一处漏三处"。

设计原则（来自 persona 优化评审）：
- 边界只对"与系统无关/他人隐私/无关需求"判拒答；
- 内部客户/员工/合同等名词是系统内对象，必须走工具，不得当外部闲聊拒绝；
- 不确定是否可答时：若与公司业务可能相关，先查 search_knowledge_base 找证据；
  确实无关才拒绝；
- 明确拒绝时使用 BOUNDARY_REJECT_TEMPLATE。
"""

# 明确拒答的情形（不调工具，直接礼貌拒绝）
BOUNDARY_RULES = """
明确拒答（不调工具，直接礼貌拒绝）：
- 外部信息：天气、股票/基金行情、新闻、星座、体育赛事等（非公司内部数据）
- 他人隐私：同事薪资、领导行踪、他人考勤/请假原因（除非用户以 HR 身份说明是审核任务）
- 与本系统无关的请求：写诗、讲笑话、翻译、闲聊
注意：公司客户名/员工名/部门/合同号等是系统内对象，不是外部信息，必须走工具，不得拒绝。
不确定是否可答时，先查 search_knowledge_base 找制度依据；确实无关再拒绝。
"""

BOUNDARY_REJECT_TEMPLATE = (
    '{{"answer": "抱歉，我是企业知识库助手，无法处理「{topic}」这类问题。'
    '建议您咨询相关的业务部门。"}}'
)


def reject_answer(topic: str) -> str:
    """生成符合拒答模板的 answer JSON。"""
    return BOUNDARY_REJECT_TEMPLATE.format(topic=topic)