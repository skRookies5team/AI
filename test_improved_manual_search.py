"""개선된 메뉴얼 검색 Agent 테스트 스크립트

menualai.py와 동일한 고도화된 질문으로 개선된 Agent를 테스트합니다.
run_manual_search 함수를 사용하여 LangGraph 기반 파이프라인을 테스트합니다.
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from manual_search import ManualSearchConfig, run_manual_search, Embedder, DocumentRetriever, AnswerGenerator

load_dotenv()


def test_improved_agent():
    """개선된 Agent 테스트"""
    print("=" * 60)
    print("개선된 메뉴얼 검색 Agent 테스트")
    print("=" * 60)
    
    # 설정 로드
    try:
        config = ManualSearchConfig.from_env()
        print(f"✅ 설정 로드 완료")
        print(f"   Milvus 호스트: {config.milvus.host}:{config.milvus.port}")
        print(f"   컬렉션: {config.milvus.collection_name}")
        print(f"   임베딩 모델: {config.embedding.openai_model}")
        print(f"   하이브리드 검색: {'활성화' if config.milvus.hybrid_search and config.milvus.hybrid_search.enabled else '비활성화'}")
        if config.milvus.hybrid_search and config.milvus.hybrid_search.enabled:
            print(f"   벡터 가중치: {config.milvus.hybrid_search.vector_weight}")
            print(f"   키워드 가중치: {config.milvus.hybrid_search.keyword_weight}")
    except Exception as e:
        print(f"❌ 설정 로드 실패: {e}")
        return
    
    print("=" * 60)
    
    # 컴포넌트 초기화
    try:
        print("\n컴포넌트 초기화 중...")
        embedder = Embedder(
            api_key=config.embedding.api_key or config.llm.api_key,
            model=config.embedding.openai_model,
        )
        print("✅ Embedder 초기화 완료")
        
        retriever = DocumentRetriever(config.milvus)
        print("✅ DocumentRetriever 초기화 완료")
        
        generator = AnswerGenerator(config.llm)
        print("✅ AnswerGenerator 초기화 완료")
    except Exception as e:
        print(f"❌ 컴포넌트 초기화 실패: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 테스트 질문들 (menualai.py와 동일한 고도화된 질문)
    test_questions = [
        #"인사 정책에 대해 알려주세요",
        #"연차 휴가를 사용하려면 어떻게 해야 하나요?",
        #"나는 연차 휴가 며칠 쓸 수 있어?",
        #"다리를 다쳐서 병가를 쓰고 싶은데 며칠 쓸 수 있어?",
        "병가 신청 절차는 어떻게 되나요?",
        #"출근 시간은 몇 시인가요?",
        #"퇴직금은 언제 받을 수 있나요?",
        #"교육 훈련 프로그램에 참여하려면 어떻게 해야 하나요?",
        #'내가 6개월 근무했는데 그럼 연차 휴가를 며칠 받을 수 있어?',
        #'내가 1년 미만으로 근속했는데 그럼 연차 휴가를 며칠 받을 수 있어?',
        #'내가 1년 미만으로 근무했는데 그럼 유급 휴가를 며칠 받을 수 있어?',
        #'회사 이름이 뭐야?'
    ]
    
    print("\n" + "=" * 60)
    print("테스트 시작")
    print("=" * 60)
    
    for idx, question in enumerate(test_questions, 1):
        print(f"\n{'='*60}")
        print(f"테스트 {idx}/{len(test_questions)}")
        print(f"{'='*60}")
        print(f"질문: {question}")
        print("-" * 60)
        
        try:
            # 전체 시간 측정 시작
            total_start_time = time.time()
            
            # run_manual_search를 사용하여 검색 및 답변 생성
            print("\n[처리 중...]")
            result = run_manual_search(
                query=question,
                embedder=embedder,
                retriever=retriever,
                answer_generator=generator,
                top_k=config.milvus.top_k,
            )
            
            # 전체 시간 계산
            total_time = time.time() - total_start_time
            
            # 결과 출력
            print("\n" + "-" * 60)
            print("📋 검색된 문서:")
            print("-" * 60)
            for i, source in enumerate(result.sources[:3], 1):  # 상위 3개만 표시
                preview = source.document[:150] + "..." if len(source.document) > 150 else source.document
                score = source.score if source.score is not None else 0.0
                
                # 메타데이터에서 파일명 추출
                metadata = source.metadata or {}
                filename = metadata.get("filename") or metadata.get("file_name") or metadata.get("source") or "알 수 없음"
                file_path = metadata.get("file_path") or metadata.get("path") or ""
                
                print(f"\n[문서 {i}] (점수: {score:.4f})")
                if filename != "알 수 없음" or file_path:
                    file_info = file_path if file_path else filename
                    print(f"📄 파일: {file_info}")
                print(f"📝 내용: {preview}")
            
            print("\n" + "-" * 60)
            print("💬 AI 답변:")
            print("-" * 60)
            print(result.answer.text)
            print("-" * 60)
            
            # 시간 정보 출력
            print("\n⏱️  처리 시간:")
            print(f"  • 총 소요 시간:   {total_time:.2f}초")
            print(f"  • 검색된 문서 수: {len(result.sources)}개")
            
            # 모든 검색된 문서의 파일명 목록 출력
            print("\n📁 검색에 사용된 파일 목록:")
            print("-" * 60)
            file_list = []
            for source in result.sources:
                metadata = source.metadata or {}
                filename = (
                    metadata.get("filename") or 
                    metadata.get("file_name") or 
                    metadata.get("source") or 
                    metadata.get("file_path") or 
                    metadata.get("path") or 
                    "알 수 없음"
                )
                if filename not in file_list:
                    file_list.append(filename)
            
            if file_list:
                for idx, filename in enumerate(file_list, 1):
                    print(f"  {idx}. {filename}")
            else:
                print("  (파일명 정보 없음)")
            print("=" * 60)
            
        except Exception as e:
            print(f"\n❌ 오류 발생: {str(e)}")
            import traceback
            traceback.print_exc()
            print("=" * 60)
            continue
    
    # 리소스 정리
    try:
        retriever.close()
        print("\n✅ 리소스 정리 완료")
    except Exception as e:
        print(f"\n⚠️ 리소스 정리 중 오류: {e}")
    
    print("\n" + "=" * 60)
    print("테스트 완료")
    print("=" * 60)


if __name__ == "__main__":
    test_improved_agent()

