"""통합 회의 전사 및 요약 생성기

회의 오디오 파일을 전사하고 AI 기반 요약 및 분석을 제공합니다.
"""

import os
import sys
import json
import argparse
from pathlib import Path

# 상위 디렉토리의 패키지를 import하기 위한 설정
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ncp_clova_speech import ClovaSpeechClient, ClovaSpeechError
from ncp_clova_speech.formatter import (
    format_segments_to_transcript,
    format_segments_to_detailed_transcript,
    extract_speaker_statistics,
    generate_meeting_summary_header
)
from ncp_clova_speech.report_generator import ReportGenerator, ReportGeneratorError


def load_env():
    """환경변수 로드"""
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        return True
    else:
        print(f"❌ .env 파일을 찾을 수 없습니다: {env_path}")
        print("📄 .env.example을 참고하여 설정하세요.")
        return False


def transcribe_audio(file_path: str, client: ClovaSpeechClient, advanced_options: dict) -> dict:
    """오디오 파일 전사"""
    print(f"🎵 오디오 파일 전사 중: {file_path}")

    try:
        result = client.request_by_file(
            file_path,
            language="ko-KR",
            completion="sync",
            wordAlignment=True,
            fullText=True,
            **advanced_options
        )

        if 'segments' in result:
            print(f"✅ 전사 완료: {len(result['segments'])}개 세그먼트")
            return result
        else:
            print("❌ 전사 결과에 segments가 없습니다")
            print(f"응답: {json.dumps(result, indent=2, ensure_ascii=False)}")
            return None

    except ClovaSpeechError as e:
        print(f"❌ 전사 실패: {e}")
        return None


def generate_reports(transcript: str, generator: ReportGenerator) -> dict:
    """AI 기반 보고서 생성"""
    reports = {}

    try:
        print("📋 핵심 요약 생성 중...")
        reports['summary'] = generator.summarize(transcript)

        print("📝 공식 회의록 생성 중...")
        reports['meeting_notes'] = generator.generate_meeting_notes(transcript)

        print("📋 액션 아이템 추출 중...")
        reports['action_items'] = generator.generate_action_items(transcript)

        print("🔍 회의 분위기 분석 중...")
        reports['sentiment'] = generator.analyze_sentiment(transcript)

        print("❓ 후속 질문 생성 중...")
        reports['follow_up'] = generator.generate_follow_up_questions(transcript)

        return reports

    except ReportGeneratorError as e:
        print(f"❌ 보고서 생성 실패: {e}")
        return {}


def save_results(output_dir: Path, file_name: str, stt_result: dict,
                transcript: str, detailed_transcript: str, stats: dict, reports: dict):
    """결과 파일들 저장"""
    output_dir.mkdir(exist_ok=True)

    # 1. 원본 STT 결과 (JSON)
    stt_file = output_dir / f"{file_name}_stt_result.json"
    with open(stt_file, 'w', encoding='utf-8') as f:
        json.dump(stt_result, f, indent=2, ensure_ascii=False)
    print(f"💾 STT 결과 저장: {stt_file}")

    # 2. 기본 대화록
    transcript_file = output_dir / f"{file_name}_transcript.txt"
    with open(transcript_file, 'w', encoding='utf-8') as f:
        f.write(transcript)
    print(f"💾 대화록 저장: {transcript_file}")

    # 3. 상세 대화록 (타임스탬프 포함)
    detailed_file = output_dir / f"{file_name}_detailed_transcript.txt"
    with open(detailed_file, 'w', encoding='utf-8') as f:
        f.write(detailed_transcript)
    print(f"💾 상세 대화록 저장: {detailed_file}")

    # 4. 화자 통계
    stats_file = output_dir / f"{file_name}_speaker_stats.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"💾 화자 통계 저장: {stats_file}")

    # 5. AI 요약 보고서들
    if reports:
        # 통합 보고서
        report_file = output_dir / f"{file_name}_meeting_report.md"
        with open(report_file, 'w', encoding='utf-8') as f:
            # 헤더 생성
            header = generate_meeting_summary_header(stt_result.get('segments', []))
            f.write(header)

            # 각 섹션 작성
            if 'summary' in reports:
                f.write("## 핵심 요약\n\n")
                f.write(reports['summary'])
                f.write("\n\n")

            if 'meeting_notes' in reports:
                f.write(reports['meeting_notes'])
                f.write("\n\n")

            if 'action_items' in reports:
                f.write("## 액션 아이템\n\n")
                f.write(reports['action_items'])
                f.write("\n\n")

            if 'sentiment' in reports:
                f.write("## 회의 분위기 분석\n\n")
                f.write(reports['sentiment'])
                f.write("\n\n")

            if 'follow_up' in reports:
                f.write("## 후속 질문\n\n")
                f.write(reports['follow_up'])
                f.write("\n\n")

            # 원본 대화록 첨부
            f.write("---\n\n## 원본 대화록\n\n")
            f.write(transcript)

        print(f"💾 통합 회의 보고서 저장: {report_file}")


def main():
    parser = argparse.ArgumentParser(description="통합 회의 전사 및 요약 생성기")
    parser.add_argument("audio_file", help="전사할 오디오 파일 경로")
    parser.add_argument("-o", "--output", default="outputs", help="출력 디렉토리 (기본: outputs)")
    parser.add_argument("--no-ai", action="store_true", help="AI 요약 생성 건너뛰기")
    parser.add_argument("--no-diarization", action="store_true", help="화자 분리 비활성화")
    parser.add_argument("--enable-sed", action="store_true", help="음향 이벤트 탐지 활성화")
    parser.add_argument("--speaker-min", type=int, default=2, help="최소 화자 수")
    parser.add_argument("--speaker-max", type=int, default=10, help="최대 화자 수")

    args = parser.parse_args()

    # 환경변수 로드
    if not load_env():
        return 1

    # 입력 파일 확인
    audio_path = Path(args.audio_file)
    if not audio_path.exists():
        print(f"❌ 오디오 파일을 찾을 수 없습니다: {audio_path}")
        return 1

    # 출력 디렉토리 설정
    output_dir = Path(args.output)
    file_name = audio_path.stem

    print(f"🚀 회의 전사 및 분석 시작")
    print(f"📁 입력 파일: {audio_path}")
    print(f"📁 출력 디렉토리: {output_dir}")

    # CLOVA Speech 클라이언트 초기화
    try:
        client = ClovaSpeechClient(
            invoke_url=os.getenv("CLOVA_SPEECH_INVOKE_URL"),
            secret_key=os.getenv("CLOVA_SPEECH_SECRET_KEY")
        )
    except Exception as e:
        print(f"❌ CLOVA Speech 클라이언트 초기화 실패: {e}")
        return 1

    # 고급 옵션 설정
    advanced_options = {
        'enable_diarization': not args.no_diarization,
        'enable_noise_filtering': True,
        'enable_sed': args.enable_sed
    }

    if not args.no_diarization:
        advanced_options['diarization'] = {
            'enable': True,
            'speakerCountMin': args.speaker_min,
            'speakerCountMax': args.speaker_max
        }

    # 1. 오디오 전사
    stt_result = transcribe_audio(str(audio_path), client, advanced_options)
    if not stt_result:
        return 1

    segments = stt_result.get('segments', [])
    if not segments:
        print("❌ 전사 결과에 세그먼트가 없습니다")
        return 1

    # 2. 대화록 포맷팅
    print("📝 대화록 포맷팅 중...")
    transcript = format_segments_to_transcript(segments)
    detailed_transcript = format_segments_to_detailed_transcript(
        segments, include_timestamps=True, include_confidence=True
    )

    # 3. 화자 통계 추출
    print("📊 화자 통계 추출 중...")
    stats = extract_speaker_statistics(segments)

    # 4. AI 요약 생성 (옵션)
    reports = {}
    if not args.no_ai:
        try:
            generator = ReportGenerator()
            reports = generate_reports(transcript, generator)
        except Exception as e:
            print(f"⚠️ AI 요약 생성을 건너뜁니다: {e}")
    else:
        print("⏭️ AI 요약 생성을 건너뜁니다")

    # 5. 결과 저장
    print("💾 결과 파일 저장 중...")
    save_results(output_dir, file_name, stt_result, transcript, detailed_transcript, stats, reports)

    # 6. 요약 출력
    print("\n" + "="*60)
    print("📋 처리 완료 요약")
    print("="*60)
    print(f"🎵 전사된 세그먼트: {len(segments)}개")
    print(f"👥 인식된 화자: {len(stats)}명")

    if stats:
        print("\n👥 화자별 발화 시간:")
        for speaker, stat in stats.items():
            duration_min = stat['total_duration_ms'] // 60000
            duration_sec = (stat['total_duration_ms'] % 60000) // 1000
            print(f"  - 화자 {speaker}: {duration_min:02d}:{duration_sec:02d} ({stat['duration_percentage']:.1f}%)")

    if reports and 'summary' in reports:
        print(f"\n📋 AI 핵심 요약:")
        print(reports['summary'])

    print(f"\n📁 모든 결과가 '{output_dir}' 디렉토리에 저장되었습니다.")
    return 0


if __name__ == "__main__":
    exit(main())