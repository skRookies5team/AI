"""CSV 파일의 데이터로 schedule 테이블을 교체하는 스크립트."""

from __future__ import annotations

import asyncio
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

# 가상환경 체크
try:
    import asyncmy
except ImportError:
    print("=" * 70)
    print("⚠️  가상환경이 활성화되지 않았습니다!")
    print("=" * 70)
    print("\n다음 중 하나의 방법으로 실행하세요:")
    print("\n[방법 1] 가상환경 활성화 후 실행:")
    print("  .venv\\Scripts\\activate")
    print("  python import_schedule_csv.py")
    print("\n[방법 2] uv 사용:")
    print("  uv run python import_schedule_csv.py")
    print("\n" + "=" * 70)
    sys.exit(1)

from agents.schedule_data import get_session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


CSV_PATH = r"c:\Users\user\Desktop\최종 프로젝트\schedule agent data set\schedule.csv"


def parse_datetime(value: str) -> str | None:
    """날짜 문자열을 파싱하여 DB에 저장 가능한 형식으로 변환."""
    if not value or value.strip() == "":
        return None
    
    # "2025-11-01 0:00" 형식을 "2025-11-01 00:00:00" 형식으로 변환
    value = value.strip()
    
    # 날짜만 있는 경우 (시간 없음)
    if len(value) == 10 and value.count("-") == 2:
        return f"{value} 00:00:00"
    
    # 날짜와 시간이 있는 경우
    if " " in value:
        date_part, time_part = value.split(" ", 1)
        # 시간 부분 정규화 (예: "0:00" -> "00:00:00")
        time_parts = time_part.split(":")
        if len(time_parts) == 2:
            hour = time_parts[0].zfill(2)
            minute = time_parts[1].zfill(2)
            time_part = f"{hour}:{minute}:00"
        elif len(time_parts) == 3:
            hour = time_parts[0].zfill(2)
            minute = time_parts[1].zfill(2)
            second = time_parts[2].zfill(2)
            time_part = f"{hour}:{minute}:{second}"
        return f"{date_part} {time_part}"
    
    return value


async def import_schedule_csv():
    """CSV 파일의 데이터를 schedule 테이블로 가져온다."""
    print("=" * 70)
    print("CSV 데이터를 schedule 테이블로 가져오기")
    print("=" * 70)
    
    # CSV 파일 확인
    csv_file = Path(CSV_PATH)
    if not csv_file.exists():
        print(f"\n[오류] CSV 파일을 찾을 수 없습니다: {CSV_PATH}")
        return
    
    print(f"\n[1] CSV 파일 읽기: {csv_file}")
    
    # CSV 파일 읽기 (인코딩 자동 감지)
    rows_to_insert = []
    encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1"]
    file_content = None
    used_encoding = None
    
    for encoding in encodings:
        try:
            with open(csv_file, "r", encoding=encoding) as f:
                file_content = f.read()
                used_encoding = encoding
                print(f"  - 인코딩: {encoding}")
                break
        except UnicodeDecodeError:
            continue
    
    if file_content is None:
        print("\n[오류] CSV 파일을 읽을 수 없습니다. 인코딩을 확인해주세요.")
        return
    
    # CSV 파싱
    import io
    reader = csv.DictReader(io.StringIO(file_content))
    for row in reader:
            # 빈 행 건너뛰기
            if not row.get("schedule_id"):
                continue
            
            # 데이터 정규화
            schedule_id = int(row["schedule_id"]) if row.get("schedule_id") else None
            employee_id = int(row["employee_id"]) if row.get("employee_id") else None
            title = row.get("title", "").strip()
            description = row.get("description", "").strip()
            start_time = parse_datetime(row.get("start_time", ""))
            end_time = parse_datetime(row.get("end_time", ""))
            schedule_type = row.get("schedule_type", "").strip()
            location = row.get("location", "").strip()
            # alert_time은 정수(분) 또는 NULL로 처리
            alert_time_str = row.get("alert_time", "").strip()
            if alert_time_str and alert_time_str.isdigit():
                alert_time = int(alert_time_str)
            else:
                alert_time = None
            created_at = parse_datetime(row.get("created_at", ""))
            
            if schedule_id is None or employee_id is None:
                continue
            
            rows_to_insert.append({
                "schedule_id": schedule_id,
                "employee_id": employee_id,
                "title": title,
                "description": description,
                "start_time": start_time,
                "end_time": end_time,
                "schedule_type": schedule_type,
                "location": location,
                "alert_time": alert_time,
                "created_at": created_at,
            })
    
    print(f"  - 읽은 데이터: {len(rows_to_insert)}개")
    
    if not rows_to_insert:
        print("\n[오류] CSV 파일에 데이터가 없습니다.")
        return
    
    # DB 연결 및 데이터 삽입
    print("\n[2] DB 연결 및 데이터 삽입")
    
    try:
        async with get_session() as session:
            # 기존 데이터 삭제
            print("  - 기존 schedule 테이블 데이터 삭제 중...")
            await session.execute(text("DELETE FROM schedule"))
            await session.commit()
            result = await session.execute(text("SELECT COUNT(*) FROM schedule"))
            remaining_count = result.scalar()
            print(f"  - 삭제 완료 (남은 데이터: {remaining_count}개)")
            
            # 테이블 구조 확인
            result = await session.execute(text("SHOW COLUMNS FROM schedule"))
            column_info = {}
            columns = []
            for row in result.fetchall():
                col_name = row[0]
                col_type = row[1].upper()
                columns.append(col_name)
                column_info[col_name] = col_type
            print(f"  - 테이블 컬럼: {', '.join(columns)}")
            
            # alert_time 컬럼 타입 확인
            alert_time_type = column_info.get("alert_time", "").upper()
            print(f"  - alert_time 컬럼 타입: {alert_time_type}")
            
            # 데이터 삽입
            print(f"  - {len(rows_to_insert)}개 데이터 삽입 중...")
            
            inserted_count = 0
            error_count = 0
            
            # 배치로 삽입 (한 번에 너무 많이 하면 메모리 문제가 있을 수 있으므로)
            batch_size = 100
            for i in range(0, len(rows_to_insert), batch_size):
                batch = rows_to_insert[i:i + batch_size]
                
                for row_data in batch:
                    try:
                        # 컬럼 목록 동적으로 생성
                        col_names = []
                        col_values = []
                        params = {}
                        
                        for key, value in row_data.items():
                            if key not in columns:
                                continue
                            
                            # alert_time이 DATETIME 타입인 경우 NULL로 처리 (CSV의 정수 값은 분 단위이므로)
                            if key == "alert_time" and "DATETIME" in alert_time_type:
                                # DATETIME 타입인 경우 NULL로 설정
                                if value is not None:
                                    # 정수 값(분)을 datetime으로 변환할 수 없으므로 NULL 처리
                                    value = None
                            
                            col_names.append(key)
                            col_values.append(f":{key}")
                            params[key] = value
                        
                        if not col_names:
                            continue
                        
                        # ON DUPLICATE KEY UPDATE 또는 REPLACE 사용 (schedule_id가 PK인 경우)
                        # 또는 단순 INSERT
                        update_clause = ", ".join([f"{col}=VALUES({col})" for col in col_names if col != "schedule_id"])
                        if update_clause:
                            query = text(
                                f"INSERT INTO schedule ({', '.join(col_names)}) "
                                f"VALUES ({', '.join(col_values)}) "
                                f"ON DUPLICATE KEY UPDATE {update_clause}"
                            )
                        else:
                            query = text(
                                f"INSERT INTO schedule ({', '.join(col_names)}) "
                                f"VALUES ({', '.join(col_values)})"
                            )
                        
                        await session.execute(query, params)
                        inserted_count += 1
                        
                    except Exception as e:
                        error_count += 1
                        print(f"    [오류] schedule_id {row_data.get('schedule_id')} 삽입 실패: {e}")
                
                # 배치마다 커밋
                await session.commit()
                print(f"    - 진행: {min(i + batch_size, len(rows_to_insert))}/{len(rows_to_insert)}")
            
            print(f"\n[3] 완료")
            print(f"  - 삽입 성공: {inserted_count}개")
            if error_count > 0:
                print(f"  - 삽입 실패: {error_count}개")
            
            # 최종 확인
            result = await session.execute(text("SELECT COUNT(*) FROM schedule"))
            total_count = result.scalar()
            print(f"  - 테이블 총 데이터 수: {total_count}개")
            
            # 샘플 데이터 확인
            result = await session.execute(
                text("SELECT schedule_id, employee_id, title, start_time, end_time FROM schedule LIMIT 5")
            )
            print(f"\n[4] 샘플 데이터 (최대 5개):")
            for row in result.fetchall():
                print(f"  - ID: {row[0]}, 사번: {row[1]}, 제목: {row[2]}, 시작: {row[3]}, 종료: {row[4]}")
            
    except SQLAlchemyError as e:
        print(f"\n[오류] DB 오류: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n[오류] 예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(import_schedule_csv())

