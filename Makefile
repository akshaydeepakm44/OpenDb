.PHONY: up down backend frontend test clean

up:
	docker compose up -d

down:
	docker compose down

backend:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	$env:PYTHONPATH="backend"; .\venv\Scripts\pytest backend/tests/test_pipeline.py

clean:
	rm -rf data/raw/* data/processed/* data/manifests/*
