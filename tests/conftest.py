from __future__ import annotations

import sys
from pathlib import Path


def _ensure_project_root_on_path() -> None:
    """프로젝트 루트를 sys.path에 추가한다."""
    project_root = Path(__file__).resolve().parents[1]
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


_ensure_project_root_on_path()



