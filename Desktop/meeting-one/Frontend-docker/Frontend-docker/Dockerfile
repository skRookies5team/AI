# -------------------------------
# 1️⃣ 빌드 단계
# -------------------------------
FROM node:22-alpine AS builder
WORKDIR /app

# package*.json만 복사해 캐시 효율 극대화
COPY package*.json ./

# 프로덕션 의존성 포함 전체 설치 (npm ci는 lockfile 기반 설치)
RUN npm ci

# 전체 소스 복사
COPY . .

# Next.js 빌드 (출력: .next)
RUN npm run build


# -------------------------------
# 2️⃣ 실행 단계
# -------------------------------
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000

# 빌드 산출물과 런타임 의존성만 복사
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/package*.json ./

# devDependencies 제외하고 설치
RUN npm ci --omit=dev

# 포트 노출
EXPOSE 3000

# 프로덕션 서버 시작
CMD ["npm", "start"]
