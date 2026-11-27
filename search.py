# -*- coding: utf-8 -*-
"""
routes/search.py  —— 单篇文献内部模糊搜索 + 拼写纠错
================================================================================
说明：本模块在分工表里属于 B 同学。这里提供一份「可直接运行的完整实现」，
      目的是保证 D 的上传链路（/paper/<论文>）点进来就能搜，不会被卡住。
      若 B 已完成 services/db_ops.search_terms_in_paper，本文件会自动优先调用
      B 的实现，不会重复造轮子。

【核心逻辑（与《模块接口定义》7.1 / 7.2 完全一致）】
  1) q 为空        -> 展示该文献全部术语（按词频降序）
  2) q 有值        -> Term.word LIKE '%q%' 模糊匹配
  3) 匹配不到      -> difflib.get_close_matches 做拼写纠错，返回"您是不是要找"
  4) ?format=json  -> 返回 JSON，供前端做 AJAX 实时过滤

【对接说明】
  蓝图名 search_bp，A 同学在 app.py 里：
      from routes.search import search_bp
      app.register_blueprint(search_bp)
================================================================================
"""

import difflib

from flask import Blueprint, jsonify, render_template, request

search_bp = Blueprint('search', __name__)

SUGGEST_N = 5
SUGGEST_CUTOFF = 0.6


# ============================================================ 数据访问（优先用 B 的实现）
def _get_terms_by_paper(paper_name):
    """取某篇文献的全部术语，按词频降序。"""
    try:
        from services.db_ops import get_terms_by_paper
        return list(get_terms_by_paper(paper_name))
    except Exception:
        pass
    # 降级：直接用 ORM 模型（B 的 models/database.py）
    from models.database import Term
    return (Term.query
            .filter(Term.source_paper == paper_name)
            .order_by(Term.frequency.desc())
            .all())


def _search_terms_in_paper(paper_name, keyword):
    """
    返回 (results, suggestions)。
    优先调用 B 的 services.db_ops.search_terms_in_paper；
    否则用本地实现（LIKE 模糊匹配 + difflib 纠错）。
    """
    try:
        from services.db_ops import search_terms_in_paper as _b_impl
        results, suggestions = _b_impl(paper_name, keyword)
        return list(results), list(suggestions)
    except Exception:
        pass

    from models.database import Term
    kw = (keyword or '').strip()
    results = (Term.query
               .filter(Term.source_paper == paper_name)
               .filter(Term.word.like(f'%{kw}%'))
               .order_by(Term.frequency.desc())
               .all())

    if results:
        return results, []

    # 无匹配 -> 拼写纠错
    all_words = [t.word for t in
                 Term.query.filter(Term.source_paper == paper_name).all()]
    suggestions = difflib.get_close_matches(kw.lower(), all_words,
                                            n=SUGGEST_N, cutoff=SUGGEST_CUTOFF)
    if not suggestions:
        suggestions = difflib.get_close_matches(kw.lower(), all_words,
                                                n=SUGGEST_N, cutoff=0.5)
    return [], suggestions


def _term_to_dict(t):
    return {
        'word': getattr(t, 'word', ''),
        'definition': getattr(t, 'definition', '') or '暂无释义',
        'frequency': getattr(t, 'frequency', 0) or 0,
        'category': getattr(t, 'category', ''),
    }


# ============================================================ 路由
@search_bp.route('/paper/<paper_name>/search', methods=['GET'])
def search_in_paper(paper_name):
    """
    GET /paper/<paper_name>/search?q=xxx[&format=json]
    """
    keyword = (request.args.get('q') or '').strip()
    want_json = (request.args.get('format') or '').lower() == 'json'

    if not keyword:
        terms = _get_terms_by_paper(paper_name)
        if want_json:
            return jsonify({'paper': paper_name, 'keyword': '',
                            'results': [_term_to_dict(t) for t in terms],
                            'suggestions': []})
        return render_template('paper_terms.html',
                               paper_name=paper_name, terms=terms,
                               keyword='', suggestions=[], searched=False)

    results, suggestions = _search_terms_in_paper(paper_name, keyword)

    if want_json:
        return jsonify({
            'paper': paper_name,
            'keyword': keyword,
            'results': [_term_to_dict(t) for t in results],
            'suggestions': suggestions,
        })

    return render_template('paper_terms.html',
                           paper_name=paper_name, terms=results,
                           keyword=keyword, suggestions=suggestions,
                           searched=True)


@search_bp.route('/paper/<paper_name>', methods=['GET'])
def paper_terms(paper_name):
    """不带搜索词时直接看术语列表（供上传完成页跳转过来）。"""
    terms = _get_terms_by_paper(paper_name)
    return render_template('paper_terms.html',
                           paper_name=paper_name, terms=terms,
                           keyword='', suggestions=[], searched=False)
