"""NAVER Cloud Platform CLOVA Speech STT SDK

현재 경로는 호환성을 위해 유지되며, 실제 구현은 src.core로 이동되었습니다.
"""

from src.core.stt_client import ClovaSpeechClient, ClovaSpeechError

__version__ = "1.0.0"
__all__ = ["ClovaSpeechClient", "ClovaSpeechError"]