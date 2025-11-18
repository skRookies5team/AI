"""_resolve_employee_ids_by_names 함수 상세 디버깅."""

from __future__ import annotations

import asyncio
from agents.schedule_data import get_session, _get_employee_columns, _match_column, _EMPLOYEE_ID_CANDIDATES, _EMPLOYEE_NAME_CANDIDATES
from sqlalchemy import text


async def debug_resolve() -> None:
    """_resolve_employee_ids_by_names 함수 상세 디버깅."""
    print("=" * 70)
    print("_resolve_employee_ids_by_names 상세 디버깅")
    print("=" * 70)
    
    async with get_session() as session:
        # 1. employee 컬럼 확인
        print("\n[1] employee 테이블 컬럼 확인...")
        columns = await _get_employee_columns(session)
        print(f"  컬럼 목록: {columns}")
        
        # 2. 컬럼 매칭 확인
        print("\n[2] 컬럼 매칭 확인...")
        id_column = _match_column(columns, _EMPLOYEE_ID_CANDIDATES)
        name_column = _match_column(columns, _EMPLOYEE_NAME_CANDIDATES)
        print(f"  employee_id 컬럼: {id_column}")
        print(f"  employee_name 컬럼: {name_column}")
        
        if not id_column or not name_column:
            print(f"  [ERROR] 컬럼을 찾지 못했습니다!")
            return
        
        # 3. 실제 이름 검색 쿼리 테스트
        print("\n[3] 실제 이름 검색 쿼리 테스트...")
        test_name = "홍길동"
        query = text(
            f"SELECT DISTINCT {id_column} FROM employee "
            f"WHERE {name_column} LIKE :pattern LIMIT 100"
        )
        result = await session.execute(query, {"pattern": f"%{test_name}%"})
        rows = result.fetchall()
        print(f"  '{test_name}' 검색 결과: {len(rows)}개")
        for row in rows[:5]:
            print(f"    - employee_id: {row[0]}")
        
        # 4. _resolve_employee_ids_by_names 함수 직접 테스트
        print("\n[4] _resolve_employee_ids_by_names 함수 테스트...")
        from agents.schedule_data import _resolve_employee_ids_by_names
        resolved_ids = await _resolve_employee_ids_by_names(session, [test_name])
        print(f"  찾은 employee_ids: {resolved_ids}")


if __name__ == "__main__":
    asyncio.run(debug_resolve())

