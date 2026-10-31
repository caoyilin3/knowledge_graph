# 评测说明

这里有两层东西，别混用。

## 1. 合成场景（已实现）

`scenarios.py` + `eval.py`：38 条按默认策略表推导出期望值的场景。

```bash
python bench/eval.py            # 同时写入 docs/EVALUATION.md
```

它回答的是「引擎有没有按写下的规则办事」，指标是**规则实现一致性**：
状态一致率、删除精确率/召回率、核心知识保留率、纠错链完整率、幂等性。

它**不回答**「这套策略在真实新闻流上效果如何」。不要把 `docs/EVALUATION.md`
里的 38/38 当成真实准确率对外引用。

## 2. 人工标注评测（未实现，需要数据）

要回答「真实语料上效果如何」，需要：

1. 一批真实条目（含发布时间、类型、以及是否被纠正）；
2. 人工标注的期望动作：`keep` / `delete` / `deprecate` / `archive`；
3. 标注协议（什么算该删、什么算核心知识、两人标注一致性怎么算）。

建议的标注格式（JSONL，一行一条）：

```json
{"id": "real-0001", "content_type": "news", "created_at": "2026-05-01T08:00:00+00:00", "text": "……", "label": "delete", "corrects": null}
```

接入方式：写一个 `bench/labeled.py`，读 JSONL → 用固定时钟调 `Engine.add()` /
`supersede()` → `cleanup(now=...)` → 与 `label` 比对，复用 `eval.py` 的指标计算。

在没有标注集之前，本仓库不会生成任何「真实语料准确率」数字——上游项目在这
一点上出过问题（详见 `docs/PROVENANCE.md`），这里不再重复。
