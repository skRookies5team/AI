"""마스터그래프 모듈 - 각 도메인별 LangGraph 서브그래프."""

from agents.master_graphs.base import BaseMasterGraph

# 도메인별 마스터그래프 import (새 폴더 구조)
from agents.master_graphs.schedule import get_schedule_master_graph
from agents.master_graphs.document import get_document_master_graph
from agents.master_graphs.qa import get_qa_master_graph
from agents.master_graphs.email import get_email_master_graph
from agents.master_graphs.tech import get_tech_master_graph
from agents.master_graphs.recruitment import get_recruitment_master_graph

__all__ = [
    "BaseMasterGraph",
    "get_schedule_master_graph",
    "get_document_master_graph",
    "get_qa_master_graph",
    "get_email_master_graph",
    "get_tech_master_graph",
    "get_recruitment_master_graph",
]

