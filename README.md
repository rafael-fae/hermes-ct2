# CT2 — Control Tower para Equipes Hermes Agent

Servidor API REST que centraliza o workflow de equipes multi-agente. Funciona como **plugin nativo do Hermes Dashboard**, substituindo planilhas e arquivos soltos por um sistema integrado de projetos, tasks, auditorias e scorecards.

## O que o CT2 faz

| Funcionalidade | Descrição |
|---------------|-----------|
| **📋 Tasks Detalhadas** | Tasks agrupadas por dia com colunas: #, Título, Status, Agente, Motor, Exec (✅/⬜), Audit (👁/⬜), Commit, Conclusão |
| **📄 Página de Detalhe** | Ao clicar em qualquer task, abre página HTML com markdown completo + metadados + auditorias |
| **🔍 Auditorias nas Tasks** | Cada task mostra suas auditorias na própria página de detalhe (seção 🔍 Auditorias). Aba de Auditorias removida — tudo fica na task |
| **📊 Scorecards** | Métricas por agente: first-pass rate, rework, scope creep, taxa de aprovação |
| **🏢 Multi-projeto** | Suporte a múltiplos projetos simultâneos (oeste-gestao, control-tower-v2, agent-ops-workflow) |
| **🔄 Scanner** | Lê PLANO.md + task_XX.md do planejamento diário → SQLite |
| **📡 Event Hooks** | Recebe on_session_start / on_session_end dos gateways |
| **🌐 GitHub Webhooks** | Sincroniza commits com tasks |
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

**Arquivos do plugin:**
- `dashboard/manifest.json` — registro do plugin no Dashboard
- `dashboard/plugin_api.py` — proxy FastAPI das rotas do CT2 (`/api/plugins/ct2/*`)
- `dashboard/dist/index.js` — frontend React (tasks, projetos, scorecards)
- `dashboard/dist/style.css` — tema escuro (DS Teal)

### 5. Reiniciar o Dashboard

```bash
pm2 restart hermes-dashboard
```

## Funcionalidades

### 📋 Tasks — Visão Detalhada por Dia

A aba **Tasks** mostra todas as tasks agrupadas por **dia** (expansível), com colunas detalhadas:

| Coluna | Descrição |
|--------|-----------|
| **#** | Número da task (ex: 103) |
| **Título** | Descrição da task (truncado em 55 chars) |
| **Status** | Todo / Done / Blocked |
| **Agente** | Quem executou |
| **Motor** | Modelo LLM utilizado |
| **Exec** | ✅ (executada) / ⬜ (pendente) |
| **Audit** | 👁 (auditada) / ⬜ (pendente) |
| **Commit** | Hash do commit (7 chars) |
| **Conclusão** | Data formatada: `DD-MM-AAAA - HH:MM` |

**Ordenação:** tasks em ordem decrescente por número (mais recentes primeiro).

### 📄 Página de Detalhe da Task

Ao **clicar** em qualquer task, abre uma página HTML completa contendo:

- **Metadados:** Status, Agente, Motor, Data de conclusão, Sprint, Commit
- **📄 Markdown:** Conteúdo completo do arquivo `task_XX.md` com formatação preservada
- **🔍 Auditorias:** Lista de todas as auditorias registradas para aquela task (veredito, hash, ressalvas, observações)

**URL:** `/tasks/<projeto>/<task_number>` (ex: `/tasks/oeste-gestao/102`)

**Proxy via Dashboard:** `/api/plugins/ct2/tasks/<projeto>/<task_number>`

### 🔍 Auditorias nas Tasks

As auditorias ficam na própria página de detalhe da task — **não há aba separada de Auditorias**.

**Regras para preenchimento correto:**
1. Toda task executada (`status_execucao=✅`) DEVE ter uma auditoria
2. Auditor Dalinar audita código + preenche `task_XX.md` + registra no banco CT2
3. `status_auditoria` vira `👁` automaticamente ao registrar auditoria
4. Hash da auditoria (`audit_hash`) é gerado a partir dos dados da task + veredito

### 📊 Scorecards

Métricas por agente: total de tasks, first-pass rate, rework, scope creep, taxa de aprovação.

Endpoint: `GET /api/scorecards?days=30&agent=Jasnah`

### 🔄 Scanner

```bash
python3 ct2.py scan                    # Todos os projetos
python3 ct2.py scan --project <slug>   # Projeto específico
```

## API REST

```
GET  /api/projects                              # Listar projetos
GET  /api/projects/<slug>/tasks?status=done     # Tasks do projeto (filtro por status)
GET  /api/projects/<slug>/tasks?limit=200       # Tasks com limite
GET  /api/projects/<slug>/sprints               # Sprints
GET  /api/projects/<slug>/auditorias            # Auditorias (JSON)
GET  /api/projects/<slug>/<id>/tasks/md         # Conteúdo markdown da task (JSON)
GET  /api/scorecards?days=30                    # Scorecards
GET  /api/health                                # Health check

# Páginas HTML
GET  /tasks/<slug>/<task_number>                # Página de detalhe da task
GET  /auditorias/<id>                           # Página de detalhe da auditoria
```

## Comandos CLI

```bash
ct2.py scan                     # Scan de projetos
ct2.py serve --port 7890        # Servidor API
ct2.py project list             # Listar projetos
ct2.py task start <proj> <id>   # Iniciar task
ct2.py task done <proj> <id> --hash <sha>  # Concluir task
ct2.py task audit <proj> <id> --veredito <aprovado|rejeitado>  # Auditar
ct2.py briefing <proj>          # Briefing do projeto
```

## Formato de Data

Todas as datas de conclusão seguem o padrão **`DD-MM-AAAA - HH:MM`** (minutos sempre com 2 dígitos, zero-padded).

Exemplos: `25-06-2026 - 14:05`, `17-06-2026 - 08:30`

## Troubleshooting

**Tasks sem conteúdo ao clicar:**
- Arquivo `.md` não existe na pasta `planejamento-diario/<data>/`
- Criar o arquivo ou rodar `ct2.py scan` para detectar

**Auditorias não aparecem nas tasks:**
- Rodar auditoria: `ct2.py task audit <proj> <id> --veredito aprovado --hash <sha>`
- Ou registrar manualmente no banco via SQLite

**Dashboard não carrega:**
- CT2 server rodando? `curl localhost:7890/api/health`
- Dashboard reiniciado? `pm2 restart hermes-dashboard`
- F5/Ctrl+Shift+R no browser para limpar cache

## Licença

MIT
