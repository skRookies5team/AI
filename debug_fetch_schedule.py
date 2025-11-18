"""fetch_schedule_rows 함수 디버깅."""

from __future__ import annotations

import asyncio
from agents.schedule_data import fetch_schedule_rows, ScheduleFilters, get_session, _resolve_employee_ids_by_names
from agents.schedule_langgraph import analyze_query, ScheduleState


async def debug_fetch() -> None:
    """fetch_schedule_rows 함수 디버깅."""
    print("=" * 70)
    print("fetch_schedule_rows 디버깅")
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
        
        # 2. 이름으로 employee_id 찾기 (직접)
        if filters and filters.employee_name_keywords:
            print(f"\n[2] 이름으로 employee_id 찾기 (직접)...")
            resolved_ids = await _resolve_employee_ids_by_names(
                session, filters.employee_name_keywords
            )
            print(f"  찾은 employee_ids: {resolved_ids}")
            
            # 3. 찾은 employee_ids로 필터 생성
            if resolved_ids:
                print(f"\n[3] 찾은 employee_ids로 필터 생성...")
                new_filters = ScheduleFilters(employee_ids=resolved_ids, limit=5)
                print(f"  employee_ids: {new_filters.employee_ids}")
                
                # 4. fetch_schedule_rows 호출
                print(f"\n[4] fetch_schedule_rows 호출...")
                rows = await fetch_schedule_rows(new_filters)
                print(f"  조회된 일정: {len(rows)}개")
                if rows:
                    print(f"  [OK] 일정 조회 성공!")
                    if "employee_name" in rows[0]:
                        print(f"  [OK] employee_name: {rows[0]['employee_name']}")
                else:
                    print(f"  [ERROR] 일정 조회 실패")
            else:
                print(f"  [ERROR] 이름으로 employee_id를 찾지 못했습니다.")
        
        # 5. 원래 필터로 직접 fetch_schedule_rows 호출
        if filters:
            print(f"\n[5] 원래 필터로 fetch_schedule_rows 호출...")
            print(f"  employee_name_keywords: {filters.employee_name_keywords}")
            print(f"  employee_id: {filters.employee_id}")
            print(f"  employee_ids: {filters.employee_ids}")
            try:
                rows = await fetch_schedule_rows(filters)
                print(f"  조회된 일정: {len(rows)}개")
                if rows:
                    print(f"  [OK] 일정 조회 성공!")
                else:
                    print(f"  [ERROR] 일정 조회 실패 - 빈 결과")
            except Exception as e:
                print(f"  [ERROR] 예외 발생: {e}")
                import traceback
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(debug_fetch())

