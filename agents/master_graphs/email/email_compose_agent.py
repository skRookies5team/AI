"""이메일 작성/발송 에이전트 - 초안 생성, 승인, 수정, 발송 (AI-026, AI-027)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import smtplib
import json
import base64
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

# Email 전용 로거 설정
def _setup_email_logger() -> logging.Logger:
    """이메일 관련 로그를 파일에 저장하는 로거 설정."""
    logger = logging.getLogger("email_compose")
    
    # 로거 레벨 설정
    logger.setLevel(logging.INFO)
    
    # logs 디렉토리 생성
    current_dir = Path(__file__).parent
    project_root = current_dir.parent.parent.parent.parent
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # 로그 파일 경로 (날짜별로 생성)
    log_file = log_dir / f"email_compose_{datetime.now().strftime('%Y%m%d')}.log"
    
    # 기존 핸들러 제거 (중복 방지)
    logger.handlers.clear()
    
    # 파일 핸들러 생성
    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8", mode='a')
        file_handler.setLevel(logging.INFO)
        
        # 포맷 설정: 시간 | 레벨 | 메시지
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.propagate = False  # 상위 로거로 전파하지 않음
        
        # 로거 초기화 확인 로그
        logger.info("=" * 80)
        logger.info(f"✅ Email Compose Logger 초기화 완료")
        logger.info(f"   로그 파일: {log_file}")
        logger.info(f"   핸들러 수: {len(logger.handlers)}")
        logger.info("=" * 80)
        
    except Exception as e:
        # 로거 설정 실패 시 기본 로거 사용
        import sys
        print(f"⚠️ Email 로거 설정 실패: {e}", file=sys.stderr)
        print(f"   로그 파일 경로: {log_file}", file=sys.stderr)
    
    return logger

# 로거 인스턴스 생성
logger = _setup_email_logger()


# ───────────────────────────────────────────────────────────
# .env 파일 로드
# ───────────────────────────────────────────────────────────
def _load_env_file() -> None:
	"""모듈 로드 시 .env 파일을 읽어 환경변수에 주입."""
	try:
		# 현재 파일의 부모 디렉토리 (email)
		current_dir = Path(__file__).parent
		# 프로젝트 루트 디렉토리 (AI)
		project_root = current_dir.parent.parent.parent.parent
		env_path = project_root / ".env"

		if env_path.exists():
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
			logger.info(f"✅ .env 파일 로드 성공: {env_path}")
		else:
			logger.warning(f"⚠️ .env 파일을 찾을 수 없습니다: {env_path}")
	except Exception as e:
		logger.error(f"❌ .env 파일 로드 실패: {e}", exc_info=True)

_load_env_file()


def _get_env(name: str, default: Optional[str] = None) -> Optional[str]:
	"""환경 변수 안전 조회."""
	return os.environ.get(name, default)


#
# 회사 서명 생성 함수 (AI_test/emailai.py 참고)
#
def get_company_signature_html() -> str:
	"""회사 로고, 정보를 포함한 HTML 서명을 생성합니다."""
	department = _get_env("EMAIL_DEPARTMENT", "인사팀")
	name = _get_env("EMAIL_NAME", "관리자")
	address = _get_env("EMAIL_ADDRESS_FULL", "서울특별시 중구 필동로 1길 30")
	phone = _get_env("EMAIL_PHONE", "02-2260-3114")
	
	# 로고 이미지를 base64로 인코딩
	logo_path = Path(__file__).parent.parent.parent.parent / "data" / "logo.png"
	logo_base64 = ""
	logo_mime_type = "image/png"
	
	if logo_path.exists():
		try:
			with open(logo_path, "rb") as f:
				logo_data = f.read()
				if len(logo_data) <= 5 * 1024 * 1024:  # 5MB 제한
					logo_base64 = base64.b64encode(logo_data).decode('utf-8')
					if logo_path.suffix.lower() in ['.jpg', '.jpeg']:
						logo_mime_type = "image/jpeg"
					elif logo_path.suffix.lower() == '.png':
						logo_mime_type = "image/png"
					elif logo_path.suffix.lower() == '.gif':
						logo_mime_type = "image/gif"
					elif logo_path.suffix.lower() == '.svg':
						logo_mime_type = "image/svg+xml"
		except Exception as e:
			logger.debug(f"로고 이미지 로드 실패: {e}")
	
	signature_html = """<table style="margin-top: 40px; padding-top: 20px; border-top: 2px solid #e0e0e0; font-family: 'Malgun Gothic', '맑은 고딕', Arial, sans-serif; font-size: 12px; color: #333333; line-height: 1.8; width: 100%; max-width: 600px;">"""
	
	if logo_base64:
		signature_html += f"""<tr><td style="padding-bottom: 15px;"><img src="data:{logo_mime_type};base64,{logo_base64}" alt="깐부전자 로고" style="max-width: 200px; height: auto; display: block; border: none;" /></td></tr>"""
	
	signature_html += f"""<tr><td style="padding-bottom: 8px;"><strong style="font-size: 15px; color: #000000;">깐부전자</strong> / <span style="font-size: 13px;">{department}</span></td></tr>"""
	signature_html += f"""<tr><td style="padding-bottom: 8px; font-size: 13px;">{name}</td></tr>"""
	signature_html += f"""<tr><td style="padding-bottom: 5px; font-size: 12px; color: #555555;">주소: {address}</td></tr>"""
	signature_html += f"""<tr><td style="padding-bottom: 20px; font-size: 12px; color: #555555;">전화: {phone}</td></tr>"""
	
	signature_html += """<tr><td style="padding-top: 20px; padding: 15px; background-color: #f5f5f5; border-left: 4px solid #d0d0d0; font-size: 11px; color: #666666;">"""
	signature_html += """<p style="margin: 0 0 8px 0; line-height: 1.6;"><strong>AI 자동 답변 안내:</strong> 본 이메일은 AI가 자동으로 작성한 메일입니다.</p>"""
	signature_html += """<p style="margin: 0 0 8px 0; line-height: 1.6;">내용에 문제가 있거나 수정이 필요한 경우, 이메일로 회신해 주시기 바랍니다.</p>"""
	signature_html += """<p style="margin: 0; line-height: 1.6;">혹여나 메일에 비하, 모욕 등 수신자에게 불쾌감을 줄 수 있는 내용이 포함되더라도, 이는 절대 의도된 것이 아니며, AI의 오작동으로 인한 것으로 양해 부탁드립니다. 이와 관련된 피드백이 있으시면 언제든지 연락 주시기 바랍니다.</p>"""
	signature_html += """</td></tr></table>"""
	
	return signature_html


def add_company_signature(body: str) -> str:
	"""이메일 본문에 회사 서명을 추가합니다."""
	signature = get_company_signature_html()
	
	if body.strip().endswith('</div>'):
		body = body.rstrip()
		body = body[:-6]
		html_body = f"{body}\n{signature}\n</div>"
	else:
		html_body = f"{body}\n{signature}"
	
	return html_body


#
# LLM 기반 이메일 작성 함수 (AI_test/emailai.py 참고)
#
async def generate_email_draft(
	to_email: str,
	user_message: str,
	original_subject: Optional[str] = None,
	original_sender: Optional[str] = None,
	original_body: Optional[str] = None,
	is_revision: bool = False,
	feedback: Optional[str] = None,
	current_draft_subject: Optional[str] = None,
	current_draft_body: Optional[str] = None,
) -> tuple[str, str]:
	"""
	이메일 내용을 LLM을 사용하여 작성합니다.
	답장인 경우 원본 이메일 내용을 참조하고, 수정 요청인 경우 피드백을 반영합니다.
	"""
	api_key = _get_env("OPENAI_API_KEY")
	if not api_key:
		raise ValueError("OpenAI API 키가 없습니다. .env 파일에 OPENAI_API_KEY를 설정하세요.")
	
	llm = ChatOpenAI(
		model="gpt-4o-mini",
		temperature=0,
		api_key=api_key
	)
	
	# 답장인지 새 메일인지 확인
	is_reply = bool(original_subject and original_sender)
	
	if is_reply:
		# 답장 작성
		system_prompt = """당신은 전문적인 이메일 작성 어시스턴트입니다.
사용자가 간단하게 말한 내용을 바탕으로 정중하고 전문적인 이메일 답장을 작성해주세요.

답장 작성 시 다음을 고려하세요:
1. 원본 이메일의 맥락을 이해하고 반영
2. 사용자의 의도를 정중하고 명확하게 표현
3. 적절한 인사말과 마무리 인사 포함
4. 원본 메시지를 참고용으로 포함 (선택사항)

답장은 한국어로 작성하고, 비즈니스 이메일 톤을 유지하세요."""
		
		# 수정 요청이고 기존 초안이 있는 경우
		if is_revision and feedback and current_draft_subject and current_draft_body:
			system_prompt += "\n\n중요: 다음은 기존에 작성된 초안입니다. 이 초안의 내용을 유지하면서, 사용자의 피드백만 반영하여 수정해주세요. 기존 내용을 모두 무시하지 말고, 피드백이 요청한 부분만 수정하세요."
			
			prompt_template = ChatPromptTemplate.from_messages([
				("system", system_prompt),
				("user", """기존 이메일 초안:
**제목:** {current_draft_subject}
**본문:**
{current_draft_body}

원본 이메일:
**제목:** {original_subject}
**발신자:** {original_sender}
**본문:**
{original_body}

사용자 요청: {user_message}
사용자 피드백: {feedback}

위 기존 초안의 내용을 유지하면서, 사용자 피드백만 반영하여 이메일을 수정해주세요. 기존 내용을 모두 새로 작성하지 말고, 피드백이 요청한 부분만 변경하세요.
답장 제목과 본문을 JSON 형식으로 반환해주세요:
{{
    "subject": "답장 제목",
    "body": "답장 본문"
}}""")
			])
			
			# prompt_template과 llm을 체인으로 연결
			chain = prompt_template | llm
			
			response = await chain.ainvoke({
				"current_draft_subject": current_draft_subject,
				"current_draft_body": re.sub(r'<[^>]+>', '', current_draft_body)[:3000],  # HTML 태그 제거하고 길이 제한
				"original_subject": original_subject or "",
				"original_sender": original_sender or "",
				"original_body": (original_body or "")[:3000],
				"user_message": user_message,
				"feedback": feedback
			})
		else:
			# 일반 답장 작성 또는 피드백 없이 수정
			if is_revision and feedback:
				system_prompt += f"\n\n사용자가 다음 피드백을 제공했습니다: {feedback}\n이 피드백을 반영하여 이메일을 수정해주세요."
			
			prompt_template = ChatPromptTemplate.from_messages([
				("system", system_prompt),
				("user", """원본 이메일:
**제목:** {original_subject}
**발신자:** {original_sender}
**본문:**
{original_body}

사용자 요청: {user_message}
{feedback_text}

위 사용자 요청을 바탕으로 원본 이메일에게 대한 답장을 작성해주세요.
답장 제목과 본문을 JSON 형식으로 반환해주세요:
{{
    "subject": "답장 제목",
    "body": "답장 본문"
}}

제목은 "Re: [원본 제목]" 형식으로 시작하거나, 사용자 요청에 맞게 적절히 작성해주세요.""")
			])
			
			feedback_text = f"\n사용자 피드백: {feedback}" if is_revision and feedback else ""
			
			# prompt_template과 llm을 체인으로 연결
			chain = prompt_template | llm
			
			response = await chain.ainvoke({
				"original_subject": original_subject or "",
				"original_sender": original_sender or "",
				"original_body": (original_body or "")[:3000],
				"user_message": user_message,
				"feedback_text": feedback_text
			})
	else:
		# 새 메일 작성
		recipients = [addr.strip() for addr in to_email.split(',') if addr.strip()]
		is_multiple = len(recipients) > 1
		
		system_prompt = """당신은 전문적인 이메일 작성 어시스턴트입니다.
사용자가 간단하게 말한 내용을 바탕으로 정중하고 전문적인 이메일을 작성해주세요.

이메일 작성 시 다음을 고려하세요:
1. 사용자의 의도를 정확히 파악하여 명확하게 표현
2. 적절한 인사말과 마무리 인사 포함
3. 비즈니스 이메일 톤 유지
4. 간결하고 명확한 내용
5. 여러 수신자가 있는 경우, 모두에게 적절한 인사말 사용

이메일은 한국어로 작성하세요."""
		
		# 수정 요청이고 기존 초안이 있는 경우
		if is_revision and feedback and current_draft_subject and current_draft_body:
			system_prompt += "\n\n중요: 다음은 기존에 작성된 초안입니다. 이 초안의 내용을 유지하면서, 사용자의 피드백만 반영하여 수정해주세요. 기존 내용을 모두 무시하지 말고, 피드백이 요청한 부분만 수정하세요."
			
			recipient_info = "여러 수신자에게 보내는 이메일입니다." if is_multiple else ""
			
			prompt_template = ChatPromptTemplate.from_messages([
				("system", system_prompt),
				("user", """기존 이메일 초안:
**제목:** {current_draft_subject}
**본문:**
{current_draft_body}

수신자: {to_email}
{recipient_info}
사용자 요청: {user_message}
사용자 피드백: {feedback}

위 기존 초안의 내용을 유지하면서, 사용자 피드백만 반영하여 이메일을 수정해주세요. 기존 내용을 모두 새로 작성하지 말고, 피드백이 요청한 부분만 변경하세요.
이메일 제목과 본문을 JSON 형식으로 반환해주세요:
{{
    "subject": "이메일 제목",
    "body": "이메일 본문"
}}""")
			])
			
			# prompt_template과 llm을 체인으로 연결
			chain = prompt_template | llm
			
			response = await chain.ainvoke({
				"current_draft_subject": current_draft_subject,
				"current_draft_body": re.sub(r'<[^>]+>', '', current_draft_body)[:3000],  # HTML 태그 제거하고 길이 제한
				"to_email": to_email,
				"recipient_info": recipient_info,
				"user_message": user_message,
				"feedback": feedback
			})
		else:
			# 일반 새 메일 작성 또는 피드백 없이 수정
			if is_revision and feedback:
				system_prompt += f"\n\n사용자가 다음 피드백을 제공했습니다: {feedback}\n이 피드백을 반영하여 이메일을 수정해주세요."
			
			recipient_info = "여러 수신자에게 보내는 이메일입니다." if is_multiple else ""
			
			prompt_template = ChatPromptTemplate.from_messages([
				("system", system_prompt),
				("user", """수신자: {to_email}
{recipient_info}
사용자 요청: {user_message}
{feedback_text}

위 사용자 요청을 바탕으로 이메일을 작성해주세요.
이메일 제목과 본문을 JSON 형식으로 반환해주세요:
{{
    "subject": "이메일 제목",
    "body": "이메일 본문"
}}""")
			])
			
			feedback_text = f"\n사용자 피드백: {feedback}" if is_revision and feedback else ""
			
			# prompt_template과 llm을 체인으로 연결
			chain = prompt_template | llm
			
			response = await chain.ainvoke({
				"to_email": to_email,
				"recipient_info": recipient_info,
				"user_message": user_message,
				"feedback_text": feedback_text
			})
	
	# JSON 파싱
	content = response.content
	
	json_match = re.search(r'\{[^{}]*"subject"[^{}]*"body"[^{}]*\}', content, re.DOTALL)
	if json_match:
		try:
			result = json.loads(json_match.group(0))
			subject = result.get("subject", f"Re: {original_subject}" if is_reply else "이메일")
			body = result.get("body", content)
		except:
			subject = f"Re: {original_subject}" if is_reply else "이메일"
			body = content
	else:
		subject = f"Re: {original_subject}" if is_reply else "이메일"
		body = content
	
	# 본문을 HTML로 변환
	if not re.search(r'<[^>]+>', body):
		body_escaped = body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
		body = body_escaped.replace('\n', '<br>')
		body = f'<div style="font-family: \'Malgun Gothic\', \'맑은 고딕\', Arial, sans-serif; font-size: 14px; color: #333333; line-height: 1.8; padding: 20px;">{body}</div>'
	
	# 회사 서명 추가
	body = add_company_signature(body)
	
	return subject, body


#
# SMTP 발송 함수
#
def send_email_via_smtp(
	to: str,
	subject: str,
	body: str,
	cc: Optional[str] = None,
	bcc: Optional[str] = None,
	is_html: bool = True
) -> tuple[bool, Optional[str]]:
	"""
	SMTP를 통해 이메일을 발송합니다.
	
	Returns:
		(success, error_message) 튜플
	"""
	try:
		smtp_server = _get_env("SMTP_SERVER")
		smtp_port = int(_get_env("SMTP_PORT"))
		email_user = _get_env("GMAIL_EMAIL")
		email_password = _get_env("GMAIL_APP_PASSWORD")
		
		if not email_user or not email_password:
			return False, "SMTP 접속 정보가 필요합니다. .env 파일에 GMAIL_EMAIL, GMAIL_APP_PASSWORD를 설정해 주세요."
		
		to_emails = [addr.strip() for addr in to.split(',') if addr.strip()]
		if not to_emails:
			return False, "수신자 이메일 주소가 없습니다."
		
		msg = MIMEMultipart('alternative')
		msg['From'] = email_user
		msg['To'] = ', '.join(to_emails)
		msg['Subject'] = subject
		
		if cc:
			msg['Cc'] = cc
		
		if is_html:
			# Plain text 버전 생성 (줄바꿈 유지)
			text_body = re.sub(r'<br\s*/?>', '\n', body, flags=re.IGNORECASE)  # <br>을 \n으로 변환
			text_body = re.sub(r'</p>', '\n\n', text_body)  # </p>를 두 줄바꿈으로 변환
			text_body = re.sub(r'</div>', '\n', text_body)  # </div>를 줄바꿈으로 변환
			text_body = re.sub(r'<[^>]+>', '', text_body)  # 나머지 HTML 태그 제거
			text_body = text_body.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
			text_body = re.sub(r'\n{3,}', '\n\n', text_body)  # 연속된 줄바꿈 정리
			
			# 순서 중요: plain text 먼저, HTML 나중에 (클라이언트가 HTML을 지원하면 HTML을 선택)
			text_part = MIMEText(text_body.strip(), 'plain', 'utf-8')
			text_part.set_charset('utf-8')
			msg.attach(text_part)
			
			# HTML 버전 나중에 attach
			html_part = MIMEText(body, 'html', 'utf-8')
			html_part.set_charset('utf-8')
			msg.attach(html_part)
		else:
			msg.attach(MIMEText(body, 'plain', 'utf-8'))
		
		with smtplib.SMTP(smtp_server, smtp_port) as server:
			server.starttls()
			server.login(email_user, email_password)
			
			recipients = to_emails.copy()
			if cc:
				recipients.extend([addr.strip() for addr in cc.split(',') if addr.strip()])
			if bcc:
				recipients.extend([addr.strip() for addr in bcc.split(',') if addr.strip()])
			
			recipients = list(set(recipients))
			
			server.send_message(msg, to_addrs=recipients)
		
		return True, None
	except Exception as e:
		logger.error(f"이메일 발송 실패: {e}", exc_info=True)
		return False, str(e)


#
# 이메일 작성/발송 로직 (LangGraph 없이 단순 함수로 처리)
#
async def _generate_draft(
	to_email: str,
	user_message: str,
	original_subject: Optional[str] = None,
	original_sender: Optional[str] = None,
	original_body: Optional[str] = None,
	approval_status: Optional[str] = None,
	user_feedback: Optional[str] = None,
	email_draft_subject: Optional[str] = None,
	email_draft_body: Optional[str] = None,
) -> Dict[str, Any]:
	"""이메일 초안을 생성합니다."""
	print("--- [Email Compose Agent] Generating Draft ---")
	
	print(f"[EMAIL COMPOSE AGENT] _generate_draft 호출")
	print(f"  - approval_status: {approval_status}")
	print(f"  - email_draft_subject: {email_draft_subject[:50] if email_draft_subject else None}...")
	print(f"  - email_draft_body: {'있음' if email_draft_body else '없음'}")
	
	# 이미 초안이 있고 승인 상태가 "approved"이면 초안 생성을 건너뛰고 바로 통과
	if email_draft_subject and email_draft_body and approval_status == "approved":
		print(f"[EMAIL COMPOSE AGENT] ✅ 초안 건너뛰기: 기존 초안 있음 + approved 상태")
		print("--- [Email Compose Agent] Existing draft found with approval status, skipping generation ---")
		return {
			"email_draft_subject": email_draft_subject,
			"email_draft_body": email_draft_body,
			"approval_status": approval_status,
		}
	
	is_revision = approval_status == "rejected"
	
	try:
		subject, body = await generate_email_draft(
			to_email=to_email,
			user_message=user_message,
			original_subject=original_subject,
			original_sender=original_sender,
			original_body=original_body,
			is_revision=is_revision,
			feedback=user_feedback
		)
		
		# HTML 본문을 텍스트로 변환하여 미리보기 생성
		text_body = re.sub(r'<[^>]+>', '', body)
		text_body = text_body.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
		body_preview = text_body[:500] + "..." if len(text_body) > 500 else text_body
		
		# 승인 상태가 이미 설정되어 있으면 그대로 유지, 아니면 "pending"으로 설정
		final_approval_status = approval_status if approval_status in ["approved", "rejected"] else "pending"
		
		message = f"""이메일 초안이 생성되었습니다. 발송 여부를 확인해주세요.

📧 **제목:** {subject}
📮 **수신자:** {to_email}

📝 **본문 미리보기:**
{body_preview}

✅ 발송을 원하시면 '발송' 또는 '보내기'라고 말씀해주세요.
✏️ 수정을 원하시면 '수정: [수정 내용]' 형식으로 알려주세요."""
		
		return {
			"email_draft_subject": subject,
			"email_draft_body": body,
			"approval_status": final_approval_status,
			"final_response": message,
			"messages": [message]
		}
	except Exception as e:
		logger.error(f"초안 생성 실패: {e}", exc_info=True)
		return {
			"final_response": f"이메일 초안 생성 중 오류가 발생했습니다: {str(e)}",
			"messages": [f"오류: {str(e)}"],
			"approval_status": None,
		}


async def _revise_draft(
	to_email: str,
	user_message: str,
	original_subject: Optional[str] = None,
	original_sender: Optional[str] = None,
	original_body: Optional[str] = None,
	user_feedback: Optional[str] = None,
	current_draft_subject: Optional[str] = None,
	current_draft_body: Optional[str] = None,
) -> Dict[str, Any]:
	"""사용자 피드백을 반영하여 초안을 수정합니다."""
	print("--- [Email Compose Agent] Revising Draft ---")
	print(f"  - current_draft_subject: {current_draft_subject[:50] if current_draft_subject else None}...")
	print(f"  - current_draft_body: {'있음' if current_draft_body else '없음'}")
	print(f"  - user_feedback: {user_feedback}")
	
	try:
		# 피드백 반영하여 초안 재생성
		subject, body = await generate_email_draft(
			to_email=to_email,
			user_message=user_message,
			original_subject=original_subject,
			original_sender=original_sender,
			original_body=original_body,
			is_revision=True,
			feedback=user_feedback,
			current_draft_subject=current_draft_subject,
			current_draft_body=current_draft_body,
		)
		
		# HTML 본문을 텍스트로 변환하여 미리보기 생성
		text_body = re.sub(r'<[^>]+>', '', body)
		text_body = text_body.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
		body_preview = text_body[:500] + "..." if len(text_body) > 500 else text_body
		
		message = f"""피드백을 반영하여 이메일 초안을 수정했습니다. 확인 후 발송 여부를 알려주세요.

📧 **제목:** {subject}
📮 **수신자:** {to_email}

📝 **본문 미리보기:**
{body_preview}

✅ 발송을 원하시면 '발송' 또는 '보내기'라고 말씀해주세요.
✏️ 수정을 원하시면 '수정: [수정 내용]' 형식으로 알려주세요."""
		
		return {
			"email_draft_subject": subject,
			"email_draft_body": body,
			"approval_status": "pending",  # 수정 후 다시 승인 대기
			"final_response": message,
			"messages": [message]
		}
	except Exception as e:
		logger.error(f"초안 수정 실패: {e}", exc_info=True)
		return {
			"final_response": f"이메일 초안 수정 중 오류가 발생했습니다: {str(e)}",
			"messages": [f"오류: {str(e)}"],
			"approval_status": None,
		}


async def _send_email(
	to_email: str,
	email_draft_subject: str,
	email_draft_body: str,
	cc: Optional[str] = None,
	bcc: Optional[str] = None,
) -> Dict[str, Any]:
	"""이메일을 발송합니다."""
	print("--- [Email Compose Agent] Sending Email ---")
	
	print(f"[EMAIL COMPOSE AGENT] _send_email 호출")
	print(f"  - to_email: {to_email}")
	print(f"  - email_draft_subject: {email_draft_subject[:50] if email_draft_subject else None}...")
	print(f"  - email_draft_body 길이: {len(email_draft_body) if email_draft_body else 0}")
	print(f"  - cc: {cc}")
	print(f"  - bcc: {bcc}")
	
	try:
		print(f"[EMAIL COMPOSE AGENT] SMTP 발송 시도 중...")
		success, error_msg = await asyncio.to_thread(
			send_email_via_smtp,
			to=to_email,
			subject=email_draft_subject,
			body=email_draft_body,
			cc=cc,
			bcc=bcc,
			is_html=True
		)
		
		if success:
			print(f"[EMAIL COMPOSE AGENT] ✅ 이메일 발송 성공!")
			return {
				"final_response": f"이메일이 성공적으로 발송되었습니다! (수신자: {to_email})",
				"messages": [f"이메일 발송 완료."],
				"approval_status": "approved",
			}
		else:
			print(f"[EMAIL COMPOSE AGENT] ❌ 이메일 발송 실패: {error_msg}")
			return {
				"final_response": f"이메일 발송에 실패했습니다: {error_msg}",
				"messages": [f"발송 실패: {error_msg}"],
				"approval_status": "rejected",
			}
	except Exception as e:
		logger.error(f"이메일 발송 중 오류: {e}", exc_info=True)
		return {
			"final_response": f"이메일 발송 중 오류가 발생했습니다: {str(e)}",
			"messages": [f"오류: {str(e)}"],
			"approval_status": "rejected",
		}


#
# 외부 인터페이스 함수
#
async def compose_and_send_email(
	to_email: str,
	user_message: str,
	original_subject: Optional[str] = None,
	original_sender: Optional[str] = None,
	original_body: Optional[str] = None,
	cc: Optional[str] = None,
	bcc: Optional[str] = None,
	session_id: Optional[str] = None,
	approval_status: Optional[str] = None,  # 외부에서 설정된 승인 상태
	user_feedback: Optional[str] = None,  # 외부에서 제공된 피드백
	email_draft_subject: Optional[str] = None,  # 저장된 초안 제목 (발송 명령 시 사용)
	email_draft_body: Optional[str] = None,  # 저장된 초안 본문 (발송 명령 시 사용)
) -> Dict[str, Any]:
	"""
	이메일 작성 및 발송을 위한 통합 함수.
	초안 생성, 사용자 승인, 수정, 발송 과정을 관리합니다 (LangGraph 없이 단순 함수로 처리).
	
	Args:
		approval_status: 외부에서 설정된 승인 상태 ("approved", "rejected", None)
		user_feedback: 외부에서 제공된 피드백 (수정 요청 시)
		email_draft_subject: 저장된 초안 제목 (발송 명령 시 사용)
		email_draft_body: 저장된 초안 본문 (발송 명령 시 사용)
	"""
	# 디버그: compose_and_send_email 호출
	print(f"[EMAIL COMPOSE AGENT] compose_and_send_email 호출됨")
	print(f"  - to_email: {to_email}")
	print(f"  - user_message: {user_message[:100] if user_message else None}...")
	print(f"  - approval_status (입력): {approval_status}")
	print(f"  - email_draft_subject (입력): {email_draft_subject[:50] if email_draft_subject else None}...")
	print(f"  - email_draft_body (입력): {'있음' if email_draft_body else '없음'}")
	
	# 사용자 쿼리에서 승인/거부/피드백 감지
	if approval_status is None:
		query_lower = user_message.lower()
		if any(k in query_lower for k in ["발송", "보내", "전송", "send", "승인", "ok", "좋아"]):
			approval_status = "approved"
		elif any(k in query_lower for k in ["수정", "다시", "revision", "추가", "변경"]):
			approval_status = "rejected"
			# 피드백 추출
			if user_feedback is None:
				feedback_keywords = ["수정", "다시", "추가", "변경", "더"]
				for keyword in feedback_keywords:
					if keyword in query_lower:
						idx = query_lower.find(keyword.lower())
						user_feedback = user_message[idx + len(keyword):].strip()
						if user_feedback.startswith(":"):
							user_feedback = user_feedback[1:].strip()
						break
		elif any(k in query_lower for k in ["거부", "안 보내", "취소", "reject", "cancel"]):
			approval_status = "rejected"
	
	# 승인 상태 설정
	approval_status = approval_status or "pending"
	
	# 디버그: Initial State
	print(f"[EMAIL COMPOSE AGENT] 처리 시작:")
	print(f"  - approval_status: {approval_status}")
	print(f"  - email_draft_subject: {email_draft_subject[:50] if email_draft_subject else None}...")
	print(f"  - email_draft_body: {'있음' if email_draft_body else '없음'}")
	print(f"  - to_email: {to_email}")
	
	# 1. 초안 생성 또는 기존 초안 사용
	draft_result = await _generate_draft(
		to_email=to_email,
		user_message=user_message,
		original_subject=original_subject,
		original_sender=original_sender,
		original_body=original_body,
		approval_status=approval_status,
		user_feedback=user_feedback,
		email_draft_subject=email_draft_subject,
		email_draft_body=email_draft_body,
	)
	
	draft_subject = draft_result.get("email_draft_subject")
	draft_body = draft_result.get("email_draft_body")
	current_approval_status = draft_result.get("approval_status", approval_status)
	
	# 오류 발생 시
	if draft_result.get("approval_status") is None and not draft_result.get("email_draft_subject"):
		return {
			"ok": False,
			"message": draft_result.get("final_response", "이메일 처리 중 오류가 발생했습니다."),
			"draft": None,
			"error": draft_result.get("final_response"),
		}
	
	# 2. 승인 상태 확인 및 처리
	# pending 상태면 초안만 반환하고 종료 (사용자 응답 대기)
	if current_approval_status == "pending":
		print(f"[EMAIL COMPOSE AGENT] approval_status=pending, 사용자 응답 대기")
		return {
			"ok": False,  # 아직 발송 안 됨
			"message": draft_result.get("final_response", "이메일 초안이 생성되었습니다."),
			"draft": {
				"subject": draft_subject,
				"body": draft_body,
			} if draft_subject else None,
			"error": None,
		}
	
	# approved 상태면 바로 발송
	if current_approval_status == "approved":
		print(f"[EMAIL COMPOSE AGENT] ✅ approval_status=approved, 이메일 발송")
		send_result = await _send_email(
			to_email=to_email,
			email_draft_subject=draft_subject,
			email_draft_body=draft_body,
			cc=cc,
			bcc=bcc,
		)
		
		return {
			"ok": send_result.get("approval_status") == "approved",
			"message": send_result.get("final_response", "이메일 처리가 완료되었습니다."),
			"draft": {
				"subject": draft_subject,
				"body": draft_body,
			} if draft_subject else None,
			"error": send_result.get("final_response") if send_result.get("approval_status") == "rejected" else None,
		}
	
	# rejected 상태면 피드백에 따라 수정 또는 종료
	if current_approval_status == "rejected":
		if user_feedback:
			print(f"[EMAIL COMPOSE AGENT] approval_status=rejected, 피드백 반영하여 수정")
			print(f"  - 기존 초안 사용: {draft_subject[:50] if draft_subject else None}...")
			revise_result = await _revise_draft(
				to_email=to_email,
				user_message=user_message,
				original_subject=original_subject,
				original_sender=original_sender,
				original_body=original_body,
				user_feedback=user_feedback,
				current_draft_subject=draft_subject,  # 기존 초안 제목 전달
				current_draft_body=draft_body,  # 기존 초안 본문 전달
			)
			
			revise_subject = revise_result.get("email_draft_subject")
			revise_body = revise_result.get("email_draft_body")
			
			return {
				"ok": False,  # 아직 발송 안 됨 (수정 후 다시 승인 대기)
				"message": revise_result.get("final_response", "이메일 초안을 수정했습니다."),
				"draft": {
					"subject": revise_subject,
					"body": revise_body,
				} if revise_subject else None,
				"error": None,
			}
		else:
			# 피드백 없으면 초안을 다시 보여주고 종료
			print(f"[EMAIL COMPOSE AGENT] approval_status=rejected, 피드백 없음 - 초안 반환")
			return {
				"ok": False,
				"message": draft_result.get("final_response", "이메일 초안이 생성되었습니다."),
				"draft": {
					"subject": draft_subject,
					"body": draft_body,
				} if draft_subject else None,
				"error": draft_result.get("final_response"),
			}
	
	# 예상치 못한 상태
	print(f"[EMAIL COMPOSE AGENT] ⚠️ 예상치 못한 approval_status: {current_approval_status}")
	return {
		"ok": False,
		"message": draft_result.get("final_response", "이메일 처리가 완료되었습니다."),
		"draft": {
			"subject": draft_subject,
			"body": draft_body,
		} if draft_subject else None,
		"error": None,
	}
