# 📝 Meeting One Line - 회의록 관리 서버

> Spring Boot 기반의 AI 회의록 관리 백엔드 서비스  
> 음성 파일 업로드 → AI 분석 결과 콜백 → 회의록 조회/검색/수정/삭제까지 전 과정을 처리합니다.

---

docker exec -it meeting-one-line-db mariadb -u root -p

## 실행방법
1. docker pull mariadb:11 (이미지 없을 경우)
2. docker-compose up -d

---

## 🚀 기술 스택

| 영역 | 기술 |
|------|------|
| **Language** | Java 17 |
| **Framework** | Spring Boot 3.x |
| **ORM / DB** | Spring Data JPA + MariaDB |
| **Security** | Spring Security + JWT |
| **API 문서화** | Springdoc OpenAPI (Swagger UI) |
| **빌드 도구** | Gradle |
| **로깅** | SLF4J + Logback |
| **테스트 DB / 로컬 개발** | Docker + MariaDB:11.4 |

---

## 📁 프로젝트 구조

src/main/java/com/meetingoneline/meeting_one_line
┣ global
┃ ┣ config
┃ ┣ dto
┃ ┣ exception
┃ ┗ jwt
┣ user
┣ auth
┗ meeting
┣ controller
┣ service
┣ dto
┣ entity
┗ repository

