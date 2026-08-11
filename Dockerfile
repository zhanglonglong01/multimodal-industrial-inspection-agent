FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_MODE=demo \
    INSPECTION_DATA_DIR=/app/data \
    INSPECTION_DATABASE_PATH=/app/data/runtime/inspection_agent.db \
    INSPECTION_CHECKPOINT_PATH=/app/data/runtime/langgraph_checkpoints.db

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir . && \
    mkdir -p /app/data/runtime/uploads

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"

CMD ["sh", "-c", "inspection-agent init-web-demo && uvicorn inspection_agent.web:app --host 0.0.0.0 --port 8000"]
