# services/dictionary.py
"""ECDICT 词典查询。数据来自官方 SQLite 版（data/stardict.db）。

负责人：B。对外只暴露两个方法：
    get_dict().lookup(word)         -> dict（找不到也返回结构完整的空值，不抛异常）
    get_dict().is_basic_word(word)  -> bool（是不是该被排除的中学/四六级基础词）

为什么用 SQLite 而不是把 CSV 读进内存：ECDICT 完整版约 77 万词条，
全读进 Python 字典要占 1GB 左右内存，同学的笔记本同时开浏览器和编辑器会卡死。
SQLite 版带索引，单次查询毫秒级，内存占用接近零。
"""

import os
import sqlite3
import threading
from functools import lru_cache

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DICT_PATH = os.path.join(BASE_DIR, "data", "stardict.db")

# 中考 / 高考 / 四级 / 六级：命中这些标签的一律算基础词
BASIC_TAGS = ("zk", "gk", "cet4", "cet6")

# 词频排名小于这个数的算常见词（bnc / frq 都是越小越常见）
COMMON_RANK = 8000


class Dictionary:
    """ECDICT 的只读查询封装。

    一个进程只建一个实例（见文件末尾的 get_dict），
    连接用 check_same_thread=False 打开，并用一把锁串行化查询，
    因为 A 的后台处理线程也会查词典。
    """

    def __init__(self, db_path: str = DICT_PATH):
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"找不到词典文件：{db_path}\n"
                "请下载 ecdict-sqlite-28.zip，解压出 stardict.db 放到 data/ 目录。"
            )
        self.db_path = db_path
        self._conn = None
        self._lock = threading.Lock()

    @property
    def conn(self):
        """第一次真正用到时才建连接：import 本模块不会因为缺文件而崩。"""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # ---------- 对外接口 ----------

    def lookup(self, word: str) -> dict:
        """查一个词，返回 {found, zh, en, tag, bnc, frq, exchange}。

        查询顺序：原词 → 全小写 → 首字母大写（ECDICT 里专有名词多按原样收录）。
        三次都查不到返回 found=False 的空结构，**不抛异常**——查不到是常态
        （基因名、新方法名词典本来就不收），让上层决定怎么展示。
        """
        if not word:
            return self._empty()
        info = self._lookup_cached(word.strip())
        # 缓存里存的是 tuple，这里每次给调用方一个新的 dict，
        # 防止有人改了返回值把缓存污染了。
        return {
            "found": info[0],
            "zh": info[1],
            "en": info[2],
            "tag": info[3],
            "bnc": info[4],
            "frq": info[5],
            "exchange": info[6],
        }

    def is_basic_word(self, word: str) -> bool:
        """判断是不是「该被排除的基础词」。

        判定顺序（与规格 7.4 第二步一致）：
        1. 词典里根本没有 -> 不是基础词（可能是专业词，交给上层判断）
        2. 带中考/高考/四六级标签 -> 是基础词
        3. bnc 或 frq 排名靠前 -> 是基础词
        4. 无标签也无排名 -> 视为无信息，按基础词处理
           （宁可漏掉几个专业词，也不要收一堆 the / of / result）
        """
        info = self.lookup(word)
        if not info["found"]:
            return False
        tags = info["tag"].split()
        if any(t in BASIC_TAGS for t in tags):
            return True
        ranks = [r for r in (info["bnc"], info["frq"]) if r > 0]
        if not ranks:
            return True
        return min(ranks) < COMMON_RANK

    def close(self):
        """进程退出或换库时用；日常不用管。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---------- 内部实现 ----------

    @lru_cache(maxsize=5000)
    def _lookup_cached(self, word: str) -> tuple:
        """真正的查询。结果缓存 5000 条：一篇论文里高频词会反复查，
        缓存能省掉大量重复的 SQL。

        返回 tuple 而不是 dict，是因为 lru_cache 会把同一个对象反复发给
        不同调用方；tuple 不可变，不会被谁改坏。
        """
        sql = ("SELECT translation, definition, tag, bnc, frq, exchange "
               "FROM stardict WHERE word = ? LIMIT 1")
        candidates = (word, word.lower(), word.capitalize())

        with self._lock:
            cur = self.conn.cursor()
            for key in dict.fromkeys(candidates):   # 去重且保序
                row = cur.execute(sql, (key,)).fetchone()
                if row is not None:
                    return (
                        True,
                        (row["translation"] or "").strip(),
                        (row["definition"] or "").strip(),
                        (row["tag"] or "").strip(),
                        int(row["bnc"] or 0),
                        int(row["frq"] or 0),
                        (row["exchange"] or "").strip(),
                    )
        return self._empty_tuple()

    @staticmethod
    def _empty_tuple() -> tuple:
        return (False, "", "", "", 0, 0, "")

    @classmethod
    def _empty(cls) -> dict:
        return {"found": False, "zh": "", "en": "", "tag": "",
                "bnc": 0, "frq": 0, "exchange": ""}


_dict = None


def get_dict() -> Dictionary:
    """全局单例。第一次调用时才建连接。"""
    global _dict
    if _dict is None:
        _dict = Dictionary()
    return _dict
