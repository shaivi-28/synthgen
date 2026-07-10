#!/bin/bash
set -e

# Load server config
if [ ! -f deploy.env ]; then
  echo "Error: deploy.env not found. Copy deploy.env.example and fill in your server details."
  exit 1
fi
export $(grep -v '^#' deploy.env | xargs)

IMAGE_NAME="recon-testgen:latest"
TMP_FILE="/tmp/recon-testgen.tar.gz"
REMOTE_FILE="${SERVER_PATH}/recon-testgen.tar.gz"

echo "==> Building Docker image..."
docker build -t $IMAGE_NAME .

echo "==> Exporting image (~50MB, this takes a moment)..."
docker save $IMAGE_NAME | gzip > $TMP_FILE

echo "==> Copying to server ${SERVER_USER}@${SERVER_HOST}..."
scp $TMP_FILE ${SERVER_USER}@${SERVER_HOST}:${REMOTE_FILE}

echo "==> Deploying on server..."
ssh ${SERVER_USER}@${SERVER_HOST} bash << EOF
  set -e
  docker load < ${REMOTE_FILE}
  docker stop recon-testgen 2>/dev/null || true
  docker rm recon-testgen 2>/dev/null || true
  docker run -d \\
    --name recon-testgen \\
    --restart unless-stopped \\
    -p 5050:5050 \\
    recon-testgen:latest
  rm -f ${REMOTE_FILE}
  echo "Container status:"
  docker ps | grep recon-testgen
EOF

rm -f $TMP_FILE
echo ""
echo "Deployed! App running at http://${SERVER_HOST}:5050"
