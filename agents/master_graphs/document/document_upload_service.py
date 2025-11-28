"""문서 업로드 및 Milvus DB 저장 서비스."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

# Optional imports for document processing
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    PyPDF2 = None

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    Document = None

try:
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False
    Collection = None
    CollectionSchema = None
    DataType = None
    FieldSchema = None
    connections = None
    utility = None

# sentence_transformers는 실제 사용 시점에만 import (lazy import)
# 모듈 레벨에서 import하면 transformers 라이브러리 충돌 발생

# Milvus 연결 설정 (환경 변수 또는 기본값)
# HOST 환경 변수 사용 (SCHEDULE_DB_HOST와 MILVUS_HOST를 통합)
MILVUS_HOST = os.getenv("HOST")
MILVUS_PORT = os.getenv("MILVUS_PORT")
MILVUS_COLLECTION_NAME = "document_vectors"

# 임베딩 모델 초기화 (한번만 로드)
_embedding_model = None


def get_embedding_model():
    """임베딩 모델을 가져오거나 초기화합니다."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers가 설치되지 않았습니다. "
                "pip install sentence-transformers를 실행해주세요."
            )
        # 한국어 지원 임베딩 모델 사용
        _embedding_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
    return _embedding_model


def extract_text_from_pdf(file_path: Path) -> str:
    """PDF 파일에서 텍스트를 추출합니다."""
    if not PDF_AVAILABLE:
        raise ImportError("PyPDF2가 설치되지 않았습니다. pip install PyPDF2를 실행해주세요.")
    
    text = ""
    try:
        with open(file_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page in pdf_reader.pages:
                text += page.extract_text() + "\n"
    except Exception as e:
        raise ValueError(f"PDF 텍스트 추출 실패: {str(e)}") from e
    return text.strip()


def extract_text_from_docx(file_path: Path) -> str:
    """DOCX 파일에서 텍스트를 추출합니다."""
    if not DOCX_AVAILABLE:
        raise ImportError("python-docx가 설치되지 않았습니다. pip install python-docx를 실행해주세요.")
    
    text = ""
    try:
        doc = Document(file_path)
        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"
    except Exception as e:
        raise ValueError(f"DOCX 텍스트 추출 실패: {str(e)}") from e
    return text.strip()


def extract_text_from_file(file_path: Path) -> str:
    """파일 확장자에 따라 적절한 텍스트 추출 함수를 호출합니다."""
    extension = file_path.suffix.lower()
    
    if extension == '.pdf':
        return extract_text_from_pdf(file_path)
    elif extension == '.docx':
        return extract_text_from_docx(file_path)
    else:
        raise ValueError(f"지원하지 않는 파일 형식: {extension}")


def split_text_into_chunks(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """텍스트를 청크로 분할합니다."""
    words = text.split()
    chunks = []
    
    for i in range(0, len(words), chunk_size - overlap):
        chunk = ' '.join(words[i:i + chunk_size])
        chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
    
    return chunks


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 100) -> List[str]:
    """텍스트를 청크로 분할합니다 (AI-013 요구사항).
    
    Args:
        text: 분할할 텍스트
        chunk_size: 청크 크기 (단어 수)
        chunk_overlap: 청크 간 겹치는 단어 수
    
    Returns:
        청크 리스트
    """
    return split_text_into_chunks(text, chunk_size=chunk_size, overlap=chunk_overlap)


def upsert_to_milvus(
    file_path: str,
    chunks: List[str],
    embeddings: List[List[float]],
    metadata: Dict[str, Any],
    collection_name: str = MILVUS_COLLECTION_NAME,
) -> Dict[str, Any]:
    """Milvus에 벡터 데이터를 Upsert합니다 (AI-013 요구사항).
    
    Args:
        file_path: 원본 파일 경로
        chunks: 텍스트 청크 리스트
        embeddings: 임베딩 벡터 리스트
        metadata: 추가 메타데이터
        collection_name: Milvus 컬렉션 이름
    
    Returns:
        처리 결과 딕셔너리
    """
    if not MILVUS_AVAILABLE:
        return {
            "ok": False,
            "error": "pymilvus가 설치되지 않았습니다.",
            "message": "pymilvus가 설치되지 않았습니다. pip install pymilvus를 실행해주세요.",
        }
    
    try:
        # Milvus 컬렉션 확인/생성
        collection = ensure_milvus_collection(collection_name)
        
        # 동일한 제목의 기존 문서 삭제 (Upsert)
        document_title = metadata.get("source", Path(file_path).stem)
        delete_existing_document(collection, document_title)
        
        # 데이터 준비
        entities = [
            [document_title] * len(chunks),  # document_title
            chunks,  # chunk_text
            embeddings,  # embedding
        ]
        
        # Milvus에 삽입
        collection.insert(entities)
        collection.flush()
        
        return {
            "ok": True,
            "message": f"문서가 성공적으로 저장되었습니다. ({len(chunks)}개 청크)",
            "document_title": document_title,
            "chunks_count": len(chunks),
            "embeddings_count": len(embeddings),
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(e),
            "message": f"Milvus 업로드 실패: {str(e)}",
        }


def ensure_milvus_collection(collection_name: str = MILVUS_COLLECTION_NAME):
    """Milvus 컬렉션이 존재하는지 확인하고 없으면 생성합니다."""
    if not MILVUS_AVAILABLE:
        raise ImportError("pymilvus가 설치되지 않았습니다. pip install pymilvus를 실행해주세요.")
    
    # Milvus 연결
    if not connections.has_connection("default"):
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
    
    # 컬렉션이 존재하지 않으면 생성
    if not utility.has_collection(collection_name):
        # 필드 스키마 정의
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="document_title", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=10000),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384),  # multilingual-MiniLM-L12-v2 차원
        ]
        
        schema = CollectionSchema(fields=fields, description="Document vectors for RAG")
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


def delete_existing_document(collection: Collection, document_title: str) -> None:
    """동일한 제목의 기존 문서를 삭제합니다."""
    try:
        # document_title 필드로 필터링하여 삭제
        collection.delete(expr=f'document_title == "{document_title}"')
        collection.flush()
    except Exception as e:
        print(f"기존 문서 삭제 중 오류 (무시 가능): {str(e)}")


def upload_document_to_milvus(
    file_path: Path,
    document_title: str,
    collection_name: str = MILVUS_COLLECTION_NAME,
) -> Dict[str, Any]:
    """
    문서를 처리하여 Milvus DB에 저장/업데이트합니다.
    
    Args:
        file_path: 업로드된 파일 경로
        document_title: 문서 제목
        collection_name: Milvus 컬렉션 이름
        
    Returns:
        처리 결과 딕셔너리
    """
    try:
        # 1. 텍스트 추출
        text = extract_text_from_file(file_path)
        if not text.strip():
            raise ValueError("추출된 텍스트가 없습니다.")
        
        # 2. 텍스트를 청크로 분할
        chunks = split_text_into_chunks(text)
        if not chunks:
            raise ValueError("텍스트 청크가 생성되지 않았습니다.")
        
        # 3. Milvus 컬렉션 확인/생성
        collection = ensure_milvus_collection(collection_name)
        
        # 4. 동일한 제목의 기존 문서 삭제
        delete_existing_document(collection, document_title)
        
        # 5. 임베딩 생성
        model = get_embedding_model()
        embeddings = model.encode(chunks, show_progress_bar=False)
        
        # 6. 데이터 준비
        entities = [
            [document_title] * len(chunks),  # document_title
            chunks,  # chunk_text
            embeddings.tolist(),  # embedding
        ]
        
        # 7. Milvus에 삽입
        collection.insert(entities)
        collection.flush()
        
        return {
            "ok": True,
            "message": "문서가 성공적으로 저장되었습니다.",
            "document_title": document_title,
            "chunks_count": len(chunks),
            "text_length": len(text),
        }
        
    except Exception as e:
        return {
            "ok": False,
            "message": f"문서 저장 실패: {str(e)}",
            "document_title": document_title,
        }


def upload_document(
    file_path: str | Path,
    document_title: str | None = None,
) -> Dict[str, Any]:
    """
    문서 파일을 업로드하여 Milvus DB에 저장합니다.
    
    Args:
        file_path: 파일 경로 (str 또는 Path)
        document_title: 문서 제목 (없으면 파일명 사용)
        
    Returns:
        처리 결과 딕셔너리
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        return {
            "ok": False,
            "message": f"파일을 찾을 수 없습니다: {file_path}",
        }
    
    # 문서 제목이 없으면 파일명 사용
    if document_title is None:
        document_title = file_path.stem
    
    return upload_document_to_milvus(file_path, document_title)


def process_uploaded_file(
    file_content: bytes,
    filename: str,
    document_title: str,
) -> Dict[str, Any]:
    """
    업로드된 파일을 처리합니다.
    
    Args:
        file_content: 파일 내용 (bytes)
        filename: 파일명
        document_title: 문서 제목
        
    Returns:
        처리 결과 딕셔너리
    """
    # 임시 파일로 저장
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        tmp_file.write(file_content)
        tmp_path = Path(tmp_file.name)
    
    try:
        # Milvus에 업로드
        result = upload_document_to_milvus(tmp_path, document_title)
        return result
    finally:
        # 임시 파일 삭제
        if tmp_path.exists():
            tmp_path.unlink()
