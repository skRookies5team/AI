"""회의 전사 REST API 사용 예제

새로운 /meeting/transcribe 엔드포인트를 사용하여
회의 오디오를 전사하고 AI 요약을 받는 예제입니다.
"""

import json
import requests
from typing import Dict, Any


def test_meeting_transcribe_api():
    """회의 전사 API 테스트"""

    # API 서버 URL (로컬 서버 가정)
    base_url = "http://localhost:8000"

    # 테스트용 오디오 URL
    test_url = "https://example.com/meeting.wav"  # 실제 오디오 URL로 변경

    # 회의 전사 요청
    payload = {
        "url": test_url,
        "language": "ko-KR",
        "include_ai_summary": True,
        "meeting_title": "프로젝트 킥오프 회의",
        "speaker_count_min": 2,
        "speaker_count_max": 5
    }

    print("🚀 회의 전사 요청 중...")
    print(f"📍 URL: {test_url}")

    try:
        response = requests.post(
            f"{base_url}/meeting/transcribe",
            json=payload,
            timeout=300  # 5분 타임아웃
        )

        if response.status_code == 200:
            result = response.json()

            print("✅ 전사 완료!")
            print("="*60)

            # 기본 정보 출력
            if 'speaker_statistics' in result:
                stats = result['speaker_statistics']
                print(f"👥 인식된 화자: {len(stats)}명")
                for speaker, stat in stats.items():
                    print(f"  - 화자 {speaker}: {stat['duration_percentage']:.1f}% 발화")

            # 대화록 출력 (일부만)
            if 'transcript' in result:
                transcript = result['transcript']
                print(f"\n📝 대화록 (처음 200자):")
                print(transcript[:200] + "..." if len(transcript) > 200 else transcript)

            # AI 요약 출력
            if 'ai_reports' in result:
                ai_reports = result['ai_reports']

                if 'summary' in ai_reports:
                    print(f"\n📋 핵심 요약:")
                    print(ai_reports['summary'])

                if 'action_items' in ai_reports:
                    print(f"\n📋 액션 아이템:")
                    print(ai_reports['action_items'])

            # 전체 결과를 파일로 저장
            with open('meeting_result.json', 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"\n💾 전체 결과가 'meeting_result.json'에 저장되었습니다.")

        else:
            print(f"❌ 요청 실패: {response.status_code}")
            print(f"오류: {response.text}")

    except requests.exceptions.Timeout:
        print("⏰ 요청 시간 초과 (5분)")
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다. API 서버가 실행 중인지 확인하세요.")
    except Exception as e:
        print(f"❌ 오류 발생: {e}")


def test_transcript_format_api():
    """대화록 포맷팅 API 테스트"""

    base_url = "http://localhost:8000"

    # 테스트용 segments 데이터
    test_segments = [
        {
            "speaker": {"label": "1"},
            "text": "안녕하세요, 오늘 회의에 참석해주셔서 감사합니다.",
            "start": 1000,
            "end": 3000,
            "confidence": 0.95
        },
        {
            "speaker": {"label": "2"},
            "text": "네, 반갑습니다. 오늘 안건이 무엇인가요?",
            "start": 3500,
            "end": 5500,
            "confidence": 0.92
        }
    ]

    # 기본 포맷팅 테스트
    payload = {
        "segments": test_segments,
        "format_type": "basic"
    }

    print("\n🔄 대화록 포맷팅 테스트...")

    try:
        response = requests.post(
            f"{base_url}/transcript/format",
            json=payload
        )

        if response.status_code == 200:
            result = response.json()
            print("✅ 포맷팅 완료:")
            print(result['formatted_transcript'])
        else:
            print(f"❌ 포맷팅 실패: {response.status_code}")
            print(response.text)

    except Exception as e:
        print(f"❌ 오류 발생: {e}")


def test_summarize_api():
    """요약 API 테스트"""

    base_url = "http://localhost:8000"

    # 테스트용 대화록
    test_transcript = """
[화자 1]: 안녕하세요, 오늘 회의에 참석해주셔서 감사합니다. 새로운 프로젝트에 대해 논의하겠습니다.
[화자 2]: 네, 반갑습니다. 어떤 프로젝트인지 궁금합니다.
[화자 1]: AI 기반 회의 전사 시스템을 개발하는 프로젝트입니다. 3월까지 완료 예정입니다.
[화자 2]: 좋은 아이디어네요. 제가 UI 개발을 담당하겠습니다.
"""

    # 핵심 요약 테스트
    payload = {
        "transcript": test_transcript,
        "summary_type": "summary"
    }

    print("\n📋 AI 요약 테스트...")

    try:
        response = requests.post(
            f"{base_url}/transcript/summarize",
            json=payload
        )

        if response.status_code == 200:
            result = response.json()
            print("✅ 요약 완료:")
            print(result['result'])
        else:
            print(f"❌ 요약 실패: {response.status_code}")
            print(response.text)

    except Exception as e:
        print(f"❌ 오류 발생: {e}")


def main():
    """메인 함수"""
    print("🧪 회의 전사 API 테스트 시작")
    print("="*60)

    # 1. 회의 전사 API 테스트 (실제 오디오 URL이 필요)
    print("⚠️ 실제 테스트를 위해서는 meeting_api_example.py의 test_url을 실제 오디오 URL로 변경하세요.")
    # test_meeting_transcribe_api()

    # 2. 포맷팅 API 테스트
    test_transcript_format_api()

    # 3. 요약 API 테스트
    test_summarize_api()

    print("\n✅ 테스트 완료")


if __name__ == "__main__":
    main()