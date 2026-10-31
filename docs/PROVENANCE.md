# 来源与边界说明

## 谱系

本仓库是 **AutoNewsAnalyzer**（上游原型仓库）中「知识生命周期管理」
这条线的独立重写，不是从那个仓库里抽取的模块。

需要说清楚的是：上游仓库里的 `src/core/lifecycle.py` 是**进程生命周期**
（启动/关闭、健康检查、信号处理），不是知识生命周期；论文里描述的
「分类策略驱动」在代码里对应 `src/memory/`，而其中的
`RetentionRule.evaluate()` 是一个恒返回 `False` 的占位实现。上游实际执行清理的
是「容量阈值 + 重要性/遗忘算法」，不是按类别匹配策略。

所以本仓库的历史位置是：**第一次把「类别 → 保留策略 → 清理动作」写成可执行代码。**

## 继承了什么

- 概念：按内容类别匹配差异化保留策略；
- 四元组形式：`Policy(Category, Retention, Action, Priority)`；
- 三种清理动作（删除 / 归档 / 标记过时）的想法；
- 审计留痕的要求。

## 没有继承什么

- 上游的抽取、可信度评估、进化学习、存储后端等模块，一概不在本仓库；
- **上游三篇设计稿中的所有量化结论**（F1 86.2 / 81.5、准确率 88.5%、
  拦截率 89.6%、三个月清理 40%、时效性 +60% 等）。

## 关于那些数字

那些数字写在上游的《功能论文》《项目报告》《答辩 PPT》里，属于已提交的原型
阶段材料。本仓库：

1. 没有复现它们；
2. 没有任何代码或数据能产生它们；
3. **不引用、不转述、不把它们当作本仓库的评测结果。**

本仓库自己的评测只有 `bench/eval.py` 的 38 条**合成场景**，衡量规则实现一致性，
并在 [EVALUATION.md](EVALUATION.md) 里明确写了它不是真实语料准确率。

## 新增了什么

- **纠错链**：`superseded_by` / `corrects` 双向指针，`correction_chain()`、
  `corrections()`、`integrity()`、`restore()`；被取代的条目保留而不是删除。
  这是上游任何一篇设计稿里都没有的东西。
- 四种清理动作与优先级仲裁；
- JSON 原子持久化、自动落盘、损坏即报错；
- UTC 时间语义与旧数据迁移；
- 168 个 unittest 与 38 条场景评测。

## 旧文件去哪了

| 文件 | 现在的位置 | 说明 |
| --- | --- | --- |
| `knowledge_graph/core/*`、`stores/pickle_store.py`、`test_engine.py` | git 历史（v1.0 基线提交） | v1 实现在基线提交里，可随时对照 |
| `knowledge_graph/dcs/knowledge_graph项目结构.txt` | [docs/legacy/](legacy/) | 已归档，内容描述的是 v1 结构 |
| `knowledge_graph/data/test.pkl` | `data/legacy/v1-test.pkl`（不入库） | 已迁移为 `data/demo.json`；pickle 不随仓库分发 |

迁移脚本：`scripts/migrate_v1_pickle.py`。它会补上 v1 缺失的反向指针 `corrects`——
旧数据只有 `superseded_by`，纠错链是半边断的。
