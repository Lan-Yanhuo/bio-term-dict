# services/vocab.py
"""专业词表：加载、切词、最长匹配。

负责人：B。对外接口（A 的 term_extractor 会调）：
    tokenize_for_match(sentence) -> List[str]     把句子切成词
    get_vocab().match(tokens)     -> List[str]     命中的术语（全小写）
    get_vocab().match_terms(tokens)                match 的同义写法（规格第十章用这个名字）
    get_vocab().contains(term)    -> bool          某个词是否在词表里（词形归并时要用）

词表来源：data/vocab_mesh.txt（必需，由 tools/build_vocab.py 一次性生成）
          data/vocab_taxonomy.txt（可选，由 tools/build_taxonomy.py 生成）
"""

import os
import re
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOCAB_PATH = os.path.join(BASE_DIR, "data", "vocab_mesh.txt")
TAXONOMY_PATH = os.path.join(BASE_DIR, "data", "vocab_taxonomy.txt")

# 只取「字母、数字、希腊字母」开头、可带内部连字符的片段。
# 这样 BRCA1、p53、IL-6、TNF-α 都能整体取出来，不会被削成 brca、p、il、tnf。
# \u0370-\u03FF 是希腊字母区（α β γ …）。
TOKEN_RE = re.compile(r"[A-Za-z0-9\u0370-\u03FF][A-Za-z0-9\u0370-\u03FF\-]*")

# 词表里个别条目长得离谱（MeSH 有些组合名十几个词），匹配时一律跳过，
# 一是没意义，二是每多一个词就多一次字符串拼接。
MAX_TERM_WORDS = 8

# 行内空白统一
_WS_RE = re.compile(r"\s+")


def tokenize_for_match(sentence: str):
    """把句子切成词。A 同学的 term_extractor 直接调用这个函数。"""
    return TOKEN_RE.findall(sentence or "")


def _normalize_line(line: str) -> str:
    """一行词表 → 标准形式：去首尾空白、压内部空白、转小写。"""
    return _WS_RE.sub(" ", line.strip().lower())


class Vocab:
    """专业词表，按首词分组 + 最长优先匹配。

    内存结构：
        terms      {完整术语字符串}                     用于 contains()
        by_first   {首词: [(词数, 完整术语, 连字符归一形式), ...]}
                                                        每个列表按词数降序
    只存一份字符串对象被多个结构引用，不复制文本。
    """

    def __init__(self, path: str = VOCAB_PATH, extra_paths=()):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"找不到专业词表：{path}\n"
                "请先运行 python tools/build_vocab.py data/desc2025.xml data/vocab_mesh.txt"
            )
        if isinstance(extra_paths, (str, os.PathLike)):
            extra_paths = (extra_paths,)

        self.terms = set()
        self.by_first = defaultdict(list)
        self.sources = [path]

        self._load_file(path)

        # NCBI 物种名是可选增强：有就合并，没有不影响启动
        for extra in extra_paths:
            if extra and os.path.exists(extra):
                self._load_file(extra)
                self.sources.append(extra)

        # 每个首词组内按词数降序 —— 保证「最长优先」：
        # transcription factor 命中后，不会再让 factor 单独算一次
        for key in self.by_first:
            self.by_first[key].sort(key=lambda x: (x[0], len(x[1])), reverse=True)

    def _load_file(self, path: str):
        with open(path, encoding="utf-8") as f:
            for line in f:
                t = _normalize_line(line)
                if not t or len(t) > 80:
                    continue
                if not re.search(r"[a-z]", t):     # 纯数字、纯符号的条目没有意义
                    continue
                if t in self.terms:
                    continue
                # 连字符当成空格处理：IL-6 与 IL 6 视为同一个术语的两种写法，
                # 这样 "IL-6-induced" 这种连写词里也能切出 IL-6。
                key = t.replace("-", " ")
                word_count = key.count(" ") + 1
                if word_count > MAX_TERM_WORDS:
                    continue
                self.terms.add(t)
                self.by_first[key.split(" ", 1)[0]].append((word_count, t, key))

    def __len__(self):
        return len(self.terms)

    def contains(self, term: str) -> bool:
        """某个词是否在词表里（原样查，不做词形归并）。"""
        return (term or "").strip().lower() in self.terms

    def match(self, tokens):
        """在一串词里找命中的术语，返回术语列表（全小写，按出现顺序）。

        做法：把 token 再按连字符摊平成原子词，从每个位置试着匹配最长的
        候选术语，命中就整段跳过，这样一段文字不会被重复计数。

        词语之间用空格比较、连字符也当空格比较，所以
        'transcription factor'、'IL-6'、'TNF-α' 都能按整串命中。
        """
        hits = []
        if not tokens:
            return hits

        # ['IL-6-induced', 'STAT3'] -> ['il', '6', 'induced', 'stat3']
        atoms = []
        for tok in tokens:
            atoms.extend(a for a in tok.lower().replace("-", " ").split() if a)

        i = 0
        n = len(atoms)
        while i < n:
            matched = False
            cands = self.by_first.get(atoms[i])
            if cands:
                for k, term, key in cands:
                    if i + k <= n and " ".join(atoms[i:i + k]) == key:
                        hits.append(term)
                        i += k
                        matched = True
                        break
            if not matched:
                i += 1
        return hits

    # 规格第十章把接口写成 match_terms()，手册写成 match()。
    # 两个名字指向同一实现，谁照着哪份文档写都不会错。
    def match_terms(self, tokens):
        return self.match(tokens)


_vocab = None


def get_vocab() -> Vocab:
    """全局单例。第一次调用时才加载词表（30 万行约 1–2 秒）。"""
    global _vocab
    if _vocab is None:
        _vocab = Vocab(extra_paths=(TAXONOMY_PATH,))
    return _vocab
