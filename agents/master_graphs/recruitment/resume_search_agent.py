"""이력서 검색 및 인재 추천 에이전트 - 벡터 DB 기반 검색."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional
from pathlib import Path

try:
    from pymilvus import connections, Collection, utility
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False
    connections = None
    Collection = None
    utility = None

from agents.embedding_utils import get_embedding_generator

logger = logging.getLogger(__name__)


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent.parent.parent
        env_path = project_root / ".env"

        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass


_load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """환경 변수 안전 조회."""
    return os.environ.get(name, default)


# 전역 변수: Milvus 컬렉션 및 임베딩 모델
_collection: Optional[Collection] = None
_embedding_model = None


def _initialize_milvus() -> None:
    """Milvus 연결 초기화."""
    global _collection
    
    if not MILVUS_AVAILABLE:
        logger.error("❌ pymilvus가 설치되지 않았습니다.")
        _collection = None
        return
    
    try:
        # .env에서 환경 변수 읽기 (다른 agent와 동일한 패턴)
        milvus_host = _get_env("HOST") or _get_env("MILVUS_HOST") or "175.114.229.152"
        milvus_port_str = _get_env("MILVUS_PORT") or "19530"
        milvus_port = int(milvus_port_str.strip()) if milvus_port_str else 19530
        
        print(f"🔌 [DEBUG] Milvus 연결 시도: {milvus_host}:{milvus_port}")
        logger.info(f"🔌 Milvus 연결 시도: {milvus_host}:{milvus_port}")
        
        # Milvus 연결 (이미 연결되어 있으면 재연결하지 않음)
        try:
            if not connections.has_connection("default"):
                connections.connect("default", host=milvus_host, port=milvus_port)
                print(f"✅ [DEBUG] Milvus 연결 성공: {milvus_host}:{milvus_port}")
                logger.info(f"✅ Milvus 연결 성공: {milvus_host}:{milvus_port}")
            else:
                print(f"ℹ️ [DEBUG] Milvus 이미 연결되어 있음")
                logger.info(f"ℹ️ Milvus 이미 연결되어 있음")
        except Exception as e:
            logger.error(f"❌ Milvus 연결 실패: {e}")
            print(f"❌ [DEBUG] Milvus 연결 실패: {e}")
            _collection = None
            return
        
        # 컬렉션 이름 가져오기
        collection_name = _get_env("RECRUITMENT_COLLECTION") or "hr_data_embeddings"
        print(f"📦 [DEBUG] 컬렉션 이름: {collection_name}")
        logger.info(f"📦 컬렉션 이름: {collection_name}")
        
        # 컬렉션 존재 확인 및 로드
        try:
            if utility.has_collection(collection_name):
                print(f"✅ [DEBUG] 컬렉션 '{collection_name}' 존재 확인됨")
                logger.info(f"✅ 컬렉션 '{collection_name}' 존재 확인됨")
                
                _collection = Collection(collection_name)
                _collection.load()
                
                # 컬렉션 엔티티 수 확인
                num_entities = _collection.num_entities
                print(f"✅ [DEBUG] Recruitment 컬렉션 로드 완료: {collection_name} (엔티티 수: {num_entities})")
                logger.info(f"✅ Recruitment 컬렉션 로드 완료: {collection_name} (엔티티 수: {num_entities})")
            else:
                available_collections = utility.list_collections()
                error_msg = (
                    f"⚠️ 컬렉션 '{collection_name}'이 존재하지 않습니다. "
                    f"사용 가능한 컬렉션: {', '.join(available_collections) if available_collections else '없음'}"
                )
                print(f"❌ [DEBUG] {error_msg}")
                logger.warning(error_msg)
                _collection = None
        except Exception as e:
            logger.error(f"❌ 컬렉션 로드 실패: {e}")
            print(f"❌ [DEBUG] 컬렉션 로드 실패: {e}")
            _collection = None
            
    except Exception as e:
        logger.error(f"❌ Milvus 초기화 중 오류 발생: {e}", exc_info=True)
        print(f"❌ [DEBUG] Milvus 초기화 중 오류 발생: {e}")
        _collection = None


def _initialize_embedding_model() -> None:
    """임베딩 모델 초기화."""
    global _embedding_model
    
    if _embedding_model is None:
        try:
            _embedding_model = get_embedding_generator()
            logger.info("✅ 임베딩 모델 초기화 완료")
        except Exception as e:
            logger.error(f"❌ 임베딩 모델 초기화 실패: {e}")
            _embedding_model = None


async def search_resumes(
    query: str,
    limit: int = 5,
    session_id: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    벡터 DB에서 이력서를 검색하여 인재를 추천합니다.
    
    Args:
        query: 검색 쿼리 (예: "Python 개발자 경력 5년 이상")
        limit: 반환할 최대 결과 수
        session_id: 세션 ID
        state: OrchestratorState
        
    Returns:
        {
            "message": str,  # 사용자에게 보여줄 메시지
            "resumes": List[Dict],  # 검색된 이력서 목록
            "total_count": int
        }
    """
    try:
        # Milvus 및 임베딩 모델 초기화
        _initialize_milvus()
        _initialize_embedding_model()
        
        if _collection is None:
            return {
                "message": "이력서 데이터베이스에 연결할 수 없습니다. 관리자에게 문의하세요.",
                "resumes": [],
                "total_count": 0,
                "ok": False,
            }
        
        if _embedding_model is None:
            return {
                "message": "임베딩 모델을 초기화할 수 없습니다. 관리자에게 문의하세요.",
                "resumes": [],
                "total_count": 0,
                "ok": False,
            }
        
        # 쿼리 파싱: 필터링 조건 추출
        query_lower = query.lower()
        is_different_request = any(keyword in query_lower for keyword in ["다른", "다시", "추가", "더", "another", "different", "more"])
        is_senior_only = any(keyword in query_lower for keyword in ["경력직", "경력", "시니어", "senior", "experienced"])
        is_junior_only = any(keyword in query_lower for keyword in ["신입", "주니어", "junior", "entry"])
        
        # "경력직만"과 "신입만"이 동시에 있으면 둘 다 무시
        if is_senior_only and is_junior_only:
            is_senior_only = False
            is_junior_only = False
        
        # 이전 검색 결과 가져오기 (다른 인재 요청 시 제외용)
        excluded_resume_ids = set()
        if is_different_request and state:
            recruitment_context = state.get("recruitment_context", {})
            if isinstance(recruitment_context, dict):
                resume_search = recruitment_context.get("resume_search", {})
                last_results = resume_search.get("last_results", [])
                for resume in last_results:
                    resume_id = resume.get("resume_id") or resume.get("id")
                    if resume_id:
                        excluded_resume_ids.add(str(resume_id))
                print(f"📋 [DEBUG] 이전 검색 결과 제외: {len(excluded_resume_ids)}개 인재 ID")
        
        # 쿼리 임베딩 생성
        query_embedding = await asyncio.to_thread(_embedding_model.embed, query)
        
        # 검색 파라미터
        # 다른 인재를 요청한 경우 더 많이 검색하여 필터링 후 반환
        search_limit = limit * 3 if is_different_request else limit * 2  # 필터링을 위해 더 많이 검색
        milvus_metric_type = _get_env("MILVUS_METRIC_TYPE", "L2")
        milvus_nprobe = int(_get_env("MILVUS_NPROBE", "10"))
        
        # 컬렉션 스키마 확인하여 출력 필드 결정
        schema = _collection.schema
        field_names = [field.name for field in schema.fields]
        
        # 실제 필드명 로깅 (디버깅용)
        print(f"📋 [DEBUG] 컬렉션 스키마 필드: {field_names}")
        logger.info(f"📋 컬렉션 스키마 필드: {field_names}")
        
        # 이력서 관련 필드 목록 (존재하는 필드만 포함)
        # 실제 DB 스키마에 맞는 다양한 필드명 시도
        output_fields = []
        possible_fields = [
            "resume_id", "id", 
            "name", "이름", "applicant_name", "candidate_name",
            "position", "직무", "job_title", "직책",
            "experience", "경력", "career", "경력사항",
            "skills", "기술", "skill", "기술스택", "tech_stack",
            "content", "내용", "이력서내용", "resume_content",
            "education", "학력", "학력사항"
        ]
        for field in possible_fields:
            if field in field_names and field not in output_fields:
                output_fields.append(field)
        
        # embedding 필드는 제외
        if "embedding" in output_fields:
            output_fields.remove("embedding")
        
        # 항상 모든 필드를 가져오기 (embedding 제외) - 실제 스키마 확인을 위해
        output_fields = [f for f in field_names if f != "embedding"]
        if not output_fields:
            # embedding만 있으면 embedding도 포함 (예외 상황)
            output_fields = field_names
        
        print(f"✅ [DEBUG] 사용할 출력 필드 (모두): {output_fields}")
        logger.info(f"✅ 사용할 출력 필드: {output_fields}")
        
        # 벡터 검색 실행
        search_params = {
            "metric_type": milvus_metric_type,
            "params": {"nprobe": milvus_nprobe},
        }
        
        search_results = await asyncio.to_thread(
            _collection.search,
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=search_limit,
            output_fields=output_fields if output_fields else None,
        )
        
        # 검색 결과 처리
        resumes = []
        filtered_count = 0  # 필터링된 인재 수 추적
        
        for idx, hit in enumerate(search_results[0], 1):
            resume_data = hit.entity
            
            # 실제 필드값 확인 및 로깅 (첫 번째 결과만 상세 로깅)
            if idx == 1:
                # entity의 모든 속성 확인
                entity_dict = {}
                try:
                    # 딕셔너리인 경우
                    if hasattr(resume_data, 'keys') and callable(getattr(resume_data, 'keys')):
                        entity_dict = {k: resume_data.get(k) for k in resume_data.keys()}
                        print(f"📋 [DEBUG] 첫 번째 검색 결과 - Entity 타입: dict-like")
                        print(f"📋 [DEBUG] 첫 번째 검색 결과 - Entity keys: {list(resume_data.keys())}")
                        print(f"📋 [DEBUG] 첫 번째 검색 결과 - Entity 전체 내용: {entity_dict}")
                        logger.info(f"📋 첫 번째 검색 결과 - Entity 타입: dict-like")
                        logger.info(f"📋 첫 번째 검색 결과 - Entity keys: {list(resume_data.keys())}")
                        logger.info(f"📋 첫 번째 검색 결과 - Entity 전체 내용: {entity_dict}")
                    # 객체인 경우
                    elif hasattr(resume_data, '__dict__'):
                        entity_dict = resume_data.__dict__
                        logger.info(f"📋 첫 번째 검색 결과 - Entity 타입: object with __dict__")
                        logger.info(f"📋 첫 번째 검색 결과 - Entity attributes: {list(resume_data.__dict__.keys())}")
                        logger.info(f"📋 첫 번째 검색 결과 - Entity 전체 내용: {entity_dict}")
                    # dir()로 모든 속성 확인
                    else:
                        all_attrs = [attr for attr in dir(resume_data) if not attr.startswith('_')]
                        logger.info(f"📋 첫 번째 검색 결과 - Entity 타입: {type(resume_data)}")
                        logger.info(f"📋 첫 번째 검색 결과 - Entity attributes (dir): {all_attrs}")
                        # 주요 속성값 출력
                        for attr in all_attrs[:10]:  # 처음 10개만
                            try:
                                value = getattr(resume_data, attr)
                                if not callable(value):
                                    logger.info(f"   {attr}: {value}")
                            except:
                                pass
                except Exception as e:
                    logger.error(f"❌ Entity 파싱 오류: {e}")
                    logger.info(f"📋 Entity 타입: {type(resume_data)}")
                    logger.info(f"📋 Entity repr: {repr(resume_data)}")
            
            # Milvus의 hit.entity는 딕셔너리처럼 동작하므로 .get() 메서드 직접 사용
            # 모든 필드를 딕셔너리로 변환 (실제 필드 확인용)
            entity_dict = {}
            try:
                # Milvus entity는 일반적으로 .get() 메서드를 지원
                if hasattr(resume_data, 'get'):
                    # 모든 output_fields의 값을 가져오기
                    for field in output_fields:
                        try:
                            val = resume_data.get(field)
                            if val is not None:
                                entity_dict[field] = val
                        except:
                            pass
            except Exception as e:
                logger.warning(f"⚠️ Entity 딕셔너리 변환 실패: {e}")
            
            # 첫 번째 결과에서 모든 필드 출력 (디버깅)
            if idx == 1:
                print(f"📋 [DEBUG] 첫 번째 결과 - Entity 모든 필드:")
                for key, val in entity_dict.items():
                    val_str = str(val)
                    if len(val_str) > 100:
                        val_str = val_str[:100] + "..."
                    print(f"   {key}: {val_str}")
                print(f"📋 [DEBUG] Entity 전체 내용: {entity_dict}")
            
            # resume_id 추출
            resume_id = (
                entity_dict.get("resume_id") or
                entity_dict.get("id") or
                str(hit.id) if hasattr(hit, 'id') else str(idx)
            )
            
            # 모든 필드에서 정보 추출 시도
            # name 추출
            name = (
                entity_dict.get("name") or
                entity_dict.get("이름") or
                entity_dict.get("applicant_name") or
                entity_dict.get("candidate_name") or
                entity_dict.get("person_name") or
                "이름 없음"
            )
            
            # position 추출
            position = (
                entity_dict.get("position") or
                entity_dict.get("직무") or
                entity_dict.get("job_title") or
                entity_dict.get("직책") or
                entity_dict.get("job_position") or
                "포지션 미지정"
            )
            
            # experience 추출
            experience = (
                entity_dict.get("experience") or
                entity_dict.get("경력") or
                entity_dict.get("career") or
                entity_dict.get("경력사항") or
                entity_dict.get("career_year") or
                entity_dict.get("경력년수") or
                entity_dict.get("years_of_experience") or
                "경력 정보 없음"
            )
            
            # skills 추출
            skills = (
                entity_dict.get("skills") or
                entity_dict.get("기술") or
                entity_dict.get("skill") or
                entity_dict.get("기술스택") or
                entity_dict.get("tech_stack") or
                entity_dict.get("skill_set") or
                []
            )
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.split(",") if s.strip()]
            
            # content 추출 (가장 중요 - 모든 정보가 여기에 있을 수 있음)
            content = (
                entity_dict.get("content") or
                entity_dict.get("내용") or
                entity_dict.get("이력서내용") or
                entity_dict.get("resume_content") or
                entity_dict.get("text") or
                entity_dict.get("이력서") or
                entity_dict.get("full_content") or
                ""
            )
            
            # content에서 정보 추출 (이력서 형식 파싱)
            if content:
                content_lines = content.split('\n')
                content_preview = content[:1000] if len(content) > 1000 else content
                
                # 이름 추출: "=== RESUME N ===" 다음 줄이 이름인 경우가 많음
                if name == "이름 없음":
                    # 첫 몇 줄에서 이름 추출
                    for i, line in enumerate(content_lines[:10]):
                        line = line.strip()
                        # "===" 헤더 건너뛰기
                        if "===" in line or not line:
                            continue
                        # 전화번호나 이메일이 있는 줄은 건너뛰기
                        if '@' in line or (len(re.findall(r'\d{2,4}', line)) > 2):
                            continue
                        # 이름 패턴: 한글 2-5자 또는 영문 이름 (첫 글자가 대문자)
                        # 예: "서연호", "John Doe"
                        if re.match(r'^[가-힣]{2,5}$', line):
                            name = line.strip()
                            break
                        elif re.match(r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?$', line):
                            name = line.strip()
                            break
                
                # 포지션 추출: "요약" 섹션이나 "현재 구직 중" 다음 줄에서 추출
                if position == "포지션 미지정":
                    # "요약" 섹션 찾기
                    for i, line in enumerate(content_lines):
                        if "요약" in line and i + 1 < len(content_lines):
                            next_lines = ' '.join(content_lines[i+1:i+4])
                            # "XXX 기획자", "XXX 개발자", "XXX 지망" 등 패턴
                            pos_match = re.search(r'([가-힣a-zA-Z\s]+(?:기획자|개발자|디자이너|엔지니어|매니저|관리자|전문|지망|경력|백엔드|프론트엔드|데이터|AI|인공지능))', next_lines)
                            if pos_match:
                                position = pos_match.group(1).strip()
                                # "지망" 같은 불필요한 단어 제거 및 길이 제한
                                position = re.sub(r'\s*(지망|의|로서).*$', '', position).strip()
                                if len(position) > 20:
                                    position = ' '.join(position.split()[:3])
                                break
                    
                    # "현재 구직 중" 앞뒤에서 찾기
                    if position == "포지션 미지정":
                        for i, line in enumerate(content_lines):
                            if "구직" in line or "현재" in line:
                                # 같은 줄이나 인접한 줄에서 포지션 찾기
                                search_lines = ' '.join(content_lines[max(0, i-2):i+3])
                                pos_match = re.search(r'([가-힣a-zA-Z\s]+(?:기획자|개발자|디자이너|엔지니어|매니저|관리자|전문|백엔드|프론트엔드))', search_lines)
                                if pos_match:
                                    position = pos_match.group(1).strip()
                                    position = re.sub(r'\s*(지망|의|로서).*$', '', position).strip()
                                    if len(position) > 20:
                                        position = ' '.join(position.split()[:3])
                                    break
                
                # 경력 추출: "주요 경력" 섹션에서
                if experience == "경력 정보 없음":
                    for i, line in enumerate(content_lines):
                        if "경력" in line or "career" in line.lower():
                            # "신입", "주니어", "시니어", "N년" 등의 패턴
                            exp_match = re.search(r'(신입|주니어|시니어|[\d]+년|경력\s*[\d]+년|[\d]+년\s*경력)', line, re.IGNORECASE)
                            if exp_match:
                                experience = exp_match.group(1).strip()
                                break
                            # 다음 줄 확인
                            if i + 1 < len(content_lines):
                                next_line = content_lines[i+1]
                                exp_match = re.search(r'(신입|주니어|시니어|[\d]+년|경력\s*[\d]+년)', next_line, re.IGNORECASE)
                                if exp_match:
                                    experience = exp_match.group(1).strip()
                                    break
                
                # 기술 스택 추출: "기술 스택" 섹션에서
                if not skills:
                    for i, line in enumerate(content_lines):
                        if "기술" in line or "스택" in line or "skill" in line.lower():
                            # 다음 몇 줄에서 기술 스택 추출
                            skill_lines = '\n'.join(content_lines[i:i+5])
                            # 쉼표로 구분된 기술들 추출
                            skill_match = re.findall(r'([A-Za-z0-9#+\-]+(?:\s*[A-Za-z0-9#+\-]+)*)', skill_lines)
                            if skill_match:
                                skills = [s.strip() for s in skill_match if len(s.strip()) > 1][:10]  # 최대 10개
                                break
                
                if idx == 1:
                    print(f"📋 [DEBUG] Content에서 추출한 정보:")
                    print(f"   name: {name}")
                    print(f"   position: {position}")
                    print(f"   experience: {experience}")
                    print(f"   skills: {skills}")
            
            if idx == 1:
                print(f"✅ [DEBUG] 첫 번째 인재 추출 결과:")
                print(f"   resume_id: {resume_id}")
                print(f"   name: {name}")
                print(f"   position: {position}")
                print(f"   experience: {experience}")
                print(f"   skills: {skills}")
                print(f"   content 길이: {len(content) if content else 0}")
                logger.info(f"✅ 첫 번째 인재 추출 결과:")
                logger.info(f"   resume_id: {resume_id}")
                logger.info(f"   name: {name}")
                logger.info(f"   position: {position}")
                logger.info(f"   experience: {experience}")
                logger.info(f"   skills: {skills}")
                logger.info(f"   content 길이: {len(content) if content else 0}")
            
            # 필터링: 다른 인재 요청 시 이전 결과 제외
            if is_different_request and str(resume_id) in excluded_resume_ids:
                filtered_count += 1
                continue
            
            # 필터링: 경력직만 요청
            if is_senior_only:
                # "신입"이 포함되어 있으면 제외
                experience_lower = experience.lower() if experience else ""
                if "신입" in experience_lower or "junior" in experience_lower or "entry" in experience_lower:
                    # 경력년수가 명시되지 않고 신입으로 분류된 경우 제외
                    if not re.search(r'\d+\s*년', experience) and not re.search(r'\d+\s*year', experience_lower):
                        filtered_count += 1
                        continue
            
            # 필터링: 신입만 요청
            if is_junior_only:
                experience_lower = experience.lower() if experience else ""
                # 경력년수가 있거나 "경력"이 명시적으로 포함된 경우 제외
                if re.search(r'\d+\s*년', experience) or re.search(r'\d+\s*year', experience_lower):
                    filtered_count += 1
                    continue
                # "경력직", "시니어" 등이 포함된 경우 제외
                if any(keyword in experience_lower for keyword in ["경력직", "시니어", "senior", "experienced"]):
                    filtered_count += 1
                    continue
            
            score = 1.0 / (1.0 + hit.distance) if hit.distance > 0 else 1.0
            
            resumes.append({
                "index": str(len(resumes) + 1),  # 필터링 후 번호 (1, 2, 3...)
                "resume_id": resume_id,
                "name": name,
                "position": position,
                "experience": experience,
                "skills": skills if isinstance(skills, list) else [],
                "score": round(score, 3),
                "distance": round(hit.distance, 3),
                "content": content,  # 전체 이력서 내용
            })
            
            # limit 개수만큼 수집하면 중단
            if len(resumes) >= limit:
                break
        
        # 사용자 메시지 생성 (간단한 안내 메시지)
        if resumes:
            filter_info = []
            if is_different_request:
                filter_info.append("다른 인재")
            if is_senior_only:
                filter_info.append("경력직")
            elif is_junior_only:
                filter_info.append("신입")
            
            filter_msg = f" ({', '.join(filter_info)} 필터 적용)" if filter_info else ""
            
            if filtered_count > 0:
                message = f"검색 결과 {len(resumes)}명의 인재를 찾았습니다{filter_msg}. (필터링: {filtered_count}개 제외)"
            else:
                message = f"검색 결과 {len(resumes)}명의 인재를 찾았습니다{filter_msg}. 카드를 클릭하면 상세 정보를 확인할 수 있습니다."
        else:
            filter_info = []
            if is_different_request:
                filter_info.append("다른 인재")
            if is_senior_only:
                filter_info.append("경력직")
            elif is_junior_only:
                filter_info.append("신입")
            
            filter_msg = f" ({', '.join(filter_info)} 필터)" if filter_info else ""
            message = f"'{query}' 조건{filter_msg}에 맞는 인재를 찾을 수 없습니다."
        
        return {
            "message": message,
            "resumes": resumes,
            "total_count": len(resumes),
            "ok": True,
            # 카드 형식 데이터를 위한 추가 정보
            "display_type": "cards",  # 프론트엔드에서 카드 형식으로 표시하라는 신호
        }
        
    except Exception as e:
        logger.error(f"❌ 이력서 검색 실패: {e}", exc_info=True)
        return {
            "message": f"이력서 검색 중 오류가 발생했습니다: {str(e)}",
            "resumes": [],
            "total_count": 0,
            "ok": False,
        }

