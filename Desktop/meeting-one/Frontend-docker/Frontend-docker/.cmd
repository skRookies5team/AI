docker build -t cookie940605/front:0.1 .

docker push cookie940605/front:0.1

# EC2에서 실행
sudo docker pull cookie940605/ai-frontend:latest
sudo docker run -d --name ai-frontend -p 3000:3000 \
  -e NODE_ENV=production \
  -e NEXT_PUBLIC_API_BASE_URL=http://<EC2-IP>:8080/api \
  cookie940605/ai-frontend:latest
