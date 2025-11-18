# AI Module - Email Search Agent (POP3)

## 환경 변수 설정

아래 값을 실제 계정에 맞게 설정하세요(예: NAVER Mail).

- `EMAIL_POP3_HOST`: 예) `pop.naver.com`
- `EMAIL_POP3_PORT`: 기본 `995`
- `EMAIL_POP3_SSL`: 기본 `true`
- `EMAIL_USERNAME`: 전체 이메일 주소(예: `user@naver.com`)
- `EMAIL_PASSWORD`: 앱 비밀번호(권장) 또는 계정 비밀번호
- `EMAIL_MAX_FETCH`: 최근 N개 메일에서 검색(기본 `100`)
- `EMAIL_BODY_PREVIEW_LINES`: 본문 스니펫 라인 수(기본 `10`)

## 사용 방법

`main_agents.py`를 실행한 뒤 아래처럼 질문하세요.

```
사용자> 육아휴직 신청 관련 메일을 찾아줘
```

Supervisor는 해당 질의를 Q&A 마스터로 라우팅하고, `agents/email_search_agent.py`가 POP3로 최근 메일을 검사하여 관련 메일들을 반환합니다.

## 주의사항

- POP3는 서버 검색을 지원하지 않아 최근 메일 일부를 내려받아 로컬에서 필터링합니다.
- NAVER는 POP3 사용을 계정에서 활성화해야 하며, 2FA 환경에서는 앱 비밀번호 사용을 권장합니다.





