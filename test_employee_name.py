"""employee 테이블에서 이름 검색 테스트."""

from __future__ import annotations

import asyncio
from agents.schedule_data import get_session, _resolve_employee_ids_by_names
from sqlalchemy import text


async def test_employee_search() -> None:
    """employee 테이블에서 이름 검색 테스트."""
    print("=" * 70)
    print("Employee 이름 검색 테스트")
    print("=" * 70)
    
    async with get_session() as session:
        # 1. employee 테이블 구조 확인
        print("\n[1] employee 테이블 구조 확인...")
        result = await session.execute(text("SHOW COLUMNS FROM employee"))
        columns = [row[0] for row in result.fetchall()]
        print(f"컬럼: {', '.join(columns)}")
        
        # 2. 이름 컬럼 찾기
        name_column = None
        for col in columns:
            if "name" in col.lower():
                name_column = col
                break
        
        if not name_column:
            print("[오류] 이름 컬럼을 찾을 수 없습니다!")
            return
        
        print(f"\n[2] 이름 컬럼: {name_column}")
        
        # 3. 전체 이름 샘플 조회
        print("\n[3] 이름 샘플 조회...")
        result = await session.execute(
            text(f"SELECT {name_column}, employee_id FROM employee LIMIT 10")
        )
        rows = result.fetchall()
        print(f"총 {len(rows)}개 샘플:")
        for row in rows:
            print(f"  - {row[0]} (employee_id: {row[1]})")
        
        # 4. 샘플 이름 검색 (employee_id 4001의 이름 사용)
        print("\n[4] 샘플 이름 검색...")
        # employee_id 4001의 이름 가져오기
        result = await session.execute(
            text(f"SELECT {name_column} FROM employee WHERE employee_id = 4001")
        )
        sample_name = result.scalar()
        if sample_name:
            print(f"  샘플 이름: '{sample_name}' (employee_id: 4001)")
            result = await session.execute(
                text(f"SELECT {name_column}, employee_id FROM employee WHERE {name_column} LIKE :pattern"),
                {"pattern": f"%{sample_name}%"}
            )
            matches = result.fetchall()
            if matches:
                print(f"  찾은 결과: {len(matches)}개")
                for row in matches[:5]:
                    print(f"    - {row[0]} (employee_id: {row[1]})")
        
        # 5. _resolve_employee_ids_by_names 함수 테스트
        print("\n[5] _resolve_employee_ids_by_names 함수 테스트...")
        if sample_name:
            test_names = [sample_name, sample_name[:1] if len(sample_name) > 1 else sample_name]
        else:
            test_names = ["홍", "길동"]
        for name in test_names:
            ids = await _resolve_employee_ids_by_names(session, [name])
            if ids:
                print(f"  '{name}' → employee_ids: {ids}")
            else:
                print(f"  '{name}' → 찾지 못함")


if __name__ == "__main__":
    asyncio.run(test_employee_search())

