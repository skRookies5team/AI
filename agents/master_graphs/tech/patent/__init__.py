"""특허 검색 모듈 패키지."""

from .patent_keyword import generate_patent_keyword, generate_patent_keyword_from_text
from .patent_types import PatentSearchState
from .patent_config import (
    KIPRIS_API_KEY,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    PATENT_DATA_DIR,
    _notify_progress,
    load_golden_set,
    get_golden_set,
    _set_session_id,
    get_search_results,
    save_search_results,
    is_waiting_for_search_mode,
    set_waiting_for_search_mode,
    get_pending_search_keyword,
    get_session_search_mode,
    set_session_search_mode,
    parse_search_mode_from_input,
)
from .patent_kipris_api import kipris_search, kipris_download_pdf
from .patent_pdf_utils import (
    extract_pdf_text,
    save_text_file,
    save_metadata,
    load_metadata,
    get_pdf_link_from_metadata,
    get_full_text_from_file,
    ensure_list,
    clean_text,
)
from .patent_milvus_db import (
    _initialize_models,
    _initialize_milvus,
    get_collection_name,
    get_or_create_collection,
    check_vector_db,
    embed_and_store_patent,
    extract_keyword_from_text,
)
from .patent_nodes import (
    check_db_node,
    ask_search_mode_node,
    ask_web_search_node,
    search_kipris_node,
    download_pdf_node,
    rag_answer_node,
    get_workflow,
)
from .patent_utils import (
    _extract_keyword,
    _parse_selection_number,
    _parse_selection_numbers,
)
from .patent_agent import patent_search

__all__ = [
    # Keyword
    "generate_patent_keyword",
    "generate_patent_keyword_from_text",
    # Types
    "PatentSearchState",
    # Config
    "KIPRIS_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "PATENT_DATA_DIR",
    # KIPRIS API
    "kipris_search",
    "kipris_download_pdf",
    # PDF Utils
    "extract_pdf_text",
    "save_text_file",
    "save_metadata",
    "load_metadata",
    "get_pdf_link_from_metadata",
    "get_full_text_from_file",
    "ensure_list",
    "clean_text",
    # Milvus DB
    "check_vector_db",
    "embed_and_store_patent",
    "extract_keyword_from_text",
    "get_collection_name",
    "get_or_create_collection",
    # Nodes
    "check_db_node",
    "ask_search_mode_node",
    "ask_web_search_node",
    "search_kipris_node",
    "download_pdf_node",
    "rag_answer_node",
    "get_workflow",
    # Utils
    "_extract_keyword",
    "_parse_selection_number",
    "_parse_selection_numbers",
    # Main Agent
    "patent_search",
]
