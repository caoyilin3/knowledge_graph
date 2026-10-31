# 参与贡献

## 环境

- Python 3.10+，运行时零第三方依赖；
- 不需要虚拟环境也能开发，但建议用。

## 本地验证

```bash
python -m unittest discover -s tests -v   # 全量测试
python bench/eval.py --no-write           # 场景评测，不写文件
```

提交前这两条都应该通过。

## 代码约定

- **内核（`knowledge_graph/`）不依赖 CLI，也不引入第三方包**；需要新能力时先想清楚
  它属于内核还是 lite。
- lite（`kg_lite/`）只能调用内核的公开 API，不碰 `_private` 方法。
- 新行为要有测试；修 bug 先写一个能复现的用例，再修。
- 时间统一用 UTC aware `datetime`；对外展示再转本地时区。
- 注释与文档用中文，标识符用英文。

## 提交信息

一行说清做了什么，正文写「为什么」和影响面。例如：

```
fix: 清理时不再删除被取代的条目

删除会让纠错链断裂；改为 deprecate 保留，清理只处理 active。
```

## 安全问题

不要开公开 issue。请使用 GitHub 仓库的 **Private vulnerability reporting**
（Security 标签页），或直接联系维护者。详见 [SECURITY.md](SECURITY.md)。

## 许可证

贡献即表示同意以 [Apache License 2.0](LICENSE) 许可证发布。
