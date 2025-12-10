"""임베딩 저장 및 검색 관리 (사용자별 분리 버전)"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity


class EmbeddingManager:
    """
    [수정] 사용자별로 임베딩을 분리하여 저장 및 검색을 관리하는 클래스
    """

    def __init__(self, embeddings_dir: str = "embeddings"):
        """
        EmbeddingManager 초기화

        Args:
            embeddings_dir: 임베딩 파일을 저장할 최상위 디렉토리
        """
        self.base_dir = Path(embeddings_dir)
        self.base_dir.mkdir(exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def _get_user_embedding_dir(self, user_id: str) -> Path:
        """
        [신규] 사용자별 임베딩 디렉토리 경로를 반환하고, 없으면 생성합니다.

        Args:
            user_id: 사용자 ID

        Returns:
            사용자별 임베딩 디렉토리 경로 (예: embeddings/user_123)
        """
        if not user_id:
            # user_id가 없는 경우를 대비한 안전 장치
            raise ValueError("user_id는 필수입니다.")
            
        user_dir = self.base_dir / str(user_id)
        user_dir.mkdir(exist_ok=True)
        return user_dir

    def save_meeting_embedding(
        self,
        user_id: str, # [신규] 사용자 ID
        meeting_id: str,
        title: str,
        summary: str,
        embedding: List[float],
        segments: Optional[List[Dict]] = None,
        keywords: Optional[List[str]] = None, # [신규]
        speakers: Optional[List[Dict]] = None # [신규 수정] 화자 이름 목록
    ) -> None:
        """
        [수정] 특정 사용자의 회의록 임베딩을 저장합니다.

        Args:
            user_id: 사용자 ID
            meeting_id: 회의 ID
            title: 회의 제목
            summary: 회의 요약
            embedding: 임베딩 벡터
            segments: 세그먼트별 임베딩 (선택사항)
            keywords: 키워드 리스트 (선택사항)
            speakers: 화자 이름 리스트 (선택사항) [신규 수정]
        """
        data = {
            "meeting_id": meeting_id,
            "user_id": user_id, # 식별용
            "title": title,
            "summary": summary,
            "embedding": embedding,
            "segments": segments or [],
            "keywords": keywords or [], # [신규]
            "speakers": speakers or []  # [신규 수정] 화자 이름 데이터 저장
        }
        
        # 사용자별 디렉토리 경로
        user_dir = self._get_user_embedding_dir(user_id)
        file_path = user_dir / f"meeting_{meeting_id}.json"

        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.logger.info(f"임베딩 저장 완료 (User: {user_id}): {file_path}")

    def load_all_embeddings(self, user_id: str) -> List[Dict]:
        """
        [수정] 특정 사용자의 저장된 모든 회의록 임베딩을 로드합니다.

        Args:
            user_id: 사용자 ID

        Returns:
            회의록 임베딩 데이터 리스트
        """
        embeddings = []
        user_dir = self._get_user_embedding_dir(user_id)

        for file_path in user_dir.glob("meeting_*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    embeddings.append(data)
            except Exception as e:
                self.logger.error(f"임베딩 로드 실패 ({file_path}): {e}")

        self.logger.info(f"임베딩 {len(embeddings)}개 로드 완료 (User: {user_id})")
        return embeddings

    def search_similar_meetings(
        self,
        user_id: str, # [신규] 사용자 ID
        query_embedding: List[float],
        top_k: int = 5
    ) -> List[Dict]:
        """
        [수정] 특정 사용자의 쿼리 임베딩과 유사한 회의록을 검색합니다.

        Args:
            user_id: 검색할 사용자 ID
            query_embedding: 검색 쿼리의 임베딩 벡터
            top_k: 반환할 결과 개수

        Returns:
            유사도 높은 순으로 정렬된 회의록 정보 리스트
        """
        # 해당 사용자의 임베딩만 로드
        all_embeddings = self.load_all_embeddings(user_id)

        if not all_embeddings:
            self.logger.warning(f"저장된 임베딩이 없습니다 (User: {user_id})")
            return []

        results = []

        for meeting_data in all_embeddings:
            meeting_embedding = meeting_data.get("embedding")
            if not meeting_embedding:
                continue

            # 코사인 유사도 계산
            similarity = cosine_similarity(
                [query_embedding],
                [meeting_embedding]
            )[0][0]

            results.append({
                "meeting_id": meeting_data["meeting_id"],
                "title": meeting_data["title"],
                "summary": meeting_data["summary"],
                "similarity": float(similarity)
            })

        # 유사도 높은 순으로 정렬
        results.sort(key=lambda x: x["similarity"], reverse=True)

        return results[:top_k]

    def delete_meeting_embedding(self, user_id: str, meeting_id: str) -> bool:
        """
        [수정] 특정 사용자의 회의록 임베딩을 삭제합니다.

        Args:
            user_id: 사용자 ID
            meeting_id: 삭제할 회의 ID

        Returns:
            삭제 성공 여부
        """
        user_dir = self._get_user_embedding_dir(user_id)
        file_path = user_dir / f"meeting_{meeting_id}.json"

        if file_path.exists():
            file_path.unlink()
            self.logger.info(f"임베딩 삭제 완료 (User: {user_id}): {meeting_id}")
            return True
        else:
            self.logger.warning(f"임베딩 파일 없음 (User: {user_id}): {meeting_id}")
            return False

    def get_stats(self, user_id: Optional[str] = None) -> Dict:
        """
        [수정] 저장된 임베딩 통계 정보를 반환합니다.
        (user_id가 있으면 해당 유저, 없으면 전체 통계)
        """
        if user_id:
            # 특정 사용자 통계
            all_embeddings = self.load_all_embeddings(user_id)
            return {
                "user_id": user_id,
                "total_meetings": len(all_embeddings),
                "storage_path": str(self._get_user_embedding_dir(user_id).absolute()),
                "meetings": [
                    {"meeting_id": e["meeting_id"], "title": e["title"]}
                    for e in all_embeddings
                ]
            }
        else:
            # 전체 통계 (디버깅용)
            total_count = 0
            user_dirs = [d for d in self.base_dir.iterdir() if d.is_dir()]
            for user_dir in user_dirs:
                total_count += len(list(user_dir.glob("meeting_*.json")))
            
            return {
                "scope": "global",
                "total_users_with_embeddings": len(user_dirs),
                "total_meetings_all_users": total_count,
                "storage_path": str(self.base_dir.absolute())
            }