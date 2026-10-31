# 设计说明

这份文档记录**为什么是这样**，而不是有什么功能（功能见 README）。

## 1. 为什么把「取代」与「删除」分开

v1 的 `cleanup()` 会把被取代的假科普直接 `store.delete()`。看起来省事，实际上
把项目唯一有价值的东西删了：

- 删掉之后，没人能回答「这条结论原来错在哪、被谁改的、什么时候改的」；
- 审计日志里只剩一条 delete，纠错关系无法重建；
- 下游若引用过这条知识，引用会变成悬空。

现在的语义：

| 事件 | 动作 | 结果 |
| --- | --- | --- |
| 新闻满 7 天 | `delete` | 物理删除（低价值、高体积，审计留痕即可） |
| 假科普被补充科普取代 | `deprecate` | 状态变 `deprecated`，条目保留，链完整 |
| 科研知识永远不到期 | `none` | 一直 `active` |
| 策略配置为归档 | `archive` | 状态变 `archived`，不再出现在默认视图 |

**原则**：删掉的是「低价值噪声」，留下的是「纠正的证据」。

## 2. 状态与可见性

`Status` 只有四个值：`active` / `expired` / `deprecated` / `archived`。

- `active`：唯一默认可见状态；
- `expired`：到期但策略动作是 `none`，保留、隐藏；
- `deprecated`：被取代或手动标记过时，保留、隐藏、可追溯；
- `archived`：归档，保留、隐藏。

「可见」是由 `list_active()` / `search()` 的过滤条件实现的，不是靠删除数据实现。
这样任何隐藏状态都能通过 `include_hidden=True` 或 `get()` 取回。

## 3. 保留策略为什么是四元组

设计稿里是 `Policy(Category, Retention, Action, Priority)`。v1 只实现了
`Retention` 的一半（天数、是否自动过期），动作写死成删除，优先级完全没有。
这导致三个问题：

1. 想归档必须改引擎代码；
2. 新闻和科研知识只能共用一种处理方式；
3. 多标签场景没有仲裁规则。

现在：

- `Retention`：`fixed_days` / `permanent` / `until_superseded`；
- `Action`：`delete` / `archive` / `deprecate` / `none`；
- `Priority`：`PolicyBook.resolve()` 取最大者，同优先级按类别名排序；
- 未登记类型落到 `_default`：永久保留、不清理（宁可留着，也不误删）。

## 4. 为什么用 JSON 而不是 pickle

pickle 的问题是三个叠加：不可读、跨版本脆弱、**能执行任意代码**。知识库要长期
存放外部内容，用 pickle 等于给自己留一个 RCE 入口。

JSON 的代价是：没有类型、体积大、写入要整体重写。对「几万条以内、偶尔清理」的
场景完全够用；再往上应该换 SQLite，而不是换回 pickle。

## 5. 落盘为什么默认同步

v1 的 `save()` 只改内存，`flush()` 才写盘，`test_engine.py` 最后手动 flush 掩盖了
这一点——真实使用里忘一次就丢数据。

现在 `JsonStore(auto_flush=True)` 是默认：每次 `save/delete` 都原子落盘。批量导入
可以 `auto_flush=False`，最后 `flush()`。

原子写入：同目录临时文件 → `fsync` → `os.replace`。中途崩溃只会留下临时文件，
不会留下半个 JSON。

## 6. 失败必须响

v1 的 `_load()` 捕获所有异常后 `self._data = {}`，等于「文件坏了就当空库」。
现在的规则：

- JSON 损坏 → `StoreError`，带行列号；
- 单条反序列化失败 → `StoreError`，带条目 id；
- 文件版本高于程序支持 → `StoreError`；
- 未知查询字段 → `ValidationError`（而不是静默返回空列表）。

代价是启动可能失败。这是故意的：数据损坏应该在启动时暴露，而不是在写入时
把好数据覆盖掉。

## 7. 幂等与可预演

`cleanup()` 只处理 `active` 条目，处理完状态就变了，所以第二次调用是空操作。
`dry_run=True` 返回同样的分桶结果但不落盘，可以先看再执行。

这两点让清理可以安全地挂在定时任务上。

## 8. 依赖注入与测试

`Engine(store, policies=..., audit=...)` 三个依赖都可替换：

- `MemoryStore` 用于测试；
- `JsonStore` 用于持久化；
- `AuditLog(sink=...)` 可选 JSONL 落盘；
- 时间通过 `created_at=` 与 `cleanup(now=)` 注入，测试不依赖本机时钟。

## 9. 刻意没做的事

- **没有后台调度器**：库不应该偷偷起线程；调度交给调用方。
- **没有图算法**：不做最短路径、社区发现；那不是本项目的边界。
- **没有版本快照**：只有审计日志，回滚需要自己实现。
- **没有并发控制**：单进程写，多进程写同一文件需要外层加锁。

## 10. 扩展点

| 想做什么 | 改哪里 |
| --- | --- |
| 换存储（SQLite / Redis） | 实现 `BaseStore` 七个方法 |
| 改某类策略 | `PolicyBook.register()` |
| 多标签仲裁 | 调 `PolicyBook.resolve([...])` 后写入 `content_type` |
| 审计入 ELK | `AuditLog(sink=...)` 或继承后重写 `_append_to_sink` |
| 自定义清理节奏 | 调用方定时执行 `cleanup()` |
