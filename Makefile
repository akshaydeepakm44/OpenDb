.PHONY: up down restart logs prune clean

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart backend celery_discovery

logs:
	docker compose logs -f --tail=100 backend celery_discovery

prune:
	docker system prune -f && docker image prune -a

clean:
	rm -rf data/staging/*
