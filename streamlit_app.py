# streamlit_app.py — Streamlit 기반 대화형 인터페이스
from __future__ import annotations

import streamlit as st
import uuid
from main_agents import format_result, run_supervisor

# 페이지 설정
st.set_page_config(
	page_title="Super Supervisor AI",
	page_icon="🤖",
	layout="wide",
	initial_sidebar_state="collapsed"
)

# 세션 상태 초기화
if "session_id" not in st.session_state:
	st.session_state.session_id = str(uuid.uuid4())
	st.session_state.messages = []

# 제목 및 설명
st.title("🤖 kkanbu AI")
st.markdown("---")
st.markdown("*납기는 생명, 품질은 자존심!*")
st.markdown("**복리후생, 일정, 문서, 기술 검색 등 다양한 업무를 도와드립니다.**")

# 사이드바 (선택사항)
with st.sidebar:
	st.header("ℹ️ 사용 가이드")
	st.markdown("""
	### 질문 예시:
	
	**복리후생 관련:**
	- 우리 회사 휴가·근무제도에 대해 알려줘
	- 급여 외 혜택에 대해 알려줘
	
	**일정 관련:**
	- 사번 1001 연차 조회
	- 사번 1001 연차 3일 신청 (시작: 2024-12-20, 종료: 2024-12-22)
	
	**이메일 검색:**
	- 육아휴직 관련 메일 찾아줘
	
	**기술 검색:**
	- 디스플레이 관련 특허 찾아줘
	""")
	
	if st.button("🔄 대화 초기화"):
		st.session_state.session_id = str(uuid.uuid4())
		st.session_state.messages = []
		st.rerun()

# 채팅 메시지 표시
for message in st.session_state.messages:
	with st.chat_message(message["role"]):
		st.markdown(message["content"])

# 사용자 입력
if prompt := st.chat_input("질문을 입력하세요..."):
	# 사용자 메시지 추가
	st.session_state.messages.append({"role": "user", "content": prompt})
	with st.chat_message("user"):
		st.markdown(prompt)
	
	# 에이전트 응답 생성
	with st.chat_message("assistant"):
		with st.spinner("처리 중..."):
			try:
				result = run_supervisor(prompt, session_id=st.session_state.session_id)
				response = format_result(result)
				
				# 응답 표시
				st.markdown(response)
				
				# 메시지 히스토리에 추가
				st.session_state.messages.append({"role": "assistant", "content": response})
				
			except Exception as e:
				error_msg = f"오류가 발생했습니다: {str(e)}"
				st.error(error_msg)
				st.session_state.messages.append({"role": "assistant", "content": error_msg})

# 하단 정보
st.markdown("---")
st.caption("💡 팁: 'exit' 또는 'quit'을 입력하면 종료됩니다. (Streamlit에서는 페이지를 새로고침하세요)")



