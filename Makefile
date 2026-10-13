.PHONY: setup download train benchmark test lint demo stop logs

setup:
	uv venv --python 3.11
	uv pip install --python .venv/bin/python -e '.[train,dev]'

download:
	.venv/bin/python -m aiot_pm.download_data

train:
	.venv/bin/python -m aiot_pm.train

benchmark:
	.venv/bin/python -m aiot_pm.benchmark

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/python -m ruff check src tests

demo:
	docker compose up --build

stop:
	docker compose down

logs:
	docker compose logs -f edge controller simulator dashboard
