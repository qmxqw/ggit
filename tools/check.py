"""一条命令跑完全部检查：ruff（代码风格 / 常见隐患）、mypy（类型）、pytest（测试）。

用法：  python tools/check.py
需要先装开发依赖：  pip install -r requirements-dev.txt
CI（.github/workflows/ci.yml）跑的也是这一条命令。
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

STEPS = [
    ("ruff", ["-m", "ruff", "check", "."]),
    ("mypy", ["-m", "mypy"]),
    ("pytest", ["-m", "pytest", "-q"]),
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env = dict(os.environ, PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen")
    failed = []
    for name, args in STEPS:
        print(f"== {name}: python {' '.join(args)}", flush=True)
        if subprocess.run([sys.executable, *args], cwd=ROOT, env=env).returncode != 0:
            failed.append(name)
    if failed:
        print(f"== 没通过：{', '.join(failed)}")
        return 1
    print("== 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
