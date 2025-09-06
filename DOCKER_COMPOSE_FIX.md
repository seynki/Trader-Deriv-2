# 🔧 Correção do Erro "recharts" no Docker Compose

## 🚨 Problema
O erro `Cannot find module 'recharts'` ocorre no Docker Compose local porque o volume monta sobrescrevia o `node_modules` instalado durante o build.

## ✅ Solução Aplicada

### 1. Correção do docker-compose.yml
- Alterado volume do node_modules para usar volume nomeado
- Adicionada seção volumes para persistir node_modules

### 2. Melhoria no Dockerfile
- Incluído yarn.lock no COPY para melhor cache
- Mantido processo de instalação das dependências

### 3. Arquivos .dockerignore
- Criados para evitar conflitos durante build

## 🚀 Como usar no seu ambiente local

### Passo 1: Parar containers existentes
```bash
docker-compose down -v
```

### Passo 2: Remover imagens antigas (força rebuild)
```bash
docker-compose build --no-cache frontend
# Ou rebuild tudo:
# docker-compose build --no-cache
```

### Passo 3: Subir os serviços
```bash
docker-compose up -d
```

### Passo 4: Verificar logs do frontend
```bash
docker-compose logs -f frontend
```

## 🔍 Verificação
- Acesse http://localhost:3000
- A aplicação deve carregar sem erros de "recharts"
- O frontend deve conectar ao backend em http://localhost:8001/api

## 📋 Arquivos Modificados
- ✅ `docker-compose.yml` - Volume node_modules corrigido
- ✅ `frontend/Dockerfile` - Melhorado cache das dependências  
- ✅ `frontend/.dockerignore` - Evita conflitos durante build
- ✅ `.dockerignore` - Configuração raiz

## 🛠️ Troubleshooting

### Se ainda houver erro:
```bash
# Rebuild completo forçado
docker-compose down -v
docker system prune -f
docker-compose build --no-cache
docker-compose up -d
```

### Para debug:
```bash
# Entrar no container frontend
docker exec -it app-frontend sh

# Verificar se recharts existe
ls node_modules | grep recharts

# Ver logs detalhados
docker-compose logs frontend
```

Agora o Docker Compose deve funcionar perfeitamente! 🎉