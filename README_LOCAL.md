# Execução local com Docker Compose

Este guia permite rodar o backend + treinador de ML com um único comando, usando seu MongoDB Atlas (ou CSV local como fallback) e preparar dados de candles para não começar do zero.

## Pré-requisitos
- Docker e Docker Compose instalados
- Credenciais da Deriv no arquivo backend/.env (já preenchido no projeto)
- MONGO_URL e DB_NAME válidos (backend/.env já configurado). Se preferir, exporte variáveis no shell para sobrescrever sem editar o arquivo:
  ```bash
  export MONGO_URL="mongodb+srv://..."
  export DB_NAME="market_ticks"
  ```

## Subir tudo (backend + treinador semanal)
```bash
docker compose up -d --build
```
- Backend: http://localhost:8001/api
- Verifique status:
  ```bash
  curl -s http://localhost:8001/api/deriv/status | jq
  ```

## Popular candles inicialmente (opcional, recomendado)
Para não começar do zero, rode o seed de candles uma vez:
```bash
SEED_SYMBOL=R_100 SEED_GRANULARITY=60 SEED_COUNT=2000 \
  docker compose --profile manual up seed_candles
```
- O endpoint faz upsert, então rodar novamente não duplica.

## Treino semanal automático (já habilitado)
O serviço ml_trainer roda continuamente e treina 1x/semana. Você pode ajustar variáveis:
```bash
export TRAIN_SYMBOL=R_100
export TRAIN_TIMEFRAME=3m
export TRAIN_HORIZON=3
export TRAIN_THRESHOLD=0.003
export TRAIN_MODEL_TYPE=rf
```
Recrie o serviço se alterar variáveis:
```bash
docker compose up -d --build ml_trainer
```

## Treino manual sob demanda (uma única execução)
```bash
TRAIN_SYMBOL=R_100 TRAIN_TIMEFRAME=3m TRAIN_HORIZON=3 TRAIN_THRESHOLD=0.003 TRAIN_MODEL_TYPE=rf \
  docker compose --profile manual run --rm ml_train_once
```
A saída exibirá o resultado e eventual promoção de modelo (champion).

## Fallback para CSV local
Se o Mongo Atlas estiver indisponível ou vazio, o treinador busca `/data/ml/ohlcv.csv` dentro do container. Monte um CSV local assim:
```bash
mkdir -p data/ml
cp seu_ohlcv.csv data/ml/ohlcv.csv
```
O compose já monta `./data/ml` em `/data/ml` para todos os serviços relevantes.

## Dicas de conectividade com Mongo Atlas
- Garanta que seu IP está liberado em Network Access do Atlas (temporariamente 0.0.0.0/0 ajuda a validar)
- O código usa TLS automaticamente. Se houver erro de handshake TLS, ajuste a whitelist no Atlas.

## Parar os serviços
```bash
docker compose down
```