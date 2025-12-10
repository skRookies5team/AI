"""KIPRIS API 관련 함수들."""

import re
from pathlib import Path
from typing import Dict, Any, Optional
import requests
import xmltodict

from .patent_config import KIPRIS_API_KEY


def kipris_search(word: str, year: int = 0, rows: int = 10, page: int = 1) -> Dict[str, Any]:
    """KIPRIS 검색 API."""
    url = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch"
    params = {
        "word": word,
        "year": year,
        "patent": "true",
        "utility": "false",
        "numOfRows": rows,
        "pageNo": page,
        "ServiceKey": KIPRIS_API_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.encoding = "utf-8"
        r.raise_for_status()
        parsed_data = xmltodict.parse(r.text)
        # xmltodict.parse가 None을 반환할 수 있으므로 체크
        if parsed_data is None:
            return {"response": {"body": {"items": {"item": []}}, "header": {"resultCode": "ERROR", "resultMsg": "XML 파싱 실패"}}}
        return parsed_data
    except Exception as e:
        return {"response": {"body": {"items": {"item": []}}, "header": {"resultCode": "ERROR", "resultMsg": str(e)}}}


def kipris_download_pdf(application_number: str, save_dir: Path) -> tuple[Optional[Path], Dict[str, Any]]:
    """KIPRIS PDF 다운로드."""
    meta_url = "http://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice/getPubFullTextInfoSearch"
    meta_params = {"applicationNumber": application_number, "ServiceKey": KIPRIS_API_KEY}
    
    download_info = {
        "application_number": application_number,
        "error": "",
        "pdf_saved": False,
        "download_url": "",
        "doc_name": f"{application_number}.pdf",
    }

    try:
        meta_resp = requests.get(meta_url, params=meta_params, timeout=20)
        meta_resp.raise_for_status()
        meta_data = xmltodict.parse(meta_resp.text)

        # xmltodict.parse가 None을 반환할 수 있으므로 체크
        if not meta_data or not isinstance(meta_data, dict):
            download_info["error"] = "XML 파싱 실패: 응답 데이터가 비어있습니다."
            return None, download_info

    except Exception as e:
        download_info["error"] = str(e)
        return None, download_info

    try:
        response = meta_data.get("response", {})
        if not response or not isinstance(response, dict):
            download_info["error"] = "응답 형식 오류: response 필드가 없습니다."
            return None, download_info

        body = response.get("body", {})
        if not body or not isinstance(body, dict):
            download_info["error"] = "응답 형식 오류: body 필드가 없습니다."
            return None, download_info

        item = body.get("item") or {}
        if not isinstance(item, dict):
            download_info["error"] = "응답 형식 오류: item 필드가 없습니다."
            return None, download_info

        download_url = item.get("path")
        doc_name = item.get("docName") or f"{application_number}.pdf"
        download_info["doc_name"] = doc_name
        download_info["download_url"] = download_url or ""
    except Exception as e:
        download_info["error"] = f"메타데이터 파싱 오류: {str(e)}"
        return None, download_info

    if not download_url:
        download_info["error"] = "PDF 다운로드 URL이 없습니다."
        return None, download_info

    save_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^0-9A-Za-z_.-]", "_", doc_name)
    out_path = save_dir / safe_name
    download_info["pdf_path"] = str(out_path)

    headers = {
        "Range": "bytes=0-",
        "User-Agent": "Mozilla/5.0 (compatible; PatentAgent/1.0)",
    }

    try:
        with requests.get(download_url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "").lower()
            is_pdf_header = "application/pdf" in content_type or "octet-stream" in content_type
            is_pdf_magic = False

            with open(out_path, "wb") as f:
                for idx, chunk in enumerate(r.iter_content(chunk_size=8192)):
                    if not chunk:
                        continue
                    if idx == 0:
                        is_pdf_magic = chunk.startswith(b"%PDF")
                    f.write(chunk)

        if (is_pdf_header and is_pdf_magic) and out_path.exists() and out_path.stat().st_size > 0:
            download_info["pdf_saved"] = True
            return out_path, download_info
        else:
            download_info["error"] = "PDF 파일 형식이 올바르지 않거나 파일이 비어있습니다."
    except Exception as e:
        download_info["error"] = f"PDF 다운로드 중 오류: {str(e)}"

    try:
        if out_path.exists():
            out_path.unlink(missing_ok=True)
    except Exception:
        pass

    return None, download_info

