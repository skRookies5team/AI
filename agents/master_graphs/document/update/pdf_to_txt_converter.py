"""PDF를 TXT로 변환하는 유틸리티 모듈.

PDF 파일에서 표와 텍스트를 추출하여 TXT 파일로 변환합니다.
표 데이터는 열(column) 기준으로 세로 나열 형식으로 포맷팅됩니다.

이 모듈은 document_update_service에서도 사용됩니다.
"""

import os
import re
from pathlib import Path
from typing import List, Optional

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    pdfplumber = None

# .env 파일 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def extract_tables_from_pdf(pdf_path: str) -> List[List[List[str]]]:
    """PDF 파일에서 표를 추출합니다.
    
    Args:
        pdf_path: PDF 파일 경로
        
    Returns:
        추출된 표들의 리스트 (각 표는 리스트의 리스트 형태)
        
    Raises:
        ImportError: pdfplumber가 설치되지 않은 경우
    """
    if not PDFPLUMBER_AVAILABLE:
        raise ImportError(
            "pdfplumber가 설치되지 않았습니다. "
            "pip install pdfplumber를 실행하여 설치해주세요."
        )
    
    tables = []
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            print(f"PDF 파일 열기: {os.path.basename(pdf_path)} ({len(pdf.pages)}페이지)")
            
            for page_num, page in enumerate(pdf.pages, 1):
                # 표 추출 시도
                page_tables = page.extract_tables()
                
                if page_tables:
                    print(f"  페이지 {page_num}: {len(page_tables)}개 표 발견")
                    tables.extend(page_tables)
                else:
                    # 표가 없는 경우 일반 텍스트 추출
                    text = page.extract_text()
                    if text and text.strip():
                        # 텍스트를 표 형식처럼 변환 시도
                        lines = text.split('\n')
                        if lines:
                            # 빈 줄 제거 및 정리
                            cleaned_lines = [line.strip() for line in lines if line.strip()]
                            if cleaned_lines:
                                # 각 줄을 리스트로 변환 (표 형식처럼)
                                table_like = [[line] for line in cleaned_lines]
                                tables.append(table_like)
                                print(f"  페이지 {page_num}: 텍스트 추출 (표 형식으로 변환)")
    
    except Exception as e:
        print(f"PDF 처리 오류 ({pdf_path}): {e}")
        return []
    
    return tables


def format_table_as_text(table: List[List[str]]) -> str:
    """표 데이터를 열(column) 기준으로 세로 나열 형식으로 포맷팅합니다.
    
    모든 열(첫 번째 열 포함)을 세로로 나열합니다.
    
    Args:
        table: 표 데이터 (리스트의 리스트)
        
    Returns:
        포맷팅된 텍스트 문자열 (각 열이 세로로 나열됨)
    """
    if not table:
        return ""
    
    # 표 정리: None을 빈 문자열로 변환하고 공백 제거
    cleaned_table = []
    for row in table:
        cleaned_row = []
        for cell in row:
            if cell is None:
                cell = ""
            cell_str = str(cell).strip()
            cleaned_row.append(cell_str)
        cleaned_table.append(cleaned_row)
    
    if not cleaned_table:
        return ""
    
    # 최대 열 수 찾기
    max_cols = max(len(row) for row in cleaned_table) if cleaned_table else 0
    
    # 모든 열을 데이터로 처리 (첫 번째 열 포함)
    # 각 열별로 데이터 수집 (세로 방향)
    columns = []
    for col_idx in range(0, max_cols):  # 첫 번째 열(인덱스 0) 포함
        column_data = []
        for row in cleaned_table:
            if col_idx < len(row):
                cell = row[col_idx].strip()
                if cell:  # 빈 셀이 아닌 경우만 추가
                    column_data.append(cell)
        if column_data:  # 빈 열이 아닌 경우만 추가
            columns.append(column_data)
    
    # 각 열을 세로로 나열 (열 사이에 빈 줄 추가)
    formatted_lines = []
    for col_idx, column in enumerate(columns):
        # 각 열의 데이터를 세로로 나열
        for cell in column:
            formatted_lines.append(cell)
        
        # 마지막 열이 아니면 빈 줄 추가
        if col_idx < len(columns) - 1:
            formatted_lines.append("")
    
    return "\n".join(formatted_lines)


def clean_text(text: str) -> str:
    """텍스트를 정리합니다 (불필요한 공백, 특수문자 등).
    
    Args:
        text: 원본 텍스트
        
    Returns:
        정리된 텍스트
    """
    # 여러 공백을 하나로
    text = re.sub(r'\s+', ' ', text)
    # 줄바꿈 정리
    text = re.sub(r'\n\s*\n', '\n\n', text)
    # 앞뒤 공백 제거
    text = text.strip()
    
    return text


def convert_pdf_to_txt(pdf_path: str, output_path: Optional[str] = None) -> str:
    """PDF 파일을 TXT 파일로 변환합니다.
    
    Args:
        pdf_path: PDF 파일 경로
        output_path: 출력 TXT 파일 경로 (None이면 자동 생성)
        
    Returns:
        생성된 TXT 파일 경로
        
    Raises:
        FileNotFoundError: PDF 파일을 찾을 수 없는 경우
        ImportError: pdfplumber가 설치되지 않은 경우
    """
    if not PDFPLUMBER_AVAILABLE:
        raise ImportError(
            "pdfplumber가 설치되지 않았습니다. "
            "pip install pdfplumber를 실행하여 설치해주세요."
        )
    
    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")
    
    # 출력 경로 자동 생성
    if output_path is None:
        pdf_name = pdf_path_obj.stem
        output_dir = pdf_path_obj.parent
        output_path = str(output_dir / f"{pdf_name}.txt")
    
    print(f"\n변환 시작: {os.path.basename(pdf_path)}")
    
    # 표 추출
    tables = extract_tables_from_pdf(pdf_path)
    
    if not tables:
        print("표를 찾을 수 없습니다. 일반 텍스트로 추출을 시도합니다...")
        # 일반 텍스트 추출
        try:
            with pdfplumber.open(pdf_path) as pdf:
                all_text = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        all_text.append(clean_text(text))
                
                if all_text:
                    content = "\n\n".join(all_text)
                else:
                    content = "텍스트를 추출할 수 없습니다."
        except Exception as e:
            print(f"텍스트 추출 실패: {e}")
            content = f"PDF 처리 중 오류 발생: {e}"
    else:
        # 표들을 텍스트로 변환
        table_texts = []
        for idx, table in enumerate(tables, 1):
            table_text = format_table_as_text(table)
            if table_text:
                # 표 제목 제거하고 바로 내용만 추가
                table_texts.append(table_text)
        
        # 모든 표를 하나로 합치기 (표 사이에 빈 줄 추가)
        content = "\n\n".join(table_texts)
    
    # TXT 파일로 저장
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"변환 완료: {os.path.basename(output_path)}")
        return output_path
    except Exception as e:
        print(f"파일 저장 실패: {e}")
        raise


def process_pdf_folder(folder_path: str = "data", output_folder: Optional[str] = None) -> None:
    """폴더 내의 모든 PDF 파일을 TXT로 변환합니다.
    
    Args:
        folder_path: PDF 파일이 있는 폴더 경로
        output_folder: 출력 폴더 경로 (None이면 입력 폴더와 동일)
    """
    folder = Path(folder_path)
    
    if not folder.exists():
        print(f"폴더를 찾을 수 없습니다: {folder_path}")
        return
    
    # 출력 폴더 설정
    if output_folder is None:
        output_folder = folder_path
    else:
        output_folder = Path(output_folder)
        output_folder.mkdir(parents=True, exist_ok=True)
    
    # PDF 파일 찾기
    pdf_files = list(folder.glob("*.pdf"))
    
    if not pdf_files:
        print(f"PDF 파일을 찾을 수 없습니다: {folder_path}")
        return
    
    print(f"폴더: {folder_path}")
    print(f"발견된 PDF 파일: {len(pdf_files)}개\n")
    
    # 각 PDF 파일 처리
    success_count = 0
    for pdf_file in pdf_files:
        try:
            output_path = Path(output_folder) / f"{pdf_file.stem}.txt"
            convert_pdf_to_txt(str(pdf_file), str(output_path))
            success_count += 1
        except Exception as e:
            print(f"처리 실패 ({pdf_file.name}): {e}\n")
    
    print(f"\n{'='*60}")
    print(f"완료: {success_count}/{len(pdf_files)}개 파일 변환 성공")
    print(f"{'='*60}")