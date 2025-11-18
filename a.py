import os
from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection, utility
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PyPDF2 import PdfReader

# 1️⃣ Milvus 연결
connections.connect("default", host="127.0.0.1", port="19530")

# 2️⃣ PDF 텍스트 추출
pdf_path = r"C:\AI활용\00. 팀프로젝트\FinalProject\데이터셋\2022년_8월_식단표(직원).pdf"
reader = PdfReader(pdf_path)
text = ""
for page in reader.pages:
    text += page.extract_text() + "\n"

print(f"✅ PDF 텍스트 추출 완료, 총 {len(text)}자")

# 3️⃣ 텍스트 분할 (길이 제한 방지)
splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
chunks = splitter.split_text(text)

print(f"✅ 총 {len(chunks)}개의 텍스트 청크로 분할")

# 4️⃣ 임베딩 생성
model = SentenceTransformer("all-MiniLM-L6-v2")
embeddings = model.encode(chunks, show_progress_bar=True)

# 5️⃣ Milvus 컬렉션 정의
collection_name = "pdf_embeddings"

fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=384)
]
schema = CollectionSchema(fields, description="Company Meal Plan Data")

# 6️⃣ 컬렉션 생성 (기존 있으면 삭제 후 새로 생성)
if collection_name in utility.list_collections():
    utility.drop_collection(collection_name)

collection = Collection(name=collection_name, schema=schema)

print(f"✅ 컬렉션 '{collection_name}' 준비 완료")

# 7️⃣ 데이터 삽입
# ⚠️ id는 auto_id=True 이므로 넣지 않습니다!
data = [
    chunks,          # content 필드
    embeddings.tolist()  # embedding 필드 (numpy → list 변환)
]
collection.insert(data)

# 8️⃣ 인덱스 생성
collection.create_index(
    field_name="embedding",
    index_params={
        "metric_type": "L2",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128}
    }
)

collection.load()

print("✅ 데이터 삽입 및 인덱싱 완료")
