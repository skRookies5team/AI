#!/usr/bin/env python3
"""파일 업로드 방식 음성 인식 예제"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from ncp_clova_speech import ClovaSpeechClient, ClovaSpeechError


def setup_logging():
    """로깅 설정"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )


def save_result(result: dict, output_dir: Path, prefix: str = "file_result"):
    """결과를 파일로 저장"""
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON 결과 저장
    json_file = output_dir / f"{prefix}_{timestamp}.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 텍스트 결과 저장
    if 'text' in result:
        text_file = output_dir / f"{prefix}_{timestamp}.txt"
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(result['text'])
        print(f"텍스트 결과: {text_file}")

    print(f"JSON 결과: {json_file}")
    return json_file, text_file if 'text' in result else None


def print_result_summary(result: dict):
    """결과 요약 출력"""
    print("\n" + "="*60)
    print("음성 인식 결과")
    print("="*60)

    if 'text' in result:
        print(f"\n전체 텍스트:\n{result['text']}\n")

    if 'segments' in result:
        print(f"세그먼트 수: {len(result['segments'])}")

        for i, segment in enumerate(result['segments'][:3]):  # 처음 3개만
            print(f"\n세그먼트 {i+1}:")
            if 'text' in segment:
                print(f"  텍스트: {segment['text']}")
            if 'start' in segment:
                print(f"  시작: {segment['start']}ms")
            if 'end' in segment:
                print(f"  종료: {segment['end']}ms")
            if 'speaker' in segment:
                print(f"  화자: {segment['speaker'].get('label', 'Unknown')}")

        if len(result['segments']) > 3:
            print(f"\n... 외 {len(result['segments']) - 3}개 세그먼트")

    if 'speakers' in result:
        print(f"\n화자 정보:")
        for speaker in result['speakers']:
            print(f"  - {speaker.get('label', 'Unknown')}: {speaker.get('name', 'Unknown')}")


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description='CLOVA Speech 파일 업로드 방식 음성 인식',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python transcribe_file.py --file ./examples/sample.wav
  python transcribe_file.py --file audio.wav --completion async
  python transcribe_file.py --file audio.wav --diarization --sed
        """
    )

    parser.add_argument('--file', required=True, help='음성 파일 경로')
    parser.add_argument('--lang', default='ko-KR', help='언어 설정 (기본: ko-KR)')
    parser.add_argument('--completion', choices=['sync', 'async'], default='sync',
                        help='완료 모드 (기본: sync)')

    # 토글 옵션
    parser.add_argument('--word-alignment', action='store_true', default=True,
                        help='단어 정렬 활성화 (기본값)')
    parser.add_argument('--no-word-alignment', action='store_false', dest='word_alignment',
                        help='단어 정렬 비활성화')
    parser.add_argument('--full-text', action='store_true', default=True,
                        help='전체 텍스트 활성화 (기본값)')
    parser.add_argument('--no-full-text', action='store_false', dest='full_text',
                        help='전체 텍스트 비활성화')

    # 고급 옵션
    parser.add_argument('--callback', help='콜백 URL (async 모드)')
    parser.add_argument('--userdata', help='사용자 데이터')
    parser.add_argument('--diarization', action='store_true', help='화자 분리 활성화')
    parser.add_argument('--sed', action='store_true', help='음향 이벤트 탐지 활성화')

    args = parser.parse_args()

    # 로깅 설정
    setup_logging()

    # .env 파일 로드
    load_dotenv()

    # 환경변수 확인
    invoke_url = os.getenv('CLOVA_SPEECH_INVOKE_URL')
    secret_key = os.getenv('CLOVA_SPEECH_SECRET_KEY')

    if not invoke_url:
        print("오류: CLOVA_SPEECH_INVOKE_URL이 설정되지 않았습니다.")
        print("   .env 파일에 CLOVA_SPEECH_INVOKE_URL을 설정해주세요.")
        sys.exit(1)

    if not secret_key:
        print("오류: CLOVA_SPEECH_SECRET_KEY가 설정되지 않았습니다.")
        print("   .env 파일에 CLOVA_SPEECH_SECRET_KEY를 설정해주세요.")
        sys.exit(1)

    # 파일 확인
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"오류: 파일을 찾을 수 없습니다: {args.file}")
        sys.exit(1)

    # 클라이언트 생성
    client = ClovaSpeechClient(invoke_url, secret_key)

    # 옵션 구성
    options = {
        'language': args.lang,
        'completion': args.completion,
        'wordAlignment': args.word_alignment,
        'fullText': args.full_text
    }

    if args.callback:
        options['callback'] = args.callback
    if args.userdata:
        options['userdata'] = args.userdata
    if args.diarization:
        options['diarization'] = {'enable': True}
    if args.sed:
        options['sed'] = {'enable': True}

    print(f"파일: {file_path}")
    print(f"파일 크기: {file_path.stat().st_size:,} bytes")
    print(f"언어: {args.lang}")
    print(f"모드: {args.completion}")
    print(f"단어 정렬: {'활성화' if args.word_alignment else '비활성화'}")
    print(f"전체 텍스트: {'활성화' if args.full_text else '비활성화'}")
    print(f"화자 분리: {'활성화' if args.diarization else '비활성화'}")
    print(f"음향 이벤트: {'활성화' if args.sed else '비활성화'}")

    try:
        print("\n요청 전송...")
        result = client.request_by_file(file_path, **options)

        if args.completion == 'async':
            print(f"작업 ID: {result}")
            print("처리 중... (폴링 시작)")

            result = client.wait_for_completion(result)
            print("완료!")
        else:
            print("완료!")

        # 결과 출력
        print_result_summary(result)

        # 결과 저장
        output_dir = Path("outputs")
        save_result(result, output_dir, "file_result")

    except ClovaSpeechError as e:
        print(f"CLOVA Speech 오류: {e}")
        if "401" in str(e):
            print("   Secret Key를 확인해주세요.")
        elif "403" in str(e):
            print("   API 권한을 확인해주세요.")
        elif "404" in str(e):
            print("   Invoke URL이 올바른지 확인해주세요.")
        elif "413" in str(e):
            print("   파일이 너무 큽니다. 작은 파일로 시도해주세요.")
        elif "415" in str(e):
            print("   지원하지 않는 파일 형식입니다.")
        sys.exit(1)
    except Exception as e:
        print(f"예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()