#!/usr/bin/env bash
set -e

TAG="${1:-latest}"

echo "=== Building Backend Image ==="
docker build -t "akshaydeepakm/opendb:backend" -t "akshaydeepakm/opendb:backend-${TAG}" ./backend

echo "=== Building Frontend Image ==="
docker build -t "akshaydeepakm/opendb:frontend" -t "akshaydeepakm/opendb:frontend-${TAG}" ./frontend

echo "=== Logging in to Docker Hub ==="
docker login

echo "=== Pushing to Docker Hub ==="
docker push "akshaydeepakm/opendb:backend"
docker push "akshaydeepakm/opendb:backend-${TAG}"
docker push "akshaydeepakm/opendb:frontend"
docker push "akshaydeepakm/opendb:frontend-${TAG}"

echo "=== Successfully Pushed to Docker Hub! ==="
echo "Images:"
echo "  - akshaydeepakm/opendb:backend"
echo "  - akshaydeepakm/opendb:backend-${TAG}"
echo "  - akshaydeepakm/opendb:frontend"
echo "  - akshaydeepakm/opendb:frontend-${TAG}"
