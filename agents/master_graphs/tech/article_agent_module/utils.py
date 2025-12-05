"""논문 에이전트 유틸리티 함수들."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from .state import ScienceONAgentState, _get_int_env, OPENAI_MODEL

LOGGER = logging.getLogger(__name__)


def validate_user_input(user_input: str) -> Tuple[bool, Optional[str]]:
    """사용자 입력을 검증한다.
    
    Args:
        user_input: 검증할 사용자 입력
        
    Returns:
        (is_valid, error_message) 튜플
        - is_valid: 유효하면 True, 아니면 False
        - error_message: 유효하지 않을 경우 에러 메시지
    """
    # 1. 빈 문자열 체크
    if not user_input or not user_input.strip():
        return False, "입력이 비어있습니다."
    
    # 2. 길이 제한 (DoS 방지)
    MAX_INPUT_LENGTH = _get_int_env("MAX_USER_INPUT_LENGTH", 2000)  # 기본 2000자
    if len(user_input) > MAX_INPUT_LENGTH:
        return False, f"입력이 너무 깁니다. (최대 {MAX_INPUT_LENGTH}자)"
    
    # 3. 위험한 패턴 체크
    dangerous_patterns = [
        r'<script[^>]*>',      # 스크립트 태그
        r'javascript:',         # JavaScript 프로토콜
        r'on\w+\s*=',          # 이벤트 핸들러 (onclick, onerror 등)
        r'data:text/html',     # data URI
        r'vbscript:',          # VBScript 프로토콜
        r'<iframe[^>]*>',      # iframe 태그
    ]
    
    user_input_lower = user_input.lower()
    for pattern in dangerous_patterns:
        if re.search(pattern, user_input_lower):
            LOGGER.warning(f"위험한 패턴 감지: {pattern} in user_input")
            return False, "입력에 허용되지 않은 문자가 포함되어 있습니다."
    
    return True, None


def sanitize_user_input(user_input: str) -> str:
    """사용자 입력을 정제한다 (검증 후 사용).
    
    Args:
        user_input: 정제할 사용자 입력
        
    Returns:
        정제된 사용자 입력
    """
    # HTML 태그 제거 (검색 쿼리에는 태그가 필요 없음)
    sanitized = re.sub(r'<[^>]+>', '', user_input)
    
    # 제어 문자 제거 (탭, 줄바꿈, 캐리지 리턴은 허용)
    sanitized = ''.join(
        char for char in sanitized 
        if ord(char) >= 32 or char in '\n\t\r'
    )
    
    # 앞뒤 공백 제거
    sanitized = sanitized.strip()
    
    return sanitized


def get_user_friendly_error_message(exc: Exception) -> str:
    """기술적 에러 메시지를 사용자 친화적인 메시지로 변환한다."""
    error_str = str(exc).lower()
    
    # 타임아웃 관련
    if "timeout" in error_str or "timed out" in error_str:
        return "요청 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
    
    # 네트워크 관련
    if "connection" in error_str or "network" in error_str or "unreachable" in error_str:
        return "네트워크 연결에 문제가 있습니다. 인터넷 연결을 확인해 주세요."
    
    # API 키 관련
    if "api key" in error_str or "authentication" in error_str or "unauthorized" in error_str:
        return "인증에 문제가 있습니다. 관리자에게 문의해 주세요."
    
    # 벡터DB 관련
    if "milvus" in error_str or "vector" in error_str or "collection" in error_str:
        return "데이터베이스 연결에 문제가 있습니다. 잠시 후 다시 시도해 주세요."
    
    # 메모리 관련
    if "memory" in error_str or "out of memory" in error_str:
        return "메모리 부족으로 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."
    
    # 파일 관련
    if "file" in error_str or "not found" in error_str or "no such file" in error_str:
        return "파일을 찾을 수 없습니다. 다시 시도해 주세요."
    
    # 일반적인 에러
    return "처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def validate_state(state: ScienceONAgentState) -> Tuple[bool, Optional[str]]:
    """상태의 일관성을 검증한다.
    
    Returns:
        (is_valid, error_message) 튜플
    """
    # download_index 범위 검증
    download_index = state.get("download_index", [])
    results = state.get("results", [])
    
    if download_index:
        for idx in download_index:
            if not isinstance(idx, int):
                return False, f"download_index에 유효하지 않은 값이 있습니다: {idx}"
            if idx < 0 or idx >= len(results):
                return False, f"요청하신 {idx + 1}번째 논문을 찾을 수 없습니다. (현재 검색 결과: {len(results)}개)"
    
    # 필수 필드 검증
    required_fields = ["user_input", "intent", "query", "download_index", "keyword"]
    for field in required_fields:
        if field not in state:
            return False, f"필수 필드가 누락되었습니다: {field}"
    
    # intent 값 검증
    valid_intents = ["search", "detail", "download", "ask_source", "unknown"]
    if state.get("intent") not in valid_intents:
        return False, f"유효하지 않은 intent 값입니다: {state.get('intent')}"
    
    return True, None


def validate_text_quality(
    text: str, 
    min_length: int = 100,
    source: str = "unknown",  # "docling", "pypdf", "vision"
    total_pages: Optional[int] = None
) -> Tuple[bool, str]:
    """추출된 텍스트의 품질을 검증한다.
    
    Args:
        text: 검증할 텍스트
        min_length: 최소 텍스트 길이 (기본값: 100자)
        source: 추출 소스 ("docling", "pypdf", "vision")
        total_pages: PDF 총 페이지 수 (docling/pypdf의 경우)
        
    Returns:
        (is_valid, reason) 튜플
        - is_valid: 품질이 좋으면 True, 나쁘면 False
        - reason: 품질이 나쁜 경우 이유 설명
    """
    if not text or not text.strip():
        return False, "텍스트가 비어있음"
    
    text_len = len(text)
    
    # Vision API는 검사를 거의 안 함 (최소 길이만 체크)
    if source == "vision":
        if text_len < min_length:
            return False, f"텍스트가 너무 짧음 ({text_len}자 < {min_length}자)"
        return True, "Vision API 결과 (검사 통과)"
    
    # docling/pypdf: 페이지 수 대비 텍스트 비율 체크 + 이상한 문자 비율 체크
    if source in ("docling", "pypdf"):
        # total_pages가 있으면 페이지 수 대비 체크
        if total_pages and total_pages > 0:
            # 1. 페이지당 평균 텍스트 길이 계산
            avg_chars_per_page = text_len / total_pages
            # 페이지당 50자 미만이면 "텍스트 추출이 현저히 적음"으로 판단
            if avg_chars_per_page < 50:
                return False, (
                    f"페이지 수에 비해 텍스트가 현저히 적음 "
                    f"({total_pages}페이지, 총 {text_len}자, 페이지당 평균 {avg_chars_per_page:.1f}자 < 50자)"
                )
        
        # 2. 이상한 문자 비율 체크 (total_pages가 없어도 항상 체크)
        weird_chars = re.findall(r'[^\x00-\x7F가-힣a-zA-Z0-9\s\.\,\;\:\!\?\-\(\)\[\]\{\}"\'/\\]', text)
        weird_ratio = len(weird_chars) / text_len if text_len > 0 else 0
        
        # 이상한 문자 비율이 10% 이상이면 실패 (이미지 기반 PDF로 판단)
        if weird_ratio > 0.1:
            return False, (
                f"이상한 문자 비율이 너무 높음 (이미지 기반 PDF 가능성) "
                f"({weird_ratio:.2%} > 10%, {len(weird_chars)}개 이상한 문자)"
            )
        
        # 3. 정상 문자 비율 체크 (40% 미만이면 실패)
        normal_chars = re.findall(r'[가-힣a-zA-Z0-9\s\.\,\;\:\!\?\-\(\)\[\]\{\}"\'/\\]', text)
        normal_ratio = len(normal_chars) / text_len if text_len > 0 else 0
        
        if normal_ratio < 0.4:
            return False, (
                f"정상 문자 비율이 너무 낮음 (이미지 기반 PDF 가능성) "
                f"({normal_ratio:.2%} < 40%)"
            )
        
        # 4. 의미있는 단어 비율 체크 (15% 미만이면 실패)
        meaningful_words = re.findall(r'[가-힣]{2,}|[a-zA-Z]{2,}', text)
        meaningful_chars = sum(len(word) for word in meaningful_words)
        meaningful_ratio = meaningful_chars / text_len if text_len > 0 else 0
        
        if meaningful_ratio < 0.15:
            return False, (
                f"의미있는 단어 비율이 너무 낮음 (이미지 기반 PDF 가능성) "
                f"({meaningful_ratio:.2%} < 15%)"
            )
        
        # 5. 한글 vs 비한글 문자 비율 체크
        korean_chars = re.findall(r'[가-힣]', text)
        korean_count = len(korean_chars)
        
        non_korean_cjk_chars = re.findall(r'[\u4E00-\u9FFF]', text)
        non_korean_count = len(non_korean_cjk_chars)
        
        # 비한글이 한글보다 많으면 이미지 기반 PDF로 판단
        if non_korean_count > korean_count:
            return False, (
                f"비한글 문자가 한글보다 많음 (이미지 기반 PDF 가능성) "
                f"(한글: {korean_count}자, 비한글: {non_korean_count}자)"
            )
        
        # 한글이 없고 비한글만 있으면 실패
        if korean_count == 0 and non_korean_count > 0:
            return False, (
                f"한글이 없고 비한글 문자만 있음 (이미지 기반 PDF 가능성) "
                f"(비한글: {non_korean_count}자)"
            )
        
        return True, "페이지 수 대비 텍스트 양 적절, 품질 검증 통과"
    
    # 기존 로직 (source가 없거나 페이지 수를 모를 때)
    if text_len < min_length:
        return False, f"텍스트가 너무 짧음 ({text_len}자 < {min_length}자)"
    
    # 정상 문자 비율 계산
    normal_chars = re.findall(r'[가-힣a-zA-Z0-9\s\.\,\;\:\!\?\-\(\)\[\]\{\}"\'/\\]', text)
    normal_ratio = len(normal_chars) / text_len if text_len > 0 else 0
    
    # 이상한 문자 비율 계산
    weird_chars = re.findall(r'[^\x00-\x7F가-힣a-zA-Z0-9\s\.\,\;\:\!\?\-\(\)\[\]\{\}"\'/\\]', text)
    weird_ratio = len(weird_chars) / text_len if text_len > 0 else 0
    
    # 의미있는 단어 비율 계산
    meaningful_words = re.findall(r'[가-힣]{2,}|[a-zA-Z]{2,}', text)
    meaningful_chars = sum(len(word) for word in meaningful_words)
    meaningful_ratio = meaningful_chars / text_len if text_len > 0 else 0
    
    # 검증 기준
    if normal_ratio < 0.3:
        return False, f"정상 문자 비율이 너무 낮음 ({normal_ratio:.2%} < 30%)"
    
    if weird_ratio > 0.6:
        return False, f"이상한 문자 비율이 너무 높음 ({weird_ratio:.2%} > 60%)"
    
    if meaningful_ratio < 0.1:
        return False, f"의미있는 단어 비율이 너무 낮음 ({meaningful_ratio:.2%} < 10%)"
    
    return True, "품질 검증 통과"


def strip_refusal_lines(text: str) -> str:
    """Vision 응답에서 '이미지 분석 불가/도와줄 수 없음' 같은 거절 문장만 제거한다."""
    refusal_patterns = [
        "I'm unable to extract text from images directly",
        "I'm sorry, but I can't assist with that",
        "이미지를 직접 분석하거나 텍스트를 추출할 수는 없습니다",
        "이미지를 직접 분석할 수는 없습니다",
        "텍스트를 추출할 수는 없습니다",
    ]

    lines = text.splitlines()
    filtered: list[str] = []
    for line in lines:
        lowered = line.lower()
        if any(pat.lower() in lowered for pat in refusal_patterns):
            continue
        if line.strip():
            filtered.append(line)
    return "\n".join(filtered).strip()


async def extract_text_from_pdf_with_vision(
    pdf_source: bytes,
    model: Optional[str] = None
) -> Optional[str]:
    """GPT-4o mini Vision API를 사용하여 이미지 기반 PDF에서 텍스트 추출."""
    try:
        from pdf2image import convert_from_bytes
        from openai import AsyncOpenAI
        import base64
        from PIL import Image
        
        if not isinstance(pdf_source, bytes):
            LOGGER.warning(f"pdf_source는 bytes 타입이어야 합니다. 받은 타입: {type(pdf_source)}")
            return None
        
        pdf_bytes = pdf_source
        source_name = "memory"
        
        vision_model = model or OPENAI_MODEL or "gpt-4o-mini"
        openai_api_key = os.getenv("OPENAI_API_KEY")
        
        if not openai_api_key:
            LOGGER.warning("OPENAI_API_KEY가 설정되지 않았습니다.")
            return None
        
        client = AsyncOpenAI(api_key=openai_api_key)
        text_parts: list[str] = []
        
        LOGGER.info(f"PDF를 이미지로 변환 중: {source_name} (Vision 모델: {vision_model})")
        
        default_poppler_path = r"C:\a_program\poppler-25.11.0\Library\bin"
        poppler_path = os.getenv("POPPLER_PATH", default_poppler_path)

        try:
            images = await asyncio.to_thread(
                convert_from_bytes,
                pdf_bytes,
                dpi=300,
                poppler_path=poppler_path,
            )
        except Exception as e:
            LOGGER.warning(f"PDF를 이미지로 변환 실패: {e}")
            return None
        
        total_pages = len(images)
        LOGGER.info(f"총 {total_pages}페이지를 Vision API로 처리합니다...")
        
        vision_prompt = (
            "당신은 OCR 어시스턴트입니다. 이미지 안의 보이는 텍스트만 그대로 추출해서 출력하세요.\n"
            "- 설명, 사과, 거절, 안내 문구는 쓰지 마세요.\n"
            "- '이미지를 분석할 수 없습니다', '도와줄 수 없습니다' 같은 문장을 절대 쓰지 마세요.\n"
            "- 문단과 줄바꿈은 최대한 원본과 비슷하게 유지하세요.\n"
            "- 표/그림 안의 텍스트도 보이면 그대로 적어 주세요."
        )

        pages_success = 0
        pages_empty = 0
        pages_refused = 0
        
        for i, image in enumerate(images):
            try:
                w, h = image.size
                if min(w, h) < 800:
                    scale = 2
                    image = image.resize(
                        (w * scale, h * scale),
                        Image.LANCZOS,
                    )

                buffered = BytesIO()
                image.save(buffered, format="PNG", optimize=True)
                img_base64 = base64.b64encode(buffered.getvalue()).decode()
                
                response = await client.chat.completions.create(
                    model=vision_model,
                    messages=[
                        {
                            "role": "system",
                            "content": vision_prompt,
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "이 이미지에서 보이는 텍스트만 그대로 추출해 주세요.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{img_base64}"
                                    },
                                },
                            ],
                        },
                    ],
                    max_tokens=4096,
                    temperature=0.0,
                )
                
                raw_text = response.choices[0].message.content or ""
                if not raw_text.strip():
                    pages_empty += 1
                    continue

                page_text = strip_refusal_lines(raw_text)
                if not page_text:
                    pages_refused += 1
                    continue
                
                text_parts.append(page_text)
                pages_success += 1
                
            except Exception as page_error:
                LOGGER.warning(f"페이지 {i+1} Vision API 처리 실패: {page_error}")
                continue
        
        if not text_parts:
            LOGGER.warning(f"Vision API로 텍스트를 추출할 수 없었습니다: {source_name}")
            return None

        extracted_text = "\n\n".join(text_parts)
        LOGGER.info(
            f"Vision API 텍스트 추출 성공: {source_name}, "
            f"{len(text_parts)}페이지, {len(extracted_text)}자"
        )
        return extracted_text
        
    except ImportError as import_error:
        LOGGER.warning(
            f"Vision API 필수 패키지가 설치되지 않았습니다: {import_error}. "
            f"pip install pdf2image openai pillow 설치 필요"
        )
        return None
    except Exception as e:
        LOGGER.warning(f"Vision API 텍스트 추출 실패: {e}", exc_info=True)
        return None


def _extract_text_with_docling_from_bytes(pdf_bytes: bytes) -> Tuple[Optional[str], Optional[int]]:
    """docling을 사용한 PDF 텍스트 추출 (BytesIO 버전, 동기 함수)."""
    try:
        from docling.document_converter import DocumentConverter
        from docling_core.transforms.chunker import HierarchicalChunker
        import tempfile
        
        LOGGER.info("PDF 변환 시작 (메모리에서)")
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(pdf_bytes)
            tmp_path = tmp_file.name
        
        try:
            converter = DocumentConverter()
            result = converter.convert(tmp_path)
            doc = result.document
            
            if not doc:
                LOGGER.warning("PDF 변환 결과가 비어있습니다 (메모리에서)")
                return None, None
            
            chunker = HierarchicalChunker()
            chunks = list(chunker.chunk(doc))
            
            if not chunks:
                LOGGER.warning("PDF에서 청크를 생성할 수 없습니다 (메모리에서)")
                return None, None
            
            text_parts = []
            for chunk in chunks:
                if hasattr(chunk, 'text') and chunk.text:
                    text_parts.append(chunk.text.strip())
                elif isinstance(chunk, dict) and 'text' in chunk:
                    text_parts.append(chunk['text'].strip())
                elif isinstance(chunk, str):
                    text_parts.append(chunk.strip())
            
            if not text_parts:
                LOGGER.warning(f"PDF에서 텍스트를 추출할 수 없습니다 (메모리에서), 청크 수: {len(chunks)}")
                return None, None
            
            extracted_text = "\n\n".join(text_parts)
            
            total_pages = None
            try:
                from pypdf import PdfReader
                reader = PdfReader(BytesIO(pdf_bytes))
                total_pages = len(reader.pages)
            except Exception:
                pass
            
            return extracted_text, total_pages
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
    except ImportError as import_error:
        LOGGER.warning(f"docling import 실패: {import_error}")
        raise ImportError("docling이 설치되지 않았습니다. pip install docling을 실행하세요.")


def _extract_text_with_pypdf_from_bytes(pdf_bytes: bytes) -> Tuple[Optional[str], Optional[int]]:
    """pypdf를 사용한 PDF 텍스트 추출 (BytesIO 버전, 동기 함수)."""
    try:
        from pypdf import PdfReader
        
        pdf_file = BytesIO(pdf_bytes)
        reader = PdfReader(pdf_file)
        total_pages = len(reader.pages)
        LOGGER.info(f"PDF 읽기 성공 (pypdf, 메모리에서): 총 {total_pages}페이지")
        
        text_chunks = []
        for i, page in enumerate(reader.pages):
            try:
                content = page.extract_text() or ""
                if content.strip():
                    text_chunks.append(content.strip())
            except Exception as page_error:
                LOGGER.warning(f"페이지 {i+1} 텍스트 추출 실패: {page_error}")
        
        if not text_chunks:
            LOGGER.warning("PDF에서 텍스트를 추출할 수 없습니다 (이미지 기반일 가능성, 메모리에서)")
            return None, total_pages
        
        extracted_text = "\n\n".join(text_chunks)
        return extracted_text, total_pages
    except Exception as e:
        LOGGER.warning(f"pypdf 텍스트 추출 실패 (메모리에서): {e}")
        return None, None


async def extract_text_from_pdf(pdf_source: bytes) -> Optional[str]:
    """PDF에서 텍스트를 추출한다 (docling 사용).
    
    Args:
        pdf_source: PDF 바이트 데이터 (bytes) - 메모리 처리 방식
        
    Returns:
        추출된 텍스트 문자열. 실패 시 None.
    """
    try:
        if not isinstance(pdf_source, bytes):
            LOGGER.warning(f"pdf_source는 bytes 타입이어야 합니다. 받은 타입: {type(pdf_source)}")
            return None
        
        pdf_bytes = pdf_source
        source_name = "memory"
        
        # docling을 사용한 PDF 변환 (CPU 바운드 작업)
        source = "docling"
        try:
            extracted_text, total_pages = await asyncio.to_thread(
                _extract_text_with_docling_from_bytes, pdf_bytes
            )
        except ImportError as e:
            LOGGER.warning(f"docling이 설치되지 않았습니다. pypdf로 폴백: {e}")
            source = "pypdf"
            extracted_text, total_pages = await asyncio.to_thread(
                _extract_text_with_pypdf_from_bytes, pdf_bytes
            )
        
        if not extracted_text:
            return None
        
        # 텍스트 품질 검증 (docling/pypdf, 페이지 수 전달)
        is_valid, reason = validate_text_quality(
            extracted_text, 
            source=source,
            total_pages=total_pages
        )
        if not is_valid:
            LOGGER.warning(
                f"PDF 텍스트 품질 검증 실패: {source_name} - {reason}. "
                f"이미지 기반 PDF일 가능성이 높아 Vision API로 폴백 시도..."
            )
            # 품질이 낮으면 Vision API로 폴백
            vision_text = await extract_text_from_pdf_with_vision(pdf_bytes)
            if vision_text:
                is_valid, reason = validate_text_quality(
                    vision_text,
                    source="vision"
                )
                if is_valid:
                    LOGGER.info(
                        f"Vision API로 텍스트 추출 성공 (품질 검증 통과): {source_name}, "
                        f"{len(vision_text)}자"
                    )
                    return vision_text
                else:
                    LOGGER.warning(f"Vision API 결과도 품질 낮음: {reason}. 벡터DB 저장을 건너뜁니다.")
            else:
                LOGGER.warning(
                    f"Vision API로 텍스트 추출 실패: {source_name}. "
                    f"이미지 기반 PDF이지만 Vision API가 실패하여 저장하지 않습니다."
                )
            return None
        
        LOGGER.info(f"PDF 텍스트 추출 성공: {source_name}, {len(extracted_text)}자")
        return extracted_text
    except Exception as e:
        LOGGER.warning(f"PDF 텍스트 추출 실패: {e}", exc_info=True)
        return None


def render_history(messages: List[BaseMessage]) -> str:
    """LangChain 메시지 목록을 문자열로 변환한다."""
    if not messages:
        return "(이전 대화 없음)"
    lines: List[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            speaker = "사용자"
        elif isinstance(message, AIMessage):
            speaker = "AI"
        else:
            speaker = message.type
        lines.append(f"{speaker}: {message.content}")
    return "\n".join(lines)


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """텍스트에서 JSON 객체를 추출한다 (중첩된 중괄호 처리)."""
    start_idx = text.find('{')
    if start_idx == -1:
        return None
    
    brace_count = 0
    for i in range(start_idx, len(text)):
        if text[i] == '{':
            brace_count += 1
        elif text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                json_str = text[start_idx:i+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return None
    return None


def parse_search_source_keywords(user_input: str) -> Tuple[bool, bool]:
    """사용자 입력에서 웹/사내 서버 키워드를 파싱한다.
    
    Args:
        user_input: 사용자 입력 문자열
        
    Returns:
        (has_web_keyword, has_internal_keyword) 튜플
    """
    web_keywords = ["웹", "인터넷", "온라인", "web", "internet"]
    internal_keywords = ["사내", "내부", "서버", "internal", "server"]
    
    user_input_lower = user_input.lower()
    has_web = any(kw in user_input_lower for kw in web_keywords)
    has_internal = any(kw in user_input_lower for kw in internal_keywords)
    
    return has_web, has_internal

