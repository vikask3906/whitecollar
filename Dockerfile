# ─── Stage 1: Build the Python backend ─────────────────────────────────────
FROM python:3.13-slim AS backend

WORKDIR /app

# Install system deps for psycopg2 and PostGIS
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY data/ data/
COPY db/ db/
COPY scripts/ scripts/

# Expose FastAPI port
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
