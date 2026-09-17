"""导出知识库文档为 JSON：数据与代码分离。

用法：.venv/bin/python tools/export_documents.py
产出：data/documents.json（27 篇制度文档，换企业制度 = 改这个文件）

documents.py 保留为内置 fallback（JSON 缺失时兜底），保证迁移期 45/45 不受影响。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp_servers"))

OUT = os.path.join(ROOT, "data", "documents.json")


def main():
    from documents import DOCUMENTS
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "documents": DOCUMENTS}, f, ensure_ascii=False, indent=2)
    print(f"已导出 {len(DOCUMENTS)} 篇文档 → {OUT}")
    print(f"覆盖密级: {sorted({d['classification'] for d in DOCUMENTS})}")


if __name__ == "__main__":
    main()