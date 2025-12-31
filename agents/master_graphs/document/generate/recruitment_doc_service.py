import os
import json
from typing import Any, Dict
from agents.master_graphs.document.generate.field_extractors.interview_evaluation import (
    extract_interview_evaluation_data,
    populate_interview_evaluation_form
)

class RecruitmentDocService:
    """채용 관련 문서 생성 전용 서비스"""
    
    @staticmethod
    async def generate_interview_evaluation(state: Dict[str, Any]) -> str:
        """면접 평가표 HTML을 즉시 생성합니다.
        
        복잡한 문서 생성 엔진을 거치지 않고, 직접 HTML 템플릿을 읽어 
        데이터를 주입하여 반환합니다.
        """
        print("🚀 [RecruitmentDocService] 면접 평가표 생성 시작")
        
        # 1. 템플릿 경로 설정
        # 현재 작업 디렉토리가 ai_module인 경우를 가정
        template_path = os.path.join("templates", "interview_evaluation_form.html")
        
        if not os.path.exists(template_path):
            # 대안 경로 확인 (상위 디렉토리 등)
            alt_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))), "templates", "interview_evaluation_form.html")
            if os.path.exists(alt_path):
                template_path = alt_path
            else:
                raise FileNotFoundError(f"템플릿 파일을 찾을 수 없습니다: {template_path}")
        
        # 2. HTML 템플릿 읽기
        with open(template_path, "r", encoding="utf-8") as f:
            html_content = f.read()
            
        # 3. 데이터 추출 (RDB 및 workflow_temp_data 활용)
        data = await extract_interview_evaluation_data(state)
        
        # 4. 데이터 주입
        final_html = populate_interview_evaluation_form(html_content, data)
        
        print(f"✅ [RecruitmentDocService] 면접 평가표 생성 완료 (데이터: {data.get('candidate_name')})")
        return final_html

