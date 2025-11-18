"""schedule 테이블의 title 컬럼에서 반차/병가 관련 데이터를 확인하는 스크립트."""

from __future__ import annotations

import os
import sys

# 가상환경 체크
try:
    from agents.schedule_data import get_session
    from agents.leave_data import _run
except ImportError:
    print("=" * 70)
    print("⚠️  가상환경이 활성화되지 않았습니다!")
    print("=" * 70)
    sys.exit(1)

from sqlalchemy import text

async def check_schedule_title():
    """schedule 테이블의 title에서 반차/병가 관련 데이터를 확인한다."""
    async with get_session() as session:
        # title에 반차/병가가 포함된 데이터 확인
        print("=" * 70)
        print("title에 반차/병가가 포함된 데이터:")
        print("=" * 70)
        
        result = await session.execute(
            text("""
                SELECT schedule.schedule_id, schedule.employee_id, schedule.schedule_type, 
                       schedule.title, employee.name AS employee_name
                FROM schedule 
                LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                WHERE schedule.title LIKE '%반차%' 
                   OR schedule.title LIKE '%병가%'
                LIMIT 50
            """)
        )
        rows = result.mappings().all()
        
        if rows:
            print(f"\n총 {len(rows)}건의 반차/병가 관련 일정 (title 기준):\n")
            for idx, row in enumerate(rows, 1):
                schedule_id = row.get("schedule_id")
                employee_id = row.get("employee_id")
                schedule_type = row.get("schedule_type")
                title = row.get("title", "제목 없음")
                employee_name = row.get("employee_name", "N/A")
                
                print(f"{idx}. [ID: {schedule_id}, 사번: {employee_id}, 이름: {employee_name}]")
                print(f"   - schedule_type: '{schedule_type}'")
                print(f"   - title: '{title}'")
                print()
        else:
            print("\ntitle에 반차/병가가 포함된 일정이 없습니다.")
        
        # 사번 4001, 4003의 모든 데이터 확인 (title 포함)
        print("\n" + "=" * 70)
        print("사번 4001, 4003의 모든 데이터 (title 포함):")
        print("=" * 70)
        
        for emp_id in [4001, 4003]:
            result = await session.execute(
                text("""
                    SELECT schedule.schedule_id, schedule.schedule_type, 
                           schedule.title, employee.name AS employee_name
                    FROM schedule 
                    LEFT JOIN employee ON schedule.employee_id = employee.employee_id
                    WHERE schedule.employee_id = :emp_id
                    LIMIT 30
                """),
                {"emp_id": emp_id}
            )
            rows = result.mappings().all()
            
            print(f"\n사번 {emp_id} (총 {len(rows)}건):")
            if rows:
                for idx, row in enumerate(rows, 1):
                    schedule_id = row.get("schedule_id")
                    schedule_type = row.get("schedule_type")
                    title = row.get("title", "제목 없음")
                    employee_name = row.get("employee_name", "N/A")
                    
                    # 반차/병가 관련 키워드가 있는지 확인
                    keywords = []
                    if "반차" in str(title):
                        keywords.append("반차")
                    if "병가" in str(title):
                        keywords.append("병가")
                    if "오전" in str(title):
                        keywords.append("오전")
                    if "오후" in str(title):
                        keywords.append("오후")
                    
                    keyword_str = f" [키워드: {', '.join(keywords)}]" if keywords else ""
                    
                    print(f"  {idx}. [ID: {schedule_id}] schedule_type: '{schedule_type}', title: '{title}'{keyword_str}")
            else:
                print("  - 일정이 없습니다.")

if __name__ == "__main__":
    try:
        _run(check_schedule_title())
    except Exception as e:
        print(f"\n오류 발생: {e}")
        import traceback
        traceback.print_exc()

