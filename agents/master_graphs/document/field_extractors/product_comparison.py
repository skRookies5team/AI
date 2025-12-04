"""제품 비교표 템플릿 필드 추출 모듈."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional


def extract_product_comparison_fields(
    field_values: Dict[str, Any],
    requirements: str,
    document_title: str,
    db_data: Optional[List[Dict[str, Any]]] = None
) -> None:
    """제품 비교표 템플릿의 필드 값을 요구사항과 DB 데이터에서 추출합니다.
    
    Args:
        field_values: 필드 값 딕셔너리 (in-place 수정)
        requirements: 사용자 요구사항
        document_title: 문서 제목
        db_data: DB에서 조회한 제품 데이터 리스트 (None이거나 빈 리스트일 수 있음)
    """
    print(f"🔍 [extract_product_comparison_fields] 시작")
    print(f"  - db_data: {len(db_data) if db_data else 0}개 제품")
    print(f"  - field_values에 이미 있는 키: {list(field_values.keys())}")
    # 1. 비교 목적 추출
    if 'comparisonPurpose' not in field_values or not field_values.get('comparisonPurpose') or (isinstance(field_values.get('comparisonPurpose'), str) and not field_values.get('comparisonPurpose').strip()):
        purpose_patterns = [
            r'비교\s+목적[:\s]+([^\n]+)',
            r'목적[:\s]+([^\n]+)',
            r'비교[:\s]+([^\n]+)',
        ]
        for pattern in purpose_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                field_values['comparisonPurpose'] = match.group(1).strip()
                print(f"✅ 요구사항에서 comparisonPurpose 추출: {field_values['comparisonPurpose']}")
                break
        if 'comparisonPurpose' not in field_values or not field_values.get('comparisonPurpose'):
            field_values['comparisonPurpose'] = '제품 성능 비교'
    
    # 2. 제품명 추출 (요구사항에서)
    if db_data and isinstance(db_data, list) and len(db_data) > 0:
        # DB 데이터에서 제품명 추출 (mobile_devices 테이블)
        product_names = []
        for product in db_data[:3]:  # 최대 3개
            # 대소문자 구분 없이 컬럼명 검색 (company_name, model_name 컬럼 사용)
            brand = None
            model = None
            for key in product.keys():
                key_lower = key.lower()
                if key_lower in ['brand', 'company_name']:
                    brand = product[key]
                elif key_lower in ['model', 'model_name']:
                    model = product[key]
            
            brand = str(brand) if brand else ''
            model = str(model) if model else ''
            
            if brand and model:
                product_name = f"{brand} {model}".strip()
                product_names.append(product_name)
            elif model:
                product_names.append(model)
            elif brand:
                product_names.append(brand)
        
        # field_values에 제품명 저장
        for idx, name in enumerate(product_names[:3], 1):
            field_values[f'product{idx}Name'] = name
            print(f"✅ DB에서 product{idx}Name 추출: {name}")
    
    # 요구사항에서도 제품명 추출 시도 (DB 데이터가 없는 경우 또는 DB 데이터가 있어도 제품명이 없는 경우)
    # "비교 대상: iPhone 15 Pro 512GB, Galaxy S24 Ultra 128GB, Pixel 8 Pro 256GB" 형식 처리
    for idx in range(1, 4):
        if f'product{idx}Name' not in field_values or not field_values.get(f'product{idx}Name'):
            # 1. "비교 대상:" 패턴으로 여러 제품명 추출
            comparison_target_pattern = r'비교\s+대상[:\s]+([^\n]+)'
            match = re.search(comparison_target_pattern, requirements, re.IGNORECASE)
            if match:
                targets_text = match.group(1).strip()
                # 쉼표로 구분된 제품명 추출
                products = [p.strip() for p in re.split(r'[,，、]', targets_text) if p.strip()]
                if idx <= len(products):
                    product_name = products[idx - 1].strip()
                    # 저장공간 정보 제거 (512GB, 128GB 등)
                    product_name = re.sub(r'\s*\d+\s*(GB|TB|gb|tb)\s*$', '', product_name).strip()
                    if product_name and product_name not in ['제품 1', '제품 2', '제품 3']:
                        field_values[f'product{idx}Name'] = product_name
                        print(f"✅ 요구사항에서 product{idx}Name 추출: {product_name}")
                        continue
            
            # 2. 개별 제품명 패턴
            product_patterns = [
                rf'제품\s*{idx}[:\s]+([^\n,]+)',
            ]
            for pattern in product_patterns:
                match = re.search(pattern, requirements, re.IGNORECASE)
                if match:
                    product_name = match.group(1).strip()
                    # 저장공간 정보 제거
                    product_name = re.sub(r'\s*\d+\s*(GB|TB|gb|tb)\s*$', '', product_name).strip()
                    if product_name and product_name not in ['제품 1', '제품 2', '제품 3']:
                        field_values[f'product{idx}Name'] = product_name
                        print(f"✅ 요구사항에서 product{idx}Name 추출: {product_name}")
                        break
    
    # 3. 비교 항목 테이블 데이터 생성 (DB 데이터 기반)
    if db_data and isinstance(db_data, list) and len(db_data) > 0:
        comparison_rows = []
        
        # DB에서 실제 컬럼명 가져오기 (첫 번째 제품의 키를 기준으로)
        first_product = db_data[0]
        actual_db_columns = list(first_product.keys())
        print(f"📋 DB에서 발견된 실제 컬럼명: {actual_db_columns}")
        
        # 실제 DB에 있는 컬럼 중 비교 항목으로 사용할 컬럼만 필터링
        # company_name, model_name은 제외 (제품명으로 이미 사용됨)
        comparison_columns = []
        for col in actual_db_columns:
            # company_name, model_name, brand, model은 제외
            if col.lower() in ['brand', 'company_name', 'model', 'model_name']:
                continue
            # 나머지 컬럼은 모두 비교 항목으로 사용
            comparison_columns.append(col)
        
        print(f"📋 비교 항목으로 사용할 컬럼: {comparison_columns}")
        
        # 컬럼명 → 한국어 항목명 매핑 (실제 DB 컬럼명 기준)
        column_to_korean = {
            # 가격 관련
            "launch_price_kor": "가격", "launch_price_usa": "가격", "price": "가격", "Price": "가격", "PRICE": "가격",
            # 무게 관련
            "weight": "무게", "Weight": "무게", "WEIGHT": "무게",
            # 배터리 관련
            "battery_capacity": "배터리", "Battery": "배터리", "battery": "배터리", "BATTERY": "배터리",
            # 디스플레이 관련
            "screen_size": "디스플레이", "Display_Size": "디스플레이", "Display": "디스플레이", "display_size": "디스플레이", 
            "display": "디스플레이", "DISPLAY_SIZE": "디스플레이",
            # 프로세서 관련
            "processor": "프로세서", "Processor": "프로세서", "processor": "프로세서", "CPU": "프로세서", "cpu": "프로세서", "PROCESSOR": "프로세서",
            # 카메라 관련
            "front_camera": "전면 카메라", "rear_camera": "후면 카메라", "Camera": "카메라", "camera": "카메라", "CAMERA": "카메라",
            # 저장공간 관련
            "Storage": "저장공간", "storage": "저장공간", "STORAGE": "저장공간",
            # RAM 관련
            "ram": "RAM", "RAM": "RAM", "ram": "RAM", "Ram": "RAM", "Memory": "RAM", "memory": "RAM", "MEMORY": "RAM",
            # 운영체제 관련
            "Operating_System": "운영체제", "operating_system": "운영체제", "OS": "운영체제", "os": "운영체제", "Os": "운영체제",
            # 네트워크 관련
            "Network": "네트워크", "network": "네트워크", "NETWORK": "네트워크",
            # 색상 관련
            "Color": "색상", "color": "색상", "Colour": "색상", "colour": "색상", "COLOR": "색상",
        }
        
        # 비교 항목 리스트 생성 (한국어 항목명 사용)
        comparison_items = []
        for col in comparison_columns:
            # 컬럼명을 한국어로 매핑
            korean_name = None
            for db_col, kor_name in column_to_korean.items():
                if col.lower() == db_col.lower():
                    korean_name = kor_name
                    break
            
            # 매핑이 없으면 컬럼명을 그대로 사용
            if not korean_name:
                korean_name = col
            
            comparison_items.append({
                "item": korean_name,  # 한국어 항목명 사용
                "db_columns": [col]  # 실제 DB 컬럼명
            })
        
        print(f"📋 생성된 비교 항목: {len(comparison_items)}개 (한국어 항목명 사용)")
        
        for item_info in comparison_items:
            item_name = item_info["item"]
            db_columns = item_info["db_columns"]
            
            row_data = {
                "item_name": item_name,
                "product1_value": "",
                "product2_value": "",
                "product3_value": ""
            }
            
            # 각 제품의 데이터 추출 (기종별로 value 가져오기)
            for idx, product in enumerate(db_data[:3], 1):
                value = ""
                col_value = None
                
                # 실제 DB 컬럼명으로 데이터 추출 (db_columns는 실제 DB 컬럼명 리스트)
                for col in db_columns:
                    # 직접 키로 검색 (대소문자 구분)
                    if col in product:
                        col_value = product[col]
                    else:
                        # 대소문자 무시하고 검색
                        for key in product.keys():
                            if key.lower() == col.lower():
                                col_value = product[key]
                                break
                    
                    # 값이 있으면 포맷팅 후 break
                    if col_value is not None and col_value != "" and str(col_value).strip() != "":
                        # 숫자형 데이터 포맷팅 (실제 DB 컬럼명 기반)
                        if isinstance(col_value, (int, float)):
                            col_lower = col.lower()
                            # 가격: 천단위 콤마 + 원
                            if col_lower in ['launch_price_kor', 'launch_price_usa', 'price']:
                                value = f"{int(col_value):,}원"
                            # 무게: g 단위
                            elif col_lower in ['weight']:
                                value = f"{col_value}g"
                            # 배터리: mAh 단위
                            elif col_lower in ['battery_capacity', 'battery']:
                                value = f"{int(col_value)}mAh"
                            # RAM: GB 단위
                            elif col_lower in ['ram', 'memory']:
                                value = f"{int(col_value)}GB"
                            # 저장공간: GB 단위
                            elif col_lower in ['storage']:
                                value = f"{int(col_value)}GB"
                            # 디스플레이: inches 단위
                            elif col_lower in ['screen_size', 'display_size', 'display']:
                                value = f"{col_value} inches"
                            # 기타 숫자: 그대로 문자열 변환
                            else:
                                value = str(col_value)
                        else:
                            # 문자열 데이터: 공백 제거 후 그대로 사용
                            value = str(col_value).strip()
                        
                        # 값이 있으면 다음 컬럼 시도하지 않고 break
                        if value and value.strip() != "":
                            print(f"    ✅ 제품 {idx} - {item_name}: {col} → {value}")
                            break
                
                # 값이 있으면 row_data에 저장
                if value:
                    row_data[f"product{idx}_value"] = value
                else:
                    print(f"    ⚠️ 제품 {idx} - {item_name}: 데이터 없음 (컬럼: {db_columns})")
            
            comparison_rows.append(row_data)
        
        if comparison_rows:
            field_values['comparisonTable'] = comparison_rows
            print(f"✅ DB 데이터 기반 comparisonTable 생성: {len(comparison_rows)}개 행")
        else:
            print(f"⚠️ DB 데이터가 있지만 comparisonTable 행이 생성되지 않음")
    
    # 4. 비교 항목 추출 (요구사항에서, DB 데이터가 없는 경우 또는 DB 데이터가 있어도 comparisonTable이 비어있는 경우)
    if 'comparisonTable' not in field_values or not field_values.get('comparisonTable') or not isinstance(field_values.get('comparisonTable'), list) or len(field_values.get('comparisonTable', [])) == 0:
        print(f"⚠️ comparisonTable이 비어있음. 기본 비교 항목으로 초기화합니다...")
        
        # 기본 비교 항목 정의 (한국어 항목명 사용)
        default_items = [
            "가격",
            "무게",
            "배터리",
            "디스플레이",
            "프로세서",
            "카메라",
            "저장공간",
            "RAM",
            "운영체제",
            "네트워크",
            "색상",
        ]
        
        comparison_rows = []
        for item_name in default_items:
            comparison_rows.append({
                "item_name": item_name,
                "product1_value": "",
                "product2_value": "",
                "product3_value": ""
            })
        
        field_values['comparisonTable'] = comparison_rows
        print(f"✅ 기본 comparisonTable 생성: {len(comparison_rows)}개 행 (DB 데이터 없음 또는 조회 실패)")
        # 요구사항에서 비교 항목 추출
        item_patterns = [
            r'비교\s+항목[:\s]+([^\n]+)',
            r'항목[:\s]+([^\n]+)',
        ]
        
        items = []
        for pattern in item_patterns:
            match = re.search(pattern, requirements, re.IGNORECASE)
            if match:
                items_text = match.group(1).strip()
                # 쉼표나 줄바꿈으로 구분된 항목 추출
                items = [item.strip() for item in re.split(r'[,，、\n]', items_text) if item.strip()]
                break
        
        if items:
            comparison_rows = []
            for item in items[:10]:  # 최대 10개 항목
                comparison_rows.append({
                    "item_name": item,
                    "product1_value": "",
                    "product2_value": "",
                    "product3_value": ""
                })
            
            if comparison_rows:
                field_values['comparisonTable'] = comparison_rows
                print(f"✅ 요구사항에서 comparisonTable 추출: {len(comparison_rows)}개 행")

