'use strict';

const fs = require('fs');

const name = 'guard';

const FEEDBACK = process.env.GUARD_FEEDBACK || '/tmp/guard_feedback.jsonl';

function logFeedback(rec) {
  try {
    fs.appendFileSync(FEEDBACK, JSON.stringify({ ts: Date.now(), ...rec }) + '\n');
  } catch (_) {}
}

const BUSINESS = [
  '制度', '报销', '差旅', '住宿', '发票', '退票', '请假', '年假', '调休', '加班',
  '培训', '测评', '试用期', '入职', '报到', '转正', '离职', '离职证明',
  '工资', '社保', '考勤', '绩效', '申请', '流程', '预算', '合同', '客户',
  '员工', '部门', '经理', '负责人', 'IT账号', '权限', '资产', '文件', '文档',
  '安全', '脱敏', '保密', '密码', '几天', '多少', '多久', '费用', '标准',
  '通知', '材料', '证明', '备案', '审批', '截止', '天数', '流程', '延长', '终止',
];

const REFUSE = [
  /未明确规定|未找到相关|无法回答|不能回答|无法提供|请咨询|咨询人力资源|不掌握|没有该(信息|资料)|文档中未|没有明确提到|没有明确描述|未明确提到|无法从|需要登录|无权访问|推测|可能包含|没有涉及|未涉及/,
];

// ===== 工具白名单（与 react_agent READ_TOOLS 对齐：只读 10 个，写工具永不开放）=====
const READ_TOOLS = [
  'mcp__ops__lookup_employee',
  'mcp__ops__query_budget',
  'mcp__ops__list_departments',
  'mcp__ops__get_customer_info',
  'mcp__ops__list_customers',
  'mcp__ops__query_contract',
  'mcp__docs__search_knowledge_base',
  'mcp__security__redact_pii',
  'mcp__security__risk_review_text',
  'mcp__security__sanitize_for_storage',
];
const WRITE_PUBLIC = ['mcp__ops__create_leave_request', 'mcp__ops__create_ticket'];
const GUARD_REASON = '该写操作未在当前白名单开放，请引导员工直接通过 OA 系统提交。';

// ===== 输出回检（verifier 移植）：无源断言拦截 =====
const MEASURE_WORDS = [
  '补考', '额外培训', '延长期', '终止试用', '解除', '个人承担', '诊断证明',
  '保密承诺书', '保密协议', '员工手册', '入职登记表', '作废', '申诉', '退回',
  '补卡', '转正', '录用', '辞退', '赔偿', '补偿', '没收', '罚款', '书面同意',
  '副总', '分管领导', '总监', '总裁',
];
const UNIT = '(元|万|晚|天|个工作日|工作日|个月|年|月|日|小时|周|次|%|份|条|级|笔|折|倍)';
const NUMBER_CLAIM = new RegExp('(\\d+(?:\\.\\d+)?)\\s*' + UNIT, 'g');

const GUIDANCE_TOOL = [
  '系统检测到上一轮你没有调用任何工具就直接给出了回答。',
  '请严格执行"先查后答"流程：先分析这个问题需要哪一类工具，然后立即调用相应的 MCP 工具',
  '（人物/员工/部门/请假/工单/客户/合同/预算 → mcp__ops__*，制度文档/操作流程 → mcp__docs__search_knowledge_base，手机号/身份证/敏感词 → mcp__security__*）。',
  '调用 mcp__docs__search_knowledge_base 时必须显式传 is_authenticated: true；若问题涉及具体员工，先用 mcp__ops__lookup_employee 查部门后，把 user_department 传给 search_knowledge_base。',
  '若返回的 denied 提示"需登录/无权访问"，说明鉴权参数没传对，补齐鉴权参数重查后再作答。',
  '获取真实数据后，再基于工具返回的内容作答，并注明出处。不要凭印象作答。',
].join('\n');

const GUIDANCE_NL = [
  '请不要输出任何工具调用语法（:invoke、mcp__、</call>、JSON 结构等）。',
  '如果你还需要数据，先用系统的工具调用机制真实调用工具获取，然后用 1-2 句自然语言给出最终答复。',
  '不要向用户展示任何工具名、参数或调用过程。',
].join('\n');

function correction(issues) {
  return [
    '检查：回答存在无源断言：' + issues.slice(0, 5).join('；') + '。',
    '请严格依据本轮已返回的工具观测原文重答：删除所有无依据的数字/措施/责任单位/DOC 引用，',
    '只保留有工具返回支撑的内容，1-3 句；不确定就明确说"制度中未写明"。',
    '若某信息确实没查到，如实说明，禁止编造或引用不存在的文档编号。',
  ].join('\n');
}

function blocksText(m) {
  const c = m && m.content;
  if (typeof c === 'string') return c;
  if (Array.isArray(c)) {
    return c
      .filter((b) => b && (b.type === 'text' || typeof b.text === 'string'))
      .map((b) => b.text || b)
      .join('\n')
      .trim();
  }
  return '';
}

function isToolResultBlock(b) {
  return b && b.type === 'tool-result';
}

function lastProcessedUser(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (!m || m.role !== 'user') continue;
    if (m.source && m.source.kind === 'plugin') continue;
    if (Array.isArray(m.content) && m.content.some(isToolResultBlock)) continue;
    const text = blocksText(m);
    if (!text) continue;
    return { idx: i, text };
  }
  return { idx: -1, text: '' };
}

function isBusiness(q) {
  return BUSINESS.some((kw) => q.includes(kw));
}

// ---- 回检器：证据约束 + 输出校验（对应 agent/verifier.py）----
function toolBlob(tail) {
  const parts = [];
  for (const m of tail) {
    if (!m || !Array.isArray(m.content)) continue;
    for (const b of m.content) {
      if (!b) continue;
      if (b.type === 'tool-call') {
        if (typeof b.arguments === 'string') parts.push(b.arguments);
        else if (b.arguments) parts.push(JSON.stringify(b.arguments));
      } else if (b.type === 'tool-result') {
        parts.push(blocksText({ content: Array.isArray(b.content) ? b.content : [] }));
      }
    }
  }
  return parts.join('\n');
}

function numberSupported(num, unit, blob) {
  if (new RegExp(RegExpEscape(num) + '\\s*' + RegExpEscape(unit)).test(blob)) return true;
  if (['元', '万', '天', '晚', '次', '份', '条', '%'].includes(unit)) {
    const bare = new RegExp('(?<!\\d)' + RegExpEscape(num) + '(?!\\d)');
    if (!bare.test(blob)) return false;
    return blob.includes(unit);
  }
  return false;
}

function RegExpEscape(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function docClaims(text) {
  return [...new Set((text || '').match(/DOC-\d{3}/g) || [])];
}

function numberClaims(text) {
  const out = [];
  const re = new RegExp(NUMBER_CLAIM.source, 'g');
  let m;
  while ((m = re.exec(text || '')) !== null) {
    out.push([m[1], m[2]]);
  }
  return out;
}

function measureClaims(text) {
  return MEASURE_WORDS.filter((w) => (text || '').includes(w));
}

function verifyAnswer(tail, finalText) {
  const blob = toolBlob(tail);
  const issues = [];
  if (/:invoke|mcp__[a-z]+__|run_code|调用mcp__/.test(finalText)) {
    issues.push('回答正文夹带了工具调用语法，工具必须通过系统工具调用机制真实执行，禁止在正文冒充');
  }
  for (const d of docClaims(finalText)) {
    if (!blob.includes(d)) issues.push('引用文档 ' + d + '，但工具返回未命中该文档');
  }
  for (const [num, unit] of numberClaims(finalText)) {
    if (!numberSupported(num, unit, blob)) {
      issues.push('回答出现数字断言 ' + num + unit + '，工具返回中无此值');
    }
  }
  for (const w of measureClaims(finalText)) {
    if (!blob.includes(w)) issues.push('回答出现措施/判定词"' + w + '"，工具返回中无此内容');
  }
  if (REFUSE.some((re) => re.test(finalText))) {
    issues.push('回答仍是敷衍/模糊话术，请基于工具返回落实具体内容');
  }
  return issues;
}

const seen = new WeakSet();

function apply(ctx, config = {}) {
  const retryMax = config.retryMax ?? 3;
  const state = new WeakMap();

  if (ctx.tools && typeof ctx.tools.guard === 'function') {
    ctx.tools.guard((exec) =>
      WRITE_PUBLIC.includes(exec.name) ? GUARD_REASON : undefined
    );
  }

  const restrictAgent = (agent) => {
    if (!agent || seen.has(agent)) return;
    seen.add(agent);
    try {
      const t = agent && agent.ctx && agent.ctx.tools;
      if (!t || typeof t.restrict !== 'function') return;
      t.restrict({ allow: READ_TOOLS });
      process.stderr.write(`[guard] read-tools whitelist applied for ${agent.id} (${READ_TOOLS.length} tools)\n`);
    } catch (e) {
      process.stderr.write('[guard] restrict failed: ' + (e && e.message) + '\n');
    }
  };
  if (ctx.agents) {
    ctx.on('agent/created', ({ agent }) => restrictAgent(agent));
  }

  ctx.on('agent/turn-stopping', ({ agent }) => {
    const msgs = agent.session.deriveMessages();
    const last = lastProcessedUser(msgs);
    const q = last.text;
    if (!q || !isBusiness(q)) return;
    const tail = msgs.slice(last.idx + 1);
    const usedTool = tail.some((m) => m && Array.isArray(m.content) && m.content.some(isToolResultBlock));
    const finalText = blocksText(tail.filter((m) => m && m.role === 'assistant').slice(-1)[0]);
    if (!finalText) return;

    let s = state.get(agent) || { stamp: '', strikes: 0 };
    const stamp = String(last.idx) + '::' + q.slice(0, 80);
    if (s.stamp !== stamp) {
      s = { stamp, strikes: 0 };
      state.set(agent, s);
    }

    const issues = verifyAnswer(tail, finalText);
    if (issues.length === 0) {
      s.strikes = 0;
      state.set(agent, s);
      if (/未写明|未提供|未涉及|未找到|尚未明确|未查到|不明确/.test(finalText)) {
        logFeedback({ type: 'unanswered', question: q, finalText: finalText.slice(0, 400) });
      }
      return;
    }
    const isPseudo = /:invoke|mcp__[a-z]+__|run_code|<\/call>/.test(finalText);
    s.strikes += 1;
    state.set(agent, s);
    logFeedback({
      type: 'guarded',
      question: q.slice(0, 200),
      strike: s.strikes,
      issues: issues.slice(0, 5),
      finalText: finalText.slice(0, 400),
    });
    if (s.strikes > retryMax) {
      if (isPseudo && !s.nlForced) {
        s.nlForced = true;
        process.stderr.write(`[guard] pseudo-tool forced NL (final)\n`);
        agent.steer({
          content: [{ type: 'text', text: GUIDANCE_NL }],
          source: { kind: 'plugin', plugin: name },
        });
      }
      return;
    }

    const guidance = !usedTool || isPseudo ? GUIDANCE_TOOL : correction(issues);
    process.stderr.write(`[guard] blocked (strike ${s.strikes}): ${issues.slice(0, 3).join(' | ')}\n`);
    agent.steer({
      content: [{ type: 'text', text: guidance }],
      source: { kind: 'plugin', plugin: name },
    });
  });
}

module.exports = { name, apply, inject: ['tools', 'agents'], test: { verifyAnswer, toolBlob, docClaims, numberClaims, measureClaims } };