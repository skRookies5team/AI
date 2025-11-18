import os
from dotenv import load_dotenv
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer
import openai

# =========================
# 0️⃣ .env 파일에서 OPENAI_API_KEY 로드
# =========================
load_dotenv()  # 현재 디렉토리의 .env 파일 자동 로드
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OpenAI API 키가 없습니다. .env 파일에 OPENAI_API_KEY를 설정하세요.")
openai.api_key = api_key

# =========================
# 1️⃣ Milvus 연결
# =========================
connections.connect("default", host="127.0.0.1", port="19530")
collection_name = "pdf_embeddings"
collection = Collection(collection_name)

# =========================
# 2️⃣ 임베딩 모델 초기화
# =========================
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

# =========================
# 3️⃣ 사용자 질문 반복 입력
# =========================
while True:
    query_text = input("\n질문을 입력하세요 (종료하려면 'exit' 입력): ")
    if query_text.lower() == "exit":
        print("프로그램 종료")
        break

    # =========================
    # 4️⃣ 질문 임베딩 생성
    # =========================
    query_vector = embedding_model.encode([query_text])[0].tolist()

    # =========================
    # 5️⃣ Milvus에서 유사 벡터 검색
    # =========================
    search_results = collection.search(
        data=[query_vector],
        anns_field="embedding",
        param={"metric_type": "L2", "params": {"nprobe": 10}},
        limit=3,  # 상위 3개 청크
        output_fields=["content"]
    )

    # =========================
    # 6️⃣ 검색 결과 합치기
    # =========================
    retrieved_texts = [hit.entity.get('content') for hit in search_results[0]]
    context = "\n".join(retrieved_texts)

    # =========================
    # 7️⃣ RAG용 GPT 프롬프트 작성
    # =========================
    prompt = f"""
아래 정보를 참고해서 질문에 답해주세요.

정보:
{context}

질문: {query_text}
"""

    # =========================
    # 8️⃣ 최신 OpenAI Chat API 호출
    # =========================
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )

    answer = response.choices[0].message.content

    # =========================
    # 9️⃣ 결과 출력
    # =========================
    print("\n🔹 검색 결과 청크:\n", context)
    print("\n🔹 GPT 답변:\n", answer)
