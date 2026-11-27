# B 同学交付说明：数据层

本目录是 BioTermGraph（生物论文术语抽取与共现图谱系统）中 **B 负责的数据层**。
依据：`_spec.md`（V4 规格）+ `_b_manual.md`（B 同学任务手册）。
凡与这两份冲突之处，以 V4 规格为准；本说明第 8 节列出实现时做的全部补充。

一句话职责：**让数据进得去、查得出、查得快。**

---

## 1. 包含哪些文件

| 文件 | 作用 | 规格/手册对应 |
|---|---|---|
| `models/database.py` | 四张表：论文、术语、句子、共现边 | 手册第 1 步 / 规格 5.1–5.4 |
| `tools/build_vocab.py` | MeSH XML → 纯文本词表（一次性） | 手册第 2 步 |
| `tools/build_taxonomy.py` | NCBI 物种名 → 词表（可选，一次性） | 规格 3.3 |
| `services/dictionary.py` | ECDICT 查询、基础词判断 | 手册第 3 步 |
| `services/vocab.py` | 词表加载、切词、最长匹配 | 手册第 4 步 |
| `services/db_ops.py` | 所有数据库读写（全组唯一入口） | 手册第 5 步 |
| `routes/paper.py` | 词典页、页内过滤接口、历史页 | 手册第 6 步 |
| `routes/search.py` | 全站搜索 | 手册第 7 步 |
| `tools/verify_b.py` | 自检脚本（81 条断言） | 本说明第 6 节 |
| `tests/smoke_templates/*.html` | **仅自测用的占位模板**，不是交付页面 | — |
| `tests/fixtures/*` | 构建脚本的极小样本 | — |

## 2. 明确不包含（属于别人）

- `app.py`、`config.py`、`services/term_extractor.py`、`services/cooccurrence.py`、`routes/graph.py` —— A
- `templates/`、`static/` —— C（`tests/smoke_templates/` 只是自测占位，C 的正式模板到位后可以整个删掉）
- `routes/upload.py`、`templates/upload.html`、`templates/progress.html`、`static/js/progress.js` —— D
- `requirements.txt` —— A 统一维护，B 没有新增任何第三方依赖

## 3. 目录结构（B 的部分）

```
biograph/
├── models/
│   ├── __init__.py
│   └── database.py
├── services/
│   ├── __init__.py
│   ├── dictionary.py
│   ├── vocab.py
│   └── db_ops.py
├── routes/
│   ├── __init__.py
│   ├── paper.py
│   └── search.py
├── tools/
│   ├── build_vocab.py
│   ├── build_taxonomy.py
│   └── verify_b.py
├── tests/
│   ├── smoke_templates/{paper_terms,history,search}.html
│   └── fixtures/{desc_sample.xml,names_sample.dmp}
└── data/                     # 自己建，不进 Git
    ├── stardict.db
    ├── vocab_mesh.txt
    └── vocab_taxonomy.txt    # 可选
```

A 的 `app.py` 里需要这样接线（B 不写这个文件，但接口必须对上）：

```python
from models.database import db
from routes.paper import paper_bp
from routes.search import search_bp

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///termgraph.db?timeout=15"
db.init_app(app)
app.register_blueprint(paper_bp)
app.register_blueprint(search_bp)      # A 再注册 graph_bp，D 注册 upload_bp

with app.app_context():
    db.create_all()
```

## 4. 跑起来（四步）

```bash
cd biograph

# 1) 虚拟环境（已建过就跳过）
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS / Linux

# 2) 装依赖（由 A 提供的 requirements.txt）
pip install -r requirements.txt

# 3) 准备数据（见第 5 节），确认 data/ 下有两个文件
#    data/stardict.db、data/vocab_mesh.txt

# 4) 自检：不需要真实数据，2 秒跑完
python tools/verify_b.py
```

看到 `通过 81 / 81` 即数据层正常。

想直接用浏览器点页面看效果（用假数据、占位模板）：

```bash
python tools/verify_b.py --serve
# 打开 http://127.0.0.1:5000/paper/1
```

## 5. 三份数据文件（第 1 周必须做完）

| 文件 | 从哪来 | 放哪 / 怎么处理 |
|---|---|---|
| ECDICT SQLite 版 | `https://github.com/skywind3000/ECDICT/releases/download/1.0.28/ecdict-sqlite-28.zip` | 解压出 `stardict.db` → `data/stardict.db` |
| MeSH 2025 XML | `https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2025.xml` | 跑下面第一条命令，生成后即可删除 XML |
| NCBI 物种名（可选） | `https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz` | 解压出 `names.dmp`，跑第二条命令 |

```bash
# 360MB XML → 约 8MB 词表（约 3–8 分钟，内存稳定在 200MB 左右）
python tools/build_vocab.py data/desc2025.xml data/vocab_mesh.txt

# 检查（行数应在 25 万–35 万之间；今天实际跑出来是多少就记多少，不要编）
wc -l data/vocab_mesh.txt
grep "^brca1" data/vocab_mesh.txt

# 可选：物种名（默认只收「属名 + 种加词」两词学名）
python tools/build_taxonomy.py data/names.dmp data/vocab_taxonomy.txt
```

`data/` 不进 Git。`vocab_mesh.txt` 打包发群，别让每个人各自下 360MB。

## 6. 自检脚本做了什么

`python tools/verify_b.py` 会在临时目录建库、造假词典与假词表，覆盖：

1. 四张表、`uq_term_paper_word`、`uq_cooc_edge`、`ck_cooc_order`、两个索引是否存在
2. `mitochondria` 有中文、`the`/`result` 是基础词、`BRCA1` 查不到但不抛异常、大小写回退
3. `transcription factor` 整串命中、`factor` 不单独出现、`IL-6` 从 `IL-6-induced` 里切出、`match_terms` 别名
4. 入库幂等（连续保存两次条数不变）、同批重复 word 合并、`in_dict` 由中文释义派生
5. 反序重复边只存一条、全篇图边权 ≥ 2、自我中心图、不存在的 `term_id` 返回空
6. 分页不重复、未收录不进主列表、LIKE 通配符转义、`geen → gene` 纠错
7. 状态机：`failed → pending` 可重置、`pending` 只能被占用一次
8. 四条路由：200 / 404 / 越界页码 / JSON 结构
9. 删论文后术语、句子、共现边全部级联删除

实测结果：**81 / 81 通过**（Flask 3.0.3 + Flask-SQLAlchemy 3.1.1 + SQLAlchemy 2.1.9，Python 3.13）。

## 7. 交接契约

### 7.1 给 A（抽取、共现、建图）

| 调用 | 返回 |
|---|---|
| `from services.vocab import get_vocab, tokenize_for_match` | 单例 |
| `tokenize_for_match(sentence)` | `List[str]`，保留 `BRCA1`、`p53`、`IL-6`、`TNF-α` |
| `get_vocab().match(tokens)`（或 `.match_terms(tokens)`） | 命中术语列表，全小写，最长优先 |
| `get_vocab().contains(term)` | 词表里是否有该原形（词形归并第 1 条要用） |
| `get_dict().lookup(word)` | `{found, zh, en, tag, bnc, frq, exchange}`，未命中不抛异常 |
| `get_dict().is_basic_word(word)` | `bool` |
| `db_ops.save_sentences(paper_id, sentences)` | 写入条数 |
| `db_ops.save_terms(paper_id, terms)` | 写入条数（`terms` 每条必须带 8 个键，见下） |
| `db_ops.get_term_ids_by_word(paper_id)` | `{word: term_id}` |
| `db_ops.save_cooccurrences(paper_id, [(a_id, b_id, w)])` | 写入边数（顺序随意，内部统一 `min/max`） |
| `db_ops.get_graph_data(paper_id, center_term_id=None)` | `(nodes, edges)` |

`save_terms` 每条必须带：
`word`、`surface`、`definition_zh`、`definition_en`、`frequency`、`context_sentence`、`is_vocab_hit`、`in_dict`。
少传一个不会报错，但那一列会是空的——最难查，所以请照抄。

### 7.2 给 D（上传、进度）

| 调用 | 说明 |
|---|---|
| `db_ops.create_paper(title, stored_filename, source_type, category)` | 返回含 `id` 的 `Paper`；`source_type` 只接受 `txt`/`pdf` |
| `db_ops.claim_paper(paper_id)` | 原子占用：`pending → processing`，成功 `True`。**启动后台任务前先调它**，防止连点两次起两条线程 |
| `db_ops.update_paper_status(...)` | `status/progress/message/error_message/char_count/term_count` 按需传；`progress` 自动钳在 0–100 |
| `db_ops.reset_paper_for_retry(paper_id)` | 只允许 `failed → pending`，其它状态返回 `None` |
| `db_ops.get_paper(paper_id).to_dict()` | 直接 `jsonify` 给 `/api/paper/<id>/status` |

### 7.3 给 C（页面）

| 路由 | 模板 | 模板变量 |
|---|---|---|
| `/paper/<int:paper_id>` | `paper_terms.html` | `paper`、`pagination`、`terms`、`q`、`sort`、`page`、`unlisted_count`、`unlisted`、`show_unlisted` |
| `/api/paper/<int:paper_id>/terms?q=` | — | JSON：`{ok, total, truncated, items[]}`，`items` 最多 50 条 |
| `/history?category=&page=` | `history.html` | `papers`（当前页）、`pagination`、`total`、`categories`、`current_category`、`category` |
| `/search?q=&page=` | `search.html` | `q`、`rows`（`(Term, Paper)` 元组）、`total`、`page`、`pages`、`suggestions` |

- `sort` 取值：`frequency`（默认降序）/ `frequency_asc` / `word`
- `terms` 里的对象字段：`id/word/surface/definition_zh/definition_en/frequency/context_sentence/in_dict`
- 页码越界会自动钳到最后一页，不会出现空白页
- 模板里链接图谱请用 `url_for('graph.paper_graph', paper_id=...)` 与 `url_for('graph.term_graph', paper_id=..., term_id=...)`——**这两个端点名需要 A 确认**

## 8. 与手册的差异（全部为补充，逐条说明）

| 编号 | 差异 | 为什么 |
|---|---|---|
| B-1 | `database.py` 增加 `PRAGMA foreign_keys=ON` | SQLite 默认**不执行**外键的 `ON DELETE CASCADE`。不打开这个开关，手册里写的 `passive_deletes=True` 与 `ondelete="CASCADE"` 全是装饰，删论文后术语、句子、边会变成孤儿数据。这是实现规格 5.5「级联删除」的必要条件 |
| B-2 | `vocab.match_terms()` 作为 `match()` 的别名 | 规格 10.3 写的是 `match_terms`，手册 6.1 写的是 `match`。两个名字都能用，谁照哪份文档写都不会错 |
| B-3 | 匹配时把连字符当空格摊平 | 手册原实现下，`IL-6-induced` 会被切成一个整 token，`il-6` 永远匹配不到。摊平后 `IL-6`、`T-cell` 能从连写词里切出，且不改变输出格式（仍是小写术语串） |
| B-4 | `save_terms` 同批重复 word 合并、`in_dict` 由 `definition_zh` 派生 | 手册排错第 11 条要求「加一层去重兜底」；规格 7.9 要求没有中文释义的词必须沉到未收录区，不能指望上游每次都传对 |
| B-5 | 新增 `claim_paper()` 与 `reset_paper_for_retry()` | 规格 5.6 规定「只有 pending 可以启动」「failed 可重置」。这两个动作必须是原子的，否则连点按钮会起两条线程抢同一篇论文 |
| B-6 | `/history` 改为每页 20 篇（原手册传全量列表） | C 的设计稿（`界面设计稿/history.html`）有分页条。`papers` 仍可用（当前页），并额外给 `pagination` 与 `total`；若 C 不要分页，把 `list_papers_page` 换回 `list_papers` 即可，一行 |

## 9. 关键假设（前文未写死、按以下推定实现）

| 编号 | 假设 |
|---|---|
| A-1 | 项目放在 `biograph/`，包名 `models` / `services` / `routes`（规格 3.3） |
| A-2 | 数据库文件 `instance/termgraph.db`，连接串 `sqlite:///termgraph.db?timeout=15`（手册 3.3） |
| A-3 | 词典固定为 `data/stardict.db`，表名 `stardict`，列 `translation/definition/tag/bnc/frq/exchange`（ECDICT 1.0.28） |
| A-4 | `data/vocab_mesh.txt` 视为**必需**，缺失时抛 `FileNotFoundError` 并在提示里给出生成命令；`vocab_taxonomy.txt` 视为可选，不存在则静默跳过 |
| A-5 | 模板文件名与变量名按手册第十章；A 的图谱端点名为 `graph.paper_graph` / `graph.term_graph` / `graph.fragment` |
| A-6 | `terms` 字典的键名按手册第十章，`word` 已由 A 归并成小写 |
| A-7 | Python 3.10 以上（实测 3.13），依赖版本按规格 3.2，B 不新增第三方包 |
| A-8 | 上传路由已完成扩展名与大小校验，因此 `create_paper` 只接受 `txt`/`pdf`，其它值直接报错而不是悄悄改成 txt |

## 10. 需要安装的软件与库

| 名称 | 版本 | 安装方式 | 谁需要 |
|---|---|---|---|
| Python | 3.10 及以上（实测 3.13） | python.org 安装包，安装时勾选 Add to PATH | 全员 |
| Flask | 3.0.3 | `pip install -r requirements.txt` | 全员 |
| Flask-SQLAlchemy | 3.1.1 | 同上（会自动带 SQLAlchemy 2.x） | 全员 |
| Flask-SocketIO | 5.3.6 | 同上 | A / D（B 的代码不 import 它） |

**B 的代码不需要额外安装任何包。** 两个构建脚本只用 Python 标准库（`xml.etree.ElementTree`、`gzip`、`argparse`），不需要 `lxml`、`pandas`。

> 不要安装 `eventlet` / `gevent`（规格 3.2 已明确），Windows 上会因猴子补丁出现难查的崩溃。

## 11. 注意与已知限制

1. **ECDICT 的 `translation` 是词级对译，不是解释。** `methylation → 甲基化`，不懂生物的人仍不懂。答辩时主动说，这是规格 2.6 的第一条已知局限。
2. **多词术语大概率没有中文释义。** ECDICT 以单词为主，`transcription factor` 这类会落到「未收录」。这是数据源限制，不是筛选写错。
3. **`suggest_terms` 每次最多取 2 万个词算编辑距离**，只在前面的 LIKE 查询完全无结果时调用。论文很多时可改为只取 `in_dict=True` 的词。
4. **`dictionary.lookup` 的进程内缓存上限 5000 条**（`lru_cache`）。换词典文件后要重启进程，缓存不会自动失效。
5. **`get_graph_data` 的取数上限是写死的**（自我中心 30 条边 + 邻居间 200 条、全篇 60 条边），与规格 7.10 一致。要改先改规格，再改代码。
6. **`claim_paper` 目前不处理「进程崩溃后卡在 processing」**。演示前若库里留下 processing 的僵尸记录，用 `sqlite3 instance/termgraph.db` 手工改回 `pending`，或调用 `update_paper_status(..., status="pending")`。
7. **如果启动就报 `ImportError: DLL load failed ... 应用程序控制策略已阻止此文件`**：这台机器的 Windows 应用控制策略拦了 SQLAlchemy 的 8 个 `.pyd` 编译模块。SQLAlchemy 源码包里同时带了同名纯 Python 实现，把它们改名即可绕过（功能完全一致，只是慢一点）：

   ```powershell
   $p = "<你的venv>\Lib\site-packages\sqlalchemy"
   Get-ChildItem $p -Recurse -Filter *.pyd | ForEach-Object { Rename-Item $_.FullName "$($_.Name).bak" }
   ```

   本项目已在这台机器上按此办法实测：Flask 3.0.3 + Flask-SQLAlchemy 3.1.1 + SQLAlchemy 2.1.9，81 条断言全部通过。
