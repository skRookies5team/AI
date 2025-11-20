from __future__ import annotations

from fastapi.testclient import TestClient

from fastapi_app import app


def test_get_schedule_endpoint(monkeypatch) -> None:
    """GET 일정 엔드포인트가 정상적으로 결과를 반환하는지 확인."""

    def fake_schedule_master(query: str) -> dict:
        assert query == "회의실 일정 알려줘"
        return {"ok": True, "message": "회의 일정", "rows": [{"title": "회의"}]}

    monkeypatch.setattr("fastapi_app.schedule_master", fake_schedule_master)

    client = TestClient(app)
    response = client.get("/api/schedule", params={"query": "회의실 일정 알려줘"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["message"] == "회의 일정"
    assert payload["rows"] == [{"title": "회의"}]


def test_get_schedule_endpoint_requires_query() -> None:
    """공백만 전달되면 400 오류를 반환해야 한다."""

    client = TestClient(app)
    response = client.get("/api/schedule", params={"query": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "query parameter must not be empty."


def test_leave_chat_endpoint(monkeypatch) -> None:
    """휴가 전용 엔드포인트가 schedule_master를 통해 응답하는지 확인."""

    def fake_schedule_master(query: str) -> dict:
        assert query == "사번 4001 연차 잔여 조회"
        return {"ok": True, "message": "잔여 5일", "rows": [{"remaining_days": 5}], "error": None}

    monkeypatch.setattr("fastapi_app.schedule_master", fake_schedule_master)
    client = TestClient(app)

    response = client.post(
        "/api/leave/chat",
        json={"query": "사번 4001 연차 잔여 조회"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["message"] == "잔여 5일"
    assert payload["rows"] == [{"remaining_days": 5}]
    """공백만 전달되면 400 오류를 반환해야 한다."""

    client = TestClient(app)
    response = client.get("/api/schedule", params={"query": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "query parameter must not be empty."
