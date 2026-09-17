"""零依赖本地 Web 前端：浏览器里直接和 agent（默认 14b）对话。

用法:  .venv/bin/python agent/web_app.py [port]
访问:  http://127.0.0.1:8788（自研 MVP，仅等价对照；生产主界面用 dsh web 8787）
"""
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "mcp_servers"))

from react_agent import agent, classify_profile  # noqa: E402
from data import EMPLOYEES  # noqa: E402
import secure_auth  # noqa: E402
from workflow import detect_workflow, missing_fields, kind_of, build_draft, dangling_workflow  # noqa: E402

try:
    from data_loader import load_policy  # noqa: E402

    def _human_console_roles() -> set:
        """读 policy 的人工坐席可用角色（默认 admin/manager，可定制）。"""
        return set(load_policy().get("rbac", {}).get(
            "human_console_roles", ["admin", "manager"]))
except ImportError:  # 独立运行但缺 data_loader 时兜底
    def _human_console_roles() -> set:
        return {"admin", "manager"}

FEEDBACK = os.environ.get("WEB_FEEDBACK", "/tmp/web_feedback.jsonl")
HUMAN_QUEUE = os.environ.get("HUMAN_QUEUE", "/tmp/human_queue.jsonl")
HUMAN_ANSWERS = os.environ.get("HUMAN_ANSWERS", "/tmp/human_answers.jsonl")
GUARD_FEEDBACK = os.environ.get("GUARD_FEEDBACK", "/tmp/guard_feedback.jsonl")
SESSION_HISTORY_DIR = os.environ.get("SESSION_HISTORY_DIR", "/tmp/web_sessions")
AUTH_FILE = os.environ.get("AUTH_FILE", os.path.join(ROOT, "data", "auth_users.json"))
_human_lock = threading.Lock()

MANAGER_LEVELS = {"D1", "D2", "M1", "M2"}

# ===== 登录会话（内存 session；Auth 即企业员工目录 + 密码表） =====
_sessions: dict = {}
_session_lock = threading.Lock()
SESSION_HOURS = float(os.environ.get("SESSION_HOURS", "12"))


def authenticate(name: str, password: str) -> dict | None:
    """校验姓名+密码，返回该员工的登录上下文；失败返回 None（fail-closed）。

    账号来源：AUTH_USERS 环境变量(JSON) > AUTH_FILE；无 `*` 通配、无隐式默认口令。
    明文条目命中后自动升级为 PBKDF2 哈希并写回。
    """
    store = secure_auth.load_store(AUTH_FILE)
    emp = secure_auth.authenticate(name, password, EMPLOYEES, store)
    if emp is None:
        return None
    if secure_auth.needs_upgrade(store.get(name)):
        store[name] = secure_auth.make_entry(password)
        try:
            with open(AUTH_FILE, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
    level = emp.get("level", "")
    return {
        "name": name,
        "department": emp.get("department", ""),
        "position": emp.get("position", ""),
        "user_role": "manager" if (level in MANAGER_LEVELS or name == "刘洋") else "",
    }


def new_session(user: dict) -> str:
    tok = secrets.token_hex(16)
    with _session_lock:
        _sessions[tok] = {**user, "exp": time.time() + SESSION_HOURS * 3600}
    return tok


def session_user(tok: str | None) -> dict | None:
    if not tok:
        return None
    with _session_lock:
        s = _sessions.get(tok)
        if not s:
            return None
        if s["exp"] < time.time():
            _sessions.pop(tok, None)
            return None
        return {k: v for k, v in s.items() if k != "exp"}


def drop_session(tok: str | None):
    if tok:
        with _session_lock:
            _sessions.pop(tok, None)


def _get_token(cookie: str | None) -> str | None:
    if not cookie:
        return None
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "tok" and v:
            return v
    return None


def log_feedback(rec: dict):
    try:
        with open(FEEDBACK, "a") as f:
            f.write(json.dumps({**{"ts": time.time()}, **rec}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _feed_trace(trace: list | None) -> list:
    """把 ReAct 轨迹压缩成摘要落盘（供轨迹质量分析，控制体积）。"""
    out = []
    for t in trace or []:
        args = {k: (str(v)[:60]) for k, v in (t.get("args") or {}).items()}
        out.append({
            "tool": t.get("tool") or "",
            "args": args,
            "obs": (t.get("obs") or "")[:200],
        })
    return out


def _append_jsonl(path: str, rec: dict):
    with _human_lock:
        with open(path, "a") as f:
            f.write(json.dumps({**{"ts": time.time()}, **rec}, ensure_ascii=False) + "\n")


def _read_jsonl(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, json.JSONDecodeError):
        return []


# ===== 会话历史持久化（Fernet 加密，防明文落盘） =====
_SECRET_KEY_PATH = os.path.join(ROOT, "data", "secret.key")


def _fernet_key() -> bytes:
    """取加密密钥：优先 env，次选 key 文件，缺省生成并保存。"""
    import base64
    raw = os.environ.get("SESSION_SECRET")
    if raw:
        if len(raw) == 44:
            return raw.encode()
        from hashlib import sha256
        return base64.urlsafe_b64encode(sha256(raw.encode()).digest())
    try:
        with open(_SECRET_KEY_PATH, "rb") as f:
            return f.read().strip()
    except OSError:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(_SECRET_KEY_PATH), exist_ok=True)
        fd = os.open(_SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, key)
        os.close(fd)
        return key


from cryptography.fernet import Fernet as _FernetCls  # noqa: E402（确保本地可用）
_fernet_cache: _FernetCls | None = None


def _fernet() -> _FernetCls:
    global _fernet_cache
    if _fernet_cache is None:
        _fernet_cache = _FernetCls(_fernet_key())
    return _fernet_cache


def _session_file(tok: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", tok or "")
    return os.path.join(SESSION_HISTORY_DIR, f"{safe}.enc")  # .enc 后缀=加密文件


def _session_file_plain(tok: str) -> str:
    """旧明文文件路径（迁移期 fallback 读取用）。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", tok or "")
    return os.path.join(SESSION_HISTORY_DIR, f"{safe}.jsonl")


def session_history(tok: str | None) -> list[dict]:
    """返回该会话已持久化的问答轮 [{q, a}, ...]。自动兼容旧明文文件并升级。"""
    if not tok:
        return []
    enc_path = _session_file(tok)
    plain_path = _session_file_plain(tok)
    fer = _fernet()
    lines: list[str] = []
    upgraded = False
    # 优先读加密文件
    if os.path.exists(enc_path):
        try:
            with open(enc_path, "rb") as f:
                data = fer.decrypt(f.read()).decode("utf-8")
            lines = [l for l in data.splitlines() if l.strip()]
        except Exception:
            lines = []
    # 兼容旧明文文件（读完即加密升级）
    elif os.path.exists(plain_path):
        try:
            with open(plain_path) as f:
                lines = [l for l in f if l.strip()]
            upgraded = True
        except (OSError, json.JSONDecodeError):
            lines = []
    rec = []
    for l in lines:
        try:
            rec.append(json.loads(l))
        except json.JSONDecodeError:
            continue
    if upgraded and rec:
        try:
            encrypted = fer.encrypt(b"\n".join(line.encode() for line in lines))
            with open(enc_path, "wb") as f:
                f.write(encrypted)
            os.remove(plain_path)
        except OSError:
            pass
    return [{"q": r.get("q", ""), "a": r.get("a", "")} for r in rec
            if isinstance(r, dict) and r.get("q")]


def append_session_history(tok: str | None, q: str, a: str):
    """把本轮问答追加到会话历史文件（Fernet 加密）。"""
    if not tok or not q:
        return
    try:
        os.makedirs(SESSION_HISTORY_DIR, exist_ok=True)
        enc_path = _session_file(tok)
        fer = _fernet()
        new_line = (json.dumps({"q": q, "a": a}, ensure_ascii=False) + "\n").encode()
        # 增量追加：读旧 → 拼新 → 加密写回
        if os.path.exists(enc_path):
            try:
                with open(enc_path, "rb") as f:
                    old = fer.decrypt(f.read())
            except Exception:
                old = b""
        else:
            old = b""
        with open(enc_path, "wb") as f:
            f.write(fer.encrypt(old + new_line))
    except OSError:
        pass


def _answered_keys() -> set:
    keys = set()
    for r in _read_jsonl(HUMAN_ANSWERS):
        if r.get("qhash"):
            keys.add(r["qhash"])
    return keys


def _collect_human_queue() -> list:
    keys = _answered_keys()
    out = []
    for r in _read_jsonl(HUMAN_QUEUE):
        if r.get("type") != "needs_human":
            continue
        q = (r.get("question") or "").strip()
        if not q:
            continue
        r["qhash"] = r.get("qhash") or json.dumps({"q": q}, ensure_ascii=False)
        if r["qhash"] in keys:
            continue
        out.append(r)
    for r in _read_jsonl(GUARD_FEEDBACK):
        if r.get("type") != "needs_human":
            continue
        q = (r.get("question") or "").strip()
        if not q:
            continue
        r["qhash"] = json.dumps({"q": q, "g": 1}, ensure_ascii=False)
        if r["qhash"] in keys:
            continue
        out.append(r)
    return out


def queue_for_human(question: str, answer: str, reason: str):
    ans = answer or ""
    low = reason == "500" or not ans or any(
        w in ans for w in ("未写明", "未提供", "未涉及", "未找到", "尚未明确", "不明确",
                           "未公开", "请咨询", "咨询人力资源", "无法提供", "无法确认",
                           "无权访问", "未在公开文档")
    )
    if low:
        _append_jsonl(HUMAN_QUEUE, {
            "type": "needs_human", "question": question, "reason": reason,
            "system_answer": ans[:300],
            "qhash": json.dumps({"q": question}, ensure_ascii=False),
        })

FROM_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
FROM_HOST, FROM_PORT = FROM_BASE_URL.split("://")[1].split(":")[0], int(
    FROM_BASE_URL.split(":")[-1].split("/")[0] or 11434
)
MCP_PROBES = [("docs", 8001), ("ops", 8002), ("security", 8003), ("memory", 8004)]


def healthcheck(timeout: float = 2.5) -> tuple:
    issues = []
    try:
        with socket.create_connection((FROM_HOST, FROM_PORT), timeout=min(timeout, 2.0)):
            pass
    except OSError:
        issues.append("llm")
    try:
        with urllib.request.urlopen(
            f"http://{FROM_HOST}:{FROM_PORT}/api/tags", timeout=min(timeout, 2.0)
        ) as r:
            if not json.loads(r.read().decode("utf-8")).get("models"):
                issues.append("llm")
    except Exception:
        issues.append("llm")
    for name, port in MCP_PROBES:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=min(timeout, 1.0)):
                pass
        except OSError:
            issues.append(name)
    return (not issues, issues)

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>企业知识库 AI 助手</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,"PingFang SC",sans-serif; background:#f5f6fa; }
  header { background:#2b3a55; color:#fff; padding:14px 20px; font-weight:600;
           display:flex; justify-content:space-between; align-items:center; }
  header span { font-size:13px; font-weight:400; opacity:.8; margin-left:10px; }
  #who { font-size:13px; font-weight:400; opacity:.9; display:flex; gap:12px; align-items:center; }
  #who a { color:#fff; cursor:pointer; text-decoration:underline; }
  #box { max-width:760px; margin:18px auto; height:calc(100vh - 150px);
         overflow-y:auto; padding:12px; background:#fff; border-radius:10px;
         box-shadow:0 2px 8px rgba(0,0,0,.08); }
  .m { margin:10px 0; display:flex; }
  .u { justify-content:flex-end; } .a { justify-content:flex-start; }
  .b { max-width:70%; padding:9px 13px; border-radius:12px; white-space:pre-wrap; line-height:1.55; }
  .u .b { background:#2b3a55; color:#fff; }
  .a .b { background:#eef1f6; color:#222; }
  .tag { font-size:11px; color:#9aa3b2; margin:2px 4px; }
  #inp { max-width:760px; margin:10px auto; display:flex; gap:8px; }
  input { flex:1; padding:12px 14px; border:1px solid #d4d9e2; border-radius:8px; font-size:15px; }
  button { padding:12px 22px; border:0; border-radius:8px; background:#2b3a55; color:#fff; font-size:15px; cursor:pointer; }
  button:disabled { opacity:.5; }
</style>
</head>
<body>
<header>企业知识库 AI 助手 <span>qwen2.5:14b · ReAct · 真实工具</span>
  <div id="who"></div>
</header>
<div id="box">
  <div class="m a"><div class="b">你好，我是企业知识库助手。可以问我：员工信息、年假、部门预算、客户/合同、制度条文、脱敏/风险检查等。试试「技术部预算多少？」</div></div>
</div>
<div id="inp">
  <input id="q" placeholder="输入你的问题…" autofocus>
  <button id="go">发送</button>
</div>
<script>
const box=document.getElementById('box'), q=document.getElementById('q'), go=document.getElementById('go');
const who=document.getElementById('who');
(async function(){
  try{
    const r=await fetch('/api/me'); const d=await r.json();
    if(d.ok){
      who.textContent=(d.user.name||'')+' · '+(d.user.position||'')+(d.user.user_role==='manager'?' · 经理权限':'');
      const a=document.createElement('a'); a.textContent='退出';
      a.onclick=async()=>{ await fetch('/api/logout',{method:'POST'}); location.href='/login'; };
      who.appendChild(a);
      // 会话历史恢复：刷新/重开后不丢上下文
      try{
        const hr=await fetch('/api/session_history'); const hd=await hr.json();
        if(hd.ok&&Array.isArray(hd.history)&&hd.history.length){
          for(const it of hd.history){
            if(it.q) add('u', it.q);
            if(it.a) add('a', it.a, it.t?(' · '+it.t):'');
            turns.push({q:it.q||'',a:it.a||''});
          }
          if(turns.length>20) turns.splice(0, turns.length-20);
        }
      }catch(_){}
    }
  }catch(_){}
})();
function add(role, text, tag){
  const d=document.createElement('div'); d.className='m '+role;
  const b=document.createElement('div'); b.className='b'; b.textContent=text;
  d.appendChild(b); box.appendChild(d); box.scrollTop=box.scrollHeight;
  if(tag){const t=document.createElement('div'); t.className='tag'; t.textContent=tag; d.appendChild(t);}
}
let turns=[];
const CHAT_TIMEOUT_MS=120000;
async function postChat(payload){
  const ctl=new AbortController();
  const timer=setTimeout(()=>ctl.abort(), CHAT_TIMEOUT_MS);
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:ctl.signal});
    let d; try{ d=await r.json(); }
    catch(_){ throw new Error('服务端返回非 JSON（HTTP '+r.status+'）'); }
    if(!r.ok && !d.answer) throw new Error(d.message||('HTTP '+r.status));
    return d;
  } finally { clearTimeout(timer); }
}
async function send(){
  const val=q.value.trim(); if(!val) return;
  add('u', val); q.value=''; go.disabled=true;
  add('a', '…思考中'); // 占位
  const t0=performance.now();
  const payload={question:val,history:turns.map(t=>({q:t.q,a:t.a}))};
  let d=null, err=null;
  for(let attempt=0; attempt<2 && !d; attempt++){
    try{ d=await postChat(payload); }
    catch(e){
      err=e;
      if(attempt===0){ box.lastElementChild.textContent='网络中断，正在重试…'; await new Promise(r=>setTimeout(r,1200)); }
    }
  }
  box.lastElementChild.remove();
  if(d){
    const tools=Array.isArray(d.tools)&&d.tools.length? (' · 工具: '+d.tools.join(' → ')) : ' · 未调用工具';
    add('a', d.answer, '用时 '+(d.elapsed_s||0).toFixed(1)+'s · 模型 '+(d.model||'14b')+tools);
    turns.push({q:val,a:d.answer||''}); if(turns.length>20) turns.shift();
  } else {
    const name=(err&&err.name)||'';
    const tip=name==='AbortError'? '请求超时（后端仍在处理或服务未运行）': String(err);
    add('a','请求失败: '+tip+'\n请确认服务在运行（/api/health 返回 ok）后重试。');
  }
  go.disabled=false; q.focus();
}
go.onclick=send; q.onkeydown=e=>{ if(e.key==='Enter') send(); };
</script>
</body>
</html>"""


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>登录 · 企业知识库 AI 助手</title>
<style>
  body { margin:0; font-family:-apple-system,"PingFang SC",sans-serif; background:#f5f6fa;
         display:flex; align-items:center; justify-content:center; height:100vh; }
  .card { background:#fff; width:340px; border-radius:12px; box-shadow:0 4px 20px rgba(0,0,0,.08); padding:30px 28px; }
  h1 { font-size:18px; margin:0 0 4px; color:#2b3a55; }
  p { font-size:12px; color:#9aa3b2; margin:0 0 18px; }
  label { font-size:13px; color:#555; display:block; margin:12px 0 6px; }
  input { width:100%; padding:11px 12px; border:1px solid #d4d9e2; border-radius:8px; font-size:15px; box-sizing:border-box; }
  button { width:100%; margin-top:20px; padding:12px; border:0; border-radius:8px; background:#2b3a55;
           color:#fff; font-size:15px; cursor:pointer; }
  button:disabled { opacity:.5; }
  .err { color:#c0392b; font-size:13px; margin-top:10px; min-height:18px; }
  .hint { font-size:11px; color:#b0b7c4; margin-top:14px; line-height:1.6; }
</style>
</head>
<body>
<div class="card">
  <h1>企业知识库</h1>
  <p>请使用企业账号登录（员工姓名）</p>
  <form id="f">
    <label for="name">姓名</label>
    <input id="name" placeholder="例如：刘洋" autocomplete="username">
    <label for="pwd">密码</label>
    <input id="pwd" type="password" placeholder="企业账号密码" autocomplete="current-password">
    <button id="go">登 录</button>
  </form>
  <div class="err" id="err"></div>
  <div class="hint">仅已登记账号可登录（无默认口令）；经理职级（D1/D2/M1/M2）可见客户信息。<br>
  账号由管理员用 tools/set_password.py 管理。</div>
</div>
<script>
const f=document.getElementById('f'), err=document.getElementById('err');
f.onsubmit=async e=>{ e.preventDefault(); err.textContent=''; const go=document.getElementById('go');
  go.disabled=true;
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:document.getElementById('name').value.trim(),
                         password:document.getElementById('pwd').value})});
  const d=await r.json(); go.disabled=false;
  if(d.ok){ location.href='/'; } else { err.textContent=d.message||'登录失败'; }
};
</script>
</body>
</html>"""


HUMAN_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>人工兜底坐席</title>
<style>
  body { margin:0; font-family:-apple-system,"PingFang SC",sans-serif; background:#f5f6fa; padding:20px; }
  h1 { font-size:18px; color:#2b3a55; }
  .card { background:#fff; border-radius:10px; padding:14px 16px; margin:12px 0;
          box-shadow:0 2px 8px rgba(0,0,0,.06); }
  .q { font-weight:600; margin:0 0 6px; }
  .meta { font-size:12px; color:#9aa3b2; margin-bottom:8px; }
  .sys { font-size:13px; color:#666; background:#f2f4f8; border-radius:6px; padding:8px; margin:6px 0; }
  textarea { width:100%; height:64px; border:1px solid #d4d9e2; border-radius:6px; padding:8px; font-size:14px; }
  button { margin-top:8px; padding:8px 18px; border:0; border-radius:6px; background:#2b3a55; color:#fff; cursor:pointer; }
  .none { color:#9aa3b2; }
</style>
</head>
<body>
<h1>人工兜底坐席 <small>系统搞不定的问题会进到这里，作答后自动回填评测飞轮</small></h1>
<div id="list"><div class="card none">加载中…</div></div>
<script>
async function load(){
  const r=await fetch('/api/human/list');
  const d=await r.json();
  const list=document.getElementById('list');
  if(!d.items.length){ list.innerHTML='<div class="card none">暂无待处理问题</div>'; return; }
  list.innerHTML='';
  for(const it of d.items){
    const c=document.createElement('div'); c.className='card';
    c.innerHTML='<p class="q"></p><div class="meta"></div>'+
      '<div class="sys"></div>'+
      '<textarea placeholder="补充正确答案（留空=不处理即下线该问题）"></textarea>'+
      '<button>提交回填</button>';
    c.querySelector('.q').textContent=it.question;
    c.querySelector('.meta').textContent='来源: '+(it.source||'')+' · 原因: '+(it.reason||'guard');
    c.querySelector('.sys').textContent='系统回答: '+(it.system_answer||it.finalText||'') || '字段省略';
    if(!it.system_answer && it.finalText) c.querySelector('.sys').textContent='系统回答: '+it.finalText;
    c.querySelector('button').onclick=async ()=>{
      const ans=c.querySelector('textarea').value.trim();
      await fetch('/api/human/answer',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({qhash:it.qhash, answer:ans})});
      load();
    };
    list.appendChild(c);
  }
}
load();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:  # noqa: BLE001
            self._safe_error(e)

    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:  # noqa: BLE001
            self._safe_error(e)

    def _safe_error(self, e: Exception):
        """兜底：任何未捕获异常都必须回 JSON，绝不能让连接被直接断开。

        否则浏览器 fetch() 只会看到 `TypeError: Failed to fetch`，无法定位。
        """
        try:
            body = json.dumps(
                {"answer": "⚠ 服务端异常，请稍后再试。(%s: %s)" % (type(e).__name__, e),
                 "model": "", "elapsed_s": 0},
                ensure_ascii=False,
            ).encode("utf-8")
            self._send(500, body)
        except Exception:  # noqa: BLE001
            pass

    def _do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            ok, issues = healthcheck()
            body = json.dumps({"ok": ok, "issues": issues}, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/login":
            body = LOGIN_PAGE.encode("utf-8")
            self._send(200, body, ctype="text/html; charset=utf-8")
            return
        tok = _get_token(self.headers.get("Cookie"))
        if path == "/api/me":
            u = session_user(tok)
            if not u:
                self._send(401, json.dumps({"ok": False, "message": "未登录"}, ensure_ascii=False).encode("utf-8"))
            else:
                self._send(200, json.dumps({"ok": True, "user": u}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/human/list":
            u = session_user(tok)
            if not u or u.get("user_role", "") not in _human_console_roles():
                self._send(403, json.dumps({"ok": False, "message": "仅人工坐席角色可访问"}, ensure_ascii=False).encode("utf-8"))
                return
            body = json.dumps({"items": _collect_human_queue()}, ensure_ascii=False).encode("utf-8")
            self._send(200, body)
            return
        if path == "/api/session_history":
            u = session_user(tok)
            if not u:
                self._send(401, json.dumps({"ok": False, "message": "未登录"}, ensure_ascii=False).encode("utf-8"))
                return
            body = json.dumps({"ok": True, "history": session_history(tok)}, ensure_ascii=False).encode("utf-8")
            self._send(200, body)
            return
        if path in ("/", "/human"):
            if not session_user(tok):
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
        if path == "/human":
            u = session_user(tok)
            if not u:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            if u.get("user_role", "") not in _human_console_roles():
                self._send(403, b"forbidden", ctype="text/plain; charset=utf-8")
                return
            body = HUMAN_PAGE.encode("utf-8")
            self._send(200, body, ctype="text/html; charset=utf-8")
            return
        if path != "/":
            self.send_response(404); self.end_headers(); return
        body = PAGE.encode("utf-8")
        self._send(200, body, ctype="text/html; charset=utf-8")

    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n > 0 else b""
        try:
            data = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, json.dumps({"ok": False, "answer": "请求格式错误（非合法 JSON）。"},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if not isinstance(data, dict):
            self._send(400, json.dumps({"ok": False, "answer": "请求格式错误（应为 JSON 对象）。"},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/login":
            name = (data.get("name") or "").strip()
            pwd = data.get("password") or ""
            user = authenticate(name, pwd)
            if not user:
                self._send(401, json.dumps({"ok": False, "message": "姓名或密码错误"}, ensure_ascii=False).encode("utf-8"))
                return
            tok = new_session(user)
            body = json.dumps({"ok": True, "user": user}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Set-Cookie", f"tok={tok}; HttpOnly; Path=/; Max-Age={int(SESSION_HOURS * 3600)}")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        tok = _get_token(self.headers.get("Cookie"))
        if path == "/api/logout":
            drop_session(tok)
            body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Set-Cookie", "tok=; HttpOnly; Path=/; Max-Age=0")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not session_user(tok):
            self._send(401, json.dumps({"answer": "未登录或会话已过期，请先登录后再提问。", "model": "", "elapsed_s": 0}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/human/answer":
            u = session_user(tok)
            if not u or u.get("user_role", "") not in _human_console_roles():
                self._send(403, json.dumps({"ok": False, "message": "仅人工坐席角色可回填"}, ensure_ascii=False).encode("utf-8"))
                return
            qhash = (data.get("qhash") or "").strip()
            ans = (data.get("answer") or "").strip()
            if not ans:
                self._send(400, json.dumps({"ok": False, "message": "回填答案不能为空"}, ensure_ascii=False).encode("utf-8"))
                return
            refuse_hits = [w for w in ("未写明", "请咨询", "无法提供", "未找到", "不能回答") if w in ans]
            if refuse_hits:
                self._send(400, json.dumps({"ok": False, "message": "回填答案含敷衍话术：" + "、".join(refuse_hits)}, ensure_ascii=False).encode("utf-8"))
                return
            _append_jsonl(HUMAN_ANSWERS, {"qhash": qhash, "answer": ans[:2000]})
            if ans and qhash:
                try:
                    q = json.loads(qhash).get("q", "")
                    if q:
                        log_feedback({"source": "human", "question": q, "answer": ans})
                except (json.JSONDecodeError, AttributeError):
                    pass
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            return
        if path != "/api/chat":
            self.send_response(404); self.end_headers(); return
        question = (data.get("question") or "").strip()
        hist_raw = data.get("history") or []
        history = ([{"q": str(h.get("q", ""))[:600], "a": str(h.get("a", ""))[:1200]}
                    for h in hist_raw if isinstance(h, dict)][-16:]
                   if isinstance(hist_raw, list) else [])
        usr = session_user(tok)
        status, out = 200, err_to_dict("问题不能为空")
        if question:
            ok, issues = healthcheck()
            if not ok:
                status, out = 503, degraded_dict(issues)
                queue_for_human(question, "", "降级:" + ",".join(issues))
            else:
                t0 = time.time()
                try:
                    trace = []
                    allow = classify_profile(question)
                    wf_name = detect_workflow(question) or dangling_workflow(history, question)
                    guided = False
                    if wf_name:
                        guided = True
                        if kind_of(wf_name) == "inquiry":
                            if "合同" in question:
                                from react_agent import run_tool, resolve_context  # noqa: F401
                                r = run_tool("query_contract", {"status": "pending"},
                                             resolve_context("", usr or {}))
                                out = {"answer": "以下为 OA 可查的合同审批记录（以工具返回为准）：\n" + str(r),
                                       "model": "web/workflow-inquiry", "tools": ["query_contract"],
                                       "allow": allow, "elapsed_s": round(time.time() - t0, 1),
                                       "workflow": wf_name}
                            else:
                                out = {"answer": "「%s」：请假/报销/加班等自建流程的审批进度，OA 暂无在线数据，"
                                                 "请到 OA「我的申请」查看最新状态；系统不编造审批节点。" % wf_name,
                                       "model": "web/workflow-inquiry", "tools": [], "allow": allow,
                                       "elapsed_s": round(time.time() - t0, 1), "workflow": wf_name}
                        else:
                            missing = missing_fields(question, wf_name, history=history)
                            if missing:
                                out = {
                                    "answer": "收到，继续「%s」流程。请补齐以下信息后我再帮你生成草稿单：\n"
                                              "· %s\n（姓名/部门可用当前登录人，金额与日期请按实际填写）" % (wf_name, "\n· ".join(missing)),
                                    "model": "web/workflow-guide", "tools": [], "allow": allow,
                                    "elapsed_s": round(time.time() - t0, 1), "workflow": wf_name,
                                    "needs_fields": missing,
                                }
                            else:
                                out = {"answer": build_draft(question, wf_name, usr, history=history),
                                       "model": "web/workflow-draft", "tools": [], "allow": allow,
                                       "elapsed_s": round(time.time() - t0, 1), "workflow": wf_name}
                    if not guided:
                        ans = agent(question, model="qwen2.5:14b", trace=trace, allow=allow, user_ctx=usr, history=history)
                        out = {"answer": ans, "model": "qwen2.5:14b",
                               "tools": [t["tool"] for t in trace],
                               "allow": allow,
                               "elapsed_s": round(time.time() - t0, 1)}
                        log_feedback({"source": "web", "user": usr.get("name") if usr else None,
                                      "question": question,
                                      "answer": ans, "tools": [t["tool"] for t in trace],
                                      "trace": _feed_trace(trace),
                                      "elapsed_s": out["elapsed_s"], "status": 200})
                        queue_for_human(question, ans, "不确定性")
                    else:
                        log_feedback({"source": "web", "user": usr.get("name") if usr else None,
                                      "question": question,
                                      "answer": out["answer"], "tools": [],
                                      "trace": _feed_trace(trace),
                                      "elapsed_s": out["elapsed_s"], "status": 200, "workflow": wf_name})
                    append_session_history(tok, question, out.get("answer", ""))
                except Exception as e:
                    status, out = 500, err_to_dict(str(e))
                    log_feedback({"source": "web", "question": question, "status": 500,
                                  "trace": _feed_trace(trace),
                                  "err": str(e)[:200]})
                    queue_for_human(question, "", "500")
        body = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self._send(status, body)

    def log_message(self, *a):
        pass


def err_to_dict(msg: str) -> dict:
    return {"answer": "⚠ 请求处理失败，请稍后再试。若持续异常请联系技术支持。 (" + msg + ")",
            "model": "", "elapsed_s": 0}


def degraded_dict(issues: list) -> dict:
    if "llm" in issues:
        tip = "底层 AI 服务未连接（模型服务不可用）"
    else:
        tip = "知识/业务数据服务不可用（" + ", ".join(issues) + "）"
    return {"answer": "⚠ 知识助手暂时不可用：" + tip + "，当前无法安全返回内容，请稍后再试或联系技术支持。",
            "model": "", "elapsed_s": 0}


def _maybe_wrap_tls(srv):
    """若设置 TLS_CERTFILE/TLS_KEYFILE 则把 HTTP server 包成 HTTPS。

    生产环境建议用反向代理（nginx/网关）终止 TLS，此处提供直连选项。
    """
    cert = os.environ.get("TLS_CERTFILE")
    key = os.environ.get("TLS_KEYFILE")
    if not cert or not key:
        return srv
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    srv = _maybe_wrap_tls(ThreadingHTTPServer((host, port), Handler))
    scheme = "https" if os.environ.get("TLS_CERTFILE") else "http"
    print(f"前端已启动: {scheme}://{host}:{port}   (Ctrl+C 退出)")
    srv.serve_forever()


if __name__ == "__main__":
    main()