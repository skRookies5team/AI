"""Milvus 벡터 DB 일괄 업로드 유틸리티.

PDF/DOCX 파일을 읽어서 텍스트를 추출하고, 청크로 분할한 후
OpenAI 임베딩을 생성하여 Milvus 벡터 DB에 저장하는 함수들을 제공합니다.

문서 업데이트 agent의 일부로 사용됩니다.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional

# .env 파일 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 환경 변수에서 Milvus 연결 정보 가져오기 (보안상 하드코딩 금지)
MILVUS_HOST = os.getenv("HOST")
MILVUS_PORT_STR = os.getenv("MILVUS_PORT")

if not MILVUS_HOST:
    raise ValueError(
        "환경 변수 'HOST'가 설정되지 않았습니다. "
        ".env 파일에 HOST를 설정해주세요."
    )
if not MILVUS_PORT_STR:
    raise ValueError(
        "환경 변수 'MILVUS_PORT'가 설정되지 않았습니다. "
        ".env 파일에 MILVUS_PORT를 설정해주세요."
    )

try:
    MILVUS_PORT = int(MILVUS_PORT_STR.strip())
except ValueError:
    raise ValueError(
        f"환경 변수 'MILVUS_PORT'가 유효한 숫자가 아닙니다: {MILVUS_PORT_STR}"
    )


def batch_upload_file_to_milvus(
    file_path: str,
    document_title: str,
    document_type: Optional[str] = None,
    collection_name: Optional[str] = None,
) -> Dict[str, Any]:
    """파일을 Milvus 벡터 DB에 일괄 업로드합니다.
    
    document_update_service의 update_document_to_milvus 함수를 재사용합니다.
    
    Args:
        file_path: 업로드할 파일 경로 (PDF 또는 DOCX)
        document_title: 문서 제목
        document_type: 문서 타입 ("복리후생", "식단", "메뉴얼"). None이면 자동 추론
        collection_name: 컬렉션 이름. None이면 document_type에 따라 자동 결정 (현재 미사용)
        
    Returns:
        처리 결과 딕셔너리
        
    Raises:
        FileNotFoundError: 파일을 찾을 수 없는 경우
        ImportError: 필요한 라이브러리가 설치되지 않은 경우
        ValueError: 환경 변수가 설정되지 않은 경우
    """
    # Lazy import로 순환 import 방지
    from agents.master_graphs.document.update.document_update_service import (
        extract_text_from_file,
        detect_document_type_from_content,
        update_document_to_milvus,
    )
    
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")
    
    try:
        # 문서 타입 자동 추론 (필요한 경우)
        extracted_text = None
        if not document_type:
            print(f"📄 [Batch Upload] 텍스트 추출 중... ({file_path_obj.name})")
            extracted_text = extract_text_from_file(file_path_obj)
            if not extracted_text.strip():
                raise ValueError("추출된 텍스트가 없습니다.")
            
            document_type = detect_document_type_from_content(
                extracted_text, file_path_obj.name, document_title
            )
            print(f"🔍 [Batch Upload] 문서 타입 자동 추론: {document_type}")
        
        # update_document_to_milvus 함수 재사용 (중복 코드 제거, 추출된 텍스트 재사용)
        result = update_document_to_milvus(
            file_path=file_path_obj,
            document_title=document_title,
            document_type=document_type,
            text=extracted_text,  # 이미 추출된 텍스트가 있으면 재사용
        )
        
        # 결과에 추가 정보 포함
        if result.get("ok"):
            result["document_type"] = document_type
            result["collection_name"] = result.get("collection_name")
        
        return result
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "message": f"일괄 업로드 실패: {str(e)}",
            "document_title": document_title,
            "error": str(e),
        }


def batch_upload_folder_to_milvus(
    folder_path: str,
    document_type: Optional[str] = None,
    output_collection_name: Optional[str] = None,
) -> Dict[str, Any]:
    """폴더 내의 모든 PDF/DOCX 파일을 Milvus에 일괄 업로드합니다.
    
    Args:
        folder_path: PDF/DOCX 파일이 있는 폴더 경로
        document_type: 문서 타입 ("복리후생", "식단", "메뉴얼"). None이면 각 파일에서 자동 추론
        output_collection_name: 출력 컬렉션 이름. None이면 document_type에 따라 자동 결정
        
    Returns:
        처리 결과 딕셔너리
    """
    folder = Path(folder_path)
    
    if not folder.exists():
        return {
            "ok": False,
            "message": f"폴더를 찾을 수 없습니다: {folder_path}",
            "error": "folder_not_found",
        }
    
    # PDF/DOCX 파일 찾기
    pdf_files = list(folder.glob("*.pdf"))
    docx_files = list(folder.glob("*.docx"))
    all_files = pdf_files + docx_files
    
    if not all_files:
        return {
            "ok": False,
            "message": f"PDF/DOCX 파일을 찾을 수 없습니다: {folder_path}",
            "error": "no_files_found",
        }
    
    print(f"📁 [Batch Upload] 폴더: {folder_path}")
    print(f"📄 [Batch Upload] 발견된 파일: {len(all_files)}개\n")
    
    # 각 파일 처리
    results = []
    success_count = 0
    
    for file_path in all_files:
        try:
            # 파일명을 문서 제목으로 사용
            document_title = file_path.stem
            
            print(f"\n{'='*60}")
            print(f"📤 [Batch Upload] 처리 중: {file_path.name}")
            print(f"{'='*60}")
            
            result = batch_upload_file_to_milvus(
                file_path=str(file_path),
                document_title=document_title,
                document_type=document_type,
                collection_name=output_collection_name,
            )
            
            results.append({
                "file": file_path.name,
                "result": result,
            })
            
            if result.get("ok"):
                success_count += 1
                print(f"✅ [Batch Upload] 성공: {file_path.name}")
            else:
                print(f"❌ [Batch Upload] 실패: {file_path.name} - {result.get('message')}")
                
        except Exception as e:
            print(f"❌ [Batch Upload] 오류 ({file_path.name}): {e}")
            results.append({
                "file": file_path.name,
                "result": {
                    "ok": False,
                    "message": str(e),
                    "error": "exception",
                },
            })
    
    print(f"\n{'='*60}")
    print(f"🎉 [Batch Upload] 완료: {success_count}/{len(all_files)}개 파일 업로드 성공")
    print(f"{'='*60}")
    
    return {
        "ok": success_count > 0,
        "message": f"{success_count}/{len(all_files)}개 파일 업로드 성공",
        "total_files": len(all_files),
        "success_count": success_count,
        "results": results,
    }