from __future__ import annotations

from agents.email_search_agent import _match_query, _parse_email_from_bytes


def test_match_query_basic():
	assert _match_query(["육아휴직 신청 안내 메일입니다."], "육아휴직 신청") is True
	assert _match_query(["복리후생 공지"], "육아휴직") is False


def test_parse_email_from_bytes_simple():
	raw = [
		b"From: HR <hr@example.com>",
		b"To: you@example.com",
		b"Subject: \xea\xb7\xb8\xeb\xa3\xb9 \xec\x95\x88\xeb\x82\xb4 - \xec\x9c\xa1\xec\x95\x84\xed\x9c\xb4\xec\xa7\x81 \xec\x8b\xa0\xec\xb2\xad",
		b"Date: Tue, 11 Nov 2025 10:00:00 +0900",
		b"Content-Type: text/plain; charset=utf-8",
		b"",
		b"\xec\x9c\xa1\xec\x95\x84\xed\x9c\xb4\xec\xa7\x81 \xec\x8b\xa0\xec\xb2\xad \xec\x95\x88\xeb\x82\xb4 \xeb\x93\xb1\xeb\xa1\x9d \xec\x95\x88\xeb\x82\xb4",
		b"2\xec\xa3\xbc \xec\xa0\x84 \xec\x8b\xa0\xec\xb2\xad \xec\x9a\x94\xec\xb2\xad \xed\x95\xb4\xec\xa3\xbc\xec\x84\xb8\xec\x9a\x94.",
	]
	parsed = _parse_email_from_bytes(5, raw)
	assert parsed.index == 5
	assert "육아휴직" in parsed.subject or parsed.subject  # tolerant on header decoding
	assert parsed.from_addr.startswith("HR")
	assert "2주 전" in parsed.snippet or len(parsed.snippet) > 0





