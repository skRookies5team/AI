from __future__ import annotations

from typing import Dict


def welfare_info(user_text: str) -> Dict:
    # 실제 구현에서는 HR 정책 문서/DB에서 규정 조회
    return {
        "ok": True,
        "message": "복리후생 안내: 연차, 반차, 각종 수당, 선택적 복지몰 등 기본 정보를 전달합니다.",
        "echo": user_text,
    }






