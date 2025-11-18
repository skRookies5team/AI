"""이름 검색 상세 디버깅."""

from __future__ import annotations

import asyncio
from agents.schedule_data import get_session, _get_employee_columns, _match_column, _EMPLOYEE_ID_CANDIDATES, _EMPLOYEE_NAME_CANDIDATES, _resolve_employee_ids_by_names
from sqlalchemy import text


async def debug_name_search_detail() -> None:
    """이름 검색 상세 디버깅."""
    print("=" * 70)
    print("이름 검색 상세 디버깅")
    print("=" * 70)
    
    async with get_session() as session:
        # 실제 DB에 존재하는 이름들 가져오기
        result = await session.execute(text("""
            SELECT DISTINCT e.name, e.employee_id
            FROM employee e
            INNER JOIN schedule s ON e.employee_id = s.employee_id
            LIMIT 5
        """))
        employees = result.fetchall()
        
        print("\n테스트할 직원 목록:")
        for name, emp_id in employees:
            print(f"  - {name} (사번: {emp_id})")
        
        # 각 이름으로 검색 테스트
        for name, emp_id in employees[:3]:
            print(f"\n{'='*70}")
            print(f"이름 검색 테스트: '{name}' (예상 사번: {emp_id})")
            print("=" * 70)
            
            # 1. 컬럼 확인
            columns = await _get_employee_columns(session)
            id_column = _match_column(columns, _EMPLOYEE_ID_CANDIDATES)
            name_column = _match_column(columns, _EMPLOYEE_NAME_CANDIDATES)
            print(f"\n[1] 컬럼 매칭:")
            print(f"  id_column: {id_column}")
            print(f"  name_column: {name_column}")
            
            # 2. 직접 SQL 쿼리 테스트
            print(f"\n[2] 직접 SQL 쿼리 테스트:")
            query = text(f"SELECT {id_column} FROM employee WHERE {name_column} LIKE :pattern LIMIT 5")
            result = await session.execute(query, {"pattern": f"%{name}%"})
            rows = result.fetchall()
            print(f"  '{name}' 검색 결과: {len(rows)}개")
            for row in rows:
                print(f"    - employee_id: {row[0]}")
            
            # 3. _resolve_employee_ids_by_names 함수 테스트
            print(f"\n[3] _resolve_employee_ids_by_names 함수 테스트:")
            resolved_ids = await _resolve_employee_ids_by_names(session, [name])
            print(f"  찾은 employee_ids: {resolved_ids}")
            if emp_id in resolved_ids:
                print(f"  [OK] 예상한 employee_id {emp_id} 포함됨!")
            else:
                print(f"  [WARNING] 예상한 employee_id {emp_id}가 포함되지 않음")


if __name__ == "__main__":
    asyncio.run(debug_name_search_detail())

