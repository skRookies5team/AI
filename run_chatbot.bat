@echo off
REM AI 에이전트 매니저먼트 챗봇 실행 스크립트

REM 가상환경 활성화
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
    python main_agents.py
) else (
    echo 가상환경을 찾을 수 없습니다.
    echo uv run python main_agents.py 를 사용하세요.
    pause
)


