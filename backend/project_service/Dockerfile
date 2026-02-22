FROM python:3.12-slim

WORKDIR /app

# Install system deps for asyncpg and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/backend/project_service/

# Need the backend package to be importable
RUN touch /app/backend/__init__.py

ENV PYTHONPATH=/app

CMD ["uvicorn", "backend.project_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
