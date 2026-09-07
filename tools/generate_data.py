#!/usr/bin/env python3
"""数据生成器：确定性生成企业数据，输出 data_generated.py

设计：
- 固定 seed，可复现
- 4 大事业群 + 17 个一级子部门
- 保留原有 33 名员工（作为真实名单），其余程序生成到 ~340 人
- 客户 30+，合同 45+，预算含事业群级汇总
"""
import random
import re

SEED = 20240815
rng = random.Random(SEED)

# ======================= 事业群 & 部门定义 =======================
# (部门编码, 部门名, 事业群, 部门负责人职位模板)
DEPARTMENT_DEF = [
    # 技术研发群
    ("TECH", "技术部", "技术研发群", "研发中心"),
    ("PROD", "产品部", "技术研发群", "产品设计中心"),
    ("RD1", "研发一部", "技术研发群", "后端研发中心"),
    ("RD2", "研发二部", "技术研发群", "前端/移动端研发中心"),
    # 市场销售群
    ("SALES", "销售部", "市场销售群", "销售中心"),
    ("MKT", "市场部", "市场销售群", "品牌市场中心"),
    ("BD", "商务拓展部", "市场销售群", "商务合作中心"),
    ("CSM", "客户成功部", "市场销售群", "客户运营中心"),
    # 运营交付群
    ("OPS", "运维部", "运营交付群", "基础设施中心"),
    ("DELIV", "交付实施部", "运营交付群", "项目交付中心"),
    ("CS", "客服运营部", "运营交付群", "客户服务中心"),
    ("SUPPLY", "供应链管理部", "运营交付群", "供应链中心"),
    # 职能支持群
    ("FIN", "财务部", "职能支持群", "财务中心"),
    ("HR", "人事部", "职能支持群", "人力资源中心"),
    ("LEGAL", "法务部", "职能支持群", "法务合规中心"),
    ("ADMIN", "行政管理部", "职能支持群", "行政中心"),
    ("PURCH", "采购部", "职能支持群", "采购中心"),
]

POSITIONS = {
    "TECH": ["技术总监", "后端工程师", "前端工程师", "AI算法工程师", "测试工程师", "架构师", "安全工程师", "测试开发工程师", "DevOps工程师", "数据工程师"],
    "PROD": ["产品总监", "产品经理", "产品运营", "交互设计师", "视觉设计师", "用户研究员", "项目经理"],
    "RD1": ["研发总监", "JAVA工程师", "Go工程师", "分布式系统工程师", "中间件工程师", "大数据工程师", "测试开发工程师"],
    "RD2": ["研发总监", "Web前端工程师", "App开发工程师", "跨端工程师", "UI工程师", "小程序工程师"],
    "SALES": ["销售总监", "大客户经理", "销售经理", "销售代表", "渠道经理", "客户成功经理", "售前工程师", "商务顾问"],
    "MKT": ["市场总监", "品牌经理", "内容营销专员", "数字营销", "活动策划", "市场分析师", "公关经理"],
    "BD": ["商务总监", "商务经理", "商务拓展专员", "战略合作经理", "生态运营"],
    "CSM": ["客户成功总监", "客户成功经理", "客户运营", "续费顾问", "用户成长经理"],
    "OPS": ["运维总监", "SRE工程师", "运维工程师", "网络工程师", "DBA", "云平台工程师", "监控告警工程师"],
    "DELIV": ["交付总监", "项目经理", "实施顾问", "交付工程师", "驻场运维", "培训讲师"],
    "CS": ["客服总监", "客服主管", "客服专员", "投诉处理专员", "质检专员"],
    "SUPPLY": ["供应链总监", "供应链经理", "计划员", "物流专员", "库存管理员", "数据统计员"],
    "FIN": ["财务总监", "财务经理", "会计", "出纳", "财务分析", "税务专员", "审计专员", "预算管理"],
    "HR": ["HRD", "HRBP", "招聘经理", "招聘专员", "薪酬绩效专员", "培训专员", "员工关系", "组织发展"],
    "LEGAL": ["法务总监", "合同审查律师", "合规专员", "知识产权专员", "诉讼律师"],
    "ADMIN": ["行政经理", "前台", "行政专员", "文秘", "资产管理专员"],
    "PURCH": ["采购总监", "采购经理", "采购专员", "供应商管理", "招标专员"],
}

# 原 33 名真实员工（保留）
KEEP_EMPLOYEES = {
    "陈志强": {"department": "技术部", "position": "技术总监", "level": "D2", "leave_balance": 15, "hire_date": "2016-03-12"},
    "黄国栋": {"department": "技术部", "position": "后端工程师", "level": "P6", "leave_balance": 8, "hire_date": "2021-07-05"},
    "刘洋": {"department": "技术部", "position": "后端工程师", "level": "P5", "leave_balance": 12, "hire_date": "2019-11-18"},
    "马晓峰": {"department": "技术部", "position": "前端工程师", "level": "P5", "leave_balance": 10, "hire_date": "2020-02-27"},
    "徐磊": {"department": "技术部", "position": "AI算法工程师", "level": "P6", "leave_balance": 9, "hire_date": "2022-01-10"},
    "张伟": {"department": "技术部", "position": "测试工程师", "level": "P4", "leave_balance": 7, "hire_date": "2022-06-15"},
    "高翔": {"department": "技术部", "position": "架构师", "level": "P7", "leave_balance": 14, "hire_date": "2018-04-09"},
    "李思远": {"department": "技术部", "position": "实习生", "level": "P1", "leave_balance": 5, "hire_date": "2024-01-08"},
    "刘建国": {"department": "运维部", "position": "运维总监", "level": "M2", "leave_balance": 13, "hire_date": "2017-09-20"},
    "王强": {"department": "运维部", "position": "运维工程师", "level": "P5", "leave_balance": 11, "hire_date": "2020-08-03"},
    "赵文斌": {"department": "运维部", "position": "SRE工程师", "level": "P6", "leave_balance": 6, "hire_date": "2019-05-21"},
    "郭涛": {"department": "运维部", "position": "运维工程师", "level": "P4", "leave_balance": 9, "hire_date": "2021-12-01"},
    "赵秀英": {"department": "财务部", "position": "财务总监", "level": "D2", "leave_balance": 18, "hire_date": "2014-06-30"},
    "钱进": {"department": "财务部", "position": "会计", "level": "P5", "leave_balance": 10, "hire_date": "2019-03-14"},
    "孙敏": {"department": "财务部", "position": "出纳", "level": "P4", "leave_balance": 8, "hire_date": "2021-10-26"},
    "韩雪": {"department": "财务部", "position": "财务分析", "level": "P6", "leave_balance": 12, "hire_date": "2022-04-11"},
    "孙丽": {"department": "人事部", "position": "HRD", "level": "M2", "leave_balance": 16, "hire_date": "2015-12-08"},
    "冯媛": {"department": "人事部", "position": "HRBP", "level": "P6", "leave_balance": 13, "hire_date": "2020-05-18"},
    "沈洁": {"department": "人事部", "position": "招聘专员", "level": "P4", "leave_balance": 9, "hire_date": "2022-09-05"},
    "周宏伟": {"department": "销售部", "position": "销售总监", "level": "D2", "leave_balance": 17, "hire_date": "2013-08-19"},
    "吴海峰": {"department": "销售部", "position": "大客户经理", "level": "P6", "leave_balance": 11, "hire_date": "2018-01-22"},
    "郑强": {"department": "销售部", "position": "销售经理", "level": "P5", "leave_balance": 8, "hire_date": "2021-02-14"},
    "林小芳": {"department": "销售部", "position": "销售代表", "level": "P4", "leave_balance": 10, "hire_date": "2022-07-19"},
    "何平": {"department": "销售部", "position": "销售代表", "level": "P4", "leave_balance": 6, "hire_date": "2023-04-03"},
    "罗玉梅": {"department": "销售部", "position": "客户成功经理", "level": "P5", "leave_balance": 12, "hire_date": "2019-09-09"},
    "吴倩": {"department": "市场部", "position": "市场总监", "level": "M2", "leave_balance": 14, "hire_date": "2016-11-02"},
    "蒋丽": {"department": "市场部", "position": "品牌经理", "level": "P5", "leave_balance": 7, "hire_date": "2020-10-12"},
    "金鑫": {"department": "市场部", "position": "数字营销", "level": "P5", "leave_balance": 9, "hire_date": "2021-06-28"},
    "郑晓东": {"department": "产品部", "position": "产品总监", "level": "D1", "leave_balance": 15, "hire_date": "2017-02-14"},
    "曹颖": {"department": "产品部", "position": "产品经理", "level": "P6", "leave_balance": 11, "hire_date": "2019-08-30"},
    "彭蕾": {"department": "产品部", "position": "产品运营", "level": "P5", "leave_balance": 9, "hire_date": "2021-03-22"},
    "王爱华": {"department": "法务部", "position": "法务总监", "level": "M1", "leave_balance": 13, "hire_date": "2018-05-07"},
    "胡静": {"department": "法务部", "position": "合同审查律师", "level": "P5", "leave_balance": 8, "hire_date": "2022-11-14"},
}

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN_SINGLE = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英华玉婷"
GIVEN_DOUBLE = ["志强", "国栋", "晓峰", "文斌", "秀英", "建国", "宏伟", "海峰", "文杰", "淑华", "亚平", "永刚", "春梅", "建军", "凤兰", "国庆", "丽华", "玉明", "淑珍", "立新", "艳红", "明辉", "秀兰", "桂芳", "建华", "春生", "宏图", "晓梅", "志华", "思远", "浩然", "泽宇", "柏言", "致远", "梓豪", "雨桐"]

def gen_employee_name(used):
    while True:
        if rng.random() < 0.45:
            name = rng.choice(SURNAMES) + rng.choice(GIVEN_SINGLE)
        else:
            name = rng.choice(SURNAMES) + rng.choice(GIVEN_DOUBLE)
        if name not in used:
            return name

def gen_hire_date():
    y = rng.randint(2013, 2024)
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    return f"{y:04d}-{m:02d}-{d:02d}"

def level_for(pos):
    if "总监" in pos or "主管" in pos or "经理" in pos:
        return rng.choice(["D1", "D2", "M1", "M2"])
    if "实习生" in pos:
        return "P1"
    return rng.choice(["P3", "P4", "P5", "P6", "P7"])

# 目标规模：每个部门人数（原部门已有人，其余补齐）
DEPT_TARGET = {
    "TECH": 30, "PROD": 24, "RD1": 26, "RD2": 22,
    "SALES": 32, "MKT": 18, "BD": 14, "CSM": 18,
    "OPS": 18, "DELIV": 20, "CS": 22, "SUPPLY": 14,
    "FIN": 16, "HR": 16, "LEGAL": 12, "ADMIN": 14, "PURCH": 12,
}

DEPARTMENTS = []
EMPLOYEES = {}
used_names = set(KEEP_EMPLOYEES.keys())

# 先生成部门（含事业群），并确定负责人
for code, dep_name, bg, _ in DEPARTMENT_DEF:
    DEPARTMENTS.append({"name": dep_name, "code": code, "bg": bg})

# 补齐各部门员工
def populate(dep_name, target):
    exists = sum(1 for e in EMPLOYEES.values() if e["department"] == dep_name)
    for _ in range(max(0, target - exists)):
        name = gen_employee_name(used_names)
        used_names.add(name)
        pos_pool = POSITIONS[CONTRA[dep_name]]
        pos = rng.choice([p for p in pos_pool if not (("总监" in p) and exists > 0)])
        if "总监" in pos or "主管" in pos or "经理" in pos:
            pos = rng.choice([p for p in pos_pool])
        hire = gen_hire_date()
        years = rng.randint(2, 12)
        balance = min(max(years + rng.randint(0, 4), 4), 20)
        EMPLOYEES[name] = {
            "department": dep_name, "position": pos,
            "level": level_for(pos), "leave_balance": balance,
            "hire_date": hire,
        }

CONTRA = {d[1]: d[0] for d in DEPARTMENT_DEF}
CONTRA.update({d[1]: d[0] for d in DEPARTMENT_DEF})
CONTRA = {d[1]: d[0] for d in DEPARTMENT_DEF}

# 先放保留员工
for n, info in KEEP_EMPLOYEES.items():
    EMPLOYEES[n] = dict(info)

# 补齐
for code in DEPT_TARGET:
    dep_name = next(d["name"] for d in DEPARTMENTS if d["code"] == code)
    populate(dep_name, DEPT_TARGET[code])

# 负责人：每个部门挑选级别最高的（总监/经理）作为 manager
for d in DEPARTMENTS:
    cands = [n for n, e in EMPLOYEES.items() if e["department"] == d["name"]]
    d["manager"] = ""
    for n in cands:
        if "总监" in EMPLOYEES[n]["position"] or "HRD" in EMPLOYEES[n]["position"]:
            d["manager"] = n
            break
    if not d["manager"]:
        for n in cands:
            if "经理" in EMPLOYEES[n]["position"]:
                d["manager"] = n
                break
    if not d["manager"]:
        d["manager"] = cands[0] if cands else ""
    d["headcount"] = len(cands)

# ======================= 预算 =======================
BGU = {"技术研发群": 1350, "市场销售群": 980, "运营交付群": 760, "职能支持群": 520}
# 部门预算按事业群拆分（比例），再算 spent/remaining
SPLIT = {
    "技术研发群": {"技术部": 0.30, "产品部": 0.22, "研发一部": 0.28, "研发二部": 0.20},
    "市场销售群": {"销售部": 0.45, "市场部": 0.25, "商务拓展部": 0.15, "客户成功部": 0.15},
    "运营交付群": {"运维部": 0.30, "交付实施部": 0.25, "客服运营部": 0.25, "供应链管理部": 0.20},
    "职能支持群": {"财务部": 0.25, "人事部": 0.25, "法务部": 0.20, "行政管理部": 0.15, "采购部": 0.15},
}
BUDGETS = {}
BG_BUDGETS = {}
for bg, total in BGU.items():
    spent_bg = 0
    for dep, ratio in SPLIT[bg].items():
        annual = int(total * ratio)
        spent = int(annual * rng.uniform(0.4, 0.7))
        BUDGETS[dep] = {
            "annual": annual, "spent": spent, "remaining": annual - spent,
            "items": [],
            "bg": bg,
        }
        spent_bg += spent
    BG_BUDGETS[bg] = {"annual": total, "spent": spent_bg, "remaining": total - spent_bg}

# ======================= 客户 30+ =======================
KEEP_CUSTOMERS = {
    "华宇科技": {"industry": "制造业", "contact": "王总", "level": "VIP", "contract_amount": 260, "credit": "良好"},
    "星辰网络": {"industry": "互联网", "contact": "李总", "level": "重点", "contract_amount": 150, "credit": "良好"},
    "天穹金融": {"industry": "金融", "contact": "陈总", "level": "VIP", "contract_amount": 320, "credit": "优秀"},
    "恒信地产": {"industry": "地产", "contact": "刘总", "level": "普通", "contract_amount": 45, "credit": "一般"},
    "云帆物流": {"industry": "物流", "contact": "张总", "level": "普通", "contract_amount": 30, "credit": "一般"},
    "蓝海能源": {"industry": "能源", "contact": "赵总", "level": "重点", "contract_amount": 180, "credit": "良好"},
}

INDUSTRIES = ["制造业", "互联网", "金融", "地产", "物流", "能源", "医疗健康", "教育", "零售", "汽车", "政务", "文旅", "农业", "通信", "生物科技", "跨境电商", "游戏", "网络安全", "新消费", "半导体", "智能硬件", "保险", "电商零售", "大数据服务"]
LEVELS = ["VIP", "重点", "普通", "潜力"]
CREDIT = ["优秀", "良好", "一般", "风险"]
COMP_NAMES = [
    "启明智能", "远景云创", "博远数据", "瀚海互联", "中科数联", "盛达实业", "天瑞集团", "宏图建筑",
    "飞腾网络", "恒润医疗", "智联教育", "云端零售", "路驰汽车", "金政数科", "山河文旅", "绿野农科",
    "迅捷通信", "康泰生物", "淘海跨境", "星动游戏", "安盾网安", "鲜生活", "芯纪元", "智禾科技",
    "优贝保险", "环球易购", "深蓝智造", "云谷数智", "华信集团", "盛合供应链",
]

def gen_customer(used_c):
    name = rng.choice([c for c in COMP_NAMES if c not in used_c])
    lv = rng.choice(LEVELS)
    amt = {"VIP": rng.randint(200, 500), "重点": rng.randint(100, 250), "普通": rng.randint(10, 80), "潜力": rng.randint(0, 30)}[lv]
    return {
        "industry": rng.choice(INDUSTRIES), "contact": rng.choice(["王总", "李总", "张总", "刘总", "陈总", "赵总", "孙总", "周总"]),
        "level": lv, "contract_amount": amt, "credit": rng.choice(CREDIT),
    }

CUSTOMERS = dict(KEEP_CUSTOMERS)
used_c = set(CUSTOMERS.keys())
for _ in range(24):
    name = rng.choice([c for c in COMP_NAMES if c not in used_c])
    used_c.add(name)
    CUSTOMERS[name] = gen_customer(used_c)

# ======================= 合同 45+ =======================
KEEP_CONTRACTS = {
    "HT-2024-001": {"customer": "华宇科技", "status": "已签署", "amount": 120, "sign_date": "2024-01-15", "owner": "吴海峰"},
    "HT-2024-002": {"customer": "星辰网络", "status": "审批中", "amount": 80, "sign_date": "2024-02-01", "owner": "吴海峰"},
    "HT-2024-003": {"customer": "天穹金融", "status": "已签署", "amount": 320, "sign_date": "2024-03-10", "owner": "周宏伟"},
    "HT-2024-004": {"customer": "蓝海能源", "status": "待法务审核", "amount": 180, "sign_date": "2024-03-22", "owner": "郑强"},
    "HT-2023-009": {"customer": "恒信地产", "status": "已终止", "amount": 45, "sign_date": "2023-09-05", "owner": "郑强"},
    "HT-2024-005": {"customer": "云帆物流", "status": "草拟中", "amount": 30, "sign_date": "2024-04-01", "owner": "郑强"},
}
STATUS = ["已签署", "审批中", "待法务审核", "草拟中", "履行中", "异常终止", "纠纷中"]
SALES_OWNERS = ["吴海峰", "郑强", "林小芳", "何平", "罗玉梅"]

CONTRACTS = dict(KEEP_CONTRACTS)
seq = 6
for cust, info in CUSTOMERS.items():
    n_contracts = 1 if rng.random() < 0.4 else 2
    for _ in range(n_contracts):
        y = rng.choice(["2023", "2024"])
        amount = int(info["contract_amount"] * rng.uniform(0.2, 1.0))
        mth = rng.randint(1, 12)
        day = rng.randint(1, 28)
        CONTRACTS[f"HT-{y}-{seq:03d}"] = {
            "customer": cust, "status": rng.choice(STATUS),
            "amount": amount, "sign_date": f"{y}-{mth:02d}-{day:02d}",
            "owner": rng.choice(SALES_OWNERS),
        }
        seq += 1

# ======================= 输出 =======================
def _dump():
    lines = []
    lines.append(f"# 自动生成（seed={SEED}），勿手改；改数据请编辑 tools/generate_data.py 后重新生成")
    lines.append(f"DEPARTMENTS = {DEPARTMENTS!r}")
    lines.append(f"EMPLOYEES = {EMPLOYEES!r}")
    lines.append(f"BUDGETS = {BUDGETS!r}")
    lines.append(f"BG_BUDGETS = {BG_BUDGETS!r}")
    lines.append(f"CUSTOMERS = {CUSTOMERS!r}")
    lines.append(f"CONTRACTS = {CONTRACTS!r}")
    return "\n".join(lines)

if __name__ == "__main__":
    with open("mcp_servers/data_generated.py", "w", encoding="utf-8") as f:
        f.write(_dump())
    print(f"生成完成：部门 {len(DEPARTMENTS)}，员工 {len(EMPLOYEES)}，客户 {len(CUSTOMERS)}，合同 {len(CONTRACTS)}")