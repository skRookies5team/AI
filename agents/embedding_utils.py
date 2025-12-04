"""임베딩 유틸리티 - OpenAI Embeddings API 사용."""

from __future__ import annotations

import os
from typing import List, Optional

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


def _load_env_file() -> None:
    """모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
    try:
        env_path = os.path.join(os.getcwd(), ".env")
        if os.path.exists(env_path):
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


class OpenAIEmbeddingGenerator:
    """텍스트를 벡터로 변환하는 임베딩 생성기 (OpenAI API 사용)."""

    _instance: Optional["OpenAIEmbeddingGenerator"] = None
    _client: Optional[OpenAI] = None
    _model: str = "text-embedding-3-small"

    def __new__(cls) -> "OpenAIEmbeddingGenerator":
        """싱글톤 패턴."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """초기화."""
        if not OPENAI_AVAILABLE:
            raise ImportError("openai 패키지가 설치되지 않았습니다. pip install openai")
        
        api_key = _get_env("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 환경 변수가 설정되지 않았습니다.")
        
        self._model = _get_env("EMBEDDING_MODEL", "text-embedding-3-small")
        self._client = OpenAI(api_key=api_key)

    @property
    def model(self) -> str:
        """임베딩 모델명."""
        return self._model

    @property
    def dimension(self) -> int:
        """임베딩 차원 수."""
        if "3-small" in self._model:
            return 1536
        elif "3-large" in self._model:
            return 3072
        else:
            return 1536  # 기본값

    def embed(self, text: str) -> List[float]:
        """텍스트를 벡터로 변환.

        Args:
            text: 임베딩할 텍스트

        Returns:
            벡터 리스트
        """
        if not self._client:
            self._initialize()
        
        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            raise RuntimeError(f"임베딩 생성 실패: {e}")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """여러 텍스트를 벡터로 변환 (배치 처리).

        Args:
            texts: 임베딩할 텍스트 리스트

        Returns:
            벡터 리스트의 리스트
        """
        if not self._client:
            self._initialize()
        
        try:
            # OpenAI API는 최대 2048개 텍스트를 한 번에 처리 가능
            response = self._client.embeddings.create(
                model=self._model,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            raise RuntimeError(f"임베딩 생성 실패: {e}")


def get_embedding_generator() -> OpenAIEmbeddingGenerator:
    """OpenAI 임베딩 생성기 인스턴스를 반환 (싱글톤).

    Returns:
        OpenAIEmbeddingGenerator 인스턴스
    """
    return OpenAIEmbeddingGenerator()






