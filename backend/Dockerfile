FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dependências de build para libs científicas
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia requirements e instala primeiro para cache eficiente
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copia o código do backend
COPY backend /app/backend

WORKDIR /app/backend
EXPOSE 8001

# Inicia o FastAPI
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]