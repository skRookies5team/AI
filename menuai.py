import os
import re
import time
from typing import TypedDict, List, Dict
from collections import Counter
from dotenv import load_dotenv
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
import openai

# =========================
# 환경 변수 로드
# =========================
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OpenAI API 키가 없습니다. .env 파일에 OPENAI_API_KEY를 설정하세요.")

# =========================
# Milvus 연결 설정
# =========================
MILVUS_HOST = "175.120.35.228"
MILVUS_PORT = "19530"
COLLECTION_NAME = "Employee_Meal_Menu_embeddings"

# 전역 변수 (나중에 초기화)
collection = None
embedding_model = None


def initialize_connections():
    """Milvus 연결 및 모델 초기화"""
    global collection, embedding_model
    
    try:
        print("Milvus 연결 중...")
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
        collection = Collection(COLLECTION_NAME)
        collection.load()
        print(f"Milvus 연결 완료: {COLLECTION_NAME}")
    except Exception as e:
        print(f"Milvus 연결 실패: {e}")
        raise
    
    try:
        print("임베딩 모델 로딩 중...")
        embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        print("임베딩 모델 로딩 완료")
    except Exception as e:
        print(f"임베딩 모델 로딩 실패: {e}")
        raise

# =========================
# LangGraph State 정의
# =========================
class GraphState(TypedDict):
    question: str
    query_embedding: List[float]
    query_keywords: List[str]
    vector_search_results: List[Dict]
    keyword_search_results: List[Dict]
    retrieved_contexts: List[str]
    answer: str
    timings: Dict[str, float]  # 각 단계별 소요 시간


# =========================
# 키워드 추출 함수
# =========================
def extract_keywords(text: str, min_length: int = 2) -> List[str]:
    """텍스트에서 키워드 추출 (한글, 영문, 숫자)"""
    # 한글, 영문, 숫자만 추출
    pattern = r'[가-힣a-zA-Z0-9]+'
    keywords = re.findall(pattern, text.lower())
    # 최소 길이 이상인 키워드만 반환
    keywords = [kw for kw in keywords if len(kw) >= min_length]
    # 빈도수 기반으로 중요 키워드 선택 (상위 10개)
    keyword_counts = Counter(keywords)
    return [kw for kw, _ in keyword_counts.most_common(10)]


# =========================
# 노드 1: 질문 분석 (임베딩 + 키워드 추출)
# =========================
def analyze_query(state: GraphState) -> GraphState:
    """사용자 질문을 임베딩 벡터로 변환하고 키워드 추출"""
    start_time = time.time()
    question = state["question"]
    
    # 임베딩 생성
    query_embedding = embedding_model.encode([question])[0].tolist()
    
    # 키워드 추출
    query_keywords = extract_keywords(question)
    
    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["analyze_query"] = elapsed_time
    
    print(f"질문 분석 완료: {question[:50]}... ({elapsed_time:.2f}초)")
    if query_keywords:
        print(f"   추출된 키워드: {', '.join(query_keywords[:5])}")
    
    return {
        "query_embedding": query_embedding,
        "query_keywords": query_keywords,
        "timings": timings
    }


# =========================
# 키워드 점수 계산 함수
# =========================
def calculate_keyword_score(text: str, keywords: List[str]) -> float:
    """문서에서 키워드 매칭 점수 계산"""
    text_lower = text.lower()
    score = 0.0
    
    for keyword in keywords:
        # 정확한 키워드 매칭 (가중치: 2.0)
        if keyword in text_lower:
            score += 2.0
            # 키워드가 여러 번 나타나는 경우 추가 점수
            score += text_lower.count(keyword) * 0.1
        
        # 부분 매칭 (긴 키워드의 경우)
        if len(keyword) > 3:
            for i in range(len(keyword) - 2):
                substring = keyword[i:i+3]
                if substring in text_lower:
                    score += 0.1
    
    return score


# =========================
# 노드 2: 벡터 검색 (의미 기반)
# =========================
def vector_search(state: GraphState) -> GraphState:
    """Milvus에서 벡터 기반 유사 문서 검색"""
    start_time = time.time()
    query_embedding = state["query_embedding"]
    
    # Milvus에서 유사 벡터 검색 (더 많은 후보 가져오기)
    search_results = collection.search(
        data=[query_embedding],
        anns_field="embedding",
        param={"metric_type": "L2", "params": {"nprobe": 10}},
        limit=15,  # 하이브리드 검색을 위해 더 많은 후보 가져오기
        output_fields=["content"]
    )
    
    # 검색 결과를 딕셔너리 형태로 저장 (점수 포함)
    vector_results = []
    for hit in search_results[0]:
        content = hit.entity.get('content')
        # L2 거리를 점수로 변환 (거리가 작을수록 높은 점수)
        distance = hit.distance
        score = 1.0 / (1.0 + distance)  # 거리를 점수로 변환
        vector_results.append({
            "content": content,
            "score": score,
            "distance": distance,
            "source": "vector"
        })
    
    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["vector_search"] = elapsed_time
    
    print(f"벡터 검색 완료: {len(vector_results)}개 문서 ({elapsed_time:.2f}초)")
    return {
        "vector_search_results": vector_results,
        "timings": timings
    }


# =========================
# 노드 3: 키워드 검색
# =========================
def keyword_search(state: GraphState) -> GraphState:
    """키워드 기반 문서 검색"""
    start_time = time.time()
    query_keywords = state["query_keywords"]
    vector_results = state.get("vector_search_results", [])
    
    if not query_keywords or not vector_results:
        elapsed_time = time.time() - start_time
        timings = state.get("timings", {})
        timings["keyword_search"] = elapsed_time
        return {
            "keyword_search_results": [],
            "timings": timings
        }
    
    # 벡터 검색 결과에서 키워드 점수 계산
    keyword_results = []
    for result in vector_results:
        content = result["content"]
        keyword_score = calculate_keyword_score(content, query_keywords)
        
        keyword_results.append({
            "content": content,
            "score": keyword_score,
            "source": "keyword"
        })
    
    # 키워드 점수 기준으로 정렬
    keyword_results.sort(key=lambda x: x["score"], reverse=True)
    
    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["keyword_search"] = elapsed_time
    
    print(f"키워드 검색 완료: {len(keyword_results)}개 문서 ({elapsed_time:.2f}초)")
    return {
        "keyword_search_results": keyword_results,
        "timings": timings
    }


# =========================
# 노드 4: 하이브리드 검색 결과 병합 및 리랭킹
# =========================
def hybrid_merge(state: GraphState) -> GraphState:
    """벡터 검색과 키워드 검색 결과를 병합하여 최종 결과 생성"""
    start_time = time.time()
    vector_results = state.get("vector_search_results", [])
    keyword_results = state.get("keyword_search_results", [])
    
    # 결과를 content를 키로 하는 딕셔너리로 병합
    merged_results: Dict[str, Dict] = {}
    
    # 벡터 검색 결과 추가
    for result in vector_results:
        content = result["content"]
        if content not in merged_results:
            merged_results[content] = {
                "content": content,
                "vector_score": result["score"],
                "keyword_score": 0.0,
                "source": "vector"
            }
        else:
            merged_results[content]["vector_score"] = result["score"]
    
    # 키워드 검색 결과 병합
    for result in keyword_results:
        content = result["content"]
        if content not in merged_results:
            merged_results[content] = {
                "content": content,
                "vector_score": 0.0,
                "keyword_score": result["score"],
                "source": "keyword"
            }
        else:
            merged_results[content]["keyword_score"] = result["score"]
            merged_results[content]["source"] = "hybrid"
    
    # 하이브리드 점수 계산 (가중 평균)
    # 벡터 검색: 0.6, 키워드 검색: 0.4
    VECTOR_WEIGHT = 0.6
    KEYWORD_WEIGHT = 0.4
    
    # 점수 정규화 (0-1 범위로)
    if vector_results:
        max_vector_score = max(r["score"] for r in vector_results)
        min_vector_score = min(r["score"] for r in vector_results)
        vector_range = max_vector_score - min_vector_score if max_vector_score > min_vector_score else 1.0
    else:
        vector_range = 1.0
    
    if keyword_results:
        max_keyword_score = max(r["score"] for r in keyword_results)
        min_keyword_score = min(r["score"] for r in keyword_results)
        keyword_range = max_keyword_score - min_keyword_score if max_keyword_score > min_keyword_score else 1.0
    else:
        keyword_range = 1.0
    
    # 하이브리드 점수 계산
    final_results = []
    for content, result in merged_results.items():
        # 점수 정규화
        normalized_vector = (result["vector_score"] - (min_vector_score if vector_results else 0)) / vector_range if vector_range > 0 else 0
        normalized_keyword = (result["keyword_score"] - (min_keyword_score if keyword_results else 0)) / keyword_range if keyword_range > 0 else 0
        
        # 하이브리드 점수
        hybrid_score = (normalized_vector * VECTOR_WEIGHT) + (normalized_keyword * KEYWORD_WEIGHT)
        
        final_results.append({
            "content": content,
            "hybrid_score": hybrid_score,
            "vector_score": normalized_vector,
            "keyword_score": normalized_keyword,
            "source": result["source"]
        })
    
    # 하이브리드 점수 기준으로 정렬
    final_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
    
    # 상위 7개 선택 (하이브리드 검색의 장점 활용)
    top_results = final_results[:7]
    retrieved_contexts = [r["content"] for r in top_results]
    
    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["hybrid_merge"] = elapsed_time
    
    print(f"하이브리드 검색 완료: {len(retrieved_contexts)}개 문서 선택 ({elapsed_time:.2f}초)")
    print(f"   벡터 검색 결과: {len(vector_results)}개, 키워드 검색 결과: {len(keyword_results)}개")
    
    return {
        "retrieved_contexts": retrieved_contexts,
        "timings": timings
    }


# =========================
# 노드 5: GPT를 사용한 답변 생성
# =========================
def generate_answer(state: GraphState) -> GraphState:
    """검색된 컨텍스트를 바탕으로 GPT가 답변 생성"""
    start_time = time.time()
    question = state["question"]
    contexts = state["retrieved_contexts"]
    
    # 컨텍스트 합치기
    context_text = "\n\n".join([f"[문서 {i+1}]\n{ctx}" for i, ctx in enumerate(contexts)])
    
    # 프롬프트 템플릿
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", """당신은 깐부전자의 직원 식단표를 안내하는 전문 AI 어시스턴트입니다.
제공된 식단표 데이터를 기반으로 직원들의 식단 관련 질문에 대해 정확하고 상세한 답변을 제공하세요.
답변할 때는 식단표의 내용을 직접 참고하여 답변하며, 날짜, 요일, 메뉴, 칼로리 등의 정보를 명확히 안내하세요.
식단표에 없는 내용이라면, 그렇다고 명확히 말씀해주세요.
답변은 친절하고 이해하기 쉽게 작성해주세요."""),
        ("user", """다음은 깐부전자의 직원 식단표 데이터에서 검색된 관련 내용입니다:

{context}

질문: {question}

위 식단표 데이터를 참고하여 질문에 대한 상세하고 실용적인 답변을 한국어로 제공해주세요.
답변에는 가능한 한 구체적인 날짜, 요일, 메뉴 정보, 칼로리 등을 포함해주세요.""")
    ])
    
    # LLM 초기화
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0,
        api_key=api_key
    )
    
    # 체인 생성 및 실행
    chain = prompt_template | llm
    response = chain.invoke({
        "context": context_text,
        "question": question
    })
    
    answer = response.content
    elapsed_time = time.time() - start_time
    timings = state.get("timings", {})
    timings["generate_answer"] = elapsed_time
    
    print(f"답변 생성 완료 ({elapsed_time:.2f}초)")
    
    return {
        "answer": answer,
        "timings": timings
    }


# =========================
# LangGraph 그래프 구성
# =========================
def create_rag_graph():
    """하이브리드 RAG 그래프 생성"""
    workflow = StateGraph(GraphState)
    
    # 노드 추가
    workflow.add_node("analyze", analyze_query)  # 질문 분석 (임베딩 + 키워드)
    workflow.add_node("vector_search", vector_search)  # 벡터 검색
    workflow.add_node("keyword_search", keyword_search)  # 키워드 검색
    workflow.add_node("hybrid_merge", hybrid_merge)  # 하이브리드 병합
    workflow.add_node("generate", generate_answer)  # 답변 생성
    
    # 엣지 추가
    workflow.set_entry_point("analyze")
    workflow.add_edge("analyze", "vector_search")
    workflow.add_edge("vector_search", "keyword_search")  # 벡터 검색 후 키워드 검색
    workflow.add_edge("keyword_search", "hybrid_merge")  # 병합
    workflow.add_edge("hybrid_merge", "generate")  # 답변 생성
    workflow.add_edge("generate", END)
    
    return workflow.compile()


# =========================
# 메인 함수
# =========================
def main():
    """메인 실행 함수"""
    print("=" * 60)
    print("깐부전자 식단표(Menu) AI 어시스턴트")
    print("=" * 60)
    print(f"Milvus 호스트: {MILVUS_HOST}:{MILVUS_PORT}")
    print(f"컬렉션: {COLLECTION_NAME}")
    print("=" * 60)
    
    # 연결 초기화
    try:
        initialize_connections()
    except Exception as e:
        print(f"\n초기화 실패: {e}")
        print("프로그램을 종료합니다.")
        return
    
    # 그래프 생성
    app = create_rag_graph()
    
    # 대화 루프
    while True:
        print("\n" + "-" * 60)
        question = input("\n질문을 입력하세요 (종료하려면 'exit' 또는 'quit' 입력): ").strip()
        
        if question.lower() in ['exit', 'quit', '종료']:
            print("\n프로그램을 종료합니다.")
            break
        
        if not question:
            print("질문을 입력해주세요.")
            continue
        
        try:
            # 전체 시간 측정 시작
            total_start_time = time.time()
            
            # 초기 상태 설정
            initial_state = {
                "question": question,
                "query_embedding": [],
                "query_keywords": [],
                "vector_search_results": [],
                "keyword_search_results": [],
                "retrieved_contexts": [],
                "answer": "",
                "timings": {}
            }
            
            # 그래프 실행
            print("\n[처리 중...]")
            result = app.invoke(initial_state)
            
            # 전체 시간 계산
            total_elapsed_time = time.time() - total_start_time
            
            # 결과 출력
            print("\n" + "=" * 60)
            print("검색된 문서:")
            print("=" * 60)
            for i, ctx in enumerate(result["retrieved_contexts"], 1):
                print(f"\n[문서 {i}]\n{ctx[:200]}..." if len(ctx) > 200 else f"\n[문서 {i}]\n{ctx}")
            
            print("\n" + "=" * 60)
            print("AI 답변:")
            print("=" * 60)
            print(result["answer"])
            print("=" * 60)
            
            # 시간 정보 출력
            timings = result.get("timings", {})
            print("\n" + "=" * 60)
            print("처리 시간:")
            print("=" * 60)
            if timings:
                print(f"  • 질문 분석:        {timings.get('analyze_query', 0):.2f}초")
                print(f"  • 벡터 검색:        {timings.get('vector_search', 0):.2f}초")
                print(f"  • 키워드 검색:      {timings.get('keyword_search', 0):.2f}초")
                print(f"  • 하이브리드 병합:  {timings.get('hybrid_merge', 0):.2f}초")
                print(f"  • 답변 생성:        {timings.get('generate_answer', 0):.2f}초")
                print("-" * 60)
            print(f"  • 총 소요 시간:      {total_elapsed_time:.2f}초")
            print("=" * 60)
            
        except Exception as e:
            print(f"\n오류 발생: {str(e)}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()

