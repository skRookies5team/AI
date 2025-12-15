"""문서 템플릿별 입력 예시문을 제공하는 모듈."""

from pathlib import Path
from typing import Dict, Optional

# YAML 파일 로드
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    yaml = None

# 템플릿 예시 캐시 (모듈 레벨에서 한 번만 로드)
_template_examples_cache: Optional[Dict[str, Dict[str, str]]] = None


def _load_template_examples() -> Dict[str, Dict[str, str]]:
    """YAML 파일에서 템플릿 예시를 로드합니다.
    
    캐시를 사용하여 첫 호출 시에만 YAML 파일을 읽고, 이후에는 캐시된 값을 반환합니다.
    
    Returns:
        템플릿 예시 딕셔너리 (예: {"product_development_plan": {"title": "...", "example": "..."}})
    
    Raises:
        ImportError: yaml 모듈이 설치되지 않은 경우
        FileNotFoundError: template_examples.yaml 파일을 찾을 수 없는 경우
        ValueError: YAML 파일 파싱 실패 시
    """
    global _template_examples_cache
    
    # 캐시가 있으면 즉시 반환
    if _template_examples_cache is not None:
        return _template_examples_cache
    
    # yaml 모듈 확인
    if not YAML_AVAILABLE:
        raise ImportError(
            "yaml 모듈이 설치되지 않았습니다. "
            "pip install pyyaml을 실행하여 설치해주세요."
        )
    
    # 여러 경로에서 YAML 파일 찾기
    yaml_path = None
    search_paths = []
    
    # 1. 현재 파일 기준 상대 경로
    current_file = Path(__file__).resolve()
    search_paths.append(current_file.parent / "template_examples.yaml")
    
    # 2. 현재 작업 디렉토리 기준
    cwd = Path.cwd()
    search_paths.append(cwd / "agents" / "master_graphs" / "document" / "generate" / "template_examples.yaml")
    
    # 3. 프로젝트 루트에서 찾기 (상위 디렉토리 탐색)
    for parent in [cwd] + list(cwd.parents):
        potential_path = parent / "agents" / "master_graphs" / "document" / "generate" / "template_examples.yaml"
        if potential_path.exists():
            search_paths.append(potential_path)
            break
    
    # 파일 찾기
    for path in search_paths:
        if path.exists():
            yaml_path = path
            print(f"✅ [Template Examples] YAML 파일 발견: {yaml_path}")
            break
    
    if yaml_path is None:
        error_msg = (
            f"template_examples.yaml 파일을 찾을 수 없습니다.\n"
            f"시도한 경로:\n"
        )
        for path in search_paths:
            error_msg += f"  - {path}\n"
        raise FileNotFoundError(error_msg)
    
    # YAML 파일 읽기
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f)
    except Exception as e:
        raise ValueError(
            f"YAML 파일 파싱 실패 ({yaml_path}): {str(e)}\n"
            "파일 형식이 올바른지 확인해주세요."
        ) from e
    
    if not yaml_data:
        raise ValueError(f"YAML 파일이 비어있습니다: {yaml_path}")
    
    # templates 섹션 추출
    templates = yaml_data.get("templates", {})
    if not templates:
        raise ValueError(
            f"YAML 파일에 'templates' 섹션이 없습니다: {yaml_path}\n"
            "template_examples.yaml 파일의 구조를 확인해주세요."
        )
    
    # 캐시에 저장
    _template_examples_cache = templates
    
    print(f"✅ [Template Examples] 템플릿 예시 로드 완료: {len(templates)}개 템플릿")
    
    return _template_examples_cache


def get_template_example(template_name: str) -> Optional[str]:
    """템플릿 이름에 해당하는 예시문을 반환합니다.
    
    Args:
        template_name: 템플릿 이름 (예: "product_development_plan")
    
    Returns:
        예시문 문자열 또는 None
    """
    try:
        templates = _load_template_examples()
        template_info = templates.get(template_name)
        if template_info:
            return template_info.get("example")
    except Exception as e:
        print(f"⚠️ [Template Examples] 예시문 로드 실패: {e}")
    
    return None


def get_template_title(template_name: str) -> Optional[str]:
    """템플릿 이름에 해당하는 한글 제목을 반환합니다.
    
    Args:
        template_name: 템플릿 이름 (예: "product_development_plan")
    
    Returns:
        한글 제목 문자열 또는 None
    """
    try:
        templates = _load_template_examples()
        template_info = templates.get(template_name)
        if template_info:
            return template_info.get("title")
    except Exception as e:
        print(f"⚠️ [Template Examples] 제목 로드 실패: {e}")
    
    return None


def get_available_templates() -> Dict[str, str]:
    """작성 가능한 모든 문서 템플릿 목록을 반환합니다.
    
    Returns:
        템플릿 딕셔너리 {template_name: title} 형식
        예: {"product_development_plan": "제품개발 계획서", ...}
    """
    try:
        templates = _load_template_examples()
        result = {}
        for template_name, template_info in templates.items():
            title = template_info.get("title", template_name)
            result[template_name] = title
        return result
    except Exception as e:
        print(f"⚠️ [Template Examples] 템플릿 목록 로드 실패: {e}")
        return {}