FROM node:20-alpine

WORKDIR /app/frontend

# Instalar dependências primeiro (aproveitar cache)
COPY frontend/package.json frontend/yarn.lock ./
RUN corepack enable &amp;&amp; yarn install

# Copiar código (será sobrescrito por volume em dev)
COPY frontend/ .

EXPOSE 3000
ENV HOST=0.0.0.0

# Dev com hot-reload (CRACO/CRA)
CMD ["yarn", "start"]