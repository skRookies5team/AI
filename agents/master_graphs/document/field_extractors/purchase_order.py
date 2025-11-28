"""발주서 템플릿 필드 추출 모듈."""

import re
from datetime import datetime
from typing import Any, Dict


def extract_purchase_order_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str
) -> None:
    """발주서 템플릿의 필드 값을 요구사항에서 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (in-place 수정)
        requirements: 사용자 요구사항
        document_title: 문서 제목
    """
    # 1. orderNumber 추출
    if 'orderNumber' not in field_values or not field_values.get('orderNumber') or (isinstance(field_values.get('orderNumber'), str) and not field_values.get('orderNumber').strip()):
        order_patterns = [
            r'발주번호[:\s]+([^\n]+)',
            r'발주\s+번호[:\s]+([^\n]+)',
            r'PO[:\s-]+([^\n]+)',
        ]
        for pattern in order_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['orderNumber'] = match.group(1).strip()
                print(f"✅ 요구사항에서 orderNumber 추출: {field_values['orderNumber']}")
                break
        if 'orderNumber' not in field_values or not field_values.get('orderNumber'):
            today = datetime.now()
            field_values['orderNumber'] = f"PO-{today.year}-{today.month:02d}-{today.day:02d}"
    
    # 2. supplierName 추출
    if 'supplierName' not in field_values or not field_values.get('supplierName') or (isinstance(field_values.get('supplierName'), str) and not field_values.get('supplierName').strip()):
        supplier_patterns = [
            r'발주\s+업체[:\s]+([^\n]+)',
            r'수주처[:\s]+([^\n]+)',
            r'업체[:\s]+([^\n]+)',
        ]
        for pattern in supplier_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['supplierName'] = match.group(1).strip()
                print(f"✅ 요구사항에서 supplierName 추출: {field_values['supplierName']}")
                break
    
    # 3. buyerName 추출
    if 'buyerName' not in field_values or not field_values.get('buyerName') or (isinstance(field_values.get('buyerName'), str) and not field_values.get('buyerName').strip()):
        buyer_patterns = [
            r'발주처[:\s]+([^\n]+)',
        ]
        for pattern in buyer_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['buyerName'] = match.group(1).strip()
                print(f"✅ 요구사항에서 buyerName 추출: {field_values['buyerName']}")
                break
        if 'buyerName' not in field_values or not field_values.get('buyerName'):
            field_values['buyerName'] = '(주)깐부전자'
    
    # 4. itemsTable 추출
    if 'itemsTable' not in field_values or not field_values.get('itemsTable') or not isinstance(field_values.get('itemsTable'), list) or len(field_values.get('itemsTable', [])) == 0:
        items_rows = []
        
        # 패턴 1: 여러 줄 형식 (- 품명: ..., 규격: ..., 수량: ..., 단가: ...)
        # 예: "- 품명: 디스플레이 패널, 규격: 10.1인치, 수량: 10,000개, 단가: 5만원"
        # 개선: 각 필드를 개별적으로 추출하는 방식으로 변경
        item_lines = re.findall(r'[-•]\s*품명[:\s]+[^\n]+', requirements, re.IGNORECASE | re.MULTILINE)
        for item_line in item_lines:
            # 품명 추출
            item_name_match = re.search(r'품명[:\s]+([^,]+)', item_line, re.IGNORECASE)
            item_name = item_name_match.group(1).strip() if item_name_match else ''
            
            # 규격 추출
            spec_match = re.search(r'규격[:\s]+([^,]+)', item_line, re.IGNORECASE)
            specification = spec_match.group(1).strip() if spec_match else ''
            
            # 수량 추출
            quantity_match = re.search(r'수량[:\s]+(\d+(?:,\d+)?)\s*개', item_line, re.IGNORECASE)
            quantity_str = quantity_match.group(1) if quantity_match else '0'
            quantity = int(quantity_str.replace(',', '')) if quantity_str and quantity_str != '0' else 0
            
            # 단가 추출
            unit_price_match = re.search(r'단가[:\s]+(\d+(?:만|억|천)?원?)', item_line, re.IGNORECASE)
            unit_price_str = unit_price_match.group(1) if unit_price_match else '0'
            unit_price = 0
            if unit_price_str and unit_price_str != '0':
                num_match = re.search(r'(\d+(?:\.\d+)?)', unit_price_str)
                if num_match:
                    num = float(num_match.group(1))
                    if '억' in unit_price_str:
                        unit_price = int(num * 100000000)
                    elif '만' in unit_price_str:
                        unit_price = int(num * 10000)
                    elif '천' in unit_price_str:
                        unit_price = int(num * 1000)
                    else:
                        unit_price = int(num)
            
            if item_name:
                items_rows.append({
                    'item_name': item_name,
                    'specification': specification if specification else '',
                    'quantity': quantity if quantity > 0 else 0,
                    'unit_price': unit_price if unit_price > 0 else 50000
                })
                print(f"✅ 항목 추출: {item_name}, 규격: {specification}, 수량: {quantity}, 단가: {unit_price}")
        
        # 패턴 2: 한 줄 형식 (발주 항목: ...)
        if not items_rows:
            items_patterns = [
                r'발주\s+항목[:\s]+([^\n]+(?:\n[^\d]+[^\n]+)*)',
                r'품목[:\s]+([^\n]+(?:\n[^\d]+[^\n]+)*)',
            ]
            for pattern in items_patterns:
                match = re.search(pattern, requirements, re.IGNORECASE | re.DOTALL)
                if match:
                    items_text = match.group(1).strip()
                    # 여러 줄에서 개별 항목 추출
                    item_matches = re.findall(r'([^,]+?)\s+(\d+(?:,\d+)?)\s*개', items_text)
                    for item_name, quantity_str in item_matches:
                        quantity = int(quantity_str.replace(',', ''))
                        unit_price = 50000
                        items_rows.append({
                            'item_name': item_name.strip(),
                            'specification': '',
                            'quantity': quantity,
                            'unit_price': unit_price
                        })
                    if items_rows:
                        field_values['itemsTable'] = items_rows
                        print(f"✅ 요구사항에서 itemsTable 추출: {len(items_rows)}개 행")
                        break
        
        # 패턴 3: 상세 패턴 (품명: ... 규격: ... 수량: ... 단가: ...)
        if not items_rows:
            detailed_pattern = r'품명[:\s]+([^,규격]+?)(?:,\s*규격[:\s]+([^,수량]+?))?(?:,\s*수량[:\s]+(\d+(?:,\d+)?)\s*개)?(?:,\s*단가[:\s]+(\d+(?:만|억)?원?))?'
            matches = re.finditer(detailed_pattern, requirements, re.IGNORECASE)
            for match in matches:
                item_name = match.group(1).strip() if match.group(1) else ''
                specification = match.group(2).strip() if match.group(2) else ''
                quantity_str = match.group(3) if match.group(3) else '0'
                unit_price_str = match.group(4) if match.group(4) else '0'
                
                quantity = int(quantity_str.replace(',', '')) if quantity_str else 0
                unit_price = 0
                if unit_price_str:
                    num_match = re.search(r'(\d+(?:\.\d+)?)', unit_price_str)
                    if num_match:
                        num = float(num_match.group(1))
                        if '억' in unit_price_str:
                            unit_price = int(num * 100000000)
                        elif '만' in unit_price_str:
                            unit_price = int(num * 10000)
                        elif '천' in unit_price_str:
                            unit_price = int(num * 1000)
                        else:
                            unit_price = int(num)
                
                if item_name and quantity > 0:
                    items_rows.append({
                        'item_name': item_name,
                        'specification': specification,
                        'quantity': quantity,
                        'unit_price': unit_price if unit_price > 0 else 50000
                    })
        
        if items_rows:
            field_values['itemsTable'] = items_rows
            print(f"✅ 요구사항에서 itemsTable 추출 완료: {len(items_rows)}개 행")
    
    # 5. remarks 추출
    if 'remarks' not in field_values or not field_values.get('remarks') or (isinstance(field_values.get('remarks'), str) and not field_values.get('remarks').strip()):
        remarks_parts = []
        
        delivery_match = re.search(r'납기일[:\s]+([^\n]+)', requirements, re.IGNORECASE)
        if delivery_match:
            remarks_parts.append(f"납기일: {delivery_match.group(1).strip()}")
        
        location_match = re.search(r'납품\s+장소[:\s]+([^\n]+)', requirements, re.IGNORECASE)
        if location_match:
            remarks_parts.append(f"납품 장소: {location_match.group(1).strip()}")
        
        payment_match = re.search(r'결제\s+조건[:\s]+([^\n]+)', requirements, re.IGNORECASE)
        if payment_match:
            remarks_parts.append(f"결제 조건: {payment_match.group(1).strip()}")
        
        if remarks_parts:
            field_values['remarks'] = ', '.join(remarks_parts)
            print(f"✅ 요구사항에서 remarks 추출")
    
    # 6. 기본값 설정
    if 'companyName' not in field_values or not field_values.get('companyName'):
        field_values['companyName'] = '(주)깐부전자'
    
    if 'representative' not in field_values or not field_values.get('representative'):
        field_values['representative'] = '홍길동'

