"""회의실 에이전트 래퍼 (schedule_master 연동용)"""
from __future__ import annotations

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

# 로거 설정
logger = logging.getLogger(__name__)

# 전역 ThreadPoolExecutor
_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="meeting_agent")

# 세션별 대화 상태 보관
_SESSION_MEMORY: Dict[str, Dict[str, Any]] = {}


def _get_session_memory(session_id: str) -> Dict[str, Any]:
    return _SESSION_MEMORY.setdefault(session_id, {})


def _update_session_memory(session_id: str, payload: Dict[str, Any]) -> None:
    memory = _SESSION_MEMORY.setdefault(session_id, {})
    memory.update(payload)
    # 모든 필드가 비어 있으면 메모리 제거
    if not any(memory.values()):
        _SESSION_MEMORY.pop(session_id, None)


def _reset_session_memory(session_id: str) -> None:
    _SESSION_MEMORY.pop(session_id, None)


def _isolated_runner(user_text: str, context: Optional[Dict[str, Any]] = None) -> Dict:
    """
    완전히 격리된 환경에서 비동기 코드 실행
    - 새 이벤트 루프
    - 새 DB 연결
    - 완전한 정리
    """
    logger.info(f"🔧 _isolated_runner 호출됨 - user_text: {user_text}, context: {context}")
    
    # 새 이벤트 루프 생성 (재사용 절대 금지!)
    loop = asyncio.new_event_loop()
    
    try:
        # 이 스레드의 이벤트 루프로 설정
        asyncio.set_event_loop(loop)
        
        # 비동기 실행
        result = loop.run_until_complete(_async_workflow(user_text, context))
        return result
        
    except Exception as e:
        import traceback
        logger.error(f"❌ _isolated_runner 오류: {e}", exc_info=True)
        return {
            "ok": False,
            "error": f"실행 오류: {str(e)}",
            "traceback": traceback.format_exc(),
        }
    finally:
        try:
            # 1. 모든 pending 태스크 취소
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            
            # pending 태스크들이 완료될 때까지 대기
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            
            # 2. async generator 정리
            loop.run_until_complete(loop.shutdown_asyncgens())
            
            # 3. executor 정리 (Python 3.9+)
            if sys.version_info >= (3, 9):
                loop.run_until_complete(loop.shutdown_default_executor())
            
        except Exception:
            pass
        finally:
            # 4. 루프 완전 종료
            loop.close()
            
            # 5. 이벤트 루프 제거
            asyncio.set_event_loop(None)


async def _async_workflow(user_text: str, context: Optional[Dict[str, Any]] = None) -> Dict:
    """비동기 워크플로 실행"""
    logger.info(f"🎯 _async_workflow 시작 - context: {context}")
    
    from .meeting_room_agent.workflow import build_meeting_room_graph
    from .meeting_room_agent.schemas import AgentState
    from .meeting_room_agent.tools import DBPool
    from .meeting_room_agent.settings import settings
    
    db_initialized = False
    
    try:
        # DB 연결 풀 생성 (매번 새로 생성!)
        await DBPool.init_pool()
        db_initialized = True
        
        # 워크플로 빌드
        graph = build_meeting_room_graph()
        
        # 🆕 state 구성: user_input + context 병합
        state: AgentState = {"user_input": user_text}

        session_id: Optional[str] = None
        
        logger.info(f"📦 초기 state: {state}")
        
        if context:
            logger.info(f"📋 context 있음: {context}")
            
            # context에 reservation이 있으면 state에 직접 추가
            if "reservation" in context:
                state["reservation"] = context["reservation"]
                logger.info(f"✅ reservation 추가됨: {state['reservation']}")
            
            session_id = context.get("session_id")
            if session_id:
                state["session_id"] = session_id
                memory = _get_session_memory(session_id)
                for key in ("reservation_candidate", "lookup_candidate", "pending_field", "pending_intent"):
                    if key in memory:
                        state[key] = memory[key]
                        logger.info("✅ 세션 메모리 로드: %s=%s", key, memory[key])
            
            # 나머지 context도 병합
            for key, value in context.items():
                if key not in state:
                    state[key] = value
                    logger.info(f"✅ {key} 추가됨")
        
        logger.info(f"🎯 최종 state: {state}")
        
        # 실행
        result = await graph.ainvoke(state)
        
        logger.info(f"✅ 워크플로 실행 완료: {result.get('response', '')[:100]}")

        if session_id:
            memory_payload = {
                "reservation_candidate": result.get("reservation_candidate"),
                "lookup_candidate": result.get("lookup_candidate"),
                "pending_field": result.get("pending_field"),
                "pending_intent": result.get("pending_intent"),
            }
            if any(memory_payload.values()):
                _update_session_memory(session_id, memory_payload)
                logger.info("🧠 세션 메모리 저장: %s", memory_payload)
            else:
                _reset_session_memory(session_id)
                logger.info("🧠 세션 메모리 초기화")
        
        return {
            "ok": True,
            "message": result.get("response", "응답을 생성하지 못했습니다."),
            "intent": result.get("intent"),
            "recommended_rooms": result.get("recommended_rooms", []),
            "reservations": result.get("reservations", []),
            "reservation_candidate": result.get("reservation_candidate"),
            "lookup_candidate": result.get("lookup_candidate"),
            "pending_field": result.get("pending_field"),
            "pending_intent": result.get("pending_intent"),
        }
        
    except Exception as e:
        import traceback
        error_str = str(e)
        logger.error(f"❌ _async_workflow 오류: {e}", exc_info=True)
        
        # 데이터베이스 연결 오류에 대한 친화적인 메시지
        if "Can't connect to MySQL server" in error_str or "2003" in error_str:
            error_msg = (
                "⚠️ 데이터베이스 서버에 연결할 수 없습니다.\n\n"
                f"서버 주소: {settings.db_host}:{settings.db_port}\n"
                "가능한 원인:\n"
                "1. 데이터베이스 서버가 다운되었거나 접근 불가능\n"
                "2. 방화벽이 포트를 차단하고 있음\n"
                "3. 네트워크 연결 문제\n"
                "4. 서버가 해당 IP에서의 연결을 허용하지 않음\n\n"
                "시스템 관리자에게 문의하거나 네트워크 연결을 확인해주세요."
            )
        elif "Access denied" in error_str or "1045" in error_str:
            error_msg = (
                "⚠️ 데이터베이스 접근이 거부되었습니다.\n\n"
                "가능한 원인:\n"
                "1. 잘못된 사용자명 또는 비밀번호\n"
                "2. 사용자 권한 부족\n"
                "3. IP 주소에서의 접속이 허용되지 않음\n\n"
                "데이터베이스 관리자에게 접근 권한을 확인해주세요."
            )
        else:
            error_msg = f"회의실 에이전트 오류: {error_str}"
        
        return {
            "ok": False,
            "error": error_msg,
            "traceback": traceback.format_exc(),
            "reservation_candidate": None,
            "lookup_candidate": None,
            "pending_field": None,
            "pending_intent": None,
        }
    finally:
        # DB 연결 풀 정리 (반드시!)
        if db_initialized:
            try:
                await DBPool.close_pool()
            except Exception:
                pass


def meeting_room_query(user_text: str, context: Optional[Dict[str, Any]] = None) -> Dict:
    """
    동기 래퍼: schedule_master에서 호출 가능
    ThreadPoolExecutor를 사용하여 별도 스레드에서 실행
    
    Args:
        user_text: 사용자 입력
        context: 추가 컨텍스트 (예약 정보 등)
    """
    logger.info(f"🚀 meeting_room_query 호출됨 - user_text: {user_text}, context: {context}")
    
    try:
        # ThreadPoolExecutor에서 실행 (FastAPI와 격리)
        future = _executor.submit(_isolated_runner, user_text, context)
        result = future.result(timeout=30)
        
        logger.info(f"✅ meeting_room_query 완료: {result.get('ok', False)}")
        return result
        
    except TimeoutError:
        logger.error("❌ Timeout!")
        return {
            "ok": False,
            "error": "요청 시간 초과 (30초)",
        }
    except Exception as e:
        import traceback
        logger.error(f"❌ meeting_room_query 오류: {e}", exc_info=True)
        return {
            "ok": False,
            "error": f"실행 실패: {str(e)}",
            "traceback": traceback.format_exc(),
        }