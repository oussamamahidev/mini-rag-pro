.PHONY: help up down build logs logs-api logs-worker ps test test-fast shell-api shell-frontend init seed clean restart-api health

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

up:  ## Start all services
	docker compose up -d

down:  ## Stop all services
	docker compose down

build:  ## Rebuild all images
	docker compose build --no-cache

logs:  ## Follow logs for all services
	docker compose logs -f

logs-api:  ## Follow FastAPI logs only
	docker compose logs -f fastapi

logs-worker:  ## Follow Celery worker logs
	docker compose logs -f celery-worker

ps:  ## Show service status
	docker compose ps

test:  ## Run backend tests
	docker compose run --rm fastapi pytest tests/ -v --cov=src --cov-report=term-missing

test-fast:  ## Run tests excluding slow (RAGAS) tests
	docker compose run --rm fastapi pytest tests/ -v -m "not slow"

shell-api:  ## Open shell in FastAPI container
	docker compose exec fastapi bash

shell-frontend:  ## Open shell in Next.js container
	docker compose exec nextjs sh

init:  ## Initialize database (create indexes + admin tenant)
	docker compose exec fastapi python scripts/init_db.py

seed:  ## Seed with sample data for development
	docker compose exec fastapi python scripts/seed_dev_data.py

clean:  ## Remove containers and volumes (DESTROYS ALL DATA)
	docker compose down -v --remove-orphans

restart-api:  ## Restart FastAPI only (after code changes in prod)
	docker compose restart fastapi celery-worker celery-beat

health:  ## Check health of all services
	curl -s http://localhost/api/health | python3 -m json.tool
