from __future__ import annotations

import urllib.parse
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://scienceon.kisti.re.kr/srch/selectPORSrchArticleList.do"


def _search_url(keyword: str) -> str:
    q = urllib.parse.quote(keyword, safe="")
    return f"{BASE_URL}?queryText={q}"


def article_search(keyword: str) -> Dict:
    url = _search_url(keyword)
    items: List[Dict] = []
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a"):
            title = a.get_text(strip=True)
            href = a.get("href") or ""
            if not title or not href:
                continue
            if ".pdf" in href.lower():
                items.append({"title": title, "link": urllib.parse.urljoin(BASE_URL, href)})
            if len(items) >= 5:
                break
    except Exception:
        pass

    return {
        "ok": True,
        "message": f"논문 검색 결과(키워드: {keyword})입니다. 상세는 검색 페이지를 참고하세요.",
        "link": url,
        "items": items,
    }






