"""DB 쿼리 생성 모듈 - 사용자 요구사항에서 필요한 DB 테이블/컬럼을 LLM으로 추출."""

import json
import re
from typing import Any, Dict, List, Optional

try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from ..utils import get_env

# ... existing code ...
