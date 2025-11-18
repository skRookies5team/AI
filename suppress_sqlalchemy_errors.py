"""SQLAlchemy 연결 종료 오류 억제 유틸리티."""

from __future__ import annotations

import sys
import warnings
import logging
from contextlib import contextmanager
from io import StringIO


@contextmanager
def suppress_sqlalchemy_errors():
    """SQLAlchemy 연결 종료 시 발생하는 오류를 억제하는 컨텍스트 매니저."""
    # SQLAlchemy 로거 레벨 조정
    sqlalchemy_loggers = [
        "sqlalchemy.pool",
        "sqlalchemy.engine",
        "sqlalchemy.dialects",
    ]
    original_levels = {}
    for logger_name in sqlalchemy_loggers:
        logger = logging.getLogger(logger_name)
        original_levels[logger_name] = logger.level
        logger.setLevel(logging.ERROR)
    
    # 경고 억제
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*event loop.*")
        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*proactor.*")
        warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*AttributeError.*")
        
        # stderr 리다이렉트 (선택적)
        old_stderr = sys.stderr
        try:
            # StringIO로 리다이렉트하여 오류 메시지 캡처
            sys.stderr = StringIO()
            yield
        finally:
            sys.stderr = old_stderr
            # 로거 레벨 복원
            for logger_name, level in original_levels.items():
                logging.getLogger(logger_name).setLevel(level)

