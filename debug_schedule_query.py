"""일정 조회 문제 디버깅 스크립트."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_langgraph import analyze_query, ScheduleState, load_schedule
from agents.schedule_data import fetch_schedule_rows, ScheduleFilters


async def debug_schedule_query(query: str) -> None:
    """일정 조회 과정을 단계별로 디버깅."""
    print("=" * 70)
    print(f"일정 조회 디버깅: {query}")
    print("=" * 70)
    
    # 1. 질의 파싱
    print("\n[1단계] 질의 파싱...")
    state = ScheduleState(query=query)
    state = analyze_query(state)
    
    filters = state.filters
    if not filters:
        print("  [오류] 필터가 생성되지 않았습니다!")
        return
    
    print(f"  employee_id: {filters.employee_id}")
    print(f"  employee_ids: {filters.employee_ids}")
    print(f"  employee_name_keywords: {filters.employee_name_keywords}")
    print(f"  start_at: {filters.start_at}")
    print(f"  end_at: {filters.end_at}")
    print(f"  keyword: {filters.keyword}")
    print(f"  schedule_types: {filters.schedule_types}")
    print(f"  limit: {filters.limit}")
    
    # 2. 필터 없이 전체 조회 테스트
    print("\n[2단계] 필터 없이 전체 조회 테스트...")
    try:
        empty_filters = ScheduleFilters(limit=5)
        all_rows = await fetch_schedule_rows(empty_filters)
        print(f"  전체 일정 개수: {len(all_rows)}개")
        if all_rows:
            print("  첫 번째 일정:")
            print(json.dumps(all_rows[0], ensure_ascii=False, indent=2, default=str))
    except Exception as e:
        print(f"  [오류] 전체 조회 실패: {e}")
        import traceback
        traceback.print_exc()
    
    # 3. 실제 필터로 조회
    print("\n[3단계] 실제 필터로 조회...")
    try:
        rows = await fetch_schedule_rows(filters)
        print(f"  조회된 일정 개수: {len(rows)}개")
        
        if rows:
            print("  첫 번째 일정:")
            print(json.dumps(rows[0], ensure_ascii=False, indent=2, default=str))
        else:
            print("  [경고] 조회된 일정이 없습니다!")
            print("\n  원인 분석:")
            
            # employee_id 필터 확인
            if filters.employee_id:
                print(f"    - employee_id 필터: {filters.employee_id}")
                # 해당 employee_id의 일정 개수 확인
                test_filter = ScheduleFilters(employee_id=filters.employee_id, limit=1)
                test_rows = await fetch_schedule_rows(test_filter)
                if test_rows:
                    print(f"      → employee_id {filters.employee_id}의 일정은 존재합니다!")
                else:
                    print(f"      → employee_id {filters.employee_id}의 일정이 없습니다.")
            
            # employee_name_keywords 확인
            if filters.employee_name_keywords:
                print(f"    - employee_name_keywords: {filters.employee_name_keywords}")
                # 이름으로 employee_id 찾기 테스트
                from agents.schedule_data import _resolve_employee_ids_by_names, get_session
                async with get_session() as session:
                    resolved_ids = await _resolve_employee_ids_by_names(
                        session, filters.employee_name_keywords
                    )
                    if resolved_ids:
                        print(f"      → 찾은 employee_ids: {resolved_ids}")
                        test_filter = ScheduleFilters(employee_ids=resolved_ids, limit=1)
                        test_rows = await fetch_schedule_rows(test_filter)
                        if test_rows:
                            print(f"      → 해당 employee_ids의 일정은 존재합니다!")
                        else:
                            print(f"      → 해당 employee_ids의 일정이 없습니다.")
                    else:
                        print(f"      → 이름으로 employee_id를 찾지 못했습니다.")
            
            # 날짜 필터 확인
            if filters.start_at or filters.end_at:
                print(f"    - 날짜 필터: start_at={filters.start_at}, end_at={filters.end_at}")
                # 날짜 필터 없이 조회
                no_date_filter = ScheduleFilters(
                    employee_id=filters.employee_id,
                    employee_ids=filters.employee_ids,
                    limit=5
                )
                no_date_rows = await fetch_schedule_rows(no_date_filter)
                if no_date_rows:
                    print(f"      → 날짜 필터 없이 조회 시 {len(no_date_rows)}개 일정 존재")
                    print(f"      → 날짜 필터가 너무 엄격할 수 있습니다.")
            
            # schedule_types 필터 확인
            if filters.schedule_types:
                print(f"    - schedule_types 필터: {filters.schedule_types}")
                # 타입 필터 없이 조회
                no_type_filter = ScheduleFilters(
                    employee_id=filters.employee_id,
                    employee_ids=filters.employee_ids,
                    limit=5
                )
                no_type_rows = await fetch_schedule_rows(no_type_filter)
                if no_type_rows:
                    print(f"      → 타입 필터 없이 조회 시 {len(no_type_rows)}개 일정 존재")
                    print(f"      → 타입 필터가 너무 엄격할 수 있습니다.")
            
    except Exception as e:
        print(f"  [오류] 조회 실패: {e}")
        import traceback
        traceback.print_exc()
    
    # 4. LangGraph 워크플로우 전체 실행
    print("\n[4단계] LangGraph 워크플로우 전체 실행...")
    try:
        from agents.schedule_langgraph import run_schedule_agent
        result = run_schedule_agent(query)
        print(f"  결과: ok={result.get('ok')}, message={result.get('message', '')[:100]}")
        if result.get('rows'):
            print(f"  조회된 행 수: {len(result['rows'])}")
    except Exception as e:
        print(f"  [오류] 워크플로우 실행 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_queries = [
        "사번 4001 일정 알려줘",
        "사번 4002 일정 있어?",
    ]
    
    for query in test_queries:
        asyncio.run(debug_schedule_query(query))
        print("\n" + "=" * 70 + "\n")

