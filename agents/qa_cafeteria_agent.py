from __future__ import annotations

from typing import Dict


def cafeteria_menu(user_text: str) -> Dict:
    # 실제 구현에서는 주간 식단표 데이터/캘린더에서 조회
    return {
        "ok": True,
        "message": "이번 주 구내식당 식단 정보를 안내합니다. (샘플 데이터, 시스템 연동 필요)",
        "echo": user_text,
    }






