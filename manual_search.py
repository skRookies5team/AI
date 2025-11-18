"""메뉴얼 검색 시스템 통합 모듈

이 파일은 다음 파일들을 통합한 것입니다:
- config.py (설정 클래스들)
- components.py (Embedder, KeywordSearcher, AnswerGenerator, DocumentRetriever)
- graph.py (LangGraph 관련 클래스 및 함수들)
"""

# ============================================================================
# Imports
# ============================================================================

from typing import Any, Dict, List, Optional, Union
import re
import logging
import json
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
    host: str = Field(default="175.120.35.228", description="Milvus 호스트")
    port: int = Field(default=19530, description="Milvus 포트")
    collection_name: str = Field(default="Employee_Menual_embeddings", description="컬렉션 이름")
    dimension: int = Field(default=1024, description="벡터 차원")
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
        """환경 변수에서 설정 로드"""
        # 임베딩 설정
        embedding_config = EmbeddingConfig(
            openai_model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
        )
        
        # Milvus 차원은 OpenAI 모델 기준으로 설정 (기본값: 1536)
        default_dimension = 1536
        milvus_dimension = int(os.getenv("MILVUS_DIMENSION", str(default_dimension)))
        
        # 하이브리드 검색 설정
        hybrid_search_config = HybridSearchConfig(
            enabled=os.getenv("HYBRID_SEARCH_ENABLED", "false").lower() == "true",
            vector_weight=float(os.getenv("HYBRID_SEARCH_VECTOR_WEIGHT", "0.5")),
            keyword_weight=float(os.getenv("HYBRID_SEARCH_KEYWORD_WEIGHT", "0.5")),
            use_rrf=os.getenv("HYBRID_SEARCH_USE_RRF", "false").lower() == "true",
            rrf_k=int(os.getenv("HYBRID_SEARCH_RRF_K", "60")),
            vector_expansion_factor=float(os.getenv("HYBRID_SEARCH_VECTOR_EXPANSION_FACTOR", "5.0")),
            keyword_expansion_factor=float(os.getenv("HYBRID_SEARCH_KEYWORD_EXPANSION_FACTOR", "2.0")),
            min_score_threshold=float(os.getenv("HYBRID_SEARCH_MIN_SCORE_THRESHOLD", "0.3")),
            core_keyword_bonus_per_match=float(os.getenv("HYBRID_SEARCH_CORE_KEYWORD_BONUS", "2.0")),
            core_keyword_penalty_ratio=float(os.getenv("HYBRID_SEARCH_CORE_KEYWORD_PENALTY", "0.5")),
        )
        
        return cls(
            milvus=MilvusConfig(
                host=os.getenv("MILVUS_HOST", "175.120.35.228"),
                port=int(os.getenv("MILVUS_PORT", "19530")),
                collection_name=os.getenv("MILVUS_COLLECTION_NAME", "Employee_Menual_embeddings"),
                dimension=milvus_dimension,
                top_k=int(os.getenv("MILVUS_TOP_K", "5")),
                embedding_field=os.getenv("MILVUS_EMBEDDING_FIELD", "embedding"),
                document_field=os.getenv("MILVUS_DOCUMENT_FIELD", "content"),
                metadata_field=os.getenv("MILVUS_METADATA_FIELD", "metadata"),
                metric_type=os.getenv("MILVUS_METRIC_TYPE", "L2"),
                hybrid_search=hybrid_search_config,
            ),
            llm=LLMConfig(
                api_key=os.getenv("OPENAI_API_KEY", ""),
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.7")),
                max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "1000")),
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
        # OpenAI 모델별 차원 설정
        if "3-small" in model:
            self.dimension = 1536
        elif "3-large" in model:
            self.dimension = 3072
        else:
            # 기본값 (다른 모델의 경우)
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
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """여러 텍스트를 벡터로 변환
        
        Args:
            texts: 임베딩할 텍스트 리스트
            
        Returns:
            벡터 리스트의 리스트
        """
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            raise RuntimeError(f"배치 임베딩 생성 실패: {e}")


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
        
        # 한국어 및 영어 지원 토큰화
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
        
        # 소문자 변환 후 단어 단위로 분리
        # \w+는 한글, 영문, 숫자를 포함
        tokens = re.findall(r'\b\w+\b', text.lower())
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
        # 한글, 영문, 숫자만 추출
        pattern = r'[가-힣a-zA-Z0-9]+'
        keywords = re.findall(pattern, text.lower())
        # 최소 길이 이상인 키워드만 반환
        keywords = [kw for kw in keywords if len(kw) >= min_length]
        # 빈도수 기반으로 중요 키워드 선택 (상위 18개)
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
        
        # BM25 점수 계산
        scores = self.bm25.get_scores(tokenized_query)
        
        # 상위 k개 인덱스 선택
        top_indices = sorted(
            range(len(scores)), 
            key=lambda i: scores[i], 
            reverse=True
        )[:top_k]
        
        # 결과 생성 (점수가 0보다 큰 것만 포함)
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
    ) -> str:
        """프롬프트 생성
        
        Args:
            query: 사용자 질의
            documents: 검색된 문서 리스트
            
        Returns:
            생성된 프롬프트
        """
        # 문서 내용 추출
        document_texts = []
        for i, doc in enumerate(documents, 1):
            doc_text = doc.get("document", "").strip()
            metadata = doc.get("metadata", {}) # 사용되지 않음
            document_texts.append(f"[문서 {i}]\n{doc_text}")
        
        if not document_texts:
            return f"""사용자 질문: {query}
            
            위 질문에 대한 관련 문서를 찾을 수 없습니다.
            정중하게 관련 문서가 없다고 알려주세요."""

        documents_section = "\n\n".join(document_texts)
        
        prompt = f"""당신은 실무형 고성능 AI 어시스턴트입니다.

        **역할:**
        - 인사 정책 매뉴얼 문서를 참고해 사용자에게 유용한 답변을 제공합니다.

        **참고 문서:**
        {documents_section}

        **사용자 질문:** {query}

        **답변 작성 지침:**
        1) 답변은 2~3줄 핵심 요약 후, 필요 시 단계별 설명을 제공합니다.
        2) 문서 내용의 맥락과 문맥을 고려하여 답변하세요.
        3) 구체적인 숫자, 날짜, 기간, 수량을 묻는 질문이 포함되어 있으면 문서에서 구체적인 숫자나 날짜를 찾아 명확히 답변하세요.
        4) 문서에 구체적인 숫자가 없으면 "제공된 문서에는 구체적인 일수 정보가 없습니다"라고 명확히 말하세요.
        5) 답변에 추가 정보가 필요하면 사용자에게 추가 정보를 요청하는 질문을 하세요.
        6) 여러 문서가 제공되었을 때, 질문과 가장 직접적으로 관련된 문서의 내용을 우선적으로 사용하세요.


        **답변:**"""
        
        return prompt
    
    def generate(
        self,
        query: str,
        documents: List[Dict[str, Any]],
    ) -> str:
        """답변 생성
        
        Args:
            query: 사용자 질의
            documents: 검색된 문서 리스트
            
        Returns:
            생성된 답변
        """
        prompt = self.generate_prompt(query, documents)
        
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {
                        "role": "system",
                        "content":  f"""당신은 인사 정책 및 절차 매뉴얼을 도와주는 AI 어시스턴트입니다.
                        제공된 문서를 기반으로 정확하고 상세한 답변을 제공하세요.
                        답변할 때는 문서의 내용을 직접 인용하거나 참고하여 답변하세요.
                        만약 문서에 없는 내용이라면, 그렇다고 명확히 말씀해주세요.""",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            
            answer = response.choices[0].message.content.strip()
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
            else:
                raise ValueError(f"컬렉션 '{self.config.collection_name}'이 존재하지 않습니다.")
        except Exception as e:
            raise ValueError(f"컬렉션 로드 실패: {e}")
    
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
        
        # 컬렉션 스키마에서 실제 존재하는 필드 확인
        schema = self.collection.schema
        existing_fields = [field.name for field in schema.fields]
        
        # output_fields에 존재하는 필드만 포함
        output_fields = []
        if self.config.document_field in existing_fields:
            output_fields.append(self.config.document_field)
        else:
            raise ValueError(
                f"컬렉션에 '{self.config.document_field}' 필드가 존재하지 않습니다. "
                f"존재하는 필드: {existing_fields}"
            )
        
        if self.config.metadata_field in existing_fields:
            output_fields.append(self.config.metadata_field)
        else:
            logger.warning(
                f"⚠️ 경고: 컬렉션에 '{self.config.metadata_field}' 필드가 없습니다. "
                f"메타데이터 없이 진행합니다."
            )
        
        # 검색 파라미터
        search_params = {
            "metric_type": self.config.metric_type,
            "params": {"nprobe": 10},
        }
        
        # 벡터 차원 검증 및 형식 확인
        if not isinstance(query_vector, list):
            raise ValueError(f"query_vector는 list여야 합니다. 현재 타입: {type(query_vector)}")
        
        # 중첩 리스트인지 확인
        if query_vector and isinstance(query_vector[0], list):
            logger.warning("query_vector가 중첩 리스트입니다. 첫 번째 요소를 사용합니다.")
            query_vector = query_vector[0]
        
        vector_dim = len(query_vector)
        logger.debug(f"검색 벡터 차원: {vector_dim}")
        
        # 컬렉션 스키마에서 벡터 필드 차원 확인
        embedding_field = None
        for field in schema.fields:
            if field.name == self.config.embedding_field:
                embedding_field = field
                break
        
        if embedding_field and hasattr(embedding_field, 'params') and 'dim' in embedding_field.params:
            expected_dim = embedding_field.params['dim']
            if vector_dim != expected_dim:
                # 벡터 차원 불일치 처리
                logger.warning(
                    f"⚠️ 벡터 차원 불일치 감지: "
                    f"예상 차원={expected_dim}, 실제 차원={vector_dim}. "
                    f"벡터를 자동으로 조정합니다."
                )
                
                # 차원 축소: PCA 또는 간단한 다운샘플링
                if vector_dim > expected_dim:
                    # 벡터를 expected_dim으로 축소 (간단한 다운샘플링)
                    # 더 정교한 방법(PCA 등)을 원하면 추가 가능
                    step = vector_dim / expected_dim
                    query_vector = [query_vector[int(i * step)] for i in range(expected_dim)]
                    logger.info(f"벡터 차원을 {vector_dim}에서 {expected_dim}으로 축소했습니다.")
                elif vector_dim < expected_dim:
                    # 벡터를 expected_dim으로 확장 (제로 패딩)
                    padding = [0.0] * (expected_dim - vector_dim)
                    query_vector = query_vector + padding
                    logger.info(f"벡터 차원을 {vector_dim}에서 {expected_dim}으로 확장했습니다.")
                else:
                    # 이미 같음 (이 경우는 발생하지 않아야 함)
                    pass
        
        # 검색 실행
        results = self.collection.search(
            data=[query_vector],
            anns_field=self.config.embedding_field,
            param=search_params,
            limit=top_k,
            expr=filter_expr,
            output_fields=output_fields if output_fields else None,
        )
        
        # 결과 포맷팅
        documents = []
        for hit in results[0]:
            # metadata 처리 (필드가 있으면 파싱, 없으면 빈 dict)
            if self.config.metadata_field in output_fields:
                metadata_str = hit.entity.get(self.config.metadata_field, "{}")
                try:
                    metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else metadata_str
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            else:
                metadata = {}
            
            # 개선된 점수 정규화
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
                "metadata": metadata,
            }
            documents.append(doc)
        
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
        
        # 1. 벡터 검색 (더 많이 가져와서 결합)
        vector_expansion = int(top_k * self.config.hybrid_search.vector_expansion_factor)
        vector_results = self.search(query_vector, vector_expansion, filter_expr)
        
        if not vector_results:
            return []
        
        # 2. 키워드 검색을 위한 문서 준비
        documents = [doc["document"] for doc in vector_results]
        doc_ids = [doc["id"] for doc in vector_results]
        
        # 3. 키워드 검색
        keyword_searcher = KeywordSearcher(documents, doc_ids)
        keyword_expansion = int(top_k * self.config.hybrid_search.keyword_expansion_factor)
        keyword_results = keyword_searcher.search(query_text, keyword_expansion)
        
        # 3-1. 핵심 키워드 추출 및 점수 보정
        core_keywords = self._extract_core_keywords(query_text)
        
        if core_keywords:
            keyword_results = self._apply_core_keyword_boost(keyword_results, core_keywords)
        
        # 4. 결과 결합
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
        
        return combined_results
    
    def _combine_with_rrf(
        self,
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """RRF(Reciprocal Rank Fusion)로 결과 결합"""
        k = self.config.hybrid_search.rrf_k if self.config.hybrid_search else 60
        
        # ID별로 RRF 점수 계산
        rrf_scores: Dict[Any, Dict[str, Any]] = {}
        
        # 벡터 검색 결과의 RRF 점수
        for rank, doc in enumerate(vector_results, 1):
            doc_id = doc["id"]
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {"doc": doc, "rrf_score": 0.0}
            rrf_scores[doc_id]["rrf_score"] += 1.0 / (k + rank)
        
        # 키워드 검색 결과의 RRF 점수
        for rank, doc in enumerate(keyword_results, 1):
            doc_id = doc["id"]
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {"doc": doc, "rrf_score": 0.0}
            rrf_scores[doc_id]["rrf_score"] += 1.0 / (k + rank)
        
        # RRF 점수로 정렬
        final_top_k = min(top_k * 1.4, 7) if top_k <= 5 else top_k
        combined = sorted(
            rrf_scores.values(), 
            key=lambda x: x["rrf_score"], 
            reverse=True
        )[:int(final_top_k)]
        
        # 낮은 점수 문서 필터링
        min_threshold = self.config.hybrid_search.min_score_threshold if self.config.hybrid_search else 0.3
        filtered = [item for item in combined if item["rrf_score"] >= min_threshold]
        
        # 결과 포맷팅
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
        # ID별로 점수 정규화 및 결합
        combined_scores: Dict[Any, Dict[str, Any]] = {}
        
        # 벡터 검색 점수 정규화
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
        
        # 키워드 검색 점수 정규화 및 결합
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
        
        # 결합 점수로 정렬
        final_top_k = min(top_k * 1.4, 7) if top_k <= 5 else top_k
        combined = sorted(
            combined_scores.values(), 
            key=lambda x: x["combined_score"], 
            reverse=True
        )[:int(final_top_k)]
        
        # 낮은 점수 문서 필터링
        min_threshold = self.config.hybrid_search.min_score_threshold if self.config.hybrid_search else 0.3
        filtered = [item for item in combined if item["combined_score"] >= min_threshold]
        
        # 최종 결과 포맷팅
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
        
        # 복합 키워드도 추출
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
        """연결 종료"""
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
            results = self._retriever.hybrid_search(
                embedding.vector,
                query_text,
                top_k=self._top_k,
            )
        else:
            results = self._retriever.search(
                embedding.vector,
                top_k=self._top_k,
            )

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
            raise ValueError("검색된 문서가 없습니다.")

        query_text = state.query.text
        document_payloads = [doc.model_dump() for doc in documents]

        prompt = self._generator.generate_prompt(query_text, document_payloads)
        answer_text = self._generator.generate(query_text, document_payloads)

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


def run_manual_search(
    query: str,
    embedder: Embedder,
    retriever: DocumentRetriever,
    answer_generator: AnswerGenerator,
    *,
    top_k: Optional[int] = None,
) -> ManualSearchOutput:
    """LangGraph 파이프라인을 실행하고 최종 결과를 반환."""
    compiled_graph = build_manual_search_graph(
        embedder=embedder,
        retriever=retriever,
        answer_generator=answer_generator,
        top_k=top_k,
    )
    initial_state = ManualSearchState(query=QueryPayload(text=query))
    result = compiled_graph.invoke(initial_state)

    if isinstance(result, dict):
        final_state = ManualSearchState(**result)
    else:
        final_state = result

    if final_state.final_result is None:
        raise RuntimeError("그래프 실행 결과가 생성되지 않았습니다.")

    return final_state.final_result


# ============================================================================
# Export 목록
# ============================================================================

__all__ = [
    # Config
    "HybridSearchConfig",
    "MilvusConfig",
    "LLMConfig",
    "EmbeddingConfig",
    "ManualSearchConfig",
    # Components
    "Embedder",
    "KeywordSearcher",
    "AnswerGenerator",
    "DocumentRetriever",
    # Graph I/O Models
    "QueryPayload",
    "QueryEmbedding",
    "RetrievedDocument",
    "AnswerPayload",
    "ManualSearchOutput",
    # Graph State
    "ManualSearchState",
    # Graph Nodes
    "LoadQueryNode",
    "EmbedQueryNode",
    "RetrievalNode",
    "GenerateAnswerNode",
    "FormatOutputNode",
    # Graph Functions
    "build_manual_search_graph",
    "run_manual_search",
]


# ============================================================================
# 직접 실행 시 간단한 테스트
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("메뉴얼 검색 시스템 모듈")
    print("=" * 60)
    print("\n이 모듈은 라이브러리입니다.")
    print("사용 방법:")
    print("  1. 다른 스크립트에서 임포트하여 사용")
    print("  2. test_improved_manual_search.py를 실행하여 테스트")
    print("\n예시:")
    print("  from manual_search import ManualSearchConfig, run_manual_search")
    print("  config = ManualSearchConfig.from_env()")
    print("  # ... 컴포넌트 초기화 및 실행")
    print("\n테스트 실행:")
    print("  python test_improved_manual_search.py")
    print("=" * 60)