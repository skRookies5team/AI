"""fetch_schedule_rows 내부 이름 검색 디버깅."""

from __future__ import annotations

import asyncio
from agents.schedule_data import fetch_schedule_rows, ScheduleFilters, get_session, _resolve_employee_ids_by_names
from agents.schedule_langgraph import analyze_query, ScheduleState


async def debug_fetch_internal() -> None:
    """fetch_schedule_rows 내부 이름 검색 디버깅."""
    print("=" * 70)
    print("fetch_schedule_rows 내부 이름 검색 디버깅")
    print("=" * 70)
    
    # 실제 이름 가져오기
    async with get_session() as session:
        from sqlalchemy import text
        result = await session.execute(
            text("SELECT name FROM employee WHERE employee_id = 4001 LIMIT 1")
        )
        test_name = result.scalar()
        if not test_name:
            print("[오류] 테스트할 직원이 없습니다.")
            return
        
        print(f"\n테스트 이름: {test_name}")
        
        # 1. 질의 파싱
        query = f"{test_name} 일정 알려줘"
        print(f"\n[1] 질의 파싱: '{query}'")
        state = ScheduleState(query=query)
        state = analyze_query(state)
        
        filters = state.filters
        print(f"  employee_name_keywords: {filters.employee_name_keywords if filters else None}")
        
        # 2. fetch_schedule_rows 내부 로직 시뮬레이션
        if filters and filters.employee_name_keywords:
            print(f"\n[2] fetch_schedule_rows 내부 로직 시뮬레이션...")
            print(f"  employee_ids (시작): {filters.employee_ids}")
            print(f"  employee_name_keywords: {filters.employee_name_keywords}")
            
            # fetch_schedule_rows 내부와 동일한 로직
            if (
                filters.employee_ids is None
                and filters.employee_name_keywords
                and any(filters.employee_name_keywords)
            ):
                print(f"  [조건 만족] 이름 검색 시도...")
                resolved_ids = await _resolve_employee_ids_by_names(
                    session, filters.employee_name_keywords
                )
                print(f"  찾은 employee_ids: {resolved_ids}")
                
                if resolved_ids:
                    filters.employee_ids = resolved_ids
                    print(f"  [성공] filters.employee_ids 설정: {filters.employee_ids}")
                else:
                    print(f"  [실패] 이름 검색 실패")
            
            # 3. 설정된 employee_ids로 조회
            if filters.employee_ids:
                print(f"\n[3] 설정된 employee_ids로 조회...")
                test_filters = ScheduleFilters(employee_ids=filters.employee_ids, limit=5)
                rows = await fetch_schedule_rows(test_filters)
                print(f"  조회된 일정: {len(rows)}개")
                if rows:
                    print(f"  [OK] 일정 조회 성공!")
                    if "employee_name" in rows[0]:
                        print(f"  [OK] employee_name: {rows[0]['employee_name']}")
        
        # 4. 원래 필터로 직접 fetch_schedule_rows 호출
        if filters:
            print(f"\n[4] 원래 필터로 fetch_schedule_rows 직접 호출...")
            print(f"  employee_name_keywords: {filters.employee_name_keywords}")
            print(f"  employee_ids (호출 전): {filters.employee_ids}")
            try:
                rows = await fetch_schedule_rows(filters)
                print(f"  조회된 일정: {len(rows)}개")
                print(f"  employee_ids (호출 후): {filters.employee_ids}")
                if rows:
                    print(f"  [OK] 일정 조회 성공!")
                else:
                    print(f"  [ERROR] 일정 조회 실패 - 빈 결과")
            except Exception as e:
                print(f"  [ERROR] 예외 발생: {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(debug_fetch_internal())

