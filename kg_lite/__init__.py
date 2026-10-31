"""knowledge-graph-lite：个人知识库命令行发行版。"""

from __future__ import annotations

__version__ = "0.1.0"

from .cli import main  # noqa: E402

__all__ = ["main", "__version__"]
