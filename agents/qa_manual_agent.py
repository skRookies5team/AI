from __future__ import annotations

from typing import Dict


def manual_search(user_text: str) -> Dict:
    # 실제 구현에서는 벡터 검색/키워드 검색 등을 수행
    return {
        "ok": True,
        "message": "메뉴얼에서 관련 항목을 검색했습니다. 구체 키워드를 더 주시면 정확도가 올라갑니다.",
        "echo": user_text,
    }








