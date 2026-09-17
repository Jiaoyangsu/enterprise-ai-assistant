'use strict';

// guard 插件回归测试（与 agent/verifier.py test_verifier.py 语义对齐）
const g = require('./index.js').test;

const mk = (content, args) => [{
  content: [
    { type: 'tool-call', arguments: args != null ? JSON.stringify(args) : '{}' },
    { type: 'tool-result', content: [{ type: 'text', text: content }] },
  ],
}];

const passes = (name, got, wantPass) => {
  const ok = got.length === 0;
  const mark = ok === wantPass ? 'PASS' : 'FAIL';
  console.log(`${mark}  ${name}${got.length ? '  -> ' + got[0] : ''}`);
  return ok === wantPass;
};

let pass = 0, total = 0;
const check = (name, got, wantPass) => { total++; pass += passes(name, got, wantPass) ? 1 : 0; };

// ===== 有源通过 =====
const docs = (content) => mk(JSON.stringify({ found: true, results: [{ id: 'DOC-101', title: '培训指南', content }] }));
check('good-3d', g.verifyAnswer(mk(JSON.stringify({ found: true, results: [{ id: 'DOC-101', title: '培训', content: '为期 3 天' }] })), '培训为期3天[DOC-101]'), true);

// ===== 语义单位推断（对齐 Python _SEMANTIC_UNITS）=====
const cust = (obj) => mk(JSON.stringify({ found: true, data: obj }));
check('semantic-cust-260', g.verifyAnswer(cust({ name: '华宇科技', contract_amount: 260 }), '华宇科技合同额260万元'), true);
check('semantic-cust-nosrc', g.verifyAnswer(cust({ name: '华宇科技', contract_amount: 100 }), '华宇科技合同额350万元'), false);
check('semantic-budget', g.verifyAnswer(cust({ department: '技术部', annual_budget: 405 }), '技术部年度预算405万元'), true);
check('semantic-balance', g.verifyAnswer(cust({ employee: '李雷', leave_balance: 12 }), '李雷年假余额12天'), true);
check('semantic-amount', g.verifyAnswer(cust({ expense: 350 }), '费用350元'), true);
check('semantic-nested-contract', g.verifyAnswer(mk(JSON.stringify({ found: true, contracts: [{ id: 'HT-2024-001', amount: 260 }] })), '合同HT-2024-001金额260万元'), true);

// ===== 漏答检测（对齐 Python _denial_but_evidence）=====
const t3db = mk(JSON.stringify({ found: true, results: [{ id: 'DOC-101', title: '培训', content: '为期 3 天' }] }));
check('denial-evidence', g.verifyAnswer(t3db, '制度中未写明新员工集中培训的具体天数。'), false);
check('denial-noinfo', g.verifyAnswer(mk(JSON.stringify({ found: false, message: '未找到相关文档' })), '制度中未写明食堂开放时间。'), true);
check('answer-positive', g.verifyAnswer(t3db, '新员工集中入职培训为期3天（DOC-101）。'), true);

// ===== 无源拦截 =====
check('nosrc-doc-102', g.verifyAnswer(mk(JSON.stringify({ found: true, results: [{ id: 'DOC-102', title: '报销' }] })), '据DOC-101培训为期3天'), false);
check('nosrc-measure', g.verifyAnswer(mk(JSON.stringify({ found: true, results: [{ id: 'DOC-101', title: '保密', content: '保密承诺书' }] })), '要求签署额外的员工手册'), false);
check('nosrc-cn-digit', g.verifyAnswer(mk(JSON.stringify({ count: 13, unit: '个' })), '公司共有三百二十八人'), false);
check('good-cn-digit', g.verifyAnswer(mk(JSON.stringify({ count: 328, unit: '人' })), '公司共有三百二十八人'), true);
check('pseudo-tool', g.verifyAnswer(mk(JSON.stringify({}), JSON.stringify({})), '请mcp__ops__lookup_employee查一下'), false);

// ===== 裸数字断言（对齐 Python extract_bare_numbers/_bare_supported）=====
const bare13 = mk(JSON.stringify({ found: true, count: 13, departments: ['技术部', '人事部'] }));
check('bare-block-328', g.verifyAnswer(bare13, '公司共有328名员工'), false);
check('bare-allow-328', g.verifyAnswer(mk(JSON.stringify({ found: true, count: 328 })), '共328个部门'), true);
check('bare-2digit-ok', g.verifyAnswer(bare13, '有13个部门'), true);
check('bare-year-2024-inobs', g.verifyAnswer(mk(JSON.stringify({ found: true, results: [{ content: '2024年起实行' }] })), '2024年生效'), true);
check('bare-year-nosrc', g.verifyAnswer(bare13, '2024年起实行'), false);

// ===== 模糊话术拦截 =====
const refuseTrace = mk(JSON.stringify({ found: true, results: [{ id: 'DOC-101', title: '报销', content: '住宿标准200元' }] }));
check('refuse-vague', g.verifyAnswer(refuseTrace, '建议咨询人力资源部了解详情'), false);

console.log(`\n结果：${pass}/${total} 通过，${total - pass} 失败`);
process.exit(pass === total ? 0 : 1);