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
