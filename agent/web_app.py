"""零依赖本地 Web 前端：浏览器里直接和 agent（默认 14b）对话。

用法:  .venv/bin/python agent/web_app.py [port]
访问:  http://127.0.0.1:8787
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from react_agent import agent  # noqa: E402

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>企业知识库 AI 助手</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,"PingFang SC",sans-serif; background:#f5f6fa; }
  header { background:#2b3a55; color:#fff; padding:14px 20px; font-weight:600; }
  header span { font-size:13px; font-weight:400; opacity:.8; margin-left:10px; }
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
<header>企业知识库 AI 助手 <span>qwen2.5:14b · ReAct · 真实工具</span></header>
<div id="box">
  <div class="m a"><div class="b">你好，我是企业知识库助手。可以问我：员工信息、年假、部门预算、客户/合同、制度条文、脱敏/风险检查等。试试「技术部预算多少？」</div></div>
</div>
<div id="inp">
  <input id="q" placeholder="输入你的问题…" autofocus>
  <button id="go">发送</button>
</div>
<script>
const box=document.getElementById('box'), q=document.getElementById('q'), go=document.getElementById('go');
function add(role, text, tag){
  const d=document.createElement('div'); d.className='m '+role;
  const b=document.createElement('div'); b.className='b'; b.textContent=text;
  d.appendChild(b); box.appendChild(d); box.scrollTop=box.scrollHeight;
  if(tag){const t=document.createElement('div'); t.className='tag'; t.textContent=tag; d.appendChild(t);}
}
async function send(){
  const val=q.value.trim(); if(!val) return;
  add('u', val); q.value=''; go.disabled=true;
  add('a', '…思考中'); // 占位
  const t0=performance.now();
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:val})});
    const d=await r.json();
    box.lastElementChild.remove();
    const tools=Array.isArray(d.tools)&&d.tools.length? (' · 工具: '+d.tools.join(' → ')) : ' · 未调用工具';
    add('a', d.answer, '用时 '+(d.elapsed_s||0).toFixed(1)+'s · 模型 '+(d.model||'14b')+tools);
  }catch(e){ box.lastElementChild.remove(); add('a','请求失败: '+e); }
  go.disabled=false; q.focus();
}
go.onclick=send; q.onkeydown=e=>{ if(e.key==='Enter') send(); };
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path != "/":
            self.send_response(404); self.end_headers(); return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/api/chat":
            self.send_response(404); self.end_headers(); return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")
        question = (data.get("question") or "").strip()
        status, out = 200, err_to_dict("问题不能为空")
        if question:
            t0 = time.time()
            try:
                trace = []
                ans = agent(question, model="qwen2.5:14b", trace=trace)
                out = {"answer": ans, "model": "qwen2.5:14b",
                       "tools": [t["tool"] for t in trace],
                       "elapsed_s": round(time.time() - t0, 1)}
            except Exception as e:
                status, out = 500, err_to_dict(str(e))
        body = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def err_to_dict(msg: str) -> dict:
    return {"answer": "⚠ " + msg, "model": "", "elapsed_s": 0}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"前端已启动: http://127.0.0.1:{port}   (Ctrl+C 退出)")
    srv.serve_forever()


if __name__ == "__main__":
    main()