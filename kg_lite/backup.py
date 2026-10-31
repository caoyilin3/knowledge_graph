"""lite 的本地备份：把 JSON 数据文件复制到 ``<数据目录>/backups/``，并滚动保留。

为什么是文件级备份：核心的 JSON 存储本来就是单文件，复制它就是完整快照，
不需要额外实现导出逻辑；也避免了「备份时漏掉某个字段」的风险。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

DEFAULT_KEEP = 10


def backup_dir(data_path: Path) -> Path:
    return data_path.parent / "backups"


def create_backup(data_path: Path, *, keep: int = DEFAULT_KEEP) -> Path | None:
    """复制一份备份并滚动清理旧文件；数据文件不存在时返回 ``None``。"""
    if keep < 0:
        raise ValueError("keep 不能为负")
    if not data_path.exists():
        return None

    target_dir = backup_dir(data_path)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = target_dir / f"{data_path.stem}-{stamp}{data_path.suffix or '.json'}"
    shutil.copy2(data_path, target)
    if keep > 0:
        _prune(target_dir, data_path.stem, keep)
    return target


def _prune(directory: Path, stem: str, keep: int) -> None:
    backups = sorted(directory.glob(f"{stem}-*"))
    for stale in backups[:-keep]:
        try:
            stale.unlink()
        except OSError:  # pragma: no cover - 清理失败不影响本次备份
            pass
