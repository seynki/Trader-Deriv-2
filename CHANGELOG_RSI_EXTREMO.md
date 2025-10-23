# Changelog - Implementação RSI Extremo no Painel de Automação

## Data: 2025-01-28

## Mudanças Implementadas

### 1. Nova Estratégia: RSI Extremo
O painel de "Automação" no frontend agora usa a estratégia **RSI Extremo** ao invés de cruzamento de média móvel.

#### Lógica RSI Extremo:
- **CALL (Compra)**: Executado quando RSI ≤ 25 (oversold extremo)
- **PUT (Venda)**: Executado quando RSI ≥ 75 (overbought extremo)

### 2. Implementação Técnica

#### Frontend (/app/frontend/src/App.js)
1. **Função `calculateRSI()`**: Implementada para calcular o RSI (Relative Strength Index) com período 14
   - Usa método EMA (Exponential Moving Average) para ganhos e perdas
   - Retorna valor entre 0-100

2. **Lógica de Sinal**: Modificada no `useEffect` do `AutomacaoPanel`
   - Coleta preços via WebSocket em tempo real
   - Calcula RSI a cada novo tick
   - Gera sinal CALL quando RSI ≤ 25
   - Gera sinal PUT quando RSI ≥ 75
   - Respeita cooldown entre trades

3. **UI Atualizada**:
   - Descrição: "Estratégia: RSI Extremo (CALL se RSI ≤25, PUT se RSI ≥75)"
   - Exibição do último sinal inclui valor do RSI

#### Código Anterior (Mantido mas Comentado)
A lógica antiga de cruzamento de média móvel foi mantida no código como comentário para possível uso futuro:
```javascript
/* LÓGICA ANTIGA (CRUZAMENTO DE MÉDIA) - COMENTADA PARA POSSÍVEL USO FUTURO
  ... código do cruzamento de média ...
*/
```

### 3. Configurações Mantidas
- **Cooldown**: Tempo mínimo entre trades (padrão: 30s)
- **Período**: Número de preços mantidos em buffer (padrão: 20, mínimo 15 para RSI)
- **Stake**: Valor da aposta
- **Take Profit / Stop Loss**: Configurações de TP/SL mantidas
- **Tipos de Contrato**: CALLPUT, ACCUMULATOR, TURBOS, MULTIPLIERS

### 4. Benefícios da Mudança
- **Alinhamento com backend**: Agora usa mesma estratégia RSI extremo do StrategyRunner
- **Sinais mais conservadores**: RSI extremo (25/75) vs cruzamento de média
- **Menor frequência de trades**: Trades apenas em condições extremas de mercado
- **Maior potencial de reversão**: Captura pontos de possível reversão de tendência

### 5. Conectividade
- **WebSocket**: Mantido para receber ticks em tempo real
- **Backend**: Chamadas via `/api/deriv/buy` continuam funcionando
- **Reconexão automática**: Sistema reconecta automaticamente em caso de queda

## Como Usar

1. Acesse o painel de "Automação" no frontend
2. Configure:
   - **Símbolo**: R_10, R_25, R_50, R_75, R_100, etc
   - **Período**: Mínimo 15 (recomendado 20-50)
   - **Cooldown**: Tempo entre trades (30-120s recomendado)
   - **Tipo**: CALLPUT (recomendado para RSI extremo)
   - **TP/SL**: Configure Take Profit e Stop Loss em USD
3. Ative o switch
4. O sistema irá:
   - Coletar preços via WebSocket
   - Calcular RSI a cada tick
   - Executar trade quando RSI ≤ 25 (CALL) ou RSI ≥ 75 (PUT)
   - Respeitar cooldown entre trades

## Observações Importantes

- **Dados mínimos**: Sistema requer mínimo 15 preços para calcular RSI
- **Cooldown crítico**: Configure cooldown adequado para evitar overtrading
- **Mercados voláteis**: RSI extremo funciona melhor em mercados com reversões
- **Monitoramento**: Acompanhe os sinais gerados no console do navegador

## Logs de Debug

Para verificar o funcionamento:
```javascript
console.log // No navegador (F12 > Console)
// Você verá: "🎯 Sinal RSI Extremo detectado: CALL/PUT - Preço: X.XXXX, RSI: XX.X"
```

## Reversão para Lógica Antiga

Se necessário reverter para cruzamento de média:
1. Edite `/app/frontend/src/App.js`
2. Descomente o bloco `/* LÓGICA ANTIGA (CRUZAMENTO DE MÉDIA) */`
3. Comente a lógica RSI extremo
4. Recompile: `cd /app/frontend && yarn build`
5. Reinicie: `sudo supervisorctl restart frontend`

## Status
✅ Implementado
✅ Testado (compilação)
✅ Frontend reiniciado
✅ Backend conectado à Deriv
⏳ Aguardando teste em produção com dados reais

## Próximos Passos Sugeridos
1. Testar com diferentes períodos (20, 30, 50)
2. Ajustar limiares RSI se necessário (ex: 20/80 para sinais mais raros)
3. Adicionar mais indicadores de confirmação se desejado (ADX, Volume, etc)
4. Implementar backtesting para validar estratégia
