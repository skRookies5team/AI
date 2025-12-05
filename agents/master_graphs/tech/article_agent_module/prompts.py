"""프롬프트 및 LLM 설정."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from agents.master_graphs.tech.article_agent_module.state import (
    OPENAI_MODEL,
    OPENAI_TIMEOUT,
)

# Intent 분류용 LLM
LLM = ChatOpenAI(
    model=OPENAI_MODEL,
    temperature=0.0,
    max_tokens=512,
    timeout=OPENAI_TIMEOUT,
    max_retries=2,
)

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
                "   - 특징: 검색어나 주제가 명시되어 있고, 다운로드/상세 정보 관련 키워드가 없음\n\n"
                "2. detail: 이전 검색 결과 중 특정 논문의 상세 정보 요청\n"
                "   - 예시: '1번 논문에 대해 알려줘', '2번째 논문 상세 정보', '첫 번째 논문 설명해줘', '1번', '2번'\n"
                "   - 특징: '알려', '상세', '정보', '설명', '에 대해' 같은 키워드 + 순번(1번째, 2번째 등)\n"
                "   - 중요: 이전에 검색 결과가 있어야 함 (results_count > 0)\n\n"
                "3. download: 이전 검색 결과 중 특정 논문의 PDF 다운로드 요청\n"
                "   - 예시: '1번 논문 다운로드', '2번째 논문 pdf 다운받아줘', '첫 번째 논문 저장', '1번 pdf 받아'\n"
                "   - 특징: '다운로드', '다운받', 'pdf 저장', 'pdf 다운', '저장해', '받아' 같은 키워드 + 순번\n"
                "   - 중요: 이전에 검색 결과가 있어야 함 (results_count > 0)\n\n"
                "반드시 아래 형식의 JSON만 반환하세요 (설명 없이 JSON만):\n"
                '{{"intent": "search" 또는 "detail" 또는 "download", "query": "검색어 또는 빈 문자열", "download_index": 숫자 배열 또는 null}}\n\n'
                "규칙:\n"
                "- intent가 detail 또는 download이면 query는 빈 문자열 \"\"\n"
                "- intent가 search이면 query에 검색어를 넣고 download_index는 null\n"
                "- download_index는 1부터 시작하는 정수 배열 (예: [1, 2] 또는 [2, 3])\n"
                "  - 1번째=1, 2번째=2, 첫 번째=1, 두 번째=2\n"
                "  - 여러 순번 요청 시: '2, 3번' → [2, 3], '1번이랑 3번' → [1, 3], '다섯번째랑 여섯번째' → [5, 6]\n"
                "  - 단일 순번도 배열로 반환: '1번' → [1], '2번째' → [2]\n"
                "- 순번이 없으면 download_index는 null\n"
                "- results_count가 0이고 intent가 detail 또는 download이면, intent를 search로 변경하고 query에 사용자 입력을 넣으세요\n"
                "- results_count > 0이고 사용자 입력이 순번만 포함 ('1번', '2번', '첫 번째' 등)하면 detail로 분류\n"
                "- '다운로드', '다운받', 'pdf 저장', 'pdf 다운', '저장해', '받아' 같은 키워드가 있으면 download로 분류\n"
                "- '요약', '알려', '상세', '정보', '설명', '에 대해' 같은 키워드가 있고 순번이 있으면 detail로 분류\n"
                "- 여러 순번 요청 ('2, 3번', '1번이랑 3번', '다섯번째랑 여섯번째 논문에 대해 요약해줄래?' 등)은 모두 배열로 반환 (예: [2, 3], [1, 3], [5, 6])"
            ),
        ),
        (
            "human",
            (
                "상황 정보:\n"
                "- 이전 검색 결과 개수: {results_count}개\n"
                "- 사용자 입력: {user_input}\n\n"
                "이전 대화:\n{history}\n\n"
                "위 정보를 바탕으로 의도를 분류하고 JSON을 반환하세요:"
            ),
        ),
    ]
)

