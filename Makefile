.PHONY: install test quick results serve docker-web docker-batch clean
install:
	pip install -r requirements-dev.txt
test:
	python -m pytest -q
quick:
	python scripts/run_all.py --quick --out outputs_quick
results:
	python scripts/run_all.py
serve:
	python -m tstsim.webapp
docker-web:
	docker compose up --build
docker-batch:
	docker build -t climate-tst-sim . && docker run --rm -v $$PWD/outputs:/app/outputs climate-tst-sim python scripts/run_all.py
clean:
	rm -rf outputs_quick .pytest_cache __pycache__ */__pycache__
