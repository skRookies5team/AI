"""휴가 신청서 템플릿 필드 추출 모듈."""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from ..db_connector import (
    fetch_employee_info,
    fetch_department_manager,
    fetch_leave_request_data
)


def extract_vacation_request_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str,
    db_data: Optional[Dict[str, Any]] = None,
    employee_id: Optional[int] = None
) -> None:
    """휴가 신청서 템플릿의 필드 값을 요구사항과 DB에서 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (in-place 수정)
        requirements: 사용자 요구사항
        document_title: 문서 제목
        db_data: DB 데이터 (선택사항, orchestrator_state에서 전달)
        employee_id: 로그인한 직원 ID (선택사항)
    """
    print(f"🔍 [vacation_request_form] 필드 값 추출 시작...")
    print(f"  - employee_id: {employee_id}")
    print(f"  - requirements: {requirements[:100] if requirements else None}...")
    
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
    
    # 2. 휴가 정보 추출 (요구사항에서)
    if 'vacationType' not in field_values or not field_values.get('vacationType'):
        # 휴가 유형 키워드 패턴
        vacation_patterns = [
            (r'휴가\s*유형[:\s]+([^\n,]+)', '직접 지정'),
            (r'유형[:\s]+([^\n,]+)', '유형 키워드'),
            (r'(연차|반차|병가|경조사|출산휴가|육아휴직)', '키워드 매칭'),
        ]
        
        vacation_types = ['연차', '반차(오전)', '반차(오후)', '병가', '경조사', '출산휴가', '육아휴직', '기타']
        
        # 패턴 매칭으로 추출 시도
        extracted_type = None
        for pattern, desc in vacation_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                extracted_text = match.group(1).strip() if len(match.groups()) > 0 else match.group(0).strip()
                print(f"🔍 [vacationType] 패턴 매칭 ({desc}): '{extracted_text}'")
                
                # 추출된 텍스트와 정확히 일치하는 타입 찾기
                for vtype in vacation_types:
                    if vtype in extracted_text or extracted_text in vtype:
                        extracted_type = vtype
                        break
                
                # 부분 일치 확인 (예: "반차" -> "반차(오전)" 또는 "반차(오후)")
                if not extracted_type:
                    if '반차' in extracted_text:
                        if '오전' in extracted_text:
                            extracted_type = '반차(오전)'
                        elif '오후' in extracted_text:
                            extracted_type = '반차(오후)'
                        else:
                            extracted_type = '반차(오전)'  # 기본값
                    elif '연차' in extracted_text:
                        extracted_type = '연차'
                    elif '병가' in extracted_text:
                        extracted_type = '병가'
                    elif '경조사' in extracted_text:
                        extracted_type = '경조사'
                
                if extracted_type:
                    break
        
        # 패턴 매칭 실패 시 기존 방식 사용
        if not extracted_type:
            for vtype in vacation_types:
                if vtype in requirements or vtype.replace('(', '').replace(')', '') in requirements:
                    extracted_type = vtype
                    break
        
        if extracted_type:
            field_values['vacationType'] = extracted_type
            print(f"✅ 요구사항에서 vacationType 추출: {extracted_type}")
        else:
            # 기본값 설정
            field_values['vacationType'] = '연차'
            print(f"⚠️ vacationType을 찾을 수 없어 기본값 '연차' 사용")
    
    # 3. 휴가 기간 추출
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
    
    # 4. leave_request 테이블에서 휴가 정보 참조
    # field_values에서 최종 날짜 가져오기
    final_start_date = field_values.get('startDate')
    final_end_date = field_values.get('endDate')
    
    if employee_id and final_start_date:
        leave_data = fetch_leave_request_data(
            employee_id=employee_id,
            start_date=final_start_date,
            end_date=final_end_date,
            leave_type=field_values.get('vacationType')
        )
        
        if leave_data and len(leave_data) > 0:
            # 가장 최근 휴가 신청 데이터 사용
            latest_leave = leave_data[0]
            print(f"✅ leave_request 테이블에서 휴가 정보 참조: request_id={latest_leave.get('request_id', 'N/A')}")
            
            # leave_request 데이터로 필드 업데이트 (없는 경우만)
            if not field_values.get('vacationType') and latest_leave.get('leave_type'):
                field_values['vacationType'] = latest_leave.get('leave_type')
            
            if not field_values.get('startDate') and latest_leave.get('start_date'):
                field_values['startDate'] = str(latest_leave.get('start_date'))
            
            if not field_values.get('endDate') and latest_leave.get('end_date'):
                field_values['endDate'] = str(latest_leave.get('end_date'))
            
            if not field_values.get('reason') and latest_leave.get('reason'):
                field_values['reason'] = latest_leave.get('reason')
    
    # 5. 휴가 일수 계산
    # field_values에서 최종 날짜와 휴가 유형 가져오기
    final_start_date = field_values.get('startDate')
    final_end_date = field_values.get('endDate')
    final_vacation_type = field_values.get('vacationType', '연차')
    
    if final_start_date and final_end_date:
        try:
            start = datetime.strptime(final_start_date, "%Y-%m-%d")
            end = datetime.strptime(final_end_date, "%Y-%m-%d")
            
            # 반차인 경우 0.5일
            if "반차" in final_vacation_type:
                vacation_days = 0.5
            else:
                # 주말 제외한 일수 계산
                vacation_days = 0
                current = start
                while current <= end:
                    weekday = current.weekday()
                    if weekday < 5:  # 월~금 (0~4)
                        vacation_days += 1
                    # 다음 날로 이동
                    current = current + timedelta(days=1)
            
            field_values['vacationDays'] = vacation_days
            print(f"✅ 휴가 일수 계산: {vacation_days}일 (유형: {final_vacation_type}, 기간: {final_start_date} ~ {final_end_date})")
        except Exception as e:
            print(f"⚠️ 휴가 일수 계산 실패: {e}")
            # 기본값 설정
            if "반차" in final_vacation_type:
                field_values['vacationDays'] = 0.5
            else:
                field_values['vacationDays'] = 1.0
    else:
        # 날짜가 없으면 기본값 설정
        if "반차" in final_vacation_type:
            field_values['vacationDays'] = 0.5
        else:
            field_values['vacationDays'] = 1.0
        print(f"⚠️ 휴가 기간 정보가 없어 기본 일수 사용: {field_values['vacationDays']}일")
    
    # 6. 휴가 사유 추출 (요구사항에서)
    if 'reason' not in field_values or not field_values.get('reason'):
        reason_patterns = [
            r'휴가\s+사유[:\s]+([^\n]+)',
            r'사유[:\s]+([^\n]+)',
            r'목적[:\s]+([^\n]+)',
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
    
    # 2차 결재자: 부서장
    dept = field_values.get('department') or (applicant_info.get('dept') if applicant_info else None)
    if dept:
        dept_manager = fetch_department_manager(dept)
        if dept_manager:
            field_values['approver2Name'] = dept_manager.get('name', '')
            field_values['approver2Position'] = dept_manager.get('position', '') or dept_manager.get('job_title', '')
            field_values['approver2Signature'] = '결제대기'  # 서명 대신 결제대기 표시
            field_values['approver2Date'] = ''
            print(f"✅ 2차 결재자(부서장) 설정: {field_values['approver2Name']} (결제대기)")
        else:
            print(f"⚠️ 부서장 정보를 찾을 수 없음: dept={dept}")
    
    # 7. 신청일 자동 설정
    today = datetime.now()
    field_values['requestDate'] = f"{today.year}-{today.month:02d}-{today.day:02d}"
    
    # 8. 신청서 번호 자동 생성
    if 'formNumber' not in field_values or not field_values.get('formNumber'):
        form_number = f"VAC-{today.year}{today.month:02d}{today.day:02d}-{employee_id or '000'}"
        field_values['formNumber'] = form_number
        print(f"✅ 신청서 번호 생성: {form_number}")
    
    # 9. 최종 필드 값 확인 (디버깅)
    print(f"📋 [vacation_request_form] 최종 field_values:")
    print(f"  - startDate: {field_values.get('startDate')}")
    print(f"  - endDate: {field_values.get('endDate')}")
    print(f"  - vacationType: {field_values.get('vacationType')}")
    print(f"  - reason: {field_values.get('reason', '')[:50]}...")
    print(f"  - employee_id: {employee_id}")
    
    print(f"✅ [vacation_request_form] 필드 값 추출 완료")

