"""HTML 필드 채우기 모듈."""

import re
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


def fill_fields_with_beautifulsoup(
    template_html: str,
    field_values: Dict[str, Any],
    document_title: Optional[str] = None,
    template_name: Optional[str] = None
) -> str:
    """BeautifulSoup을 사용하여 HTML 템플릿의 필드에 값을 채웁니다.
    
    Args:
        template_html: 원본 템플릿 HTML
        field_values: 필드 ID와 값의 매핑 딕셔너리
        document_title: 문서 제목
        template_name: 템플릿 이름
    
    Returns:
        필드가 채워진 HTML
    """
    if not BS4_AVAILABLE:
        print("⚠️ BeautifulSoup이 설치되지 않아 HTML 필드 채우기를 건너뜁니다.")
        return template_html
    
    try:
        from bs4 import BeautifulSoup, NavigableString
        
        soup = BeautifulSoup(template_html, 'html.parser')
        
        # 필드 값 상태 확인
        total_fields = len([k for k in field_values.keys() if not isinstance(field_values[k], (list, dict))])
        print(f"📝 필드 값 채우기 시작: 총 {total_fields} 필드 (field_values 전체: {len(field_values)} 키)")
        
        # 디버깅: 실제 필드 값 출력 (처음 5개만)
        print(f"🔍 필드 값 샘플:")
        for idx, (field_id, value) in enumerate(list(field_values.items())[:5]):
            if not isinstance(value, (list, dict)):
                value_preview = str(value)[:50] if value else '(None/empty)'
                print(f"   - {field_id}: {value_preview}...")
        
        # 날짜 자동 설정 (createDate, orderDate 등) - 가장 먼저
        date_fields = ['createDate', 'orderDate']
        today = datetime.now()
        date_str = f"{today.year}년 {today.month}월 {today.day}일"
        for date_field_id in date_fields:
            date_field = soup.find(id=date_field_id)
            if date_field:
                date_field['value'] = date_str
                print(f"✅ {date_field_id} 자동 설정: {date_str}")
        
        # year, month 필드 채우기 (monthly_profit_analysis_report 템플릿용)
        if 'year' in field_values:
            year_field = soup.find(id='year')
            if year_field:
                year_field['value'] = str(field_values['year'])
                print(f"✅ year 필드 채움: {field_values['year']}")
        
        if 'month' in field_values:
            month_field = soup.find(id='month')
            if month_field:
                month_field['value'] = str(field_values['month'])
                print(f"✅ month 필드 채움: {field_values['month']}")
        
        # period, department 필드 채우기 (activity_based_costing 템플릿용)
        if 'period' in field_values:
            period_field = soup.find(id='period')
            if period_field:
                period_field['value'] = str(field_values['period'])
                print(f"✅ period 필드 채움: {field_values['period']}")
        
        if 'department' in field_values:
            department_field = soup.find(id='department')
            if department_field:
                department_field['value'] = str(field_values['department'])
                print(f"✅ department 필드 채움: {field_values['department']}")
        
        # 일반 input/textarea 필드 채우기
        filled_fields_count = 0
        skipped_fields = []
        
        # 템플릿에서 실제 존재하는 모든 필드 ID 추출 (검증용)
        all_field_ids = set()
        for element in soup.find_all(['input', 'textarea']):
            field_id = element.get('id')
            if field_id:
                all_field_ids.add(field_id)
        
        print(f"🔍 템플릿에 존재하는 필드: {len(all_field_ids)}개")
        
        # 일반 필드 채우기
        filled_fields_count = _fill_general_fields(
            soup, field_values, document_title, skipped_fields
        )
        
        # 기본값으로 필수 필드 채우기
        filled_fields_count += _fill_default_values(
            soup, document_title, filled_fields_count
        )
        
        print(f"📊 필드 채우기 완료: {filled_fields_count}개 성공, {len(skipped_fields)}개 건너뜀")
        if skipped_fields:
            print(f"⚠️ 건너뛴 필드: {', '.join(skipped_fields[:10])}...")
        
        # 테이블 데이터 채우기
        _fill_table_data(soup, field_values)
        
        # 템플릿별 특수 처리
        if template_name == "work_performance_review":
            _handle_work_performance_review_special(
                soup, field_values, template_name
            )
        elif template_name == "product_comparison_table":
            _handle_product_comparison_special(
                soup, field_values
            )
        elif template_name == "hr_summary_report":
            _handle_hr_summary_report_special(
                soup, field_values, template_name
            )
        
        # 제목 업데이트
        _update_title_tags(soup, field_values, document_title)
        
        return str(soup)
    except Exception as e:
        print(f"⚠️ BeautifulSoup 필드 채우기 실패: {e}")
        import traceback
        traceback.print_exc()
        return template_html


def _fill_general_fields(
    soup: BeautifulSoup,
    field_values: Dict[str, Any],
    document_title: Optional[str],
    skipped_fields: list
) -> int:
    """일반 input/textarea 필드 채우기."""
    filled_count = 0
    
    for field_id, value in field_values.items():
        # 테이블 데이터와 document_title은 별도 처리
        if field_id in ['scheduleTable', 'costTable', 'itemsTable', 'evaluationTable', 'comparisonTable', 
                        'regionalTable', 'productTable', 'promotionTable', 'channelTable', 'conclusionTable',
                        'costingTable', 'employeeTable', 'document_title'] or isinstance(value, (list, dict)):
            continue
        
        # 숫자 필드는 0도 유효한 값이므로 별도 처리
        is_number_field = field_id in ['year', 'startMonth', 'startDay', 'endMonth', 'endDay']
        
        # None이나 빈 문자열 체크
        if is_number_field:
            if value is None:
                continue
        elif not value or (isinstance(value, str) and value.strip() == ''):
            essential_fields = {
                'itemName': document_title.split()[0] if document_title else '제품',
                'author': 'AI 생성',
                'department': '연구개발부서',
                'responsiblePerson': '담당자',
                'purpose': '제품 사용 목적',
                'specifications': '제품 사양',
                'developmentPurpose': '신제품 개발을 통한 시장 경쟁력 강화',
                'developmentPeriod': '개발 기간',
                'developmentContent': '개발 내용',
                'expectedEffects': '기대효과',
                'specialNotes': '특기사항',
            }
            
            if field_id in essential_fields:
                value = essential_fields[field_id]
                print(f"⚠️ {field_id}가 비어있어 기본값 사용: {value}")
            else:
                skipped_fields.append(field_id)
                print(f"⚠️ {field_id} 건너뜀 (값 없음)")
                continue
        
        element = soup.find(id=field_id)
        if element:
            try:
                if element.name == 'input':
                    if element.get('type') == 'number':
                        if value is not None:
                            try:
                                if isinstance(value, (int, float)):
                                    element['value'] = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
                                else:
                                    num_value = int(float(str(value)))
                                    element['value'] = str(num_value)
                            except (ValueError, TypeError):
                                element['value'] = ''
                        else:
                            element['value'] = ''
                    else:
                        element['value'] = str(value) if value is not None else ''
                    
                    if 'placeholder' in element.attrs:
                        del element.attrs['placeholder']
                    filled_count += 1
                    value_str = str(value)[:50] if value is not None else ""
                    print(f"✅ [{filled_count}] {field_id} = '{value_str}...'")
                    
                elif element.name == 'textarea':
                    element.clear()
                    text_content = str(value) if value else ''
                    if text_content:
                        from bs4 import NavigableString
                        element.append(NavigableString(text_content))
                    if 'placeholder' in element.attrs:
                        del element.attrs['placeholder']
                    filled_count += 1
                    print(f"✅ [{filled_count}] {field_id} = '{str(value)[:50]}...'")
                
                elif element.name == 'select':
                    # select 요소 처리
                    value_str = str(value).strip() if value else ''
                    if value_str:
                        # option 요소 중에서 value가 일치하는 것을 찾아서 selected 속성 설정
                        options = element.find_all('option')
                        found = False
                        for option in options:
                            option_value = option.get('value', '').strip()
                            option_text = option.string.strip() if option.string else ''
                            
                            # value 또는 text가 일치하면 선택
                            if option_value == value_str or option_text == value_str:
                                option['selected'] = 'selected'
                                found = True
                                break
                        
                        if found:
                            filled_count += 1
                            print(f"✅ [{filled_count}] {field_id} (select) = '{value_str}'")
                        else:
                            print(f"⚠️ {field_id} (select): 값 '{value_str}'에 해당하는 option을 찾을 수 없음")
                            skipped_fields.append(field_id)
                    else:
                        print(f"⚠️ {field_id} (select): 값이 비어있어 건너뜀")
                        skipped_fields.append(field_id)
                
                elif element.name == 'div' and field_id == 'vacationDays':
                    # 휴가 일수 div 요소 처리
                    if value is not None:
                        # 숫자 값에 따라 표시 형식 결정
                        if isinstance(value, (int, float)):
                            if value == 0.5:
                                element.string = '0.5일'
                            else:
                                element.string = f'{int(value)}일'
                            filled_count += 1
                            print(f"✅ [{filled_count}] {field_id} (div) = '{element.string}'")
                        else:
                            # 문자열인 경우 그대로 사용
                            element.string = str(value)
                            filled_count += 1
                            print(f"✅ [{filled_count}] {field_id} (div) = '{element.string}'")
                    else:
                        skipped_fields.append(field_id)
                    
            except Exception as e:
                print(f"⚠️ 필드 채우기 실패: {field_id} - {e}")
                skipped_fields.append(field_id)
        else:
            print(f"⚠️ 필드를 찾을 수 없음: {field_id} (HTML에 존재하지 않음)")
            skipped_fields.append(field_id)
    
    return filled_count


def _fill_default_values(
    soup: BeautifulSoup,
    document_title: Optional[str],
    current_count: int
) -> int:
    """비어있는 필수 필드에 기본값으로 채우기."""
    title_keywords = {}
    if document_title:
        if '스마트워치' in document_title:
            title_keywords['itemName'] = '스마트워치'
        elif '제품' in document_title or '신제품' in document_title:
            first_word = document_title.split()[0] if document_title.split() else '제품'
            title_keywords['itemName'] = first_word
    
    essential_field_defaults = {
        'itemName': title_keywords.get('itemName', document_title.split()[0] if document_title else '제품'),
        'author': 'AI 생성',
        'department': '연구개발부서',
        'responsiblePerson': '담당자',
        'purpose': '제품 사용 목적',
        'specifications': '제품 사양',
        'developmentPurpose': '신제품 개발을 통한 시장 경쟁력 강화 및 매출 증대',
        'developmentPeriod': '개발 기간',
        'developmentContent': '개발 내용',
        'expectedEffects': '기대효과',
        'specialNotes': '특기사항',
    }
    
    filled_count = 0
    for field_id, default_value in essential_field_defaults.items():
        element = soup.find(id=field_id)
        if element:
            if element.name == 'input' and element.get('value', '').strip():
                continue
            elif element.name == 'textarea' and element.get_text(strip=True):
                continue
            
            try:
                if element.name == 'input':
                    element['value'] = default_value
                    if 'placeholder' in element.attrs:
                        del element.attrs['placeholder']
                    filled_count += 1
                    print(f"✅ [{current_count + filled_count}] {field_id} = 기본값 '{default_value}'")
                elif element.name == 'textarea':
                    element.clear()
                    from bs4 import NavigableString
                    element.append(NavigableString(default_value))
                    if 'placeholder' in element.attrs:
                        del element.attrs['placeholder']
                    filled_count += 1
                    print(f"✅ [{current_count + filled_count}] {field_id} = 기본값 '{default_value}'")
            except Exception as e:
                print(f"⚠️ 기본값 설정 실패: {field_id} - {e}")
    
    return filled_count


def _fill_table_data(soup: BeautifulSoup, field_values: Dict[str, Any]) -> None:
    """테이블 데이터 채우기 (scheduleTable, costTable, itemsTable, evaluationTable)."""
    # scheduleTable
    if 'scheduleTable' in field_values and isinstance(field_values['scheduleTable'], list):
        schedule_body = soup.find('tbody', id='scheduleBody')
        if schedule_body:
            schedule_body.clear()
            for row_data in field_values['scheduleTable']:
                tr = soup.new_tag('tr')
                
                td1 = soup.new_tag('td')
                input1 = soup.new_tag('input', type='text', class_='task-name')
                input1['value'] = str(row_data.get('task_name', ''))
                td1.append(input1)
                tr.append(td1)
                
                td2 = soup.new_tag('td')
                input2 = soup.new_tag('input', type='text', class_='task-period')
                input2['value'] = str(row_data.get('task_period', ''))
                td2.append(input2)
                tr.append(td2)
                
                td3 = soup.new_tag('td')
                input3 = soup.new_tag('input', type='text', class_='task-remarks')
                input3['value'] = str(row_data.get('remarks', ''))
                td3.append(input3)
                tr.append(td3)
                
                schedule_body.append(tr)
            print(f"✅ scheduleTable: {len(field_values['scheduleTable'])}개 행 생성")
    
    # costTable
    if 'costTable' in field_values and isinstance(field_values['costTable'], list):
        cost_body = soup.find('tbody', id='costBody')
        if cost_body:
            cost_body.clear()
            for row_data in field_values['costTable']:
                tr = soup.new_tag('tr')
                
                td1 = soup.new_tag('td')
                input1 = soup.new_tag('input', type='text', class_='cost-task-name')
                input1['value'] = str(row_data.get('task_name', ''))
                td1.append(input1)
                tr.append(td1)
                
                td2 = soup.new_tag('td')
                input2 = soup.new_tag('input', type='number', class_='cost-amount', min='0', step='1000')
                cost_value = row_data.get('cost', 0)
                if isinstance(cost_value, str):
                    num_match = re.search(r'(\d+(?:\.\d+)?)', str(cost_value))
                    if num_match:
                        num = float(num_match.group(1))
                        if '억' in str(cost_value) or '억원' in str(cost_value):
                            cost_value = int(num * 100000000)
                        elif '만' in str(cost_value) or '만원' in str(cost_value):
                            cost_value = int(num * 10000)
                        elif '천' in str(cost_value) or '천원' in str(cost_value):
                            cost_value = int(num * 1000)
                        else:
                            cost_value = int(num)
                    else:
                        cost_value = 0
                input2['value'] = str(int(cost_value))
                td2.append(input2)
                tr.append(td2)
                
                td3 = soup.new_tag('td')
                input3 = soup.new_tag('input', type='text', class_='cost-remarks')
                input3['value'] = str(row_data.get('remarks', ''))
                td3.append(input3)
                tr.append(td3)
                
                cost_body.append(tr)
            print(f"✅ costTable: {len(field_values['costTable'])}개 행 생성")
    
    # itemsTable (purchase_order 템플릿용)
    if 'itemsTable' in field_values and isinstance(field_values['itemsTable'], list):
        items_body = soup.find('tbody', id='itemsBody')
        if items_body:
            items_body.clear()
            for idx, row_data in enumerate(field_values['itemsTable'], 1):
                tr = soup.new_tag('tr')
                
                # NO 컬럼 (1, 2, 3, ...)
                td_no = soup.new_tag('td', class_='no-cell')
                td_no.string = str(idx)
                tr.append(td_no)
                
                # 품명
                td1 = soup.new_tag('td')
                input1 = soup.new_tag('input', type='text', class_='item-name')
                input1['value'] = str(row_data.get('item_name', ''))
                td1.append(input1)
                tr.append(td1)
                
                # 규격
                td2 = soup.new_tag('td')
                input2 = soup.new_tag('input', type='text', class_='specification')
                input2['value'] = str(row_data.get('specification', ''))
                td2.append(input2)
                tr.append(td2)
                
                # 수량
                td3 = soup.new_tag('td')
                input3 = soup.new_tag('input', type='number', class_='quantity', min='0', step='1')
                input3['value'] = str(row_data.get('quantity', 0))
                td3.append(input3)
                tr.append(td3)
                
                # 단가
                td4 = soup.new_tag('td')
                input4 = soup.new_tag('input', type='number', class_='unit-price', min='0', step='100')
                unit_price = row_data.get('unit_price', 0)
                input4['value'] = str(int(unit_price))
                td4.append(input4)
                tr.append(td4)
                
                # 공급가액 (계산된 금액, 스팬으로 표시)
                td5 = soup.new_tag('td')
                quantity = row_data.get('quantity', 0)
                amount = int(quantity) * int(unit_price)
                span = soup.new_tag('span', class_='amount-display item-amount')
                span.string = f"₩{amount:,}"
                td5.append(span)
                tr.append(td5)
                
                items_body.append(tr)
            print(f"✅ itemsTable: {len(field_values['itemsTable'])}개 행 생성")
    
    # evaluationTable
    if 'evaluationTable' in field_values and isinstance(field_values['evaluationTable'], list):
        table_body = soup.find('tbody', id='tableBody')
        if table_body:
            table_body.clear()
            for idx, row_data in enumerate(field_values['evaluationTable'], 1):
                tr = soup.new_tag('tr')
                
                td1 = soup.new_tag('td')
                week_num = row_data.get('week', idx)
                td1.string = f"{week_num}주"
                tr.append(td1)
                
                td2 = soup.new_tag('td')
                input2 = soup.new_tag('input', type='number', min='0', max='100', step='0.1')
                input2['class'] = 'score-input'  # class_ 대신 ['class'] 사용
                input2['value'] = str(row_data.get('score', 0))
                td2.append(input2)
                tr.append(td2)
                
                td3 = soup.new_tag('td')
                textarea3 = soup.new_tag('textarea')
                textarea3['class'] = 'remarks-input'  # class_ 대신 ['class'] 사용
                remarks_text = str(row_data.get('remarks', ''))
                if remarks_text:
                    from bs4 import NavigableString
                    textarea3.append(NavigableString(remarks_text))
                td3.append(textarea3)
                tr.append(td3)
                
                table_body.append(tr)
            print(f"✅ evaluationTable: {len(field_values['evaluationTable'])}개 행 생성")
    
    # comparisonTable (product_comparison_table 템플릿용)
    if 'comparisonTable' in field_values and isinstance(field_values['comparisonTable'], list):
        comparison_body = soup.find('tbody', id='comparisonBody')
        if comparison_body:
            print(f"🔍 comparisonTable 데이터 발견: {len(field_values['comparisonTable'])}개 행")
            comparison_body.clear()  # 기존 내용 제거 (주석 포함)
            
            for row_idx, row_data in enumerate(field_values['comparisonTable'], 1):
                tr = soup.new_tag('tr')
                
                # 항목명 (첫 번째 컬럼)
                td_item = soup.new_tag('td')
                item_name = str(row_data.get('item_name', ''))
                td_item.string = item_name
                tr.append(td_item)
                
                # 제품 1 정보
                td1 = soup.new_tag('td')
                input1 = soup.new_tag('input', type='text')
                input1['class'] = 'product-input'  # class_ 대신 class 사용
                product1_value = str(row_data.get('product1_value', '')).strip()
                if product1_value:
                    input1['value'] = product1_value
                    print(f"    ✅ 제품1 {item_name}: {product1_value}")
                else:
                    # 값이 없으면 placeholder 설정
                    input1['placeholder'] = f"제품 1의 {item_name} 정보"
                    print(f"    ⚠️ 제품1 {item_name}: 값 없음 (placeholder 설정)")
                td1.append(input1)
                tr.append(td1)
                
                # 제품 2 정보
                td2 = soup.new_tag('td')
                input2 = soup.new_tag('input', type='text')
                input2['class'] = 'product-input'  # class_ 대신 class 사용
                product2_value = str(row_data.get('product2_value', '')).strip()
                if product2_value:
                    input2['value'] = product2_value
                    print(f"    ✅ 제품2 {item_name}: {product2_value}")
                else:
                    # 값이 없으면 placeholder 설정
                    input2['placeholder'] = f"제품 2의 {item_name} 정보"
                    print(f"    ⚠️ 제품2 {item_name}: 값 없음 (placeholder 설정)")
                td2.append(input2)
                tr.append(td2)
                
                # 제품 3 정보
                td3 = soup.new_tag('td')
                input3 = soup.new_tag('input', type='text')
                input3['class'] = 'product-input'  # class_ 대신 class 사용
                product3_value = str(row_data.get('product3_value', '')).strip()
                if product3_value:
                    input3['value'] = product3_value
                    print(f"    ✅ 제품3 {item_name}: {product3_value}")
                else:
                    # 값이 없으면 placeholder 설정
                    input3['placeholder'] = f"제품 3의 {item_name} 정보"
                    print(f"    ⚠️ 제품3 {item_name}: 값 없음 (placeholder 설정)")
                td3.append(input3)
                tr.append(td3)
                
                comparison_body.append(tr)
                print(f"  → 행 {row_idx}: {item_name} | 제품1: {product1_value[:30] if product1_value else '(빈값)'} | 제품2: {product2_value[:30] if product2_value else '(빈값)'} | 제품3: {product3_value[:30] if product3_value else '(빈값)'}")
            
            print(f"✅ comparisonTable: {len(field_values['comparisonTable'])}개 행 생성 완료")
        else:
            print(f"⚠️ comparisonBody tbody를 찾을 수 없음")
    else:
        if 'comparisonTable' not in field_values:
            print(f"⚠️ comparisonTable 데이터가 field_values에 없음")
        elif not isinstance(field_values.get('comparisonTable'), list):
            print(f"⚠️ comparisonTable이 리스트가 아님: {type(field_values.get('comparisonTable'))}")
    
    # regionalTable (monthly_profit_analysis_report 템플릿용)
    if 'regionalTable' in field_values and isinstance(field_values['regionalTable'], list):
        regional_body = soup.find('tbody', id='regionalBody')
        if regional_body:
            print(f"🔍 regionalTable 데이터 발견: {len(field_values['regionalTable'])}개 행")
            regional_body.clear()
            
            for row_data in field_values['regionalTable']:
                tr = soup.new_tag('tr')
                
                # NO
                td_no = soup.new_tag('td', class_='no-cell')
                td_no.string = str(row_data.get('no', ''))
                tr.append(td_no)
                
                # 지역
                td_region = soup.new_tag('td')
                input_region = soup.new_tag('input', type='text')
                input_region['value'] = str(row_data.get('region', ''))
                td_region.append(input_region)
                tr.append(td_region)
                
                # 국가
                td_country = soup.new_tag('td')
                input_country = soup.new_tag('input', type='text')
                input_country['value'] = str(row_data.get('country', ''))
                td_country.append(input_country)
                tr.append(td_country)
                
                # 총매출
                td_sales = soup.new_tag('td')
                input_sales = soup.new_tag('input', type='number', class_='sales-input')
                input_sales['value'] = str(row_data.get('total_sales', 0))
                td_sales.append(input_sales)
                tr.append(td_sales)
                
                # 총수익
                td_profit = soup.new_tag('td')
                input_profit = soup.new_tag('input', type='number', class_='profit-input')
                input_profit['value'] = str(row_data.get('total_profit', 0))
                td_profit.append(input_profit)
                tr.append(td_profit)
                
                # 수익률
                td_margin = soup.new_tag('td')
                span_margin = soup.new_tag('span', class_='percentage margin-display')
                span_margin.string = f"{row_data.get('profit_margin', 0)}%"
                td_margin.append(span_margin)
                tr.append(td_margin)
                
                regional_body.append(tr)
            print(f"✅ regionalTable: {len(field_values['regionalTable'])}개 행 생성 완료")
    
    # productTable (monthly_profit_analysis_report 템플릿용)
    if 'productTable' in field_values and isinstance(field_values['productTable'], list):
        product_body = soup.find('tbody', id='productBody')
        if product_body:
            print(f"🔍 productTable 데이터 발견: {len(field_values['productTable'])}개 행")
            product_body.clear()
            
            for row_data in field_values['productTable']:
                tr = soup.new_tag('tr')
                
                # NO
                td_no = soup.new_tag('td', class_='no-cell')
                td_no.string = str(row_data.get('no', ''))
                tr.append(td_no)
                
                # 제품명
                td_name = soup.new_tag('td')
                input_name = soup.new_tag('input', type='text')
                input_name['value'] = str(row_data.get('product_name', ''))
                td_name.append(input_name)
                tr.append(td_name)
                
                # 제품 카테고리
                td_category = soup.new_tag('td')
                input_category = soup.new_tag('input', type='text')
                input_category['value'] = str(row_data.get('category', ''))
                td_category.append(input_category)
                tr.append(td_category)
                
                # 제조업체
                td_manufacturer = soup.new_tag('td')
                input_manufacturer = soup.new_tag('input', type='text')
                input_manufacturer['value'] = str(row_data.get('manufacturer', ''))
                td_manufacturer.append(input_manufacturer)
                tr.append(td_manufacturer)
                
                # 매출액
                td_sales = soup.new_tag('td')
                input_sales = soup.new_tag('input', type='number', class_='sales-input')
                input_sales['value'] = str(row_data.get('sales_amount', 0))
                td_sales.append(input_sales)
                tr.append(td_sales)
                
                # 원가
                td_cost = soup.new_tag('td')
                input_cost = soup.new_tag('input', type='number', class_='cost-input')
                input_cost['value'] = str(row_data.get('cost_amount', 0))
                td_cost.append(input_cost)
                tr.append(td_cost)
                
                # 수익
                td_profit = soup.new_tag('td')
                span_profit = soup.new_tag('span', class_='currency profit-display')
                profit_amount = row_data.get('profit_amount', 0)
                span_profit.string = f"₩{int(profit_amount):,}"
                td_profit.append(span_profit)
                tr.append(td_profit)
                
                # 수익률
                td_margin = soup.new_tag('td')
                span_margin = soup.new_tag('span', class_='percentage margin-display')
                span_margin.string = f"{row_data.get('profit_margin', 0)}%"
                td_margin.append(span_margin)
                tr.append(td_margin)
                
                product_body.append(tr)
            print(f"✅ productTable: {len(field_values['productTable'])}개 행 생성 완료")
    
    # promotionTable (monthly_profit_analysis_report 템플릿용)
    if 'promotionTable' in field_values and isinstance(field_values['promotionTable'], list):
        promotion_body = soup.find('tbody', id='promotionBody')
        if promotion_body:
            print(f"🔍 promotionTable 데이터 발견: {len(field_values['promotionTable'])}개 행")
            promotion_body.clear()
            
            for row_data in field_values['promotionTable']:
                tr = soup.new_tag('tr')
                
                # NO
                td_no = soup.new_tag('td', class_='no-cell')
                td_no.string = str(row_data.get('no', ''))
                tr.append(td_no)
                
                # 프로모션명
                td_name = soup.new_tag('td')
                input_name = soup.new_tag('input', type='text')
                input_name['value'] = str(row_data.get('promotion_name', ''))
                td_name.append(input_name)
                tr.append(td_name)
                
                # 참여제품
                td_products = soup.new_tag('td')
                input_products = soup.new_tag('input', type='text')
                input_products['value'] = str(row_data.get('products', ''))
                td_products.append(input_products)
                tr.append(td_products)
                
                # 총비용
                td_cost = soup.new_tag('td')
                input_cost = soup.new_tag('input', type='number', class_='cost-input')
                input_cost['value'] = str(row_data.get('total_cost', 0))
                td_cost.append(input_cost)
                tr.append(td_cost)
                
                # 추가매출
                td_sales = soup.new_tag('td')
                input_sales = soup.new_tag('input', type='number', class_='sales-input')
                input_sales['value'] = str(row_data.get('additional_sales', 0))
                td_sales.append(input_sales)
                tr.append(td_sales)
                
                # ROI
                td_roi = soup.new_tag('td')
                span_roi = soup.new_tag('span', class_='percentage roi-display')
                span_roi.string = f"{row_data.get('roi', 0)}%"
                td_roi.append(span_roi)
                tr.append(td_roi)
                
                # 비고
                td_remarks = soup.new_tag('td')
                textarea_remarks = soup.new_tag('textarea')
                textarea_remarks.string = str(row_data.get('remarks', ''))
                td_remarks.append(textarea_remarks)
                tr.append(td_remarks)
                
                promotion_body.append(tr)
            print(f"✅ promotionTable: {len(field_values['promotionTable'])}개 행 생성 완료")
    
    # channelTable (monthly_profit_analysis_report 템플릿용)
    if 'channelTable' in field_values and isinstance(field_values['channelTable'], list):
        channel_body = soup.find('tbody', id='channelBody')
        if channel_body:
            print(f"🔍 channelTable 데이터 발견: {len(field_values['channelTable'])}개 행")
            channel_body.clear()
            
            for row_data in field_values['channelTable']:
                tr = soup.new_tag('tr')
                
                # 채널
                td_channel = soup.new_tag('td')
                input_channel = soup.new_tag('input', type='text')
                input_channel['value'] = str(row_data.get('channel', ''))
                td_channel.append(input_channel)
                tr.append(td_channel)
                
                # 총매출
                td_sales = soup.new_tag('td')
                input_sales = soup.new_tag('input', type='number', class_='sales-input')
                input_sales['value'] = str(row_data.get('total_sales', 0))
                td_sales.append(input_sales)
                tr.append(td_sales)
                
                # 총수익
                td_profit = soup.new_tag('td')
                input_profit = soup.new_tag('input', type='number', class_='profit-input')
                input_profit['value'] = str(row_data.get('total_profit', 0))
                td_profit.append(input_profit)
                tr.append(td_profit)
                
                # 비중
                td_proportion = soup.new_tag('td')
                span_proportion = soup.new_tag('span', class_='percentage proportion-display')
                span_proportion.string = f"{row_data.get('proportion', 0)}%"
                td_proportion.append(span_proportion)
                tr.append(td_proportion)
                
                # 증감률
                td_change = soup.new_tag('td')
                input_change = soup.new_tag('input', type='number', class_='change-input')
                input_change['value'] = str(row_data.get('change_rate', 0))
                td_change.append(input_change)
                tr.append(td_change)
                
                channel_body.append(tr)
            print(f"✅ channelTable: {len(field_values['channelTable'])}개 행 생성 완료")
    
    # conclusionTable (monthly_profit_analysis_report 템플릿용)
    if 'conclusionTable' in field_values and isinstance(field_values['conclusionTable'], list):
        conclusion_body = soup.find('tbody', id='conclusionBody')
        if conclusion_body:
            print(f"🔍 conclusionTable 데이터 발견: {len(field_values['conclusionTable'])}개 행")
            conclusion_body.clear()
            
            for row_data in field_values['conclusionTable']:
                tr = soup.new_tag('tr')
                
                # 구분 (category)
                td_category = soup.new_tag('td')
                input_category = soup.new_tag('input', type='text')
                category_value = str(row_data.get('category', ''))
                input_category['value'] = category_value
                td_category.append(input_category)
                tr.append(td_category)
                
                # 내용 (content)
                td_content = soup.new_tag('td')
                textarea_content = soup.new_tag('textarea')
                textarea_content['class'] = 'auto-resize'  # 자동 높이 조정 클래스 추가
                content_value = str(row_data.get('content', ''))
                if content_value:
                    from bs4 import NavigableString
                    textarea_content.append(NavigableString(content_value))
                td_content.append(textarea_content)
                tr.append(td_content)
                
                conclusion_body.append(tr)
            print(f"✅ conclusionTable: {len(field_values['conclusionTable'])}개 행 생성 완료")
    
    # costingTable (activity_based_costing 템플릿용)
    if 'costingTable' in field_values and isinstance(field_values['costingTable'], list):
        costing_body = soup.find('tbody', id='costingBody')
        if costing_body:
            print(f"🔍 costingTable 데이터 발견: {len(field_values['costingTable'])}개 행")
            costing_body.clear()
            
            for row_data in field_values['costingTable']:
                tr = soup.new_tag('tr')
                
                # 활동명 (activity_name)
                td_activity_name = soup.new_tag('td')
                td_activity_name['class'] = 'text-left'
                input_activity_name = soup.new_tag('input', type='text')
                input_activity_name['class'] = 'activity-name-input'
                input_activity_name['value'] = str(row_data.get('activity_name', ''))
                td_activity_name.append(input_activity_name)
                tr.append(td_activity_name)
                
                # 원가 드라이버 (cost_driver)
                td_cost_driver = soup.new_tag('td')
                input_cost_driver = soup.new_tag('input', type='text')
                input_cost_driver['class'] = 'cost-driver-input'
                input_cost_driver['value'] = str(row_data.get('cost_driver', ''))
                td_cost_driver.append(input_cost_driver)
                tr.append(td_cost_driver)
                
                # 단위당 원가율 (cost_rate_per_unit)
                td_cost_rate = soup.new_tag('td')
                input_cost_rate = soup.new_tag('input', type='number')
                input_cost_rate['class'] = 'cost-rate-input'
                input_cost_rate['step'] = '0.01'
                input_cost_rate['value'] = str(row_data.get('cost_rate_per_unit', 0))
                td_cost_rate.append(input_cost_rate)
                tr.append(td_cost_rate)
                
                # 활동량 (activity_quantity)
                td_activity_quantity = soup.new_tag('td')
                input_activity_quantity = soup.new_tag('input', type='number')
                input_activity_quantity['class'] = 'activity-quantity-input'
                input_activity_quantity['value'] = str(row_data.get('activity_quantity', 0))
                td_activity_quantity.append(input_activity_quantity)
                tr.append(td_activity_quantity)
                
                # 총 활동 원가 (total_activity_cost)
                td_total_cost = soup.new_tag('td')
                input_total_cost = soup.new_tag('input', type='number')
                input_total_cost['class'] = 'total-activity-cost-input'
                input_total_cost['step'] = '1000'
                input_total_cost['value'] = str(row_data.get('total_activity_cost', 0))
                td_total_cost.append(input_total_cost)
                tr.append(td_total_cost)
                
                # 고정비 (fixed_cost)
                td_fixed_cost = soup.new_tag('td')
                input_fixed_cost = soup.new_tag('input', type='number')
                input_fixed_cost['class'] = 'fixed-cost-input'
                input_fixed_cost['step'] = '1000'
                input_fixed_cost['value'] = str(row_data.get('fixed_cost', 0))
                td_fixed_cost.append(input_fixed_cost)
                tr.append(td_fixed_cost)
                
                # 변동비 (variable_cost)
                td_variable_cost = soup.new_tag('td')
                input_variable_cost = soup.new_tag('input', type='number')
                input_variable_cost['class'] = 'variable-cost-input'
                input_variable_cost['step'] = '1000'
                input_variable_cost['value'] = str(row_data.get('variable_cost', 0))
                td_variable_cost.append(input_variable_cost)
                tr.append(td_variable_cost)
                
                # 고정/변동 비율 (fixed_variable_ratio)
                td_ratio = soup.new_tag('td')
                input_ratio = soup.new_tag('input', type='text')
                input_ratio['class'] = 'ratio-input'
                input_ratio['value'] = str(row_data.get('fixed_variable_ratio', ''))
                td_ratio.append(input_ratio)
                tr.append(td_ratio)
                
                # 활동 유형 (activity_type)
                td_activity_type = soup.new_tag('td')
                input_activity_type = soup.new_tag('input', type='text')
                input_activity_type['class'] = 'activity-type-input'
                input_activity_type['value'] = str(row_data.get('activity_type', ''))
                td_activity_type.append(input_activity_type)
                tr.append(td_activity_type)
                
                # 부서 (department)
                td_department = soup.new_tag('td')
                input_department = soup.new_tag('input', type='text')
                input_department['class'] = 'department-input'
                input_department['value'] = str(row_data.get('department', ''))
                td_department.append(input_department)
                tr.append(td_department)
                
                # 절감 가능 비용 (potential_savings_cost)
                td_potential_savings = soup.new_tag('td')
                input_potential_savings = soup.new_tag('input', type='number')
                input_potential_savings['class'] = 'potential-savings-input'
                input_potential_savings['step'] = '1000'
                input_potential_savings['value'] = str(row_data.get('potential_savings_cost', 0))
                td_potential_savings.append(input_potential_savings)
                tr.append(td_potential_savings)
                
                # 비필수 활동 원가 (non_essential_activity_cost)
                td_non_essential = soup.new_tag('td')
                input_non_essential = soup.new_tag('input', type='number')
                input_non_essential['class'] = 'non-essential-cost-input'
                input_non_essential['step'] = '1000'
                input_non_essential['value'] = str(row_data.get('non_essential_activity_cost', 0))
                td_non_essential.append(input_non_essential)
                tr.append(td_non_essential)
                
                # 생산성 영향 수준 (productivity_impact_level)
                td_productivity = soup.new_tag('td')
                input_productivity = soup.new_tag('input', type='text')
                input_productivity['class'] = 'productivity-impact-input'
                input_productivity['value'] = str(row_data.get('productivity_impact_level', ''))
                td_productivity.append(input_productivity)
                tr.append(td_productivity)
                
                # 원가 풀 (cost_pool)
                td_cost_pool = soup.new_tag('td')
                input_cost_pool = soup.new_tag('input', type='text')
                input_cost_pool['class'] = 'cost-pool-input'
                input_cost_pool['value'] = str(row_data.get('cost_pool', ''))
                td_cost_pool.append(input_cost_pool)
                tr.append(td_cost_pool)
                
                # 데이터 참조일 (data_reference_date)
                td_reference_date = soup.new_tag('td')
                input_reference_date = soup.new_tag('input', type='date')
                input_reference_date['class'] = 'date-input'
                date_value = str(row_data.get('data_reference_date', ''))
                # 날짜 형식 변환 (YYYY-MM-DD)
                if date_value:
                    # 이미 YYYY-MM-DD 형식이면 그대로 사용
                    if re.match(r'\d{4}-\d{2}-\d{2}', date_value):
                        input_reference_date['value'] = date_value
                    else:
                        # 다른 형식이면 변환 시도
                        try:
                            from datetime import datetime
                            if isinstance(date_value, str):
                                # 다양한 날짜 형식 파싱 시도
                                for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d']:
                                    try:
                                        dt = datetime.strptime(date_value, fmt)
                                        input_reference_date['value'] = dt.strftime('%Y-%m-%d')
                                        break
                                    except:
                                        continue
                        except:
                            input_reference_date['value'] = ''
                else:
                    # 오늘 날짜를 기본값으로
                    from datetime import datetime
                    input_reference_date['value'] = datetime.now().strftime('%Y-%m-%d')
                td_reference_date.append(input_reference_date)
                tr.append(td_reference_date)
                
                costing_body.append(tr)
            print(f"✅ costingTable: {len(field_values['costingTable'])}개 행 생성 완료")
    
    # employeeTable (hr_summary_report 템플릿용)
    if 'employeeTable' in field_values:
        print(f"🔍 employeeTable 필드 확인: {type(field_values['employeeTable'])}, 길이: {len(field_values['employeeTable']) if isinstance(field_values['employeeTable'], list) else 'N/A'}")
        
        if isinstance(field_values['employeeTable'], list):
            employee_body = soup.find('tbody', id='tableBody')
            if not employee_body:
                print(f"⚠️ ERROR: tbody#tableBody를 찾을 수 없습니다!")
                # 대안: tbody 태그를 찾아서 사용
                employee_body = soup.find('tbody')
                if not employee_body:
                    print(f"⚠️ ERROR: tbody 태그도 찾을 수 없습니다!")
                else:
                    print(f"✅ 대안: tbody 태그를 찾았습니다 (id 없음)")
            
            if employee_body:
                print(f"🔍 employeeTable 데이터 발견: {len(field_values['employeeTable'])}개 행")
                if len(field_values['employeeTable']) > 0:
                    print(f"🔍 첫 번째 employeeTable 행 샘플: {field_values['employeeTable'][0]}")
                employee_body.clear()
                
                for idx, row_data in enumerate(field_values['employeeTable']):
                    # 디버깅: 첫 번째 행 데이터 확인
                    if idx == 0:
                        print(f"🔍 HTML 채우기 - 첫 번째 행 데이터:")
                        print(f"  - name: {row_data.get('name', 'N/A')}")
                        print(f"  - dept: {row_data.get('dept', 'N/A')}")
                        print(f"  - position: {row_data.get('position', 'N/A')}")
                        print(f"  - job_role: {row_data.get('job_role', 'N/A')}")
                        print(f"  - email: {row_data.get('email', 'N/A')}")
                    tr = soup.new_tag('tr')
                    
                    # 이름
                    td_name = soup.new_tag('td')
                    input_name = soup.new_tag('input', type='text')
                    input_name['class'] = 'name-input'
                    input_name['value'] = str(row_data.get('name', ''))
                    td_name.append(input_name)
                    tr.append(td_name)
                    
                    # 부서
                    td_dept = soup.new_tag('td')
                    input_dept = soup.new_tag('input', type='text')
                    input_dept['class'] = 'dept-input'
                    input_dept['value'] = str(row_data.get('dept', ''))
                    td_dept.append(input_dept)
                    tr.append(td_dept)
                    
                    # 직급/직위
                    td_position = soup.new_tag('td')
                    input_position = soup.new_tag('input', type='text')
                    input_position['class'] = 'position-input'
                    input_position['value'] = str(row_data.get('position', ''))
                    td_position.append(input_position)
                    tr.append(td_position)
                    
                    # 역할
                    td_job_role = soup.new_tag('td')
                    input_job_role = soup.new_tag('input', type='text')
                    input_job_role['class'] = 'job-role-input'
                    input_job_role['value'] = str(row_data.get('job_role', ''))
                    td_job_role.append(input_job_role)
                    tr.append(td_job_role)
                    
                    # 이메일
                    td_email = soup.new_tag('td')
                    input_email = soup.new_tag('input', type='email')
                    input_email['class'] = 'email-input'
                    input_email['value'] = str(row_data.get('email', ''))
                    td_email.append(input_email)
                    tr.append(td_email)
                    
                    employee_body.append(tr)
                
                print(f"✅ employeeTable: {len(field_values['employeeTable'])}개 행 HTML에 채우기 완료")
                
                # 데이터가 채워졌는지 확인
                filled_rows = employee_body.find_all('tr')
                print(f"🔍 채워진 테이블 행 수: {len(filled_rows)}")
                if len(filled_rows) > 0:
                    first_row = filled_rows[0]
                    first_name_input = first_row.find('input', class_='name-input')
                    if first_name_input:
                        print(f"🔍 첫 번째 행 이름 입력값: '{first_name_input.get('value', 'N/A')}'")
                else:
                    print(f"⚠️ WARNING: 테이블에 채워진 행이 없습니다!")
            else:
                print(f"⚠️ ERROR: employee_body가 None이어서 데이터를 채울 수 없습니다!")
        else:
            print(f"⚠️ WARNING: employeeTable이 리스트가 아닙니다: {type(field_values['employeeTable'])}")
    else:
        print(f"⚠️ WARNING: field_values에 'employeeTable' 키가 없습니다!")
    
    # reportDate 필드 채우기 (hr_summary_report 템플릿용)
    if 'reportDate' in field_values:
        report_date_elem = soup.find(id='reportDate')
        if report_date_elem:
            report_date_elem.string = str(field_values['reportDate'])
            print(f"✅ reportDate 필드 채움: {field_values['reportDate']}")


def _handle_hr_summary_report_special(
    soup: BeautifulSoup,
    field_values: Dict[str, Any],
    template_name: str
) -> None:
    """hr_summary_report 템플릿 특수 처리."""
    print(f"🔍 [hr_summary_report] JavaScript 자동 계산 보장 중...")
    
    # JavaScript의 initializeTable() 호출 시 이미 데이터가 있으면 건너뛰도록 보장
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and 'initializeTable' in script.string:
            script_content = script.string
            modified = False
            
            # DOMContentLoaded 이벤트에서 calculateStats() 호출 보장
            if 'DOMContentLoaded' in script_content:
                # calculateStats()가 호출되지 않았거나 주석 처리된 경우 추가
                if 'calculateStats()' not in script_content or '// calculateStats()' in script_content:
                    # initializeTable() 호출 후 calculateStats() 추가
                    if 'initializeTable()' in script_content:
                        script_content = re.sub(
                            r'(initializeTable\(\);)',
                            r'\1\n            calculateStats(); // 통계 자동 계산',
                            script_content
                        )
                        print(f"  🔧 calculateStats() 호출 추가")
                        modified = True
                    # 또는 DOMContentLoaded 블록 끝에 추가
                    elif 'window.addEventListener' in script_content:
                        script_content = re.sub(
                            r'(window\.addEventListener\([\'"]DOMContentLoaded[\'"],\s*\(\)\s*=>\s*\{[^}]*?)(\s*\}\);)',
                            r'\1\n            calculateStats(); // 통계 자동 계산\2',
                            script_content,
                            flags=re.DOTALL
                        )
                        print(f"  🔧 calculateStats() 호출 추가 (DOMContentLoaded 끝)")
                        modified = True
                else:
                    print(f"  ✅ calculateStats() 호출 이미 존재")
            
            if modified:
                script.string = script_content
                print(f"✅ JavaScript 수정 완료 (calculateStats 자동 실행 보장)")


def _handle_work_performance_review_special(
    soup: BeautifulSoup,
    field_values: Dict[str, Any],
    template_name: str
) -> None:
    """work_performance_review 템플릿 특수 처리."""
    print(f"🔍 [work_performance_review] 평가 기간 필드 확인 중...")
    date_fields = ['year', 'startMonth', 'startDay', 'endMonth', 'endDay']
    for date_field in date_fields:
        if date_field in field_values:
            print(f"  - {date_field}: {field_values[date_field]} (타입: {type(field_values[date_field]).__name__})")
        else:
            print(f"  - {date_field}: 없음")
    
    # JavaScript의 setDefaultDate() 함수 호출 제거 및 calculateSummary() 자동 실행 보장
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string:
            script_content = script.string
            modified = False
            
            # setDefaultDate() 호출 제거
            if 'setDefaultDate' in script_content:
                lines = script_content.split('\n')
                new_lines = []
                for line in lines:
                    stripped = line.strip()
                    if 'setDefaultDate()' in stripped and not stripped.startswith('//'):
                        indent = len(line) - len(line.lstrip())
                        new_lines.append(' ' * indent + '// setDefaultDate(); // AI가 채운 값을 유지하기 위해 비활성화')
                        print(f"  🔧 setDefaultDate() 호출 주석 처리됨: {line.strip()}")
                        modified = True
                    else:
                        new_lines.append(line)
                script_content = '\n'.join(new_lines)
            
            # DOMContentLoaded 이벤트에서 calculateSummary() 호출 보장
            if 'DOMContentLoaded' in script_content:
                # calculateSummary()가 호출되지 않았거나 주석 처리된 경우 추가
                if 'calculateSummary()' not in script_content or '// calculateSummary()' in script_content:
                    # initializeTable() 다음에 calculateSummary() 추가
                    if 'initializeTable()' in script_content:
                        script_content = re.sub(
                            r'(initializeTable\(\);)',
                            r'\1\n            calculateSummary(); // 점수 자동 계산',
                            script_content
                        )
                        print(f"  🔧 calculateSummary() 호출 추가")
                        modified = True
                    # 또는 DOMContentLoaded 블록 끝에 추가
                    elif 'window.addEventListener' in script_content:
                        script_content = re.sub(
                            r'(window\.addEventListener\([\'"]DOMContentLoaded[\'"],\s*\(\)\s*=>\s*\{[^}]*?)(\s*\}\);)',
                            r'\1\n            calculateSummary(); // 점수 자동 계산\2',
                            script_content,
                            flags=re.DOTALL
                        )
                        print(f"  🔧 calculateSummary() 호출 추가 (DOMContentLoaded 끝)")
                        modified = True
                else:
                    print(f"  ✅ calculateSummary() 호출 이미 존재")
            
            if modified:
                script.string = script_content
                print(f"✅ JavaScript 수정 완료 (setDefaultDate 비활성화, calculateSummary 보장)")
    
    # label 텍스트로 필드 찾아서 채우기
    label_field_mapping = {
        '업무명': 'workName',
        '작성자명': 'authorName',
        '담당 부서장명': 'departmentHeadName',
        '주평가대상': 'evaluationTarget',
        '벌점 및 주당근무시간': 'penaltyAndWorkHours',
        '평가 방법': 'evaluationMethod',
        '선수과목': 'prerequisite',
        '후속과목': 'followUp',
    }
    
    for label_text, field_key in label_field_mapping.items():
        if field_key in field_values and field_values[field_key]:
            labels = soup.find_all('span', class_='info-label', string=re.compile(label_text, re.IGNORECASE))
            for label in labels:
                parent = label.find_parent()
                if parent:
                    input_elem = parent.find('input', class_='info-input')
                    if input_elem:
                        input_elem['value'] = str(field_values[field_key])
                        print(f"✅ {field_key} 필드 채우기: {field_values[field_key]}")
    
    # textarea 필드 처리
    textarea_mapping = {
        '평가 목표 및 개요': 'evaluationGoal',
        '참고': 'remarks',
    }
    
    for label_text, field_key in textarea_mapping.items():
        if field_key in field_values and field_values[field_key]:
            labels = soup.find_all('label', class_='textarea-label', string=re.compile(label_text, re.IGNORECASE))
            for label in labels:
                parent = label.find_parent()
                if parent:
                    textarea_elem = parent.find('textarea', class_='textarea-input')
                    if textarea_elem:
                        textarea_elem.clear()
                        from bs4 import NavigableString
                        textarea_elem.append(NavigableString(str(field_values[field_key])))
                        print(f"✅ {field_key} textarea 필드 채우기")
    
    # 평가 기간 필드 강제 설정
    date_fields_to_set = {
        'year': field_values.get('year'),
        'startMonth': field_values.get('startMonth'),
        'startDay': field_values.get('startDay'),
        'endMonth': field_values.get('endMonth'),
        'endDay': field_values.get('endDay'),
    }
    
    for date_field_id, date_value in date_fields_to_set.items():
        if date_value is not None:
            date_element = soup.find('input', id=date_field_id)
            if date_element:
                date_element['value'] = str(int(date_value))
                if 'placeholder' in date_element.attrs:
                    del date_element.attrs['placeholder']
                print(f"✅ {date_field_id} 필드 강제 설정: {date_value}")
            else:
                print(f"⚠️ {date_field_id} 필드를 찾을 수 없음")


def _handle_product_comparison_special(
    soup: BeautifulSoup,
    field_values: Dict[str, Any]
) -> None:
    """product_comparison_table 템플릿 특수 처리."""
    import re
    print(f"🔍 [product_comparison_table] 제품명 필드 확인 중...")
    
    # 제품명 필드 채우기 (product1Name, product2Name, product3Name)
    for idx in range(1, 4):
        field_key = f'product{idx}Name'
        if field_key in field_values and field_values[field_key]:
            product_name_input = soup.find('input', id=field_key)
            if product_name_input:
                product_name_input['value'] = str(field_values[field_key])
                if 'placeholder' in product_name_input.attrs:
                    del product_name_input.attrs['placeholder']
                print(f"✅ {field_key} 필드 채우기: {field_values[field_key]}")
            else:
                print(f"⚠️ {field_key} 필드를 찾을 수 없음")
    
    # 비교 목적 필드
    if 'comparisonPurpose' in field_values and field_values['comparisonPurpose']:
        purpose_input = soup.find('input', id='comparisonPurpose')
        if purpose_input:
            purpose_input['value'] = str(field_values['comparisonPurpose'])
            if 'placeholder' in purpose_input.attrs:
                del purpose_input.attrs['placeholder']
            print(f"✅ comparisonPurpose 필드 채우기: {field_values['comparisonPurpose']}")
    
    # JavaScript의 initializeTable() 호출 비활성화 (서버에서 채운 데이터 보호)
    # comparisonBody에 이미 데이터가 있으면 initializeTable()을 호출하지 않도록 수정
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and 'initializeTable' in script.string:
            script_content = script.string
            
            # initializeTable() 호출을 조건부로 변경
            # comparisonBody에 이미 행이 있으면 initializeTable()을 호출하지 않음
            new_content = re.sub(
                r'(\s*)initializeTable\(\);',
                r'\1// AI가 데이터를 채웠으면 initializeTable() 건너뜀\n'
                r'\1if (document.getElementById("comparisonBody") && document.getElementById("comparisonBody").children.length === 0) {\n'
                r'\1    initializeTable();\n'
                r'\1}',
                script_content
            )
            
            if new_content != script_content:
                script.string = new_content
                print(f"✅ initializeTable() 호출을 조건부로 변경 (데이터가 있을 경우 건너뜀)")
            break


def _update_title_tags(
    soup: BeautifulSoup,
    field_values: Dict[str, Any],
    document_title: Optional[str]
) -> None:
    """제목 태그 업데이트."""
    h1 = soup.find('h1')
    if h1:
        if 'document_title' in field_values:
            h1.string = str(field_values['document_title'])
        elif document_title:
            h1.string = str(document_title)
    
    title_tag = soup.find('title')
    if title_tag:
        if 'document_title' in field_values:
            title_tag.string = str(field_values['document_title'])
        elif document_title:
            title_tag.string = str(document_title)

