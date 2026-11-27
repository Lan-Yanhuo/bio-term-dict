# tools/build_taxonomy.py
"""（可选）把 NCBI 物种名 names.dmp 转成一行一个名称的纯文本词表。

用法：
    tar -xzf taxdump.tar.gz names.dmp
    python tools/build_taxonomy.py data/names.dmp data/vocab_taxonomy.txt
    python tools/build_taxonomy.py data/names.dmp data/vocab_taxonomy.txt --all-names

为什么不默认全收：
    names.dmp 里除了学名（scientific name）还有大量俗名、菌株编号、缩写，
    全收进来会让 "human"、"mouse" 这种到处出现的词变成专业术语，
    反而污染术语表。默认只收「属名 + 种加词」两词的学名（如 Homo sapiens）。

这个文件是可选增强：删掉 data/vocab_taxonomy.txt，程序照常启动，
只是少认一些物种名。若生成出来超过 50MB，建议直接不要用——
加载慢、占内存，收益不如 MeSH 主词表。
"""

import argparse
import os
import re
import sys

# 只允许字母、空格、连字符、点：把 "Escherichia coli str. K-12" 这类带编号的名字挡在外面
_SAFE_RE = re.compile(r"^[A-Za-z][A-Za-z .\-]*$")
_WS_RE = re.compile(r"\s+")


def parse_args(argv):
    p = argparse.ArgumentParser(description="NCBI names.dmp → 物种名词表")
    p.add_argument("src", nargs="?", default="data/names.dmp")
    p.add_argument("dst", nargs="?", default="data/vocab_taxonomy.txt")
    p.add_argument("--classes", default="scientific name",
                   help="要保留的 name class，逗号分隔；默认只收学名")
    p.add_argument("--all-names", action="store_true",
                   help="收 --classes 指定的全部名称，而不是只收两词的学名")
    return p.parse_args(argv)


def build(src: str, dst: str, classes=("scientific name",), binomial_only=True) -> int:
    wanted = {c.strip().lower() for c in classes if c.strip()}
    names = set()
    total = 0

    with open(src, encoding="utf-8", errors="replace") as f:
        for line in f:
            total += 1
            parts = line.split("|")
            if len(parts) < 4:
                continue
            name = _WS_RE.sub(" ", parts[1].strip())
            name_class = parts[3].strip().lower()
            if name_class not in wanted or not name:
                continue
            if len(name) > 80 or not _SAFE_RE.match(name):
                continue
            if binomial_only and len(name.split(" ")) != 2:
                continue
            names.add(name.lower())

    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        for n in sorted(names):
            f.write(n + "\n")

    size_mb = os.path.getsize(dst) / 1024 / 1024
    print(f"读取 {total} 行，写出 {len(names)} 个名称 → {dst}（{size_mb:.1f} MB）")
    if size_mb > 50:
        print("提示：文件偏大，建议删掉不用，或加 --binomial-only（默认已开）。")
    return len(names)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    if not os.path.exists(args.src):
        raise SystemExit(
            f"找不到输入文件：{args.src}\n"
            "请从 https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz 下载并解压出 names.dmp。"
        )
    build(args.src, args.dst,
          classes=tuple(args.classes.split(",")),
          binomial_only=not args.all_names)
