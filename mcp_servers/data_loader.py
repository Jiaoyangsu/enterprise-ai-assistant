"""数据统一加载器：数据与代码分离的核心。

规则：
- 优先从 data/documents.json 读取文档（换企业制度 = 改这个文件，不动代码）
- 文件缺失/损坏时 fallback 到 mcp_servers/documents.py 内置（迁移期兜底，零破坏）
- 敏感词表从 data/config.json 读取（可按企业自定义），fallback 内置默认
"""
import json
import os


def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


DATA_DIR = os.environ.get("APP_DATA_DIR", os.path.join(_root(), "data"))


def _load_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_documents() -> list[dict]:
    """加载知识库文档。优先 JSON（数据目录），fallback 内置 documents.py。"""
    docs = _load_json(os.path.join(DATA_DIR, "documents.json"))
    if docs and isinstance(docs.get("documents"), list) and docs["documents"]:
        return docs["documents"]
    try:
        from documents import DOCUMENTS  # 内置 fallback
        return DOCUMENTS
    except Exception:
        return []


def load_config() -> dict:
    """加载企业自定义配置（敏感词等）。"""
    cfg = _load_json(os.path.join(DATA_DIR, "config.json"))
    return cfg if isinstance(cfg, dict) else {}


def load_sensitive_words() -> list[str]:
    """企业敏感词表：data/config.json 的 sensitive_words 优先，缺省用内置默认。"""
    words = load_config().get("sensitive_words")
    if isinstance(words, list) and words:
        return [str(w) for w in words]
    return ["竞品", "泄密", "薪资倒挂", "跳槽", "机密", "内幕", "裁员", "股票期权"]


def load_policy() -> dict:
    """加载 RBAC 权限策略（唯一权限声明点）。

    客户可从 data/policy.json 定制：
    - rbac.customer_visible_roles: 可查看客户信息的角色列表
    - rbac.docs_visibility: 各密级文档的可见性（min_auth / 部门限制）
    - rbac.human_console_roles: 可访问人工坐席页的角色
    缺省提供保守默认（仅 admin/manager 可看客户），保证安全。
    """
    pol = _load_json(os.path.join(DATA_DIR, "policy.json"))
    if not isinstance(pol, dict):
        pol = {}
    rbac = pol.get("rbac") if isinstance(pol.get("rbac"), dict) else {}
    return {
        "customer_visible_roles": set(
            rbac.get("customer_visible_roles") or ["admin", "manager"]
        ),
        "human_console_roles": set(
            rbac.get("human_console_roles") or ["admin", "manager"]
        ),
        "docs_visibility": rbac.get("docs_visibility")
        if isinstance(rbac.get("docs_visibility"), dict)
        else {},
    }