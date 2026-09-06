"""业务数据层：模拟企业真实数据。

设计目标：
- 贴近真实企业：多部门、员工含职位/入职日期/年假余额/职级
- RBAC：文档按密级(public/internal/confidential)分类，confidential 需部门精确匹配
- 后续可无缝替换为真实数据库导出（保持字段名即可）
"""

DEPARTMENTS = [
    {"name": "技术部", "code": "TECH", "manager": "陈志强", "headcount": 8},
    {"name": "运维部", "code": "OPS", "manager": "刘建国", "headcount": 4},
    {"name": "财务部", "code": "FIN", "manager": "赵秀英", "headcount": 4},
    {"name": "人事部", "code": "HR", "manager": "孙丽", "headcount": 3},
    {"name": "销售部", "code": "SALES", "manager": "周宏伟", "headcount": 6},
    {"name": "市场部", "code": "MKT", "manager": "吴倩", "headcount": 3},
    {"name": "产品部", "code": "PROD", "manager": "郑晓东", "headcount": 3},
    {"name": "法务部", "code": "LEGAL", "manager": "王爱华", "headcount": 2},
]

# 30 名员工：姓名 -> 部门/职位/职级/年假余额/入职日期
EMPLOYEES = {
    # 技术部 8
    "陈志强": {"department": "技术部", "position": "技术总监", "level": "D2", "leave_balance": 15, "hire_date": "2016-03-12"},
    "黄国栋": {"department": "技术部", "position": "后端工程师", "level": "P6", "leave_balance": 8, "hire_date": "2021-07-05"},
    "刘洋": {"department": "技术部", "position": "后端工程师", "level": "P5", "leave_balance": 12, "hire_date": "2019-11-18"},
    "马晓峰": {"department": "技术部", "position": "前端工程师", "level": "P5", "leave_balance": 10, "hire_date": "2020-02-27"},
    "徐磊": {"department": "技术部", "position": "算法工程师", "level": "P6", "leave_balance": 9, "hire_date": "2022-01-10"},
    "张伟": {"department": "技术部", "position": "测试工程师", "level": "P4", "leave_balance": 7, "hire_date": "2022-06-15"},
    "高翔": {"department": "技术部", "position": "架构师", "level": "P7", "leave_balance": 14, "hire_date": "2018-04-09"},
    "李思远": {"department": "技术部", "position": "实习生", "level": "P1", "leave_balance": 5, "hire_date": "2024-01-08"},
    # 运维部 4
    "刘建国": {"department": "运维部", "position": "运维经理", "level": "M2", "leave_balance": 13, "hire_date": "2017-09-20"},
    "王强": {"department": "运维部", "position": "运维工程师", "level": "P5", "leave_balance": 11, "hire_date": "2020-08-03"},
    "赵文斌": {"department": "运维部", "position": "SRE工程师", "level": "P6", "leave_balance": 6, "hire_date": "2019-05-21"},
    "郭涛": {"department": "运维部", "position": "运维工程师", "level": "P4", "leave_balance": 9, "hire_date": "2021-12-01"},
    # 财务部 4
    "赵秀英": {"department": "财务部", "position": "财务总监", "level": "D2", "leave_balance": 18, "hire_date": "2014-06-30"},
    "钱进": {"department": "财务部", "position": "会计", "level": "P5", "leave_balance": 10, "hire_date": "2019-03-14"},
    "孙敏": {"department": "财务部", "position": "出纳", "level": "P4", "leave_balance": 8, "hire_date": "2021-10-26"},
    "韩雪": {"department": "财务部", "position": "财务分析", "level": "P6", "leave_balance": 12, "hire_date": "2022-04-11"},
    # 人事部 3
    "孙丽": {"department": "人事部", "position": "HR经理", "level": "M2", "leave_balance": 16, "hire_date": "2015-12-08"},
    "冯媛": {"department": "人事部", "position": "HRBP", "level": "P6", "leave_balance": 13, "hire_date": "2020-05-18"},
    "沈洁": {"department": "人事部", "position": "招聘专员", "level": "P4", "leave_balance": 9, "hire_date": "2022-09-05"},
    # 销售部 6
    "周宏伟": {"department": "销售部", "position": "销售总监", "level": "D2", "leave_balance": 17, "hire_date": "2013-08-19"},
    "吴海峰": {"department": "销售部", "position": "大客户经理", "level": "P6", "leave_balance": 11, "hire_date": "2018-01-22"},
    "郑强": {"department": "销售部", "position": "销售经理", "level": "P5", "leave_balance": 8, "hire_date": "2021-02-14"},
    "林小芳": {"department": "销售部", "position": "销售代表", "level": "P4", "leave_balance": 10, "hire_date": "2022-07-19"},
    "何平": {"department": "销售部", "position": "销售代表", "level": "P4", "leave_balance": 6, "hire_date": "2023-04-03"},
    "罗玉梅": {"department": "销售部", "position": "客户成功", "level": "P5", "leave_balance": 12, "hire_date": "2019-09-09"},
    # 市场部 3
    "吴倩": {"department": "市场部", "position": "市场经理", "level": "M2", "leave_balance": 14, "hire_date": "2016-11-02"},
    "蒋丽": {"department": "市场部", "position": "品牌策划", "level": "P5", "leave_balance": 7, "hire_date": "2020-10-12"},
    "金鑫": {"department": "市场部", "position": "数字营销", "level": "P5", "leave_balance": 9, "hire_date": "2021-06-28"},
    # 产品部 3
    "郑晓东": {"department": "产品部", "position": "产品总监", "level": "D1", "leave_balance": 15, "hire_date": "2017-02-14"},
    "曹颖": {"department": "产品部", "position": "产品经理", "level": "P6", "leave_balance": 11, "hire_date": "2019-08-30"},
    "彭蕾": {"department": "产品部", "position": "产品经理", "level": "P5", "leave_balance": 9, "hire_date": "2021-03-22"},
    # 法务部 2
    "王爱华": {"department": "法务部", "position": "法务主管", "level": "M1", "leave_balance": 13, "hire_date": "2018-05-07"},
    "胡静": {"department": "法务部", "position": "法务专员", "level": "P5", "leave_balance": 8, "hire_date": "2022-11-14"},
}

# 年度预算（万元）
BUDGETS = {
    "技术部": {"annual": 500, "spent": 328.6, "remaining": 171.4, "items": ["人员成本60%", "设备采购25%", "外包15%"]},
    "运维部": {"annual": 180, "spent": 96.2, "remaining": 83.8, "items": ["云资源70%", "工具订阅20%", "其他10%"]},
    "财务部": {"annual": 120, "spent": 45.3, "remaining": 74.7, "items": ["审计费", "财税软件", "办公耗材"]},
    "人事部": {"annual": 150, "spent": 88.9, "remaining": 61.1, "items": ["招聘渠道", "培训费", "团建"]},
    "销售部": {"annual": 300, "spent": 210.5, "remaining": 89.5, "items": ["差旅费", "客户招待", "提成"]},
    "市场部": {"annual": 250, "spent": 178.3, "remaining": 71.7, "items": ["广告投放", "活动物料", "展会"]},
    "产品部": {"annual": 90, "spent": 28.4, "remaining": 61.6, "items": ["用户调研", "原型工具", "设计外包"]},
    "法务部": {"annual": 60, "spent": 12.8, "remaining": 47.2, "items": ["外部律所", "合同管理系统", "培训"]},
}

# 客户（需管理层权限查看）
CUSTOMERS = {
    "华宇科技": {"industry": "制造业", "contact": "王总", "level": "VIP", "contract_amount": 260, "credit": "良好"},
    "星辰网络": {"industry": "互联网", "contact": "李总", "level": "重点", "contract_amount": 150, "credit": "良好"},
    "天穹金融": {"industry": "金融", "contact": "陈总", "level": "VIP", "contract_amount": 320, "credit": "优秀"},
    "恒信地产": {"industry": "地产", "contact": "刘总", "level": "普通", "contract_amount": 45, "credit": "一般"},
    "云帆物流": {"industry": "物流", "contact": "张总", "level": "普通", "contract_amount": 30, "credit": "一般"},
    "蓝海能源": {"industry": "能源", "contact": "赵总", "level": "重点", "contract_amount": 180, "credit": "良好"},
}

# 合同
CONTRACTS = {
    "HT-2024-001": {"customer": "华宇科技", "status": "已签署", "amount": 120, "sign_date": "2024-01-15", "owner": "吴海峰"},
    "HT-2024-002": {"customer": "星辰网络", "status": "审批中", "amount": 80, "sign_date": "2024-02-01", "owner": "吴海峰"},
    "HT-2024-003": {"customer": "天穹金融", "status": "已签署", "amount": 320, "sign_date": "2024-03-10", "owner": "周宏伟"},
    "HT-2024-004": {"customer": "蓝海能源", "status": "待法务审核", "amount": 180, "sign_date": "2024-03-22", "owner": "郑强"},
    "HT-2023-009": {"customer": "恒信地产", "status": "已终止", "amount": 45, "sign_date": "2023-09-05", "owner": "郑强"},
    "HT-2024-005": {"customer": "云帆物流", "status": "草拟中", "amount": 30, "sign_date": "2024-04-01", "owner": "郑强"},
}

# 知识库文档（密级：public/internal/confidential）
DOCUMENTS = [
    {
        "id": "DOC-001",
        "title": "员工请假管理制度",
        "content": ("员工请假需提前在工作流系统提交申请。年假需提前3个工作日申请，事假建议至少提前1天。"
                     "请假流程：填写申请单 -> 直属上级审批 -> 人事部备案。3天以上需部门负责人审批。"),
        "keywords": ["请假", "年假", "事假", "申请", "审批"],
        "classification": "internal",
        "department": "全员",
        "version": "v3.2",
        "last_updated": "2024-01-20",
    },
    {
        "id": "DOC-002",
        "title": "公司差旅报销标准",
        "content": ("出差住宿标准：一线城市500元/晚，省会城市350元/晚，其他城市280元/晚。"
                     "餐补：一线城市150元/天，其他100元/天。交通：高铁一等座需副总审批。"
                     "报销需在回程后7个工作日内提交。"),
        "keywords": ["差旅", "报销", "住宿", "餐补", "出差", "高铁"],
        "classification": "internal",
        "department": "全员",
        "version": "v2.8",
        "last_updated": "2023-11-05",
    },
    {
        "id": "DOC-003",
        "title": "技术部年度研发预算细则",
        "content": ("技术部2024年度研发预算500万元。人员成本占60%（300万），设备采购25%（125万），"
                     "外包服务15%（75万）。服务器扩容项目占比30%，AI中台项目45%，安全建设25%。"),
        "keywords": ["预算", "研发", "技术部", "成本", "服务器", "AI"],
        "classification": "confidential",
        "department": "技术部",
        "version": "v1.4",
        "last_updated": "2024-01-08",
    },
    {
        "id": "DOC-004",
        "title": "新员工入职流程",
        "content": ("新员工入职当天流程：签劳动合同 -> 开通OA账号 -> 领取工牌 -> 参加下午的新人培训。"
                     "试用期3个月，首次劳动合同期限3年。必备材料：身份证、学历证明、离职证明。"),
        "keywords": ["入职", "新员工", "合同", "培训", "试用期"],
        "classification": "public",
        "department": "全员",
        "version": "v4.0",
        "last_updated": "2023-12-15",
    },
    {
        "id": "DOC-005",
        "title": "考勤管理制度",
        "content": ("弹性工作制：核心时间10:00-16:00，9:00-19:00任选8小时。"
                     "迟到超过30分钟记一次迟到，每月累计3次迟到影响当月绩效一档。"
                     "加班需提前申请，调休额度按月清零。"),
        "keywords": ["考勤", "迟到", "打卡", "加班", "调休", "弹性"],
        "classification": "internal",
        "department": "全员",
        "version": "v3.0",
        "last_updated": "2024-02-01",
    },
    {
        "id": "DOC-006",
        "title": "销售部客户招待费用标准",
        "content": ("客户招待标准：VIP客户人均500元/餐，重点客户300元/餐，普通客户200元/餐。"
                     "商务礼品单价不超过500元，需销售总监审批。超标部分自理并需说明。"),
        "keywords": ["招待", "客户", "销售", "礼品", "费用"],
        "classification": "confidential",
        "department": "销售部",
        "version": "v2.1",
        "last_updated": "2023-10-20",
    },
    {
        "id": "DOC-007",
        "title": "信息安全和保密管理制度",
        "content": ("禁止将公司内部文件上传至外部网盘或聊天工具。核心代码库权限按需申请。"
                     "涉密文档（含预算、客户合同）严禁外发，离职员工账号即时回收。"
                     "违反保密制度视情节给予警告至辞退处分。"),
        "keywords": ["保密", "安全", "涉密", "权限", "外发"],
        "classification": "internal",
        "department": "全员",
        "version": "v5.0",
        "last_updated": "2024-03-01",
    },
    {
        "id": "DOC-008",
        "title": "人事部薪酬体系说明",
        "content": ("薪酬体系：P系列技术/专业岗，M系列管理岗，D系列高管。"
                     "年度调薪窗口为每年3月，调薪幅度与绩效强相关。"
                     "薪资倒挂处理由HRBP单独跟进，不参与公开讨论。"),
        "keywords": ["薪酬", "薪资", "调薪", "绩效", "人事"],
        "classification": "confidential",
        "department": "人事部",
        "version": "v2.5",
        "last_updated": "2024-01-30",
    },
    {
        "id": "DOC-009",
        "title": "工伤与意外保险理赔流程",
        "content": ("员工发生工伤后24小时内报告直属上级并联系人事部。"
                     "理赔所需材料：工伤认定书、医疗发票、就诊记录。"
                     "医保范围内的费用先行垫付，报销周期约2周。"),
        "keywords": ["工伤", "保险", "理赔", "医疗", "报销"],
        "classification": "public",
        "department": "全员",
        "version": "v1.2",
        "last_updated": "2023-06-18",
    },
]