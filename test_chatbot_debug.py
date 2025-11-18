"""챗봇 질의 파싱 및 필터 디버깅."""

from __future__ import annotations

import asyncio
import json
from agents.schedule_langgraph import analyze_query, ScheduleState
from agents.schedule_data import fetch_schedule_rows


async def debug_query(query: str) -> None:
    """질의 파싱 및 필터 디버깅."""
    print("=" * 70)
    print(f"질의 디버깅: {query}")
    print("=" * 70)
    
    # 1. 질의 파싱
    print("\n[1] 질의 파싱...")
    state = ScheduleState(query=query)
    state = analyze_query(state)
    
    filters = state.filters
    if filters:
        print(f"  employee_id: {filters.employee_id}")
        print(f"  employee_ids: {filters.employee_ids}")
        print(f"  employee_name_keywords: {filters.employee_name_keywords}")
        print(f"  start_at: {filters.start_at}")
        print(f"  end_at: {filters.end_at}")
        print(f"  keyword: {filters.keyword}")
        print(f"  schedule_types: {filters.schedule_types}")
        print(f"  limit: {filters.limit}")
    else:
        print("  [ERROR] 필터가 생성되지 않았습니다!")
        return
    
    # 2. 실제 조회
    print("\n[2] 실제 데이터 조회...")
    try:
        rows = await fetch_schedule_rows(filters)
        print(f"  조회된 일정 수: {len(rows)}")
        
        if rows:
            print("\n  첫 번째 일정:")
            print(json.dumps(rows[0], ensure_ascii=False, indent=2, default=str))
        else:
            print("\n  [WARNING] 조회된 일정이 없습니다!")
            print("\n  가능한 원인:")
            if filters.employee_name_keywords:
                print(f"    - 이름 '{filters.employee_name_keywords}'으로 employee_id를 찾지 못함")
            if filters.start_at or filters.end_at:
                print(f"    - 날짜 필터가 너무 엄격함 (start: {filters.start_at}, end: {filters.end_at})")
            if filters.schedule_types:
                print(f"    - 일정 유형 필터: {filters.schedule_types}")
            if filters.keyword:
                print(f"    - 키워드 필터: {filters.keyword}")
            
            # 필터 없이 조회해보기
            print("\n  [3] 필터 없이 전체 조회 테스트...")
            empty_filters = type(filters)(limit=5)
            all_rows = await fetch_schedule_rows(empty_filters)
            print(f"    전체 조회 결과: {len(all_rows)}개")
            
    except Exception as e:
        print(f"  [ERROR] 조회 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_queries = [
        "사번 4001 일정 알려줘",
        "사번 4002 일정 있어?",
        "오늘 일정 알려줘",
    ]
    
    for query in test_queries:
        asyncio.run(debug_query(query))
        print("\n" + "=" * 70 + "\n")

