# 变更记录

## 2.0.0 — 全面整改

### 修复

- **overwrite 会拆断纠错链**：覆盖一个仍在链上的条目会让反向指针悬空；现在直接
  拒绝，要求先 `restore()` / `delete(force=True)`。
- **metadata 不可序列化时在落盘才炸**：现在在构造时校验（键必须是字符串、值必须
  可 JSON 序列化），存储层同时把 `TypeError` 包成 `StoreError`，失败不留半文件。
- **schema_version 非法时抛裸 `ValueError`**：现在统一为 `StoreError`。
- **审计记录可被调用方改写**：`get_history()` / `entries` 改为返回深拷贝。
- **审计保留字段可被附加字段覆盖**：现在忽略冲突字段并告警。
- **CLI 遇到缺失 / 损坏文件直接抛 traceback**：现在打印一行错误并返回 1。
- **策略删除条目时可能留下悬空 `corrects`**：删除前清理入向指针。
- **已被取代的条目还能改类型**：现在拒绝，必须先 `restore()`。
- **`cleanup()` 的「删取代」分支永远不可达**：被取代的条目改走 `deprecate` 保留，
  纠错链完整可查（这是 v1 最严重的问题）。
- **包无法导入**：`__init__.py` 使用 `from core import ...` 绝对导入，从仓库根目录
  `import knowledge_graph` 直接 `ModuleNotFoundError`；现在全部改为相对导入，
  并补齐 `pyproject.toml`。
- **忘了 flush 就丢数据**：`save/delete` 只改内存；现在 `JsonStore` 默认
  `auto_flush=True`，原子落盘。
- **损坏文件被静默清空**：原 `_load()` 捕获所有异常后置空；现在抛 `StoreError`。
- **查询拼错字段静默返回空**：现在抛 `ValidationError`。
- **同 id 静默覆盖**：现在抛 `ConflictError`，需显式 `overwrite=True`。

### 新增

- **lite 个人向功能**：多词搜索（默认 AND、`--any` 取 OR、按标题/标签/正文相关度排序）、
  标签筛选（`list --tag`，支持 AND/OR）与 `tags` 统计、破坏性操作前自动备份并滚动保留、
  手动 `backup` 命令。
- **lite 命令行**（`kg_lite/`，命令 `kg`）：init / add / list / show / search /
  update / delete / cleanup / purge / stats / export / import；数据默认落在
  `~/.knowledge-graph/kb.json`，支持 `--data`、`KG_DATA`、`--json`；
  `import --restore` 忠实还原备份，默认导入按策略重建。
- 保留策略四元组：`Retention`（fixed_days / permanent / until_superseded）+
  `Action`（delete / archive / deprecate / none）+ `Priority` + `can_be_superseded`。
- 纠错链：`superseded_by` / `corrects` 双向指针，`correction_chain()`、
  `corrections()`、`integrity()`、`restore()`，删除链上条目需 `force=True`。
- `cleanup(dry_run=True)` 预演、`purge()` 批量清除（会清理悬空 `corrects`）。
- `AuditLog` 支持 JSONL 旁路落盘；动作覆盖 create/update/delete/expire/archive/
  deprecate/supersede/unsupersede/purge。
- 迁移脚本 `scripts/migrate_v1_pickle.py`，会补齐 v1 缺失的反向指针。
- 168 个 unittest、38 条合成场景评测（`bench/eval.py`）。

### 变更

- 存储：`PickleStore` → `JsonStore`（UTF-8、schema 版本、原子写）+ `MemoryStore`。
- 包结构：`core/` + `stores/` → 扁平模块 `models / policy / stores / audit / engine`。
- 时间：naive `datetime.now()` → UTC aware，序列化为带偏移的 ISO 8601。
- 状态：`superseded` → `deprecated`（读取旧数据时自动映射）。
- 入口：`test_engine.py` 脚本 → `python -m knowledge_graph`（demo / inspect / cleanup）。

### 文档

- README 顶部改为产品化简介：四类内容（NEWS / KNOWLEDGE / MISINFORMATION / CORRECTION）、
  生态与路线图，并写明「错误信息下架而非删除」的取舍。
- 新增 `README.md`、`docs/DESIGN.md`（设计取舍）、`docs/PROVENANCE.md`（来源与
  数字边界）、`docs/EVALUATION.md`（由脚本生成）。
- v1 结构说明归档到 `docs/legacy/`；v1 源码保留在首个基线提交（v1.0）。
