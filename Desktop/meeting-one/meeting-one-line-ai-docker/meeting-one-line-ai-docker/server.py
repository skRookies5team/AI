"""CLOVA Speech REST API 서버 (DB 연동 제거 버전)
[수정됨] App 서버 연동을 위한 /ai/analyze 엔드포인트 및 콜백 로직 추가
[수정됨] /meetings 회의록 목록 조회 엔드포인트 추가
[신규] /embeddings/** 백엔드 DB - AI 서버 임베딩 동기화 엔드포인트 추가
[수정] 2024-10-23: 모든 데이터 처리에 userId 적용 (사용자별 데이터 분리)
[수정] 2025-10-27: background_analysis_task에 추가 AI 기능(액션 아이템, 회의록, 감정 분석, 주제 분류, 후속 질문) 호출 추가
[수정] 2025-10-28: Java DTO 파싱 로직 추가 및 콜백 데이터 구조 중첩
[수정] 2025-10-27 (재요청 반영): background_analysis_task에서 meetingTitle 파라미터 받아서 사용하도록 수정
[수정] 2025-10-27 (경고 수정): on_event -> lifespan 방식으로 변경
[수정] 2025-10-27 (버그 수정): _parse_topics 함수를 단순 목록도 파싱하도록 수정
[수정] 2025-10-27 (버그 수정): AI 분석 완료 시 status가 'completed'로 정확히 설정되도록 로직 수정
"""

import os
import uuid
import asyncio
import requests # [유지] 동기 클라이언트를 위해
import httpx    # [신규] 비동기 콜백을 위해
import math
import re       # [신규] Java DTO 파싱을 위해 re(정규식) import
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path # <<< Path 객체 사용 위해 추가
from contextlib import asynccontextmanager # <<< [신규] lifespan을 위해 추가

from fastapi import (
    FastAPI, UploadFile, File, HTTPException, BackgroundTasks,
    Depends, Form, status, Query
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, Field

from dotenv import load_dotenv

# --- [DB 연동 제거] ---
# from sqlalchemy.orm import Session
# from src.core import database, models
# -----------------------

# Add src to path for imports
import sys
# from pathlib import Path # <<< 위에서 import 했으므로 중복 제거
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

# .env 로드
load_dotenv()

# --- [DB 연동 제거] ---
# models.Base.metadata.create_all(bind=database.engine) # DB 테이블 생성 제거
# -----------------------


# <<< [신규] lifespan 함수 정의 >>>
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 서버 시작 시 실행될 코드 (구 startup) ---
    print(f"CLOVA Speech STT API server starting... (v{app.version} - Lifespan)")
    print(f"Invoke URL: {INVOKE_URL}")
    if APP_SERVER_CALLBACK_HOST:
        print(f"[CALLBACK] App Server Host: {APP_SERVER_CALLBACK_HOST}")
    else:
        print("[CALLBACK] ⚠️ APP_SERVER_CALLBACK_HOST가 .env에 설정되지 않았습니다. /ai/analyze 콜백이 작동하지 않습니다.")
    # --- startup 끝 ---
    
    yield # 이 지점에서 애플리케이션이 실행됩니다.
    
    # --- 서버 종료 시 실행될 코드 (구 shutdown) ---
    print("Server shutting down...")
    temp_dir = Path("temp")
    if temp_dir.exists():
        for temp_file in temp_dir.glob("*"):
            temp_file.unlink(missing_ok=True)
        try:
            temp_dir.rmdir()
        except OSError: pass
    print("Cleanup completed")
    # --- shutdown 끝 ---


# FastAPI 앱 생성 (lifespan 적용)
app = FastAPI(
    title="CLOVA Speech STT API",
    description="NAVER Cloud Platform STT API 서버 (사용자별 데이터 분리 적용)",
    version="2.3.3", # [수정] 버전 업데이트 (lifespan, topics_fix, status_fix)
    lifespan=lifespan  # <<< [수정] lifespan 함수 연결
)

# 글로벌 설정
INVOKE_URL = os.getenv('CLOVA_SPEECH_INVOKE_URL')
SECRET_KEY = os.getenv('CLOVA_SPEECH_SECRET_KEY')
APP_SERVER_CALLBACK_HOST = os.getenv('APP_SERVER_CALLBACK_HOST')

if not INVOKE_URL or not SECRET_KEY:
    raise ValueError("CLOVA_SPEECH_INVOKE_URL과 CLOVA_SPEECH_SECRET_KEY를 .env에 설정해주세요")

# 클라이언트 인스턴스
client = ClovaSpeechClient(INVOKE_URL, SECRET_KEY)

# AI 보고서 생성기 (OpenAI 키가 있는 경우에만)
try:
    report_generator = ReportGenerator()
except Exception as e:
    print(f"⚠️ ReportGenerator 초기화 실패: {e}. AI 분석 기능이 제한될 수 있습니다.")
    report_generator = None

# 임베딩 관리자
embedding_manager = EmbeddingManager(embeddings_dir="embeddings")

# 작업 상태 저장소 (실제 환경에서는 Redis 등 사용)
job_store: Dict[str, Dict[str, Any]] = {}


# --- [DB 연동 제거] ---
# def get_db(): ... 함수 제거
# -----------------------


class URLRequest(BaseModel):
    """URL 방식 요청 모델"""
    url: HttpUrl
    language: str = "enko"
    completion: str = "sync"
    word_alignment: bool = True
    full_text: bool = True
    callback: Optional[str] = None
    userdata: Optional[str] = None
    enable_diarization: bool = True
    enable_noise_filtering: bool = True
    enable_sed: bool = False
    speaker_count_min: int = 2
    speaker_count_max: int = 10


class MeetingRequest(BaseModel):
    """회의 전사 요청 모델"""
    url: HttpUrl
    language: str = "enko"
    include_ai_summary: bool = True
    meeting_title: Optional[str] = None
    speaker_count_min: int = 2
    speaker_count_max: int = 10


class TranscriptFormatRequest(BaseModel):
    """대화록 포맷팅 요청 모델"""
    segments: list
    format_type: str = "basic"
    include_timestamps: bool = False
    include_confidence: bool = False
    speaker_names: Optional[Dict[str, str]] = None


class SummaryRequest(BaseModel):
    """요약 요청 모델"""
    transcript: str
    summary_type: str = "summary"


class SemanticSearchRequest(BaseModel):
    """
    [수정] 의미 검색 요청 모델
    """
    query: str
    userId: str # [신규] 사용자 ID
    top_k: int = 5


class JobResponse(BaseModel):
    """작업 응답 모델"""
    job_id: str
    status: str
    created_at: str
    message: Optional[str] = None

# ========================================
# App 서버 연동을 위한 Pydantic 모델
# ========================================

class AiAnalyzeRequest(BaseModel):
    """
    [수정] AI 분석 요청 모델 (App Server -> AI Server)
    API 문서 3.2 기반 + userId 추가 + meetingTitle 추가
    """
    meetingId: str
    filePath: str # 예: /data/uploads/meeting_123.wav
    userId: str   # [신규] App 서버가 인증을 통해 알아낸 사용자 ID
    meetingTitle: Optional[str] = None # <<< [신규 추가] 사용자가 입력한 원본 제목

class AiAnalyzeResponse(BaseModel):
    """AI 분석 요청 즉시 응답 모델"""
    status: str

# ========================================
# 3.4 회의록 목록 조회를 위한 Pydantic 모델
# ========================================

class MeetingSpeakerItem(BaseModel):
    """[신규] 목록에 표시할 간단한 화자 정보 모델"""
    speakerId: str
    name: Optional[str] = None

class MeetingListItem(BaseModel):
    """[수정] 회의록 목록의 개별 항목 모델"""
    meetingId: str
    title: str
    status: str
    summary: Optional[str] = None
    createdAt: str
    speakers: List[MeetingSpeakerItem] = [] # [신규 수정] 화자 목록

class MeetingListResponse(BaseModel):
    """회의록 목록 페이징 응답 모델"""
    content: List[MeetingListItem]
    page: int
    size: int
    totalPages: int

# ========================================
# 3.5 임베딩 동기화를 위한 Pydantic 모델
# ========================================

class SpeakerSegmentUpsert(BaseModel):
    """
    [신규] /embeddings/upsert에서 받을 segments 모델 (파싱용)
    App 서버가 보내는 데이터를 받기 위함이며, 실제 저장되지는 않음.
    """
    start: Optional[float] = None
    end: Optional[float] = None
    text: Optional[str] = None

class SpeakerUpsertData(BaseModel):
    """
    [신규] /embeddings/upsert 요청 시 받을 speaker 객체 모델
    App 서버가 보내는 'segments'를 포함하여 파싱합니다.
    """
    speakerId: str
    name: Optional[str] = None
    segments: Optional[List[SpeakerSegmentUpsert]] = None # segments를 받도록 허용

class EmbeddingUpsertRequest(BaseModel):
    """
    [수정] 임베딩 생성/수정 요청 모델
    """
    meetingId: str
    userId: str
    title: str
    summary: str
    keywords: List[str]
    # [수정] App 서버가 보내는 speaker 구조(SpeakerUpsertData)를 받도록 수정
    speakers: Optional[List[SpeakerUpsertData]] = Field(default_factory=list)


class EmbeddingSyncResponse(BaseModel):
    """임베딩 동기화 응답 모델"""
    meetingId: str
    userId: str # [신규] 사용자 ID
    status: str
    message: Optional[str] = None


# ========================================

@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "service": "CLOVA Speech STT API",
        "version": app.version, # <<< FastAPI 앱 버전 사용
        "endpoints": [
            "/stt/url",
            "/stt/file",
            "/stt/status/{job_id}",
            "/meeting/transcribe",
            "/transcript/format",
            "/transcript/summarize",
            "/transcript/statistics",
            "/upload_and_analyze",
            "[User] /ai/analyze",
            "[User] /meetings",
            "[User] /search/semantic",
            "[SYNC] /embeddings/upsert",
            "[SYNC] /embeddings/{meeting_id}?userId=...",
            "[SYNC] /embeddings/status?userId=...",
        ]
    }


@app.get("/health")
async def health():
    """헬스체크"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ========================================
# [신규] Java DTO 파싱 헬퍼 함수 3개
# ========================================

def _parse_action_items(text: str) -> List[Dict[str, Any]]:
    """
    Java DTO의 List<ActionItem> 형식에 맞게 AI 텍스트를 파싱합니다.
    예상 입력:
        [담당자1] 작업내용1
        - [담당자2] 작업내용2
    """
    items = []
    if not text:
        return items

    # 정규식: "[담당자] 작업내용" 또는 "- [담당자] 작업내용"
    # 그룹 1: 담당자 (name)
    # 그룹 2: 작업내용 (content)
    pattern = re.compile(r"^\s*[\-•*]?\s*\[([^\]]+)\]\s*(.*)", re.MULTILINE)

    for i, match in enumerate(pattern.finditer(text)):
        name = match.group(1).strip()
        content = match.group(2).strip()

        if not name or not content:
            continue

        items.append({
            "name": name,
            "content": content,
            "orderIndex": i
        })

    # 정규식으로 파싱이 안 된 경우 (예: " - 작업내용 (담당: 없음)")
    if not items and text.strip():
        for i, line in enumerate(text.strip().split('\n')):
            line = line.strip().lstrip('-•* ')
            if not line:
                continue
            items.append({
                "name": "미지정", # 기본값
                "content": line,
                "orderIndex": i
            })

    return items

# <<< [수정] _parse_topics 함수를 단순화된 버전으로 교체 >>>
def _parse_topics(text: str) -> List[Dict[str, Any]]:
    """
    [수정] Java DTO의 List<Topic> 형식에 맞게 AI 텍스트를 파싱합니다.
    (AI가 복잡한 형식을 따르지 않는 경우를 대비한 단순화 버전)
    """
    topics = []
    if not text:
        return topics

    # **주요 주제 분류** 섹션이 있다면 그 아랫부분만 사용
    section_text = text
    section_match = re.search(r"\*\*주요 주제 분류\*\*([\s\S]*)", text, re.MULTILINE | re.IGNORECASE)
    if section_match:
        section_text = section_match.group(1)

    # 정규식: (-, *, •, 1.) 등으로 시작하는 목록 항목
    # 그룹 1: 주제명 (title)
    pattern = re.compile(r"^\s*[\-•*]?\s*\d*\.?\s*\[?([^\]\n:]+)\]?", re.MULTILINE)

    for match in pattern.finditer(section_text):
        title = match.group(1).strip()
        
        # 너무 짧거나 불필요한 단어/헤더 필터링
        if not title or len(title) < 3 or title.lower().startswith("중요도") or title.lower().startswith("논의 내용 요약") or title.lower().startswith("전체 대화에서"):
            continue

        topics.append({
            "title": title,
            "importance": "중간", # AI가 생성하지 않으므로 기본값
            "summary": f"{title} 관련 논의", # AI가 생성하지 않으므로 기본값
            "proportion": 0 # AI가 생성하지 않으므로 기본값
        })
    
    return topics

def _parse_follow_up_questions(text: str) -> List[Dict[str, Any]]:
    """
    Java DTO의 List<FollowUpCategory> 형식에 맞게 AI 텍스트를 파싱합니다.
    AI가 카테고리 없이 질문 목록만 반환하므로, '주요 후속 질문' 단일 카테고리로 묶습니다.

    예상 입력:
        - 후속 질문 1
        - 후속 질문 2
    """
    questions = []
    if not text:
        return []

    # 질문 목록 파싱
    pattern = re.compile(r"^\s*[\-•*]\s*(.*)", re.MULTILINE)

    for i, match in enumerate(pattern.finditer(text)):
        question_text = match.group(1).strip().strip('?') + '?'
        if not question_text:
            continue

        questions.append({
            "question": question_text,
            "orderIndex": i
        })

    if not questions:
        return []

    # Java DTO 구조에 맞게 단일 카테고리로 래핑
    default_category = {
        "category": "주요 후속 질문", # DTO의 FollowUpCategory.category
        "questions": questions      # DTO의 FollowUpCategory.questions
    }

    return [default_category]


# ========================================
# App 서버 연동을 위한 헬퍼 함수 및 백그라운드 작업
# ========================================

def format_clova_to_app_speakers(segments: list) -> list:
    """CLOVA STT segments를 App 서버 콜백('speakers') 형식으로 변환합니다."""
    # (기존 코드와 동일 - 변경 없음)
    speakers_dict = {}

    if not segments:
        return []

    for segment in segments:
        try:
            speaker_label = segment.get("speaker", {}).get("label", "Unknown")
            speaker_id = f"S{speaker_label}"

            if speaker_id not in speakers_dict:
                speakers_dict[speaker_id] = {"speakerId": speaker_id, "segments": []}

            start_sec = segment.get("start", 0) / 1000.0
            end_sec = segment.get("end", 0) / 1000.0

            speakers_dict[speaker_id]["segments"].append({
                "start": round(start_sec, 2),
                "end": round(end_sec, 2),
                "text": segment.get("text", "").strip()
            })
        except Exception as e:
            print(f"세그먼트 포맷팅 중 오류: {e} (세그먼트: {segment})")

    for speaker_data in speakers_dict.values():
        speaker_data["segments"].sort(key=lambda x: x["start"])

    return list(speakers_dict.values())


# [수정] 비동기 작업 + Java DTO 파싱 및 데이터 중첩 + meetingTitle 사용
async def background_analysis_task(meeting_id: str, file_path: str, user_id: str, meeting_title: Optional[str] = None): # <<< meeting_title 추가
    """
    [수정] 비동기 백그라운드 분석 작업 + Java DTO 파싱 및 데이터 중첩 + meetingTitle 사용
    """
    print(f"[Task {meeting_id}] AI 분석 작업 시작 (User: {user_id}): {file_path}")
    # [신규] 전달받은 제목 로그 추가
    if meeting_title:
        print(f"[Task {meeting_id}] 전달받은 회의 제목: {meeting_title}")
    else:
        print(f"[Task {meeting_id}] 전달받은 회의 제목 없음, 파일명 기반으로 생성 예정.")

    # App 서버로 전송할 콜백 데이터 (기본값 설정)
    callback_data = {
        "status": "failed", # 기본값 'failed'
        "summary": None,
        "keywords": [],
        "speakers": [],
        # --- [신규] Java DTO 구조에 맞춘 'feedback' 필드 ---
        "feedback": {
            "actionItems": [],
            "topics": [],
            "followUpCategories": []
        },
        # --- [유지] DTO에 없는 추가 분석 결과 (최상위) ---
        "meetingNotes": None,
        "sentiment": None,
        # ---------------------------
        "error": None
    }
    segments = [] # <<< finally 블록에서 사용하기 위해 초기화

    try:
        # 0. 파일 경로 유효성 검사 (AI 서버 로컬 경로)
        local_path = Path(file_path) # <<< Path 객체로 변환
        if not local_path.exists():
            raise FileNotFoundError(f"AI 서버에서 해당 파일을 찾을 수 없습니다: {file_path}")

        # 1. STT + 화자 분리 (CLOVA Speech 사용)
        print(f"[Task {meeting_id}] 1. STT(Clova) 및 화자 분리 시작...")
        stt_options = {
            'language': 'enko', 'completion': 'sync', 'wordAlignment': True,
            'fullText': True, 'enable_diarization': True,
            'diarization': {'enable': True, 'speakerCountMin': 2, 'speakerCountMax': 10}
        }
        stt_result = await asyncio.to_thread(
            client.request_by_file,
            local_path, # <<< Path 객체 사용
            **stt_options
        )
        if 'segments' not in stt_result or not stt_result['segments']:
            raise ValueError("STT 실패: Clova 결과에 'segments'가 없습니다.")
        segments = stt_result.get('segments', []) # <<< segments 변수 할당
        print(f"[Task {meeting_id}] 2. STT 완료 (세그먼트 {len(segments)}개)")

        # 2. 대화록(flat text) 변환 (AI 분석용)
        transcript = format_segments_to_transcript(segments)
        if not transcript:
             print(f"[Task {meeting_id}] ⚠️ 대화록이 비어있어 AI 분석을 건너뜁니다.")
             callback_data["speakers"] = format_clova_to_app_speakers(segments)
             callback_data["status"] = "completed_no_transcript"
             if "error" in callback_data: callback_data.pop("error")
             # <<< 여기서 return하지 않고 finally 블록에서 콜백 전송하도록 변경
             # return # <<< 제거


        # 4. 화자 데이터 포맷팅 (App 서버 요구사항)
        print(f"[Task {meeting_id}] 4. 화자 데이터 포맷팅...")
        callback_data["speakers"] = format_clova_to_app_speakers(segments)

        # [신규] 4.5. 임베딩 저장용 '화자 이름' 목록 생성 (segments 제외)
        speaker_name_data = [
            {"speakerId": s["speakerId"], "name": None}
            for s in callback_data["speakers"]
        ]

        # 3. AI 분석 (요약 + 키워드 + 추가 기능들 + 후속 질문) - transcript가 있을 때만 실행
        if transcript and report_generator:
            analysis_tasks = []

            # --- 기존 분석 ---
            print(f"[Task {meeting_id}] 3a. AI 요약(LangChain) 시작...")
            analysis_tasks.append(asyncio.to_thread(report_generator.summarize, transcript))

            print(f"[Task {meeting_id}] 3b. AI 키워드 추출(LangChain) 시작...")
            analysis_tasks.append(asyncio.to_thread(report_generator.extract_keywords, transcript))

            # --- [신규] 추가 분석 ---
            print(f"[Task {meeting_id}] 3c. AI 액션 아이템 추출(LangChain) 시작...")
            analysis_tasks.append(asyncio.to_thread(report_generator.generate_action_items, transcript))

            print(f"[Task {meeting_id}] 3d. AI 회의록 생성(LangChain) 시작...")
            analysis_tasks.append(asyncio.to_thread(report_generator.generate_meeting_notes, transcript))

            print(f"[Task {meeting_id}] 3e. AI 감정 분석(LangChain) 시작...")
            analysis_tasks.append(asyncio.to_thread(report_generator.analyze_sentiment, transcript))

            print(f"[Task {meeting_id}] 3f. AI 주제 분류(LangChain) 시작...")
            analysis_tasks.append(asyncio.to_thread(report_generator.classify_topics, transcript))

            # --- <<< [신규] 후속 질문 분석 추가 ---
            print(f"[Task {meeting_id}] 3g. AI 후속 질문 생성(LangChain) 시작...")
            analysis_tasks.append(asyncio.to_thread(report_generator.generate_follow_up_questions, transcript))
            # ----------------------------------

            # 병렬 실행 (결과는 순서대로 리스트에 담김)
            print(f"[Task {meeting_id}] 3h. AI 분석 병렬 실행...")
            results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

            # --- [수정] 결과 매핑 및 파싱 ---

            # 3-1. (최상위) 요약 (Summary)
            if isinstance(results[0], Exception):
                print(f"[Task {meeting_id}] ⚠️ 요약 생성 실패: {results[0]}")
            else:
                callback_data["summary"] = results[0]

            # 3-2. (최상위) 키워드 (Keywords)
            if isinstance(results[1], Exception):
                print(f"[Task {meeting_id}] ⚠️ 키워드 추출 실패: {results[1]}")
            else:
                keyword_text = results[1]
                raw_keywords = [line.strip().lstrip('-•* ').strip() for line in keyword_text.split('\n') if line.strip().lstrip('-•* ').strip()]
                filtered_keywords = [
                    kw for kw in raw_keywords
                    if not kw.endswith(':')
                    and len(kw) <= 30
                    and (kw.count('.') + kw.count(',')) < 2
                ]
                unique_keywords = list(dict.fromkeys(filtered_keywords))
                callback_data["keywords"] = unique_keywords[:10]

            # 3-3. (Feedback) 액션 아이템 (ActionItems)
            if isinstance(results[2], Exception):
                print(f"[Task {meeting_id}] ⚠️ 액션 아이템 추출 실패: {results[2]}")
            else:
                print(f"[Task {meeting_id}] 🌀 (파싱) 액션 아이템 파싱 시도...")
                callback_data["feedback"]["actionItems"] = _parse_action_items(results[2])

            # 3-4. (최상위) 회의록 (MeetingNotes)
            if isinstance(results[3], Exception):
                print(f"[Task {meeting_id}] ⚠️ 회의록 생성 실패: {results[3]}")
            else:
                callback_data["meetingNotes"] = results[3]

            # 3-5. (최상위) 감정 분석 (Sentiment)
            if isinstance(results[4], Exception):
                print(f"[Task {meeting_id}] ⚠️ 감정 분석 실패: {results[4]}")
            else:
                callback_data["sentiment"] = results[4]

            # 3-6. (Feedback) 주제 분류 (Topics)
            if isinstance(results[5], Exception):
                print(f"[Task {meeting_id}] ⚠️ 주제 분류 실패: {results[5]}")
            else:
                # <<< 로그 추가 시작 >>>
                ai_topics_output = results[5]
                print(f"[Task {meeting_id}] 🌀 (파싱 전) AI Topics 출력:\n---\n{ai_topics_output}\n---")
                print(f"[Task {meeting_id}] 🌀 (파싱 시도) 주제 분류 파싱 시도...")
                callback_data["feedback"]["topics"] = _parse_topics(ai_topics_output) # <<< 수정된 _parse_topics 사용
                print(f"[Task {meeting_id}] 🌀 (파싱 후) Topics 결과: {callback_data['feedback']['topics']}")
                # <<< 로그 추가 끝 >>>


            # 3-7. (Feedback) 후속 질문 (FollowUpQuestions)
            if isinstance(results[6], Exception):
                print(f"[Task {meeting_id}] ⚠️ 후속 질문 생성 실패: {results[6]}")
            else:
                # <<< 로그 추가 시작 >>>
                ai_followup_output = results[6]
                print(f"[Task {meeting_id}] 🌀 (파싱 전) AI Follow-up 출력:\n---\n{ai_followup_output}\n---")
                print(f"[Task {meeting_id}] 🌀 (파싱 시도) 후속 질문 파싱 시도...")
                callback_data["feedback"]["followUpCategories"] = _parse_follow_up_questions(ai_followup_output)
                print(f"[Task {meeting_id}] 🌀 (파싱 후) Follow-up 결과: {callback_data['feedback']['followUpCategories']}")
                # <<< 로그 추가 끝 >>>


            # --- [파싱 완료] ---

            # 5. 임베딩 저장 (요약이 성공했을 경우에만 시도)
            if callback_data["summary"]:
                try:
                    print(f"[Task {meeting_id}] 5. 임베딩 저장 시작 (User: {user_id})")
                    embedding_vector = await asyncio.to_thread(
                        report_generator.generate_embedding,
                        callback_data["summary"]
                    )
                    # <<< 제목 결정 로직 수정 >>>
                    title_to_save = meeting_title if meeting_title else local_path.stem # <<< meeting_title 우선 사용
                    print(f"[Task {meeting_id}] 5. 저장될 제목: {title_to_save}")

                    await asyncio.to_thread(
                        embedding_manager.save_meeting_embedding,
                        user_id=user_id,
                        meeting_id=meeting_id,
                        title=title_to_save, # <<< 수정된 제목 사용
                        summary=callback_data["summary"],
                        embedding=embedding_vector,
                        keywords=callback_data["keywords"],
                        speakers=speaker_name_data
                    )
                    print(f"[Task {meeting_id}] 5. 임베딩 저장 완료: {meeting_id}")
                except Exception as e:
                    print(f"[Task {meeting_id}] ⚠️ 5. 임베딩 저장 실패: {e}") # <<< 임베딩 저장 실패는 경고로 처리
            else:
                 print(f"[Task {meeting_id}] ⚠️ 요약이 없어 임베딩 저장을 건너뜁니다.")

        elif not report_generator:
            print(f"[Task {meeting_id}] ⚠️ AI 분석기(ReportGenerator)가 없어 AI 분석을 건너뜁니다.")

        # <<< [수정] status 'completed' 설정 로직 수정 >>>
        # transcript(대화록)가 존재하고, 이전에 'completed_no_transcript'로 설정되지 않았다면
        # (임베딩 저장 실패(⚠️)는 무시하고) 분석 작업은 완료된 것으로 간주합니다.
        if transcript and callback_data["status"] != "completed_no_transcript":
            callback_data["status"] = "completed"
            # 'failed'가 초기값이었을 수 있으므로 'error' 필드 제거
            if "error" in callback_data:
                callback_data.pop("error")
        
        # (transcript가 없는 경우는 이미 'completed_no_transcript'로 설정되었음)
        # (만약 이전에 심각한 오류(❌)가 발생했다면 status는 'failed'로 유지됨)

    except Exception as e:
        print(f"[Task {meeting_id}] ❌ 분석 중 심각한 오류 발생: {e}")
        callback_data["status"] = "failed" # 명시적으로 failed 설정
        callback_data["error"] = str(e)
        # 필요한 경우, speakers 정보라도 채워넣기 (오류 발생 전에 segments가 생성되었다면)
        if not callback_data["speakers"] and segments: # <<< 'segments in locals()' 제거
            try:
                callback_data["speakers"] = format_clova_to_app_speakers(segments)
            except Exception as format_e:
                 print(f"[Task {meeting_id}] ⚠️ 오류 발생 후 speakers 포맷팅 실패: {format_e}")
    finally:
        # 6. App 서버로 콜백 전송 (성공/실패/일부 성공 모두 전송 시도)
        if not APP_SERVER_CALLBACK_HOST:
            print(f"[Task {meeting_id}] ⚠️ .env에 APP_SERVER_CALLBACK_HOST가 설정되지 않아 콜백을 보낼 수 없습니다.")
            # return # <<< 콜백 불가 시 여기서 종료하지 않음 (로깅은 남김)
        else:
            callback_url = f"{APP_SERVER_CALLBACK_HOST}/api/meetings/{meeting_id}/callback"
            try:
                async with httpx.AsyncClient() as async_client:
                    print(f"[Task {meeting_id}] 6. App 서버로 콜백 전송: {callback_url}")
                    # import json # <<< 디버깅 시 필요
                    # print(f"[DEBUG] 콜백 데이터: {json.dumps(callback_data, indent=2, ensure_ascii=False)}") # 디버깅용
                    response = await async_client.post(callback_url, json=callback_data, timeout=30) # <<< 타임아웃 증가
                    response.raise_for_status()
                    print(f"[Task {meeting_id}] 7. 콜백 전송 성공 (App 서버 응답: {response.status_code})")
            except httpx.RequestError as e:
                print(f"[Task {meeting_id}] ❌ 7. 콜백 전송 실패 (HTTPX 오류): {e}")
            except Exception as e:
                print(f"[Task {meeting_id}] ❌ 7. 콜백 전송 실패 (일반 오류): {e}")


# ========================================
# (기존 엔드포인트 - 대부분 변경 없음)
# ========================================

@app.post("/stt/url")
async def transcribe_url(request: URLRequest, background_tasks: BackgroundTasks):
    """URL 방식 음성 인식 (kwak 버전 기본값 enko 적용)"""
    # (기존 코드와 동일 - 변경 없음)
    try:
        options = {
            'language': request.language, 'completion': request.completion,
            'wordAlignment': request.word_alignment, 'fullText': request.full_text,
            'enable_diarization': request.enable_diarization,
            'enable_noise_filtering': request.enable_noise_filtering,
            'enable_sed': request.enable_sed
        }
        if request.enable_diarization:
            options['diarization'] = {
                'enable': True, 'speakerCountMin': request.speaker_count_min,
                'speakerCountMax': request.speaker_count_max
            }
        if request.callback: options['callback'] = request.callback
        if request.userdata: options['userdata'] = request.userdata

        result = client.request_by_url(str(request.url), **options)

        if request.completion == 'sync':
            return JSONResponse(content=result)
        else:
            job_id = result if isinstance(result, str) else str(uuid.uuid4())
            job_store[job_id] = {
                'status': 'processing', 'created_at': datetime.now().isoformat(),
                'type': 'url', 'url': str(request.url), 'options': options, 'result': None
            }
            background_tasks.add_task(poll_result, job_id)
            return JobResponse(job_id=job_id, status='processing', created_at=job_store[job_id]['created_at'])
    except ClovaSpeechError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"내부 서버 오류: {e}")


@app.post("/stt/file")
async def transcribe_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: str = Form("enko"),
    completion: str = Form("sync"),
    word_alignment: bool = Form(True),
    full_text: bool = Form(True),
    callback: Optional[str] = Form(None),
    userdata: Optional[str] = Form(None),
    enable_diarization: bool = Form(True),
    enable_noise_filtering: bool = Form(True),
    enable_sed: bool = Form(False),
    speaker_count_min: int = Form(2),
    speaker_count_max: int = Form(10)
):
    """파일 업로드 방식 음성 인식 (kwak 버전 기본값 enko 적용)"""
    # (기존 코드와 동일 - 변경 없음)
    temp_file = None
    try:
        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)
        temp_file = temp_dir / f"{uuid.uuid4()}_{file.filename}"

        content = await file.read()
        with open(temp_file, "wb") as f:
            f.write(content)

        options = {
            'language': language, 'completion': completion, 'wordAlignment': word_alignment,
            'fullText': full_text, 'enable_diarization': enable_diarization,
            'enable_noise_filtering': enable_noise_filtering, 'enable_sed': enable_sed
        }
        if enable_diarization:
            options['diarization'] = {'enable': True, 'speakerCountMin': speaker_count_min, 'speakerCountMax': speaker_count_max}
        if callback: options['callback'] = callback
        if userdata: options['userdata'] = userdata

        result = await asyncio.to_thread(
            client.request_by_file,
            temp_file,
            **options
        )

        if completion == 'sync':
            # temp_file.unlink(missing_ok=True) # <<< finally에서 처리하므로 주석 처리
            return JSONResponse(content=result)
        else:
            job_id = result if isinstance(result, str) else str(uuid.uuid4())
            job_store[job_id] = {
                'status': 'processing', 'created_at': datetime.now().isoformat(),
                'type': 'file', 'filename': file.filename, 'temp_file': str(temp_file),
                'options': options, 'result': None
            }
            background_tasks.add_task(poll_result, job_id)
            return JobResponse(job_id=job_id, status='processing', created_at=job_store[job_id]['created_at'])

    except ClovaSpeechError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"내부 서버 오류: {e}")
    finally:
        # <<< finally 블록으로 파일 삭제 이동 (sync 모드에서도 오류 발생 시 삭제되도록)
        if temp_file and temp_file.exists():
             temp_file.unlink(missing_ok=True)


@app.get("/stt/status/{job_id}")
async def get_job_status(job_id: str):
    """작업 상태 조회"""
    # (기존 코드와 동일 - 변경 없음)
    if job_id not in job_store:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
    job_info = job_store[job_id]
    if job_info['status'] == 'completed' and job_info['result']:
        return JSONResponse(content=job_info['result'])
    elif job_info['status'] == 'failed':
        return JobResponse(job_id=job_id, status='failed', created_at=job_info['created_at'], message=job_info.get('error'))
    else:
        return JobResponse(job_id=job_id, status=job_info['status'], created_at=job_info['created_at'])


async def poll_result(job_id: str):
    """[수정] 백그라운드에서 비동기 작업 결과 폴링 (비차단)"""
    # (기존 코드와 동일 - 변경 없음)
    temp_file_path = None # <<< finally 블록에서 사용하기 위해 초기화
    try:
        result = await asyncio.to_thread(
            client.wait_for_completion,
            job_id,
            poll_interval=2,
            max_wait=300
        )
        job_store[job_id]['status'] = 'completed'
        job_store[job_id]['result'] = result
    except Exception as e:
        job_store[job_id]['status'] = 'failed'
        job_store[job_id]['error'] = str(e)
    finally:
        # <<< async 모드의 파일 삭제 로직
        if job_id in job_store and job_store[job_id]['type'] == 'file':
            temp_file_path = job_store[job_id].get('temp_file')
            if temp_file_path and Path(temp_file_path).exists():
                Path(temp_file_path).unlink(missing_ok=True)


# <<< [삭제] @app.on_event("startup") 함수 삭제 >>>
# <<< [삭제] @app.on_event("shutdown") 함수 삭제 >>>


@app.post("/meeting/transcribe")
async def transcribe_meeting(request: MeetingRequest, background_tasks: BackgroundTasks):
    """
    [수정] 회의 오디오 전사 및 AI 요약 (비차단, **모든 AI 분석 포함**)
    (이 엔드포인트는 /ai/analyze와 달리 userId를 받지 않으므로 임베딩 저장은 하지 않음)
    """
    try:
        options = {
            'language': request.language, 'completion': 'sync', 'wordAlignment': True,
            'fullText': True, 'enable_diarization': True, 'enable_noise_filtering': True,
            'diarization': {'enable': True, 'speakerCountMin': request.speaker_count_min, 'speakerCountMax': request.speaker_count_max}
        }

        stt_result = await asyncio.to_thread(
            client.request_by_url,
            str(request.url),
            **options
        )

        if 'segments' not in stt_result:
            raise HTTPException(status_code=400, detail="STT 결과에 segments가 없습니다")

        segments = stt_result['segments']
        transcript = format_segments_to_transcript(segments)
        detailed_transcript = format_segments_to_detailed_transcript(segments, include_timestamps=True, include_confidence=True)
        speaker_stats = extract_speaker_statistics(segments)
        response_data = {
            'stt_result': stt_result, 'transcript': transcript, 'detailed_transcript': detailed_transcript,
            'speaker_statistics': speaker_stats, 'meeting_header': generate_meeting_summary_header(segments, request.meeting_title)
        }

        if request.include_ai_summary and report_generator and transcript:
            print(f"[/meeting/transcribe] 모든 AI 분석 병렬 실행 시작...")
            analysis_tasks = [
                asyncio.to_thread(report_generator.summarize, transcript),
                asyncio.to_thread(report_generator.generate_meeting_notes, transcript),
                asyncio.to_thread(report_generator.generate_action_items, transcript),
                asyncio.to_thread(report_generator.analyze_sentiment, transcript),
                asyncio.to_thread(report_generator.generate_follow_up_questions, transcript), # <<< 후속 질문 추가됨
                asyncio.to_thread(report_generator.extract_keywords, transcript),
                asyncio.to_thread(report_generator.classify_topics, transcript),
                asyncio.to_thread(report_generator.analyze_by_speaker, transcript),
                asyncio.to_thread(report_generator.classify_meeting_type, transcript),
                asyncio.to_thread(report_generator.summarize_by_speaker, transcript),
                asyncio.to_thread(report_generator.calculate_engagement_score, transcript),
                asyncio.to_thread(report_generator.generate_improvement_suggestions, transcript)
            ]

            results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

            # 결과 매핑 (오류 시 에러 메시지 포함) - <<< 인덱스 유지됨 (follow_up_questions가 중간에 추가)
            ai_reports = {
                'summary': results[0] if not isinstance(results[0], Exception) else f"Error: {results[0]}",
                'meeting_notes': results[1] if not isinstance(results[1], Exception) else f"Error: {results[1]}",
                'action_items': results[2] if not isinstance(results[2], Exception) else f"Error: {results[2]}",
                'sentiment': results[3] if not isinstance(results[3], Exception) else f"Error: {results[3]}",
                'follow_up_questions': results[4] if not isinstance(results[4], Exception) else f"Error: {results[4]}", # <<< 추가됨
                'keywords': results[5] if not isinstance(results[5], Exception) else f"Error: {results[5]}",
                'topics': results[6] if not isinstance(results[6], Exception) else f"Error: {results[6]}",
                'by_speaker': results[7] if not isinstance(results[7], Exception) else f"Error: {results[7]}",
                'meeting_type': results[8] if not isinstance(results[8], Exception) else f"Error: {results[8]}",
                'speaker_summary': results[9] if not isinstance(results[9], Exception) else f"Error: {results[9]}",
                'engagement_score': results[10] if not isinstance(results[10], Exception) else f"Error: {results[10]}",
                'improvement_suggestions': results[11] if not isinstance(results[11], Exception) else f"Error: {results[11]}"
            }
            response_data['ai_reports'] = ai_reports
            print(f"[/meeting/transcribe] 모든 AI 분석 완료.")
        elif not transcript:
             response_data['ai_reports_error'] = "대화록이 비어있어 AI 분석을 건너뛰었습니다."
        elif not report_generator:
             response_data['ai_reports_error'] = "AI 분석기(ReportGenerator)가 설정되지 않았습니다."


        return JSONResponse(content=response_data)

    except ClovaSpeechError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"내부 서버 오류: {e}")


@app.post("/transcript/format")
async def format_transcript(request: TranscriptFormatRequest):
    """STT segments 포맷팅 (DB 무관)"""
    # (기존 코드와 동일 - 변경 없음)
    try:
        segments = request.segments
        if request.format_type == "basic":
            result = format_segments_to_transcript(segments)
        elif request.format_type == "detailed":
            result = format_segments_to_detailed_transcript(segments, include_timestamps=request.include_timestamps, include_confidence=request.include_confidence)
        elif request.format_type == "with_speakers" and request.speaker_names:
            from src.core.formatter import format_transcript_with_speakers
            result = format_transcript_with_speakers(segments, request.speaker_names)
        else:
            raise HTTPException(status_code=400, detail="지원하지 않는 format_type입니다")
        return JSONResponse(content={"formatted_transcript": result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"포맷팅 오류: {e}")


@app.post("/transcript/summarize")
async def summarize_transcript(request: SummaryRequest):
    """
    [수정] 대화록 AI 요약 및 분석 (비차단) - 모든 분석 타입 지원
    """
    if not report_generator:
        raise HTTPException(status_code=503, detail="AI 요약 기능을 사용할 수 없습니다. OPENAI_API_KEY를 설정해주세요.")

    try:
        transcript = request.transcript
        summary_type = request.summary_type

        # 모든 분석 함수 매핑 (순서 유지)
        call_map = {
            "summary": report_generator.summarize,
            "meeting_notes": report_generator.generate_meeting_notes,
            "action_items": report_generator.generate_action_items,
            "sentiment": report_generator.analyze_sentiment,
            "follow_up": report_generator.generate_follow_up_questions, # <<< 'follow_up' 키로 호출
            "keywords": report_generator.extract_keywords,
            "topics": report_generator.classify_topics,
            "by_speaker": report_generator.analyze_by_speaker,
            "meeting_type": report_generator.classify_meeting_type,
            "speaker_summary": report_generator.summarize_by_speaker,
            "engagement_score": report_generator.calculate_engagement_score,
            "improvement_suggestions": report_generator.generate_improvement_suggestions,
        }

        func_to_call = call_map.get(summary_type)
        if not func_to_call:
             supported_types = list(call_map.keys())
             raise HTTPException(status_code=400,
                                 detail=f"지원하지 않는 summary_type입니다. 사용 가능한 타입: {', '.join(supported_types)}")

        result = await asyncio.to_thread(func_to_call, transcript)

        return JSONResponse(content={"summary_type": summary_type, "result": result})

    except ReportGeneratorError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"요약 생성 오류: {e}")


@app.post("/transcript/statistics")
async def get_transcript_statistics(segments: list):
    """대화록 통계 정보 추출 (DB 무관)"""
    # (기존 코드와 동일 - 변경 없음)
    try:
        stats = extract_speaker_statistics(segments)
        header = generate_meeting_summary_header(segments)
        return JSONResponse(content={"speaker_statistics": stats, "meeting_header": header})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"통계 추출 오류: {e}")


@app.post("/upload_and_analyze")
async def upload_and_analyze(
    file: UploadFile = File(...),
    language: str = Form("enko"),
    speaker_count_min: int = Form(2),
    speaker_count_max: int = Form(10)
):
    """
    [수정] 파일 업로드 + STT + 전체 AI 분석 (비차단, **모든 AI 분석 포함**)
    (이 엔드포인트는 사용자 분리 로직이 없습니다.)
    """
    if not report_generator:
        raise HTTPException(status_code=503, detail="AI 요약 기능을 사용할 수 없습니다. OPENAI_API_KEY를 설정해주세요.")

    temp_file = None
    try:
        # 1. 파일 임시 저장
        temp_dir = Path("temp")
        temp_dir.mkdir(exist_ok=True)
        temp_file = temp_dir / f"{uuid.uuid4()}_{file.filename}"
        content = await file.read()
        with open(temp_file, "wb") as f:
            f.write(content)

        # 2. STT 옵션 구성 및 실행
        options = {
            'language': language, 'completion': 'sync', 'wordAlignment': True, 'fullText': True,
            'enable_diarization': True, 'enable_noise_filtering': True,
            'diarization': {'enable': True, 'speakerCountMin': speaker_count_min, 'speakerCountMax': speaker_count_max}
        }
        stt_result = await asyncio.to_thread(
            client.request_by_file,
            temp_file,
            **options
        )

    except ClovaSpeechError as e:
        # if temp_file and temp_file.exists(): temp_file.unlink(missing_ok=True) # <<< finally에서 처리
        raise HTTPException(status_code=400, detail=f"STT 오류: {e}")
    except Exception as e:
        # if temp_file and temp_file.exists(): temp_file.unlink(missing_ok=True) # <<< finally에서 처리
        raise HTTPException(status_code=500, detail=f"파일 처리 또는 STT 오류: {e}")
    finally:
        # <<< finally 블록으로 파일 삭제 이동
        if temp_file and temp_file.exists():
            temp_file.unlink(missing_ok=True)

    if 'segments' not in stt_result:
        raise HTTPException(status_code=400, detail="STT 결과에 segments가 없습니다")

    segments = stt_result.get('segments', [])
    transcript = format_segments_to_transcript(segments)
    speaker_stats = extract_speaker_statistics(segments)

    ai_reports = None
    ai_reports_error = None

    # 3. AI 전체 분석 실행 (병렬)
    if transcript:
        try:
            print(f"[/upload_and_analyze] 모든 AI 분석 병렬 실행 시작...")
            analysis_tasks = [ # 모든 분석 함수 호출 (후속 질문 포함)
                asyncio.to_thread(report_generator.summarize, transcript),
                asyncio.to_thread(report_generator.generate_meeting_notes, transcript),
                asyncio.to_thread(report_generator.generate_action_items, transcript),
                asyncio.to_thread(report_generator.analyze_sentiment, transcript),
                asyncio.to_thread(report_generator.generate_follow_up_questions, transcript), # <<< 추가됨
                asyncio.to_thread(report_generator.extract_keywords, transcript),
                asyncio.to_thread(report_generator.classify_topics, transcript),
                asyncio.to_thread(report_generator.analyze_by_speaker, transcript),
                asyncio.to_thread(report_generator.classify_meeting_type, transcript),
                asyncio.to_thread(report_generator.summarize_by_speaker, transcript),
                asyncio.to_thread(report_generator.calculate_engagement_score, transcript),
                asyncio.to_thread(report_generator.generate_improvement_suggestions, transcript)
            ]
            results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

            ai_reports = { # 결과 매핑 (인덱스 유지)
                'summary': results[0] if not isinstance(results[0], Exception) else f"Error: {results[0]}",
                'meeting_notes': results[1] if not isinstance(results[1], Exception) else f"Error: {results[1]}",
                'action_items': results[2] if not isinstance(results[2], Exception) else f"Error: {results[2]}",
                'sentiment': results[3] if not isinstance(results[3], Exception) else f"Error: {results[3]}",
                'follow_up_questions': results[4] if not isinstance(results[4], Exception) else f"Error: {results[4]}", # <<< 추가됨
                'keywords': results[5] if not isinstance(results[5], Exception) else f"Error: {results[5]}",
                'topics': results[6] if not isinstance(results[6], Exception) else f"Error: {results[6]}",
                'by_speaker': results[7] if not isinstance(results[7], Exception) else f"Error: {results[7]}",
                'meeting_type': results[8] if not isinstance(results[8], Exception) else f"Error: {results[8]}",
                'speaker_summary': results[9] if not isinstance(results[9], Exception) else f"Error: {results[9]}",
                'engagement_score': results[10] if not isinstance(results[10], Exception) else f"Error: {results[10]}",
                'improvement_suggestions': results[11] if not isinstance(results[11], Exception) else f"Error: {results[11]}"
            }
            print(f"[/upload_and_analyze] 모든 AI 분석 완료.")
        except Exception as e:
            ai_reports_error = f"AI 분석 중 오류 발생: {str(e)}"
    else:
        ai_reports_error = "대화록이 비어있어 AI 분석을 건너뛰었습니다."


    # 5. 통합 결과 반환
    response_data = {
        "filename": file.filename,
        "transcript": transcript,
        "speaker_statistics": speaker_stats,
        "ai_reports": ai_reports,
        "ai_reports_error": ai_reports_error,
        "stt_raw_result": stt_result
    }

    return JSONResponse(content=response_data)

# ========================================
# 임베딩 검색 API (사용자 분리 적용)
# ========================================

@app.post("/search/semantic")
async def semantic_search(request: SemanticSearchRequest):
    """
    [수정] 의미 기반 회의록 검색 (비차단)
    """
    # (기존 코드와 동일 - 변경 없음)
    if not report_generator:
        raise HTTPException(status_code=500, detail="OpenAI API 키가 설정되지 않았습니다")

    try:
        query_embedding = await asyncio.to_thread(
            report_generator.generate_embedding,
            request.query
        )
        results = await asyncio.to_thread(
            embedding_manager.search_similar_meetings,
            user_id=request.userId,
            query_embedding=query_embedding,
            top_k=request.top_k
        )
        return {
            "query": request.query,
            "userId": request.userId,
            "total_results": len(results),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"검색 실패: {str(e)}")


# ========================================
# App 서버 연동 엔드포인트 (사용자 분리 적용)
# ========================================

@app.post("/ai/analyze",
          response_model=AiAnalyzeResponse,
          status_code=200)
async def request_ai_analysis(
    request: AiAnalyzeRequest,
    background_tasks: BackgroundTasks
):
    """
    [수정] App 서버로부터 AI 분석을 요청받습니다. (API 3.2) + 추가 AI 기능 호출 + 후속 질문 추가 + meetingTitle 전달
    """
    print(f"✅ AI 분석 요청 수신: {request.meetingId} (User: {request.userId})")

    # BackgroundTasks에 수정된 background_analysis_task 등록
    background_tasks.add_task(
        background_analysis_task, # 수정된 함수 사용
        request.meetingId,
        request.filePath,
        request.userId,
        request.meetingTitle # <<< meetingTitle 전달
    )

    return AiAnalyzeResponse(status="processing")


# ========================================
# 3.4 회의록 목록 검색 조회 (사용자 분리 적용)
# ========================================

@app.get("/meetings", response_model=MeetingListResponse)
async def get_meeting_list(
    userId: str = Query(..., description="조회할 사용자의 ID"), # [신규]
    page: int = 1,
    size: int = 10,
    keyword: Optional[str] = None,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    status: Optional[str] = None
):
    """
    [수정] 특정 사용자의 저장된 회의록 목록을 페이지 단위로 조회합니다. (비차단)
    (API 3.4와 유사하나, 이 API는 AI 서버의 임베딩 파일 기준)
    """
    # (기존 코드와 동일 - 변경 없음)
    try:
        all_meetings_data = await asyncio.to_thread(
            embedding_manager.load_all_embeddings,
            userId
        )

        enriched_meetings = []
        for meeting_data in all_meetings_data:
            meeting_id = meeting_data.get("meeting_id")
            if not meeting_id: continue

            user_dir = embedding_manager._get_user_embedding_dir(userId)
            file_path = user_dir / f"meeting_{meeting_id}.json"
            created_at_iso = datetime.now().isoformat()

            if file_path.exists():
                try:
                    mtime = file_path.stat().st_mtime
                    created_at_iso = datetime.fromtimestamp(mtime).isoformat()
                except Exception as e:
                    print(f"파일 시간 읽기 오류: {meeting_id} - {e}")

            saved_speakers = meeting_data.get("speakers", [])

            enriched_meetings.append({
                "meetingId": meeting_id,
                "title": meeting_data.get("title", ""),
                "summary": meeting_data.get("summary", ""),
                "status": "COMPLETED", # 임베딩 파일은 항상 완료 상태
                "createdAt": created_at_iso,
                "speakers": saved_speakers # 저장된 화자 정보 사용
            })

        filtered_meetings = enriched_meetings
        if keyword:
            kw = keyword.lower()
            filtered_meetings = [m for m in filtered_meetings
                                 if kw in m['title'].lower()
                                 or kw in m['summary'].lower()
                                 or any(kw in (s.get('name') or '').lower() for s in m['speakers'])
                                ]
        if title:
            filtered_meetings = [m for m in filtered_meetings if title.lower() in m['title'].lower()]
        if summary:
            filtered_meetings = [m for m in filtered_meetings if summary.lower() in m['summary'].lower()]
        if status:
            # 임베딩은 항상 completed 상태이므로, 다른 상태 필터링 시 결과 없음
            if status.lower() != "completed":
                 filtered_meetings = []
            # else: completed는 필터링 불필요

        filtered_meetings.sort(key=lambda m: m['createdAt'], reverse=True)

        total_items = len(filtered_meetings)
        total_pages = math.ceil(total_items / size) if size > 0 else 0
        if page < 1: page = 1
        start_index = (page - 1) * size
        end_index = start_index + size
        paginated_content = filtered_meetings[start_index:end_index]

        return MeetingListResponse(
            content=paginated_content,
            page=page,
            size=len(paginated_content), # 현재 페이지의 실제 아이템 수
            totalPages=total_pages
        )

    except Exception as e:
        print(f"❌ /meetings 엔드포인트 오류: {e}")
        raise HTTPException(status_code=500, detail=f"회의록 목록 조회 중 오류 발생: {e}")


# ========================================
# 3.5 임베딩 동기화 API 엔드포인트 (사용자 분리 적용)
# ========================================

@app.post("/embeddings/upsert",
          response_model=EmbeddingSyncResponse,
          status_code=status.HTTP_201_CREATED)
async def upsert_embedding(request: EmbeddingUpsertRequest):
    """
    [수정] 임베딩 생성 또는 수정 (Upsert) (비차단)
    """
    # (기존 코드와 동일 - 변경 없음)
    print(f"🔄 [SYNC] 임베딩 Upsert 요청: {request.meetingId} (User: {request.userId})")

    if not report_generator:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="OpenAI API 키가 설정되지 않아 임베딩을 생성할 수 없습니다.")

    try:
        print(f"  - 1/2. 임베딩 생성 중...")
        embedding_vector = await asyncio.to_thread(
            report_generator.generate_embedding,
            request.summary
        )

        speaker_data_to_save = []
        if request.speakers:
            for speaker in request.speakers:
                speaker_data_to_save.append({
                    "speakerId": speaker.speakerId,
                    "name": speaker.name
                })

        print(f"  - 2/2. 임베딩 파일 저장 중 (화자 {len(speaker_data_to_save)}명 정보 포함)...")
        await asyncio.to_thread(
            embedding_manager.save_meeting_embedding,
            user_id=request.userId,
            meeting_id=request.meetingId,
            title=request.title,
            summary=request.summary,
            embedding=embedding_vector,
            keywords=request.keywords,
            speakers=speaker_data_to_save
        )

        print(f"  - ✅ [SYNC] Upsert 완료: {request.meetingId}")
        return EmbeddingSyncResponse(
            meetingId=request.meetingId,
            userId=request.userId,
            status="synchronized",
            message="임베딩이 성공적으로 생성/수정되었습니다."
        )

    except ReportGeneratorError as e:
        print(f"  - ❌ [SYNC] Upsert 실패 (OpenAI 오류): {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        print(f"  - ❌ [SYNC] Upsert 실패 (서버 오류): {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"임베딩 저장 실패: {e}")


@app.delete("/embeddings/{meeting_id}",
            response_model=EmbeddingSyncResponse,
            status_code=status.HTTP_200_OK)
async def delete_embedding(
    meeting_id: str,
    userId: str = Query(..., description="삭제할 사용자의 ID")
):
    """
    [수정] 임베딩 삭제 (비차단)
    """
    # (기존 코드와 동일 - 변경 없음)
    print(f"🗑️ [SYNC] 임베딩 Delete 요청: {meeting_id} (User: {userId})")

    try:
        success = await asyncio.to_thread(
            embedding_manager.delete_meeting_embedding,
            userId,
            meeting_id
        )

        if success:
            print(f"  - ✅ [SYNC] Delete 완료: {meeting_id}")
            return EmbeddingSyncResponse(
                meetingId=meeting_id,
                userId=userId,
                status="deleted",
                message="임베딩이 성공적으로 삭제되었습니다."
            )
        else:
            print(f"  - ⚠️ [SYNC] Delete 실패 (파일 없음): {meeting_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="삭제할 임베딩 파일을 찾을 수 없습니다.")

    except Exception as e:
        print(f"  - ❌ [SYNC] Delete 실패 (서버 오류): {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"임베딩 삭제 실패: {e}")


@app.get("/embeddings/status", status_code=status.HTTP_200_OK)
async def get_embedding_stats(
    userId: Optional[str] = Query(None, description="통계를 조회할 사용자 ID (없으면 전역)")
):
    """
    [수정] 현재 저장된 임베딩 통계 조회 (디버깅용, 비차단)
    """
    # (기존 코드와 동일 - 변경 없음)
    try:
        stats = await asyncio.to_thread(
            embedding_manager.get_stats,
            userId
        )
        return stats
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"통계 조회 실패: {e}")

# ========================================

if __name__ == "__main__":
    import uvicorn
    # 로그 레벨을 DEBUG로 설정하여 상세 정보 확인 가능
    # uvicorn.run(app, host="0.0.0.0", port=8000, log_level="debug")
    # <<< [수정] reload=True 제거 (uvicorn server:app --reload 방식 권장) >>>
    uvicorn.run(app, host="0.0.0.0", port=8000)