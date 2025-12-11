"""문서 업데이트 서비스 - 복리후생, 식단, 메뉴얼 파일 업데이트."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# YAML 파일 로드
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None

# Optional imports for document processing
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    PyPDF2 = None

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    pdfplumber = None

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    Document = None

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    RecursiveCharacterTextSplitter = None

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

# PDF 변환 유틸리티 import (lazy import로 순환 참조 방지)
try:
    from agents.master_graphs.document.update.pdf_to_txt_converter import (
        extract_tables_from_pdf,
        format_table_as_text,
    )
    PDF_CONVERTER_AVAILABLE = True
except ImportError:
    PDF_CONVERTER_AVAILABLE = False
    extract_tables_from_pdf = None
    format_table_as_text = None

# Milvus 연결 설정 (환경 변수 또는 기본값)
# HOST 환경 변수 사용 (SCHEDULE_DB_HOST와 MILVUS_HOST를 통합)
MILVUS_HOST = os.getenv("HOST")
MILVUS_PORT = os.getenv("MILVUS_PORT")

# 문서 종류별 컬렉션 이름 (환경 변수에서 읽기)
BENEFIT_COLLECTION = os.getenv("BENEFIT_COLLECTION")
CAFETERIA_COLLECTION = os.getenv("CAFETERIA_COLLECTION")
MENUAL_COLLECTION = os.getenv("MENUAL_COLLECTION")
HR_COLLECTION = os.getenv("HR__COLLECTION")  # 이력서 파일용 Milvus 컬렉션 이름

# 임베딩 생성기 (OpenAI 사용)
_embedding_generator = None


def get_embedding_generator():
    """OpenAI 임베딩 생성기를 가져오거나 초기화합니다."""
    global _embedding_generator
    if _embedding_generator is None:
        try:
            from agents.embedding_utils import get_embedding_generator
            _embedding_generator = get_embedding_generator()
        except Exception as e:
            raise ImportError(
                f"임베딩 생성기 초기화 실패: {e}. "
                "OPENAI_API_KEY와 EMBEDDING_MODEL이 .env 파일에 설정되어 있는지 확인하세요."
            )
    return _embedding_generator


def get_collection_name_by_type(document_type: str) -> str:
    """문서 타입에 따라 Milvus 컬렉션 이름을 반환합니다.
    
    Args:
        document_type: 문서 타입 ("복리후생", "식단", "메뉴얼", "이력서")
        
    Returns:
        컬렉션 이름
    """
    type_mapping = {
        "복리후생": BENEFIT_COLLECTION,
        "식단": CAFETERIA_COLLECTION,  # 식단 파일은 환경 변수에서 지정한 컬렉션 사용
        "메뉴얼": MENUAL_COLLECTION,
        "이력서": HR_COLLECTION,  # 이력서 파일은 환경 변수에서 지정한 컬렉션 사용
    }
    
    # 정확한 매칭 시도
    if document_type in type_mapping:
        return type_mapping[document_type]
    
    # 부분 매칭 시도
    for key, collection_name in type_mapping.items():
        if key in document_type or document_type in key:
            return collection_name
    
    # 기본값 (복리후생)
    return BENEFIT_COLLECTION


def extract_text_from_pdf(file_path: Path, use_table_extraction_only: bool = False) -> str:
    """PDF 파일에서 텍스트를 추출합니다.
    
    pdf_to_txt_converter.py의 함수를 사용하여 표를 우선 추출하고,
    실패 시 PyPDF2로 일반 텍스트를 추출합니다.
    
    Args:
        file_path: PDF 파일 경로
        use_table_extraction_only: True이면 표 추출만 사용 (중복 방지)
        
    Returns:
        추출된 텍스트
    """
    text_parts = []
    
    # 1. pdf_to_txt_converter.py를 사용하여 표 추출 시도 (더 정확한 표 추출)
    if PDF_CONVERTER_AVAILABLE and extract_tables_from_pdf and format_table_as_text:
        try:
            # Path를 str로 변환하여 전달
            tables = extract_tables_from_pdf(str(file_path))
            if tables:
                # 표들을 텍스트로 변환
                table_texts = []
                for table in tables:
                    table_text = format_table_as_text(table)
                    if table_text:
                        table_texts.append(table_text)
                
                if table_texts:
                    text_parts.append("\n\n".join(table_texts))
                    print(f"✅ [PDF Extract] pdf_to_txt_converter로 {len(tables)}개 표 추출 완료")
        except Exception as e:
            print(f"⚠️ [PDF Extract] pdf_to_txt_converter 표 추출 실패: {e}")
    
    # 2. PyPDF2로 일반 텍스트 추출 (표 추출 실패 시 또는 use_table_extraction_only가 False일 때만)
    if not use_table_extraction_only and PDF_AVAILABLE:
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                page_texts = []
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        page_texts.append(page_text.strip())
                
                if page_texts:
                    # 표 추출 결과가 없으면 일반 텍스트만 사용
                    if not text_parts:
                        text_parts.append("\n\n".join(page_texts))
                    else:
                        # 표 추출 결과가 있으면 추가로 일반 텍스트도 포함 (중복 가능성 있음)
                        text_parts.append("\n\n".join(page_texts))
                    
                    print(f"✅ [PDF Extract] PyPDF2로 {len(page_texts)}페이지 텍스트 추출 완료")
        except Exception as e:
            print(f"⚠️ [PDF Extract] PyPDF2 텍스트 추출 실패: {e}")
    
    if not text_parts:
        if not PDF_AVAILABLE and not PDFPLUMBER_AVAILABLE:
            raise ImportError(
                "PDF 처리 라이브러리가 설치되지 않았습니다. "
                "pip install PyPDF2 pdfplumber를 실행하여 설치해주세요."
            )
        raise ValueError("PDF에서 텍스트를 추출할 수 없습니다.")
    
    # 모든 텍스트 부분을 합치기
    full_text = "\n\n".join(text_parts)
    return full_text.strip()


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


def extract_text_from_file(file_path: Path, document_type: Optional[str] = None) -> str:
    """파일 확장자에 따라 적절한 텍스트 추출 함수를 호출합니다.
    
    Args:
        file_path: 파일 경로
        document_type: 문서 타입 (이력서인 경우 표 추출만 사용하여 중복 방지)
    """
    extension = file_path.suffix.lower()
    
    if extension == '.pdf':
        # 이력서 타입인 경우 표 추출만 사용 (중복 방지)
        use_table_only = (document_type == "이력서")
        return extract_text_from_pdf(file_path, use_table_extraction_only=use_table_only)
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


def chunk_resume_by_separator(text: str) -> List[str]:
    """이력서 텍스트를 === RESUME XX === 구분자로 분리합니다.
    
    === RESUME 01 ===와 === RESUME 02 === 사이의 텍스트가 하나의 청크가 됩니다.
    
    Args:
        text: 이력서 텍스트
        
    Returns:
        각 이력서별로 분리된 청크 리스트
    """
    import re
    
    # === RESUME XX === 패턴 찾기 (XX는 숫자)
    pattern = r'=== RESUME \d+ ==='
    
    # 구분자 위치 찾기
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    
    if not matches:
        # 구분자가 없으면 전체 텍스트를 하나의 청크로 반환
        return [text.strip()] if text.strip() else []
    
    chunks = []
    
    # 첫 번째 이력서 (첫 번째 구분자 이전의 텍스트는 제외)
    # 첫 번째 구분자부터 시작
    start_idx = matches[0].start()
    
    # 각 구분자 사이의 텍스트 추출
    for i in range(len(matches)):
        # 현재 구분자 위치
        current_match = matches[i]
        current_start = current_match.start()
        current_end = current_match.end()
        
        # 다음 구분자 위치 (마지막이면 텍스트 끝)
        if i + 1 < len(matches):
            next_start = matches[i + 1].start()
            chunk_text = text[current_start:next_start].strip()
        else:
            # 마지막 이력서
            chunk_text = text[current_start:].strip()
        
        if chunk_text:
            chunks.append(chunk_text)
    
    print(f"📄 [Resume Chunking] {len(chunks)}개의 이력서로 분리 완료")
    
    # Milvus VARCHAR 제한 대응 (10000자)
    MAX_CONTENT_LENGTH = 10000
    final_chunks = []
    for chunk_text in chunks:
        if len(chunk_text) > MAX_CONTENT_LENGTH:
            # 너무 긴 청크는 추가로 분할 (구분자를 유지하면서)
            for start in range(0, len(chunk_text), MAX_CONTENT_LENGTH):
                trimmed = chunk_text[start:start + MAX_CONTENT_LENGTH].strip()
                if trimmed:
                    final_chunks.append(trimmed)
        else:
            final_chunks.append(chunk_text)
    
    return final_chunks


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 100) -> List[str]:
    """텍스트를 청크로 분할합니다.
    
    langchain의 RecursiveCharacterTextSplitter를 사용하여 더 정확한 청킹을 수행합니다.
    langchain이 없으면 기본 분할 방식을 사용합니다.
    
    Args:
        text: 분할할 텍스트
        chunk_size: 청크 크기 (문자 수, langchain 사용 시)
        chunk_overlap: 청크 간 겹치는 문자 수 (langchain 사용 시)
    
    Returns:
        청크 리스트
    """
    # langchain이 있으면 더 정확한 청킹 사용
    if LANGCHAIN_AVAILABLE and RecursiveCharacterTextSplitter:
        try:
            # text-embedding-3-small의 최대 토큰 길이는 8191 토큰
            # 한국어의 경우 대략 1 토큰 = 1-2 문자이므로, 안전하게 4000자 정도로 설정
            # 기본값보다 크면 langchain 사용, 작으면 기본 방식 사용
            if chunk_size >= 1000:
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    length_function=len,
                    separators=["\n\n", "\n", " ", ""]  # 문단 > 줄 > 단어 > 문자 순으로 분할
                )
                chunks = text_splitter.split_text(text)
                chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
                
                # Milvus VARCHAR 제한 대응 (10000자)
                MAX_CONTENT_LENGTH = 10000
                final_chunks = []
                for chunk_text in chunks:
                    if len(chunk_text) > MAX_CONTENT_LENGTH:
                        # 너무 긴 청크는 추가로 분할
                        for start in range(0, len(chunk_text), MAX_CONTENT_LENGTH):
                            trimmed = chunk_text[start:start + MAX_CONTENT_LENGTH].strip()
                            if trimmed:
                                final_chunks.append(trimmed)
                    else:
                        final_chunks.append(chunk_text)
                
                return final_chunks
        except Exception as e:
            print(f"⚠️ langchain 청킹 실패, 기본 방식 사용: {e}")
    
    # 기본 분할 방식 (단어 기준)
    return split_text_into_chunks(text, chunk_size=chunk_size, overlap=chunk_overlap)


def ensure_milvus_collection(collection_name: str):
    """Milvus 컬렉션이 존재하는지 확인하고 없으면 생성합니다.
    
    기존 컬렉션이 있지만 스키마가 맞지 않으면 삭제하고 재생성합니다.
    
    Args:
        collection_name: 컬렉션 이름
        
    Returns:
        Milvus Collection 객체
    """
    if not MILVUS_AVAILABLE:
        raise ImportError("pymilvus가 설치되지 않았습니다. pip install pymilvus를 실행해주세요.")
    
    # Milvus 연결
    if not connections.has_connection("default"):
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
    
    # 임베딩 차원 가져오기 (OpenAI embedding 모델 사용)
    embedding_gen = get_embedding_generator()
    embedding_dim = embedding_gen.dimension
    
    # 필드 스키마 정의
    # QA agent 호환성을 위해 content 필드도 포함 (chunk_text와 동일한 값)
    expected_fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="document_title", dtype=DataType.VARCHAR, max_length=500),
        FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=10000),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=10000),  # QA agent 호환성
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=embedding_dim),
    ]
    
    # 컬렉션이 존재하는지 확인
    if utility.has_collection(collection_name):
        collection = Collection(name=collection_name)
        collection.load()
        
        # 기존 컬렉션의 스키마 확인
        existing_schema = collection.schema
        expected_field_names = {field.name for field in expected_fields if not field.auto_id}
        existing_field_names = {field.name for field in existing_schema.fields if not field.auto_id}
        
        # 스키마가 다르면 컬렉션 삭제 후 재생성
        if expected_field_names != existing_field_names:
            print(f"⚠️ 컬렉션 '{collection_name}'의 스키마가 일치하지 않습니다. 재생성합니다.")
            print(f"   기존 필드: {existing_field_names}")
            print(f"   예상 필드: {expected_field_names}")
            
            # 컬렉션 삭제
            utility.drop_collection(collection_name)
            print(f"✅ 기존 컬렉션 '{collection_name}' 삭제 완료")
            
            # 새로 생성
            schema = CollectionSchema(fields=expected_fields, description="Document vectors for RAG")
            collection = Collection(name=collection_name, schema=schema)
            
            # 인덱스 생성
            index_params = {
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 1024}
            }
            collection.create_index(field_name="embedding", index_params=index_params)
            print(f"✅ 새 컬렉션 '{collection_name}' 생성 완료")
        else:
            print(f"✅ 기존 컬렉션 '{collection_name}' 스키마 확인 완료")
    else:
        # 컬렉션이 없으면 생성
        schema = CollectionSchema(fields=expected_fields, description="Document vectors for RAG")
        collection = Collection(name=collection_name, schema=schema)
        
        # 인덱스 생성
        index_params = {
            "metric_type": "L2",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 1024}
        }
        collection.create_index(field_name="embedding", index_params=index_params)
        print(f"✅ 새 컬렉션 '{collection_name}' 생성 완료")
    
    collection.load()
    return collection


def delete_all_documents_in_collection(collection: Collection) -> None:
    """컬렉션의 모든 문서를 삭제합니다.
    
    Args:
        collection: Milvus Collection 객체
    """
    try:
        # 컬렉션의 모든 문서 삭제 (document_title이 비어있지 않은 모든 문서)
        # 또는 더 간단하게: 컬렉션의 모든 데이터 삭제
        # Milvus에서는 expr을 사용하여 모든 문서를 삭제할 수 있습니다
        # "id >= 0" 같은 표현식으로 모든 문서 선택
        collection.delete(expr='id >= 0')
        collection.flush()
        print(f"✅ 컬렉션 '{collection.name}'의 모든 문서 삭제 완료")
    except Exception as e:
        print(f"⚠️ 컬렉션의 모든 문서 삭제 중 오류 (무시 가능): {str(e)}")


def upsert_to_milvus(
    file_path: str,
    chunks: List[str],
    embeddings: List[List[float]],
    metadata: Dict[str, Any],
    collection_name: str,
) -> Dict[str, Any]:
    """Milvus에 벡터 데이터를 Upsert합니다.
    
    컬렉션의 모든 기존 문서를 삭제한 후 새 문서를 삽입합니다.
    
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
        print(f"🔍 [Document Update] Milvus 컬렉션 확인/생성 중: {collection_name}")
        collection = ensure_milvus_collection(collection_name)
        print(f"✅ [Document Update] Milvus 컬렉션 준비 완료: {collection_name}")

        # 컬렉션의 모든 기존 문서 삭제
        print(f"🗑️ [Document Update] 기존 문서 삭제 중...")
        delete_all_documents_in_collection(collection)
        print(f"✅ [Document Update] 기존 문서 삭제 완료")

        # 문서 제목 가져오기
        document_title = metadata.get("source", Path(file_path).stem)

        # 데이터 준비
        # QA agent 호환성을 위해 content 필드도 포함 (chunk_text와 동일한 값)
        entities = [
            [document_title] * len(chunks),  # document_title
            chunks,  # chunk_text
            chunks,  # content (QA agent 호환성, chunk_text와 동일한 값)
            embeddings,  # embedding
        ]

        print(f"📤 [Document Update] Milvus에 삽입 중... (청크 수: {len(chunks)}, 컬렉션: {collection_name})")
        
        # Milvus에 삽입
        insert_result = collection.insert(entities)
        print(f"✅ [Document Update] Milvus 삽입 완료 (IDs: {insert_result.primary_keys[:5] if len(insert_result.primary_keys) > 0 else 'N/A'}...)")
        
        # 데이터 영구 저장을 위해 flush
        collection.flush()
        print(f"💾 [Document Update] Milvus flush 완료")
        
        # 삽입된 데이터 개수 확인
        collection.load()
        num_entities = collection.num_entities
        print(f"📊 [Document Update] 컬렉션 '{collection_name}' 현재 문서 수: {num_entities}")
        
        if num_entities == 0:
            print(f"⚠️ [Document Update] 경고: 삽입 후에도 문서 수가 0입니다!")
            return {
                "ok": False,
                "error": "데이터가 저장되지 않았습니다.",
                "message": f"Milvus에 데이터 삽입 후 문서 수가 0입니다. ({len(chunks)}개 청크 시도)",
            }

        return {
            "ok": True,
            "message": f"문서가 성공적으로 저장되었습니다. ({len(chunks)}개 청크, 총 {num_entities}개 문서)",
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


def get_updatable_documents() -> List[str]:
    """업데이트 가능한 문서 목록을 .env 파일에서 읽어옵니다.
    
    .env 파일에서 업데이트 가능한 파일 목록을 반환합니다.
    기본값: 복리후생, 식단, 메뉴얼, 이력서
    
    Returns:
        업데이트 가능한 문서 제목 목록
    """
    # .env 파일에서 업데이트 가능한 문서 목록 읽기
    # 형식: UPDATABLE_DOCUMENTS=복리후생,식단,메뉴얼,이력서
    updatable_docs_str = os.getenv("UPDATABLE_DOCUMENTS", "복리후생,식단,메뉴얼,이력서")

    # 쉼표로 분리하여 리스트로 변환
    updatable_docs = [doc.strip() for doc in updatable_docs_str.split(",") if doc.strip()]

    return updatable_docs if updatable_docs else ["복리후생", "식단", "메뉴얼", "이력서"]


def is_document_updatable(document_title: str) -> bool:
    """문서 제목이 업데이트 가능한지 확인합니다.
    
    Args:
        document_title: 문서 제목
        
    Returns:
        업데이트 가능 여부
    """
    updatable_docs = get_updatable_documents()

    # 문서 제목에 업데이트 가능한 키워드가 포함되어 있는지 확인
    for updatable_doc in updatable_docs:
        if updatable_doc in document_title:
            return True

    return False


def update_document_to_milvus(
    file_path: Path,
    document_title: str,
    document_type: str,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """문서를 업데이트하여 Milvus DB에 저장합니다.
    
    업데이트 가능한 문서(복리후생, 식단, 메뉴얼, 이력서)만 업데이트할 수 있습니다.
    문서 타입에 따라 적절한 컬렉션을 선택하고, 컬렉션의 모든 문서를 삭제한 후 새로 삽입합니다.
    
    Args:
        file_path: 업로드된 파일 경로
        document_title: 문서 제목
        document_type: 문서 타입 ("복리후생", "식단", "메뉴얼", "이력서")
        text: 이미 추출된 텍스트 (None이면 파일에서 추출)
        
    Returns:
        처리 결과 딕셔너리
    """
    try:

        # 문서 타입이 업데이트 가능한 문서인지 확인 (document_type으로 확인)
        updatable_docs = get_updatable_documents()
        if document_type not in updatable_docs:
            return {
                "ok": False,
                "message": f"업데이트 가능한 문서 타입이 아닙니다. 문서 타입: {document_type}, 업데이트 가능한 문서: {', '.join(updatable_docs)}",
                "error": "document_not_updatable",
                "document_title": document_title,
            }

        # 문서 타입에 따라 컬렉션 이름 결정
        collection_name = get_collection_name_by_type(document_type)
        print(f"📦 [Document Update] 문서 타입: {document_type}, 컬렉션: {collection_name}")

        # 1. 텍스트 추출 (이미 추출된 텍스트가 있으면 재사용)
        if text is None:
            # 이력서 타입인 경우 표 추출만 사용하여 중복 방지
            text = extract_text_from_file(file_path, document_type=document_type)
        if not text.strip():
            raise ValueError("추출된 텍스트가 없습니다.")

        # 1-1. 식단 타입인 경우 월 정보를 추출하여 첫 줄에 추가
        if document_type == "식단":
            month_info = extract_month_from_document(document_title, text, str(file_path))
            if month_info:
                # 첫 줄에 월 정보 추가 (예: "1월 식단표\n\n기존 텍스트...")
                text = f"{month_info}\n\n{text}"
                print(f"📅 [Document Update] 월 정보 추가: {month_info}")

        # 2. 텍스트를 청크로 분할
        # 이력서 타입인 경우 === RESUME XX === 구분자로 분리
        if document_type == "이력서":
            chunks = chunk_resume_by_separator(text)
            print(f"📄 [Document Update] 이력서 청킹 완료: {len(chunks)}개 청크")
        else:
            # 다른 타입은 기존 방식 사용 (더 정확한 청킹을 위해 큰 청크 크기 사용)
            # langchain이 있으면 4000자 청크 사용, 없으면 500 단어 청크 사용
            if LANGCHAIN_AVAILABLE:
                chunks = chunk_text(text, chunk_size=4000, chunk_overlap=200)
            else:
                chunks = chunk_text(text, chunk_size=500, chunk_overlap=100)

        if not chunks:
            raise ValueError("텍스트 청크가 생성되지 않았습니다.")

        # 3. OpenAI 임베딩 생성
        embedding_gen = get_embedding_generator()
        print(f"🔢 [Document Update] 임베딩 생성 중... (모델: {embedding_gen.model}, 청크 수: {len(chunks)})")
        embeddings = embedding_gen.embed_batch(chunks)
        print(f"✅ [Document Update] 임베딩 생성 완료: {len(embeddings)}개 벡터")

        # 4. Milvus에 Upsert (컬렉션의 모든 문서 삭제 후 새로 삽입)
        print(f"🚀 [Document Update] Milvus 업로드 시작 (컬렉션: {collection_name}, 청크 수: {len(chunks)})")
        result = upsert_to_milvus(
            file_path=str(file_path),
            chunks=chunks,
            embeddings=embeddings,
            metadata={"source": document_title, "updated_at": datetime.now().isoformat()},
            collection_name=collection_name,
        )
        print(f"📋 [Document Update] Milvus 업로드 결과: ok={result.get('ok')}, message={result.get('message', 'N/A')}")

        if result.get("ok"):
            return {
                "ok": True,
                "message": f"문서 '{document_title}'가 성공적으로 업데이트되었습니다. ({len(chunks)}개 청크)",
                "document_title": document_title,
                "chunks_count": len(chunks),
                "text_length": len(text),
            }
        else:
            return {
                "ok": False,
                "message": f"문서 업데이트 실패: {result.get('error', '알 수 없는 오류')}",
                "document_title": document_title,
                "error": result.get("error"),
            }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "message": f"문서 업데이트 중 오류 발생: {str(e)}",
            "document_title": document_title,
            "error": str(e),
        }


# 키워드 캐시 (모듈 레벨에서 한 번만 로드)
_document_type_keywords_cache: Optional[Dict[str, List[str]]] = None


def load_document_type_keywords() -> Dict[str, List[str]]:
    """agent_prompts.yaml에서 문서 타입 판별용 키워드를 로드합니다.
    
    캐시를 사용하여 첫 호출 시에만 YAML 파일을 읽고, 이후에는 캐시된 값을 반환합니다.
    YAML 파일 로드 실패 시 예외를 발생시킵니다.
    
    Returns:
        문서 타입별 키워드 딕셔너리 (예: {"menu": [...], "benefit": [...], "manual": [...]})
    
    Raises:
        ImportError: yaml 모듈이 설치되지 않은 경우
        FileNotFoundError: agent_prompts.yaml 파일을 찾을 수 없는 경우
        ValueError: YAML 파일에 필요한 키가 없는 경우
        Exception: YAML 파일 파싱 실패 시
    """
    global _document_type_keywords_cache

    # 캐시가 있으면 즉시 반환
    if _document_type_keywords_cache is not None:
        return _document_type_keywords_cache

    # yaml 모듈 확인
    if not YAML_AVAILABLE:
        raise ImportError(
            "yaml 모듈이 설치되지 않았습니다. "
            "pip install pyyaml을 실행하여 설치해주세요."
        )

    # 여러 경로에서 YAML 파일 찾기
    yaml_path = None
    search_paths = []

    # 1. 현재 파일 기준 상대 경로
    current_file = Path(__file__).resolve()
    agents_dir = current_file.parent.parent.parent.parent
    search_paths.append(agents_dir / "agent_prompts.yaml")

    # 2. 현재 작업 디렉토리 기준
    cwd = Path.cwd()
    search_paths.append(cwd / "agents" / "agent_prompts.yaml")

    # 3. 프로젝트 루트에서 찾기 (상위 디렉토리 탐색)
    for parent in [cwd] + list(cwd.parents):
        potential_path = parent / "agents" / "agent_prompts.yaml"
        if potential_path.exists():
            search_paths.append(potential_path)
            break

    # 파일 찾기
    for path in search_paths:
        if path.exists():
            yaml_path = path
            print(f"✅ YAML 파일 발견: {yaml_path}")
            break

    if yaml_path is None:
        error_msg = (
            f"agent_prompts.yaml 파일을 찾을 수 없습니다.\n"
            f"시도한 경로:\n"
        )
        for path in search_paths:
            error_msg += f"  - {path}\n"
        raise FileNotFoundError(error_msg)

    # YAML 파일 읽기
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            all_prompts = yaml.safe_load(f)
    except Exception as e:
        raise ValueError(
            f"YAML 파일 파싱 실패 ({yaml_path}): {str(e)}\n"
            "파일 형식이 올바른지 확인해주세요."
        ) from e

    if not all_prompts:
        raise ValueError(f"YAML 파일이 비어있습니다: {yaml_path}")

    # document.update_document.document_type_detection_keywords 경로로 접근
    document_prompts = all_prompts.get("document")
    if not document_prompts:
        raise ValueError(
            f"YAML 파일에 'document' 섹션이 없습니다: {yaml_path}\n"
            "agent_prompts.yaml 파일의 구조를 확인해주세요."
        )

    update_document = document_prompts.get("sub_agents", {}).get("update_document")
    if not update_document:
        raise ValueError(
            f"YAML 파일에 'document.sub_agents.update_document' 섹션이 없습니다: {yaml_path}\n"
            "agent_prompts.yaml 파일의 구조를 확인해주세요."
        )

    keywords = update_document.get("document_type_detection_keywords")
    if not keywords:
        raise ValueError(
            f"YAML 파일에 'document_type_detection_keywords' 섹션이 없습니다: {yaml_path}\n"
            "agent_prompts.yaml 파일에 document_type_detection_keywords를 추가해주세요."
        )

    # 키워드 추출 및 검증
    menu_keywords = keywords.get("menu", [])
    benefit_keywords = keywords.get("benefit", [])
    manual_keywords = keywords.get("manual", [])
    resume_keywords = keywords.get("resume", [])

    if not menu_keywords or not benefit_keywords or not manual_keywords:
        missing = []
        if not menu_keywords:
            missing.append("menu")
        if not benefit_keywords:
            missing.append("benefit")
        if not manual_keywords:
            missing.append("manual")

        raise ValueError(
            f"YAML 파일에 필수 키워드가 없습니다: {', '.join(missing)}\n"
            f"경로: {yaml_path}\n"
            "document_type_detection_keywords에 menu, benefit, manual 키워드를 모두 추가해주세요."
        )

    _document_type_keywords_cache = {
        "menu": menu_keywords,
        "benefit": benefit_keywords,
        "manual": manual_keywords,
        "resume": resume_keywords
    }

    print(f"✅ 문서 타입 판별 키워드 로드 완료: menu({len(menu_keywords)}개), "
          f"benefit({len(benefit_keywords)}개), manual({len(manual_keywords)}개), "
          f"resume({len(resume_keywords)}개)")

    return _document_type_keywords_cache


def extract_month_from_document(document_title: str, text: str, file_path: str) -> Optional[str]:
    """문서 제목, 텍스트, 파일 경로에서 월 정보를 추출합니다.
    
    식단표의 경우 "1월", "2월" 등의 월 정보를 추출하여 반환합니다.
    
    Args:
        document_title: 문서 제목
        text: 문서 텍스트 내용
        file_path: 파일 경로
        
    Returns:
        월 정보 문자열 (예: "1월 식단표", "2월 식단표"). 추출 실패 시 None
    """
    # 월 정보 패턴 (예: "1월", "2월", "(1월)", "1월 식단표" 등)
    month_patterns = [
        r'(\d{1,2})월',  # "1월", "12월"
        r'\((\d{1,2})월\)',  # "(1월)"
        r'식단표\((\d{1,2})월\)',  # "식단표(1월)"
        r'직원식\s*식단표\((\d{1,2})월\)',  # "직원식 식단표(1월)"
    ]

    # 검색 대상: 문서 제목, 텍스트 첫 500자, 파일명
    search_texts = [
        document_title,
        text[:500] if text else "",
        Path(file_path).stem if file_path else "",
    ]

    combined_text = " ".join(search_texts)

    # 각 패턴으로 월 정보 추출 시도
    for pattern in month_patterns:
        match = re.search(pattern, combined_text)
        if match:
            month_num = match.group(1)
            try:
                month_int = int(month_num)
                if 1 <= month_int <= 12:
                    return f"{month_int}월 식단표"
            except ValueError:
                continue

    # 패턴 매칭 실패 시 텍스트에서 직접 검색
    # "직원식 식단표(1월)" 같은 패턴
    direct_match = re.search(r'식단표.*?(\d{1,2})월', combined_text)
    if direct_match:
        month_num = direct_match.group(1)
        try:
            month_int = int(month_num)
            if 1 <= month_int <= 12:
                return f"{month_int}월 식단표"
        except ValueError:
            pass

    return None


def detect_document_type_from_content(text: str, filename: str, document_title: str) -> str:
    """파일 내용, 파일명, 문서 제목을 분석하여 문서 타입을 판별합니다.
    
    Args:
        text: 파일에서 추출한 텍스트 내용
        filename: 파일명
        document_title: 문서 제목
        
    Returns:
        문서 타입 ("복리후생", "식단", "메뉴얼", "이력서")
    """
    # YAML에서 키워드 로드
    keywords = load_document_type_keywords()
    menu_keywords = keywords.get("menu", [])
    benefit_keywords = keywords.get("benefit", [])
    manual_keywords = keywords.get("manual", [])
    resume_keywords = keywords.get("resume", [])

    # 모든 텍스트를 소문자로 변환하여 검색
    combined_text = (text + " " + filename + " " + document_title).lower()

    # 키워드 매칭 점수 계산
    menu_score = sum(1 for keyword in menu_keywords if keyword.lower() in combined_text)
    benefit_score = sum(1 for keyword in benefit_keywords if keyword.lower() in combined_text)
    manual_score = sum(1 for keyword in manual_keywords if keyword.lower() in combined_text)
    resume_score = sum(1 for keyword in resume_keywords if keyword.lower() in combined_text)

    # 가장 높은 점수의 타입 반환
    scores = {
        "식단": menu_score,
        "복리후생": benefit_score,
        "메뉴얼": manual_score,
        "이력서": resume_score
    }

    max_score_type = max(scores, key=scores.get)

    # 점수가 0이면 문서 제목에서만 추론
    if scores[max_score_type] == 0:
        updatable_docs = get_updatable_documents()
        for doc_type in updatable_docs:
            if doc_type in document_title:
                return doc_type
        return "복리후생"  # 기본값

    print(f"📊 [Document Type Detection] 점수 - 식단: {menu_score}, 복리후생: {benefit_score}, 메뉴얼: {manual_score}, 이력서: {resume_score}")
    print(f"✅ [Document Type Detection] 판별 결과: {max_score_type}")

    return max_score_type


def process_update_file(
    file_content: bytes,
    filename: str,
    document_title: str,
    document_type: Optional[str] = None,
) -> Dict[str, Any]:
    """업데이트할 파일을 처리합니다.
    
    Args:
        file_content: 파일 내용 (bytes)
        filename: 파일명
        document_title: 문서 제목
        document_type: 문서 타입 ("복리후생", "식단", "메뉴얼", "이력서"). None이면 파일 내용에서 추론
        
    Returns:
        처리 결과 딕셔너리
    """
    
    # 임시 파일로 저장 (한 번만 생성)
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        tmp_file.write(file_content)
        tmp_path = Path(tmp_file.name)

    try:
        # document_type이 없으면 파일 내용에서 추론
        extracted_text = None
        if not document_type:
            # 먼저 파일명과 제목으로 타입 추론 시도
            updatable_docs = get_updatable_documents()
            for doc_type in updatable_docs:
                if doc_type in document_title or doc_type in filename:
                    document_type = doc_type
                    print(f"🔍 [Document Update] 파일명/제목으로 타입 추론: {document_type}")
                    break

            # 파일명/제목으로 추론 실패 시 텍스트 내용으로 추론
            if not document_type:
                try:
                    # 타입 추론을 위해 표 추출만 사용 (이력서일 가능성 고려)
                    # 표 추출만 사용하면 모든 타입의 문서를 판별할 수 있음
                    extracted_text = extract_text_from_file(tmp_path, document_type="이력서")
                    # 파일 내용, 파일명, 문서 제목을 분석하여 타입 판별
                    document_type = detect_document_type_from_content(extracted_text, filename, document_title)
                    print(f"🔍 [Document Update] 파일 내용 분석 결과: {document_type}")
                except Exception as e:
                    print(f"⚠️ 파일 내용 분석 실패, 기본값 사용: {e}")
                    # 추론 실패 시 기본값
                    document_type = "복리후생"
                    print(f"⚠️ 문서 타입을 추론할 수 없어 기본값 사용: {document_type}")

        # 추론된 문서 타입이 업데이트 가능한 문서인지 확인
        updatable_docs = get_updatable_documents()
        if document_type not in updatable_docs:
            return {
                "ok": False,
                "message": f"업데이트 가능한 문서 타입이 아닙니다. 추론된 타입: {document_type}, 업데이트 가능한 문서: {', '.join(updatable_docs)}",
                "error": "document_not_updatable",
            }
        
        # 이력서가 아닌 경우 일반 추출로 재추출 (표 추출만으로는 부족할 수 있음)
        if document_type != "이력서" and extracted_text:
            # 이력서가 아니면 일반 추출로 재추출
            extracted_text = extract_text_from_file(tmp_path, document_type=None)
            print(f"📄 [Document Update] {document_type} 타입 - 일반 추출로 재추출")
        elif document_type == "이력서" and not extracted_text:
            # 이력서인데 아직 추출하지 않은 경우
            extracted_text = extract_text_from_file(tmp_path, document_type="이력서")
            print(f"📄 [Document Update] 이력서 타입 - 표 추출만 사용")

        # Milvus에 업데이트 (이미 추출된 텍스트가 있으면 재사용)
        result = update_document_to_milvus(tmp_path, document_title, document_type, text=extracted_text)
        return result
    finally:
        # 임시 파일 삭제
        if tmp_path.exists():
            tmp_path.unlink()