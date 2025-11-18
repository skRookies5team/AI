"""이름으로 질의하는 기능 디버깅."""

from __future__ import annotations

import asyncio
from agents.schedule_langgraph import analyze_query, ScheduleState
from agents.schedule_data import get_session, _resolve_employee_ids_by_names, fetch_schedule_rows, ScheduleFilters
from sqlalchemy import text


async def debug_name_query() -> None:
    """이름으로 질의하는 기능을 단계별로 디버깅."""
    print("=" * 70)
    print("이름으로 질의 디버깅")
    print("=" * 70)
    
    # 실제 DB에 존재하는 이름 가져오기
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name, employee_id FROM employee WHERE employee_id = 4001 LIMIT 1")
        )
        row = result.first()
        if not row:
            print("[오류] 테스트할 직원이 없습니다.")
            return
        
        test_name = row[0]
        test_id = row[1]
        print(f"\n테스트 이름: {test_name} (사번: {test_id})")
        
        # 질의 생성
        query = f"{test_name} 일정 알려줘"
        print(f"\n[질의] {query}")
        print("-" * 70)
        
        # 1. 질의 파싱
        print("\n[1단계] 질의 파싱...")
        state = ScheduleState(query=query)
        state = analyze_query(state)
        
        if state.filters:
            print(f"  employee_id: {state.filters.employee_id}")
            print(f"  employee_ids: {state.filters.employee_ids}")
            print(f"  employee_name_keywords: {state.filters.employee_name_keywords}")
            print(f"  keyword: {state.filters.keyword}")
            print(f"  schedule_types: {state.filters.schedule_types}")
        else:
            print("  [ERROR] 필터가 생성되지 않았습니다!")
            return
        
        # 2. 이름으로 employee_id 찾기
        if state.filters.employee_name_keywords:
            print(f"\n[2단계] 이름으로 employee_id 찾기...")
            print(f"  검색할 이름: {state.filters.employee_name_keywords}")
            resolved_ids = await _resolve_employee_ids_by_names(
                session, state.filters.employee_name_keywords
            )
            if resolved_ids:
                print(f"  [성공] 찾은 employee_ids: {resolved_ids}")
                if test_id in resolved_ids:
                    print(f"  [OK] 예상한 employee_id {test_id} 포함됨!")
                else:
                    print(f"  [WARNING] 예상한 employee_id {test_id} 없음")
            else:
                print(f"  [ERROR] 이름으로 employee_id를 찾지 못했습니다!")
                # 직접 SQL로 확인
                print(f"\n  [직접 SQL 확인]")
                result = await session.execute(
                    text("SELECT employee_id, name FROM employee WHERE name LIKE :pattern"),
                    {"pattern": f"%{test_name}%"}
                )
                matches = result.fetchall()
                if matches:
                    print(f"    SQL로 찾은 결과: {matches}")
                else:
                    print(f"    SQL로도 찾지 못함")
                return
        
        # 3. 실제 조회
        print(f"\n[3단계] 실제 일정 조회...")
        if state.filters.employee_ids:
            print(f"  employee_ids로 조회: {state.filters.employee_ids}")
            filters = ScheduleFilters(employee_ids=state.filters.employee_ids, limit=5)
            rows = await fetch_schedule_rows(filters)
            print(f"  조회된 일정: {len(rows)}개")
            if rows:
                first = rows[0]
                print(f"  첫 번째 일정:")
                print(f"    employee_id: {first.get('employee_id')}")
                print(f"    employee_name: {first.get('employee_name')}")
                print(f"    title: {first.get('title')}")
                if first.get('employee_name') == test_name:
                    print(f"  [OK] 이름 일치 확인!")
                else:
                    print(f"  [WARNING] 이름 불일치: 예상={test_name}, 실제={first.get('employee_name')}")
            else:
                print(f"  [WARNING] 조회된 일정이 없습니다.")
        else:
            print(f"  [ERROR] employee_ids가 없어서 조회할 수 없습니다.")


if __name__ == "__main__":
    asyncio.run(debug_name_query())
