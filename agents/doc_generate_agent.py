from __future__ import annotations

from typing import Dict


def doc_generate(user_text: str) -> Dict:
    # 실제 구현에서는 템플릿 선택, 변수 추출, python-docx 생성 등을 수행
    # 여기서는 요청 수락 메시지로 응답
    return {
        "ok": True,
        "message": "문서 자동 작성 요청을 접수했습니다. 필요한 템플릿과 세부 항목을 알려주세요.",
        "echo": user_text,
    }








