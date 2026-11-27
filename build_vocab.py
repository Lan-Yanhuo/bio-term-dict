# tools/build_vocab.py
"""把 MeSH 描述符 XML 转成一行一个术语的纯文本词表。只需运行一次。

用法：
    python tools/build_vocab.py data/desc2025.xml data/vocab_mesh.txt
    python tools/build_vocab.py data/desc2025.xml.gz data/vocab_mesh.txt   # 也支持 .gz

为什么要有这个脚本：
    MeSH 2025 只发布 XML（约 360MB），NLM 自 2026 年 1 月起停止发布纯文本版。
    程序每次启动都去解析 360MB 不现实，所以先一次性抽出术语，存成 8MB 左右的
    纯文本；之后程序只读那份小词表，原始 XML 可以删掉。

内存要点：
    ET.iterparse 是流式解析，每读完一条 DescriptorRecord 就交出来处理。
    但只调 elem.clear() 不够——根节点仍然挂着已经处理过的空壳元素，30 万条
    攒下来内存照样涨。所以这里额外在每条记录处理完后 root.clear()。
"""

import gzip
import os
import re
import sys
import xml.etree.ElementTree as ET

_WS_RE = re.compile(r"\s+")


def clean(term: str) -> str:
    """术语标准化：压空白、转小写、去掉首尾的标点。"""
    t = _WS_RE.sub(" ", (term or "").strip().lower())
    return t.strip(" .,;:")


def _open_xml(path: str):
    """按扩展名决定是否用 gzip 打开。"""
    if path.lower().endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def build(xml_path: str, out_path: str) -> int:
    """解析 XML 并写出词表，返回写出的术语条数。"""
    terms = set()
    records = 0
    root = None

    handle = _open_xml(xml_path)
    try:
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = elem          # 记住根节点，后面要定期清空
                continue

            if elem.tag != "DescriptorRecord":
                continue
            records += 1

            # 1) 主题词本身，如 "Transcription Factors"
            for name in elem.iter("DescriptorName"):
                s = name.find("String")
                if s is not None and s.text:
                    terms.add(clean(s.text))

            # 2) 入口词/同义词，如 "Factors, Transcription"
            for term in elem.iter("Term"):
                s = term.find("String")
                if s is not None and s.text:
                    terms.add(clean(s.text))

            elem.clear()
            if root is not None:
                root.clear()             # 关键：把根节点下已处理的空壳也丢掉
    finally:
        handle.close()

    # 过滤：非空、不太长、含拉丁字母
    terms = {t for t in terms
             if t and len(t) <= 80 and re.search(r"[a-z]", t)}

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for t in sorted(terms):
            f.write(t + "\n")

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"解析 {records} 条描述符，输出 {len(terms)} 个术语 → {out_path}"
          f"（{size_mb:.1f} MB）")
    return len(terms)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "data/desc2025.xml"
    dst = sys.argv[2] if len(sys.argv) > 2 else "data/vocab_mesh.txt"
    if not os.path.exists(src):
        raise SystemExit(
            f"找不到输入文件：{src}\n"
            "请从 https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2025.xml 下载。"
        )
    build(src, dst)
