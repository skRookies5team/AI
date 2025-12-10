# CLOVA Speech STT 프로젝트 Makefile

.PHONY: help install test run server clean lint setup

# 기본 타겟
help:
	@echo "CLOVA Speech STT 프로젝트 빌드 도구"
	@echo ""
	@echo "사용 가능한 명령어:"
	@echo "  setup     - 프로젝트 초기 설정 (가상환경 생성 + 패키지 설치)"
	@echo "  install   - 의존성 패키지 설치"
	@echo "  test      - 단위 테스트 실행"
	@echo "  run       - 예제 스크립트 실행"
	@echo "  server    - REST API 서버 실행"
	@echo "  sample    - 샘플 오디오 파일 생성"
	@echo "  lint      - 코드 스타일 검사"
	@echo "  clean     - 임시 파일 정리"

# 프로젝트 초기 설정
setup:
	@echo "프로젝트 초기 설정 중..."
	python -m venv .venv
	@echo "가상환경이 생성되었습니다. 다음 명령으로 활성화하세요:"
	@echo "Windows: .venv\\Scripts\\Activate.ps1"
	@echo "Unix/macOS: source .venv/bin/activate"
	@echo "활성화 후 'make install'을 실행하세요."

# 의존성 설치
install:
	pip install -r requirements.txt

# 테스트 실행
test:
	pytest -v tests/

# 빠른 테스트
test-quick:
	pytest -q tests/

# 예제 실행 (샘플 파일)
run:
	@if [ ! -f examples/sample.wav ]; then \
		echo "샘플 파일이 없습니다. 생성 중..."; \
		python examples/generate_sample.py; \
	fi
	@echo "파일 업로드 방식 테스트:"
	python examples/transcribe_file.py --file examples/sample.wav

# REST API 서버 실행
server:
	uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload

# 샘플 오디오 생성
sample:
	python examples/generate_sample.py

# 코드 스타일 검사 (설치 필요: pip install flake8)
lint:
	@echo "코드 스타일 검사 중..."
	-flake8 src/ examples/ tests/ --max-line-length=100 --ignore=E203,W503

# 정리
clean:
	@echo "임시 파일 정리 중..."
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} +
	rm -rf .pytest_cache
	rm -rf temp/
	rm -rf outputs/*.json outputs/*.txt

# 프로젝트 상태 확인
status:
	@echo "=== 프로젝트 상태 ==="
	@echo "Python 버전:"
	@python --version
	@echo ""
	@echo "가상환경 확인:"
	@which python
	@echo ""
	@echo "설치된 패키지:"
	@pip list | grep -E "(requests|fastapi|uvicorn|pytest|python-dotenv)"
	@echo ""
	@echo "환경변수 파일:"
	@if [ -f .env ]; then echo "✓ .env 파일 존재"; else echo "✗ .env 파일 없음 (.env.example 참조)"; fi
	@echo ""
	@echo "샘플 파일:"
	@if [ -f examples/sample.wav ]; then echo "✓ 샘플 오디오 존재"; else echo "✗ 샘플 오디오 없음 (make sample로 생성)"; fi