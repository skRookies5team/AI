#!/usr/bin/env python3
"""회의록 의미 검색 CLI 도구

Usage:
    python search.py "예산 관련 회의"
    python search.py "마케팅 전략" --top-k 10
    python search.py "일정 조율" --save meeting_123
"""

import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.ai_analyzer import ReportGenerator, ReportGeneratorError
from src.core.embedding_manager import EmbeddingManager
from dotenv import load_dotenv


def search_meetings(query: str, top_k: int = 5):
    """회의록 검색"""
    print(f"\n[SEARCH] 검색 쿼리: \"{query}\"")
    print("=" * 60)

    try:
        # AI 생성기 초기화
        generator = ReportGenerator()
        embedding_manager = EmbeddingManager()

        # 1. 쿼리를 임베딩으로 변환
        print("\n[1/2] 쿼리 임베딩 생성 중...")
        query_embedding = generator.generate_embedding(query)
        print(f"[OK] 임베딩 생성 완료 ({len(query_embedding)}차원)")

        # 2. 유사한 회의록 검색
        print(f"\n[2/2] 유사한 회의록 검색 중 (상위 {top_k}개)...")
        results = embedding_manager.search_similar_meetings(
            query_embedding=query_embedding,
            top_k=top_k
        )

        # 3. 결과 출력
        if not results:
            print("\n[ERROR] 검색 결과가 없습니다.")
            print("   회의록을 먼저 분석하여 임베딩을 생성해주세요.")
            return

        print(f"\n[OK] {len(results)}개 결과 발견\n")
        print("=" * 60)

        for i, result in enumerate(results, 1):
            similarity_percent = result['similarity'] * 100

            print(f"\n[{i}] 유사도: {similarity_percent:.1f}%")
            print(f"    제목: {result['title']}")
            print(f"    ID: {result['meeting_id']}")
            # 요약은 ASCII로 변환 (유니코드 에러 방지)
            summary_text = result['summary'][:100] if len(result['summary']) > 100 else result['summary']
            summary_safe = summary_text.encode('ascii', 'ignore').decode('ascii')
            if summary_safe.strip():
                print(f"    요약: {summary_safe}...")
            print("-" * 60)

    except ReportGeneratorError as e:
        print(f"\n[ERROR] 오류: {e}")
        print("   .env 파일의 OPENAI_API_KEY를 확인해주세요.")
        return 1
    except Exception as e:
        print(f"\n[ERROR] 예상치 못한 오류: {e}")
        return 1

    return 0


def save_embedding(meeting_id: str, title: str, summary: str):
    """회의록 임베딩 저장"""
    print(f"\n[SAVE] 임베딩 저장 중...")
    print("=" * 60)

    try:
        generator = ReportGenerator()
        embedding_manager = EmbeddingManager()

        # 임베딩 생성
        print(f"\n[1/2] \"{title}\" 임베딩 생성 중...")
        embedding = generator.generate_embedding(summary)
        print(f"[OK] 임베딩 생성 완료 ({len(embedding)}차원)")

        # 임베딩 저장
        print(f"\n[2/2] 저장 중...")
        embedding_manager.save_meeting_embedding(
            meeting_id=meeting_id,
            title=title,
            summary=summary,
            embedding=embedding
        )

        print(f"[OK] 임베딩 저장 완료!")
        print(f"   ID: {meeting_id}")
        print(f"   제목: {title}")

    except Exception as e:
        print(f"\n[ERROR] 오류: {e}")
        return 1

    return 0


def show_stats():
    """임베딩 통계 표시"""
    print("\n[STATS] 임베딩 통계")
    print("=" * 60)

    try:
        embedding_manager = EmbeddingManager()
        stats = embedding_manager.get_stats()

        print(f"\n총 회의록 수: {stats['total_meetings']}개")
        print(f"저장 경로: {stats['storage_path']}")

        if stats['meetings']:
            print("\n저장된 회의록 목록:")
            for i, meeting in enumerate(stats['meetings'], 1):
                print(f"  {i}. [{meeting['meeting_id']}] {meeting['title']}")
        else:
            print("\n저장된 회의록이 없습니다.")

    except Exception as e:
        print(f"\n[ERROR] 오류: {e}")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="회의록 의미 검색 CLI 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 검색
  python search.py "예산 관련 회의"
  python search.py "마케팅 전략" --top-k 10

  # 임베딩 저장
  python search.py --save meeting_123 --title "예산 회의" --summary "2024년 예산 계획..."

  # 통계 확인
  python search.py --stats
        """
    )

    # 검색 옵션
    parser.add_argument("query", nargs="?", help="검색 쿼리")
    parser.add_argument("--top-k", type=int, default=5, help="반환할 결과 개수 (기본: 5)")

    # 저장 옵션
    parser.add_argument("--save", metavar="MEETING_ID", help="회의 ID (임베딩 저장 시)")
    parser.add_argument("--title", help="회의 제목 (--save와 함께 사용)")
    parser.add_argument("--summary", help="회의 요약 (--save와 함께 사용)")

    # 통계 옵션
    parser.add_argument("--stats", action="store_true", help="임베딩 통계 표시")

    args = parser.parse_args()

    # 환경설정 로드
    load_dotenv()

    # 통계 표시
    if args.stats:
        return show_stats()

    # 임베딩 저장
    if args.save:
        if not args.title or not args.summary:
            print("[ERROR] --save 옵션 사용 시 --title과 --summary가 필요합니다.")
            return 1
        return save_embedding(args.save, args.title, args.summary)

    # 검색
    if not args.query:
        parser.print_help()
        return 1

    return search_meetings(args.query, args.top_k)


if __name__ == "__main__":
    exit(main())
