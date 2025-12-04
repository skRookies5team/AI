"""
특허 검색 플로우 다이어그램을 이미지로 변환하는 스크립트
"""
import re
import base64
import urllib.parse
import requests
from pathlib import Path
from typing import List, Tuple


def extract_mermaid_blocks(content: str) -> List[Tuple[str, str]]:
    """마크다운 파일에서 Mermaid 코드 블록 추출"""
    pattern = r'```mermaid\n(.*?)\n```'
    matches = re.findall(pattern, content, re.DOTALL)
    
    # 제목도 함께 추출 (### 또는 ## 또는 # 제목 형식)
    titles = []
    lines = content.split('\n')
    current_title = None
    
    for i, line in enumerate(lines):
        # 제목 추출
        if line.startswith('## '):
            current_title = line[3:].strip()
        elif line.startswith('### '):
            current_title = line[4:].strip()
        elif line.startswith('# '):
            current_title = line[2:].strip()
        
        # 다음 mermaid 블록을 만나면 제목 저장
        if line.strip() == '```mermaid' and current_title:
            titles.append(current_title)
    
    results = []
    for i, code in enumerate(matches):
        title = titles[i] if i < len(titles) else f"diagram_{i+1}"
        results.append((title, code.strip()))
    
    return results


def mermaid_to_image_url(mermaid_code: str, theme: str = "default") -> str:
    """Mermaid 코드를 이미지 URL로 변환"""
    # Mermaid 코드를 base64로 인코딩
    encoded = base64.urlsafe_b64encode(mermaid_code.encode('utf-8')).decode('utf-8')
    # mermaid.ink API 사용
    return f"https://mermaid.ink/img/{encoded}?theme={theme}"


def download_image(url: str, output_path: Path) -> bool:
    """이미지 URL에서 이미지 다운로드"""
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        return True
    except Exception as e:
        print(f"[ERROR] 이미지 다운로드 실패 ({output_path.name}): {e}")
        return False


def sanitize_filename(title: str) -> str:
    """파일명으로 사용할 수 있도록 정리"""
    # 특수 문자 제거
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', title)
    # 공백을 언더스코어로
    sanitized = re.sub(r'\s+', '_', sanitized)
    # 연속된 언더스코어 제거
    sanitized = re.sub(r'_+', '_', sanitized).strip('_')
    return sanitized


def convert_markdown_to_images(markdown_file: str, output_dir: str = "patent_flow_images"):
    """마크다운 파일의 Mermaid 다이어그램을 이미지로 변환"""
    # 파일 읽기
    with open(markdown_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Mermaid 블록 추출
    diagrams = extract_mermaid_blocks(content)
    
    if not diagrams:
        print("[ERROR] Mermaid 다이어그램을 찾을 수 없습니다.")
        return
    
    # 출력 디렉토리 생성
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    print(f"총 {len(diagrams)}개의 다이어그램을 발견했습니다.\n")
    
    # 각 다이어그램을 이미지로 변환
    for i, (title, code) in enumerate(diagrams, 1):
        print(f"[{i}/{len(diagrams)}] 처리 중: {title}")
        
        # 파일명 생성
        filename = sanitize_filename(title)
        if not filename:
            filename = f"diagram_{i}"
        
        # 이미지 URL 생성
        image_url = mermaid_to_image_url(code)
        
        # 이미지 다운로드
        output_file = output_path / f"{filename}.png"
        
        if download_image(image_url, output_file):
            print(f"[OK] 저장 완료: {output_file}\n")
        else:
            print(f"[FAIL] 실패: {title}\n")
    
    print(f"[완료] 이미지들이 '{output_dir}' 폴더에 저장되었습니다.")


def convert_via_playwright(markdown_file: str, output_dir: str = "patent_flow_images"):
    """
    Playwright를 사용하여 Mermaid를 직접 렌더링 (더 고품질)
    설치 필요: pip install playwright
    초기 설정: playwright install chromium
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERROR] Playwright가 설치되지 않았습니다.")
        print("설치 방법: pip install playwright && playwright install chromium")
        return
    
    # 파일 읽기
    with open(markdown_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    diagrams = extract_mermaid_blocks(content)
    
    if not diagrams:
        print("[ERROR] Mermaid 다이어그램을 찾을 수 없습니다.")
        return
    
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    
    print(f"총 {len(diagrams)}개의 다이어그램을 발견했습니다.\n")
    
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
        <style>
            body { 
                margin: 0; 
                padding: 40px; 
                background: white;
                font-family: Arial, sans-serif;
            }
            .mermaid { 
                display: flex;
                justify-content: center;
                align-items: center;
                width: 100%;
                overflow-x: auto;
            }
        </style>
    </head>
    <body>
        <div class="mermaid">
            {mermaid_code}
        </div>
        <script>
            mermaid.initialize({{ 
                startOnLoad: true,
                theme: 'default',
                flowchart: {{
                    useMaxWidth: true,
                    htmlLabels: true
                }},
                sequence: {{
                    diagramMarginX: 50,
                    diagramMarginY: 10,
                    actorMargin: 50,
                    width: 150,
                    height: 65,
                    boxMargin: 10,
                    boxTextMargin: 5,
                    noteMargin: 10,
                    messageMargin: 35
                }}
            }});
        </script>
    </body>
    </html>
    """
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        
        for i, (title, code) in enumerate(diagrams, 1):
            print(f"[{i}/{len(diagrams)}] 처리 중: {title}")
            
            filename = sanitize_filename(title)
            if not filename:
                filename = f"diagram_{i}"
            
            output_file = output_path / f"{filename}.png"
            
            try:
                # HTML 생성
                html = html_template.format(mermaid_code=code)
                
                # 브라우저 페이지 생성
                page = browser.new_page()
                page.set_content(html)
                
                # 렌더링 대기
                page.wait_for_selector('.mermaid svg', timeout=30000)
                
                # 추가 대기 (렌더링 완료를 위해)
                page.wait_for_timeout(2000)
                
                # 스크린샷 저장
                page.screenshot(path=str(output_file), full_page=True, timeout=60000)
                
                print(f"[OK] 저장 완료: {output_file}\n")
                
            except Exception as e:
                print(f"[FAIL] 실패 ({title}): {e}\n")
            finally:
                try:
                    page.close()
                except:
                    pass
        
        browser.close()
    
    print(f"[완료] 이미지들이 '{output_dir}' 폴더에 저장되었습니다.")


if __name__ == "__main__":
    import sys
    
    markdown_file = "patent_search_flow_diagram.md"
    choice = "1"  # 기본값
    
    # 명령줄 인자 파싱
    if len(sys.argv) > 1:
        markdown_file = sys.argv[1]
    if len(sys.argv) > 2:
        choice = sys.argv[2]
    
    if not Path(markdown_file).exists():
        print(f"[ERROR] 파일을 찾을 수 없습니다: {markdown_file}")
        sys.exit(1)
    
    print("=" * 60)
    print("특허 검색 플로우 다이어그램 -> 이미지 변환 도구")
    print("=" * 60)
    print(f"입력 파일: {markdown_file}\n")
    
    # 명령줄 인자가 없으면 대화형으로 선택
    if len(sys.argv) <= 2:
        print("변환 방법을 선택하세요:")
        print("1. mermaid.ink API (간단, 인터넷 필요)")
        print("2. Playwright (고품질, 브라우저 필요)")
        
        try:
            user_choice = input("\n선택 (1 또는 2, 기본값: 1): ").strip() or "1"
            choice = user_choice
        except:
            choice = "1"
    
    if choice == "2":
        print("\n[Playwright 방식으로 변환 중...]\n")
        convert_via_playwright(markdown_file)
    else:
        print("\n[mermaid.ink API 방식으로 변환 중...]\n")
        convert_markdown_to_images(markdown_file)

