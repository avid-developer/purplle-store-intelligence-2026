FROM python:3.12-slim

WORKDIR /app
COPY app ./app
COPY data ./data
COPY pipeline ./pipeline
COPY web ./web
COPY docs ./docs
COPY README.md ./

ENV PYTHONUNBUFFERED=1
ENV STORE_DATA_DIR=/app/data
ENV STORE_DB_PATH=/app/data/store.db
EXPOSE 8000

CMD ["python", "-m", "app.main"]

