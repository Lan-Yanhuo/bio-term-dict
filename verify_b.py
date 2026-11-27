# tools/verify_b.py
"""B 同学数据层自检脚本。不需要真实词典与词表，跑完约 2 秒。

用法：
    python tools/verify_b.py            # 跑全部断言，打印 PASS/FAIL
    python tools/verify_b.py --serve    # 造一篇演示论文，起本地服务，人工看页面

它做什么：
    1. 建一个临时 SQLite 库，检查四张表、唯一约束、CHECK 约束是否都在
    2. 用一份假 ECDICT（几十条）验证 dictionary 的命中/未命中/基础词判断
    3. 用一份假词表验证最长匹配、连字符切分、match_terms 别名
    4. 验证入库幂等（连续保存两次条数不变）、重复 word 兜底、in_dict 派生
    5. 验证分页、未收录、全站搜索、difflib 纠错、图谱数据去重
    6. 用 Flask 测试客户端把四条路由各打一遍（含 404、越界页码）

注意：脚本里的假词典、假词表、占位模板只服务于自检，
      不参与线上运行，也不属于交付页面（页面归 C）。
"""

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# Windows 控制台默认可能是 GBK，先切 UTF-8，免得中文断言信息乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from flask import Flask
    from models.database import db, Paper, Term, Sentence, Cooccurrence
    from services import db_ops
    from services.dictionary import Dictionary
    from services.vocab import Vocab, tokenize_for_match
except ImportError as exc:                       # pragma: no cover
    print(f"[FAIL] 缺少依赖：{exc}")
    print("请先执行：pip install -r requirements.txt")
    raise SystemExit(2)


# ---------------- 假数据 ----------------

# (word, translation, definition, tag, bnc, frq, exchange)
FAKE_DICT_ROWS = [
    ("mitochondria", "线粒体", "A membrane-bound organelle.", "", 15000, 14000, ""),
    ("methylation", "甲基化", "Addition of a methyl group.", "", 0, 0, ""),
    ("gene", "基因", "A unit of heredity.", "gk", 1200, 1100, ""),
    ("protein", "蛋白质", "A chain of amino acids.", "cet6", 900, 800, ""),
    ("the", "这", "definite article", "zk", 1, 1, ""),
    ("result", "结果", "outcome", "cet4", 800, 700, ""),
    ("cell", "细胞", "basic unit of life", "cet4", 400, 380, ""),
    ("transcription factor", "转录因子", "A protein that binds DNA.", "", 0, 0, ""),
]

FAKE_VOCAB = [
    "gene", "gene expression", "protein", "dna methylation", "methylation",
    "transcription factor", "breast cancer", "mitochondria",
    "brca1", "il-6", "tnf-α", "t-cell", "p53",
]


def write_fake_dict(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE stardict (
        word TEXT PRIMARY KEY, translation TEXT, definition TEXT,
        tag TEXT, bnc INTEGER, frq INTEGER, exchange TEXT)""")
    conn.executemany("INSERT INTO stardict VALUES (?,?,?,?,?,?,?)", FAKE_DICT_ROWS)
    conn.commit()
    conn.close()


def write_fake_vocab(path: Path):
    path.write_text("\n".join(FAKE_VOCAB) + "\n", encoding="utf-8")


def make_app(tmp: Path):
    instance = tmp / "instance"
    instance.mkdir(parents=True, exist_ok=True)
    app = Flask(
        __name__,
        template_folder=str(ROOT / "tests" / "smoke_templates"),
        instance_path=str(instance),
    )
    app.config.update(
        SECRET_KEY="verify-b",
        TESTING=True,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(tmp / 'termgraph.db').as_posix()}?timeout=15",
    )
    db.init_app(app)

    # 只注册 B 的两个蓝图；A 的 graph、D 的 upload 由他们自己的 app.py 注册
    from routes.paper import paper_bp
    from routes.search import search_bp
    app.register_blueprint(paper_bp)
    app.register_blueprint(search_bp)
    return app


def seed_demo(app):
    """造一篇「已完成」的论文，供页面自测与 --serve 手工查看。"""
    sentences = [
        "DNA methylation of the gene promoter represses gene expression in drought stress.",
        "The transcription factor binds the promoter and recruits a protein complex.",
        "Methylation and the protein complex jointly silence gene expression.",
        "BRCA1 is mentioned here only as an unlisted term example.",
    ]
    term_rows = [
        # word, surface, zh, en, freq, context, is_vocab_hit
        ("gene", "Gene", "基因", "A unit of heredity.", 12, sentences[0], True),
        ("methylation", "methylation", "甲基化", "Addition of a methyl group.", 8, sentences[0], True),
        ("protein", "protein", "蛋白质", "A chain of amino acids.", 5, sentences[1], True),
        ("transcription factor", "transcription factor", "转录因子",
         "A protein that binds DNA.", 4, sentences[1], True),
        ("brca1", "BRCA1", "", "", 3, sentences[3], True),      # 词典查不到 -> 未收录
        ("zzzz", "zzzz", "", "", 2, sentences[3], False),        # 未收录
    ]
    terms = [{
        "word": w, "surface": s, "definition_zh": zh, "definition_en": en,
        "frequency": f, "context_sentence": ctx, "is_vocab_hit": hit, "in_dict": bool(zh),
    } for (w, s, zh, en, f, ctx, hit) in term_rows]

    with app.app_context():
        paper = db_ops.create_paper("拟南芥干旱胁迫下的 DNA 甲基化动态",
                                    "a1b2c3d4.pdf", "pdf", "遗传")
        db_ops.claim_paper(paper.id)
        db_ops.save_sentences(paper.id, sentences)
        db_ops.save_terms(paper.id, terms)
        db_ops.update_paper_status(paper.id, status="done", progress=100,
                                   message="处理完成", char_count=241, term_count=len(terms))

        ids = db_ops.get_term_ids_by_word(paper.id)
        edges = [
            (ids["gene"], ids["methylation"], 4),
            (ids["methylation"], ids["gene"], 4),      # 故意反序重复，验证去重
            (ids["gene"], ids["protein"], 3),
            (ids["methylation"], ids["protein"], 2),
            (ids["transcription factor"], ids["gene"], 1),
            (ids["brca1"], ids["gene"], 1),
        ]
        db_ops.save_cooccurrences(paper.id, edges)
        return paper.id


# ---------------- 断言框架 ----------------

class Checker:
    def __init__(self):
        self.passed = 0
        self.failed = []

    def check(self, name, condition, detail=""):
        if condition:
            self.passed += 1
            print(f"[PASS] {name}")
        else:
            self.failed.append(f"{name} {detail}")
            print(f"[FAIL] {name} {detail}")

    def summary(self):
        total = self.passed + len(self.failed)
        print("-" * 60)
        print(f"通过 {self.passed} / {total}")
        for item in self.failed:
            print(f"  未通过：{item}")
        return not self.failed


def run():
    tmp = Path(tempfile.mkdtemp(prefix="b_verify_"))
    c = Checker()
    app = make_app(tmp)
    dict_path = tmp / "stardict.db"
    vocab_path = tmp / "vocab_mesh.txt"
    write_fake_dict(dict_path)
    write_fake_vocab(vocab_path)

    try:
        with app.app_context():
            db.create_all()

        # ---- 1. 表结构 ----
        conn = sqlite3.connect(tmp / "termgraph.db")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        c.check("四张表都建出来了", {"paper", "term", "sentence", "cooccurrence"} <= tables,
                f"实际={sorted(tables)}")
        sql_all = " ".join(r[0] or "" for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','index')"))
        c.check("term 有 (paper_id, word) 唯一约束", "uq_term_paper_word" in sql_all)
        c.check("cooccurrence 有边唯一约束", "uq_cooc_edge" in sql_all)
        c.check("cooccurrence 有 a<b 的 CHECK", "ck_cooc_order" in sql_all)
        c.check("索引 ix_term_paper_freq 存在", "ix_term_paper_freq" in sql_all)
        c.check("索引 ix_sentence_paper_seq 存在", "ix_sentence_paper_seq" in sql_all)

        # ---- 2. 词典 ----
        d = Dictionary(db_path=str(dict_path))
        mito = d.lookup("mitochondria")
        c.check("mitochondria 查到中文「线粒体」", mito["found"] and "线粒体" in mito["zh"],
                str(mito))
        c.check("mitochondria 不是基础词", d.is_basic_word("mitochondria") is False)
        c.check("the 是基础词（zk 标签）", d.is_basic_word("the") is True)
        c.check("result 是基础词（cet4）", d.is_basic_word("result") is True)
        c.check("BRCA1 查不到但 found=False 不抛异常",
                d.lookup("BRCA1")["found"] is False)
        c.check("查不到的词不算基础词", d.is_basic_word("zzzzzzzz") is False)
        c.check("多词术语也能按原样查（transcription factor）",
                d.lookup("transcription factor")["found"] is True)
        c.check("大小写回退（Methylation -> methylation）",
                d.lookup("Methylation")["found"] is True)

        # ---- 3. 词表与最长匹配 ----
        v = Vocab(path=str(vocab_path))
        c.check("词表条数正确", len(v) == len(FAKE_VOCAB), f"len={len(v)}")
        c.check("contains('gene') 为真", v.contains("gene") is True)
        c.check("contains('genee') 为假", v.contains("genee") is False)

        sentence = ("The transcription factor BRCA1 regulates gene expression; "
                    "IL-6-induced TNF-α rises, and DNA methylation of a T-cell gene follows.")
        tokens = tokenize_for_match(sentence)
        hits = v.match(tokens)
        c.check("tokenize 保留 BRCA1", "BRCA1" in tokens, str(tokens))
        c.check("tokenize 保留 TNF-α", any("α" in t for t in tokens), str(tokens))
        c.check("transcription factor 整串命中", "transcription factor" in hits, str(hits))
        c.check("命中后 factor 不再单独出现", "factor" not in hits, str(hits))
        c.check("gene expression 整串命中", "gene expression" in hits, str(hits))
        c.check("IL-6 能从连写词里切出来", "il-6" in hits, str(hits))
        c.check("TNF-α 命中", "tnf-α" in hits or "tnf" in hits, str(hits))
        c.check("T-cell 命中", "t-cell" in hits, str(hits))
        c.check("match_terms 是 match 的别名", v.match_terms(tokens) == hits)

        # ---- 4. 入库 ----
        paper_id = seed_demo(app)
        with app.app_context():
            c.check("论文状态为 done", db_ops.get_paper(paper_id).status == "done")
            c.check("术语入库 6 条", Term.query.filter_by(paper_id=paper_id).count() == 6)
            c.check("句子入库 4 条", Sentence.query.filter_by(paper_id=paper_id).count() == 4)
            c.check("句子 seq 从 1 开始",
                    Sentence.query.filter_by(paper_id=paper_id).order_by(Sentence.seq).first().seq == 1)
            c.check("反序重复边只存 1 条（6 条输入 → 5 条边）",
                    Cooccurrence.query.filter_by(paper_id=paper_id).count() == 5,
                    f"count={Cooccurrence.query.filter_by(paper_id=paper_id).count()}")

            page = db_ops.get_terms_page(paper_id)
            c.check("主列表只含有中文释义的 4 条", page.total == 4, f"total={page.total}")
            c.check("未收录 2 条", db_ops.count_unlisted(paper_id) == 2)
            c.check("未收录不出现在主列表",
                    all(t.in_dict for t in page.items))

            # 幂等：再存一遍同样的数据
            rows = [t.to_dict() for t in Term.query.filter_by(paper_id=paper_id).all()]
            for r in rows:
                r["is_vocab_hit"] = r["in_dict"] or r["word"] in ("brca1", "zzzz")
            db_ops.save_terms(paper_id, rows)
            c.check("重复入库后条数不翻倍",
                    Term.query.filter_by(paper_id=paper_id).count() == 6)
            c.check("重复入库后主列表仍为 4 条",
                    db_ops.get_terms_page(paper_id).total == 4)

            # 同批重复 word + 无中文释义却传 in_dict=True
            saved = db_ops.save_terms(paper_id, [
                {"word": "Gene", "surface": "Gene", "definition_zh": "基因",
                 "frequency": 3, "in_dict": True},
                {"word": "gene", "surface": "gene", "definition_zh": "",
                 "frequency": 9, "in_dict": True},
                {"word": "", "surface": "", "frequency": 99},
            ])
            c.check("同批重复 word 合并为 1 条", saved == 1, f"saved={saved}")
            only = Term.query.filter_by(paper_id=paper_id).one()
            c.check("合并时取较大词频", only.frequency == 9, f"freq={only.frequency}")
            c.check("合并时保留非空中文释义", only.definition_zh == "基因")
            c.check("有中文释义时 in_dict 为 True", only.in_dict is True)
            c.check("word 统一转小写", only.word == "gene")

            # in_dict 由「有没有中文释义」派生：即使上游传 True，没有中文也必须沉底
            db_ops.save_terms(paper_id, [
                {"word": "orphan", "surface": "ORPHAN", "in_dict": True, "frequency": 2},
            ])
            orphan = Term.query.filter_by(paper_id=paper_id).one()
            c.check("无中文释义时 in_dict 强制为 False", orphan.in_dict is False)

        # ---- 5. 共现与图谱 ----
        with app.app_context():
            # 上一段把 term 全删了只剩 gene，重新造一遍完整数据
            db_ops.save_terms(paper_id, [
                {"word": "gene", "surface": "Gene", "definition_zh": "基因", "frequency": 12,
                 "context_sentence": "gene sentence", "is_vocab_hit": True},
                {"word": "methylation", "surface": "methylation", "definition_zh": "甲基化",
                 "frequency": 8, "context_sentence": "methylation sentence", "is_vocab_hit": True},
                {"word": "protein", "surface": "protein", "definition_zh": "蛋白质",
                 "frequency": 5, "context_sentence": "protein sentence", "is_vocab_hit": True},
            ])
            ids = db_ops.get_term_ids_by_word(paper_id)
            a, b, cc = ids["gene"], ids["methylation"], ids["protein"]
            n = db_ops.save_cooccurrences(paper_id, [(a, b, 4), (b, a, 4), (a, cc, 2)])
            c.check("共现边去重后 2 条", n == 2, f"n={n}")

            nodes, edges = db_ops.get_graph_data(paper_id, min_weight=2)
            c.check("全篇图有节点", len(nodes) == 3, f"nodes={len(nodes)}")
            c.check("全篇图边权 ≥ 2", all(e["weight"] >= 2 for e in edges))
            c.check("边方向统一 source < target",
                    all(e["source"] < e["target"] for e in edges))
            c.check("节点带 surface 与频次",
                    all({"id", "word", "frequency", "is_vocab_hit"} <= set(nd) for nd in nodes))

            nodes2, edges2 = db_ops.get_graph_data(paper_id, center_term_id=a)
            c.check("自我中心图包含中心词", any(nd["id"] == a for nd in nodes2))
            c.check("不存在的 term_id 返回空而不报错",
                    db_ops.get_graph_data(paper_id, center_term_id=999999) == ([], []))

        # ---- 6. 搜索与分页 ----
        with app.app_context():
            rows, total = db_ops.search_terms_global("基因")
            c.check("全站搜索能按中文释义命中", total == 1, f"total={total}")
            c.check("搜索结果带论文对象", rows and rows[0][1].id == paper_id)

            rows, total = db_ops.search_terms_global("Gene")
            c.check("全站搜索大小写不敏感", total == 1, f"total={total}")

            rows, total = db_ops.search_terms_global("%")
            c.check("LIKE 通配符已转义（搜 % 不匹配全库）", total == 0, f"total={total}")

            c.check("拼写纠错能给出 gene",
                    "gene" in db_ops.suggest_terms("geen"), str(db_ops.suggest_terms("geen")))

            all_page = db_ops.get_terms_page(paper_id, page=1, per_page=2)
            c.check("分页每页 2 条", len(all_page.items) == 2)
            c.check("分页总页数正确", all_page.pages == 2, f"pages={all_page.pages}")
            second = db_ops.get_terms_page(paper_id, page=2, per_page=2)
            c.check("第 2 页与第 1 页不重复",
                    {t.id for t in second.items}.isdisjoint({t.id for t in all_page.items}))

            total_hit, items = db_ops.filter_terms(paper_id, "methyl")
            c.check("页内过滤命中 1 条", total_hit == 1 and len(items) == 1,
                    f"total={total_hit}")

        # ---- 7. 状态机 ----
        with app.app_context():
            p = db_ops.get_paper(paper_id)
            db_ops.update_paper_status(paper_id, status="failed", progress=-5,
                                       message="处理失败", error_message="模拟失败")
            p = db_ops.get_paper(paper_id)
            c.check("失败态写入 finished_at", p.finished_at is not None)
            c.check("progress 被钳在 0–100", p.progress == 0, f"progress={p.progress}")
            c.check("failed 可重置为 pending",
                    db_ops.reset_paper_for_retry(paper_id).status == "pending")
            c.check("非 failed 状态不允许重置",
                    db_ops.reset_paper_for_retry(paper_id) is None)
            c.check("claim_paper 第一次成功", db_ops.claim_paper(paper_id) is True)
            c.check("claim_paper 第二次失败（防双线程）",
                    db_ops.claim_paper(paper_id) is False)

        # ---- 8. 路由 ----
        with app.app_context():
            db_ops.update_paper_status(paper_id, status="done", progress=100,
                                       message="处理完成", term_count=3)
        client = app.test_client()

        r = client.get(f"/paper/{paper_id}")
        body = r.get_data(as_text=True)
        c.check("GET /paper/<id> 200", r.status_code == 200, f"code={r.status_code}")
        c.check("词典页渲染出术语", "基因" in body)
        c.check("词典页显示未收录计数", "未收录" in body)

        r = client.get(f"/paper/{paper_id}?page=99")
        c.check("越界页码不报错", r.status_code == 200, f"code={r.status_code}")

        r = client.get(f"/api/paper/{paper_id}/terms?q=gene")
        data = r.get_json()
        c.check("过滤接口返回 JSON", r.status_code == 200 and data["ok"] is True, str(data)[:120])
        c.check("过滤接口带 total 与 items",
                data.get("total") == 1 and len(data.get("items", [])) == 1)

        r = client.get("/api/paper/999999/terms?q=gene")
        c.check("不存在的论文接口返回 404 JSON",
                r.status_code == 404 and r.get_json()["ok"] is False)

        r = client.get("/history")
        c.check("GET /history 200", r.status_code == 200, f"code={r.status_code}")
        r = client.get("/history?category=遗传")
        c.check("历史页按分类筛选 200", r.status_code == 200)
        r = client.get("/history?category=不存在")
        c.check("空分类显示空态", r.status_code == 200 and "还没有上传" in r.get_data(as_text=True))

        r = client.get("/search?q=gene")
        c.check("GET /search 200", r.status_code == 200, f"code={r.status_code}")
        r = client.get("/search?q=geen")
        c.check("搜索无结果时给纠错建议", "您是不是想找" in r.get_data(as_text=True))

        r = client.get("/paper/999999")
        c.check("不存在的论文页面 404", r.status_code == 404, f"code={r.status_code}")

        # ---- 9. 级联删除 ----
        with app.app_context():
            c.check("删除论文成功", db_ops.delete_paper(paper_id) is True)
            c.check("术语被级联删除", Term.query.filter_by(paper_id=paper_id).count() == 0)
            c.check("句子被级联删除", Sentence.query.filter_by(paper_id=paper_id).count() == 0)
            c.check("共现边被级联删除", Cooccurrence.query.filter_by(paper_id=paper_id).count() == 0)

        conn.close()
    finally:
        with app.app_context():
            db.session.remove()
        try:
            db.engine.dispose()
        except Exception:
            pass

    ok = c.summary()
    shutil.rmtree(tmp, ignore_errors=True)
    return ok


def serve():
    """造一篇演示数据并起服务，供人工点页面看效果。"""
    tmp = Path(tempfile.mkdtemp(prefix="b_serve_"))
    app = make_app(tmp)
    write_fake_dict(tmp / "stardict.db")
    write_fake_vocab(tmp / "vocab_mesh.txt")
    with app.app_context():
        db.create_all()

    paper_id = seed_demo(app)
    print(f"演示数据已就绪（临时库 {tmp}）")
    print(f"  词典页  http://127.0.0.1:5000/paper/{paper_id}")
    print(f"  历史页  http://127.0.0.1:5000/history")
    print(f"  搜索页  http://127.0.0.1:5000/search?q=gene")
    print("按 Ctrl+C 结束。")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="B 数据层自检")
    parser.add_argument("--serve", action="store_true", help="起本地服务人工查看")
    args = parser.parse_args()
    if args.serve:
        serve()
    else:
        raise SystemExit(0 if run() else 1)
