docker build -t cookie940605/app-server:0.7 .

docker push cookie940605/app-server:0.7

docker-compose down
docker compose up -d

docker logs -f app-server

로그 보기
docker logs -f app-server

docker logs -f ai-server


docker exec -it app-server /bin/bash
cd /app/logs
ls
tail -f app.log