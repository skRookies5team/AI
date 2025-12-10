"""LangChain을 이용한 회의록 요약 및 문서 생성
[수정] 2025-10-27 (요청 반영): classify_topics와 generate_follow_up_questions 프롬프트 수정 (server.py 파싱 로직 호환성)
"""

import os
import logging
from typing import Optional
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate


class ReportGeneratorError(Exception):
    """Report Generator 오류"""
    pass


class ReportGenerator:
    """LangChain을 이용한 요약 및 문서 생성기"""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        """
        ReportGenerator 초기화

        Args:
            api_key: OpenAI API 키 (None이면 환경변수에서 로드)
            model: 사용할 OpenAI 모델 (기본: gpt-4o-mini)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ReportGeneratorError("OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인해주세요.")

        self.model = model
        self.llm = ChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            temperature=0.3, # <<< 기본 temperature 유지
            max_tokens=4000
        )
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            api_key=self.api_key
        )
        self.logger = logging.getLogger(__name__)

    def _call_llm(self, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        """
        LLM 호출 공통 함수 (LangChain 사용)

        Args:
            system_prompt: 시스템 프롬프트
            user_prompt: 사용자 프롬프트
            temperature: 창의성 수준 (0.0-1.0)

        Returns:
            LLM 응답 텍스트

        Raises:
            ReportGeneratorError: API 호출 실패 시
        """
        try:
            # ChatPromptTemplate 생성
            chat_prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("user", user_prompt)
            ])

            # temperature를 동적으로 설정하기 위해 새 LLM 인스턴스 생성
            # (기본값과 다른 temperature가 필요할 때만 새 인스턴스 사용)
            if temperature != self.llm.temperature:
                 llm = ChatOpenAI(
                    model=self.model,
                    api_key=self.api_key,
                    temperature=temperature,
                    max_tokens=4000
                 )
            else:
                llm = self.llm # 기본 인스턴스 사용

            # LangChain invoke 패턴 사용
            chain = chat_prompt | llm
            # <<< user_prompt에 template 변수가 없으므로 빈 dict 전달 >>>
            response = chain.invoke({})

            return response.content.strip()
        except Exception as e:
            self.logger.error(f"LangChain LLM 호출 오류: {e}")
            raise ReportGeneratorError(f"LLM 호출 중 오류가 발생했습니다: {e}")

    # --- [기존 5개 기능] ---

    def summarize(self, transcript: str) -> str:
        """
        대화록 텍스트를 핵심 요약합니다. (LangChain 기반)
        """
        self.logger.info("대화록 요약 생성 중... (LangChain)")

        system_prompt = """당신은 회의록을 전문적으로 요약하는 어시스턴트입니다.
        다음 지침을 따라 요약해주세요:
        1. 핵심 내용을 3-5개의 글머리 기호로 정리
        2. 중요한 결정사항이나 합의사항 강조
        3. 구체적인 수치나 날짜가 있다면 포함
        4. 간결하고 명확한 표현 사용"""

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음은 화자별로 구분된 회의록 텍스트입니다.
이 대화의 핵심 내용을 3-5줄의 글머리 기호(•)로 요약해 주세요.

--- [회의록] ---
{transcript}
----------------

요약:
"""
        return self._call_llm(system_prompt, user_prompt)

    def generate_meeting_notes(self, transcript: str) -> str:
        """
        대화록을 바탕으로 공식 회의록 문서를 생성합니다. (LangChain 기반)
        """
        self.logger.info("공식 회의록 문서 생성 중... (LangChain)")

        system_prompt = """당신은 회의 내용을 바탕으로 공식적인 회의록(Meeting Minutes)을 작성하는 전문 비서입니다.
        다음 형식을 반드시 준수하여 문서를 생성해 주세요:

        ## 회의록

        ### 1. 주요 안건
        - (대화에서 논의된 핵심 주제들을 나열)

        ### 2. 결정 사항
        - (대화를 통해 합의되거나 결정된 내용들)

        ### 3. 향후 조치 사항 (Action Items)
        - (특정 담당자에게 할당된 업무나 향후 계획)

        ### 4. 기타 사항
        - (추가적인 논의 사항이나 참고 내용)

        전문적이고 공식적인 문체를 사용하며, 구체적인 내용을 포함해주세요."""

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록 텍스트를 바탕으로 공식 회의록 문서를 작성해 주세요.

--- [회의록] ---
{transcript}
----------------

공식 회의록:
"""
        return self._call_llm(system_prompt, user_prompt)

    def generate_action_items(self, transcript: str) -> str:
        """
        대화록에서 액션 아이템을 추출합니다. (LangChain 기반)
        """
        self.logger.info("액션 아이템 추출 중... (LangChain)")

        system_prompt = """당신은 회의 내용에서 실행 가능한 액션 아이템을 추출하는 전문가입니다.
        다음 기준에 따라 액션 아이템을 식별해주세요:
        1. 구체적인 행동이 필요한 항목
        2. 담당자가 명시되거나 추정 가능한 항목
        3. 기한이 있거나 우선순위가 높은 항목"""

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록에서 액션 아이템을 추출해주세요.
각 항목에 대해 [담당자] 작업내용 형태로 정리해주세요.

--- [회의록] ---
{transcript}
----------------

액션 아이템:
"""
        return self._call_llm(system_prompt, user_prompt)

    def analyze_sentiment(self, transcript: str) -> str:
        """
        회의 분위기와 참석자들의 감정을 분석합니다. (LangChain 기반)
        """
        self.logger.info("회의 분위기 분석 중... (LangChain)")

        system_prompt = """당신은 회의 분위기와 참석자들의 감정을 분석하는 전문가입니다.
        대화의 톤, 의견 충돌, 합의 정도, 전반적인 분위기를 객관적으로 분석해주세요."""

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록의 분위기와 참석자들의 감정을 분석해주세요.

--- [회의록] ---
{transcript}
----------------

분석 결과:
"""
        # <<< 감정 분석은 좀 더 객관적이어야 하므로 temperature 낮춤 >>>
        return self._call_llm(system_prompt, user_prompt, temperature=0.1)

    # <<< [수정] 프롬프트 수정 >>>
    def generate_follow_up_questions(self, transcript: str) -> str:
        """
        회의 내용을 바탕으로 후속 질문을 생성합니다. (LangChain 기반)
        """
        self.logger.info("후속 질문 생성 중... (LangChain)")

        system_prompt = """당신은 회의 내용을 분석하여 후속 논의가 필요한 질문들을 생성하는 전문가입니다.
        미해결 이슈, 추가 검토가 필요한 사항, 명확화가 필요한 내용을 중심으로 질문 목록만 생성해주세요.
        **반드시 각 질문 앞에 하이픈(-)과 공백을 붙여 목록 형태로 작성해주세요.**
        **(다른 설명이나 카테고리는 절대 포함하지 마세요)**""" # <<< 형식 및 제외 내용 강조

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록을 분석하여 후속 회의나 개별 논의에서 다뤄야 할 질문 목록을 생성해주세요.

--- [회의록] ---
{transcript}
----------------

후속 질문:
"""
        # <<< 질문 생성은 약간의 창의성이 필요할 수 있으므로 기본 temperature 사용 >>>
        return self._call_llm(system_prompt, user_prompt)

    # --- [신규 기능 6개] ---

    def extract_keywords(self, transcript: str) -> str:
        """
        대화록에서 핵심 키워드를 추출합니다. (LangChain 기반)
        """
        self.logger.info("핵심 키워드 추출 중... (LangChain)")

        system_prompt = """당신은 텍스트에서 핵심 키워드를 추출하는 전문가입니다.
다음 규칙을 엄격히 준수하여 키워드만 추출하세요:
1. 정확히 10개의 키워드만 추출
2. 각 키워드는 30자 이내의 짧은 단어 또는 구문
3. 중복 금지
4. 설명문, 완전한 문장 금지
5. 한 줄에 하나의 키워드만 출력 (앞에 - 기호 붙이기)
6. 카테고리 헤더나 추가 설명 금지"""

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록에서 가장 중요한 키워드 10개를 추출하세요.

회의록:
{transcript}

출력 형식 (반드시 이 형식만 사용):
- 키워드1
- 키워드2
- 키워드3
...
"""
        return self._call_llm(system_prompt, user_prompt)

    # <<< [수정] 프롬프트 수정 >>>
    def classify_topics(self, transcript: str) -> str:
        """
        대화록의 주제를 분류하고 카테고리화합니다. (LangChain 기반)
        """
        self.logger.info("주제 분류 중... (LangChain)")

        system_prompt = """당신은 회의 내용을 주제별로 분류하는 전문가입니다.
        대화록을 읽고 다음 작업을 수행해주세요:
        1. 논의된 주요 주제들을 식별
        2. 각 주제에 대한 중요도 평가 (높음/중간/낮음)
        3. 각 주제별 논의 내용 요약
        4. 각 주제별 논의 비중 추정 (백분율)

        **반드시 아래의 출력 형식을 정확히 지켜주세요.**""" # <<< 형식 강조

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록의 주제를 분류하고 분석해주세요.

**출력 형식:**

**주요 주제 분류**:
1. [주제명1] (중요도: 높음/중간/낮음)
   - 논의 내용 요약: (주제1에 대한 요약)
   - 전체 대화에서 차지하는 비중: [숫자]%

2. [주제명2] (중요도: 높음/중간/낮음)
   - 논의 내용 요약: (주제2에 대한 요약)
   - 전체 대화에서 차지하는 비중: [숫자]%
(주제가 더 있으면 계속 나열)

**(주제 간 연관관계나 우선순위 등 다른 내용은 절대 포함하지 마세요)** # <<< 불필요한 내용 제외 요청

--- [회의록] ---
{transcript}
----------------

주제 분류:
"""
        return self._call_llm(system_prompt, user_prompt)

    def analyze_by_speaker(self, transcript: str) -> str:
        """
        발언자별로 내용을 요약하고 분석합니다. (LangChain 기반)
        """
        self.logger.info("발언자별 분석 중... (LangChain)")

        system_prompt = """당신은 회의 참석자들의 발언을 분석하는 전문가입니다.
        각 화자별로 다음을 분석해주세요:
        1. 주요 발언 내용 요약
        2. 전문 분야 또는 역할 추정
        3. 의견의 방향성 (찬성/반대/중립)
        4. 주도성 정도 (적극적/보통/소극적)
        5. 핵심 기여 사항"""

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록에서 각 화자별로 발언을 분석해주세요.

각 화자마다 다음 형식으로 작성해주세요:

**화자 [번호]**:
- **주요 발언 내용**: (핵심 내용 2-3줄 요약)
- **추정 역할**: (기술팀, 관리자, 마케팅 등)
- **의견 방향성**: (긍정적/부정적/중립적/혼합)
- **참여도**: (적극적/보통/소극적)
- **핵심 기여**: (이 사람이 회의에 기여한 핵심 내용)

--- [회의록] ---
{transcript}
----------------

발언자별 분석:
"""
        # <<< 분석은 객관성이 중요하므로 temperature 낮춤 >>>
        return self._call_llm(system_prompt, user_prompt, temperature=0.2)

    def classify_meeting_type(self, transcript: str) -> str:
        """
        회의 유형을 분류합니다. (LangChain 기반)
        """
        self.logger.info("회의 유형 분류 중... (LangChain)")

        system_prompt = """당신은 회의 유형을 분석하고 분류하는 전문가입니다.
        다음 카테고리 중에서 가장 적합한 유형을 선택하고 그 근거를 설명해주세요:

        **회의 유형 카테고리**:
        1. 프로젝트 킥오프 - 새로운 프로젝트 시작
        2. 정기 진행 회의 - 주간/월간 정기 미팅
        3. 브레인스토밍 - 아이디어 발산 세션
        4. 의사결정 회의 - 중요한 결정을 내리는 회의
        5. 문제 해결 회의 - 특정 문제 해결 논의
        6. 보고/공유 회의 - 정보 전달 및 공유
        7. 계획 수립 회의 - 전략/계획 수립
        8. 검토/피드백 회의 - 결과물 검토
        9. 교육/학습 세션 - 지식 전달
        10. 기타 - 위 카테고리에 해당하지 않는 경우"""

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록을 분석하여 회의 유형을 분류해주세요.

다음 형식으로 작성해주세요:

**회의 유형**: [선택한 카테고리]

**주 카테고리 신뢰도**: XX%

**부 카테고리** (해당되는 경우):
- [추가 카테고리] (XX%)

**분류 근거**:
- (이 유형으로 분류한 이유를 구체적으로 설명)

**회의 특징**:
- 참여자 구성: (경영진/실무자/혼합)
- 진행 방식: (구조적/자유로운/혼합)
- 의사결정 여부: (있음/없음/보류)

--- [회의록] ---
{transcript}
----------------

회의 유형 분류:
"""
        # <<< 분류는 객관성이 중요하므로 temperature 낮춤 >>>
        return self._call_llm(system_prompt, user_prompt, temperature=0.1)

    def summarize_by_speaker(self, transcript: str) -> str:
        """
        대화록을 바탕으로 화자별 주요 발언을 요약합니다. (LangChain 기반)
        """
        self.logger.info("화자별 간단 요약 생성 중... (LangChain)")

        system_prompt = """당신은 회의록을 바탕으로 각 화자의 주요 입장이나 발언을 요약하는 전문가입니다.
        [화자 N] 형태로 구분된 대화록을 보고, 각 화자별로 핵심 주장을 1~2줄로 요약해주세요.
        모든 화자를 포함할 필요는 없으며, 중요 발언을 한 화자 중심으로 정리해주세요.

        [출력 형식]
        - [화자 1]: (화자 1의 핵심 발언 요약)
        - [화자 2]: (화자 2의 핵심 발언 요약)
        """

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록을 화자별로 요약해주세요.

--- [회의록] ---
{transcript}
----------------

화자별 요약:
"""
        return self._call_llm(system_prompt, user_prompt)

    def calculate_engagement_score(self, transcript: str) -> str:
        """
        회의 참여도를 점수화하고 분석합니다. (LangChain 기반)

        Args:
            transcript: 대화록 텍스트 (화자별로 구분된 형태)

        Returns:
            참여도 점수 분석 결과 (JSON 형식 문자열)

        Raises:
            ReportGeneratorError: 점수 계산 실패 시
        """
        self.logger.info("회의 참여도 점수 계산 중... (LangChain)")

        system_prompt = """당신은 회의 참여도를 평가하는 전문 분석가입니다.
        회의록을 분석하여 참여도를 정량적으로 평가하고 점수화하세요.

        [평가 기준]
        1. 발언 빈도와 시간 분포 (30점)
        2. 발언의 질과 기여도 (40점)
        3. 참여 균형도 (30점)

        **반드시 아래의 JSON 형식으로만 응답해주세요.** # <<< JSON 형식 강조
        ```json
        {
            "overall_score": 85,
            "speaker_scores": {
                "화자1": {"score": 90, "발언수": 15, "기여도": "높음"},
                "화자2": {"score": 75, "발언수": 10, "기여도": "중간"}
            },
            "engagement_distribution": "균형적",
            "participation_balance": 80,
            "key_insights": [
                "화자1이 가장 활발하게 참여",
                "화자3의 참여도 개선 필요"
            ]
        }
        ```
        """

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록을 분석하여 참여도를 점수화하고 JSON 형식으로 반환해주세요.

--- [회의록] ---
{transcript}
----------------

참여도 점수 분석 (JSON):
"""
        # <<< JSON 생성을 위해 temperature 낮춤 >>>
        return self._call_llm(system_prompt, user_prompt, temperature=0.1)

    def generate_improvement_suggestions(self, transcript: str) -> str:
        """
        회의 개선을 위한 구체적인 제안을 생성합니다. (LangChain 기반)

        Args:
            transcript: 대화록 텍스트 (화자별로 구분된 형태)

        Returns:
            회의 개선 제안 (구조화된 텍스트)

        Raises:
            ReportGeneratorError: 제안 생성 실패 시
        """
        self.logger.info("회의 개선 제안 생성 중... (LangChain)")

        system_prompt = """당신은 효과적인 회의 진행을 위한 컨설턴트입니다.
        회의록을 분석하여 실행 가능한 구체적인 개선 제안을 제공하세요.

        [분석 영역]
        1. 회의 진행 방식 (의사결정, 주제 관리)
        2. 시간 관리 (주제별 시간 배분)
        3. 참여도 향상 (소극적 참여자 독려)
        4. 커뮤니케이션 효율성

        **반드시 아래의 출력 형식을 사용해주세요.** # <<< 형식 강조
        ```markdown
        ## 🎯 종합 평가
        (회의의 전반적인 평가)

        ## 💡 주요 개선 제안
        1. [구체적 제안 1]
           - 현재 문제: (문제점 설명)
           - 개선 방안: (실행 가능한 방법)
           - 기대 효과: (개선 시 효과)

        2. [구체적 제안 2]
           - 현재 문제: ...
           - 개선 방안: ...
           - 기대 효과: ...
        (필요하면 더 추가)

        ## 📊 다음 회의 체크리스트
        - [ ] (실행 항목 1)
        - [ ] (실행 항목 2)
        (필요하면 더 추가)
        ```
        """

        # <<< transcript 변수를 직접 f-string으로 삽입 >>>
        user_prompt = f"""
다음 회의록을 분석하여 구체적이고 실행 가능한 개선 제안을 제공해주세요.

--- [회의록] ---
{transcript}
----------------

개선 제안 (Markdown):
"""
        # <<< 제안 생성에는 약간의 창의성이 필요할 수 있으므로 temperature 약간 높임 >>>
        return self._call_llm(system_prompt, user_prompt, temperature=0.4)

    # --- [임베딩 기능] ---

    def generate_embedding(self, text: str) -> list[float]:
        """
        텍스트를 임베딩 벡터로 변환합니다. (LangChain OpenAIEmbeddings 사용)

        Args:
            text: 임베딩으로 변환할 텍스트

        Returns:
            1536개의 실수로 이루어진 임베딩 벡터

        Raises:
            ReportGeneratorError: 임베딩 생성 실패 시
        """
        try:
            # LangChain의 OpenAIEmbeddings.embed_query() 사용
            embedding = self.embeddings.embed_query(text)
            return embedding
        except Exception as e:
            self.logger.error(f"임베딩 생성 오류: {e}")
            raise ReportGeneratorError(f"임베딩 생성 중 오류가 발생했습니다: {e}")