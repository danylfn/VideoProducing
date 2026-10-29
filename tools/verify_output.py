"""
Проверка на готов епизод: формат, синхрон, сила на звука, субтитри, глави, thumbnail.

  python tools/verify_output.py output/<project_name>

Изход 0 = всичко е наред, 1 = има проблем (вижте редовете FAIL).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.verify import verify  # noqa: E402


def main() -> int:
    ok, lines = verify(Path(sys.argv[1]))
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
