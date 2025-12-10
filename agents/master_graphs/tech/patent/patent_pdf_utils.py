"""PDF 처리 관련 유틸리티 함수들."""

import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    from PyPDF2 import PdfReader
    PDF_SUPPORTED = True
except ImportError:
    PdfReader = None
    PDF_SUPPORTED = False

from .patent_config import PATENT_DATA_DIR


def ensure_list(x: Any) -> List[Any]:
    """리스트/단일 아이템 정규화."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def clean_text(s: Optional[str]) -> str:
    """텍스트 정리."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def extract_pdf_text(pdf_path: Path) -> str:
    """PDF 텍스트 추출."""
    if not PDF_SUPPORTED:
        raise ImportError("PyPDF2 미설치: pip install PyPDF2 로 설치 후 다시 시도하세요.")
    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        pages.append(page_text.strip())
    return "\n\n".join(filter(None, pages))


def save_text_file(text_dir: Path, application_number: str, title: str, abstract: str, body: str) -> Path:
    """텍스트 파일 저장."""
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / f"{application_number}.txt"
    parts = [
        f"제목: {title}",
        f"초록: {abstract}",
        "",
        "본문:",
        body.strip(),
    ]
    with open(text_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts).strip() + "\n")
    return text_path


def save_metadata(meta_dir: Path, metadata: Dict[str, Any]) -> Path:
    """메타데이터 저장."""
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{metadata['application_number']}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return meta_path


def load_metadata(application_number: str) -> Optional[Dict[str, Any]]:
    """메타데이터 로드."""
    try:
        meta_dir = PATENT_DATA_DIR / "meta"
        meta_path = meta_dir / f"{application_number}.json"

        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ 메타데이터 로드 오류 ({application_number}): {e}")
    return None


def get_pdf_link_from_metadata(application_number: str) -> str:
    """메타데이터에서 PDF 다운로드 링크 가져오기."""
    metadata = load_metadata(application_number)
    if metadata and isinstance(metadata, dict):
        pdf_info = metadata.get("pdf", {})
        if isinstance(pdf_info, dict):
            return pdf_info.get("download_url", "")
    return ""


def get_full_text_from_file(application_number: str) -> str:
    """텍스트 파일에서 전체 내용 가져오기."""
    try:
        text_dir = PATENT_DATA_DIR / "text"
        text_path = text_dir / f"{application_number}.txt"

        if text_path.exists():
            with open(text_path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        print(f"⚠️ 텍스트 파일 로드 오류 ({application_number}): {e}")
    return ""

