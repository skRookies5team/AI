"""문서 업데이트 관련 모듈.

이 패키지는 문서 업데이트 agent의 핵심 기능을 제공합니다:
- 문서 텍스트 추출 (PDF/DOCX)
- 텍스트 청킹 및 임베딩 생성
- Milvus 벡터 DB 업로드
- 문서 타입 자동 감지
"""

# 메인 서비스 함수들
from agents.master_graphs.document.update.document_update_service import (
    # 문서 처리
    extract_text_from_file,
    extract_text_from_pdf,
    extract_text_from_docx,
    chunk_text,
    
    # 임베딩 및 컬렉션 관리
    get_embedding_generator,
    get_collection_name_by_type,
    ensure_milvus_collection,
    delete_all_documents_in_collection,
    
    # 문서 타입 감지
    detect_document_type_from_content,
    extract_month_from_document,
    load_document_type_keywords,
    
    # 문서 업데이트
    process_update_file,
    update_document_to_milvus,
    get_updatable_documents,
    is_document_updatable,
)

# 배치 업로드 유틸리티
from agents.master_graphs.document.update.milvus_batch_upload import (
    batch_upload_file_to_milvus,
    batch_upload_folder_to_milvus,
)

__all__ = [
    # 문서 처리
    "extract_text_from_file",
    "extract_text_from_pdf",
    "extract_text_from_docx",
    "chunk_text",
    
    # 임베딩 및 컬렉션 관리
    "get_embedding_generator",
    "get_collection_name_by_type",
    "ensure_milvus_collection",
    "delete_all_documents_in_collection",
    
    # 문서 타입 감지
    "detect_document_type_from_content",
    "extract_month_from_document",
    "load_document_type_keywords",
    
    # 문서 업데이트
    "process_update_file",
    "update_document_to_milvus",
    "get_updatable_documents",
    "is_document_updatable",
    
    # 배치 업로드
    "batch_upload_file_to_milvus",
    "batch_upload_folder_to_milvus",
]
