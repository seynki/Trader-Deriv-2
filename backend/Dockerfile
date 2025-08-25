FROM python:3.11-slim

# Dependências básicas (evita falhas de build de libs python nativas)
RUN apt-get update \ 
    &amp;&amp; apt-get install -y --no-install-recommends build-essential \ 
    &amp;&amp; rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# Instalar dependências primeiro (aproveitar cache)
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -U pip \ 
    &amp;&amp; pip install --no-cache-dir -r requirements.txt

# Copiar código (será sobrescrito por volume em dev)
COPY backend/ .

EXPOSE 8001
ENV PYTHONUNBUFFERED=1

# Dev com hot-reload
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]