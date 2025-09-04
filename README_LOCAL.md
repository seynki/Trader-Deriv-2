# Execução local com Docker Compose (um comando)

Agora com Mongo local e seed automático opcional, você consegue subir tudo com um comando e já treinar.

## Subir serviços
```bash
docker compose up -d --build
```
- Serviços: mongo, backend, ml_trainer e seed_candles (roda uma vez e encerra)
- Back-end: http://localhost:8001/api

## Verificar status
```bash
curl -s http://localhost:8001/api/deriv/status | jq
```

## Treinador semanal
- Roda em loop (verifica a cada 60s; treina quando detectar nova semana e dados)
- Variáveis default: TRAIN_SYMBOL=R_100, TRAIN_TIMEFRAME=3m

## Ajustes
- **PADRÃO**: Agora usa MongoDB Atlas por padrão (mesmo do preview)
- Para usar Mongo local, exporte as variáveis antes do up:
```bash
export MONGO_URL="mongodb://mongo:27017"
export DB_NAME="market_ticks"
docker compose up -d --build
```
- Para usar credenciais diferentes:
```bash
export MONGO_URL="mongodb+srv://sua_string_conexao"
export DERIV_APP_ID="seu_app_id"  
export DERIV_API_TOKEN="seu_token"
docker compose up -d --build
```
- Para treinar manualmente 1x, rode:
```bash
docker compose run --rm -e TRAIN_RUN_ONCE=1 ml_trainer
```

## Dados iniciais (seed)
- O serviço seed_candles executa automaticamente após o backend ficar pronto (R_100, 3m=180s, 2000 candles). Você pode mudar com variáveis de ambiente:
```bash
export SEED_SYMBOL=R_100
export SEED_GRANULARITY=180
export SEED_COUNT=2000
```
- Se preferir CSV, coloque em ./data/ml/ohlcv.csv

## Parar
```bash
docker compose down
```