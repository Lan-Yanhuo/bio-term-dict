# -*- coding: utf-8 -*-
"""
services/cooccurrence.py  —— D同学新增模块
================================================================================
词汇关系图谱（Co-occurrence Graph）核心服务

功能：
  1) 滑动窗口统计「哪些词经常一起出现」（同一句/同一段内）
  2) networkx 构图，pyvis 生成可拖拽/缩放的交互式 HTML
  3) 数据落盘（JSON），避免每次访问都重算
  4) 依赖缺失时自动降级为「纯标准库 + vis-network CDN」的渲染方案

【为什么挂在上传流程里】
  共现统计必须拿到文献全文，而全文只有上传模块解析后才有。
  因此由 D 的 process_upload 在术语提取之后调用 process_paper()，
  把图谱数据落盘；之后 /paper/<论文>/graph/<词> 路由直接读盘渲染，秒开。

【对外接口（供 routes/graph.py 调用）】
    process_paper(text, paper_name, data_dir=None) -> str         # 统计+落盘，返回 json 路径
    load_cooccurrence(paper_name, data_dir=None) -> dict|None
    related_words(paper_name, word, data_dir=None, n=20) -> list[(word, count)]
    render_graph_html(paper_name, center_word, output_path,
                      data_dir=None, top_n=25, min_count=2) -> str  # 生成 HTML，返回路径
    aggregate_papers(paper_names, data_dir=None) -> dict          # 跨文献合并
================================================================================
"""

import json
import math
import os
import re
from collections import Counter, defaultdict

# ============================================================ 常量
WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")
SENT_SPLIT_RE = re.compile(r"[.!?;。！？；\n\r]+")
PARA_SPLIT_RE = re.compile(r"\n\s*\n+")

# 通用英文停用词（内置，避免依赖 NLTK；与 A 的 term_extractor 口径保持一致）
STOPWORDS = {
    'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'any', 'can', 'had',
    'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how',
    'its', 'new', 'now', 'old', 'see', 'two', 'way', 'who', 'boy', 'did', 'use',
    'that', 'with', 'have', 'this', 'will', 'your', 'from', 'they', 'know',
    'want', 'been', 'good', 'much', 'some', 'time', 'very', 'when', 'come',
    'here', 'just', 'like', 'long', 'make', 'many', 'more', 'only', 'over',
    'such', 'take', 'than', 'them', 'well', 'were', 'what', 'which', 'while',
    'would', 'there', 'their', 'about', 'after', 'also', 'been', 'both', 'each',
    'into', 'other', 'these', 'those', 'under', 'where', 'being', 'between',
    'during', 'through', 'within', 'without', 'because', 'however', 'therefore',
    'thus', 'then', 'than', 'may', 'can', 'could', 'should', 'must', 'might',
    'does', 'doing', 'done', 'such', 'same', 'different', 'used', 'using',
    'results', 'result', 'study', 'studies', 'shown', 'show', 'figure', 'table',
    'et', 'al', 'eg', 'ie', 'vs', 'fig', 'eq', 'ref', 'see', 'also',
}

DEFAULT_WINDOW = 5          # 滑动窗口大小（词数）
DEFAULT_MAX_TERMS = 60      # 图中最多保留的高频词节点数（防"毛球图"）
DEFAULT_MIN_COUNT = 2       # 边的最低共现次数


# ============================================================ 路径
def _default_data_dir():
    """默认数据目录：<项目根>/data/cooccurrence"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'data', 'cooccurrence')


def _safe_key(name):
    """把文件名/单词转成安全的文件系统键。"""
    key = re.sub(r'[^\w\u4e00-\u9fff\-.]', '_', str(name or ''))
    return key.strip('_') or 'unknown'


# ============================================================ 分词与切分
def split_sentences(text):
    """按句号/分号/换行切句，过滤过短的碎片。"""
    parts = [p.strip() for p in SENT_SPLIT_RE.split(text or '')]
    return [p for p in parts if len(p) >= 8]


def split_paragraphs(text):
    """按空行切段。"""
    parts = [p.strip() for p in PARA_SPLIT_RE.split(text or '')]
    return [p for p in parts if p]


def tokenize(text):
    """小写化 + 只保留长度>=3 的英文词 + 去停用词。"""
    words = [w.lower() for w in WORD_RE.findall(text or '')]
    return [w for w in words if w not in STOPWORDS]


# ============================================================ 共现统计
def build_cooccurrence(text, window=DEFAULT_WINDOW, level='sentence',
                       term_whitelist=None, max_terms=DEFAULT_MAX_TERMS):
    """
    滑动窗口统计词共现。

    参数：
        text           : str   文献全文
        window         : int   窗口大小（词数），默认 5
        level          : str   'sentence' 句内滑窗 / 'paragraph' 段内滑窗
        term_whitelist : set   若给定，只统计这些词（通常是提取出的专业术语）
        max_terms      : int   只保留词频最高的前 N 个词作为节点，防图爆炸

    返回：
        dict  {word: {co_word: count}}      （对称矩阵，无向）
    """
    units = split_sentences(text) if level == 'sentence' else split_paragraphs(text)

    # 第一遍：词频，用于挑出 top-N 节点
    freq = Counter()
    tokenized_units = []
    for unit in units:
        tokens = tokenize(unit)
        if term_whitelist:
            tokens = [t for t in tokens if t in term_whitelist]
        tokenized_units.append(tokens)
        freq.update(tokens)

    if not freq:
        return {}

    keep = {w for w, _ in freq.most_common(max_terms)}

    # 第二遍：滑窗统计
    cooc = defaultdict(Counter)
    for tokens in tokenized_units:
        tokens = [t for t in tokens if t in keep]
        n = len(tokens)
        for i in range(n):
            w = tokens[i]
            for j in range(i + 1, min(i + 1 + window, n)):
                v = tokens[j]
                if v == w:
                    continue
                cooc[w][v] += 1
                cooc[v][w] += 1

    return {w: dict(c) for w, c in cooc.items() if c}


def to_edges(cooc, min_count=DEFAULT_MIN_COUNT, top_n=None):
    """把共现字典转成无向边列表 [(a, b, weight)]，并按权重降序。"""
    seen = set()
    edges = []
    for a, nbrs in cooc.items():
        for b, c in nbrs.items():
            if c < min_count:
                continue
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            seen.add(key)
            edges.append((key[0], key[1], c))
    edges.sort(key=lambda x: -x[2])
    return edges[:top_n] if top_n else edges


def related_words_from_cooc(cooc, word, n=20):
    """从共现字典里取某个词的相关词，按共现次数降序。"""
    word = (word or '').lower().strip()
    nbrs = cooc.get(word) or {}
    return sorted(nbrs.items(), key=lambda x: -x[1])[:n]


# ============================================================ 落盘 / 读取
def process_paper(text, paper_name, data_dir=None, window=DEFAULT_WINDOW,
                  max_terms=DEFAULT_MAX_TERMS):
    """
    一体化入口：统计共现 + 落盘 JSON。上传流程直接调用这个。
    返回落盘后的 json 文件路径。
    """
    data_dir = data_dir or _default_data_dir()
    os.makedirs(data_dir, exist_ok=True)

    cooc = build_cooccurrence(text, window=window, max_terms=max_terms)
    word_freq = Counter()
    for a, nbrs in cooc.items():
        word_freq[a] += sum(nbrs.values())

    payload = {
        'paper': paper_name,
        'window': window,
        'max_terms': max_terms,
        'node_count': len(cooc),
        'edge_count': len(to_edges(cooc)),
        'word_freq': dict(word_freq.most_common(200)),
        'cooc': cooc,
    }
    path = os.path.join(data_dir, f'{_safe_key(paper_name)}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def load_cooccurrence(paper_name, data_dir=None):
    """读取某篇文献的共现数据；不存在返回 None。"""
    data_dir = data_dir or _default_data_dir()
    path = os.path.join(data_dir, f'{_safe_key(paper_name)}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def related_words(paper_name, word, data_dir=None, n=20):
    """读盘取相关词。"""
    data = load_cooccurrence(paper_name, data_dir)
    if not data:
        return []
    return related_words_from_cooc(data.get('cooc', {}), word, n)


def aggregate_papers(paper_names, data_dir=None, max_terms=DEFAULT_MAX_TERMS):
    """跨多篇文献合并共现数据（用于 /graph/<word> 全局视图）。"""
    merged = defaultdict(Counter)
    for name in paper_names:
        data = load_cooccurrence(name, data_dir)
        if not data:
            continue
        for a, nbrs in data.get('cooc', {}).items():
            for b, c in nbrs.items():
                merged[a][b] += c

    freq = Counter({w: sum(c.values()) for w, c in merged.items()})
    keep = {w for w, _ in freq.most_common(max_terms)}
    out = defaultdict(Counter)
    for a, nbrs in merged.items():
        if a not in keep:
            continue
        for b, c in nbrs.items():
            if b in keep:
                out[a][b] += c
    return {w: dict(c) for w, c in out.items()}


# ============================================================ 渲染
def _node_size(weight, min_w, max_w, lo=10, hi=38):
    """按权重线性映射节点大小。"""
    if max_w <= min_w:
        return (lo + hi) / 2
    return lo + (hi - lo) * (weight - min_w) / (max_w - min_w)


def render_graph_html(paper_name, center_word, output_path, data_dir=None,
                      top_n=25, min_count=DEFAULT_MIN_COUNT, cooc=None,
                      title=None):
    """
    生成交互式关系图谱 HTML。
    优先 networkx + pyvis；两者缺失时降级为 vis-network CDN 方案（纯标准库）。

    返回：output_path（生成成功）
    """
    if cooc is None:
        data = load_cooccurrence(paper_name, data_dir)
        cooc = (data or {}).get('cooc', {})

    center = (center_word or '').lower().strip()
    nbrs = related_words_from_cooc(cooc, center, top_n)

    # 组装节点 / 边：中心词 + 它的相关词 + 相关词之间的边
    nodes = {center: 0}
    edges = []
    for w, c in nbrs:
        nodes[w] = c
        edges.append((center, w, c))
        # 邻居之间的二级边（让图更像"关系网"而不是"星形"）
        for w2, c2 in cooc.get(w, {}).items():
            if w2 in dict(nbrs) and w2 != w and c2 >= min_count:
                key = tuple(sorted((w, w2)))
                if all(tuple(sorted((a, b))) != key for a, b, _ in edges):
                    edges.append((w, w2, c2))

    # 节点大小按「该词的共现总权重」来定，中心词自然最大
    nodes = {w: sum(cooc.get(w, {}).values()) for w in nodes}
    if len(nodes) <= 1:
        nodes[center] = 1

    title = title or f'{paper_name} · 词汇关系图谱 · {center}'

    try:
        return _render_with_pyvis(nodes, edges, center, output_path, title)
    except Exception as e:
        print(f'[cooccurrence] pyvis/networkx 不可用（{e}），降级为 vis-network 渲染')
        return _render_fallback(nodes, edges, center, output_path, title)


def _render_with_pyvis(nodes, edges, center, output_path, title):
    """networkx 构图 + pyvis 渲染。"""
    import networkx as nx
    from pyvis.network import Network

    G = nx.Graph()
    for w, weight in nodes.items():
        G.add_node(w, weight=weight)
    for a, b, w in edges:
        G.add_edge(a, b, weight=w)

    net = Network(height='640px', width='100%', bgcolor='#ffffff',
                  font_color='#1f2937', directed=False, notebook=False,
                  cdn_resources='remote')
    net.heading = title

    weights = [nodes[w] for w in nodes]
    lo, hi = (min(weights), max(weights)) if weights else (0, 1)

    for w in G.nodes:
        wgt = nodes.get(w, 0)
        is_center = (w == center)
        # 中心词强制用最大尺寸，保证视觉重心在中心
        size_w = hi if is_center else wgt
        net.add_node(
            w, label=w,
            size=_node_size(size_w, lo, hi, 14 if is_center else 10, 42 if is_center else 26),
            color='#dc2626' if is_center else '#2563eb',
            font={'size': 20 if is_center else 13, 'color': '#1f2937'},
            title=f'{w}｜共现总权重 {wgt}',
            borderWidth=3 if is_center else 1,
        )
    for a, b, w in G.edges(data='weight'):
        net.add_edge(a, b, value=w, title=f'共现 {w} 次',
                     color='#cbd5e1', width=min(1 + w * 0.3, 6))

    net.set_options("""
    var options = {
      "physics": {
        "forceAtlas2Based": {"gravitationalConstant": -60, "springLength": 130},
        "solver": "forceAtlas2Based", "stabilization": {"iterations": 200}
      },
      "interaction": {"hover": true, "navigationButtons": true, "tooltipDelay": 120},
      "edges": {"smooth": {"type": "continuous"}}
    }
    """)

    # 注意：不能直接用 net.save_graph()，它在 Windows 上默认编码写文件，
    # 遇到中文标题会抛 UnicodeEncodeError。这里手动以 utf-8 写出。
    html = net.generate_html()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


def _render_fallback(nodes, edges, center, output_path, title):
    """
    降级渲染：不依赖 networkx / pyvis，直接生成 vis-network 页面。
    只用标准库 + 一个 CDN 脚本，保证在任何环境下都能出图。
    """
    weights = [nodes[w] for w in nodes] or [0]
    lo, hi = min(weights), max(weights)
    vis_nodes = []
    for w, wgt in nodes.items():
        is_center = (w == center)
        vis_nodes.append({
            'id': w,
            'label': w,
            'value': hi if is_center else max(wgt, 1),   # 中心词最大
            'title': f'{w}｜共现总权重 {wgt}',
            'color': {'background': '#dc2626' if is_center else '#dbeafe',
                      'border': '#991b1b' if is_center else '#2563eb'},
            'font': {'size': 22 if is_center else 15, 'color': '#1f2937'},
            'shape': 'dot',
        })
    vis_edges = [{'from': a, 'to': b, 'value': w, 'title': f'共现 {w} 次',
                  'color': {'color': '#cbd5e1'}} for a, b, w in edges]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{_esc(title)}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html,body{{margin:0;padding:0;background:#ffffff;font-family:system-ui,"Microsoft YaHei",sans-serif;}}
  #graph{{width:100%;height:640px;}}
  .tip{{padding:8px 12px;color:#6b7280;font-size:13px;border-bottom:1px solid #eef2f7;}}
</style>
</head>
<body>
<div class="tip">中心词：<b>{_esc(center)}</b>　·　共 {len(vis_nodes)} 个节点 / {len(vis_edges)} 条关系　·　可拖拽、滚轮缩放</div>
<div id="graph"></div>
<script>
  var nodes = new vis.DataSet({json.dumps(vis_nodes, ensure_ascii=False)});
  var edges = new vis.DataSet({json.dumps(vis_edges, ensure_ascii=False)});
  var container = document.getElementById('graph');
  var options = {{
    nodes: {{scaling: {{min: 12, max: 40}}, borderWidth: 2}},
    edges: {{smooth: {{type: 'continuous'}}, scaling: {{min: 1, max: 6}}}},
    physics: {{forceAtlas2Based: {{gravitationalConstant: -60, springLength: 130}},
               solver: 'forceAtlas2Based', stabilization: {{iterations: 200}}}},
    interaction: {{hover: true, tooltipDelay: 120, navigationButtons: true, keyboard: true}}
  }};
  new vis.Network(container, {{nodes: nodes, edges: edges}}, options);
</script>
</body>
</html>"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


def _esc(s):
    return (str(s or '').replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


# ============================================================ 自测
if __name__ == '__main__':
    demo = """
    Apoptosis is a form of programmed cell death. The mitochondria play a central
    role in apoptosis. Cytochrome c is released from the mitochondria and activates
    caspase enzymes. Bcl-2 proteins regulate mitochondrial membrane permeabilization.
    ATP production in mitochondria is coupled to respiration. The mitochondrial
    membrane potential drives ATP synthesis. Apoptosis requires caspase activation.
    """
    cooc = build_cooccurrence(demo, window=5)
    print('节点数:', len(cooc))
    print('mitochondria 的相关词:', related_words_from_cooc(cooc, 'mitochondria', 8))
    print('边（前10）:', to_edges(cooc, min_count=1)[:10])
    out = os.path.join(_default_data_dir(), '..', '..', 'static', 'graphs', 'selftest.html')
    p = render_graph_html('selftest', 'mitochondria', os.path.abspath(out), cooc=cooc)
    print('已生成图谱:', p)
