# models/database.py
"""数据模型：四张表（论文、术语、句子、共现边）。

负责人：B。其他人不要改这个文件，也不要另建 db 对象。

与《V4 规格》5.1–5.4 一一对应：
    paper         一篇论文一行
    term          一篇论文里一个术语一行，(paper_id, word) 唯一
    sentence      一句话一行，共现统计的最小窗口
    cooccurrence  两个术语同句出现的一条边，term_a_id < term_b_id
"""

import sqlite3
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

# 全组唯一的 db 对象。A 的 app.py 里 db.init_app(app)，其余人只通过 db_ops 访问。
db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """打开 SQLite 的外键约束。

    为什么必须有这一段：SQLite 默认 **不** 执行外键的 ON DELETE CASCADE，
    也不检查外键是否存在。不打开这个开关，`passive_deletes=True` 和
    `ondelete="CASCADE"` 都只是写在建表语句里的装饰，删论文后术语、
    句子、共现边会全部留在库里变成孤儿数据。

    只在 SQLite 连接上执行，换成 MySQL 时本函数自动跳过。
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Paper(db.Model):
    """一篇论文。一个上传的文件对应一行。"""

    __tablename__ = "paper"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)            # 用户填的标题
    stored_filename = db.Column(db.String(300), nullable=False)  # 磁盘上的真实文件名（UUID 前缀）
    source_type = db.Column(db.String(10), nullable=False, default="txt")   # txt / pdf
    category = db.Column(db.String(50), nullable=False, default="未分类")
    # pending / processing / done / failed，见规格 5.6 状态机
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    progress = db.Column(db.Integer, nullable=False, default=0)
    message = db.Column(db.String(300), nullable=False, default="")
    error_message = db.Column(db.Text, nullable=False, default="")
    char_count = db.Column(db.Integer, nullable=False, default=0)
    term_count = db.Column(db.Integer, nullable=False, default=0)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.now)
    finished_at = db.Column(db.DateTime)

    # 删论文时连带删掉它的术语、句子、共现边（配合上面的 PRAGMA 才真正生效）
    terms = db.relationship(
        "Term", backref="paper", lazy="dynamic",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    def __repr__(self):
        return f"<Paper {self.id} {self.title}>"

    def to_dict(self):
        """给 D 的状态轮询接口用。只输出前端需要的字段，不输出磁盘文件名。"""
        return {
            "paper_id": self.id,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "error_message": self.error_message,
            "char_count": self.char_count,
            "term_count": self.term_count,
            "category": self.category,
            "uploaded_at": self.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if self.uploaded_at else "",
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else "",
        }


class Term(db.Model):
    """一个术语。同一篇论文里同一个词只有一行。"""

    __tablename__ = "term"

    id = db.Column(db.Integer, primary_key=True)
    paper_id = db.Column(db.Integer, db.ForeignKey("paper.id", ondelete="CASCADE"),
                         nullable=False)
    word = db.Column(db.String(200), nullable=False)                  # 归并后的标准形式（小写）
    surface = db.Column(db.String(200), nullable=False, default="")   # 文中首次出现的原始写法
    definition_zh = db.Column(db.Text, nullable=False, default="")
    definition_en = db.Column(db.Text, nullable=False, default="")
    frequency = db.Column(db.Integer, nullable=False, default=0)
    context_sentence = db.Column(db.Text, nullable=False, default="")
    is_vocab_hit = db.Column(db.Boolean, nullable=False, default=False)
    in_dict = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        # 同一篇论文里同一个词只允许一行：重传时不会让词频翻倍
        db.UniqueConstraint("paper_id", "word", name="uq_term_paper_word"),
        # 词典页默认按次数排序，这条联合索引直接服务那个查询
        db.Index("ix_term_paper_freq", "paper_id", "frequency"),
    )

    def to_dict(self):
        """给页内实时过滤接口用（jsonify 不能直接序列化模型对象）。"""
        return {
            "id": self.id,
            "word": self.word,
            "surface": self.surface,
            "definition_zh": self.definition_zh,
            "definition_en": self.definition_en,
            "frequency": self.frequency,
            "context_sentence": self.context_sentence,
            "in_dict": self.in_dict,
        }

    def __repr__(self):
        return f"<Term {self.id} {self.word}>"


class Sentence(db.Model):
    """一句话。共现计算的依据，也是将来「点边看原句」的基础。"""

    __tablename__ = "sentence"

    id = db.Column(db.Integer, primary_key=True)
    paper_id = db.Column(db.Integer, db.ForeignKey("paper.id", ondelete="CASCADE"),
                         nullable=False)
    seq = db.Column(db.Integer, nullable=False)      # 从 1 开始
    text = db.Column(db.Text, nullable=False)

    __table_args__ = (
        db.Index("ix_sentence_paper_seq", "paper_id", "seq"),
    )


class Cooccurrence(db.Model):
    """两个术语在同句出现的一条边。term_a_id 一定小于 term_b_id。"""

    __tablename__ = "cooccurrence"

    id = db.Column(db.Integer, primary_key=True)
    paper_id = db.Column(db.Integer, db.ForeignKey("paper.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    term_a_id = db.Column(db.Integer, db.ForeignKey("term.id", ondelete="CASCADE"),
                          nullable=False)
    term_b_id = db.Column(db.Integer, db.ForeignKey("term.id", ondelete="CASCADE"),
                          nullable=False)
    weight = db.Column(db.Integer, nullable=False, default=1)   # 同句出现的句子条数

    __table_args__ = (
        db.UniqueConstraint("paper_id", "term_a_id", "term_b_id", name="uq_cooc_edge"),
        # 这条 CHECK 从根上消灭 (A,B) 与 (B,A) 两份重复边
        db.CheckConstraint("term_a_id < term_b_id", name="ck_cooc_order"),
    )

    def __repr__(self):
        return f"<Cooc {self.term_a_id}-{self.term_b_id} w={self.weight}>"
