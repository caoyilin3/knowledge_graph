# knowledge-graph

给个人知识库提供底层的**「知识新陈代谢」**能力：内容按类型自动过期、
被纠正的错误自动下架、每一次清理都可审计。零依赖，默认解耦。

## 核心机制：四类内容，四套生命周期

| 类别 | 代码类型 | 保留规则 | 到期 / 事件动作 |
| --- | --- | --- | --- |
| 📰 NEWS 新闻 | `news` | 7 天 | 物理删除 |
| 🔬 KNOWLEDGE 知识 | `science` | 永久 | 无 |
| ⚠️ MISINFORMATION 错误信息 | `fake_science` | 直到被纠正 | 纠正后自动**下架**，保留可追溯 |
| ✅ CORRECTION 纠错 | `supplement` | 永久 | 建立纠错链，触发错误信息下架 |

> **为什么错误信息是「下架」而不是「删除」**：删掉被纠正的条目，等于删掉
> 「它曾经错过、被谁改的」这条证据，纠错链会当场断掉。默认保留；确需物理清除时用
> `purge()` 或 `delete(force=True)` 显式执行，并留审计。取舍见
> [docs/DESIGN.md](docs/DESIGN.md)。

## 30 秒

```python
from knowledge_graph import Engine, JsonStore

engine = Engine(JsonStore("data/kb.json"))

engine.add("news-001", "OpenAI 发布 GPT-5", "……", "news")
engine.add("fake-001", "喝柠檬水能治癌", "……", "fake_science")
engine.add("sup-001", "辟谣：柠檬水不能治癌", "……", "supplement", corrects="fake-001")

engine.get("fake-001").status        # <Status.DEPRECATED: 'deprecated'>，条目仍在
engine.list_active()                 # 只返回 active
engine.correction_chain("fake-001")  # {'item': ..., 'corrects': [], 'corrected_by': [sup-001]}

engine.cleanup()                     # 过期新闻被删除；纠错链原样保留
```

## 生态与路线

本包是 **knowledge-graph-lite** 的内核：零依赖、完全解耦，只做生命周期与纠错链。

| 发行版 | 面向 | 状态 |
| --- | --- | --- |
| knowledge-graph-lite | 个人：开箱即用的命令行工具 | 进行中 |

**企业版不在本仓库的路线内。** 未来如果做，会另起内核与技术栈——多租户、鉴权、
并发写、分布式存储是另一个量级的问题，不复用本包。这个取舍是有意的：内核不背
企业包袱，才能保持简单、可审计、可替换。

内核与 lite 目前放在同一个仓库，方便同步改动；`kg_lite` 只依赖内核公开 API，
需要时可按目录拆成独立发行版。

- **Phase 1（已完成）**：数据模型、策略引擎（保留规则 × 清理动作 × 优先级）、
  纠错链、审计日志、JSON 持久化、133 个测试。
- **Phase 2（进行中）**：lite —— CLI、默认数据路径、导入导出已完成；剩打包与发布。
- **Phase 3（可选，个人向）**：全文搜索、标签体系、备份 / 同步。

> **命名待定**：代码里的 `science` / `fake_science` / `supplement` 与简介里的
> KNOWLEDGE / MISINFORMATION / CORRECTION 尚未统一。lite 发布前必须定下来——
> 包名和命令名一旦发出去就很难改；届时会带旧名别名，保证旧数据继续可用。

## 它不是什么

- **不是图数据库**：没有属性图模型、没有图算法、没有 SPARQL / Cypher。名字里的
  graph 指条目之间由纠错关系构成的那条链。
- **不是抽取或可信度评估系统**：从文本里抽实体、给文章打分是上游
  AutoNewsAnalyzer 的职责，本仓库不包含这些能力。
- **不自带调度**：`cleanup()` 由调用方触发（cron、请求、脚本都行）。库不会替你
  起后台线程——那会把生命周期语义藏进看不见的地方。
- **不是企业服务端**：没有多租户、鉴权、并发写和水平扩展；这些不在本包范围内。

## 安装

Python 3.10+，运行时零第三方依赖。**不安装也能直接用**（仓库根目录即包目录）：

```bash
python -m knowledge_graph demo
```

如需安装成命令（可编辑安装需要 pip >= 23、setuptools >= 68）：

```bash
pip install -e .
kg --help
knowledge-graph demo
```

## 默认策略

| 类型 | 保留规则 | 到期动作 | 可被取代 |
| --- | --- | --- | --- |
| `news` | 7 天 | `delete` | 否 |
| `science` | 永久 | `none` | 否 |
| `fake_science` | 直到被取代 | `deprecate` | **是** |
| `supplement` | 永久 | `none` | 否 |
| 未登记类型 | 永久 | `none` | 否 |

四类保留规则：`fixed_days` / `permanent` / `until_superseded`；
四种动作：`delete` / `archive` / `deprecate` / `none`。

改策略不需要动引擎：

```python
from knowledge_graph import (
    Action, Engine, PolicyBook, Retention, RetentionKind, RetentionPolicy, default_book,
)

book = default_book()
book.register(
    RetentionPolicy(
        category="news",
        retention=Retention(RetentionKind.FIXED_DAYS, days=30),
        action=Action.ARCHIVE,
        priority=10,
    )
)
engine = Engine(JsonStore("data/kb.json"), policies=book)
```

多条知识命中多个类别时，`PolicyBook.resolve()` 取 `priority` 最大者；同优先级按
类别名排序，结果稳定可复现。

## 状态机

```
                ┌──────────── 到期（时间驱动）────────────┐
                │                                        ▼
  active ───────┼──────────── 被取代（事件驱动）──► deprecated
                │                                        │
                └────────────► archived / expired ◄──────┘
                                    │
                              purge()/delete() 才物理删除
```

- `active` 是唯一出现在默认视图里的状态；
- `deprecated` 默认不可见，但 `get()`、`list_by_type(include_hidden=True)`、
  `search(include_hidden=True)` 都能查到；
- 物理删除只有两个入口：显式 `delete()` 与批量 `purge()`，都会写审计。

## 纠错链

```python
engine.add("sup-002", "再辟谣", "……", "supplement")   # 先建条目
engine.supersede("fake-001", "sup-002")                 # 再建立取代关系
engine.corrections()                                    # 所有取代关系的健康检查
engine.integrity()                                      # 悬空引用检查（应为空）
engine.restore("fake-001")                              # 撤销取代，恢复 active
```

约束是显式的：

- 只有策略里 `can_be_superseded=True` 的类型才能被取代（默认只有 `fake_science`）；
- 已被取代的条目再被取代会报 `ConflictError`，除非 `force=True`；
- 删除纠错链上的条目默认被拒绝，`force=True` 会拆链并记审计。

## lite：个人命令行

```bash
kg init                                      # 建库：~/.knowledge-graph/kb.json
kg add --type news --title "OpenAI 发布 GPT-5" --content "……"
kg add --type fake_science --title "喝柠檬水能治癌" --id fake-001
kg add --type supplement --title "辟谣：柠檬水不能治癌" --corrects fake-001

kg list                                      # 默认只看 active
kg list --all                                # 连已下架/归档/过期一起看
kg show fake-001                             # 详情 + 纠错链
kg search 柠檬
kg search "量子 计算"                        # 多词默认全部命中，按相关度排序
kg search "量子 计算" --any                  # 命中任一即可
kg list --tag AI --tag 模型                  # 标签筛选（默认 AND，--any-tag 改为 OR）
kg tags                                      # 标签与使用次数
kg update fake-001 --status deprecated
kg cleanup --dry-run                         # 先预演
kg stats
kg backup                                    # 手动备份
```

数据位置优先级：`--data` > 环境变量 `KG_DATA` > `~/.knowledge-graph/kb.json`。

备份与迁移：

```bash
kg export backup.json                        # 导出全部条目
kg import backup.json --restore              # 忠实还原（状态/过期时间/纠错指针）
kg import seed.jsonl                         # 默认走策略重建，自动补算过期时间
```

`import` 默认按当前策略重建，适合手写 JSONL 批量入库；`--restore` 保留原状态，
适合备份回滚。`purge` 不可逆，必须加 `--yes`（或先 `--dry-run`）。

破坏性操作（`delete` / `cleanup` / `purge` / `import`）执行前会自动备份到
`<数据目录>/backups/`，默认保留 10 份；`--no-backup` 可关闭。

## 内核自带的调试命令

```bash
python -m knowledge_graph demo                 # 临时目录跑完整流程
python -m knowledge_graph inspect data/demo.json
python -m knowledge_graph cleanup data/demo.json --dry-run
```

## 质量

- `python -m unittest discover -s tests`：168 个用例，覆盖模型 / 策略 / 存储 /
  审计 / 引擎 / 纠错链 / 清理 / lite CLI；
- `python bench/eval.py`：38 条合成场景的一致性评测，结果写入
  [docs/EVALUATION.md](docs/EVALUATION.md)。

**评测的边界**：那 38 条场景的期望值是按策略表推导的，衡量的是「引擎是否按
写下的规则办事」，**不是**真实新闻流上的准确率。仓库不声称拥有标注语料；
若要对外报告效果，必须另做人工标注评测。

## 与上游的关系

概念上的来源是 AutoNewsAnalyzer 的生命周期管理设计稿；实现是重新写的。详见
[docs/PROVENANCE.md](docs/PROVENANCE.md)。一句话：**上游论文中的量化结论
（F1、准确率、清理比例等）在本仓库中没有被复现，也不应被引用为本仓库的结果。**

## 已知局限

- 查询、搜索与清理都是全量扫描，O(n)；条目量到十万级需要加索引（可替换 `BaseStore`）；
- 单进程写；没有文件锁，多进程并发写同一个 JSON 不安全；
- 自动备份与数据文件在同一目录，整盘损坏时会一起丢；重要数据请再复制到别处；
- 没有快照 / 版本历史，只有审计日志；
- 时间统一按 UTC 存储，本地时区展示由调用方负责。

安全边界、已做的防护和仍需调用方处理的限制，见 [SECURITY.md](SECURITY.md)。

## 开发

```bash
python -m unittest discover -s tests -v
python bench/eval.py
```

目录：

```
knowledge_graph/        内核（models / policy / stores / audit / engine）
kg_lite/                个人版 CLI（只依赖内核公开 API）
tests/                  标准库 unittest
bench/                  合成场景 + 评测脚本
scripts/                v1 pickle → v2 JSON 迁移
docs/                   设计说明、安全边界、来源说明、评测报告、归档
data/                   运行数据（仅 demo.json 入库）
```

## 参与贡献

- 开发与测试约定：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全边界与漏洞报告：[SECURITY.md](SECURITY.md)
- 许可证：[Apache License 2.0](LICENSE)

## 许可证

Apache License 2.0。内核与 lite 都允许商用、修改、再分发；分发时保留版权声明与 [NOTICE](NOTICE)，修改过的文件需注明改动；并包含明确的专利授权条款。
