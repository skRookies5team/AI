#!/usr/bin/env python3
"""Meeting Transcriber - 회의 음성을 텍스트로 변환하는 메인 CLI 도구

Usage:
    python transcribe.py audio_file.wav
    python transcribe.py audio_file.wav --enable-diarization --speaker-max 5
"""

import os
import sys
import argparse
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.stt_client import ClovaSpeechClient, ClovaSpeechError
from src.core.formatter import format_segments_to_transcript, extract_speaker_statistics
from dotenv import load_dotenv


def load_config():
    """환경설정 로드"""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        return True
    else:
        print(f".env 파일을 찾을 수 없습니다: {env_path}")
        print(".env.example을 참고하여 설정하세요.")
        return False


def transcribe_audio(file_path: str, options: dict) -> dict:
    """오디오 파일 전사"""
    print(f"전사 시작: {file_path}")

    # CLOVA Speech 클라이언트 초기화
    try:
        client = ClovaSpeechClient(
            invoke_url=os.getenv("CLOVA_SPEECH_INVOKE_URL"),
            secret_key=os.getenv("CLOVA_SPEECH_SECRET_KEY")
        )
    except Exception as e:
        print(f"CLOVA Speech 클라이언트 초기화 실패: {e}")
        return None

    try:
        result = client.request_by_file(file_path, **options)

        if 'segments' in result:
            print(f"전사 완료: {len(result['segments'])}개 세그먼트")
            return result
        else:
            print("전사 결과에 segments가 없습니다")
            return None

    except ClovaSpeechError as e:
        print(f"전사 실패: {e}")
        return None


def save_results(file_path: str, result: dict, transcript: str, stats: dict):
    """결과 저장"""
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    base_name = Path(file_path).stem

    # 텍스트 대화록 저장
    txt_file = output_dir / f"{base_name}_transcript.txt"
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write(transcript)
    print(f"대화록 저장: {txt_file}")

    # JSON 결과 저장
    import json
    json_file = output_dir / f"{base_name}_result.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"상세 결과 저장: {json_file}")

    # 화자 통계 저장
    stats_file = output_dir / f"{base_name}_stats.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"화자 통계 저장: {stats_file}")


def main():
    parser = argparse.ArgumentParser(description="Meeting Transcriber - 회의 음성 전사 도구")
    parser.add_argument("audio_file", help="전사할 오디오 파일 경로")
    parser.add_argument("--language", default="enko", help="언어 설정 (기본: enko - 한영 혼합)")
    parser.add_argument("--disable-diarization", action="store_true", help="화자 분리 비활성화")
    parser.add_argument("--disable-noise-filtering", action="store_true", help="노이즈 필터링 비활성화")
    parser.add_argument("--enable-sed", action="store_true", help="음향 이벤트 탐지 활성화")
    parser.add_argument("--speaker-min", type=int, default=2, help="최소 화자 수")
    parser.add_argument("--speaker-max", type=int, default=10, help="최대 화자 수")
    parser.add_argument("--async-mode", action="store_true", help="비동기 모드")

    args = parser.parse_args()

    # 환경설정 로드
    if not load_config():
        return 1

    # 입력 파일 확인
    audio_path = Path(args.audio_file)
    if not audio_path.exists():
        print(f"오디오 파일을 찾을 수 없습니다: {audio_path}")
        return 1

    print("Meeting Transcriber")
    print("="*50)
    print(f"파일: {audio_path}")
    print(f"언어: {args.language}")
    print(f"화자 분리: {'활성화' if not args.disable_diarization else '비활성화'}")
    print(f"노이즈 필터링: {'활성화' if not args.disable_noise_filtering else '비활성화'}")
    print(f"음향 이벤트 탐지: {'활성화' if args.enable_sed else '비활성화'}")
    print()

    # 전사 옵션 구성
    options = {
        'language': args.language,
        'completion': 'async' if args.async_mode else 'sync',
        'wordAlignment': True,
        'fullText': True,
        'enable_diarization': not args.disable_diarization,
        'enable_noise_filtering': not args.disable_noise_filtering,
        'enable_sed': args.enable_sed
    }

    if not args.disable_diarization:
        options['diarization'] = {
            'enable': True,
            'speakerCountMin': args.speaker_min,
            'speakerCountMax': args.speaker_max
        }

    # 전사 실행
    result = transcribe_audio(str(audio_path), options)
    if not result:
        return 1

    segments = result.get('segments', [])
    if not segments:
        print("전사 결과에 세그먼트가 없습니다")
        return 1

    # 대화록 포맷팅
    print("대화록 생성 중...")
    transcript = format_segments_to_transcript(segments)

    # 화자 통계 추출
    print("화자 통계 추출 중...")
    stats = extract_speaker_statistics(segments)

    # 결과 저장
    save_results(str(audio_path), result, transcript, stats)

    # 결과 요약 출력
    print("\n" + "="*50)
    print("전사 완료")
    print("="*50)
    print(f"세그먼트: {len(segments)}개")
    print(f"화자: {len(stats)}명")

    if stats:
        print("\n화자별 발화 비율:")
        for speaker, stat in stats.items():
            duration_min = stat['total_duration_ms'] // 60000
            duration_sec = (stat['total_duration_ms'] % 60000) // 1000
            print(f"  - 화자 {speaker}: {duration_min:02d}:{duration_sec:02d} ({stat['duration_percentage']:.1f}%)")

    print(f"\n대화록 미리보기:")
    preview = transcript[:200] + "..." if len(transcript) > 200 else transcript
    print(preview)

    print(f"\n모든 결과가 'outputs/' 디렉토리에 저장되었습니다.")
    return 0


if __name__ == "__main__":
    exit(main())