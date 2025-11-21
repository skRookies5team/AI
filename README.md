하이브리드 방식 구조
Python 코드 (기본값)
워크플로우 로직 구현
@workflow 데코레이터로 기본 메타데이터 등록
타입 안정성과 IDE 지원
YAML 파일 (오버라이드)
30개 이상의 워크플로우 메타데이터 관리
priority, enabled, examples 등을 YAML로 관리
서버 재시작 없이 메타데이터 변경 가능
자동 등록 시스템
get_xxx_graph() 함수만 있으면 자동 등록
YAML 오버라이드 자동 적용
라우팅 프롬프트 자동 생성
주요 개선 사항
1. 워크플로우 레지스트리
registry.py: Python 기반 기본 레지스트리
registry_yaml.py: YAML 오버라이드 지원 레지스트리
하이브리드 방식: Python 기본값 + YAML 오버라이드
2. 워크플로우 메타데이터 (YAML)
30개 이상의 워크플로우 등록
14개 카테고리로 그룹화
각 워크플로우별 예시 질문 포함
3. 동적 오케스트레이터
등록된 모든 워크플로우를 자동으로 노드로 추가
라우팅 프롬프트 동적 생성
30개 이상도 동일한 방식으로 관리
사용 방법
새 워크플로우 추가
워크플로우 파일 생성:
# agents/workflows/vacation_request.pyfrom agents.workflows import workflow@workflow(    name="vacation_request_flow",    category="travel",    description="휴가 신청",    priority=5,    examples=["휴가 신청"])def get_vacation_request_graph():    # 로직 구현    return workflow.compile()
YAML에 메타데이터 추가 (선택):
# agents/workflows/workflows.yamlworkflows:  vacation_request_flow:    priority: 8  # 오버라이드    enabled: true
자동 등록: 파일만 저장하면 자동으로 등록됩니다.
런타임 메타데이터 변경
# YAML 파일 수정 후 재로드 (서버 재시작 불필요)from agents.workflows import yaml_registryyaml_registry.reload_yaml()
장점
확장성: 30개 이상 워크플로우 관리 가능
유연성: 런타임 변경 가능
안정성: Python 기본값으로 타입 안정성 유지
편의성: 코드 수정 없이 워크플로우 추가
운영 편의: 비개발자도 YAML로 메타데이터 수정 가능
30개 이상의 워크플로우를 하이브리드 방식으로 관리할 수 있습니다.
