"""채용 데이터 - 벡터 DB 검색 로직."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import os

# OpenAI 임베딩 생성기 사용 (1536차원)
try:
    from agents.embedding_utils import get_embedding_generator
    EMBEDDING_UTILS_AVAILABLE = True
except ImportError:
    EMBEDDING_UTILS_AVAILABLE = False
    get_embedding_generator = None


def get_embedding_model():
    """임베딩 모델을 가져오거나 초기화합니다 (OpenAI Embedding Generator 사용)."""
    if not EMBEDDING_UTILS_AVAILABLE:
        raise ImportError(
            "agents.embedding_utils를 가져올 수 없습니다. "
            "OpenAI API 키가 설정되어 있는지 확인해주세요."
        )
    return get_embedding_generator()


def ensure_milvus_collection(collection_name: str = "hr_data_embeddings"):
    """Milvus 컬렉션이 존재하는지 확인하고 없으면 생성합니다.
    
    Args:
        collection_name: 컬렉션 이름 (기본값: hr_data_embeddings)
    
    Returns:
        Milvus Collection 객체
    """
    try:
        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
    except ImportError:
        raise ImportError("pymilvus가 설치되지 않았습니다. pip install pymilvus를 실행해주세요.")
    
    # Milvus 연결 설정
    MILVUS_HOST = os.getenv("HOST") or os.getenv("MILVUS_HOST")
    MILVUS_PORT = os.getenv("MILVUS_PORT") or "19530"
    
    # Milvus 연결
    if not connections.has_connection("default"):
        connections.connect("default", host=MILVUS_HOST, port=int(MILVUS_PORT))
    
    # 컬렉션이 존재하지 않으면 생성
    if not utility.has_collection(collection_name):
        # OpenAI 임베딩 차원 확인 (1536차원)
        embedding_dim = 1536
        try:
            if EMBEDDING_UTILS_AVAILABLE:
                embedding_generator = get_embedding_generator()
                embedding_dim = embedding_generator.dimension
        except Exception:
            pass  # 기본값 1536 사용
        
        # 필드 스키마 정의
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="document_title", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=10000),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dim),  # OpenAI text-embedding-3-small: 1536차원
        ]
        
        schema = CollectionSchema(fields=fields, description="Resume vectors for recruitment")
        collection = Collection(name=collection_name, schema=schema)
        
        # 인덱스 생성
        index_params = {
            "metric_type": "L2",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 1024}
        }
        collection.create_index(field_name="embedding", index_params=index_params)
    else:
        collection = Collection(name=collection_name)
    
    collection.load()
    return collection


async def search_resumes_from_vector_db(
    query: str,
    limit: int = 10,
    collection_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """벡터 DB에서 이력서 검색.
    
    Args:
        query: 검색 쿼리
        limit: 검색 결과 개수
        collection_name: Milvus 컬렉션 이름 (없으면 환경변수 또는 기본값 사용)
    
    Returns:
        검색된 이력서 리스트 (각 항목은 document_title, chunk_text, score 포함)
    """
    try:
        from pymilvus import Collection, utility
        
        # 컬렉션 이름 결정
        if not collection_name:
            collection_name = os.getenv("RECRUITMENT_COLLECTION") or "hr_data_embeddings"
        
        print(f"🔍 [벡터 DB 검색] 컬렉션: {collection_name}, 쿼리: {query[:50]}...")
        
        # Milvus 컬렉션 가져오기
        collection = ensure_milvus_collection(collection_name)
        
        # 컬렉션 상태 확인
        if not utility.has_collection(collection_name):
            print(f"⚠️ [벡터 DB 검색] 컬렉션 '{collection_name}'이 존재하지 않습니다.")
            return []
        
        # 컬렉션 엔티티 수 확인
        collection.load()
        num_entities = collection.num_entities
        print(f"📊 [벡터 DB 검색] 컬렉션 '{collection_name}'의 엔티티 수: {num_entities}")
        
        if num_entities == 0:
            print(f"⚠️ [벡터 DB 검색] 컬렉션 '{collection_name}'이 비어있습니다.")
            print(f"   → 이력서를 먼저 업로드해야 검색이 가능합니다.")
            print(f"   → 컬렉션 이름을 확인하세요: {collection_name}")
            print(f"   → 환경변수 RECRUITMENT_COLLECTION을 확인하세요.")
            return []
        
        # 쿼리 임베딩 생성 (OpenAI API 사용)
        print(f"🔢 [벡터 DB 검색] 쿼리 임베딩 생성 중... (OpenAI API)")
        embedding_generator = get_embedding_model()
        query_embedding = embedding_generator.embed(query)
        print(f"✅ [벡터 DB 검색] 임베딩 생성 완료 (차원: {len(query_embedding)})")
        
        # 벡터 검색
        print(f"🔍 [벡터 DB 검색] 벡터 검색 실행 중... (limit: {limit})")
        search_results = collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=limit,
            output_fields=["document_title", "chunk_text"]
        )
        
        print(f"📋 [벡터 DB 검색] 검색 결과 수: {len(search_results[0]) if search_results and len(search_results) > 0 else 0}")
        
        # 결과 변환
        resumes = []
        if search_results and len(search_results) > 0:
            for hit in search_results[0]:
                distance = hit.distance
                score = 1.0 / (1.0 + distance)  # 거리를 점수로 변환 (0~1)
                
                document_title = hit.entity.get("document_title", "제목 없음")
                chunk_text = hit.entity.get("chunk_text", "")
                
                # document_title이 "이력서"로만 저장되어 있으면 chunk_text에서 실제 제목 추출
                if document_title == "이력서" or document_title == "제목 없음":
                    # chunk_text에서 "=== RESUME XX ===" 패턴 추출
                    import re
                    resume_match = re.search(r'===?\s*RESUME\s+(\d+)\s*===?', chunk_text, re.IGNORECASE)
                    if resume_match:
                        resume_num = resume_match.group(1)
                        document_title = f"=== RESUME {resume_num} ==="
                    else:
                        # 첫 줄에서 제목 추출 시도
                        first_line = chunk_text.split('\n')[0].strip()
                        if first_line and len(first_line) < 100:
                            document_title = first_line
                
                resume = {
                    "document_title": document_title,
                    "chunk_text": chunk_text,
                    "score": score,
                    "distance": distance,
                }
                resumes.append(resume)
                print(f"  - {resume['document_title']} (점수: {score:.4f}, 거리: {distance:.4f})")
        
        print(f"✅ [벡터 DB 검색] 총 {len(resumes)}건의 이력서를 찾았습니다.")
        return resumes
        
    except Exception as e:
        print(f"❌ 벡터 DB 검색 오류: {e}")
        import traceback
        traceback.print_exc()
        return []

