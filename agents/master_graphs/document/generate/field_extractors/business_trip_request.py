"""출장 신청서 템플릿 필드 추출 모듈."""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..db_connector import (
    fetch_employee_info,
    fetch_department_manager,
    fetch_business_trip_request_data,
    fetch_team_manager
)


def extract_business_trip_request_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str,
    db_data: Optional[Dict[str, Any]] = None,
    employee_id: Optional[int] = None,
    orchestrator_state: Optional[Dict[str, Any]] = None
) -> None:
    """출장 신청서 템플릿의 필드 값을 요구사항과 DB에서 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (in-place 수정)
        requirements: 사용자 요구사항
        document_title: 문서 제목
        db_data: DB 데이터 (선택사항, orchestrator_state에서 전달)
        employee_id: 로그인한 직원 ID (선택사항)
        orchestrator_state: 오케스트레이터 상태 (workflow_temp_data 포함)
    """
    print(f"🔍 [business_trip_request_form] 필드 값 추출 시작...")
    print(f"  - employee_id: {employee_id}")
    print(f"  - requirements: {requirements[:100] if requirements else None}...")
    
    # workflow_temp_data에서 정보 추출 (우선순위 1)
    workflow_temp_data = {}
    if orchestrator_state:
        workflow_temp_data = orchestrator_state.get("workflow_temp_data", {})
        print(f"🔍 [business_trip_request_form] workflow_temp_data 확인: {list(workflow_temp_data.keys())}")
    
    # workflow_temp_data에서 날짜 정보 추출 (우선적으로 사용)
    if workflow_temp_data.get("start_time"):
        try:
            from datetime import datetime as dt
            start_time_str = workflow_temp_data["start_time"]
            # ISO 형식 파싱 (예: "2025-12-24T09:00:00+09:00")
            if isinstance(start_time_str, str):
                start_dt = dt.fromisoformat(start_time_str.replace('Z', '+00:00'))
                start_date = start_dt.strftime("%Y-%m-%d")
                field_values['startDate'] = start_date
                print(f"✅ workflow_temp_data에서 startDate 추출: {start_date}")
        except Exception as e:
            print(f"⚠️ workflow_temp_data의 start_time 파싱 실패: {e}")
    
    if workflow_temp_data.get("end_time"):
        try:
            from datetime import datetime as dt
            end_time_str = workflow_temp_data["end_time"]
            if isinstance(end_time_str, str):
                end_dt = dt.fromisoformat(end_time_str.replace('Z', '+00:00'))
                end_date = end_dt.strftime("%Y-%m-%d")
                field_values['endDate'] = end_date
                print(f"✅ workflow_temp_data에서 endDate 추출: {end_date}")
        except Exception as e:
            print(f"⚠️ workflow_temp_data의 end_time 파싱 실패: {e}")
    
    # workflow_temp_data에서 destination 추출
    if workflow_temp_data.get("destination"):
        field_values['destination'] = workflow_temp_data["destination"]
        print(f"✅ workflow_temp_data에서 destination 추출: {workflow_temp_data['destination']}")
    
    # workflow_temp_data에서 description 추출 (reason 필드로 설정)
    if workflow_temp_data.get("description"):
        field_values['reason'] = workflow_temp_data["description"]
        print(f"✅ workflow_temp_data에서 reason(description) 추출: {workflow_temp_data['description']}")
    
    # workflow_temp_data에서 title 추출 (purpose 필드로 사용 가능)
    if workflow_temp_data.get("title") and not field_values.get('purpose'):
        field_values['purpose'] = workflow_temp_data["title"]
        print(f"✅ workflow_temp_data에서 purpose(title) 추출: {workflow_temp_data['title']}")
    
    # 1. 로그인한 사용자 정보 조회 (employee 테이블)
    applicant_info = None
    if employee_id:
        applicant_info = fetch_employee_info(employee_id)
        if applicant_info:
            print(f"✅ 신청자 정보 조회 성공: {applicant_info.get('name', 'N/A')}")
            
            # 신청자 정보 자동 채우기
            field_values['applicantName'] = applicant_info.get('name', '')
            field_values['department'] = applicant_info.get('dept', '')
            field_values['position'] = applicant_info.get('position', '') or applicant_info.get('job_title', '')
            field_values['employeeId'] = str(applicant_info.get('employee_id', ''))
            # 연락처 대신 이메일 사용
            field_values['phone'] = applicant_info.get('email', '') or applicant_info.get('phone', '') or applicant_info.get('mobile', '')
            
            print(f"✅ 신청자 정보 자동 설정 완료")
        else:
            print(f"⚠️ employee_id로 신청자 정보를 찾을 수 없음")
    
    # 신청자 정보가 없으면 요구사항에서 추출 시도
    if not applicant_info:
        applicant_fields = {
            'applicantName': ['성명', '이름', '신청자'],
            'department': ['부서', '소속 부서'],
            'position': ['직급', '직위'],
            'employeeId': ['사번', '직원번호', '사원번호'],
            'phone': ['이메일', 'email', '연락처', '전화번호', '휴대폰'],  # 이메일 우선
        }
        
        for field_id, keywords in applicant_fields.items():
            if field_id not in field_values or not field_values.get(field_id):
                for keyword in keywords:
                    pattern = rf'{keyword}[:\s]+([^\n,]+)'
                    match = re.search(pattern, requirements, re.IGNORECASE)
                    if match:
                        field_values[field_id] = match.group(1).strip()
                        print(f"✅ 요구사항에서 {field_id} 추출: {field_values[field_id]}")
                        break
    
    # 2. 출장 정보 추출 (요구사항에서)
    # 출장지 추출
    if 'destination' not in field_values or not field_values.get('destination'):
        destination_patterns = [
            r'출장지[:\s]+([^\n,\.]+)',
            r'출장\s*지[:\s]+([^\n,\.]+)',
            r'방문지[:\s]+([^\n,\.]+)',
            r'목적지[:\s]+([^\n,\.]+)',
            r'출장[:\s]+([^\n,\.]+)',  # "출장: 서울" 형식
            r'([서부대인광]울|부산|대구|인천|광주|대전|울산|제주|경기|강원|충북|충남|전북|전남|경북|경남)',  # 주요 도시명 직접 매칭
        ]
        for pattern in destination_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                extracted = match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
                # "출장 기간", "출장 목적" 같은 키워드 제외
                excluded_keywords = ['기간', '목적', '사유', '신청', '출장', '신청서']
                if extracted and extracted not in excluded_keywords and len(extracted) > 1:
                    # "출장: 서울" 같은 경우 "서울"만 추출
                    if ':' in extracted:
                        extracted = extracted.split(':')[-1].strip()
                    field_values['destination'] = extracted
                    print(f"✅ 요구사항에서 destination 추출: {field_values['destination']}")
                    break
    
    # 출장 목적 추출 (기간 정보 제외)
    if 'purpose' not in field_values or not field_values.get('purpose'):
        purpose_patterns = [
            r'출장\s*목적[:\s]+([^\n]+)',
            r'목적[:\s]+([^\n]+)',
        ]
        for pattern in purpose_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                purpose_text = match.group(1).strip()
                # 기간 정보 제거 (날짜 패턴, "출장 기간", "~" 등)
                # 날짜 패턴 제거
                purpose_text = re.sub(r'\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일', '', purpose_text)
                purpose_text = re.sub(r'\d{4}-\d{1,2}-\d{1,2}', '', purpose_text)
                purpose_text = re.sub(r'\d{1,2}\s*월\s*\d{1,2}\s*일', '', purpose_text)
                # "출장 기간", "기간:", "~" 등 제거
                purpose_text = re.sub(r'출장\s*기간[:\s]*[^\n]*', '', purpose_text, flags=re.IGNORECASE)
                purpose_text = re.sub(r'기간[:\s]*[^\n]*', '', purpose_text, flags=re.IGNORECASE)
                purpose_text = re.sub(r'~\s*\d{4}[^\n]*', '', purpose_text)
                purpose_text = re.sub(r'부터[^\n]*까지', '', purpose_text)
                purpose_text = re.sub(r'[~-]\s*', '', purpose_text)
                # 공백 정리
                purpose_text = re.sub(r'\s+', ' ', purpose_text).strip()
                # 쉼표나 마침표로 끝나는 경우 제거
                purpose_text = re.sub(r'[,，]\s*$', '', purpose_text)
                
                if purpose_text and len(purpose_text) > 1:
                    field_values['purpose'] = purpose_text
                    print(f"✅ 요구사항에서 purpose 추출 (기간 정보 제거): {field_values['purpose']}")
                    break
    
    # 3. 출장 기간 추출
    date_patterns = [
        r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일',
        r'(\d{4})-(\d{1,2})-(\d{1,2})',
        r'(\d{1,2})\s*월\s*(\d{1,2})\s*일',
    ]
    
    # field_values에서 이미 추출된 날짜 확인
    start_date = field_values.get('startDate')
    end_date = field_values.get('endDate')
    
    # 시작일 추출 (없는 경우만)
    if not start_date:
        for pattern in date_patterns:
            matches = list(re.finditer(pattern, requirements))
            if matches:
                match = matches[0]
                if len(match.groups()) == 3:
                    year, month, day = match.groups()
                    # 년도가 2자리면 20xx로 가정
                    if len(year) == 2:
                        year = f"20{year}"
                    start_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                    field_values['startDate'] = start_date
                    print(f"✅ 요구사항에서 startDate 추출: {start_date}")
                    break
        # 여전히 없으면 현재 날짜 사용
        if not start_date:
            today = datetime.now()
            start_date = f"{today.year}-{today.month:02d}-{today.day:02d}"
            field_values['startDate'] = start_date
            print(f"⚠️ startDate를 찾을 수 없어 오늘 날짜 사용: {start_date}")
    
    # 종료일 추출 (없는 경우만)
    if not end_date:
        for pattern in date_patterns:
            matches = list(re.finditer(pattern, requirements))
            if len(matches) > 1:
                match = matches[1]
                if len(match.groups()) == 3:
                    year, month, day = match.groups()
                    # 년도가 2자리면 20xx로 가정
                    if len(year) == 2:
                        year = f"20{year}"
                    end_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                    field_values['endDate'] = end_date
                    print(f"✅ 요구사항에서 endDate 추출: {end_date}")
                    break
        # 여전히 없으면 start_date와 동일하게 설정
        if not end_date:
            end_date = start_date or field_values.get('startDate')
            if end_date:
                field_values['endDate'] = end_date
                print(f"✅ endDate를 startDate와 동일하게 설정: {end_date}")
            else:
                # start_date도 없으면 오늘 날짜 사용
                today = datetime.now()
                end_date = f"{today.year}-{today.month:02d}-{today.day:02d}"
                field_values['endDate'] = end_date
                print(f"⚠️ endDate를 찾을 수 없어 오늘 날짜 사용: {end_date}")
    
    # 4. business_trip 테이블에서 출장 정보 참조
    # field_values에서 최종 날짜 가져오기
    final_start_date = field_values.get('startDate')
    final_end_date = field_values.get('endDate')
    
    if employee_id and final_start_date:
        trip_data = fetch_business_trip_request_data(
            employee_id=employee_id,
            start_date=final_start_date,
            end_date=final_end_date
        )
        
        if trip_data and len(trip_data) > 0:
            # 가장 최근 출장 신청 데이터 사용
            latest_trip = trip_data[0]
            trip_id = latest_trip.get('id', 'N/A')
            print(f"✅ business_trip 테이블에서 출장 정보 참조: id={trip_id}")
            
            # business_trip 데이터로 필드 업데이트 (없는 경우만)
            # 컬럼명: destination, trip_title, start_time, end_time, trip_purpose
            if not field_values.get('destination') and latest_trip.get('destination'):
                field_values['destination'] = latest_trip.get('destination')
            
            if not field_values.get('purpose') and latest_trip.get('trip_title'):
                field_values['purpose'] = latest_trip.get('trip_title')
            
            if not field_values.get('startDate') and latest_trip.get('start_time'):
                field_values['startDate'] = str(latest_trip.get('start_time'))
            
            if not field_values.get('endDate') and latest_trip.get('end_time'):
                field_values['endDate'] = str(latest_trip.get('end_time'))
            
            if not field_values.get('reason') and latest_trip.get('trip_purpose'):
                field_values['reason'] = latest_trip.get('trip_purpose')
    
    # 5. 출장 일수 계산
    # field_values에서 최종 날짜 가져오기
    final_start_date = field_values.get('startDate')
    final_end_date = field_values.get('endDate')
    
    if final_start_date and final_end_date:
        try:
            start = datetime.strptime(final_start_date, "%Y-%m-%d")
            end = datetime.strptime(final_end_date, "%Y-%m-%d")
            
            # 출장 일수 계산 (주말 포함)
            trip_days = 0
            current = start
            while current <= end:
                trip_days += 1
                current = current + timedelta(days=1)
            
            field_values['tripDays'] = trip_days
            print(f"✅ 출장 일수 계산: {trip_days}일 (기간: {final_start_date} ~ {final_end_date})")
        except Exception as e:
            print(f"⚠️ 출장 일수 계산 실패: {e}")
            field_values['tripDays'] = 1
    else:
        field_values['tripDays'] = 1
        print(f"⚠️ 출장 기간 정보가 없어 기본 일수 사용: {field_values['tripDays']}일")
    
    # 6. 출장 사유 추출 (요구사항에서)
    if 'reason' not in field_values or not field_values.get('reason'):
        reason_patterns = [
            r'출장\s+사유[:\s]+([^\n]+)',
            r'사유[:\s]+([^\n]+)',
        ]
        for pattern in reason_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['reason'] = match.group(1).strip()
                print(f"✅ 요구사항에서 reason 추출: {field_values['reason']}")
                break
    
    # 6. 결재선 설정
    # 1차 결재자: 신청자 본인
    if applicant_info:
        field_values['approver1Name'] = applicant_info.get('name', '')
        field_values['approver1Position'] = applicant_info.get('position', '') or applicant_info.get('job_title', '')
        field_values['approver1Signature'] = '결제대기'  # 서명 대신 결제대기 표시
        field_values['approver1Date'] = ''
        print(f"✅ 1차 결재자 설정: {field_values['approver1Name']} (결제대기)")
    
    # 2차 결재자: 현재 사원의 직급이 "팀원"이면, 같은 팀의 팀장
    if applicant_info:
        job_title = applicant_info.get('job_title', '') or applicant_info.get('position', '')
        dept = applicant_info.get('dept', '')
        
        # 직급이 "팀원"인지 확인
        if job_title and '팀원' in job_title:
            # dept에서 "팀"이 첫 등장하는 위치까지 추출
            # 예: "경영지원팀1팀" -> "경영지원팀"
            team_match = re.search(r'^([^팀]*팀)', dept)
            if team_match:
                team_name = team_match.group(1)
                print(f"🔍 팀명 추출: {dept} -> {team_name}")
                
                # 같은 팀의 팀장 조회
                team_manager = fetch_team_manager(team_name)
                if team_manager:
                    field_values['approver2Name'] = team_manager.get('name', '')
                    field_values['approver2Position'] = team_manager.get('position', '') or team_manager.get('job_title', '')
                    field_values['approver2Signature'] = '결제대기'
                    field_values['approver2Date'] = ''
                    print(f"✅ 2차 결재자(팀장) 설정: {field_values['approver2Name']} (결제대기)")
                else:
                    print(f"⚠️ 팀장 정보를 찾을 수 없음: team={team_name}")
                    # 팀장을 찾을 수 없으면 부서장으로 fallback
                    dept_manager = fetch_department_manager(dept)
                    if dept_manager:
                        field_values['approver2Name'] = dept_manager.get('name', '')
                        field_values['approver2Position'] = dept_manager.get('position', '') or dept_manager.get('job_title', '')
                        field_values['approver2Signature'] = '결제대기'
                        field_values['approver2Date'] = ''
                        print(f"✅ 2차 결재자(부서장) 설정 (팀장 없음): {field_values['approver2Name']} (결제대기)")
            else:
                print(f"⚠️ 팀명을 추출할 수 없음: dept={dept}")
                # 팀명을 추출할 수 없으면 부서장으로 fallback
                dept_manager = fetch_department_manager(dept)
                if dept_manager:
                    field_values['approver2Name'] = dept_manager.get('name', '')
                    field_values['approver2Position'] = dept_manager.get('position', '') or dept_manager.get('job_title', '')
                    field_values['approver2Signature'] = '결제대기'
                    field_values['approver2Date'] = ''
                    print(f"✅ 2차 결재자(부서장) 설정 (팀명 추출 실패): {field_values['approver2Name']} (결제대기)")
        else:
            # 직급이 "팀원"이 아니면 부서장으로 설정
            dept = field_values.get('department') or dept
            if dept:
                dept_manager = fetch_department_manager(dept)
                if dept_manager:
                    field_values['approver2Name'] = dept_manager.get('name', '')
                    field_values['approver2Position'] = dept_manager.get('position', '') or dept_manager.get('job_title', '')
                    field_values['approver2Signature'] = '결제대기'
                    field_values['approver2Date'] = ''
                    print(f"✅ 2차 결재자(부서장) 설정: {field_values['approver2Name']} (결제대기)")
                else:
                    print(f"⚠️ 부서장 정보를 찾을 수 없음: dept={dept}")
    
    # 7. 신청일 자동 설정
    today = datetime.now()
    field_values['requestDate'] = f"{today.year}-{today.month:02d}-{today.day:02d}"
    
    # 8. 신청서 번호 자동 생성
    if 'formNumber' not in field_values or not field_values.get('formNumber'):
        form_number = f"BT-{today.year}{today.month:02d}{today.day:02d}-{employee_id or '000'}"
        field_values['formNumber'] = form_number
        print(f"✅ 신청서 번호 생성: {form_number}")
    
    # 9. 최종 필드 값 확인 (디버깅)
    print(f"📋 [business_trip_request_form] 최종 field_values:")
    print(f"  - startDate: {field_values.get('startDate')}")
    print(f"  - endDate: {field_values.get('endDate')}")
    print(f"  - destination: {field_values.get('destination')}")
    print(f"  - purpose: {field_values.get('purpose')}")
    print(f"  - reason: {field_values.get('reason', '')[:50]}...")
    print(f"  - employee_id: {employee_id}")
    
    # 필수 필드 검증
    missing_fields = []
    if not field_values.get('destination'):
        missing_fields.append('destination')
    if not field_values.get('startDate'):
        missing_fields.append('startDate')
    if not field_values.get('endDate'):
        missing_fields.append('endDate')
    
    if missing_fields:
        print(f"⚠️ [business_trip_request_form] 필수 필드 누락: {missing_fields}")
    else:
        print(f"✅ [business_trip_request_form] 모든 필수 필드 추출 완료")
    
    print(f"✅ [business_trip_request_form] 필드 값 추출 완료")

