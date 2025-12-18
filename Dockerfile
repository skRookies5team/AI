FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=hardlink \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# System deps:
# - poppler-utils: pdf2image 변환 시 필요
# - lib* : playwright(Chromium) 런타임 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    poppler-utils \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 libx11-6 libxext6 libxcb1 libx11-xcb1 \
    fonts-liberation \
  && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Install deps with lockfile (cache-friendly layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen \
  && rm -rf /root/.cache/uv

# App source
COPY . .

# Install Playwright browser (Chromium).
# playwright 기능을 전혀 안 쓰면 이 줄을 제거해도 됩니다.
RUN uv run playwright install chromium

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "fastapi_app:app", "--host", "0.0.0.0", "--port", "8000"]
