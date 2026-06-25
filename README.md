# CT2 — Control Tower para Equipes Hermes Agent

Servidor API REST + MCP que centraliza o workflow de equipes multi-agente. Funciona como **plugin nativo do Hermes Dashboard**, substituindo planilhas e arquivos soltos por um sistema integrado de projetos, tasks, auditorias e scorecards.

## O que o CT2 faz

| Funcionalidade | Descrição |
|---------------|-----------|
| **📋 Tasks Detalhadas** | Tasks agrupadas por dia com colunas: #, Título, Status, Agente, Motor, Exec (✅/⬜), Audit (👁/⬜), Commit, Concluído |
| **🔍 Auditoria Automática** | Quando uma task Kanban conclui, o CT2 registra auditoria automaticamente (veredito, hash, observações) e marca 👁 |
| **📊 Scorecards** | Métricas por agente: first-pass rate, rework, scope creep, taxa de aprovação |
| **🏢 Multi-projeto** | Suporte a múltiplos projetos simultâneos |
| **🔗 Kanban→CT2 Sync** | Cron job a cada 2min: tasks concluídas no Kanban viram ✅👁 no CT2 |
| **🔄 Scanner** | Lê PLANO.md + task_XX.md do planejamento diário → SQLite |
| **📡 Event Hooks** | Recebe on_session_start / on_session_end dos gateways |
| **🌐 GitHub Webhooks** | Sincroniza commits com tasks |
| **🔧 MCP Server** | Integração nativa via Model Context Protocol |
| **📡 API REST** | CRUD completo de projetos, tasks, auditorias |

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
uv sync
# ou: pip install -e .
```

### 3. Iniciar o servidor CT2

```bash
python3 ct2.py serve --port 7890

# Via PM2 (produção)
pm2 start "python3 ct2.py serve --port 7890" --name ct2-server --cwd ~/Dev/hermes-ct2
pm2 save
```

Verificar: `curl http://localhost:7890/api/health` → `{"status":"ok"}`

### 4. Plugin do Dashboard

O plugin CT2 já vem integrado no Hermes Agent (`plugins/ct2/dashboard/`). Não requer instalação adicional. Basta o servidor CT2 estar rodando na porta 7890.

### 5. Reiniciar o Dashboard

```bash
hermes gateway restart --profile dalinar
```

## Funcionalidades

### 📋 Tasks — Visão Detalhada por Dia

A aba **Tasks** mostra todas as tasks agrupadas por **dia** (expansível), com colunas detalhadas:

| Coluna | Descrição |
|--------|-----------|
| **#** | Número da task (task_103) |
| **Título** | Descrição da task |
| **Status** | Todo / In Progress / Done / Blocked |
| **Agente** | Quem executou |
| **Motor** | Modelo LLM utilizado |
| **Exec** | ✅ (executada) / ⬜ (pendente) |
| **Audit** | 👁 (auditada) / ⬜ (pendente) |
| **Commit** | Hash do commit (7 chars) |
| **Concluído** | Data de conclusão |

### 🔍 Auditoria Automática (Kanban→CT2)

Quando uma task do Kanban Hermes é concluída:
1. O sync detecta o evento `completed` no Kanban
2. Marca `status_execucao = ✅` e `data_conclusao` no CT2
3. Cria registro em `auditorias` com `veredito = aprovado`
4. Marca `status_auditoria = 👁`

**Requisito:** incluir `CT2: <task_number>` no body da task Kanban.

### 📊 Scorecards

Métricas por agente: total de tasks, taxa de execução, first-pass rate, rework, scope creep.

### 🔄 Scanner

```bash
python3 ct2.py scan                    # Todos os projetos
python3 ct2.py scan --project <slug>   # Projeto específico
python3 ct2.py build                   # Dashboard HTML standalone
```

## API REST

```
GET  /api/projects                          # Listar projetos
GET  /api/projects/<slug>/tasks?limit=200   # Tasks do projeto
GET  /api/projects/<slug>/sprints           # Sprints
GET  /api/projects/<slug>/auditorias        # Auditorias
GET  /api/scorecards?days=30                # Scorecards
GET  /api/health                            # Health check
```

## Kanban→CT2 Sync

```bash
# Verificar cron job
hermes cron list | grep "Kanban→CT2"

# Sync manual
python3 ~/.hermes/profiles/dalinar/scripts/kanban_ct2_sync.py --force
```

## Comandos CLI

```bash
ct2.py scan                     # Scan de projetos
ct2.py build                    # Gerar dashboard HTML
ct2.py serve --port 7890        # Servidor API
ct2.py project list             # Listar projetos
ct2.py task start <proj> <id>   # Iniciar task
ct2.py task done <proj> <id> --hash <sha>  # Concluir task
ct2.py task audit <proj> <id> --veredito <aprovado|rejeitado>  # Auditar
ct2.py briefing <proj>          # Briefing do projeto
```

## Troubleshooting

**Tasks mostram "—" ou datas quebradas:**
- Rodar `python3 ct2.py scan --project <slug>` para atualizar o banco
- Verificar se PLANO.md existe para cada data

**Auditorias não aparecem:**
- Rodar sync manual: `python3 ~/.hermes/profiles/dalinar/scripts/kanban_ct2_sync.py --force`
- Verificar se a task Kanban tem `CT2: <task_number>` no body

**Dashboard não carrega:**
- CT2 server rodando? `curl localhost:7890/api/health`
- Gateway reiniciado? `hermes gateway restart --profile dalinar`
- F5/Ctrl+Shift+R no browser para limpar cache

## Licença

MIT
