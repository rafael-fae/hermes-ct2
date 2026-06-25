# CT2 — Control Tower para Equipes Hermes Agent

Servidor API REST + MCP que centraliza o workflow de equipes multi-agente. Funciona como **plugin nativo do Hermes Dashboard**, substituindo planilhas e arquivos soltos por um sistema integrado de projetos, tasks, auditorias e scorecards.

## O que o CT2 faz

| Funcionalidade | Descrição |
|---------------|-----------|
| **Kanban** | Board Planejado → Executado → Auditado dentro do Dashboard |
| **Auditoria** | Timeline com hash de commits, status e agente responsável |
| **Scorecards** | Métricas: first-pass rate, rework, scope creep, tempo médio |
| **Event Hooks** | Recebe `on_session_start` / `on_session_end` dos gateways |
| **GitHub Webhooks** | Sincroniza commits com tasks automaticamente |
| **Reconciliação** | Corrige inconsistências entre tasks e auditorias a cada 10min |
| **MCP Server** | Integração nativa via Model Context Protocol |
| **API REST** | CRUD completo de projetos, tasks, auditorias |

## Pré-requisitos

- Python 3.11+
- [Hermes Agent](https://github.com/nous-research/hermes-agent) instalado
- [Hermes Dashboard](https://hermes.rafaelfae.com) rodando na porta 9119
- Git

## Instalação — Passo a Passo

### 1. Clonar o repositório

```bash
git clone https://github.com/rafael-fae/hermes-ct2.git ~/Dev/hermes-ct2
cd ~/Dev/hermes-ct2
```

### 2. Instalar dependências

```bash
# Com UV (recomendado)
uv pip install -e .

# Ou com pip
pip install -r requirements.txt
```

### 3. Iniciar o servidor CT2

```bash
# Direto no terminal (para testar)
python -m ct2 serve --port 7890

# Via PM2 (produção — recomendado)
pm2 start "python3 -m ct2 serve --port 7890" \
  --name ct2-server \
  --cwd ~/Dev/hermes-ct2
pm2 save
```

Verificar se está rodando:
```bash
curl http://localhost:7890/api/health
# Deve retornar: {"status":"ok"}
```

### 4. Instalar como Plugin do Hermes Dashboard

```bash
# Criar diretório do plugin
mkdir -p ~/.hermes/plugins/ct2/dist

# Copiar arquivos do plugin
cp ~/Dev/hermes-ct2/dashboard/manifest.json ~/.hermes/plugins/ct2/
cp ~/Dev/hermes-ct2/dashboard/dist/index.js ~/.hermes/plugins/ct2/dist/
```

### 5. Registrar o plugin no config.yaml

No arquivo `~/.hermes/config.yaml` (perfil `default`), adicionar:

```yaml
plugins:
  enabled:
    - kanban
    - ct2        # ← Adicionar esta linha
```

### 6. Reiniciar o Dashboard

```bash
pm2 restart hermes-dashboard
```

A aba **"CT2"** vai aparecer no menu lateral do Dashboard.

## API REST — Endpoints

### Projetos
```bash
GET  /api/projects                    # Listar todos
POST /api/projects                    # Criar novo
```

### Tasks
```bash
GET  /api/tasks?project={nome}        # Tasks de um projeto
POST /api/tasks                       # Criar task
```

### Auditorias
```bash
GET  /api/audits?project={nome}       # Histórico de auditorias
POST /api/audits                      # Registrar auditoria
```

### Scorecards
```bash
GET  /api/scorecards?days=30          # Métricas dos últimos 30 dias
GET  /api/scorecards?agent={nome}     # Filtrar por agente
```

### Event Hooks
```bash
POST /api/events                      # Receber eventos de sessão
```

### Health
```bash
GET  /api/health                      # Status do servidor
```

## Configurar Event Hooks nos Agentes

Para que o CT2 receba eventos de início/fim de sessão, adicionar no `config.yaml` de cada agente:

```yaml
hooks:
  on_session_start:
    - url: "http://127.0.0.1:7890/api/events"
      method: POST
  on_session_end:
    - url: "http://127.0.0.1:7890/api/events"
      method: POST
```

Reiniciar os gateways: `pm2 restart [agente] --update-env`

## Estrutura de Diretórios

```
hermes-ct2/
├── src/
│   ├── server.py       # API REST (FastAPI, porta 7890)
│   ├── db.py            # Schema SQLite com 10+ tabelas
│   └── mcp_server.py    # MCP Server nativo
├── dashboard/
│   ├── manifest.json    # Registro do plugin no Dashboard
│   └── dist/index.js    # Frontend React (Kanban, Scorecards, Timeline)
├── ct2.py               # CLI: scan, build, serve, audit, db
└── README.md
```

## Comandos CLI

```bash
python ct2.py scan                     # Scan de projetos e tasks
python ct2.py build                    # Gerar dashboard HTML
python ct2.py serve --port 7890        # Iniciar servidor API
python ct2.py audit --project [nome]   # Auditar projeto
python ct2.py db init                  # Inicializar banco SQLite
```

## Troubleshooting

**Plugin não aparece no Dashboard:**
- Verificar `config.yaml`: `plugins.enabled` inclui `ct2`
- Verificar `manifest.json` em `~/.hermes/plugins/ct2/`
- Reiniciar dashboard: `pm2 restart hermes-dashboard`

**Servidor CT2 não inicia:**
- Verificar porta 7890: `ss -tlnp | grep 7890`
- Logs: `pm2 logs ct2-server`
- Permissão de escrita no diretório `~/Dev/hermes-ct2/`

**Event Hooks não chegam:**
- Gateway reiniciado após adicionar hooks?
- CT2 server rodando? `curl localhost:7890/api/health`
- Verificar logs do gateway: `pm2 logs [agente]`

**Kanban vazio:**
- Rodar scan: `python ct2.py scan`
- Verificar se existem tasks no projeto
- `GET /api/tasks?project=seu-projeto`

## Licença

MIT
