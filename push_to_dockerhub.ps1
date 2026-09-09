# Push OpenDb images to Docker Hub
# Usage: .\push_to_dockerhub.ps1 [tag] (default: latest)

param(
    [string]$Tag = "latest"
)

Write-Host "=== Building Backend Image ===" -ForegroundColor Cyan
docker build -t "akshaydeepakm/opendb:backend" -t "akshaydeepakm/opendb:backend-$Tag" ./backend

Write-Host "=== Building Frontend Image ===" -ForegroundColor Cyan
docker build -t "akshaydeepakm/opendb:frontend" -t "akshaydeepakm/opendb:frontend-$Tag" ./frontend

Write-Host "=== Logging in to Docker Hub (if not already logged in) ===" -ForegroundColor Cyan
docker login

Write-Host "=== Pushing to Docker Hub ===" -ForegroundColor Cyan
docker push "akshaydeepakm/opendb:backend"
docker push "akshaydeepakm/opendb:backend-$Tag"
docker push "akshaydeepakm/opendb:frontend"
docker push "akshaydeepakm/opendb:frontend-$Tag"

Write-Host "=== Successfully Pushed to Docker Hub! ===" -ForegroundColor Green
Write-Host "Images available on Docker Hub:"
Write-Host "  - akshaydeepakm/opendb:backend"
Write-Host "  - akshaydeepakm/opendb:backend-$Tag"
Write-Host "  - akshaydeepakm/opendb:frontend"
Write-Host "  - akshaydeepakm/opendb:frontend-$Tag"
