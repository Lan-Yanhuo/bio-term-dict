# services/db_ops.py
"""数据库读写。全组唯一允许碰 db.session 的地方。

负责人：B。A（抽取、共现、建图）、C（页面取数）、D（上传、进度）都通过这里读写，
不要自己写 `db.session` 或 `Term.query`，否则事务边界会乱。

接口清单见文件末尾的「交接速查」。
"""

import difflib
from datetime import datetime

from sqlalchemy import or_

from models.database import db, Paper, Term, Sentence, Cooccurrence


# ---------- 小工具 ----------

def _clip(value, length):
    """截断到数据库列宽，避免 'StringDataRightTruncation' 这类底层报错。"""
    return str(value or "")[:length]


def _like_pattern(q: str) -> str:
    """把用户输入变成 LIKE 模式，并转义 % 和 _。

    不转义的话，用户搜一个 "%" 会匹配全库，搜 "a_b" 会把 a?b 也算命中。
    """
    s = (q or "").strip()
    s = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{s}%"


# ---------- 论文 ----------

def create_paper(title, stored_filename, source_type, category="未分类"):
    """上传时调用，返回新建的 Paper 对象（含 id）。

    title 用用户在表单里填的值，不从文件名派生（规格 P13）。
    """
    source_type = (source_type or "txt").lower()
    if source_type not in ("txt", "pdf"):
        raise ValueError("source_type 只能是 txt 或 pdf")

    paper = Paper(
        title=_clip(title, 300).strip() or "未命名论文",
        stored_filename=_clip(stored_filename, 300),
        source_type=source_type,
        category=_clip(category, 50).strip() or "未分类",
        status="pending",
        progress=0,
        message="等待处理",
    )
    db.session.add(paper)
    db.session.commit()
    return paper


def get_paper(paper_id):
    """按编号取论文，取不到返回 None（不抛异常，调用方自己 abort(404)）。"""
    return db.session.get(Paper, paper_id)


def update_paper_status(paper_id, status=None, progress=None, message=None,
                       error_message=None, char_count=None, term_count=None):
    """后台任务每推进一步就调一次。只更新传入的字段。

    status 传 done / failed 时自动写 finished_at。
    """
    paper = db.session.get(Paper, paper_id)
    if paper is None:
        return None
    if status is not None:
        paper.status = status
    if progress is not None:
        paper.progress = max(0, min(100, int(progress)))
    if message is not None:
        paper.message = _clip(message, 300)
    if error_message is not None:
        paper.error_message = error_message or ""
    if char_count is not None:
        paper.char_count = int(char_count)
    if term_count is not None:
        paper.term_count = int(term_count)
    if status in ("done", "failed"):
        paper.finished_at = datetime.now()
    db.session.commit()
    return paper


def claim_paper(paper_id):
    """把 pending 的论文原子地改成 processing，成功返回 True。

    为什么需要它：两条线程同时处理同一篇论文会互相覆盖数据、把 SQLite 锁死。
    这里用一条带条件的 UPDATE 完成「检查 + 占用」，数据库层面保证只有一条线程成功。
    D 的上传路由建议在启动后台任务前调用。
    """
    updated = (db.session.query(Paper)
               .filter(Paper.id == paper_id, Paper.status == "pending")
               .update({
                   Paper.status: "processing",
                   Paper.message: "开始处理",
                   Paper.error_message: "",
               }, synchronize_session=False))
    db.session.commit()
    return updated == 1


def reset_paper_for_retry(paper_id):
    """「重新处理」：只允许从 failed 重置回 pending，其它状态返回 None。

    不重置 processing，是为了挡住用户连点两次按钮导致两条线程抢同一篇论文。
    """
    paper = db.session.get(Paper, paper_id)
    if paper is None or paper.status != "failed":
        return None
    paper.status = "pending"
    paper.progress = 0
    paper.message = "等待处理"
    paper.error_message = ""
    paper.finished_at = None
    db.session.commit()
    return paper


def list_papers(category=None):
    """历史页用：全部论文，新的在前。"""
    query = Paper.query
    if category:
        query = query.filter_by(category=category)
    return query.order_by(Paper.uploaded_at.desc()).all()


def list_papers_page(category=None, page=1, per_page=20):
    """历史页需要分页时用（C 的模板有分页条）。

    返回 Flask-SQLAlchemy 的分页对象：.items / .total / .pages / .has_prev ...
    """
    query = Paper.query
    if category:
        query = query.filter_by(category=category)
    query = query.order_by(Paper.uploaded_at.desc())
    return query.paginate(page=max(1, page), per_page=per_page, error_out=False)


def list_categories():
    """历史页分类下拉框的选项。"""
    rows = db.session.query(Paper.category).distinct().all()
    return [r[0] for r in rows if r[0]]


def delete_paper(paper_id):
    """删论文。术语、句子、共现边由数据库级联删除（依赖 database.py 里的 PRAGMA）。"""
    paper = db.session.get(Paper, paper_id)
    if paper is None:
        return False
    db.session.delete(paper)
    db.session.commit()
    return True


# ---------- 写入解析结果 ----------

def save_sentences(paper_id, sentences):
    """sentences: List[str]，按顺序写入，seq 从 1 开始。

    先删后插：重传同一篇论文时不会出现两套句子。
    """
    Sentence.query.filter_by(paper_id=paper_id).delete(synchronize_session=False)
    db.session.flush()

    objs = []
    seq = 0
    for text in sentences or []:
        text = (text or "").strip()
        if not text:
            continue
        seq += 1
        objs.append(Sentence(paper_id=paper_id, seq=seq, text=text))
    db.session.add_all(objs)
    db.session.commit()
    return len(objs)


def save_terms(paper_id, terms):
    """terms: List[dict]，先删后插，保证重传不翻倍。

    每条必须包含（缺键不会报错，但那一列会是空的）：
        word / surface / definition_zh / definition_en /
        frequency / context_sentence / is_vocab_hit / in_dict

    本函数做三件兜底，都是为了避免 UNIQUE constraint failed：
    1. 同一批里出现重复 word 时合并成一条（取较大词频，保留非空释义与原句）
    2. 空 word 直接丢弃
    3. in_dict 由 definition_zh 是否有内容决定 —— 没有中文释义的词必须沉到
       「未收录」区，这是规格 7.9 的硬要求，不能靠上游记得传对。
    """
    Term.query.filter_by(paper_id=paper_id).delete(synchronize_session=False)
    db.session.flush()

    merged = {}
    for item in terms or []:
        word = _clip(item.get("word", ""), 200).strip().lower()
        if not word:
            continue
        cur = merged.get(word)
        if cur is None:
            merged[word] = {
                "word": word,
                "surface": _clip(item.get("surface", ""), 200) or word,
                "definition_zh": str(item.get("definition_zh") or ""),
                "definition_en": str(item.get("definition_en") or ""),
                "frequency": int(item.get("frequency", 0) or 0),
                "context_sentence": str(item.get("context_sentence") or ""),
                "is_vocab_hit": bool(item.get("is_vocab_hit", False)),
            }
        else:
            cur["frequency"] = max(cur["frequency"], int(item.get("frequency", 0) or 0))
            cur["is_vocab_hit"] = cur["is_vocab_hit"] or bool(item.get("is_vocab_hit", False))
            for key in ("surface", "definition_zh", "definition_en", "context_sentence"):
                if not cur[key] and item.get(key):
                    cur[key] = str(item[key])

    objs = []
    for item in merged.values():
        objs.append(Term(
            paper_id=paper_id,
            word=item["word"],
            surface=_clip(item["surface"], 200),
            definition_zh=item["definition_zh"],
            definition_en=item["definition_en"],
            frequency=item["frequency"],
            context_sentence=item["context_sentence"],
            is_vocab_hit=item["is_vocab_hit"],
            in_dict=bool(item["definition_zh"].strip()),
        ))
    db.session.add_all(objs)
    db.session.commit()
    return len(objs)


def save_cooccurrences(paper_id, edges):
    """edges: List[(term_a_id, term_b_id, weight)]，调用方不必保证顺序。

    本函数统一做 min/max，并按 pair 去重（保留较大权重），
    这样即使 A 传进来 (A,B) 和 (B,A) 两条，也不会撞唯一约束。
    """
    Cooccurrence.query.filter_by(paper_id=paper_id).delete(synchronize_session=False)
    db.session.flush()

    best = {}
    for a, b, w in edges or []:
        a, b = int(a), int(b)
        if a == b:
            continue
        pair = (min(a, b), max(a, b))
        weight = int(w)
        if pair in best:
            best[pair] = max(best[pair], weight)
        else:
            best[pair] = weight

    objs = [Cooccurrence(paper_id=paper_id, term_a_id=a, term_b_id=b, weight=w)
            for (a, b), w in best.items()]
    db.session.add_all(objs)
    db.session.commit()
    return len(objs)


def get_term_ids_by_word(paper_id):
    """给 A 算共现用：{word: term_id}。"""
    rows = db.session.query(Term.word, Term.id).filter_by(paper_id=paper_id).all()
    return {w: i for w, i in rows}


# ---------- 词典页查询 ----------

def get_terms_page(paper_id, page=1, per_page=50, q=None, sort="frequency"):
    """主列表：只返回有中文释义的术语（未收录的走 count_unlisted / get_unlisted_terms）。

    返回分页对象：.items / .total / .pages / .has_prev / .prev_num / .next_num。
    """
    query = Term.query.filter_by(paper_id=paper_id).filter(Term.in_dict.is_(True))

    if q:
        pattern = _like_pattern(q)
        query = query.filter(or_(
            Term.word.like(pattern, escape="\\"),
            Term.definition_zh.like(pattern, escape="\\"),
            Term.definition_en.like(pattern, escape="\\"),
        ))

    if sort == "word":
        query = query.order_by(Term.word.asc())
    elif sort == "frequency_asc":
        query = query.order_by(Term.frequency.asc(), Term.word.asc())
    else:
        query = query.order_by(Term.frequency.desc(), Term.word.asc())

    return query.paginate(page=max(1, page), per_page=per_page, error_out=False)


def count_unlisted(paper_id):
    """没有中文释义的术语条数。"""
    return (Term.query.filter_by(paper_id=paper_id)
            .filter(Term.in_dict.is_(False)).count())


def get_unlisted_terms(paper_id, limit=300):
    """未收录术语，按词频降序。默认最多 300 条，够展示、不会拖慢首屏。"""
    return (Term.query.filter_by(paper_id=paper_id)
            .filter(Term.in_dict.is_(False))
            .order_by(Term.frequency.desc(), Term.word.asc())
            .limit(limit).all())


def filter_terms(paper_id, q=None, limit=50):
    """词典页顶部搜索框的实时过滤。返回 (total, items)。

    total 是命中总数（可能大于 limit），C 的「共 N 条」用它，不会因为
    只取前 50 条而把数字显示错。
    """
    query = Term.query.filter_by(paper_id=paper_id).filter(Term.in_dict.is_(True))
    if q:
        pattern = _like_pattern(q)
        query = query.filter(or_(
            Term.word.like(pattern, escape="\\"),
            Term.definition_zh.like(pattern, escape="\\"),
        ))
    total = query.count()
    items = query.order_by(Term.frequency.desc(), Term.word.asc()).limit(limit).all()
    return total, items


def get_terms_for_filter(paper_id, q=None, limit=50):
    """兼容手册里的旧签名：只要列表。新代码建议用 filter_terms()（能拿到总数）。"""
    return filter_terms(paper_id, q=q, limit=limit)[1]


# ---------- 全站搜索 ----------

def search_terms_global(q, page=1, per_page=20):
    """跨论文搜索，只搜已处理完成的论文。

    返回 (行列表, 总数)，每行是 (Term, Paper) 二元组，模板里按元组解包。
    """
    pattern = _like_pattern(q)
    query = (db.session.query(Term, Paper)
             .join(Paper, Term.paper_id == Paper.id)
             .filter(Paper.status == "done")
             .filter(or_(
                 Term.word.like(pattern, escape="\\"),
                 Term.definition_zh.like(pattern, escape="\\"),
                 Term.definition_en.like(pattern, escape="\\"),
             )))

    total = query.count()
    rows = (query.order_by(Term.frequency.desc(), Term.word.asc())
            .offset((max(1, page) - 1) * per_page).limit(per_page).all())
    return rows, total


def suggest_terms(q, paper_id=None, limit=5):
    """搜不到时给近似建议（difflib 编辑距离）。

    只在 LIKE 完全无结果时调用，别每次搜索都算——它要把两万个词逐个比一遍。
    """
    query = db.session.query(Term.word).distinct()
    if paper_id:
        query = query.filter(Term.paper_id == paper_id)
    words = [w for (w,) in query.limit(20000).all()]
    return difflib.get_close_matches((q or "").strip().lower(), words,
                                     n=limit, cutoff=0.6)


# ---------- 图谱数据 ----------

def get_graph_data(paper_id, center_term_id=None, max_neighbors=30, min_weight=2):
    """返回 (nodes, edges)，A 的 routes/graph.py 直接拿去喂 networkx。

    nodes: [{'id', 'word', 'frequency', 'is_vocab_hit'}]
    edges: [{'source', 'target', 'weight'}]

    center_term_id 有值 -> 自我中心图（边权 ≥ 1，取该词最粗的 30 条边，
                          邻居之间的边也画上，图才有结构而不是星形）
    center_term_id 为空 -> 全篇图（边权 ≥ 2，取最粗的 60 条边）
    """
    if center_term_id:
        # 中心词必须属于这篇论文，否则返回空（C 的页面显示空态，而不是 500）
        center = (Term.query.filter_by(id=center_term_id, paper_id=paper_id).first())
        if center is None:
            return [], []

        main_edges = (Cooccurrence.query.filter_by(paper_id=paper_id)
                      .filter(or_(Cooccurrence.term_a_id == center_term_id,
                                  Cooccurrence.term_b_id == center_term_id))
                      .order_by(Cooccurrence.weight.desc())
                      .limit(max_neighbors).all())
        edge_map = {e.id: e for e in main_edges}
        node_ids = {center_term_id}
        for e in main_edges:
            node_ids.add(e.term_a_id)
            node_ids.add(e.term_b_id)

        if len(node_ids) > 2:
            extra = (Cooccurrence.query.filter_by(paper_id=paper_id)
                     .filter(Cooccurrence.term_a_id.in_(node_ids))
                     .filter(Cooccurrence.term_b_id.in_(node_ids))
                     .order_by(Cooccurrence.weight.desc())
                     .limit(200).all())
            for e in extra:
                edge_map[e.id] = e
        edges = list(edge_map.values())
    else:
        edges = (Cooccurrence.query.filter_by(paper_id=paper_id)
                 .filter(Cooccurrence.weight >= min_weight)
                 .order_by(Cooccurrence.weight.desc())
                 .limit(60).all())
        node_ids = set()
        for e in edges:
            node_ids.add(e.term_a_id)
            node_ids.add(e.term_b_id)

    if not node_ids:
        return [], []

    terms = (Term.query.filter(Term.id.in_(node_ids))
             .filter(Term.paper_id == paper_id).all())
    nodes = [{
        "id": t.id,
        "word": t.surface or t.word,
        "frequency": t.frequency,
        "is_vocab_hit": t.is_vocab_hit,
    } for t in terms]

    edge_list = [{"source": e.term_a_id, "target": e.term_b_id, "weight": e.weight}
                 for e in edges]
    return nodes, edge_list


# ---------- 交接速查（与规格第十章一致） ----------
#
# A：tokenize_for_match / vocab.match / dictionary.lookup / dictionary.is_basic_word
#    save_sentences(paper_id, sentences) / save_terms(paper_id, terms)
#    get_term_ids_by_word(paper_id) / save_cooccurrences(paper_id, edges)
#    get_graph_data(paper_id, center_term_id)
# D：create_paper(...) / update_paper_status(...) / get_paper(...) / claim_paper(...)
#    reset_paper_for_retry(...)
# C：/paper/<id>、/api/paper/<id>/terms、/history、/search 四个入口的模板变量
