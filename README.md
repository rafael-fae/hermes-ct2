# CT2 — Control Tower para Equipes Hermes Agent

Servidor API REST + MCP que centraliza o workflow de equipes multi-agente. Funciona como **plugin nativo do Hermes Dashboard**, substituindo planilhas e arquivos soltos por um sistema integrado de projetos, tasks, auditorias e scorecards.

## O que o CT2 faz

| Funcionalidade | Descrição |
|---------------|-----------|
| **📋 Tasks** | Visão multi-projeto com toggle 📋 Sprint (sprint→dia→wave) e 📅 Dia (tasks agrupadas por dia com detalhes: agente, motor, commit, auditoria) |
| **📊 Scorecards** | Métricas por agente: first-pass rate, rework, scope creep, taxa de aprovação |
| **🔍 Auditoria** | Timeline com hash de commits, veredito (aprovado/ressalva/rejeitado), scope creep |
| **📅 Diário** | Visão plana por dia em todos os projetos — filtros por projeto, status, agente, execução e auditoria |
| **🏢 Multi-projeto** | Suporte a múltiplos projetos simultâneos (oeste-gestao, control-tower-v2, agent-ops-workflow, etc.) |
| **🔗 Kanban→CT2 Sync** | Integração com Kanban Hermes: tasks concluídas no Kanban são automaticamente marcadas ✅👁 no CT2 |
| **🔄 Scanner** | Lê arquivos PLANO.md + task_XX.md do planejamento diário e popula o banco SQLite |
| **📡 Event Hooks** | Recebe `on_session_start` / `on_session_end` dos gateways Hermes |
| **🌐 GitHub Webhooks** | Sincroniza commits com tasks automaticamente |
| **🔧 MCP Server** | Integração nativa via Model Context Protocol |
| **📡 API REST** | CRUD completo de projetos, tasks, auditorias, scorecards |

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
uv sync

# Ou com pip
pip install -e .
```

### 3. Iniciar o servidor CT2

```bash
# Direto no terminal (para testar)
python3 ct2.py serve --port 7890

# Via PM2 (produção — recomendado)
pm2 start "python3 ct2.py serve --port 7890" \
  --name ct2-server \
  --cwd ~/Dev/hermes-ct2
pm2 save
```

Verificar se está rodando:
```bash
curl http://localhost:7890/api/health
# Deve retornar: {"status":"ok"}
```

### 4. Instalar os Plugins no Hermes Dashboard

O CT2 possui **dois plugins** que adicionam abas ao Dashboard:

#### Plugin 1: Scorecards (📊)

```bash
mkdir -p ~/.hermes/plugins/ct2/dist
cp ~/Dev/hermes-ct2/dashboard/manifest.json ~/.hermes/plugins/ct2/
cp ~/Dev/hermes-ct2/dashboard/dist/index.js ~/.hermes/plugins/ct2/dist/
```

#### Plugin 2: Tasks (📋) — NOVO v2

```bash
mkdir -p ~/.hermes/plugins/ct2-tasks/dist
cp ~/Dev/hermes-ct2/dashboard/tasks-manifest.json ~/.hermes/plugins/ct2-tasks/manifest.json
cp ~/Dev/hermes-ct2/dashboard/dist/tasks.js ~/.hermes/plugins/ct2-tasks/dist/index.js
```

### 5. Registrar os plugins no config.yaml

No arquivo `~/.hermes/config.yaml` (perfil `default`), adicionar:

```yaml
plugins:
  enabled:
    - ct2         # ← Aba Scorecards (📊)
    - ct2-tasks   # ← Aba Tasks (📋) — NOVO
```

### 6. Reiniciar o Dashboard

```bash
hermes gateway restart
```

As abas **📊 Scorecards** e **📋 Tasks** vão aparecer no menu do Dashboard.

## Funcionalidades — Detalhes

### 📋 Tasks — Visão Dupla (v2.0)

A aba **Tasks** oferece dois modos de visualização com um toggle no topo:

| Modo | Agrupamento | Descrição |
|------|------------|-----------|
| **📋 Sprint** | Sprint → Dia → Wave | Visão hierárquica tradicional, ideal para acompanhar progresso por sprint |
| **📅 Dia** | Dia → Projeto | Visão plana cronológica, multi-projeto, com detalhes completos de cada task |

**Filtros disponíveis:** projeto, status, agente, execução (✅/⬜), auditoria (👁/⬜)

**Detalhes exibidos por task:** número, título, agente, motor, módulo, sprint, wave, commit hash, data de conclusão, status de execução e auditoria.

### 📊 Scorecards

Métricas de performance por agente:
- **Total de tasks** atribuídas
- **Taxa de execução** (% concluídas)
- **Taxa de aprovação** (% aprovadas na primeira auditoria)
- **Scope creep** (% tasks com escopo expandido)
- **Rework** (tasks que precisaram de correção)
- **Tempo médio** de execução (quando disponível)

### 🔗 Integração Kanban → CT2

Quando uma task do Kanban Hermes é concluída, o sistema automaticamente:
1. Marca `status_execucao = ✅` no banco CT2
2. Cria registro de auditoria com `veredito = aprovado`
3. Marca `status_auditoria = 👁`

**Requisitos:** incluir `CT2: <task_number>` no body da task Kanban. O sync roda a cada 2 minutos via cron job.

### 🔄 Scanner de Planejamento

O scanner lê a estrutura de diretórios:

```
planejamento-diario/
├── sprint-1/
│   ├── 2026-06-01/
│   │   ├── PLANO.md       # ← Define sprint, waves, status
│   │   ├── task_01.md     # ← Detalhes da task
│   │   └── task_02.md
│   └── 2026-06-02/
│       └── ...
└── sprint-2/
    └── ...
```

Extrai automaticamente: sprint, day, wave, agent, motor, status, commit hash.

```bash
# Scan de todos os projetos
python3 ct2.py scan

# Scan de projeto específico
python3 ct2.py scan --project oeste-gestao

# Gerar dashboard HTML standalone
python3 ct2.py build
```

## API REST — Endpoints

### Projetos
```
GET  /api/projects                    # Listar todos os projetos
GET  /api/projects/<slug>             # Detalhes de um projeto
```

### Tasks
```
GET  /api/projects/<slug>/tasks       # Tasks de um projeto (com query params: ?limit=1000)
GET  /api/projects/<slug>/sprints     # Sprints de um projeto
```

### Auditorias
```
GET  /api/projects/<slug>/audits      # Histórico de auditorias
POST /api/audits                      # Registrar auditoria
```

### Scorecards
```
GET  /api/scorecards?days=30          # Métricas dos últimos 30 dias
GET  /api/scorecards?agent={nome}     # Filtrar por agente
```

### Event Hooks
```
POST /api/events                      # Receber eventos de sessão
```

### Health
```
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

Reiniciar os gateways: `hermes gateway restart`

## Estrutura de Diretórios

```
hermes-ct2/
├── src/
│   ├── server.py              # API REST (porta 7890)
│   ├── db.py                  # Schema SQLite com 10+ tabelas
│   └── mcp_server.py          # MCP Server nativo
├── dashboard/
│   ├── manifest.json          # Plugin Scorecards (📊)
│   ├── dist/index.js          # Frontend Scorecards
│   ├── tasks-manifest.json    # Plugin Tasks (📋) — NOVO v2
│   └── dist/tasks.js          # Frontend Tasks — NOVO v2
├── ct2.py                     # CLI: scan, build, serve, audit, db
└── README.md
```

## Comandos CLI

```bash
python3 ct2.py scan                          # Scan de todos os projetos (planejamento-diario → SQLite)
python3 ct2.py scan --project <slug>         # Scan de projeto específico
python3 ct2.py build                         # Gerar dashboard HTML standalone
python3 ct2.py serve --port 7890             # Iniciar servidor API REST
python3 ct2.py project list                  # Listar projetos cadastrados
python3 ct2.py project add <path>            # Adicionar novo projeto
python3 ct2.py project scan <slug>           # Re-scan de um projeto
python3 ct2.py task start <projeto> <id>     # Iniciar task
python3 ct2.py task done <projeto> <id> --hash <sha>  # Marcar task concluída
python3 ct2.py task audit <projeto> <id> --veredito <aprovado|rejeitado>  # Auditar task
python3 ct2.py task next <projeto>           # Mostrar próxima task pendente
python3 ct2.py briefing <projeto>            # Gerar briefing do projeto
python3 ct2.py github sync <projeto>         # Sincronizar dados do GitHub
```

## Troubleshooting

**Plugin não aparece no Dashboard:**
- Verificar `config.yaml`: `plugins.enabled` inclui `ct2` e `ct2-tasks`
- Verificar se os manifests estão em `~/.hermes/plugins/ct2/` e `~/.hermes/plugins/ct2-tasks/`
- Reiniciar gateway: `hermes gateway restart`

**Servidor CT2 não inicia:**
- Verificar porta 7890: `ss -tlnp | grep 7890`
- Logs: `pm2 logs ct2-server`
- Permissão de escrita no diretório `~/Dev/hermes-ct2/`

**Tasks não aparecem na aba 📋:**
- Rodar scan: `python3 ct2.py scan`
- Verificar se existem tasks no projeto: `curl localhost:7890/api/projects/<slug>/tasks`
- Verificar se os diretórios `planejamento-diario/sprint-*/` têm `PLANO.md` + `task_XX.md`

**Dados aparecem com "-" ou "NaN":**
- Verificar se o `PLANO.md` existe para a data (scanner precisa dele para extrair sprint/wave)
- Rodar `python3 ct2.py scan --project <slug>` para re-scan

**Kanban→CT2 sync não funciona:**
- Verificar se a task Kanban tem `CT2: <task_number>` no body
- Verificar cron job: `hermes cron list` (deve ter job `Kanban→CT2 Sync`)
- Rodar sync manual: `python3 ~/.hermes/scripts/kanban_ct2_sync.py`

## Licença

MIT
