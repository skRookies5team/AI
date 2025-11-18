"""_build_query 함수 디버깅."""

from __future__ import annotations

import asyncio
from agents.schedule_data import get_session, _get_schedule_columns, _get_employee_columns, _build_query, ScheduleFilters


async def debug_query_build() -> None:
    """_build_query 함수 디버깅."""
    print("=" * 70)
    print("_build_query 함수 디버깅")
    print("=" * 70)
    
    async with get_session() as session:
        # employee_id 4001로 필터 생성
        filters = ScheduleFilters(employee_ids=[4001], limit=5)
        
        columns = await _get_schedule_columns(session)
        employee_columns = await _get_employee_columns(session)
        
        print(f"\n[1] 컬럼 정보:")
        print(f"  schedule 컬럼: {columns[:5]}...")
        print(f"  employee 컬럼: {employee_columns[:5]}...")
        
        print(f"\n[2] 필터 정보:")
        print(f"  employee_ids: {filters.employee_ids}")
        print(f"  employee_id: {filters.employee_id}")
        print(f"  employee_name_keywords: {filters.employee_name_keywords}")
        
        print(f"\n[3] _build_query 호출...")
        query, params, preferred_order, table_prefix = _build_query(filters, columns, employee_columns)
        
        print(f"\n[4] 생성된 쿼리:")
        print(f"  {query}")
        print(f"\n[5] 파라미터:")
        for key, value in params.items():
            print(f"  {key}: {value}")
        print(f"\n[6] 기타 정보:")
        print(f"  preferred_order: {preferred_order}")
        print(f"  table_prefix: {table_prefix}")
        
        # 실제 쿼리 실행 테스트
        print(f"\n[7] 실제 쿼리 실행 테스트...")
        from sqlalchemy import text
        order_clause = f" ORDER BY {table_prefix}{preferred_order} ASC" if preferred_order else ""
        final_query = f"{query}{order_clause} LIMIT 5"
        print(f"  최종 쿼리: {final_query}")
        
        result = await session.execute(text(final_query), params)
        rows = result.fetchall()
        print(f"  조회된 행: {len(rows)}개")
        if rows:
            print(f"  [OK] 쿼리 실행 성공!")
            # 첫 번째 행 확인
            result_mappings = await session.execute(text(final_query), params)
            first_row = result_mappings.mappings().first()
            if first_row:
                print(f"  첫 번째 행 컬럼: {list(first_row.keys())[:10]}")
                if "employee_name" in first_row:
                    print(f"  [OK] employee_name: {first_row['employee_name']}")
        else:
            print(f"  [ERROR] 쿼리 실행 결과 없음")


if __name__ == "__main__":
    asyncio.run(debug_query_build())

