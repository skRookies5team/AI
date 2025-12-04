"""LLM 필드 값 생성 모듈."""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from .utils import get_env


def generate_field_values_with_llm(
    template_structure: Dict,
    user_requirements: str,
    document_title: str,
    template_name: str,
    db_data: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """LLM을 사용하여 사용자 요구사항으로부터 필드 값을 생성합니다.
    
    Args:
        template_structure: 템플릿 구조 정보
        user_requirements: 사용자 요구사항
        document_title: 문서 제목
        template_name: 템플릿 이름
        db_data: DB 데이터 (선택사항)
    
    Returns:
        필드 ID와 값의 매핑 딕셔너리
    """
    if not LLM_AVAILABLE:
        return None
    
    api_key = get_env("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ OPENAI_API_KEY가 설정되지 않았습니다.")
        return None
    
    try:
        # 필드 목록 구성
        field_list = []
        for field in template_structure['inputs']:
            field_info = {
                "id": field['id'],
                "label": field.get('label', ''),
                "placeholder": field.get('placeholder', ''),
                "type": field.get('type', 'input')
            }
            field_list.append(field_info)
        
        # DB 데이터 섹션
        db_data_section = ""
        if db_data:
            db_data_section = f"\n\nDB 데이터:\n{json.dumps(db_data, ensure_ascii=False, indent=2)}"
        
        # 필드 ID 목록 추출
        field_ids = [field['id'] for field in template_structure['inputs']]
        
        prompt_text = _build_llm_prompt(
            document_title, user_requirements, db_data_section,
            field_list, field_ids, template_name
        )
        
        llm = ChatOpenAI(
            model=get_env("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.3,
            api_key=api_key
        )
        
        print(f"🔄 LLM 모델 호출 중... (필드 값 매핑 생성)")
        print(f"📝 요구사항 길이: {len(user_requirements)}자")
        print(f"📋 필드 목록: {len(field_list)}개")
        print(f"📄 문서 제목: {document_title}")
        
        response = llm.invoke(prompt_text)
        response_text = response.content.strip()
        
        print(f"📥 LLM 응답 수신 완료 (길이: {len(response_text)}자)")
        print(f"📄 응답 미리보기: {response_text[:300]}...")
        
        # JSON 추출 및 파싱
        field_values = _parse_llm_response(response_text)
        
        if field_values:
            non_empty_fields = {k: v for k, v in field_values.items() if v and (not isinstance(v, str) or v.strip())}
            empty_fields = {k: v for k, v in field_values.items() if not v or (isinstance(v, str) and not v.strip())}
            
            print(f"📊 필드 값 상태: {len(non_empty_fields)}개 비어있지 않음, {len(empty_fields)}개 비어있음")
            
            if len(non_empty_fields) > 0:
                print(f"✅ 필드 값 매핑 생성 완료! ({len(non_empty_fields)}개 필수 필드 값 있음)")
                print(f"📋 생성된 필드 (비어있지 않음): {', '.join(list(non_empty_fields.keys())[:10])}...")
                return field_values
            else:
                print(f"⚠️ 모든 필드가 비어있지만 빈 딕셔너리 반환 (요구사항 추출 로직에서 채움)")
                return field_values
        
        return {}
        
    except Exception as e:
        print(f"⚠️ LLM 필드 값 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return None


def _build_llm_prompt(
    document_title: str,
    user_requirements: str,
    db_data_section: str,
    field_list: list,
    field_ids: list,
    template_name: str
) -> str:
    """LLM 프롬프트 텍스트 생성."""
    return f"""사용자의 요구사항을 분석하여 문서 템플릿의 **모든 필드**에 들어갈 값을 JSON 형식으로 생성해주세요.

=== 문서 정보 ===
제목: {document_title}

=== 사용자 요구사항 (반드시 이 내용을 모든 필드에 반영하세요!) ===
{user_requirements}

**요구사항 분석 지침 (매우 중요!):**
1. 위 요구사항에서 언급된 **모든** 제품명, 기능, 일정, 예산, 마케팅 전략 등을 찾아서
2. 아래 필드 목록의 해당 필드에 **반드시** 채워주세요.
3. 요구사항에 명시된 내용은 **그대로** 사용하고, 부족한 부분만 합리적으로 추론하세요.
4. **절대로 빈 값(null, "", "없음")을 반환하지 마세요. 모든 필드에 실제 내용을 채워주세요.**
5. 요구사항의 숫자, 날짜, 제품명, 기능명 등을 정확히 추출하여 필드에 매핑하세요.

{db_data_section}

=== 필드 목록 (반드시 모두 채워주세요) ===
{json.dumps(field_list, ensure_ascii=False, indent=2)}

=== 중요한 지시사항 ===
1. **모든 필드 ID를 반드시 키로 포함해야 합니다**: {', '.join(field_ids)}
2. **사용자 요구사항을 자세히 분석하여 각 필드에 적절한 값을 생성하세요.**
   - 요구사항에 명시된 내용은 그대로 사용하세요.
   - 요구사항을 무시하고 임의로 값을 생성하지 마세요.
   - 요구사항에 있는 모든 정보를 필드에 반영하세요.
3. 필드 목록에 없는 필드는 생성하지 마세요.
4. **테이블 필드(scheduleTable, costTable)는 반드시 배열로 생성하세요.**
   - scheduleTable: 개발 일정 테이블
     * 형식: [{{"task_name": "작업명", "task_period": "기간", "remarks": "비고"}}, ...]
     * 예시: 사용자 요구사항에 "Q1: 설계 및 프로토타입, Q2: 테스트 및 양산 준비"가 있으면
       scheduleTable: [
         {{"task_name": "설계 및 프로토타입", "task_period": "2024년 1월~3월", "remarks": "Q1"}},
         {{"task_name": "테스트 및 양산 준비", "task_period": "2024년 4월~6월", "remarks": "Q2"}}
       ]
     * 사용자 요구사항에 "개발 일정: 2024년 1월~6월, Q1: ..., Q2: ..." 형식이 있으면 반드시 추출하세요!
   - costTable: 비용 테이블 (형식: [{{"task_name": "항목명", "cost": 숫자, "remarks": "비고"}}, ...])
5. 비용은 반드시 숫자로만 입력하세요 (예: 5000000000, 문자열 X).
6. 사용자 요구사항에 명시되지 않은 정보는 합리적으로 추론하여 작성하세요.
7. **빈 값이 아닌 실제 내용을 모두 채워주세요. 빈 문자열("")이나 null은 절대 사용하지 마세요.**

=== 응답 형식 ===
**반드시 순수 JSON 형식으로만 응답하세요.** 코드 블록(```json) 없이 JSON만 반환하세요.

**매우 중요: 사용자 요구사항의 모든 내용을 반드시 필드에 반영하세요!**
- 요구사항에 명시된 모든 정보를 해당 필드에 매핑하세요.
- **요구사항에 없는 내용은 절대 만들지 말고, 요구사항에 있는 내용은 반드시 활용하세요!**

모든 필드 ID를 키로 사용하고, 실제 값을 제공하세요. 빈 값(null, "") 없이 모든 필드를 채워주세요.

**중요:**
1. JSON 형식만 반환 (설명이나 다른 텍스트 없이)
2. 모든 필드 ID를 키로 포함 ({', '.join(field_ids)})
3. 모든 값은 실제 내용 (빈 문자열 X, null X)
4. 테이블은 반드시 배열로 생성 (scheduleTable, costTable)
5. 비용은 숫자로만 (문자열 X)
6. **사용자 요구사항의 모든 내용을 반드시 활용하세요!**
7. **절대로 빈 값(null, "", "없음", "N/A")을 반환하지 마세요. 모든 필드에 실제 내용을 채워주세요!**

**최종 확인 사항:**
- 모든 필드 ID가 JSON 키로 포함되어 있는가?
- 모든 값이 실제 내용인가? (빈 문자열이나 null이 아닌가?)
- scheduleTable과 costTable이 배열로 생성되었는가?
- 사용자 요구사항의 모든 정보가 필드에 반영되었는가?

위 사항을 모두 확인한 후 JSON을 반환하세요."""


def _parse_llm_response(response_text: str) -> Optional[Dict[str, Any]]:
    """LLM 응답에서 JSON 파싱."""
    # JSON 추출 (코드 블록 제거)
    json_text = re.sub(r'```json\s*', '', response_text)
    json_text = re.sub(r'```\s*', '', json_text)
    json_text = json_text.strip()
    
    print(f"🔍 JSON 추출 완료 (길이: {len(json_text)}자)")
    
    # JSON 파싱 (더 강력한 파싱)
    try:
        field_values = None
        
        # 시도 1: 직접 파싱
        try:
            field_values = json.loads(json_text)
        except json.JSONDecodeError:
            # 시도 2: 코드 블록 완전 제거
            cleaned = re.sub(r'```(?:json)?\s*', '', json_text)
            cleaned = cleaned.strip()
            try:
                field_values = json.loads(cleaned)
            except json.JSONDecodeError:
                # 시도 3: 첫 번째 중괄호부터 마지막 중괄호까지 추출
                first_brace = cleaned.find('{')
                last_brace = cleaned.rfind('}')
                if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                    json_part = cleaned[first_brace:last_brace + 1]
                    try:
                        field_values = json.loads(json_part)
                    except json.JSONDecodeError:
                        # 시도 4: 마지막 시도 - 가장 긴 JSON 객체 추출
                        json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                        matches = re.findall(json_pattern, cleaned, re.DOTALL)
                        if matches:
                            longest_match = max(matches, key=len)
                            try:
                                field_values = json.loads(longest_match)
                            except json.JSONDecodeError:
                                pass
        
        if field_values and isinstance(field_values, dict) and len(field_values) > 0:
            return field_values
        else:
            print(f"⚠️ JSON 파싱 실패 또는 빈 딕셔너리")
            return {}
            
    except (json.JSONDecodeError, Exception) as e:
        print(f"⚠️ JSON 파싱 실패: {e}")
        print(f"응답 내용 (처음 1000자): {response_text[:1000]}")
        
        # 응답 내용을 파일로 저장 (디버깅용)
        try:
            current_dir = Path(__file__).parent
            project_root = current_dir.parent.parent.parent
            debug_file = project_root / "output" / "debug_llm_response.txt"
            debug_file.parent.mkdir(parents=True, exist_ok=True)
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(f"=== LLM 응답 원문 ===\n{response_text}\n\n")
                f.write(f"=== 파싱 시도한 텍스트 ===\n{json_text}\n")
            print(f"💾 디버깅 정보 저장: {debug_file}")
        except:
            pass
        return None

