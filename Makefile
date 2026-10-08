.PHONY: install test quick results docker clean
install:
	pip install -r requirements-dev.txt
test:
	python -m pytest -q
quick:
	python scripts/run_all.py --quick --out outputs_quick
results:
	python scripts/run_all.py
docker:
	docker build -t climate-tst-sim . && docker run --rm -v $$PWD/outputs:/app/outputs climate-tst-sim
clean:
	rm -rf outputs_quick .pytest_cache __pycache__ */__pycache__
