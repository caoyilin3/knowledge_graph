"""knowledge_graph 启动器。

用法::

    start.bat                交互菜单
    start.bat test           跑全部单元测试（168 例）
    start.bat bench          跑一致性评测
    start.bat demo           跑完整流程演示（临时目录）
    start.bat readme         打开 README

运行时零第三方依赖，只用 Python 3.10+ 标准库。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent
MIN_PY = (3, 10)

BANNER = """
============================================================
  knowledge_graph · 启动器
============================================================
"""

MENU = """
请选择：
  1) 跑单元测试      python -m unittest discover -s tests   （约 1 秒）
  2) 跑评测          python bench/eval.py                   （约 2 秒）
  3) 跑演示          python -m knowledge_graph demo          （临时目录）
  4) 打开 README
  0) 退出
"""


def check_python() -> bool:
    if sys.version_info < MIN_PY:
        print(f"需要 Python {MIN_PY[0]}.{MIN_PY[1]}+，当前是 {sys.version.split()[0]}")
        return False
    return True


def run(args: list[str], label: str) -> bool:
    print(f"\n[{time.strftime('%H:%M:%S')}] > {label}")
    t0 = time.perf_counter()
    code = subprocess.run([sys.executable, *args], cwd=ROOT).returncode
    dt = time.perf_counter() - t0
    if code != 0:
        print(f"[{time.strftime('%H:%M:%S')}] [FAIL] 退出码 {code}，用时 {dt:.1f}s")
        return False
    print(f"[{time.strftime('%H:%M:%S')}] [OK] 用时 {dt:.1f}s")
    return True


def do_test() -> bool:
    return run(["-m", "unittest", "discover", "-s", "tests"], "单元测试")


def do_bench() -> bool:
    return run(["bench/eval.py"], "一致性评测")


def do_demo() -> bool:
    return run(["-m", "knowledge_graph", "demo"], "演示（临时目录）")


def do_readme() -> None:
    target = ROOT / "README.md"
    print(f"\n打开 {target}")
    if os.name == "nt":
        os.startfile(target)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    else:
        subprocess.run(["xdg-open", str(target)], check=False)


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help", "help") for a in argv):
        print(__doc__)
        return 0
    if not check_python():
        return 1

    print(BANNER)
    if argv:
        cmd = argv[0].lower()
        table = {"test": do_test, "bench": do_bench, "demo": do_demo}
        if cmd in ("readme", "doc", "docs"):
            do_readme()
            return 0
        handler = table.get(cmd)
        if handler is None:
            print(f"未知命令：{cmd}\n可用：test bench demo readme")
            return 2
        return 0 if handler() else 1

    while True:
        print(MENU)
        try:
            choice = input("输入序号后回车：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("0", "q", "quit", "exit"):
            return 0
        if choice == "1":
            do_test()
        elif choice == "2":
            do_bench()
        elif choice == "3":
            do_demo()
        elif choice == "4":
            do_readme()
        else:
            print("没有这个选项。")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
