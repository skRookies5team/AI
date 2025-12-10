# 1단계: Build
FROM gradle:8.10.0-jdk17 AS build
WORKDIR /app

# 종속성 캐싱 최적화
COPY build.gradle settings.gradle ./
RUN gradle dependencies --no-daemon || true

# 실제 소스 복사
COPY . .

# 테스트 제외 빌드
RUN gradle clean bootJar -x test --no-daemon

# 2단계: Run
FROM openjdk:17-jdk-slim
WORKDIR /app

COPY --from=build /app/build/libs/*.jar app.jar

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
