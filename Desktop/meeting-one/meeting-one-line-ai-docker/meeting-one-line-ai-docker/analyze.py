#!/usr/bin/env python3
"""Meeting Analyzer - AI 기반 회의 분석 도구

Usage:
    python analyze.py transcript.txt
    python analyze.py transcript.txt --summary --action-items
    python analyze.py audio.wav --full-analysis
"""

import os
import sys
import argparse
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.stt_client import ClovaSpeechClient, ClovaSpeechError
from src.core.formatter import (
    format_segments_to_transcript,
    format_segments_to_detailed_transcript,
    extract_speaker_statistics,
    generate_meeting_summary_header
)
from src.core.ai_analyzer import ReportGenerator, ReportGeneratorError
from src.core.embedding_manager import EmbeddingManager
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


def transcribe_if_audio(file_path: str) -> str:
    """오디오 파일인 경우 전사 후 대화록 반환"""
    audio_extensions = {'.wav', '.mp3', '.m4a', '.flac', '.aac', '.webm', '.ogg', '.opus'}
    file_ext = Path(file_path).suffix.lower()

    if file_ext not in audio_extensions:
        # 텍스트 파일 읽기
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    print(f"[오디오 파일 감지]: {file_path}")
    print("[먼저 전사를 진행합니다...]")

    # CLOVA Speech 클라이언트 초기화
    try:
        client = ClovaSpeechClient(
            invoke_url=os.getenv("CLOVA_SPEECH_INVOKE_URL"),
            secret_key=os.getenv("CLOVA_SPEECH_SECRET_KEY")
        )
    except Exception as e:
        print(f" CLOVA Speech 클라이언트 초기화 실패: {e}")
        return None

    # 전사 옵션 (회의에 최적화)
    options = {
        'language': 'enko',  # 한영 혼합 인식
        'completion': 'sync',
        'wordAlignment': True,
        'fullText': True,
        'enable_diarization': True,
        'enable_noise_filtering': True,
        'diarization': {
            'enable': True,
            'speakerCountMin': 2,
            'speakerCountMax': 10
        }
    }

    try:
        result = client.request_by_file(file_path, **options)
        segments = result.get('segments', [])

        if not segments:
            print(" 전사 결과에 세그먼트가 없습니다")
            return None

        print(f"[전사 완료]: {len(segments)}개 세그먼트")
        return format_segments_to_transcript(segments)

    except ClovaSpeechError as e:
        print(f" 전사 실패: {e}")
        return None


def analyze_transcript(transcript: str, analysis_types: list) -> dict:
    """대화록 AI 분석"""
    try:
        generator = ReportGenerator()
    except Exception as e:
        print(f" AI 분석기 초기화 실패: {e}")
        print("[참고] OPENAI_API_KEY를 확인해주세요.")
        return {}

    results = {}

    try:
        if 'summary' in analysis_types:
            print(f"[핵심 요약 생성 중...")
            results['summary'] = generator.summarize(transcript)

        if 'meeting_notes' in analysis_types:
            print(f"[공식 회의록 생성 중...")
            results['meeting_notes'] = generator.generate_meeting_notes(transcript)

        if 'action_items' in analysis_types:
            print(f"[액션 아이템 추출 중...")
            results['action_items'] = generator.generate_action_items(transcript)

        if 'sentiment' in analysis_types:
            print(f"[회의 분위기 분석 중...")
            results['sentiment'] = generator.analyze_sentiment(transcript)

        if 'follow_up' in analysis_types:
            print(" 후속 질문 생성 중...")
            results['follow_up'] = generator.generate_follow_up_questions(transcript)

        if 'keywords' in analysis_types:
            print("[키워드 추출 중...")
            results['keywords'] = generator.extract_keywords(transcript)

        if 'topics' in analysis_types:
            print("[주제 분류 중...")
            results['topics'] = generator.classify_topics(transcript)

        if 'by_speaker' in analysis_types:
            print("[발언자별 분석 중...")
            results['by_speaker'] = generator.analyze_by_speaker(transcript)

        if 'meeting_type' in analysis_types:
            print("[회의 유형 분류 중...")
            results['meeting_type'] = generator.classify_meeting_type(transcript)

        if 'engagement_score' in analysis_types:
            print("[참여도 점수 계산 중...")
            results['engagement_score'] = generator.calculate_engagement_score(transcript)

        if 'improvement_suggestions' in analysis_types:
            print("[개선 제안 생성 중...")
            results['improvement_suggestions'] = generator.generate_improvement_suggestions(transcript)

    except ReportGeneratorError as e:
        print(f" AI 분석 실패: {e}")
        return {}

    return results


def save_analysis_results(input_file: str, transcript: str, analysis_results: dict):
    """분석 결과 저장"""
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    base_name = Path(input_file).stem
    timestamp = Path(input_file).name  # For uniqueness

    # Markdown 형태로 통합 보고서 생성
    report_file = output_dir / f"{base_name}_analysis_report.md"

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(f"# 회의 분석 보고서\n\n")
        f.write(f"**원본 파일**: {input_file}\n")
        f.write(f"**분석 일시**: {timestamp}\n\n")
        f.write("---\n\n")

        # 각 분석 결과 작성
        if 'summary' in analysis_results:
            f.write("## 핵심 요약\n\n")
            f.write(analysis_results['summary'])
            f.write("\n\n")

        if 'meeting_notes' in analysis_results:
            f.write("## 공식 회의록\n\n")
            f.write(analysis_results['meeting_notes'])
            f.write("\n\n")

        if 'action_items' in analysis_results:
            f.write("## 액션 아이템\n\n")
            f.write(analysis_results['action_items'])
            f.write("\n\n")

        if 'sentiment' in analysis_results:
            f.write("## 회의 분위기 분석\n\n")
            f.write(analysis_results['sentiment'])
            f.write("\n\n")

        if 'follow_up' in analysis_results:
            f.write("##  후속 질문\n\n")
            f.write(analysis_results['follow_up'])
            f.write("\n\n")

        if 'keywords' in analysis_results:
            f.write("## 핵심 키워드\n\n")
            f.write(analysis_results['keywords'])
            f.write("\n\n")

        if 'topics' in analysis_results:
            f.write("## 주제 분류\n\n")
            f.write(analysis_results['topics'])
            f.write("\n\n")

        if 'by_speaker' in analysis_results:
            f.write("## 발언자별 분석\n\n")
            f.write(analysis_results['by_speaker'])
            f.write("\n\n")

        if 'meeting_type' in analysis_results:
            f.write("## 회의 유형 분류\n\n")
            f.write(analysis_results['meeting_type'])
            f.write("\n\n")

        if 'engagement_score' in analysis_results:
            f.write("## 📊 참여도 점수\n\n")
            f.write(analysis_results['engagement_score'])
            f.write("\n\n")

        if 'improvement_suggestions' in analysis_results:
            f.write("## 💡 회의 개선 제안\n\n")
            f.write(analysis_results['improvement_suggestions'])
            f.write("\n\n")

        # 원본 대화록 첨부
        f.write("---\n\n")
        f.write("## 원본 대화록\n\n")
        f.write(transcript)

    print(f"[분석 보고서 저장: {report_file}")

    # 개별 분석 결과도 JSON으로 저장
    import json
    json_file = output_dir / f"{base_name}_analysis.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump({
            'transcript': transcript,
            'analysis': analysis_results
        }, f, indent=2, ensure_ascii=False)
    print(f"[분석 데이터 저장: {json_file}")

    # 임베딩 저장 (요약이 있는 경우)
    if 'summary' in analysis_results:
        try:
            print("\n[임베딩 생성 및 저장 중...")
            generator = ReportGenerator()
            embedding_manager = EmbeddingManager()

            meeting_id = base_name
            title = Path(input_file).stem
            summary = analysis_results['summary']

            # 임베딩 생성 및 저장
            embedding = generator.generate_embedding(summary)
            embedding_manager.save_meeting_embedding(
                meeting_id=meeting_id,
                title=title,
                summary=summary,
                embedding=embedding
            )

            print(f"[OK] 임베딩 저장 완료: {meeting_id}")
        except Exception as e:
            print(f"[WARNING] 임베딩 저장 실패: {e}")


def main():
    parser = argparse.ArgumentParser(description="Meeting Analyzer - AI 기반 회의 분석 도구")
    parser.add_argument("input_file", help="분석할 파일 (텍스트 대화록 또는 오디오 파일)")

    # 분석 유형 선택
    parser.add_argument("--summary", action="store_true", help="핵심 요약 생성")
    parser.add_argument("--meeting-notes", action="store_true", help="공식 회의록 생성")
    parser.add_argument("--action-items", action="store_true", help="액션 아이템 추출")
    parser.add_argument("--sentiment", action="store_true", help="회의 분위기 분석")
    parser.add_argument("--follow-up", action="store_true", help="후속 질문 생성")
    parser.add_argument("--keywords", action="store_true", help="핵심 키워드 추출")
    parser.add_argument("--topics", action="store_true", help="주제 분류")
    parser.add_argument("--by-speaker", action="store_true", help="발언자별 분석")
    parser.add_argument("--meeting-type", action="store_true", help="회의 유형 분류")
    parser.add_argument("--engagement-score", action="store_true", help="참여도 점수화")
    parser.add_argument("--improvement-suggestions", action="store_true", help="회의 개선 제안")
    parser.add_argument("--full-analysis", action="store_true", help="전체 분석 (모든 유형)")

    args = parser.parse_args()

    # 환경설정 로드
    if not load_config():
        return 1

    # 입력 파일 확인
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f" 파일을 찾을 수 없습니다: {input_path}")
        return 1

    print("Meeting Analyzer")
    print("="*50)
    print(f"입력: {input_path}")

    # 분석 유형 결정
    analysis_types = []
    if args.full_analysis:
        analysis_types = ['summary', 'meeting_notes', 'action_items', 'sentiment', 'follow_up',
                         'keywords', 'topics', 'by_speaker', 'meeting_type',
                         'engagement_score', 'improvement_suggestions']
    else:
        if args.summary:
            analysis_types.append('summary')
        if args.meeting_notes:
            analysis_types.append('meeting_notes')
        if args.action_items:
            analysis_types.append('action_items')
        if args.sentiment:
            analysis_types.append('sentiment')
        if args.follow_up:
            analysis_types.append('follow_up')
        if args.keywords:
            analysis_types.append('keywords')
        if args.topics:
            analysis_types.append('topics')
        if args.by_speaker:
            analysis_types.append('by_speaker')
        if args.meeting_type:
            analysis_types.append('meeting_type')
        if args.engagement_score:
            analysis_types.append('engagement_score')
        if args.improvement_suggestions:
            analysis_types.append('improvement_suggestions')

    # 기본값: 요약과 액션 아이템
    if not analysis_types:
        analysis_types = ['summary', 'action_items']

    print(f"[분석 유형]: {', '.join(analysis_types)}")
    print()

    # 대화록 준비 (오디오인 경우 전사 먼저)
    transcript = transcribe_if_audio(str(input_path))
    if not transcript:
        return 1

    print(f"[대화록 길이: {len(transcript)}자")
    print()

    # AI 분석 실행
    analysis_results = analyze_transcript(transcript, analysis_types)
    if not analysis_results:
        return 1

    # 결과 저장
    save_analysis_results(str(input_path), transcript, analysis_results)

    # 결과 미리보기
    print("\n" + "="*50)
    print(f"[분석 완료")
    print("="*50)

    for analysis_type, result in analysis_results.items():
        print(f"\n[{analysis_type.upper()}]:")
        preview = result[:200] + "..." if len(result) > 200 else result
        print(preview)

    print(f"\n[INFO] 전체 분석 보고서가 'outputs/' 디렉토리에 저장되었습니다.")
    return 0


if __name__ == "__main__":
    exit(main())