# routes/search.py
"""全站术语搜索。负责人：B。

路由：GET /search?q=关键词&page=1
无结果时用 difflib 给近似建议（规格 9.6）。
"""

from flask import Blueprint, render_template, request

from services import db_ops

search_bp = Blueprint("search", __name__)

PER_PAGE = 20


@search_bp.route("/search")
def search():
    q = request.args.get("q", "", type=str).strip()[:100]
    page = request.args.get("page", 1, type=int) or 1
    page = max(1, page)

    rows, total, suggestions = [], 0, []
    if q:
        rows, total = db_ops.search_terms_global(q, page=page, per_page=PER_PAGE)
        # 页码越界（比如刚搜完换了个关键词）时退回第一页
        if total and not rows and page > 1:
            page = 1
            rows, total = db_ops.search_terms_global(q, page=page, per_page=PER_PAGE)
        if total == 0:
            suggestions = db_ops.suggest_terms(q, limit=5)

    pages = (total + PER_PAGE - 1) // PER_PAGE
    return render_template(
        "search.html",
        q=q, rows=rows, total=total, page=page, pages=pages,
        suggestions=suggestions,
    )
