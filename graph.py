# -*- coding: utf-8 -*-
"""
routes/graph.py  —— D同学新增模块
================================================================================
词汇关系图谱路由

功能：
  · /paper/<paper_name>/graph/<word>   查看某篇文献内、某个词的共现关系图
  · /graph/<word>                      跨所有文献聚合的全局关系图
  · /paper/<paper_name>/graph          列出该文献中适合做图谱的高频词（入口页）

原理：数据由上传流程（routes/upload.py -> services/cooccurrence.process_paper）
      预先统计并落盘到 data/cooccurrence/<paper>.json；
      本路由只负责「读盘 -> 渲染 pyvis HTML -> 嵌入页面」，因此访问很快。

【对接说明】
  蓝图名 graph_bp，A 同学在 app.py 里：
      from routes.graph import graph_bp
      app.register_blueprint(graph_bp)
  静态目录下需存在可写子目录 static/graphs/（代码会自动创建）。
================================================================================
"""

import os
import re

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, url_for)

from services import cooccurrence

graph_bp = Blueprint('graph', __name__)

DEFAULT_TOP_N = 25
DEFAULT_MIN_COUNT = 2


# ------------------------------------------------------------------ 路径工具
def _data_dir():
    """共现数据目录：<项目根>/data/cooccurrence"""
    root = getattr(current_app, 'root_path', os.getcwd())
    folder = os.path.join(root, 'data', 'cooccurrence')
    os.makedirs(folder, exist_ok=True)
    return folder


def _graph_dir():
    """生成的图谱 HTML 存放目录：<项目根>/static/graphs"""
    static_folder = current_app.static_folder or os.path.join(current_app.root_path, 'static')
    folder = os.path.join(static_folder, 'graphs')
    os.makedirs(folder, exist_ok=True)
    return folder


def _slug(name):
    return re.sub(r'[^\w\u4e00-\u9fff\-]', '_', str(name or '')) or 'x'


def _known_papers(data_dir):
    """列出已生成共现数据的文献名。"""
    if not os.path.isdir(data_dir):
        return []
    names = []
    for fn in os.listdir(data_dir):
        if fn.endswith('.json'):
            try:
                import json
                with open(os.path.join(data_dir, fn), 'r', encoding='utf-8') as f:
                    names.append(json.load(f).get('paper') or fn[:-5])
            except Exception:
                names.append(fn[:-5])
    return sorted(set(names))


# ------------------------------------------------------------------ 页面
@graph_bp.route('/paper/<paper_name>/graph', methods=['GET'])
def paper_graph_index(paper_name):
    """该文献的高频词入口页：点任意词进入它的关系图。"""
    data = cooccurrence.load_cooccurrence(paper_name, _data_dir())
    if not data:
        return render_template('term_graph.html',
                               paper_name=paper_name, word='',
                               graph_url='', related=[], words=[],
                               message='该文献还没有共现数据，请先重新上传一次文献以生成图谱。')
    words = [w for w, _ in sorted((data.get('word_freq') or {}).items(),
                                  key=lambda x: -x[1])][:60]
    return render_template('term_graph.html',
                           paper_name=paper_name, word='',
                           graph_url='', related=[], words=words, message='')


@graph_bp.route('/paper/<paper_name>/graph/<word>', methods=['GET'])
def paper_word_graph(paper_name, word):
    """查看某篇文献内某个词的关系图。"""
    data_dir = _data_dir()
    data = cooccurrence.load_cooccurrence(paper_name, data_dir)
    if not data:
        return render_template('term_graph.html',
                               paper_name=paper_name, word=word,
                               graph_url='', related=[], words=[],
                               message='该文献还没有共现数据，请先重新上传一次文献以生成图谱。')

    word_l = word.lower().strip()
    cooc = data.get('cooc', {})
    if word_l not in cooc:
        # 词不在图里：尝试拼写纠错，给出"您是不是要找"
        import difflib
        cand = difflib.get_close_matches(word_l, list(cooc.keys()), n=5, cutoff=0.6)
        return render_template('term_graph.html',
                               paper_name=paper_name, word=word,
                               graph_url='', related=[], words=[],
                               message=f'文献中没有找到「{word}」。',
                               suggestions=cand)

    top_n = int(request.args.get('top', DEFAULT_TOP_N))
    min_count = int(request.args.get('min', DEFAULT_MIN_COUNT))

    out_name = f'{_slug(paper_name)}__{_slug(word_l)}.html'
    out_path = os.path.join(_graph_dir(), out_name)
    cooccurrence.render_graph_html(paper_name, word_l, out_path,
                                   data_dir=data_dir, top_n=top_n,
                                   min_count=min_count, cooc=cooc)

    graph_url = url_for('static', filename=f'graphs/{out_name}')
    related = cooccurrence.related_words_from_cooc(cooc, word_l, top_n)
    words = [w for w, _ in sorted((data.get('word_freq') or {}).items(),
                                  key=lambda x: -x[1])][:60]

    return render_template('term_graph.html',
                           paper_name=paper_name, word=word_l,
                           graph_url=graph_url, related=related,
                           words=words, message='',
                           node_count=data.get('node_count'),
                           edge_count=data.get('edge_count'))


@graph_bp.route('/graph/<word>', methods=['GET'])
def global_word_graph(word):
    """跨所有文献聚合的全局关系图（答辩演示很好用）。"""
    data_dir = _data_dir()
    papers = _known_papers(data_dir)
    if not papers:
        return render_template('term_graph.html', paper_name='全部文献',
                               word=word, graph_url='', related=[], words=[],
                               message='还没有任何文献的共现数据。')

    merged = cooccurrence.aggregate_papers(papers, data_dir)
    word_l = word.lower().strip()
    if word_l not in merged:
        import difflib
        cand = difflib.get_close_matches(word_l, list(merged.keys()), n=5, cutoff=0.6)
        return render_template('term_graph.html', paper_name='全部文献',
                               word=word, graph_url='', related=[], words=[],
                               message=f'全部文献中都没有找到「{word}」。',
                               suggestions=cand)

    out_name = f'__global__{_slug(word_l)}.html'
    out_path = os.path.join(_graph_dir(), out_name)
    cooccurrence.render_graph_html('全部文献', word_l, out_path,
                                   data_dir=data_dir, top_n=DEFAULT_TOP_N,
                                   cooc=merged)
    graph_url = url_for('static', filename=f'graphs/{out_name}')
    related = cooccurrence.related_words_from_cooc(merged, word_l, DEFAULT_TOP_N)
    words = [w for w, _ in sorted(
        {w: sum(c.values()) for w, c in merged.items()}.items(), key=lambda x: -x[1])][:60]

    return render_template('term_graph.html', paper_name='全部文献',
                           word=word_l, graph_url=graph_url, related=related,
                           words=words, message='')


# ------------------------------------------------------------------ JSON API（给前端做异步交互）
@graph_bp.route('/api/graph/<paper_name>/<word>', methods=['GET'])
def api_graph(paper_name, word):
    """返回某词的相关词列表，供前端做"点击展开"或调试。"""
    n = int(request.args.get('n', 20))
    data = cooccurrence.load_cooccurrence(paper_name, _data_dir())
    if not data:
        return jsonify({'error': '该文献没有共现数据'}), 404
    related = cooccurrence.related_words_from_cooc(data.get('cooc', {}),
                                                   word, n)
    return jsonify({
        'paper': paper_name,
        'word': word.lower(),
        'related': [{'word': w, 'count': c} for w, c in related],
    })


@graph_bp.route('/graph', methods=['GET'])
def graph_home():
    """图谱总入口：列出所有已有共现数据的文献。"""
    data_dir = _data_dir()
    papers = _known_papers(data_dir)
    if papers:
        return redirect(url_for('graph.paper_graph_index', paper_name=papers[0]))
    return render_template('term_graph.html', paper_name='',
                           word='', graph_url='', related=[], words=[],
                           message='还没有任何文献的共现数据，请先上传一篇文献。')
