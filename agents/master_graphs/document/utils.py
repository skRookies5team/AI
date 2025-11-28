"""문서 생성 에이전트 유틸리티 함수들."""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전 조회."""
    return os.environ.get(name, default)


def load_template(template_name: str) -> Optional[str]:
    """템플릿 파일을 읽어옵니다.
    
    Args:
        template_name: 템플릿 파일명 (확장자 제외, 예: "product_development_plan")
    
    Returns:
        템플릿 HTML 내용 또는 None
    """
    # 프로젝트 루트 디렉토리 찾기
    current_dir = Path(__file__).parent
    project_root = current_dir.parent.parent.parent
    template_path = project_root / "templates" / f"{template_name}.html"
    
    if not template_path.exists():
        print(f"⚠️ 템플릿 파일을 찾을 수 없습니다: {template_path}")
        return None
    
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"⚠️ 템플릿 파일 읽기 실패: {e}")
        return None


def extract_template_structure(html_content: str) -> Dict[str, any]:
    """HTML 템플릿에서 입력 필드 구조를 추출합니다.
    
    Args:
        html_content: HTML 템플릿 내용
    
    Returns:
        템플릿 구조 정보 (섹션, 필드 등)
    """
    structure = {
        "sections": [],
        "inputs": []
    }
    
    # 섹션 라벨 추출
    section_labels = re.findall(r'<div class="section-label">([^<]+)</div>', html_content)
    structure["sections"] = section_labels
    
    # 입력 필드 추출 (더 정확한 패턴)
    # input 필드: id와 placeholder, 그리고 앞의 label 텍스트도 추출
    input_pattern = r'(?:<label[^>]*class="[^"]*label[^"]*"[^>]*>([^<]+)</label>)?[^<]*<input[^>]*id="([^"]+)"[^>]*placeholder="([^"]*)"[^>]*>'
    textarea_pattern = r'(?:<label[^>]*class="[^"]*label[^"]*"[^>]*>([^<]+)</label>)?[^<]*<textarea[^>]*id="([^"]+)"[^>]*placeholder="([^"]*)"[^>]*>'
    
    # input 필드 추출 (라벨 포함)
    for match in re.finditer(input_pattern, html_content):
        label_text = match.group(1) if match.group(1) else ""
        field_id = match.group(2)
        placeholder = match.group(3) if match.group(3) else ""
        structure["inputs"].append({
            "id": field_id,
            "label": label_text.strip(),
            "placeholder": placeholder,
            "type": "input"
        })
    
    # textarea 필드 추출 (라벨 포함)
    for match in re.finditer(textarea_pattern, html_content):
        label_text = match.group(1) if match.group(1) else ""
        field_id = match.group(2)
        placeholder = match.group(3) if match.group(3) else ""
        structure["inputs"].append({
            "id": field_id,
            "label": label_text.strip(),
            "placeholder": placeholder,
            "type": "textarea"
        })
    
    # 추가로 input-item-label 클래스를 가진 라벨과 연결된 필드도 추출
    label_input_pattern = r'<label[^>]*class="[^"]*input-item-label[^"]*"[^>]*>([^<]+)</label>\s*<(input|textarea)[^>]*id="([^"]+)"[^>]*placeholder="([^"]*)"[^>]*>'
    for match in re.finditer(label_input_pattern, html_content):
        label_text = match.group(1).strip()
        field_type = match.group(2)
        field_id = match.group(3)
        placeholder = match.group(4) if match.group(4) else ""
        
        # 이미 추가된 필드가 아닌 경우만 추가
        if not any(f["id"] == field_id for f in structure["inputs"]):
            structure["inputs"].append({
                "id": field_id,
                "label": label_text,
                "placeholder": placeholder,
                "type": field_type
            })
    
    return structure


