"""업무 성과 평가 템플릿 필드 추출 모듈."""

import re
from datetime import datetime
from typing import Any, Dict


def extract_work_performance_review_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str
) -> None:
    """업무 성과 평가 템플릿의 필드 값을 요구사항에서 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (in-place 수정)
        requirements: 사용자 요구사항
        document_title: 문서 제목
    """
    # 1. departmentName 추출
    if 'departmentName' not in field_values or not field_values.get('departmentName') or (isinstance(field_values.get('departmentName'), str) and not field_values.get('departmentName').strip()):
        dept_patterns = [
            r'부서명[:\s]+([^\n]+)',
            r'부서[:\s]+([^\n]+)',
            r'소속\s+부서[:\s]+([^\n]+)',
        ]
        for pattern in dept_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['departmentName'] = match.group(1).strip()
                print(f"✅ 요구사항에서 departmentName 추출: {field_values['departmentName']}")
                break
        if 'departmentName' not in field_values or not field_values.get('departmentName'):
            field_values['departmentName'] = '인사팀'
    
    # 2. codeNumber 추출
    if 'codeNumber' not in field_values or not field_values.get('codeNumber') or (isinstance(field_values.get('codeNumber'), str) and not field_values.get('codeNumber').strip()):
        code_patterns = [
            r'코드번호[:\s]+([^\n]+)',
            r'코드[:\s]+([^\n]+)',
        ]
        for pattern in code_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['codeNumber'] = match.group(1).strip()
                print(f"✅ 요구사항에서 codeNumber 추출: {field_values['codeNumber']}")
                break
        if 'codeNumber' not in field_values or not field_values.get('codeNumber'):
            field_values['codeNumber'] = 'HR-001'
    
    # 3. 평가 기간 추출 (year, startMonth, startDay, endMonth, endDay)
    # 중요: 이 필드들은 항상 요구사항에서 추출 (LLM 결과와 무관하게 항상 덮어쓰기)
    period_patterns = [
        r'평가\s+기간[:\s]*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*[~-]\s*(\d{1,2})월\s*(\d{1,2})일',
        r'평가\s+기간[:\s]*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*[~-]\s*(\d{1,2})월',
        r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*[~-]\s*(\d{1,2})월\s*(\d{1,2})일',
        r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*[~-]\s*(\d{1,2})월',
    ]
    
    period_matched = False
    for pattern in period_patterns:
        match = re.search(pattern, requirements, re.IGNORECASE)
        if match:
            field_values['year'] = int(match.group(1))
            field_values['startMonth'] = int(match.group(2))
            field_values['startDay'] = int(match.group(3))
            field_values['endMonth'] = int(match.group(4))
            if match.lastindex >= 5 and match.group(5):
                field_values['endDay'] = int(match.group(5))
            else:
                field_values['endDay'] = 30
            period_matched = True
            print(f"✅ 요구사항에서 평가 기간 추출 (덮어쓰기): {field_values['year']}년 {field_values['startMonth']}월 {field_values['startDay']}일 ~ {field_values['endMonth']}월 {field_values['endDay']}일")
            break
    
    if not period_matched:
        year_match = re.search(r'(\d{4})년', requirements)
        if year_match:
            field_values['year'] = int(year_match.group(1))
            print(f"✅ 요구사항에서 year 추출 (덮어쓰기): {field_values['year']}")
        else:
            field_values['year'] = datetime.now().year
            print(f"⚠️ year 추출 실패, 오늘 연도 사용: {field_values['year']}")
        
        start_date_match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', requirements)
        if start_date_match:
            field_values['startMonth'] = int(start_date_match.group(1))
            field_values['startDay'] = int(start_date_match.group(2))
            print(f"✅ 요구사항에서 startMonth/Day 추출 (덮어쓰기): {field_values['startMonth']}월 {field_values['startDay']}일")
        else:
            first_date_match = re.search(r'(\d{1,2})월\s*(\d{1,2})일', requirements)
            if first_date_match:
                field_values['startMonth'] = int(first_date_match.group(1))
                field_values['startDay'] = int(first_date_match.group(2))
            else:
                field_values['startMonth'] = datetime.now().month
                field_values['startDay'] = 1
                print(f"⚠️ startMonth/Day 추출 실패, 기본값 사용: {field_values['startMonth']}월 {field_values['startDay']}일")
        
        end_date_pattern = r'[~-]\s*(\d{1,2})월\s*(\d{1,2})일'
        end_date_match = re.search(end_date_pattern, requirements)
        if end_date_match:
            field_values['endMonth'] = int(end_date_match.group(1))
            field_values['endDay'] = int(end_date_match.group(2))
            print(f"✅ 요구사항에서 endMonth/Day 추출 (덮어쓰기): {field_values['endMonth']}월 {field_values['endDay']}일")
        else:
            end_month_pattern = r'[~-]\s*(\d{1,2})월'
            end_month_match = re.search(end_month_pattern, requirements)
            if end_month_match:
                field_values['endMonth'] = int(end_month_match.group(1))
                field_values['endDay'] = 30
                print(f"✅ 요구사항에서 endMonth 추출 (덮어쓰기): {field_values['endMonth']}월, endDay: 30일")
            else:
                field_values['endMonth'] = datetime.now().month
                field_values['endDay'] = datetime.now().day
                print(f"⚠️ endMonth/Day 추출 실패, 오늘 날짜 사용: {field_values['endMonth']}월 {field_values['endDay']}일")
    
    # 4. 업무명 추출
    if 'workName' not in field_values or not field_values.get('workName') or (isinstance(field_values.get('workName'), str) and not field_values.get('workName').strip()):
        work_patterns = [
            r'업무명[:\s]+([^\n]+)',
            r'평가\s+업무[:\s]+([^\n]+)',
        ]
        for pattern in work_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['workName'] = match.group(1).strip()
                print(f"✅ 요구사항에서 workName 추출: {field_values['workName']}")
                break
    
    # 5. 작성자명 추출
    if 'authorName' not in field_values or not field_values.get('authorName') or (isinstance(field_values.get('authorName'), str) and not field_values.get('authorName').strip()):
        author_patterns = [
            r'작성자명[:\s]+([^\n]+)',
            r'작성자[:\s]+([^\n]+)',
        ]
        for pattern in author_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['authorName'] = match.group(1).strip()
                print(f"✅ 요구사항에서 authorName 추출: {field_values['authorName']}")
                break
    
    # 6. 담당 부서장명 추출
    if 'departmentHeadName' not in field_values or not field_values.get('departmentHeadName') or (isinstance(field_values.get('departmentHeadName'), str) and not field_values.get('departmentHeadName').strip()):
        head_patterns = [
            r'담당\s+부서장명[:\s]+([^\n]+)',
            r'부서장명[:\s]+([^\n]+)',
            r'부서장[:\s]+([^\n]+)',
        ]
        for pattern in head_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['departmentHeadName'] = match.group(1).strip()
                print(f"✅ 요구사항에서 departmentHeadName 추출: {field_values['departmentHeadName']}")
                break
    
    # 7. 주평가대상 추출
    if 'evaluationTarget' not in field_values or not field_values.get('evaluationTarget') or (isinstance(field_values.get('evaluationTarget'), str) and not field_values.get('evaluationTarget').strip()):
        target_patterns = [
            r'주평가대상[:\s]+([^\n]+)',
            r'평가대상[:\s]+([^\n]+)',
        ]
        for pattern in target_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['evaluationTarget'] = match.group(1).strip()
                print(f"✅ 요구사항에서 evaluationTarget 추출: {field_values['evaluationTarget']}")
                break
    
    # 8. 벌점 및 주당근무시간 추출
    if 'penaltyAndWorkHours' not in field_values or not field_values.get('penaltyAndWorkHours') or (isinstance(field_values.get('penaltyAndWorkHours'), str) and not field_values.get('penaltyAndWorkHours').strip()):
        penalty_patterns = [
            r'벌점[:\s]*(\d+점)?[,\s]*(?:주당\s*근무시간|주당근무시간)[:\s]*(\d+)시간',
            r'벌점\s+및\s+주당근무시간[:\s]+([^\n]+)',
        ]
        for pattern in penalty_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                if match.lastindex >= 2:
                    penalty = match.group(1) if match.group(1) else "0점"
                    hours = match.group(2)
                    field_values['penaltyAndWorkHours'] = f"벌점 {penalty}, 주당 {hours}시간"
                else:
                    field_values['penaltyAndWorkHours'] = match.group(1).strip() if match.group(1) else "벌점 0점, 주당 40시간"
                print(f"✅ 요구사항에서 penaltyAndWorkHours 추출: {field_values['penaltyAndWorkHours']}")
                break
    
    # 9. 평가 방법 추출
    if 'evaluationMethod' not in field_values or not field_values.get('evaluationMethod') or (isinstance(field_values.get('evaluationMethod'), str) and not field_values.get('evaluationMethod').strip()):
        method_patterns = [
            r'평가\s+방법[:\s]+([^\n]+)',
        ]
        for pattern in method_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['evaluationMethod'] = match.group(1).strip()
                print(f"✅ 요구사항에서 evaluationMethod 추출: {field_values['evaluationMethod']}")
                break
    
    # 10. 평가 목표 및 개요 추출
    if 'evaluationGoal' not in field_values or not field_values.get('evaluationGoal') or (isinstance(field_values.get('evaluationGoal'), str) and not field_values.get('evaluationGoal').strip()):
        goal_patterns = [
            r'평가\s+목표[:\s]+([^\n]+(?:\n[^\d]+[^\n]+)*)',
            r'평가\s+개요[:\s]+([^\n]+)',
        ]
        for pattern in goal_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE | re.DOTALL)
            if match:
                field_values['evaluationGoal'] = match.group(1).strip()
                print(f"✅ 요구사항에서 evaluationGoal 추출")
                break
    
    # 11. 참고사항 추출
    if 'remarks' not in field_values or not field_values.get('remarks') or (isinstance(field_values.get('remarks'), str) and not field_values.get('remarks').strip()):
        remarks_patterns = [
            r'참고[:\s]+([^\n]+)',
            r'참고사항[:\s]+([^\n]+)',
        ]
        for pattern in remarks_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['remarks'] = match.group(1).strip()
                print(f"✅ 요구사항에서 remarks 추출")
                break
    
    # 12. 선수과목 추출
    if 'prerequisite' not in field_values or not field_values.get('prerequisite') or (isinstance(field_values.get('prerequisite'), str) and not field_values.get('prerequisite').strip()):
        prereq_patterns = [
            r'선수과목[:\s]+([^\n]+)',
        ]
        for pattern in prereq_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['prerequisite'] = match.group(1).strip()
                print(f"✅ 요구사항에서 prerequisite 추출: {field_values['prerequisite']}")
                break
    
    # 13. 후속과목 추출
    if 'followUp' not in field_values or not field_values.get('followUp') or (isinstance(field_values.get('followUp'), str) and not field_values.get('followUp').strip()):
        follow_patterns = [
            r'후속과목[:\s]+([^\n]+)',
        ]
        for pattern in follow_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['followUp'] = match.group(1).strip()
                print(f"✅ 요구사항에서 followUp 추출: {field_values['followUp']}")
                break
    
    # 14. 주별 평가 테이블 추출 (evaluationTable)
    if 'evaluationTable' not in field_values or not field_values.get('evaluationTable') or not isinstance(field_values.get('evaluationTable'), list) or len(field_values.get('evaluationTable', [])) == 0:
        eval_rows = []
        week_patterns = [
            r'(\d+)주[:\s]*(\d+(?:\.\d+)?)\s*점',
            r'(\d+)주차[:\s]*(\d+(?:\.\d+)?)\s*점',
        ]
        
        for pattern in week_patterns:
            matches = re.finditer(pattern, requirements, re.IGNORECASE)
            for match in matches:
                week_num = int(match.group(1))
                score = float(match.group(2))
                remarks_text = ""
                eval_rows.append({
                    'week': week_num,
                    'score': score,
                    'remarks': remarks_text
                })
            
            if eval_rows:
                break
        
        if not eval_rows:
            detailed_pattern = r'(\d+)주차?\s*(\d+(?:\.\d+)?)\s*점\s*(?:\(([^)]+)\))?'
            matches = re.finditer(detailed_pattern, requirements, re.IGNORECASE)
            for match in matches:
                week_num = int(match.group(1))
                score = float(match.group(2))
                remarks_text = match.group(3).strip() if match.group(3) else ""
                eval_rows.append({
                    'week': week_num,
                    'score': score,
                    'remarks': remarks_text
                })
        
        if eval_rows:
            eval_rows.sort(key=lambda x: x['week'])
            field_values['evaluationTable'] = eval_rows
            print(f"✅ 요구사항에서 evaluationTable 추출: {len(eval_rows)}개 행")


