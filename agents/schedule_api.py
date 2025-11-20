"""Spring Boot API를 호출하여 일정 등록/취소를 처리하는 모듈."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import httpx


# Spring Boot API 기본 URL
SPRING_BOOT_API_URL = os.getenv(
    "SPRING_BOOT_API_URL",
    "http://localhost:8080"  # 기본값, 환경변수로 오버라이드 가능
)


def create_schedule(
    employee_id: int,
    title: str,
    description: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    schedule_type: Optional[str] = None,
    location: Optional[str] = None,
) -> dict[str, Any]:
    """일정을 등록한다.
    
    Args:
        employee_id: 직원 ID
        title: 일정 제목
        description: 일정 설명
        start_time: 시작 시간
        end_time: 종료 시간
        schedule_type: 일정 유형 (예: "업무", "회의", "출장", "휴가")
        location: 장소
    
    Returns:
        등록된 일정 정보를 담은 딕셔너리
    """
    url = f"{SPRING_BOOT_API_URL}/api/schedules"
    
    # 요청 본문 구성
    payload: dict[str, Any] = {
        "employeeId": employee_id,
        "title": title,
        "confirmed": True,  # 일정 등록 시 확인된 상태로 설정 (Spring Boot 요구사항)
    }
    
    if description:
        payload["description"] = description
    
    if start_time:
        # ISO 8601 형식으로 변환 (Spring Boot LocalDateTime 형식: YYYY-MM-DDTHH:mm:ss)
        # 날짜만 있는 경우 시간을 00:00:00으로 설정
        if start_time.hour == 0 and start_time.minute == 0 and start_time.second == 0:
            # LocalDateTime 형식으로 변환 (ISO 8601 형식)
            payload["startTime"] = start_time.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            payload["startTime"] = start_time.strftime("%Y-%m-%dT%H:%M:%S")
    
    if end_time:
        # ISO 8601 형식으로 변환
        if end_time.hour == 0 and end_time.minute == 0 and end_time.second == 0:
            # 종료일의 경우 23:59:59로 설정 (하루 종일 일정)
            payload["endTime"] = end_time.strftime("%Y-%m-%dT23:59:59")
        else:
            payload["endTime"] = end_time.strftime("%Y-%m-%dT%H:%M:%S")
    
    if schedule_type:
        payload["scheduleType"] = schedule_type
    
    if location:
        payload["location"] = location
    
    # 필수 필드 확인 및 디버깅 정보
    import os
    if os.getenv("DEBUG_SCHEDULE") == "true":
        print(f"[DEBUG] 일정 등록 요청 payload: {payload}")
        print(f"[DEBUG] employee_id: {employee_id}, title: {title}")
        print(f"[DEBUG] start_time: {start_time}, end_time: {end_time}")
    
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            return {
                "ok": True,
                "message": f"일정이 성공적으로 등록되었습니다. (ID: {result.get('scheduleId', 'N/A')})",
                "schedule": result,
            }
    except httpx.HTTPStatusError as e:
        error_msg = f"일정 등록 실패: HTTP {e.response.status_code}"
        try:
            error_detail = e.response.json()
            # Spring Boot validation 오류 메시지 추출
            if "message" in error_detail:
                # Spring Boot의 비즈니스 로직 오류 메시지를 그대로 사용
                error_msg = error_detail["message"]
            # errors 배열에서 상세 오류 메시지 추출
            if "errors" in error_detail and isinstance(error_detail["errors"], list) and len(error_detail["errors"]) > 0:
                error_messages = []
                for err in error_detail["errors"]:
                    if isinstance(err, dict):
                        field = err.get("field", "unknown")
                        reason = err.get("reason", "validation error")
                        error_messages.append(f"{field}: {reason}")
                if error_messages:
                    error_msg = f"일정 등록 실패: {', '.join(error_messages)}"
            elif isinstance(error_detail, list) and len(error_detail) > 0:
                # Validation 오류 리스트인 경우
                first_error = error_detail[0]
                if isinstance(first_error, dict) and "defaultMessage" in first_error:
                    error_msg = f"일정 등록 실패: {first_error['defaultMessage']}"
                elif isinstance(first_error, dict) and "message" in first_error:
                    error_msg = f"일정 등록 실패: {first_error['message']}"
            # 응답 본문 전체를 디버깅용으로 포함
            import os
            if os.getenv("DEBUG_SCHEDULE") == "true":
                print(f"[DEBUG] Spring Boot 오류 응답: {error_detail}")
                print(f"[DEBUG] 요청 payload: {payload}")
        except Exception:
            pass
        
        return {
            "ok": False,
            "message": error_msg,
            "error": error_msg,
        }
    except httpx.RequestError as e:
        error_msg = f"일정 등록 요청 실패: {e}"
        return {
            "ok": False,
            "message": error_msg,
            "error": error_msg,
        }
    except Exception as e:
        error_msg = f"일정 등록 중 오류 발생: {e}"
        return {
            "ok": False,
            "message": error_msg,
            "error": error_msg,
        }


def cancel_schedule(
    schedule_id: int,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """일정을 취소한다.
    
    Args:
        schedule_id: 취소할 일정 ID
        reason: 취소 사유
    
    Returns:
        취소된 일정 정보를 담은 딕셔너리
    """
    url = f"{SPRING_BOOT_API_URL}/api/schedules/{schedule_id}/cancel"
    
    # reason이 필수일 수 있으므로 기본값 제공
    # confirmed 필드도 필수로 요구됨 (Spring Boot 요구사항)
    payload: dict[str, Any] = {
        "reason": reason or "일정 취소 요청",  # Spring Boot가 reason을 필수로 요구할 수 있음
        "confirmed": True,  # 일정 취소 시 사용자 확인 완료 상태로 설정 (Spring Boot 요구사항)
    }
    
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            
            return {
                "ok": True,
                "message": f"일정이 성공적으로 취소되었습니다. (ID: {schedule_id})",
                "schedule": result,
            }
    except httpx.HTTPStatusError as e:
        error_msg = f"일정 취소 실패: HTTP {e.response.status_code}"
        try:
            error_detail = e.response.json()
            # Spring Boot validation 오류 메시지 추출
            if "message" in error_detail:
                # Spring Boot의 비즈니스 로직 오류 메시지를 그대로 사용
                error_msg = error_detail["message"]
            # errors 배열에서 상세 오류 메시지 추출
            if "errors" in error_detail and isinstance(error_detail["errors"], list) and len(error_detail["errors"]) > 0:
                error_messages = []
                for err in error_detail["errors"]:
                    if isinstance(err, dict):
                        field = err.get("field", "unknown")
                        reason = err.get("reason", "validation error")
                        error_messages.append(f"{field}: {reason}")
                if error_messages:
                    error_msg = f"일정 취소 실패: {', '.join(error_messages)}"
            # 디버깅 정보 출력
            import os
            if os.getenv("DEBUG_SCHEDULE") == "true":
                print(f"[DEBUG] Spring Boot 취소 오류 응답: {error_detail}")
                print(f"[DEBUG] 요청 payload: {payload}")
                print(f"[DEBUG] schedule_id: {schedule_id}")
        except Exception:
            pass
        
        return {
            "ok": False,
            "message": error_msg,
            "error": error_msg,
        }
    except httpx.RequestError as e:
        error_msg = f"일정 취소 요청 실패: {e}"
        return {
            "ok": False,
            "message": error_msg,
            "error": error_msg,
        }
    except Exception as e:
        error_msg = f"일정 취소 중 오류 발생: {e}"
        return {
            "ok": False,
            "message": error_msg,
            "error": error_msg,
        }

