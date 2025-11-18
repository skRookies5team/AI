@echo off
REM CSV 데이터를 schedule 테이블로 가져오는 스크립트

REM 가상환경 활성화
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
    python import_schedule_csv.py
    pause
) else (
    echo 가상환경을 찾을 수 없습니다.
    echo uv run python import_schedule_csv.py 를 사용하세요.
    pause
)


