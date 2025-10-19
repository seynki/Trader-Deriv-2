# Solução para Problema de Desconexão

## Problema
A aplicação mostra "Desconectado" quando rodando localmente com docker-compose porque o frontend está configurado para usar a URL da plataforma Emergent ao invés do backend local.

## Causas Identificadas

### 1. Configuração de URL Incorreta
- O arquivo `/frontend/.env` aponta para `https://strategy-validator-2.preview.emergentagent.com`  
- Quando roda localmente, deveria apontar para `http://localhost:8001`

### 2. Erro no Código JavaScript 
- Bug na linha 38 do `App.js`: variável `BACKEND_URL` não definida
- Deveria ser `BACKEND_BASE` 

## Soluções

### Opção A: Configuração Automática (Recomendada)

1. **Crie o arquivo `.env.local` no frontend:**
```bash
# No diretório /app/frontend/
cat > .env.local << 'EOF'
# Configuração para desenvolvimento local com docker-compose
REACT_APP_BACKEND_URL=http://localhost:8001
WDS_SOCKET_PORT=0
EOF
```

2. **Corrija o bug no App.js:**
```javascript
// Linha 38 em /app/frontend/src/App.js
// ANTES:
const u = new URL(BACKEND_URL);

// DEPOIS:
const u = new URL(BACKEND_BASE);
```

3. **Reconstrua e reinicie os containers:**
```bash
# Pare os containers
docker-compose down

# Reconstrua o frontend
docker-compose build frontend

# Inicie novamente
docker-compose up -d
```

### Opção B: Usar Variáveis de Ambiente

1. **Defina a variável antes de subir:**
```bash
export REACT_APP_BACKEND_URL=http://localhost:8001
docker-compose up -d
```

2. **Ou crie um arquivo .env no root:**
```bash
# No diretório /app/
echo "REACT_APP_BACKEND_URL=http://localhost:8001" > .env
docker-compose up -d
```

## Teste da Solução

Após aplicar uma das soluções, teste:

1. **Abra o navegador em:** http://localhost:3000
2. **Verifique se mostra "Conectado"** ao invés de "Desconectado"
3. **Teste uma funcionalidade:** clique em um dos índices de volatilidade
4. **Verifique no console do navegador:** não deve ter erros de WebSocket

## Estrutura de URLs Corretas

### Desenvolvimento Local:
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8001/api`  
- WebSocket: `ws://localhost:8001/api/ws/ticks`

### Produção (Emergent):
- Frontend: `https://strategy-validator-2.preview.emergentagent.com`
- Backend API: `https://strategy-validator-2.preview.emergentagent.com/api`
- WebSocket: `wss://fica-desconectado.preview.emergentagent.com/api/ws/ticks`

## Logs para Debug

Se ainda não funcionar, verifique os logs:

```bash
# Logs do frontend
docker-compose logs frontend

# Logs do backend  
docker-compose logs backend

# Logs do MongoDB
docker-compose logs mongo

# Status dos containers
docker-compose ps
```

## Arquivos Modificados

1. ✅ `/app/frontend/.env.local` - criado
2. ✅ `/app/frontend/src/App.js` - linha 38 corrigida  
3. ✅ `/app/SOLUCAO_DESCONEXAO.md` - esta documentação

## Notas Importantes

- O arquivo `.env.local` tem precedência sobre `.env`
- Sempre reconstrua o container do frontend após mudanças no código
- Para desenvolvimento local, use sempre `http://localhost:8001` (sem `/api` no final)
- O WebSocket é criado automaticamente com base na URL do backend