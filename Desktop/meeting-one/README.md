# AWS 배포 가이드 (Meeting-One-Line 스택)

이 문서는 업로드된 `docker-compose.yml` 과 Python 기반 `Dockerfile` 내용을 바탕으로 **로컬 실행 → 이미지 빌드/푸시 → AWS EC2 배포** 흐름을 정리합니다.  
업로드된 Compose 파일 일부는 생략 기호(`...`)로 표시되어 있어 세부 옵션이 전부 보이진 않습니다. 본 문서는 **확인된 항목을 중심으로** 작성했고, 빈칸은 프로젝트에 맞게 보완해 주세요.

---

## 1. 구성 개요

- **DB**: MariaDB `11.4` (컨테이너명: `meeting-one-line-db`)
- **App Server (Spring Boot)**: 이미지 `tyler12/app-server:0.7` (포트 `8080:8080` 매핑)
- **(추정) AI Server (Python)**: 업로드된 `Dockerfile`은 Python 3.13-slim 기반, `server.py`로 8000 포트 노출
- **(추정) Frontend**: Compose 일부에서 `80:3000` 포트 매핑 흔적 확인
- **공유 볼륨**
  - `mariadb_data`
  - `shared_uploads` (업로드 파일 공유)
  - `app_logs` (앱 서버 로그)
  - `ai_logs`  (AI 서버 로그)

> 확인된 Compose 원문(일부, 생략 포함):
```
version: "3.8"

services:
  # -------------------------------
  # 1️⃣ Database (MariaDB)
  # -------------------------------
  mariadb:
    image: mariadb:11.4
    container_name: meeting-one-line-db
    restart: always
    environment:
      MYSQL_ROOT_PASSWORD: 1234
      MYSQL_DATABASE: meeting_one_line
      MYSQL_USER: meeting_user
      MYSQL_PASSWORD: meeting_pass
    ports:
      - "3306:3306"
    volumes:
      - mariadb_data:/var/lib/mysql
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci

  # -------------------------------
  # 2️⃣ Spring Boot Backend (App Server)
  # -------------------------------
  app-server:
    image: tyler12/app-server:0.7
    container_name: app-server
    depends_on:
      - mariadb
    restart: always
    ports:
      - "8080:8080"
    environment:
      SPRING_PROFILES_ACTIVE: prod
      SPRING_DATASOURCE_URL: jdbc:mariadb://mariadb:3306/meeting_one_line
      SPRING_DATASOURCE_USERNAME: meeting_user
      SPRING_DATASOURCE_PASSWORD: meeting_pass
      TZ: Asia/Seoul
    volumes:
      - shared_uploads:/app/uploads
      - app_logs:/app/logs

  # -------------------------------
  # 3️⃣ AI Server (Python)
  # -------------------------------
  ai-server:
    image: tyler12/ai-server:0.1
    container_name: ai-server
    depends_on:
      - mariadb
    restart: always
    ports:
      - "8000:8000"
    environment:
      DB_URL: mariadb
      DB_NAME: meeting_one_line
      DB_USER: meeting_user
      DB_PASS: meeting_pass
      TZ: Asia/Seoul
      CLOVA_SPEECH_INVOKE_URL: https://clovaspeech-gw.ncloud.com/external/v1/13186/423d1affbf1c84f71cc799d928484e08219ca8cf567ae1e2266b0784e6e6c38a
      CLOVA_SPEECH_SECRET_KEY: 300d19dcca704dd4a7ae3d5fc842eb7a
      OPENAI_API_KEY: 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
      DEFAULT_LANGUAGE: ko-KR
      CLOVA_SPEECH_LANG: Kor
      APP_SERVER_CALLBACK_HOST: http://app-server:8080

    volumes:
      - shared_uploads:/app/uploads
      - ai_logs:/app/logs

  # -------------------------------
  # 4️⃣ Frontend (Next.js)
  # -------------------------------
  front:
    image: tyler12/front:0.1
    container_name: front
    depends_on:
      - app-server
      - ai-server
    restart: always
    ports:
      - "80:3000"
    environment:
      NODE_ENV: production

# -------------------------------
# 5️⃣ Volumes
# -------------------------------
volumes:
  mariadb_data:
  shared_uploads:  # ✅ 업로드 파일 공유
  app_logs:        # ✅ 앱 서버 로그
  ai_logs:         # ✅ AI 서버 로그

```

> 확인된 Python Dockerfile:
```dockerfile
FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    build-essential gcc g++ \
    && rm -rf /var/lib/apt/lists/*
    
WORKDIR /app

COPY requirements.txt .

# pip 최신화 후 종속성 설치 
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["python", "server.py"]

```

---

## 2. 사전 준비

- Docker / Docker Compose v2 이상
- AWS 계정 및 EC2 인스턴스 (Amazon Linux 2023 권장), 보안 그룹에서 **80, 8080, 8000, 3306**(내부용) 포트 정책 점검
- (선택) AWS ECR 리포지토리 생성 (이미지 푸시용)
- 도메인/HTTPS(선택): ALB + ACM 또는 Nginx Reverse Proxy + Let’s Encrypt

---

## 3. 환경 변수(.env) 정리

Compose 내에서 확인된 주요 항목 (값은 예시입니다):

### MariaDB
```env
MYSQL_ROOT_PASSWORD=********
MYSQL_DATABASE=meeting_one_line
MYSQL_USER=meeting_user
MYSQL_PASSWORD=meeting_pass
```

### Spring Boot (app-server)
```env
SPRING_PROFILES_ACTIVE=prod
SPRING_DATASOURCE_URL=jdbc:mariadb://mariadb:3306/meeting_one_line
SPRING_DATASOURCE_USERNAME=meeting_user
SPRING_DATASOURCE_PASSWORD=meeting_pass
```

> 보안상 평문 비밀번호 대신 **.env 파일**을 사용하고 `docker-compose.yml`에서는 `${VAR}` 참조로 바꾸는 것을 권장합니다.

---

## 4. 로컬 실행 (Compose)

```bash
# 1) (선택) .env 파일 준비
cp .env  # 없으면 새로 생성

# 2) 컨테이너 실행
docker compose up -d

# 3) 로그 확인
docker compose logs -f
```

서비스 포트 (예상):
- DB: 3306 (호스트 노출 여부는 Compose 설정 확인 필요)
- 앱 서버: http://localhost:8080
- AI 서버(Python): http://localhost:8000
- 프론트엔드: http://localhost/ (또는 http://localhost:3000 → Nginx/Reverse Proxy로 80 포워딩된 흔적)

---

## 5. Python 기반 AI 서버 이미지 빌드/실행

루트에 업로드된 `Dockerfile` 기준:
```bash
# 이미지 빌드
docker build -t meeting-one-line-ai:latest .

# 로컬 실행
docker run -d --name ai-server -p 8000:8000 meeting-one-line-ai:latest
```

필요 파일:
- `requirements.txt`
- `server.py`
- 그 외 애플리케이션 코드

> `Dockerfile`은 `server.py`를 엔트리포인트로 실행합니다. FastAPI/Flask 등을 사용한다면 `server.py` 내부에서 앱이 실행되도록 구성되어 있어야 합니다.

---

## 6. Spring Boot & Frontend (예시)

### Backend 이미지 태깅/푸시 (도커 허브 또는 ECR)
```bash
# 로컬 이미지 태그 (예시)
docker tag app-server:local tyler12/app-server:0.7

# 푸시
docker push tyler12/app-server:0.7
```

### Frontend
- Compose 일부에서 `80:3000` 매핑이 보입니다. 프론트는 `node build → nginx` 또는 `vite preview`/`next start` 유형일 수 있으니 현재 레포의 `Dockerfile.frontend` 또는 프론트 빌드 스크립트를 기준으로 조정하세요.
- 프론트 컨테이너가 **백엔드 API** 주소를 올바르게 가리키는지 (.env, Nginx `proxy_pass`, 혹은 빌드타임 `VITE_API_BASE_URL`) 확인하세요.

---

## 7. AWS ECR 연동 (선택)

```bash
# 1) AWS CLI 로그인 (리전/계정 변경)
aws ecr get-login-password --region ap-southeast-2 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-southeast-2.amazonaws.com

# 2) ECR 리포 생성 (최초 1회)
aws ecr create-repository --repository-name meeting-one-line-ai

# 3) 태깅 후 푸시
docker tag meeting-one-line-ai:latest <ACCOUNT_ID>.dkr.ecr.ap-southeast-2.amazonaws.com/meeting-one-line-ai:latest
docker push <ACCOUNT_ID>.dkr.ecr.ap-southeast-2.amazonaws.com/meeting-one-line-ai:latest
```

> 백엔드/프런트엔드도 동일 방식으로 ECR 리포를 나누어 관리하세요.

---

## 8. EC2 배포 절차 (단일 호스트, Compose)

```bash
# EC2에 접속
ssh -i ./keys/mykey.pem ec2-user@<EC2_PUBLIC_IP>

# Docker/Compose 설치 (예시)
sudo yum update -y
sudo yum install -y docker
sudo systemctl enable --now docker

DOCKER_COMPOSE_VERSION=v2.29.2
sudo curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)"   -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
docker-compose version

# 업로드
# 로컬에서
docker build -t <Docker Hub ID>/<image name> .
docker push <Docker Hub ID>/<image name>

# EC2에서
cd /home/ec2-user/meeting-one-line

# (선택) .env 파일 서버에 배치
vi .env

# 컨테이너 실행
docker compose -f docker-compose.yml up -d

# 확인
docker ps -a
docker images -a
```

### 포트/방화벽
- EC2 보안그룹에서 80/8080/8000 허용
- MariaDB 3306은 **내부통신만 허용** 권장

---

## 9. 데이터/로그 볼륨

Compose에 다음 볼륨이 정의되어 있습니다:
- `mariadb_data` : DB 영속화
- `shared_uploads` : 업로드 파일 공유 (앱/AI 간 공유 가능성)
- `app_logs`, `ai_logs` : 서비스 로그

> EC2에서는 `/home/ec2-user/meeting-one-line/docker-volume/...` 등 **호스트 경로 바인드**를 사용하면 백업과 점검이 편해집니다.

---

## 10. 헬스체크 & 의존성

- `app-server`는 `mariadb`에 `depends_on` 되어 있습니다.  
  Spring Boot의 `spring-boot-starter-actuator`와 `/actuator/health`를 이용해 **헬스체크**를 추가하면 안정성이 좋아집니다.
- AI 서버도 `/health` 같은 간단한 엔드포인트를 제공하도록 권장합니다.

---

## 11. 운영 팁 (Troubleshooting)

- **이미지 풀 실패**: `pull access denied` → 레지스트리 로그인 여부 확인 (`docker login`, `aws ecr get-login-password`)
- **포트 충돌**: 호스트에 동일 포트를 쓰는 다른 프로세스가 있는지 확인 (`sudo lsof -i :80`)
- **DB 접속 실패**: `SPRING_DATASOURCE_URL`/계정/방화벽 확인, 컨테이너 네트워크에서 `mariadb` 호스트명으로 접근
- **환경변수 누락**: Compose/EC2에 .env 동기화
- **권한 문제(키 파일)**: `chmod 400 ./keys/mykey.pem`

---

## 12. 보안 권장사항

- 비밀번호/키는 **Parameter Store/Secrets Manager** 또는 `.env`(비공개)로 관리
- **최소 권한 IAM** 사용
- 프로덕션에서는 **HTTPS** (ALB + ACM) 적용
- 정기 업데이트 & 이미지 취약점 스캔

---

## 13. 체크리스트

- [ ] `docker-compose.yml`의 생략(`...`) 구역 채움
- [ ] `.env` 분리 및 비밀 키 제거
- [ ] ECR/도커허브 이미지 태그 최신화
- [ ] 프런트엔드 → 백엔드 API 엔드포인트 점검
- [ ] 헬스체크 엔드포인트 구성
- [ ] 백업/복구 전략 수립 (DB 볼륨 스냅샷 등)

---

## 14. 유용한 명령어 모음

```bash
# 전체 로그 follow
docker compose logs -f

# 특정 서비스 로그
docker logs -f app-server

# 재시작
docker compose restart app-server

# 이미지/컨테이너 정리
docker system prune -af
docker volume ls
docker volume inspect <VOLUME>
```

---

## 15. 라이선스/저작권

이 문서와 배포 스크립트는 프로젝트 저작권 정책을 따릅니다.
