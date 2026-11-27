# routes/paper.py
"""词典页、页内过滤接口、历史页。负责人：B。

路由（与规格第六章一致）：
    GET /paper/<int:paper_id>            术语词典页
    GET /api/paper/<int:paper_id>/terms  页内实时过滤（JSON）
    GET /history                         历史记录（可带 category、page）

模板由 C 同学负责。本文件只保证「传进去的变量名」与交接表一致，
不写页面内容、不改样式。
"""

from flask import Blueprint, abort, jsonify, render_template, request

from services import db_ops

paper_bp = Blueprint("paper", __name__)

PER_PAGE_TERMS = 50      # 词典页每页 50 条（规格 9.3）
PER_PAGE_HISTORY = 20    # 历史页每页 20 条（C 的设计稿有分页条）


def _int_arg(name, default):
    """读整数查询参数。传了非数字（?page=abc）时退回默认值，不返回 500。"""
    value = request.args.get(name, default, type=int)
    return default if value is None else value


def _clamp_page(pagination, page):
    """页码超出范围时钳到最后一页，避免用户看到空白页。"""
    if pagination.pages and page > pagination.pages:
        return pagination.pages
    return max(1, page)


@paper_bp.route("/paper/<int:paper_id>")
def detail(paper_id):
    paper = db_ops.get_paper(paper_id)
    if paper is None:
        abort(404)

    q = request.args.get("q", "", type=str).strip()[:100]
    sort = request.args.get("sort", "frequency", type=str)
    if sort not in ("frequency", "frequency_asc", "word"):
        sort = "frequency"
    page = max(1, _int_arg("page", 1))
    show_unlisted = request.args.get("unlisted") == "1"

    pagination = db_ops.get_terms_page(paper_id, page=page, per_page=PER_PAGE_TERMS,
                                       q=q or None, sort=sort)

    # 页码越界时按最后一页重查一次（模板里的页码也才对得上）
    fixed_page = _clamp_page(pagination, page)
    if fixed_page != page:
        page = fixed_page
        pagination = db_ops.get_terms_page(paper_id, page=page, per_page=PER_PAGE_TERMS,
                                           q=q or None, sort=sort)

    return render_template(
        "paper_terms.html",
        paper=paper,
        pagination=pagination,
        terms=pagination.items,
        q=q,
        sort=sort,
        page=page,
        unlisted_count=db_ops.count_unlisted(paper_id),
        unlisted=db_ops.get_unlisted_terms(paper_id) if show_unlisted else [],
        show_unlisted=show_unlisted,
    )


@paper_bp.route("/api/paper/<int:paper_id>/terms")
def api_terms(paper_id):
    """词典页顶部搜索框的实时过滤接口。

    C 的 JS 每停顿 300 毫秒调一次，返回结构：
        {"ok": true, "total": 命中总数, "items": [...最多 50 条...], "truncated": bool}
    """
    paper = db_ops.get_paper(paper_id)
    if paper is None:
        return jsonify({"ok": False, "error": "论文不存在"}), 404

    q = request.args.get("q", "", type=str).strip()[:100]
    total, items = db_ops.filter_terms(paper_id, q or None, limit=50)
    return jsonify({
        "ok": True,
        "total": total,
        "truncated": total > len(items),
        "items": [t.to_dict() for t in items],
    })


@paper_bp.route("/history")
def history():
    category = request.args.get("category", "", type=str).strip() or None
    page = max(1, _int_arg("page", 1))

    pagination = db_ops.list_papers_page(category, page=page, per_page=PER_PAGE_HISTORY)
    fixed_page = _clamp_page(pagination, page)
    if fixed_page != page:
        page = fixed_page
        pagination = db_ops.list_papers_page(category, page=page, per_page=PER_PAGE_HISTORY)

    return render_template(
        "history.html",
        papers=pagination.items,        # 当前页的论文列表，模板按 papers 循环即可
        pagination=pagination,
        total=pagination.total,
        categories=db_ops.list_categories(),
        current_category=category or "全部",
        category=category or "",
    )
