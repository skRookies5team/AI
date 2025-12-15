"""LLM 필드 값 생성 모듈."""

import json
import re
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from ..document_utils import get_env


def generate_field_values_with_llm(
    template_structure: Dict[str, Any],
    user_requirements: Optional[str] = None,
    document_title: Optional[str] = None,
    template_name: Optional[str] = None,
    db_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """LLM을 사용하여 템플릿 필드 값을 생성합니다.
    
    Args:
        template_structure: 템플릿 구조 정보 (필드 목록 등)
        user_requirements: 사용자 요구사항
        document_title: 문서 제목
        template_name: 템플릿 이름
        db_data: DB에서 조회한 데이터
        
    Returns:
        필드 값 딕셔너리
    """
    if not LLM_AVAILABLE:
        print("⚠️ LangChain OpenAI가 설치되지 않았습니다. 빈 딕셔너리 반환")
        return {}
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("⚠️ OPENAI_API_KEY가 설정되지 않았습니다. 빈 딕셔너리 반환")
        return {}
    
    try:
        # 템플릿 필드 목록 추출 (template_structure는 {"sections": [], "inputs": []} 형식)
        fields = template_structure.get("inputs", [])
        if not fields:
            print("⚠️ 템플릿 필드가 없습니다. 빈 딕셔너리 반환")
            return {}
        
        # 필드 목록을 문자열로 변환
        field_list = "\n".join([
            f"- {field.get('id', '')}: {field.get('type', 'input')} ({field.get('label', '') or field.get('placeholder', '')})" 
            for field in fields[:50]  # 최대 50개만
        ])
        
        # DB 데이터를 문자열로 변환
        db_context = ""
        if db_data:
            db_context = f"\n\nDB 데이터:\n{json.dumps(db_data, ensure_ascii=False, indent=2)[:1000]}"  # 최대 1000자
        
        # LLM 프롬프트 구성
        system_prompt = """당신은 문서 생성 시스템의 필드 값 추출 전문가입니다.
사용자의 요구사항과 문서 제목을 분석하여 템플릿 필드에 맞는 값을 JSON 형식으로 추출하세요.

**규칙:**
1. 사용자가 명시적으로 제공한 정보를 우선적으로 사용하세요.
2. DB 데이터가 제공되면 이를 활용하세요.
3. 필드 타입에 맞는 형식으로 값을 생성하세요 (날짜, 숫자, 텍스트 등).
4. 추출할 수 없는 필드는 null로 설정하거나 생략하세요.
5. JSON 형식으로만 응답하세요 (설명 없이).

**응답 형식:**
{
  "field_id1": "value1",
  "field_id2": "value2",
  ...
}"""

        user_prompt = f"""템플릿 이름: {template_name or '알 수 없음'}
문서 제목: {document_title or '알 수 없음'}

사용자 요구사항:
{user_requirements or '요구사항 없음'}

템플릿 필드 목록:
{field_list}
{db_context}

위 정보를 바탕으로 필드 값들을 JSON 형식으로 추출하세요."""

        # LLM 호출
        llm = ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0,
            api_key=api_key
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("user", user_prompt)
        ])
        
        chain = prompt | llm
        response = chain.invoke({})
        
        # JSON 파싱
        content = response.content.strip()
        
        # JSON 코드 블록 제거 (```json ... ```)
        if content.startswith("```"):
            content = re.sub(r'^```(?:json)?\s*\n', '', content)
            content = re.sub(r'\n```\s*$', '', content)
        
        try:
            field_values = json.loads(content)
            if isinstance(field_values, dict):
                print(f"✅ LLM 필드 값 생성 성공: {len(field_values)}개 필드")
                return field_values
            else:
                print(f"⚠️ LLM 응답이 딕셔너리가 아닙니다: {type(field_values)}")
                return {}
        except json.JSONDecodeError as e:
            print(f"⚠️ JSON 파싱 실패: {e}")
            print(f"응답 내용 (처음 200자): {content[:200]}")
            return {}
            
    except Exception as e:
        print(f"⚠️ LLM 필드 값 생성 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return {}
