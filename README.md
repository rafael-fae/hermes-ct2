# CT2 — Control Tower para Equipes Hermes Agent

CT2 é um servidor API REST + MCP que centraliza o workflow de equipes multi-agente. Funciona como um plugin do [Hermes Dashboard](https://hermes.usuario.com).

## Funcionalidades

- **API REST** — CRUD de projetos, tasks, auditorias, scorecards
- **Kanban** — Board com colunas Planejado/Executado/Auditado
- **Auditoria** — Timeline de auditorias com hash de commits
- **Scorecards** — Métricas de performance por agente
- **MCP Server** — Integração nativa via Model Context Protocol
- **Event Hooks** — Recebe eventos de sessão dos gateways
- **GitHub Webhooks** — Sincronização com repositórios

## Instalação Rápida

```bash
git clone https://github.com/[SEU_USUARIO]/hermes-ct2.git ~/Dev/hermes-ct2
cd ~/Dev/hermes-ct2
uv pip install -e .
python -m ct2 serve --port 7890
```

## Instalar como Plugin do Hermes Dashboard

```bash
cp -r dashboard/* ~/.hermes/plugins/ct2/
```

## Estrutura

```
hermes-ct2/
├── src/
│   ├── server.py      # API REST (FastAPI)
│   ├── db.py           # Banco SQLite
│   └── mcp_server.py   # MCP Server
├── dashboard/
│   ├── manifest.json   # Plugin manifest
│   └── dist/index.js   # Plugin frontend
├── ct2.py              # CLI
└── README.md
```

## Licença

MIT
