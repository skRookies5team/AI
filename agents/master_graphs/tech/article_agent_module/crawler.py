"""ScienceON 검색 크롤러."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import platform
import concurrent.futures
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import (
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://scienceon.kisti.re.kr"
SEARCH_URL = f"{BASE_URL}/main/mainForm.do"
RESULT_LIMIT = 3
WAIT_AFTER_ENTER_SECONDS = 5
WAIT_AFTER_NETWORK_IDLE_SECONDS = 3
PDF_DOWNLOAD_DIR = Path.home() / "Downloads"

# User-Agent 설정 (실제 브라우저처럼 보이도록)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Rate limiting 설정
MIN_REQUEST_INTERVAL = 15  # 최소 15초 간격
_last_request_time: Optional[float] = None
_rate_limiter_lock: Optional[asyncio.Lock] = None


def _get_rate_limiter_lock() -> asyncio.Lock:
    """Rate limiter용 asyncio.Lock을 가져온다 (lazy initialization)."""
    global _rate_limiter_lock
    if _rate_limiter_lock is None:
        # LangServe 환경에서는 항상 이벤트 루프가 실행 중
        _rate_limiter_lock = asyncio.Lock()
    return _rate_limiter_lock


async def _wait_for_rate_limit() -> None:
    """요청 간 최소 간격을 유지한다."""
    global _last_request_time
    lock = _get_rate_limiter_lock()
    async with lock:
        if _last_request_time is not None:
            elapsed = time.time() - _last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                wait_time = MIN_REQUEST_INTERVAL - elapsed
                LOGGER.info(f"Rate limiting: {wait_time:.1f}초 대기 중...")
                await asyncio.sleep(wait_time)
        _last_request_time = time.time()


@dataclass
class PaperResult:
    """ScienceON 검색 결과 요소를 표현한다."""

    title: str
    pdf_link: str
    pdf_path: Optional[str] = None
    identifier: Optional[str] = None


def _extract_pdf_info(onclick_value: str) -> Optional[Dict[str, str]]:
    """onclick 속성에서 원문보기 정보를 추출한다.

    Args:
        onclick_value (str): HTML 버튼의 onclick 속성 값.

    Returns:
        Optional[Dict[str, str]]: 원문보기 링크를 구성하기 위한 파라미터 사전.
    """
    if not onclick_value:
        return None

    match = re.search(
        r"fncOrgDown\('([^']*)','([^']*)','([^']*)','([^']*)'\)", onclick_value
    )
    if match:
        return {
            "o_cn": match.group(1),
            "dbt": match.group(2),
            "resource": match.group(3),
            "journal": match.group(4),
        }

    legacy_match = re.search(
        r"fncOrgDown\('([^']*)','([^']*)','([^']*)'\)", onclick_value
    )
    if legacy_match:
        return {
            "o_cn": legacy_match.group(1),
            "dbt": legacy_match.group(2),
            "resource": "",
            "journal": legacy_match.group(3),
        }

    return None


def _extract_candidate_cn(text: str) -> Optional[str]:
    """문자열에서 ScienceON cn 후보를 추출한다."""
    if not text:
        return None

    if text.startswith("http"):
        parsed = urlparse(text)
        query_params = parse_qs(parsed.query)
        for key in ("cn", "artiId", "docId", "oCn"):
            if key in query_params and query_params[key]:
                return query_params[key][0]

        path_tail = parsed.path.rstrip("/").split("/")[-1]
        if path_tail:
            tokens = [token for token in re.split(r"[./]", path_tail) if token]
            for token in reversed(tokens):
                if any(char.isdigit() for char in token):
                    return token
    else:
        tokens = [token for token in re.split(r"[./]", text) if token]
        for token in reversed(tokens):
            if any(char.isdigit() for char in token):
                return token

    alnum_tokens = [token for token in re.split(r"\W+", text) if token]
    alnum_tokens.sort(key=len, reverse=True)
    for token in alnum_tokens:
        if any(char.isdigit() for char in token):
            return token

    return None


def _sanitize_filename(candidate: str, fallback: str) -> str:
    """PDF 저장 시 파일명을 안전하게 정제한다."""
    sanitized = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", candidate).strip(" ._")
    if not sanitized:
        sanitized = fallback
    if not sanitized.lower().endswith(".pdf"):
        sanitized = f"{sanitized}.pdf"
    return sanitized


async def _download_pdf(
    context: BrowserContext,
    url: str,
    download_dir: Path,
    index: int,
    title: str,
) -> Optional[str]:
    """주어진 URL에서 PDF를 다운로드하고 로컬 경로를 반환한다."""
    download_page = await context.new_page()
    try:
        async with download_page.expect_download(timeout=60_000) as download_info:
            await download_page.goto(url, timeout=60_000)
        download = await download_info.value
        suggested_name = download.suggested_filename or title or f"paper_{index}"
        filename = _sanitize_filename(suggested_name, f"paper_{index:02d}")
        file_path = download_dir / filename
        await download.save_as(str(file_path))
        LOGGER.info("PDF 다운로드 성공: %s -> %s", title, file_path)
        return str(file_path)
    except PlaywrightTimeoutError:
        LOGGER.exception("PDF 다운로드 타임아웃: %s", url)
    except Exception:
        LOGGER.exception("PDF 다운로드 실패: %s", url)
    finally:
        try:
            await download_page.close()
        except Exception:  # pragma: no cover - page close 실패 무시
            LOGGER.debug("다운로드 페이지 종료 중 오류가 발생했지만 무시합니다.", exc_info=True)
    return None


async def download_pdf_to_memory(
    pdf_link: str,
    use_playwright: bool = True
) -> Optional[bytes]:
    """PDF를 메모리로 다운로드 (로컬 저장 없음).
    
    Args:
        pdf_link: PDF URL
        use_playwright: True면 Playwright 사용 (동적 페이지용), False면 requests 사용
        
    Returns:
        PDF 바이트 데이터 또는 None
    """
    # Windows에서는 무조건 별도 스레드에서 실행 (ProactorEventLoop 사용)
    if use_playwright and platform.system() == "Windows":
        def run_in_thread():
            """별도 스레드에서 ProactorEventLoop로 실행"""
            # ProactorEventLoop 직접 생성 (Playwright는 async subprocess를 사용하므로 필요)
            new_loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(new_loop)
            
            try:
                result = new_loop.run_until_complete(
                    _download_pdf_to_memory_impl(pdf_link, use_playwright=True)
                )
                return result
            except Exception as e:
                LOGGER.warning(f"별도 스레드에서 PDF 다운로드 실패: {e}")
                # 예외를 반환하지 않고 None을 반환하여 호출자에게 전달
                return None
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)
        
        # asyncio.to_thread을 사용하여 이벤트 루프 블로킹 방지
        # Python 3.9+에서는 asyncio.to_thread 사용 가능
        try:
            # asyncio.to_thread은 별도 스레드에서 실행하고 await 가능
            result = await asyncio.to_thread(run_in_thread)
            return result
        except AttributeError:
            # Python 3.8 이하에서는 ThreadPoolExecutor 사용
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = loop.run_in_executor(executor, run_in_thread)
                return await future
    
    # 일반 경로: 현재 이벤트 루프 사용 (Windows가 아니거나 use_playwright=False인 경우)
    return await _download_pdf_to_memory_impl(pdf_link, use_playwright)


async def _download_pdf_to_memory_impl(
    pdf_link: str,
    use_playwright: bool = True
) -> Optional[bytes]:
    """PDF를 메모리로 다운로드 구현 함수 (내부용)."""
    if use_playwright:
        # Playwright 사용 (JavaScript가 필요한 경우)
        # Playwright의 Download 객체는 read() 메서드가 없으므로 임시 파일 사용
        import tempfile
        temp_path = None
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    accept_downloads=True,
                    user_agent=USER_AGENT,
                )
                try:
                    page = await context.new_page()
                    # 임시 파일 경로 생성
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
                    temp_path = temp_file.name
                    temp_file.close()
                    
                    async with page.expect_download(timeout=60_000) as download_info:
                        await page.goto(pdf_link, timeout=60_000)
                    download = await download_info.value
                    await download.save_as(temp_path)
                    
                    # 임시 파일을 메모리로 읽기
                    with open(temp_path, 'rb') as f:
                        pdf_bytes = f.read()
                    
                    # PDF 형식 검증
                    if not pdf_bytes.startswith(b"%PDF"):
                        LOGGER.warning(f"PDF 형식이 올바르지 않습니다 (Playwright): {pdf_link}")
                        return None
                    
                    LOGGER.info(f"PDF 메모리 다운로드 성공 (Playwright): {len(pdf_bytes)} bytes")
                    return pdf_bytes
                finally:
                    await context.close()
                    await browser.close()
        except NotImplementedError as e:
            LOGGER.warning(f"Playwright subprocess 오류 (Windows 환경), requests로 재시도: {e}")
        except Exception as e:
            LOGGER.warning(f"Playwright로 PDF 다운로드 실패, requests로 재시도: {e}")
        finally:
            # 임시 파일 정리
            try:
                if temp_path and Path(temp_path).exists():
                    Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
    
    # requests 사용 (단순 HTTP GET)
    try:
        import requests
        response = requests.get(pdf_link, stream=True, timeout=60, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        
        pdf_bytes = response.content
        
        # PDF 형식 검증
        if not pdf_bytes.startswith(b"%PDF"):
            LOGGER.warning(f"PDF 형식이 올바르지 않습니다 (requests): {pdf_link}")
            # Content-Type도 확인
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                LOGGER.warning(f"HTML 응답을 받았습니다 (에러 페이지일 가능성): {pdf_link}")
            return None
        
        LOGGER.info(f"PDF 메모리 다운로드 성공 (requests): {len(pdf_bytes)} bytes")
        return pdf_bytes
    except Exception as e:
        LOGGER.error(f"PDF 다운로드 실패: {e}")
        return None


def _build_pdf_link(pdf_info: Dict[str, str], query: str) -> str:
    """원문보기 링크를 구성한다.

    Args:
        pdf_info (Dict[str, str]): 원문보기 파라미터 정보.
        query (str): 검색 키워드.

    Returns:
        str: ScienceON 원문보기 URL.
    """
    encoded_query = quote(f'({query}) OR "{query}"')
    cn_candidate = (
        _extract_candidate_cn(pdf_info.get("resource", "")) or pdf_info.get("o_cn", "")
    )
    o_cn = pdf_info.get("o_cn", cn_candidate)
    cn = cn_candidate or o_cn
    dbt = pdf_info.get("dbt", "")
    journal = pdf_info.get("journal", "")
    return (
        "https://scienceon.kisti.re.kr/commons/util/originalView.do?"
        f"cn={cn}&oCn={o_cn}&dbt={dbt}&journal={journal}&keyword={encoded_query}"
    )


def _extract_url_from_onclick(onclick_value: str, query: str) -> Optional[str]:
    """onclick 속성 문자열로부터 원문보기 URL을 추출한다.

    Args:
        onclick_value (str): HTML 요소의 onclick 특성 값.
        query (str): 검색에 사용된 질의 문자열.

    Returns:
        Optional[str]: 추출된 URL. 지원되지 않는 형식일 경우 None.
    """
    if not onclick_value:
        return None

    # 기존 ScienceON 전용 다운로더
    if "fncOrgDown" in onclick_value:
        pdf_info = _extract_pdf_info(onclick_value)
        if pdf_info:
            return _build_pdf_link(pdf_info, query)

    # KCI 포털 다운로드
    kci_match = re.search(r"kciOrgDown\('([^']+)'", onclick_value)
    if kci_match:
        return unescape(kci_match.group(1))

    # AccessON (KISTI) 다운로드 - 문서 ID + 기본 URL 조합
    accesson_id_match = re.search(r"fncAcOn\('([^']+)'\)", onclick_value)
    if accesson_id_match:
        doc_id = accesson_id_match.group(1)
        base_match = re.search(
            r"insertInfraLog\('[^']*','[^']*','([^']+)'\)", onclick_value
        )
        if base_match:
            base_url = unescape(base_match.group(1))
            if doc_id in base_url:
                return base_url
            if base_url.endswith(("=", "/", "?", "&")):
                return f"{base_url}{doc_id}"
            if "artiId=" in base_url and not base_url.split("artiId=", maxsplit=1)[1]:
                return f"{base_url}{doc_id}"
            return f"{base_url.rstrip('/')}/{doc_id}"

    # RISS 또는 기타 링크 저장 함수
    log_save_match = re.search(r"fncLogSave\('([^']+)'", onclick_value)
    if log_save_match:
        return unescape(log_save_match.group(1))

    # 일반적인 URL 패턴이 포함된 경우 첫 번째 URL 반환
    generic_url_match = re.search(r"(https?://[^\s'\";]+)", onclick_value)
    if generic_url_match:
        return unescape(generic_url_match.group(1))

    return None


def _extract_pdf_link(item: Tag, query: str) -> str:
    """HTML 요소에서 원문보기 링크를 추출한다."""
    pdf_button = item.select_one("button.btn-versatile.ico_pdf") or item.select_one(
        "button[onclick]"
    )
    if pdf_button and pdf_button.has_attr("onclick"):
        extracted = _extract_url_from_onclick(pdf_button["onclick"], query)
        if extracted:
            return extracted

    anchor = item.select_one("a[href*='originalView.do']") or item.select_one(
        "a.btn-layer"
    ) or item.select_one("a[onclick]")
    if anchor:
        if anchor.has_attr("onclick"):
            extracted = _extract_url_from_onclick(anchor["onclick"], query)
            if extracted:
                return extracted
        if anchor.has_attr("href") and anchor["href"] and not anchor["href"].startswith(
            "javascript"
        ):
            return urljoin(BASE_URL, anchor["href"])

    return "원문보기 링크 없음"


def _extract_doi(item: Tag) -> Optional[str]:
    """HTML 요소에서 식별자(DOI 또는 내부 식별자)를 추출한다."""
    # 방법 1: hidden input에서 DOI 추출
    doi_input = item.select_one("input[id^='orgDoi_'][value*='doi.org']")
    if doi_input:
        value = doi_input.get("value", "")
        doi_match = re.search(r"10\.\d+/[^\s<>\"']+", value)
        if doi_match:
            return doi_match.group(0)
    
    # 방법 2: DOI가 없으면 item의 id에서 식별자 추출
    item_id = item.get("id", "")
    if item_id.startswith("result_item_"):
        identifier = item_id.replace("result_item_", "")
        return identifier
    
    return None


def _parse_items(
    soup: BeautifulSoup,
    selectors: Sequence[str],
    limit: int,
    query: str,
) -> List[PaperResult]:
    """BeautifulSoup 객체에서 논문 결과를 추출한다.

    Args:
        soup (BeautifulSoup): 파싱된 HTML 문서.
        selectors (Sequence[str]): 결과 항목을 찾기 위한 CSS 셀렉터 목록.
        limit (int): 반환할 최대 논문 수.
        query (str): 검색 키워드.

    Returns:
        List[PaperResult]: 추출된 논문 리스트.
    """
    items: List[Tag] = []
    for selector in selectors:
        items = soup.select(selector)
        if items:
            LOGGER.info("'%s' 셀렉터에서 %d개 항목 발견", selector, len(items))
            break

    results: List[PaperResult] = []
    for item in items[:limit]:
        title_tag = item.select_one("a.subject") or item.select_one("a")
        raw_title = title_tag.get_text(strip=True) if title_tag else "제목 없음"
        title = " ".join(raw_title.split())
        pdf_link = _extract_pdf_link(item=item, query=query)
        identifier = _extract_doi(item)
        results.append(PaperResult(title=title, pdf_link=pdf_link, identifier=identifier))

    if not results:
        LOGGER.warning("파싱 결과가 비어 있습니다. HTML 구조 변경 여부를 확인하세요.")

    return results


async def search_scienceon_papers(
    query: str,
    limit: int = RESULT_LIMIT,
    wait_after_enter: int = WAIT_AFTER_ENTER_SECONDS,
    wait_after_network_idle: int = WAIT_AFTER_NETWORK_IDLE_SECONDS,
    enable_downloads: bool = True,
    download_dir: Optional[str] = None,
) -> List[Dict[str, str]]:
    """ScienceON에서 논문을 검색한다.

    Args:
        query (str): 사용자 검색어.
        limit (int): 반환할 최대 논문 수.
        wait_after_enter (int): 검색 제출 후 대기할 초 단위 시간.
        wait_after_network_idle (int): 네트워크 idle 이후 추가 대기 시간(초).
        enable_downloads (bool): True일 경우 각 결과의 PDF를 내려받는다.
        download_dir (Optional[str]): 사용자 지정 저장 경로. None이면 기본 폴더(`pdf/`).

    Returns:
        List[Dict[str, str]]: 논문 제목, 원문 링크, 식별자, (다운로드 시) 로컬 경로 정보를 담은 목록.

    Raises:
        RuntimeError: 페이지 로딩 타임아웃 또는 크롤링 오류 발생 시.
    """
    # 🆕 Windows에서는 무조건 별도 스레드에서 실행 (ProactorEventLoop 사용)
    if platform.system() == "Windows":
        def run_in_thread():
            """별도 스레드에서 ProactorEventLoop로 실행"""
            # 현재 스레드의 이벤트 루프 확인 (있으면 안 됨)
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
            
            # ProactorEventLoop 직접 생성 (Playwright는 async subprocess를 사용하므로 필요)
            new_loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(new_loop)
            
            try:
                result = new_loop.run_until_complete(
                    _search_scienceon_papers_impl(
                        query, limit, wait_after_enter, wait_after_network_idle,
                        enable_downloads, download_dir
                    )
                )
                return result
            except Exception as e:
                import traceback
                traceback.print_exc()
                raise
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_in_thread)
            result = future.result()
            return result
    
    # 일반 경로: 현재 이벤트 루프 사용 (Windows가 아닌 경우)
    return await _search_scienceon_papers_impl(
        query, limit, wait_after_enter, wait_after_network_idle,
        enable_downloads, download_dir
    )


async def _search_scienceon_papers_impl(
    query: str,
    limit: int = RESULT_LIMIT,
    wait_after_enter: int = WAIT_AFTER_ENTER_SECONDS,
    wait_after_network_idle: int = WAIT_AFTER_NETWORK_IDLE_SECONDS,
    enable_downloads: bool = True,
    download_dir: Optional[str] = None,
) -> List[Dict[str, str]]:
    """ScienceON 검색 구현 함수 (내부용)."""
    # Rate limiting 적용
    await _wait_for_rate_limit()
    
    LOGGER.info("ScienceON 검색 시작: query=%s, limit=%d", query, limit)

    selectors = (
        "div.all-powerful-list.mgt20px div.powerful-con.flex-spacebetween.in-num.in-view",
        "div.powerful-con",
        "div.all-powerful-list div.powerful-con",
        "li.powerful-con",
    )

    results: List[PaperResult] = []
    should_download = enable_downloads or download_dir is not None

    # 재시도 설정
    max_retries = 2
    retry_delay = 3  # 재시도 전 대기 시간 (초)
    
    for attempt in range(max_retries + 1):
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    accept_downloads=should_download,
                    user_agent=USER_AGENT,
                )
                try:
                    page = await context.new_page()

                    # wait_until을 domcontentloaded로 변경하여 빠른 로딩
                    # 타임아웃 증가: 90초 -> 120초
                    await page.goto(SEARCH_URL, timeout=120_000, wait_until="domcontentloaded")
                    
                    # 타임아웃 증가: 20초 -> 40초, 재시도 시 더 긴 타임아웃
                    selector_timeout = 40_000 + (attempt * 10_000)  # 첫 시도: 40초, 재시도: 50초, 60초
                    try:
                        await page.wait_for_selector("#totSearchKeyword", timeout=selector_timeout)
                    except PlaywrightTimeoutError:
                        # 셀렉터가 없을 경우 페이지 상태 확인
                        current_url = page.url
                        page_title = await page.title()
                        LOGGER.warning(
                            f"셀렉터 대기 타임아웃 (시도 {attempt + 1}/{max_retries + 1}): "
                            f"URL={current_url}, Title={page_title}"
                        )
                        if attempt < max_retries:
                            await context.close()
                            await browser.close()
                            await asyncio.sleep(retry_delay)
                            continue
                        raise
                    
                    await page.fill("#totSearchKeyword", query)
                    await page.keyboard.press("Enter")

                    await asyncio.sleep(wait_after_enter)
                    # networkidle 타임아웃도 증가: 30초 -> 45초
                    await page.wait_for_load_state("networkidle", timeout=45_000)
                    await asyncio.sleep(wait_after_network_idle)

                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")

                    results = _parse_items(
                        soup=soup,
                        selectors=selectors,
                        limit=limit,
                        query=query,
                    )

                    if should_download:
                        target_dir = (
                            Path(download_dir).expanduser().resolve()
                            if download_dir
                            else PDF_DOWNLOAD_DIR
                        )
                        target_dir.mkdir(parents=True, exist_ok=True)
                        for index, result in enumerate(results, start=1):
                            if not result.pdf_link or result.pdf_link == "원문보기 링크 없음":
                                LOGGER.warning(
                                    "PDF 링크가 없어 다운로드를 건너뜁니다: %s",
                                    result.title,
                                )
                                continue
                            pdf_path = await _download_pdf(
                                context=context,
                                url=result.pdf_link,
                                download_dir=target_dir,
                                index=index,
                                title=result.title,
                            )
                            if pdf_path:
                                result.pdf_path = pdf_path
                    
                    # 성공 시 결과 반환 (PaperResult를 딕셔너리로 변환)
                    return [
                        {
                            "title": result.title,
                            "pdf_link": result.pdf_link,
                            "detail_link": None,
                            "pdf_path": result.pdf_path,
                            "identifier": result.identifier,
                        }
                        for result in results
                    ]
                finally:
                    await context.close()
                    await browser.close()
        except PlaywrightTimeoutError as exc:
            if attempt < max_retries:
                LOGGER.warning(
                    f"ScienceON 페이지 로딩 타임아웃 (시도 {attempt + 1}/{max_retries + 1}), "
                    f"{retry_delay}초 후 재시도합니다: {exc}"
                )
                await asyncio.sleep(retry_delay)
                continue
            else:
                LOGGER.exception(f"ScienceON 페이지 로딩 중 타임아웃이 발생했습니다 (최대 재시도 횟수 초과).")
                raise
        except Exception as exc:
            if attempt < max_retries and "Executable doesn't exist" not in str(exc):
                LOGGER.warning(
                    f"ScienceON 크롤링 중 오류 발생 (시도 {attempt + 1}/{max_retries + 1}), "
                    f"{retry_delay}초 후 재시도합니다: {exc}"
                )
                await asyncio.sleep(retry_delay)
                continue
            else:
                raise
    
    # 모든 재시도 실패 시 빈 리스트 반환
    return []


async def download_pdfs_from_results(
    results: List[Dict[str, str]],
    download_dir: Optional[str] = None,
) -> List[Dict[str, str]]:
    """검색 결과 목록에서 PDF를 다운로드한다.

    Args:
        results (List[Dict[str, str]]): 논문 제목과 원문 링크 정보 목록.
        download_dir (Optional[str]): 사용자 지정 저장 경로. None이면 기본 폴더(`pdf/`).

    Returns:
        List[Dict[str, str]]: PDF 경로가 추가된 검색 결과 목록.
    """
    if not results:
        return results
    
    # Windows에서는 무조건 별도 스레드에서 실행 (ProactorEventLoop 사용)
    if platform.system() == "Windows":
        def run_in_thread():
            """별도 스레드에서 ProactorEventLoop로 실행"""
            # ProactorEventLoop 직접 생성 (Playwright는 async subprocess를 사용하므로 필요)
            new_loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(new_loop)
            
            try:
                result = new_loop.run_until_complete(
                    _download_pdfs_from_results_impl(results, download_dir)
                )
                return result
            except Exception as e:
                LOGGER.warning(f"별도 스레드에서 PDF 다운로드 실패: {e}")
                import traceback
                traceback.print_exc()
                return results  # 오류 발생 시 원본 결과 반환
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)
        
        # asyncio.to_thread을 사용하여 이벤트 루프 블로킹 방지
        try:
            result = await asyncio.to_thread(run_in_thread)
            return result
        except AttributeError:
            # Python 3.8 이하에서는 ThreadPoolExecutor 사용
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = loop.run_in_executor(executor, run_in_thread)
                return await future
    
    # 일반 경로: 현재 이벤트 루프 사용 (Windows가 아닌 경우)
    return await _download_pdfs_from_results_impl(results, download_dir)


async def _download_pdfs_from_results_impl(
    results: List[Dict[str, str]],
    download_dir: Optional[str] = None,
) -> List[Dict[str, str]]:
    """PDF 다운로드 구현 함수 (내부용)."""
    if not results:
        return results
    
    target_dir = (
        Path(download_dir).expanduser().resolve()
        if download_dir
        else PDF_DOWNLOAD_DIR
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    
    updated_results: List[Dict[str, str]] = []
    
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=USER_AGENT,
            )
            try:
                for index, result in enumerate(results, start=1):
                    pdf_link = result.get("pdf_link", "")
                    title = result.get("title", f"paper_{index}")
                    
                    # 이미 다운로드된 경우 스킵
                    if result.get("pdf_path"):
                        updated_results.append(result)
                        continue
                    
                    if not pdf_link or pdf_link == "원문보기 링크 없음":
                        LOGGER.warning("PDF 링크가 없어 다운로드를 건너뜁니다: %s", title)
                        updated_results.append(result)
                        continue
                    
                    pdf_path = await _download_pdf(
                        context=context,
                        url=pdf_link,
                        download_dir=target_dir,
                        index=index,
                        title=title,
                    )
                    
                    result_copy = dict(result)
                    if pdf_path:
                        result_copy["pdf_path"] = pdf_path
                    updated_results.append(result_copy)
            finally:
                await context.close()
                await browser.close()
    except NotImplementedError as e:
        LOGGER.warning(f"Playwright subprocess 오류 (Windows 환경): {e}")
        return results  # 오류 발생 시 원본 결과 반환
    except Exception as exc:
        LOGGER.exception("PDF 다운로드 중 오류 발생")
        # 오류 발생 시 원본 결과 반환
        return results
    
    return updated_results
