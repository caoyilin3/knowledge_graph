"""默认数据文件位置。

优先级：``--data`` 参数 > 环境变量 ``KG_DATA`` > ``~/.knowledge-graph/kb.json``。
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DIR = Path.home() / ".knowledge-graph"
DEFAULT_FILE = DEFAULT_DIR / "kb.json"
ENV_VAR = "KG_DATA"


def resolve_data_path(cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()
    return DEFAULT_FILE
