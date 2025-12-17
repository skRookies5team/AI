"""프롬프트 및 LLM 설정."""

from __future__ import annotations
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from agents.master_graphs.tech.article_agent_module.state import (
    OPENAI_MODEL,
    OPENAI_TIMEOUT,
)

# Intent 분류용 LLM (지연 초기화로 변경하여 환경 변수 로드 문제 방지)
_LLM_INSTANCE: Optional[ChatOpenAI] = None

def get_intent_llm() -> ChatOpenAI:
    """Intent 분류용 LLM 인스턴스 반환 (지연 초기화)."""
    global _LLM_INSTANCE
    if _LLM_INSTANCE is None:
        if not OPENAI_MODEL:
            raise ValueError("OPENAI_MODEL 환경 변수가 설정되지 않았습니다. .env 파일에 OPENAI_MODEL을 설정해주세요.")
        _LLM_INSTANCE = ChatOpenAI(
            model=OPENAI_MODEL,
            temperature=0.0,
            max_tokens=512,
            timeout=OPENAI_TIMEOUT,
            max_retries=2,
        )
    return _LLM_INSTANCE

# 하위 호환성을 위한 별칭 (지연 초기화)
# OPENAI_MODEL이 None이면 초기화하지 않고, 사용 시점에 get_intent_llm() 호출
if OPENAI_MODEL:
    try:
        LLM = get_intent_llm()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"LLM 초기화 실패: {e}")
        LLM = None
else:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("OPENAI_MODEL 환경 변수가 설정되지 않았습니다. LLM은 None입니다.")
    LLM = None

# Intent 분류 프롬프트
INTENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "당신은 ScienceON 논문 검색 챗봇의 의도 분류 전문가입니다.\n"
                "사용자의 입력을 분석하여 다음 중 하나로 분류하세요:\n\n"
                "1. search: 새로운 논문 검색 요청\n"
                "   - 예시: '인공지능 논문 찾아줘', '디스플레이 관련 논문 검색', '머신러닝 논문'\n"
                "   - 특징: 검색어나 주제가 명시되어 있고, 다운로드/상세 정보 관련 키워드가 없음\n"
                "   - 중요: 이전 검색 키워드({previous_keyword})와 다른 주제를 검색하면 search로 분류 (예: 이전='배터리', 현재='디스플레이' → search)\n"
                "   - 중요: 이전 검색 키워드와 같은 주제라도 명시적으로 검색 키워드('찾아', '검색' 등)가 있으면 search로 분류\n\n"
                "   - ⚠️ 예외: 순번(1번, 2번, 첫 번째 등)이 있으면 detail 또는 download로 분류 (예: '1번 논문 비교해줘' → detail, '1번 논문 다운로드' → download)\n\n"
                "2. detail: 이전 검색 결과 중 특정 논문의 상세 정보 요청\n"
                "   - 예시: '1번 논문에 대해 알려줘', '2번째 논문 상세 정보', '첫 번째 논문 설명해줘', '1번', '2번'\n"
                "   - 예시: '이 논문에서 언급한 중요한 요소가 뭐야?', '그 논문의 결론 좀 요약해줘', '위 논문의 주요 내용 알려줘\n"
                "   - 특징: '알려', '상세', '정보', '설명', '에 대해', '비교' 같은 키워드 + 순번(1번째, 두 번째, 3, 4번)\n"
                "   - 중요: 이전에 검색 결과가 있어야 함 (results_count > 0)\n"
                "   - 중요: 이전 검색 키워드와 같은 주제이고 순번만 있으면 detail로 분류 (예: 이전='배터리', 현재='1번' → detail)\n\n"
                "   - 중요: 순번이나 지시어가 없어도 results_count > 0이고 '요약', '결론', '알려', '상세', '정보', '설명' 같은 키워드가 있고 이전 대화에서 논문을 선택했다면 detail로 분류 (예: '결론 좀 요약해줄래?' → detail)\n\n"
                "3. download: 이전 검색 결과 중 특정 논문의 PDF 다운로드 요청\n"
                "   - 예시: '1번 논문 다운로드', '2번째 논문 pdf 다운받아줘', '첫 번째 논문 저장', '1번 pdf 받아'\n"
                "   - 특징: '다운로드', '다운받', 'pdf 저장', 'pdf 다운', '저장해', '받아' 같은 키워드 + 순번\n"
                "   - 중요: 이전에 검색 결과가 있어야 함 (results_count > 0)\n\n"
                "반드시 아래 형식의 JSON만 반환하세요 (설명 없이 JSON만):\n"
                '{{"intent": "search" 또는 "detail" 또는 "download", "query": "검색어 또는 빈 문자열", "download_index": 숫자 배열 또는 null}}\n\n'
                "규칙 (우선순위 순):\n"
                "   1. ⚠️ 최우선: results_count > 0이고 사용자 입력에 순번(1번, 2번째, 첫 번째 등)이 있으면 무조건 detail 또는 download로 분류하세요\n"
                "      - ️results_count를 초과하는 순번(예: results_count=6인데 7번 요청)이어도 detail 또는 download로 분류하세요\n"
                "      - '다운로드', '다운받', 'pdf 저장', 'pdf 다운', '저장해', '받아' 같은 키워드가 있으면 download로 분류\n"
                "      - 그 외에는 detail로 분류 (검색 키워드가 있어도 순번이 우선)\n"
                "      - ⚠️ 중요: 순번이 있으면 반드시 download_index에 배열로 반환하세요\n"
                "      - 중요: 아래는 순번 추출 예시입니다. 참고해서 사용자 입력의 순번을 추출하세요.\n"
                "      - 예시: '1번 논문에 대해 알려줄래?' → {{\"intent\": \"detail\", \"query\": \"\", \"download_index\": [1]}}\n"
                "      - 예시: '1, 2, 4번 논문에 대해 알려줄래?' → {{\"intent\": \"detail\", \"query\": \"\", \"download_index\": [1, 2, 4]}}\n"
                "      - 예시: '2, 4번 논문에 대해 알려줄래?' → {{\"intent\": \"detail\", \"query\": \"\", \"download_index\": [2, 4]}}\n"
                "      - 예시: '세번째랑 네번째 논문에 대해 알려줄래?' → {{\"intent\": \"detail\", \"query\": \"\", \"download_index\": [3, 4]}}\n"
                "      - ⚠️ 중요: 사용자 입력에 여러 순번이 있으면 모두 추출하세요 (예: '1, 2, 4번' → [1, 2, 4], '1번이랑 3번이랑 5번' → [1, 3, 5])\n"
                "   2. results_count > 0이고 사용자 입력이 순번만 포함 ('1번', '2번', '첫 번째' 등)하면 detail로 분류\n"
                "   3. results_count > 0이고 '이 논문', '그 논문', '위 논문', '해당 논문' 같은 지시어가 있으면 detail로 분류 (download_index는 null, 이전에 선택한 논문을 참조)\n"
                "   4. results_count > 0이고 순번이나 지시어가 없어도 '요약', '결론', '알려', '상세', '정보', '설명', '비교', '에 대해' 같은 키워드가 있고:\n"
                "   - 이전 대화에서 논문을 선택했다면 detail로 분류\n"
                "   - 또는 사용자 입력의 키워드가 이전 검색 키워드({previous_keyword})와 관련이 있으면 detail로 분류\n"
                "   - 예시: 이전 검색='플렉시블디스플레이' 논문 2건, 현재='플렉시블디스플레이에 대해 자세히 설명해줄래?' → detail\n"
                "   - 예시: 이전 검색='배터리' 논문 3건, 현재='배터리 기술에 대해 알려줄래?' → detail\n"
                "   - (download_index는 null, 이전에 선택한 논문 또는 이전 검색 결과를 참조)\n"
                "   5. 사용자 입력에 검색 키워드('찾아', '검색', '찾아줘', '찾아주세요', '검색해줘')가 포함되어 있고 순번이 없으면 search로 분류\n"
                "   6. results_count가 0이고 intent가 detail 또는 download이면, intent를 search로 변경하고 query에 사용자 입력을 넣으세요\n\n"
                "download_index 규칙:\n"
                "   - download_index는 1부터 시작하는 정수 배열\n"
                "   - 1번째=1, 2번째=2, 첫 번째=1, 두 번째=2, 1번=1, 2번=2, 3번=3\n"
                "   - ⚠️ 중요: 사용자 입력에 여러 순번이 있으면 모두 추출하세요. 하나라도 누락하면 안 됩니다.\n"
                "   - ⚠️ 중요: results_count를 고려하지 말고 사용자가 요청한 모든 순번을 그대로 반환하세요.\n"
                "   - ⚠️ 중요: 사용자가 '1, 2, 4번'을 요청했으면 results_count가 2여도 [1, 2, 4]를 반환하세요. 범위 체크는 시스템이 처리합니다.\n"
                "   - 여러 순번 요청 시(예시): '2, 3번' → [2, 3], '1번이랑 3번, 4번번' → [1, 3, 4], '1번 논문과 3번 논문을 비교해줄래?' → [1, 3], '다섯번째랑 여섯번째' → [5, 6]\n"
                "   - 단일 순번도 배열로 반환(예시): '1번' → [1], '2번째' → [2]\n"
                "   - 순번이 없으면 download_index는 null\n\n"
                "query 규칙:\n"
                "   - intent가 detail 또는 download이면 query는 빈 문자열 \"\"\n"
                "   - intent가 search이면 query에 검색어를 넣고 download_index는 null\n"
            ),
        ),
        (
            "human",
            (
                "상황 정보:\n"
                "- 이전 검색 결과 개수: {results_count}개\n"
                "- 이전 검색 키워드: {previous_keyword}\n"
                "- 사용자 입력: {user_input}\n\n"
                "이전 대화:\n{history}\n\n"
                "위 정보를 바탕으로 의도를 분류하고 JSON을 반환하세요:"
            ),
        ),
    ]
)

