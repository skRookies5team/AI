"""메뉴얼 검색 Agent

이 파일은 manual_search 폴더의 코드를 통합한 것입니다:
- Config 클래스들 (HybridSearchConfig, MilvusConfig, LLMConfig, EmbeddingConfig, ManualSearchConfig)
- Components 클래스들 (Embedder, KeywordSearcher, AnswerGenerator, DocumentRetriever)
- Graph 클래스들 및 함수들 (LangGraph 관련)
"""

from __future__ import annotations

# ============================================================================
# Imports
# ============================================================================

from typing import Any, Dict, List, Optional, Union
import re
import logging
import os
from collections import Counter
from pydantic import BaseModel, Field, model_validator
from openai import OpenAI
from pymilvus import connections, Collection, utility
from rank_bm25 import BM25Okapi
from langgraph.graph import END, StateGraph
from langchain_core.runnables import RunnableLambda  # type: ignore[import]
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


# ============================================================================
# Config 클래스들
# ============================================================================

class HybridSearchConfig(BaseModel):
    """하이브리드 검색 설정"""
    enabled: bool = Field(default=False, description="하이브리드 검색 사용 여부")
    vector_weight: float = Field(
        default=0.6,
        description="벡터 검색 가중치 (0.0-1.0)"
    )
    keyword_weight: float = Field(
        default=0.4,
        description="키워드 검색 가중치 (0.0-1.0)"
    )
    use_rrf: bool = Field(
        default=False,
        description="RRF(Reciprocal Rank Fusion) 사용 여부"
    )
    rrf_k: int = Field(
        default=60,
        description="RRF k 파라미터"
    )
    vector_expansion_factor: float = Field(
        default=5.0,
        description="벡터 검색 범위 확대 배수 (top_k에 곱해지는 값)"
    )
    keyword_expansion_factor: float = Field(
        default=2.0,
        description="키워드 검색 범위 확대 배수 (top_k에 곱해지는 값)"
    )
    min_score_threshold: float = Field(
        default=0.3,
        description="최소 점수 임계값 (이 점수 미만의 문서는 필터링됨, 0.0-1.0)"
    )
    core_keyword_bonus_per_match: float = Field(
        default=2.0,
        description="핵심 키워드 매칭당 보너스 점수"
    )
    core_keyword_penalty_ratio: float = Field(
        default=0.5,
        description="핵심 키워드가 없을 때 점수 감소 비율 (0.0-1.0)"
    )

    @model_validator(mode='after')
    def validate_weights(self):
        """벡터 가중치와 키워드 가중치의 합이 1.0에 가까운지 검증"""
        if abs(self.vector_weight + self.keyword_weight - 1.0) > 0.01:
            raise ValueError(
                f"vector_weight({self.vector_weight}) + keyword_weight({self.keyword_weight}) "
                f"합이 1.0에 가까워야 합니다. (현재 합: {self.vector_weight + self.keyword_weight})"
            )
        return self


class MilvusConfig(BaseModel):
    """MilvusDB 설정"""
    host: str = Field(
        default_factory=lambda: (
            (os.getenv("HOST", "")).strip()
        ),
        description="Milvus 호스트 (환경변수: HOST 또는 MILVUS_HOST)"
    )
    port: int = Field(
        default_factory=lambda: int((os.getenv("MILVUS_PORT", "")).strip()),
        description="Milvus 포트 (환경변수: MILVUS_PORT)"
    )
    collection_name: str = Field(
        default_factory=lambda: (
            (os.getenv("MENUAL_COLLECTION", "")).strip()
        ),
        description="컬렉션 이름 (환경변수: MENUAL_COLLECTION)"
    )
    dimension: int = Field(default=1536, description="벡터 차원")
    top_k: int = Field(default=5, description="상위 k개 문서 추출")
    embedding_field: str = Field(default="embedding", description="벡터 필드명")
    document_field: str = Field(default="content", description="문서 필드명")
    metadata_field: str = Field(default="metadata", description="메타데이터 필드명")
    metric_type: str = Field(default="L2", description="거리 측정 방식 (L2 또는 IP)")
    hybrid_search: Optional[HybridSearchConfig] = Field(
        default=None,
        description="하이브리드 검색 설정"
    )


class LLMConfig(BaseModel):
    """LLM 설정"""
    api_key: str = Field(..., description="OpenAI API 키")
    model: str = Field(default="gpt-4o-mini", description="사용할 모델")
    temperature: float = Field(default=0.7, description="온도 파라미터")
    max_tokens: int = Field(default=1000, description="최대 토큰 수")


class EmbeddingConfig(BaseModel):
    """임베딩 설정"""
    openai_model: str = Field(
        default="text-embedding-3-small",
        description="OpenAI 모델명"
    )
    api_key: Optional[str] = Field(default=None, description="OpenAI API 키")


class ManualSearchConfig(BaseModel):
    """메뉴얼 검색 Agent 전체 설정"""
    milvus: MilvusConfig
    llm: LLMConfig
    embedding: EmbeddingConfig

    @classmethod
    def from_env(cls) -> "ManualSearchConfig":
        """환경 변수에서 설정 로드 (공백 제거 및 기본값 처리)."""
        
        def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
            """환경 변수 조회 (공백 제거)."""
            value = os.getenv(key, default)
            return value.strip() if value else default
        
        # 임베딩 설정
        embedding_model = get_env("EMBEDDING_MODEL") 
        embedding_config = EmbeddingConfig(
            openai_model=embedding_model,
            api_key=get_env("OPENAI_API_KEY", ""),
        )

        default_dimension = 1536
        milvus_dimension = int(get_env("MILVUS_DIMENSION") or str(default_dimension))

        # 하이브리드 검색 설정
        hybrid_search_config = HybridSearchConfig(
            enabled=(get_env("HYBRID_SEARCH_ENABLED", "true") or "true").lower() == "true",
            vector_weight=float(get_env("HYBRID_SEARCH_VECTOR_WEIGHT", "0.5") or "0.5"),
            keyword_weight=float(get_env("HYBRID_SEARCH_KEYWORD_WEIGHT", "0.5") or "0.5"),
            use_rrf=(get_env("HYBRID_SEARCH_USE_RRF", "false") or "false").lower() == "true",
            rrf_k=int(get_env("HYBRID_SEARCH_RRF_K", "60") or "60"),
            vector_expansion_factor=float(get_env("HYBRID_SEARCH_VECTOR_EXPANSION_FACTOR", "5.0") or "5.0"),
            keyword_expansion_factor=float(get_env("HYBRID_SEARCH_KEYWORD_EXPANSION_FACTOR", "2.0") or "2.0"),
            min_score_threshold=float(get_env("HYBRID_SEARCH_MIN_SCORE_THRESHOLD", "0.3") or "0.3"),
            core_keyword_bonus_per_match=float(get_env("HYBRID_SEARCH_CORE_KEYWORD_BONUS", "2.0") or "2.0"),
            core_keyword_penalty_ratio=float(get_env("HYBRID_SEARCH_CORE_KEYWORD_PENALTY", "0.5") or "0.5"),
        )

        # 컬렉션 이름: MENUAL_COLLECTION 우선, 그 다음 MANUAL_COLLECTION, MILVUS_COLLECTION_NAME_MANUAL, 마지막으로 기본값
        collection_name = (
            get_env("MENUAL_COLLECTION") 
        )
        
        # Milvus 호스트 및 포트 (공백 제거 및 기본값 제공)
        milvus_host = get_env("HOST")
        milvus_port = int(get_env("MILVUS_PORT"))
        
        # LLM 설정
        openai_api_key = get_env("OPENAI_API_KEY")
        openai_model = get_env("OPENAI_MODEL")
        
        return cls(
            milvus=MilvusConfig(
                host=milvus_host,
                port=milvus_port,
                collection_name=collection_name,
                dimension=milvus_dimension,
                top_k=int(get_env("MILVUS_TOP_K", "10") or "10"),  # 기본값 5 → 10으로 증가
                embedding_field=get_env("MILVUS_EMBEDDING_FIELD", "embedding") or "embedding",
                document_field=get_env("MILVUS_DOCUMENT_FIELD", "content") or "content",
                metadata_field=get_env("MILVUS_METADATA_FIELD", "metadata") or "metadata",
                metric_type=get_env("MILVUS_METRIC_TYPE", "L2") or "L2",
                hybrid_search=hybrid_search_config,
            ),
            llm=LLMConfig(
                api_key=openai_api_key,
                model=openai_model,
                temperature=float(get_env("OPENAI_TEMPERATURE", "0.7") or "0.7"),
                max_tokens=int(get_env("OPENAI_MAX_TOKENS", "1000") or "1000"),
            ),
            embedding=embedding_config,
        )


# ============================================================================
# Components 클래스들
# ============================================================================

class Embedder:
    """텍스트를 벡터로 변환하는 임베딩 생성기

    OpenAI API를 사용합니다.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small"
    ) -> None:
        """초기화

        Args:
            api_key: OpenAI API 키
            model: OpenAI 임베딩 모델명
        """
        if not api_key:
            raise ValueError("OpenAI API 키가 필요합니다.")

        self.client = OpenAI(api_key=api_key)
        self.model = model
        if "3-small" in model:
            self.dimension = 1536
        elif "3-large" in model:
            self.dimension = 3072
        else:
            self.dimension = 1536

    def embed(self, text: str) -> List[float]:
        """텍스트를 벡터로 변환

        Args:
            text: 임베딩할 텍스트

        Returns:
            벡터 리스트
        """
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            raise RuntimeError(f"임베딩 생성 실패: {e}")


class KeywordSearcher:
    """BM25 기반 키워드 검색기

    벡터 검색과 함께 사용하여 하이브리드 검색을 구현합니다.
    """

    def __init__(self, documents: List[str], document_ids: List[Any]) -> None:
        """초기화

        Args:
            documents: 검색 대상 문서 리스트
            document_ids: 각 문서의 ID 리스트 (documents와 같은 순서)
        """
        if len(documents) != len(document_ids):
            raise ValueError("documents와 document_ids의 길이가 일치해야 합니다.")

        self.documents = documents
        self.document_ids = document_ids

        tokenized_docs = [self._tokenize(doc) for doc in documents]
        self.bm25 = BM25Okapi(tokenized_docs)

    def _tokenize(self, text: str) -> List[str]:
        """텍스트를 토큰으로 분리 (한국어 및 영어 지원)

        Args:
            text: 토큰화할 텍스트

        Returns:
            토큰 리스트
        """
        if not text:
            return []

        # 한국어 단어(가-힣)와 영어/숫자 단어를 모두 추출
        # 한국어는 2글자 이상, 영어/숫자는 \b 경계 사용
        tokens = []
        # 한글 추출 (2글자 이상)
        korean_tokens = re.findall(r'[가-힣]{2,}', text)
        tokens.extend([t.lower() for t in korean_tokens])
        
        # 영어/숫자 단어 추출
        english_tokens = re.findall(r'\b[a-zA-Z0-9]+\b', text.lower())
        tokens.extend(english_tokens)
        
        return tokens

    @staticmethod
    def extract_keywords(text: str, min_length: int = 2) -> List[str]:
        """텍스트에서 키워드 추출 (한글, 영문, 숫자)

        Args:
            text: 키워드를 추출할 텍스트
            min_length: 최소 키워드 길이

        Returns:
            추출된 키워드 리스트 (상위 18개)
        """
        pattern = r'[가-힣a-zA-Z0-9]+'
        keywords = re.findall(pattern, text.lower())
        keywords = [kw for kw in keywords if len(kw) >= min_length]
        keyword_counts = Counter(keywords)
        return [kw for kw, _ in keyword_counts.most_common(18)]

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """키워드 검색

        Args:
            query: 검색 쿼리 텍스트
            top_k: 상위 k개 결과 반환

        Returns:
            검색 결과 리스트 (각 결과는 id, score, document 포함)
        """
        if not query or not query.strip():
            return []

        tokenized_query = self._tokenize(query)
        if not tokenized_query:
            return []

        scores = self.bm25.get_scores(tokenized_query)

        top_indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True
        )[:top_k]

        results = []
        for idx in top_indices:
            score = scores[idx]
            if score > 0:
                results.append({
                    "id": self.document_ids[idx],
                    "score": float(score),
                    "document": self.documents[idx],
                })

        return results


class AnswerGenerator:
    """LLM을 사용한 답변 생성기"""

    def __init__(self, config: LLMConfig) -> None:
        """초기화

        Args:
            config: LLM 설정
        """
        self.config = config
        self.client = OpenAI(api_key=config.api_key)

    def generate_prompt(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """프롬프트 생성

        Args:
            query: 사용자 질의
            documents: 검색된 문서 리스트
            conversation_history: 이전 대화 히스토리 (선택적)

        Returns:
            생성된 프롬프트
        """
        document_texts = []
        for i, doc in enumerate(documents, 1):
            doc_text = doc.get("document", "").strip()
            if not doc_text:
                logger.warning(f"[AnswerGenerator] 문서 {i}의 내용이 비어있습니다.")
                continue
            
            doc_header = f"[문서 {i}]"
            document_texts.append(f"{doc_header}\n{doc_text}")

        if not document_texts:
            return f"""사용자 질문: {query}

            위 질문에 대한 관련 문서를 찾을 수 없습니다.
            정중하게 관련 문서가 없다고 알려주세요."""

        documents_section = "\n\n".join(document_texts)
        
        # 이전 대화 히스토리 추가
        history_section = ""
        if conversation_history:
            history_messages = []
            for msg in conversation_history[-5:]:  # 최근 5개 메시지만 사용
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role and content:
                    role_kr = "사용자" if role == "user" else "어시스턴트"
                    history_messages.append(f"{role_kr}: {content}")
            if history_messages:
                history_section = f"""

        **이전 대화 내용:**
        {chr(10).join(history_messages)}

        **현재 질문:** {query}

        위 이전 대화 내용을 참고하여 자연스럽고 대화하듯이 답변하세요. 사용자가 이전에 언급한 내용을 기억하고 있다는 것을 보여주세요."""

        prompt = f"""당신은 실무형 고성능 AI 어시스턴트입니다.

        **역할:**
        - 인사 정책 매뉴얼 문서를 참고해 사용자에게 유용한 답변을 제공합니다.
        - 이전 대화 내용을 기억하고 자연스럽게 대화하듯이 답변합니다.

        **참고 문서:**
        {documents_section}
        {history_section if history_section else f"**사용자 질문:** {query}"}

        **답변 작성 지침:**
        1) 제공된 문서에서 질문과 관련된 내용을 찾아 답변하세요. 문서 내용을 최대한 활용하세요.
        2) 질문의 핵심 키워드(예: "출근", "연차", "휴가", "연차사용", "연차 사용" 등)가 문서에 언급되어 있으면, 그 내용을 바탕으로 답변하세요.
        3) 문서 내용이 질문과 직접적으로 관련이 없어도, 간접적으로 관련이 있거나 유사한 내용이 있으면 그 내용을 참고하여 답변하세요.
        4) 문서에서 구체적인 정보(숫자, 날짜, 절차 등)를 찾을 수 있으면 반드시 포함하세요.
        5) 문서에 질문과 관련된 내용이 조금이라도 있으면, 그 내용을 바탕으로 답변하세요. "제공된 문서에는 관련 내용이 없습니다"라고 말하는 것은 정말로 전혀 관련이 없을 때만 사용하세요.
        6) 여러 문서가 제공되었을 때, 질문과 가장 직접적으로 관련된 문서의 내용을 우선적으로 사용하세요.
        7) 문서의 전체 내용을 검토하여, 부분적으로라도 관련 있는 정보가 있으면 반드시 활용하세요. 관련 키워드가 문서에 언급되어 있으면 그 내용을 인용하여 답변하세요.
        8) 이전 대화 내용이 있으면, 그것을 참고하여 자연스럽고 대화하듯이 답변하세요.
        9) 문서에서 질문과 관련된 내용을 찾았으면, 그 내용을 직접 인용하거나 요약하여 답변하세요. 추측하거나 일반적인 답변을 하지 마세요.


        **답변:**"""

        return prompt

    def generate(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """답변 생성

        Args:
            query: 사용자 질의
            documents: 검색된 문서 리스트
            conversation_history: 이전 대화 히스토리 (선택적)

        Returns:
            생성된 답변
        """
        prompt = self.generate_prompt(query, documents, conversation_history)

        try:
            # conversation_history를 messages에 포함
            messages = [
                    {
                        "role": "system",
                        "content":  f"""당신은 인사 정책 및 절차 매뉴얼을 도와주는 AI 어시스턴트입니다.
                        제공된 문서를 기반으로 정확하고 상세한 답변을 제공하세요.
                        답변할 때는 문서의 내용을 직접 인용하거나 참고하여 답변하세요.
                    만약 문서에 없는 내용이라면, 그렇다고 명확히 말씀해주세요.
                    이전 대화 내용을 기억하고 자연스럽게 대화하듯이 답변하세요.""",
                    },
            ]
            
            # 이전 대화 히스토리 추가 (최근 5개)
            if conversation_history:
                for msg in conversation_history[-5:]:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role and content:
                        messages.append({
                            "role": role,
                            "content": content
                        })
            
            messages.append({
                        "role": "user",
                        "content": prompt,
            })
            
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            answer = response.choices[0].message.content
            if answer is None:
                raise RuntimeError("LLM이 답변을 생성하지 못했습니다.")
            answer = answer.strip()
            return answer

        except Exception as e:
            raise RuntimeError(f"답변 생성 실패: {e}")


class DocumentRetriever:
    """MilvusDB를 사용한 문서 검색기"""

    def __init__(self, config: MilvusConfig):
        """초기화

        Args:
            config: Milvus 설정
        """
        self.config = config
        self.collection: Optional[Collection] = None
        self._connect()
        self._load_collection()

    def _connect(self) -> None:
        """Milvus 연결"""
        try:
            logger.info(f"Milvus 연결 중... ({self.config.host}:{self.config.port})")
            connections.connect(
                alias="default",
                host=self.config.host,
                port=self.config.port,
            )
            logger.info(f"✅ Milvus 연결 완료: {self.config.collection_name}")
        except Exception as e:
            logger.error(f"Milvus 연결 실패: {e}")
            raise ConnectionError(f"Milvus 연결 실패: {e}")

    def _load_collection(self) -> None:
        """컬렉션 로드"""
        try:
            if utility.has_collection(self.config.collection_name):
                self.collection = Collection(self.config.collection_name)
                self.collection.load()
                logger.info(f"✅ 컬렉션 로드 완료: {self.config.collection_name}")
            else:
                # 사용 가능한 컬렉션 목록 조회
                available_collections = utility.list_collections()
                error_msg = (
                    f"컬렉션 '{self.config.collection_name}'이 존재하지 않습니다.\n"
                    f"사용 가능한 컬렉션: {', '.join(available_collections) if available_collections else '없음'}\n"
                    f"환경변수 MENUAL_COLLECTION, MANUAL_COLLECTION, 또는 MILVUS_COLLECTION_NAME_MANUAL을 확인하세요."
                )
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)
        except ValueError:
            # ValueError는 그대로 전달 (이미 포맷된 메시지)
            raise
        except Exception as e:
            error_msg = f"컬렉션 로드 실패: {e}"
            logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)

    def search(
        self,
        query_vector: List[float],
        top_k: Optional[int] = None,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """문서 검색

        Args:
            query_vector: 검색 쿼리 벡터
            top_k: 상위 k개 문서 (기본값: config.top_k)
            filter_expr: 필터 표현식 (선택사항)

        Returns:
            검색된 문서 리스트 (각 문서는 id, distance, document 등의 정보 포함)
        """
        if self.collection is None:
            raise RuntimeError("컬렉션이 로드되지 않았습니다.")

        top_k = top_k or self.config.top_k

        schema = self.collection.schema
        existing_fields = [field.name for field in schema.fields]

        output_fields = []
        if self.config.document_field in existing_fields:
            output_fields.append(self.config.document_field)
        else:
            raise ValueError(
                f"컬렉션에 '{self.config.document_field}' 필드가 존재하지 않습니다. "
                f"존재하는 필드: {existing_fields}"
            )

        search_params = {
            "metric_type": self.config.metric_type,
            "params": {"nprobe": 10},
        }

        results = self.collection.search(
            data=[query_vector],
            anns_field=self.config.embedding_field,
            param=search_params,
            limit=top_k,
            expr=filter_expr,
            output_fields=output_fields if output_fields else None,
        )

        documents = []
        filtered_count = 0
        for hit in results[0]:
            # 거리가 너무 큰 결과는 필터링 (임계값: 3.0으로 완화)
            # L2 distance의 경우, 거리가 클수록 유사도가 낮음
            # 하지만 너무 엄격한 필터링은 피해야 함
            if hit.distance > 3.0:
                filtered_count += 1
                logger.debug(f"[DocumentRetriever] 거리 {hit.distance:.4f}가 너무 커서 필터링됨 (임계값: 3.0)")
                continue

            normalized_score = 1 / (1 + hit.distance)
            if hit.distance > 0:
                adjusted_score = normalized_score * (1 - min(hit.distance / 3.0, 0.3))
            else:
                adjusted_score = 1.0

            doc = {
                "id": hit.id,
                "distance": hit.distance,
                "score": adjusted_score,
                "document": hit.entity.get(self.config.document_field, ""),
                "metadata": {},  # metadata 필드 사용 안 함
            }
            documents.append(doc)

        logger.info(
            f"[DocumentRetriever] 검색 완료: {len(documents)}개 유효한 문서 "
            f"(전체 결과: {len(results[0])}개, 필터링됨: {filtered_count}개)"
        )
        if documents:
            logger.info(f"[DocumentRetriever] 최상위 문서 거리: {documents[0].get('distance', 0):.4f}, 점수: {documents[0].get('score', 0):.4f}")
        return documents

    def hybrid_search(
        self,
        query_vector: List[float],
        query_text: str,
        top_k: Optional[int] = None,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """하이브리드 검색 (벡터 + 키워드)

        Args:
            query_vector: 검색 쿼리 벡터
            query_text: 검색 쿼리 텍스트
            top_k: 상위 k개 문서
            filter_expr: 필터 표현식

        Returns:
            하이브리드 검색 결과
        """
        if not self.config.hybrid_search or not self.config.hybrid_search.enabled:
            return self.search(query_vector, top_k, filter_expr)

        top_k = top_k or self.config.top_k

        vector_expansion = int(top_k * self.config.hybrid_search.vector_expansion_factor)
        vector_results = self.search(query_vector, vector_expansion, filter_expr)
        logger.info(f"[Hybrid Search] 벡터 검색 결과: {len(vector_results)}개")

        if not vector_results:
            logger.warning("[Hybrid Search] 벡터 검색 결과가 없어 하이브리드 검색 중단")
            return []

        documents = [doc["document"] for doc in vector_results]
        doc_ids = [doc["id"] for doc in vector_results]

        keyword_searcher = KeywordSearcher(documents, doc_ids)
        keyword_expansion = int(top_k * self.config.hybrid_search.keyword_expansion_factor)
        keyword_results = keyword_searcher.search(query_text, keyword_expansion)
        logger.info(f"[Hybrid Search] 키워드 검색 결과: {len(keyword_results)}개 (쿼리: '{query_text}')")

        core_keywords = self._extract_core_keywords(query_text)

        if core_keywords:
            keyword_results = self._apply_core_keyword_boost(keyword_results, core_keywords)

        if self.config.hybrid_search.use_rrf:
            combined_results = self._combine_with_rrf(vector_results, keyword_results, top_k)
        else:
            combined_results = self._combine_with_weighted_score(
                vector_results,
                keyword_results,
                top_k,
                self.config.hybrid_search.vector_weight,
                self.config.hybrid_search.keyword_weight,
            )
        
        logger.info(f"[Hybrid Search] 최종 통합 결과: {len(combined_results)}개 문서")
        return combined_results

    def _combine_with_rrf(
        self,
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """RRF(Reciprocal Rank Fusion)로 결과 결합"""
        k = self.config.hybrid_search.rrf_k if self.config.hybrid_search else 60

        rrf_scores: Dict[Any, Dict[str, Any]] = {}

        for rank, doc in enumerate(vector_results, 1):
            doc_id = doc["id"]
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {"doc": doc, "rrf_score": 0.0}
            rrf_scores[doc_id]["rrf_score"] += 1.0 / (k + rank)

        for rank, doc in enumerate(keyword_results, 1):
            doc_id = doc["id"]
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {"doc": doc, "rrf_score": 0.0}
            rrf_scores[doc_id]["rrf_score"] += 1.0 / (k + rank)

        final_top_k = min(top_k * 1.4, 7) if top_k <= 5 else top_k
        combined = sorted(
            rrf_scores.values(),
            key=lambda x: x["rrf_score"],
            reverse=True
        )[:int(final_top_k)]

        min_threshold = self.config.hybrid_search.min_score_threshold if self.config.hybrid_search else 0.3
        filtered = [item for item in combined if item["rrf_score"] >= min_threshold]

        results = []
        for item in filtered:
            doc = item["doc"].copy()
            doc["score"] = item["rrf_score"]
            results.append(doc)

        return results

    def _combine_with_weighted_score(
        self,
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        top_k: int,
        vector_weight: float,
        keyword_weight: float,
    ) -> List[Dict[str, Any]]:
        """가중 점수로 결과 결합"""
        combined_scores: Dict[Any, Dict[str, Any]] = {}

        if vector_results:
            vector_scores = [doc["score"] for doc in vector_results]
            max_vector_score = max(vector_scores)
            min_vector_score = min(vector_scores)
            score_range = max_vector_score - min_vector_score if max_vector_score != min_vector_score else 1.0

            for doc in vector_results:
                doc_id = doc["id"]
                normalized_score = (
                    (doc["score"] - min_vector_score) / score_range
                    if score_range > 0 else 0.0
                )
                combined_scores[doc_id] = {
                    "doc": doc,
                    "combined_score": normalized_score * vector_weight,
                }

        if keyword_results:
            keyword_scores = [doc["score"] for doc in keyword_results]
            max_keyword_score = max(keyword_scores)
            min_keyword_score = min(keyword_scores)
            score_range = (
                max_keyword_score - min_keyword_score
                if max_keyword_score != min_keyword_score else 1.0
            )

            for doc in keyword_results:
                doc_id = doc["id"]
                normalized_score = (
                    (doc["score"] - min_keyword_score) / score_range
                    if score_range > 0 else 0.0
                )
                if doc_id in combined_scores:
                    combined_scores[doc_id]["combined_score"] += normalized_score * keyword_weight
                else:
                    combined_scores[doc_id] = {
                        "doc": doc,
                        "combined_score": normalized_score * keyword_weight,
                    }

        final_top_k = min(top_k * 1.4, 7) if top_k <= 5 else top_k
        combined = sorted(
            combined_scores.values(),
            key=lambda x: x["combined_score"],
            reverse=True
        )[:int(final_top_k)]

        min_threshold = self.config.hybrid_search.min_score_threshold if self.config.hybrid_search else 0.3
        filtered = [item for item in combined if item["combined_score"] >= min_threshold]

        results = []
        for item in filtered:
            doc = item["doc"].copy()
            doc["score"] = item["combined_score"]
            results.append(doc)

        return results

    def _extract_core_keywords(self, query: str) -> List[str]:
        """질문에서 핵심 키워드 추출"""
        base_keywords = KeywordSearcher.extract_keywords(query, min_length=2)
        top_keywords = base_keywords[:5]

        words = query.split()
        if len(words) >= 2:
            for i in range(len(words) - 1):
                combined = words[i] + words[i+1]
                if re.match(r'^[가-힣]+$', combined):
                    top_keywords.append(combined)

        return list(set(top_keywords))

    def _apply_core_keyword_boost(
        self,
        keyword_results: List[Dict[str, Any]],
        core_keywords: List[str]
    ) -> List[Dict[str, Any]]:
        """핵심 키워드 매칭 보너스 적용"""
        if not core_keywords:
            return keyword_results

        hybrid_config = self.config.hybrid_search
        bonus_per_match = (
            hybrid_config.core_keyword_bonus_per_match
            if hybrid_config else 2.0
        )
        penalty_ratio = (
            hybrid_config.core_keyword_penalty_ratio
            if hybrid_config else 0.5
        )

        boosted_results = []
        for result in keyword_results:
            doc_text = result.get("document", "").lower()
            score = result.get("score", 0.0)

            matched_keywords = [kw for kw in core_keywords if kw in doc_text]

            if matched_keywords:
                bonus = len(matched_keywords) * bonus_per_match
                score += bonus
            else:
                score *= penalty_ratio

            result["score"] = score
            boosted_results.append(result)

        boosted_results.sort(key=lambda x: x.get("score", 0), reverse=True)

        return boosted_results

    def close(self) -> None:
        """연결 종료

        Note: 현재는 자동으로 호출되지 않지만, 리소스 정리가 필요한 경우
        명시적으로 호출할 수 있습니다.
        """
        if self.collection:
            self.collection.release()
        connections.disconnect("default")


# ============================================================================
# Graph 클래스들 및 함수들
# ============================================================================

class QueryPayload(BaseModel):
    """그래프 진입 시 사용하는 질의 정보."""
    text: str = Field(..., description="사용자 입력 질의")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="질의 생성 시점에 첨부할 메타데이터",
    )


class QueryEmbedding(BaseModel):
    """임베딩이 적용된 질의."""
    text: str = Field(..., description="원본 질의")
    vector: List[float] = Field(..., description="임베딩 벡터")
    model: Optional[str] = Field(default=None, description="임베딩 모델명")


class RetrievedDocument(BaseModel):
    """Milvus에서 검색된 문서 정보."""
    id: Optional[str] = Field(default=None, description="엔터티 ID")
    score: Optional[float] = Field(default=None, description="스코어(재계산)")
    distance: Optional[float] = Field(default=None, description="Milvus distance")
    document: str = Field(..., description="문서 본문")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="문서 메타데이터",
    )


class AnswerPayload(BaseModel):
    """LLM이 생성한 답변 및 관련 정보."""
    text: str = Field(..., description="생성된 답변")
    model: Optional[str] = Field(default=None, description="사용된 LLM 모델명")
    prompt: Optional[str] = Field(default=None, description="생성에 사용된 프롬프트")


class ManualSearchOutput(BaseModel):
    """그래프 최종 출력."""
    query: QueryPayload = Field(..., description="초기 질의 정보")
    answer: AnswerPayload = Field(..., description="LLM 답변")
    sources: List[RetrievedDocument] = Field(
        default_factory=list,
        description="참조 문서 목록",
    )
    raw_documents: List[RetrievedDocument] = Field(
        default_factory=list,
        description="검색된 원본 문서 목록",
    )


class ManualSearchState(BaseModel):
    """LangGraph 노드 간에 공유하는 상태."""
    query: QueryPayload = Field(..., description="사용자 질의 정보")
    embedding: Optional[QueryEmbedding] = Field(
        default=None,
        description="임베딩된 질의",
    )
    documents: list[RetrievedDocument] = Field(
        default_factory=list,
        description="검색된 문서 리스트",
    )
    answer: Optional[AnswerPayload] = Field(
        default=None,
        description="LLM 답변 정보",
    )
    final_result: Optional[ManualSearchOutput] = Field(
        default=None,
        description="그래프 최종 출력",
    )
    conversation_history: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="이전 대화 히스토리 (Redis 숏텀 메모리)",
    )

    model_config = {
        "arbitrary_types_allowed": True,
    }

    @model_validator(mode="after")
    def _normalize_fields(self) -> "ManualSearchState":
        if isinstance(self.query, str):
            self.query = QueryPayload(text=self.query)
        return self


class LoadQueryNode(RunnableLambda):
    """그래프 진입 시 질의를 검증하고 상태를 초기화하는 노드."""
    def __init__(self):
        super().__init__(self._run)

    @staticmethod
    def _ensure_state(
        state: Union[ManualSearchState, Dict[str, Any]],
    ) -> ManualSearchState:
        if isinstance(state, ManualSearchState):
            return state
        if isinstance(state, dict):
            return ManualSearchState(**state)
        raise ValueError("ManualSearchState 또는 dict 형태의 입력이 필요합니다.")

    def _normalize_query(self, payload: QueryPayload) -> QueryPayload:
        text = payload.text.strip()
        if not text:
            raise ValueError("질의가 비어 있습니다.")
        metadata = payload.metadata or {}
        return QueryPayload(text=text, metadata=metadata)

    def _run(
        self,
        state: Union[ManualSearchState, Dict[str, Any]],
    ) -> ManualSearchState:
        state_obj = self._ensure_state(state)
        query_field = state_obj.query
        if isinstance(query_field, str):
            state_obj.query = self._normalize_query(QueryPayload(text=query_field))
        elif isinstance(query_field, QueryPayload):
            state_obj.query = self._normalize_query(query_field)
        else:
            raise ValueError("query 필드는 문자열 또는 QueryPayload 여야 합니다.")

        state_obj.embedding = None
        state_obj.documents = []
        state_obj.answer = None
        state_obj.final_result = None

        return state_obj


class EmbedQueryNode(RunnableLambda):
    """사용자 질의를 임베딩으로 변환하는 LangGraph 노드."""
    def __init__(self, embedder: Embedder):
        self._embedder = embedder
        super().__init__(self._run)

    def _run(self, state: ManualSearchState) -> ManualSearchState:
        payload = state.query
        vector = self._embedder.embed(payload.text)
        state.embedding = QueryEmbedding(
            text=payload.text,
            vector=vector,
            model=self._embedder.model,
        )
        return state


class RetrievalNode(RunnableLambda):
    """Milvus에서 문서를 검색하는 노드."""
    def __init__(self, retriever: DocumentRetriever, top_k: Optional[int] = None):
        self._retriever = retriever
        self._top_k = top_k
        super().__init__(self._run)

    def _run(self, state: ManualSearchState) -> ManualSearchState:
        embedding = state.embedding
        if embedding is None:
            raise ValueError("embedding 정보가 존재하지 않습니다.")

        query_text = state.query.text

        if (
            self._retriever.config.hybrid_search
            and self._retriever.config.hybrid_search.enabled
        ):
            logger.info(f"[Manual Search] 하이브리드 검색 실행: '{query_text}'")
            results = self._retriever.hybrid_search(
                embedding.vector,
                query_text,
                top_k=self._top_k,
            )
        else:
            logger.info(f"[Manual Search] 벡터 검색 실행: '{query_text}'")
            results = self._retriever.search(
                embedding.vector,
                top_k=self._top_k,
            )

        # 검색 결과 로깅 (더 상세히)
        logger.info(f"[Manual Search] 검색 완료: {len(results)}개 문서 발견")
        if results:
            for i, doc in enumerate(results[:5], 1):  # 상위 5개 로깅
                doc_text = doc.get("document", "") or ""
                # 문서 전체 내용 로깅 (너무 길면 앞부분만)
                preview_text = doc_text[:300] if len(doc_text) > 300 else doc_text
                score = doc.get('score', 0)
                distance = doc.get('distance', 0)
                logger.info(f"  [{i}] Score: {score:.4f}, Distance: {distance:.4f}")
                logger.info(f"      Content: {preview_text}{'...' if len(doc_text) > 300 else ''}")
            
            # 검색 결과 요약
            top_score = results[0].get('score', 0) if results else 0
            top_distance = results[0].get('distance', 0) if results else 0
            logger.info(f"[Manual Search] 최상위 결과 - Score: {top_score:.4f}, Distance: {top_distance:.4f}")
        else:
            logger.warning(f"[Manual Search] ⚠️ 검색 결과가 없습니다! 쿼리: '{query_text}'")

        state.documents = [
            RetrievedDocument(
                id=str(doc.get("id")) if doc.get("id") is not None else None,
                score=doc.get("score"),
                distance=doc.get("distance"),
                document=doc.get("document", ""),
                metadata=doc.get("metadata") or {},
            )
            for doc in results
        ]
        return state


class GenerateAnswerNode(RunnableLambda):
    """검색된 문서를 기반으로 LLM 답변을 생성하는 노드."""
    def __init__(self, answer_generator: AnswerGenerator):
        self._generator = answer_generator
        super().__init__(self._run)

    def _run(self, state: ManualSearchState) -> ManualSearchState:
        documents = state.documents
        if not documents:
            logger.error(f"[Answer Generation] 검색된 문서가 없습니다. 쿼리: '{state.query.text}'")
            raise ValueError("검색된 문서가 없습니다.")

        query_text = state.query.text
        document_payloads = [doc.model_dump() for doc in documents]
        conversation_history = state.conversation_history
        
        logger.info(f"[Answer Generation] {len(documents)}개 문서로 답변 생성 중: '{query_text}'")
        if conversation_history:
            logger.info(f"[Answer Generation] 이전 대화 히스토리 {len(conversation_history)}개 메시지 활용")
        
        # 검색된 문서 내용 상세 로깅
        for i, doc in enumerate(documents[:3], 1):
            doc_preview = doc.document[:200] if doc.document else ""
            logger.info(f"[Answer Generation] 문서 {i} (Score: {doc.score:.4f}): {doc_preview}...")
        
        prompt = self._generator.generate_prompt(query_text, document_payloads, conversation_history)
        
        # 프롬프트도 로깅 (디버깅용, 첫 500자만)
        logger.debug(f"[Answer Generation] 프롬프트 (첫 500자): {prompt[:500]}...")
        
        answer_text = self._generator.generate(query_text, document_payloads, conversation_history)
        
        logger.info(f"[Answer Generation] 답변 생성 완료 (길이: {len(answer_text)}자)")
        logger.info(f"[Answer Generation] 생성된 답변: {answer_text[:300]}{'...' if len(answer_text) > 300 else ''}")

        state.answer = AnswerPayload(
            text=answer_text,
            model=self._generator.config.model,
            prompt=prompt,
        )
        return state


class FormatOutputNode(RunnableLambda):
    """그래프 마지막에 결과를 정리하는 노드."""
    def __init__(self):
        super().__init__(self._run)

    def _run(self, state: ManualSearchState) -> ManualSearchState:
        if state.answer is None:
            raise ValueError("답변이 생성되지 않았습니다.")

        state.final_result = ManualSearchOutput(
            query=state.query,
            answer=state.answer,
            sources=state.documents,
            raw_documents=state.documents,
        )
        return state


def build_manual_search_graph(
    embedder: Embedder,
    retriever: DocumentRetriever,
    answer_generator: AnswerGenerator,
    *,
    top_k: Optional[int] = None,
):
    """ManualSearch 파이프라인을 LangGraph로 구성."""
    graph = StateGraph(ManualSearchState)

    graph.add_node("load_query", LoadQueryNode())
    graph.add_node("embed_query", EmbedQueryNode(embedder))
    graph.add_node("retrieval", RetrievalNode(retriever, top_k=top_k))
    graph.add_node("generate_answer", GenerateAnswerNode(answer_generator))
    graph.add_node("format_output", FormatOutputNode())

    graph.set_entry_point("load_query")
    graph.add_edge("load_query", "embed_query")
    graph.add_edge("embed_query", "retrieval")
    graph.add_edge("retrieval", "generate_answer")
    graph.add_edge("generate_answer", "format_output")
    graph.add_edge("format_output", END)

    return graph.compile()


async def run_manual_search(
    query: str,
    embedder: Embedder,
    retriever: DocumentRetriever,
    answer_generator: AnswerGenerator,
    *,
    top_k: Optional[int] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> ManualSearchOutput:
    """LangGraph 파이프라인을 실행하고 최종 결과를 반환."""
    compiled_graph = build_manual_search_graph(
        embedder=embedder,
        retriever=retriever,
        answer_generator=answer_generator,
        top_k=top_k,
    )
    initial_state = ManualSearchState(
        query=QueryPayload(text=query),
        conversation_history=conversation_history,
    )
    result = await compiled_graph.ainvoke(initial_state)

    if isinstance(result, dict):
        final_state = ManualSearchState(**result)
    else:
        final_state = result

    if final_state.final_result is None:
        raise RuntimeError("그래프 실행 결과가 생성되지 않았습니다.")

    return final_state.final_result


# ============================================================================
# 전역 캐싱 변수 (컴포넌트 재사용)
# ============================================================================

_embedder: Optional[Embedder] = None
_retriever: Optional[DocumentRetriever] = None
_answer_generator: Optional[AnswerGenerator] = None
_config: Optional[ManualSearchConfig] = None


# ============================================================================
# 컴포넌트 초기화 함수
# ============================================================================

def _initialize_components() -> tuple[Embedder, DocumentRetriever, AnswerGenerator, ManualSearchConfig]:
    """컴포넌트 초기화 (싱글톤 패턴).

    Returns:
        (embedder, retriever, answer_generator, config) 튜플
    """
    global _embedder, _retriever, _answer_generator, _config

    if _embedder is not None and _retriever is not None and _answer_generator is not None and _config is not None:
        return _embedder, _retriever, _answer_generator, _config

    try:
        _config = ManualSearchConfig.from_env()

        _embedder = Embedder(
            api_key=_config.embedding.api_key or _config.llm.api_key,
            model=_config.embedding.openai_model,
        )

        _retriever = DocumentRetriever(_config.milvus)

        _answer_generator = AnswerGenerator(_config.llm)

        logger.info("✅ 메뉴얼 검색 Agent 컴포넌트 초기화 완료")

        return _embedder, _retriever, _answer_generator, _config

    except Exception as e:
        logger.error(f"❌ 컴포넌트 초기화 실패: {e}")
        raise RuntimeError(f"메뉴얼 검색 Agent 초기화 실패: {e}")


# ============================================================================
# Agent 메인 함수
# ============================================================================

async def manual_search(
    user_text: str,
    orchestrator_state: Optional[Dict[str, Any]] = None,
) -> Dict:
    """메뉴얼 검색 Agent 메인 함수
    
    Args:
        user_text: 사용자 질의 텍스트
        orchestrator_state: OrchestratorState 딕셔너리 (Redis 숏텀 메모리용)
        
    Returns:
        검색 결과 딕셔너리
    """
    try:
        embedder, retriever, answer_generator, config = _initialize_components()
        
        # conversation_history 추출
        conversation_history = None
        if orchestrator_state:
            conv_history = orchestrator_state.get("conversation_history", [])
            if conv_history:
                # Message 객체를 dict로 변환
                conversation_history = [
                    {
                        "role": msg.get("role", ""),
                        "content": msg.get("content", ""),
                    }
                    for msg in conv_history
                    if isinstance(msg, dict) and msg.get("role") and msg.get("content")
                ]
        
        result = await run_manual_search(
            query=user_text,
            embedder=embedder,
            retriever=retriever,
            answer_generator=answer_generator,
            top_k=config.milvus.top_k,
            conversation_history=conversation_history,
        )
        
        # 답변 텍스트 추출
        answer_text = result.answer.text if result.answer and result.answer.text else "답변을 생성할 수 없습니다."
        
        # Redis에 검색 컨텍스트 및 답변 저장
        if orchestrator_state is not None:
            # qa_search_context 초기화 (None 체크 포함)
            if "qa_search_context" not in orchestrator_state or orchestrator_state["qa_search_context"] is None:
                orchestrator_state["qa_search_context"] = {}
            if not isinstance(orchestrator_state.get("qa_search_context"), dict):
                orchestrator_state["qa_search_context"] = {}
            
            # 검색 컨텍스트 저장
            orchestrator_state["qa_search_context"]["manual"] = {
                "last_query": user_text,
                "last_answer": answer_text,
                "retrieved_contexts": [doc.document for doc in result.sources][:3],
            }
            
            # conversation_history에 답변 추가 (Redis 저장용)
            if "conversation_history" not in orchestrator_state:
                orchestrator_state["conversation_history"] = []
            if not isinstance(orchestrator_state.get("conversation_history"), list):
                orchestrator_state["conversation_history"] = []
            
            # 사용자 메시지가 없으면 추가
            conversation_history = orchestrator_state["conversation_history"]
            if not conversation_history or conversation_history[-1].get("role") != "user":
                from datetime import datetime
                conversation_history.append({
                    "role": "user",
                    "content": user_text,
                    "timestamp": datetime.now().isoformat()
                })
            
            # Assistant 답변 추가
            from datetime import datetime
            conversation_history.append({
                "role": "assistant",
                "content": answer_text,
                "timestamp": datetime.now().isoformat()
            })
        
        return {
            "ok": True,
            "message": answer_text,
            "retrieved_contexts": [doc.document for doc in result.sources][:3],  # 상위 3개 문서 내용만
            "echo": user_text
        }
    except Exception as e:
        logger.error(f"메뉴얼 검색 실패: {e}", exc_info=True)
        return {
            "ok": False,
            "error": str(e),
            "message": f"검색 중 오류가 발생했습니다: {str(e)}",
            "echo": user_text
        }
