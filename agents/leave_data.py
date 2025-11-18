from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Literal, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)

# v2 데이터셋 경로
LEAVE_BALANCE_V2_PATH = os.path.join(ROOT_DIR, "leave_balance_v2.csv")
LEAVE_REQUEST_V2_PATH = os.path.join(ROOT_DIR, "leave_request_v2.csv")


def _read_csv_utf8_sig(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    # Excel과 한글 호환을 위해 utf-8-sig 우선, 실패 시 cp949로 폴백
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp949")


def _write_csv_utf8_sig(df: pd.DataFrame, path: str) -> None:
    """
    Windows에서 파일이 Excel 등에 의해 잠겨 PermissionError가 날 수 있어
    임시 파일에 저장 후 os.replace로 교체, 여러 번 재시도한다.
    """
    tmp_path = f"{path}.tmp"
    last_err: Optional[BaseException] = None  # type: ignore[name-defined]
    for _ in range(5):
        try:
            df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
            # 원자적 교체 시도
            os.replace(tmp_path, path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.8)
        except Exception as e:  # 기타 예외는 즉시 보고
            last_err = e
            break
    # 최종 실패 시 예외 재발생
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    if last_err:
        raise last_err


def load_balance() -> pd.DataFrame:
    return _read_csv_utf8_sig(LEAVE_BALANCE_V2_PATH)


def load_requests() -> pd.DataFrame:
    return _read_csv_utf8_sig(LEAVE_REQUEST_V2_PATH)


def save_balance(df: pd.DataFrame) -> None:
    _write_csv_utf8_sig(df, LEAVE_BALANCE_V2_PATH)


def append_request(row: dict) -> None:
    df = load_requests()
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _write_csv_utf8_sig(df, LEAVE_REQUEST_V2_PATH)


def compute_used_pending_for_employee(employee_id: int) -> Tuple[float, float]:
    """
    v2 요청 데이터에서 직원의 used(승인), pending(대기) 일수를 합산.
    반차는 0.5일 등 소수점 지원.
    """
    req = load_requests()
    if req.empty:
        return 0.0, 0.0
    req_emp = req[req["employee_id"] == employee_id]
    used = (
        req_emp[req_emp["status"] == "승인"]["total_days"].astype(float).sum()
        if not req_emp.empty
        else 0.0
    )
    pending = (
        req_emp[req_emp["status"] == "대기"]["total_days"].astype(float).sum()
        if not req_emp.empty
        else 0.0
    )
    return float(used), float(pending)


def ensure_balance_row(employee_id: int, default_entitlement: float = 15.0) -> pd.DataFrame:
    """
    leave_balance_v2.csv에 해당 직원 행이 없으면 기본 값으로 생성.
    """
    bal = load_balance()
    if bal.empty:
        bal = pd.DataFrame(
            columns=[
                "employee_id",
                "year",
                "total_entitlement_days",
                "carried_over_days",
                "used_days",
                "pending_days",
                "remaining_days",
            ]
        )

    if (bal["employee_id"] == employee_id).any():
        return bal

    used, pending = compute_used_pending_for_employee(employee_id)
    remaining = float(default_entitlement) - used - pending
    new_row = {
        "employee_id": employee_id,
        "year": datetime.now().year,
        "total_entitlement_days": float(default_entitlement),
        "carried_over_days": 0.0,
        "used_days": float(used),
        "pending_days": float(pending),
        "remaining_days": float(remaining),
    }
    bal = pd.concat([bal, pd.DataFrame([new_row])], ignore_index=True)
    save_balance(bal)
    return bal


def get_balance(employee_id: int) -> Optional[dict]:
    bal = load_balance()
    if bal.empty:
        return None
    row = bal[bal["employee_id"] == employee_id]
    if row.empty:
        return None
    r = row.iloc[0].to_dict()
    # 숫자형 보장
    for k in ["total_entitlement_days", "carried_over_days", "used_days", "pending_days", "remaining_days"]:
        r[k] = float(r[k])
    r["year"] = int(r["year"])
    r["employee_id"] = int(r["employee_id"])
    return r


def update_balance_from_requests(employee_id: int) -> dict:
    """
    요청 데이터 기준으로 사용/대기/잔여를 재계산하여 저장 후 반환.
    """
    bal = ensure_balance_row(employee_id)
    used, pending = compute_used_pending_for_employee(employee_id)

    idx = bal.index[bal["employee_id"] == employee_id][0]
    total = float(bal.at[idx, "total_entitlement_days"])
    bal.at[idx, "used_days"] = float(used)
    bal.at[idx, "pending_days"] = float(pending)
    bal.at[idx, "remaining_days"] = float(total - used - pending)
    save_balance(bal)
    return get_balance(employee_id) or {}


def make_request_id(employee_id: int) -> str:
    # 간단한 unique id
    ts = int(time.time())
    return f"LR{datetime.now().year}-{employee_id}-{ts}"


def create_leave_request(
    employee_id: int,
    leave_type: Literal["연차", "반차", "병가", "경조사"],
    start_date: str,
    end_date: str,
    total_days: float,
    reason: str = "개인 사정",
    status: Literal["대기", "승인", "반려", "취소"] = "대기",
) -> dict:
    """
    신규 휴가 요청을 v2 CSV에 추가하고, 잔여/대기를 갱신한다.
    날짜 포맷: YYYY-MM-DD
    """
    request = {
        "request_id": make_request_id(employee_id),
        "employee_id": int(employee_id),
        "request_date": datetime.now().strftime("%Y-%m-%d"),
        "leave_type": leave_type,
        "start_date": start_date,
        "end_date": end_date,
        "total_days": float(total_days),
        "status": status,
        "reason": reason,
    }
    append_request(request)
    # 상태 반영
    updated = update_balance_from_requests(employee_id)
    return {"request": request, "balance": updated}

