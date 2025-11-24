"""ScienceON 검색 크롤러."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.sync_api import (
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://scienceon.kisti.re.kr"
SEARCH_URL = f"{BASE_URL}/main/mainForm.do"
RESULT_LIMIT = 10
WAIT_AFTER_ENTER_SECONDS = 5
WAIT_AFTER_NETWORK_IDLE_SECONDS = 3
PDF_DOWNLOAD_DIR = Path(__file__).resolve().parent / "pdf"


@dataclass
class PaperResult:
    """ScienceON 검색 결과 요소를 표현한다."""

    title: str
    pdf_link: str
    pdf_path: Optional[str] = None


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


def _download_pdf(
    context: BrowserContext,
    url: str,
    download_dir: Path,
    index: int,
    title: str,
) -> Optional[str]:
    """주어진 URL에서 PDF를 다운로드하고 로컬 경로를 반환한다."""
    download_page = context.new_page()
    try:
        with download_page.expect_download(timeout=60_000) as download_info:
            download_page.goto(url, timeout=60_000)
        download = download_info.value
        suggested_name = download.suggested_filename or title or f"paper_{index}"
        filename = _sanitize_filename(suggested_name, f"paper_{index:02d}")
        file_path = download_dir / filename
        download.save_as(str(file_path))
        LOGGER.info("PDF 다운로드 성공: %s -> %s", title, file_path)
        return str(file_path)
    except PlaywrightTimeoutError:
        LOGGER.exception("PDF 다운로드 타임아웃: %s", url)
    except Exception:
        LOGGER.exception("PDF 다운로드 실패: %s", url)
    finally:
        try:
            download_page.close()
        except Exception:  # pragma: no cover - page close 실패 무시
            LOGGER.debug("다운로드 페이지 종료 중 오류가 발생했지만 무시합니다.", exc_info=True)
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
        results.append(PaperResult(title=title, pdf_link=pdf_link))

    if not results:
        LOGGER.warning("파싱 결과가 비어 있습니다. HTML 구조 변경 여부를 확인하세요.")

    return results


def search_scienceon_papers(
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
        List[Dict[str, str]]: 논문 제목, 원문 링크, (다운로드 시) 로컬 경로 정보를 담은 목록.

    Raises:
        RuntimeError: 페이지 로딩 타임아웃 또는 크롤링 오류 발생 시.
    """
    LOGGER.info("ScienceON 검색 시작: query=%s, limit=%d", query, limit)

    selectors = (
        "div.all-powerful-list.mgt20px div.powerful-con.flex-spacebetween.in-num.in-view",
        "div.powerful-con",
        "div.all-powerful-list div.powerful-con",
        "li.powerful-con",
    )

    results: List[PaperResult] = []
    should_download = enable_downloads or download_dir is not None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=should_download)
            try:
                page = context.new_page()

                page.goto(SEARCH_URL, timeout=60_000)
                page.wait_for_selector("#totSearchKeyword", timeout=15_000)
                page.fill("#totSearchKeyword", query)
                page.keyboard.press("Enter")

                time.sleep(wait_after_enter)
                page.wait_for_load_state("networkidle", timeout=30_000)
                time.sleep(wait_after_network_idle)

                html = page.content()
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
                        pdf_path = _download_pdf(
                            context=context,
                            url=result.pdf_link,
                            download_dir=target_dir,
                            index=index,
                            title=result.title,
                        )
                        if pdf_path:
                            result.pdf_path = pdf_path
            finally:
                context.close()
                browser.close()
    except PlaywrightTimeoutError as exc:
        LOGGER.exception("ScienceON 페이지 로딩 중 타임아웃이 발생했습니다.")
        raise RuntimeError("ScienceON 페이지 로딩에 실패했습니다.") from exc
    except Exception as exc:
        LOGGER.exception("ScienceON 크롤링 중 오류가 발생했습니다.")
        raise RuntimeError("ScienceON 크롤링 중 알 수 없는 오류가 발생했습니다.") from exc

    LOGGER.info("ScienceON 검색 완료: 결과 %d건", len(results))
    payloads: List[Dict[str, str]] = []
    for result in results:
        result_dict: Dict[str, str] = {
            "title": result.title,
            "pdf_link": result.pdf_link,
        }
        if result.pdf_path:
            result_dict["pdf_path"] = result.pdf_path
        payloads.append(result_dict)
    return payloads


def download_pdfs_from_results(
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
    
    target_dir = (
        Path(download_dir).expanduser().resolve()
        if download_dir
        else PDF_DOWNLOAD_DIR
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    
    updated_results: List[Dict[str, str]] = []
    
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
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
                    
                    pdf_path = _download_pdf(
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
                context.close()
                browser.close()
    except Exception as exc:
        LOGGER.exception("PDF 다운로드 중 오류 발생")
        # 오류 발생 시 원본 결과 반환
        return results
    
    return updated_results


if __name__ == "__main__":
    user_query = input("검색어를 입력하세요: ")
    papers = search_scienceon_papers(user_query)
    print("\n=== 검색 결과 ===")
    for index, paper in enumerate(papers, start=1):
        print(f"[{index}] {paper['title']}")
        print(f"원문보기: {paper['pdf_link']}\n")