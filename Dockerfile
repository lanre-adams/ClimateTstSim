FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TSTSIM_DATA_DIR=/app/data/user
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
RUN python -m pytest -q
EXPOSE 8000
# Default: the web interface on http://localhost:8000
# Batch run instead:  docker run --rm -v "$PWD/outputs:/app/outputs" climate-tst-sim python scripts/run_all.py
CMD ["python", "-m", "tstsim.webapp", "--host", "0.0.0.0", "--port", "8000"]
