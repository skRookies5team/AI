"""LLM 필드 값 생성 모듈."""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from langchain_openai import ChatOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from ..utils import get_env

# ... existing code ...
