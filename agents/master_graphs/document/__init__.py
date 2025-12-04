"""문서 도메인 모듈."""

# master_graph를 lazy import하지 않고 직접 import
# (get_document_master_graph는 함수이므로 호출 시점에만 실행됨)
from agents.master_graphs.document.master_graph import get_document_master_graph

__all__ = ["get_document_master_graph"]



