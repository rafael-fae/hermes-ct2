"""
src/mcp_server.py — CT2 como MCP server (stdio, JSON-RPC 2.0) — F8.

Expõe o Control Tower V2 como *tools* para agentes (Claude/Codex) consumirem
nativamente, em vez de scraping de CLI. Stdlib pura, sem SDK externo.

Rodar:  uv run python -m src.mcp_server
Config do cliente MCP (ex.: ~/.codex ou Claude):
  {"mcpServers": {"control-tower-v2": {
     "command": "uv",
     "args": ["run", "python", "-m", "src.mcp_server"],
     "cwd": "/Users/rafaelfae/Dev/control-tower-v2"}}}
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import get_agent_status, get_connection, get_project_by_slug  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {"name": "ct2_status", "description": "Resumo: nº de projetos e tasks.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ct2_next_task", "description": "Próxima task pendente de um projeto.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"}},
                     "required": ["project"]}},
    {"name": "ct2_list_tasks", "description": "Lista tasks (status: todo|done|all).",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"},
                                    "status": {"type": "string"}},
                     "required": ["project"]}},
    {"name": "ct2_briefing", "description": "Briefing markdown de um projeto.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"}},
                     "required": ["project"]}},
    {"name": "ct2_task_done", "description": "Marca task como executada com hash de commit.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"},
                                    "id": {"type": "integer"},
                                    "hash": {"type": "string"}},
                     "required": ["project", "id", "hash"]}},
    {"name": "ct2_project_tasks", "description": "Tasks de um projeto.",
     "inputSchema": {"type": "object",
                     "properties": {"slug": {"type": "string"},
                                    "status": {"type": "string",
                                               "enum": ["todo", "done", "all"]}},
                     "required": ["slug"]}},
    {"name": "ct2_sprint_status", "description": "Status de uma sprint de um projeto.",
     "inputSchema": {"type": "object",
                     "properties": {"slug": {"type": "string"},
                                    "sprint": {"type": "integer"}},
                     "required": ["slug", "sprint"]}},
    {"name": "ct2_audit_trail", "description": "Últimas auditorias de um projeto.",
     "inputSchema": {"type": "object",
                     "properties": {"slug": {"type": "string"},
                                    "limit": {"type": "integer", "default": 10,
                                              "minimum": 1}},
                     "required": ["slug"]}},
    {"name": "ct2_agent_status", "description": "Health dos agentes.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ct2_briefing_by_slug", "description": "Briefing markdown de um projeto por slug.",
     "inputSchema": {"type": "object",
                     "properties": {"slug": {"type": "string"}},
                     "required": ["slug"]}},
    {"name": "ct2_stats", "description": "Resumo geral completo do Control Tower V2.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def list_tools():
    return TOOLS


def _text(s):
    return [{"type": "text", "text": s}]


def call_tool(conn, name, args):
    args = args or {}
    if name == "ct2_status":
        p = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        t = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return _text(json.dumps({"projects": p, "tasks": t}, ensure_ascii=False))

    if name == "ct2_next_task":
        proj = get_project_by_slug(conn, args.get("project", ""))
        if not proj:
            return _text("Projeto não encontrado.")
        row = conn.execute(
            "SELECT task_number, title, agent, motor FROM tasks "
            "WHERE project_id=? AND (status_execucao IS NULL OR status_execucao='⬜') "
            "ORDER BY task_number ASC LIMIT 1", (proj["id"],)).fetchone()
        if not row:
            return _text(json.dumps({"done": True}, ensure_ascii=False))
        return _text(json.dumps(dict(row), ensure_ascii=False))

    if name == "ct2_list_tasks":
        proj = get_project_by_slug(conn, args.get("project", ""))
        if not proj:
            return _text("Projeto não encontrado.")
        q = ("SELECT task_number, title, agent, status, status_execucao, status_auditoria "
             "FROM tasks WHERE project_id=?")
        if args.get("status") == "todo":
            q += " AND (status_execucao IS NULL OR status_execucao='⬜')"
        elif args.get("status") == "done":
            q += " AND status_execucao='✅'"
        q += " ORDER BY task_number"
        rows = [dict(r) for r in conn.execute(q, (proj["id"],)).fetchall()]
        return _text(json.dumps(rows, ensure_ascii=False))

    if name == "ct2_briefing":
        from src.briefing import generate_briefing
        return _text(generate_briefing(args.get("project", "")))

    if name == "ct2_task_done":
        proj = get_project_by_slug(conn, args.get("project", ""))
        if not proj:
            return _text("Projeto não encontrado.")
        h = str(args.get("hash", ""))
        if not re.match(r'^[a-f0-9]{7,40}$', h):
            return _text("Hash inválido (SHA hex de 7-40 caracteres).")
        cur = conn.execute(
            "UPDATE tasks SET status='done', status_execucao='✅', commit_hash=?, "
            "updated_at=datetime('now') WHERE project_id=? AND task_number=?",
            (h, proj["id"], int(args.get("id"))))
        conn.commit()
        if cur.rowcount == 0:
            return _text("Task não encontrada.")
        return _text(json.dumps({"ok": True, "task": int(args.get("id"))}, ensure_ascii=False))

    if name == "ct2_project_tasks":
        proj = get_project_by_slug(conn, args.get("slug", ""))
        if not proj:
            return _text("Projeto não encontrado.")
        status = args.get("status", "all")
        if status not in ("todo", "done", "all"):
            raise ValueError("status deve ser todo, done ou all")
        q = ("SELECT t.task_number, t.title, t.status, t.status_execucao, "
             "t.agent, t.motor, t.priority, s.number AS sprint_number, "
             "w.wave_number FROM tasks t "
             "LEFT JOIN sprints s ON s.id=t.sprint_id "
             "LEFT JOIN waves w ON w.id=t.wave_id WHERE t.project_id=?")
        if status == "todo":
            q += " AND (t.status_execucao IS NULL OR t.status_execucao='⬜')"
        elif status == "done":
            q += " AND t.status_execucao='✅'"
        q += " ORDER BY t.task_number"
        rows = [dict(r) for r in conn.execute(q, (proj["id"],)).fetchall()]
        return _text(json.dumps(rows, ensure_ascii=False))

    if name == "ct2_sprint_status":
        proj = get_project_by_slug(conn, args.get("slug", ""))
        if not proj:
            return _text("Projeto não encontrado.")
        sprint_number = int(args.get("sprint"))
        row = conn.execute(
            "SELECT s.number AS sprint_number, s.title AS sprint_title, s.status, "
            "COUNT(t.id) AS total_tasks, "
            "SUM(CASE WHEN t.status='todo' THEN 1 ELSE 0 END) AS todo, "
            "SUM(CASE WHEN t.status='in_progress' THEN 1 ELSE 0 END) AS in_progress, "
            "SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS done, "
            "SUM(CASE WHEN t.status='blocked' THEN 1 ELSE 0 END) AS blocked "
            "FROM sprints s LEFT JOIN tasks t ON t.sprint_id=s.id "
            "WHERE s.project_id=? AND s.number=? GROUP BY s.id",
            (proj["id"], sprint_number)).fetchone()
        if not row:
            return _text("Sprint não encontrada.")
        return _text(json.dumps(dict(row), ensure_ascii=False))

    if name == "ct2_audit_trail":
        proj = get_project_by_slug(conn, args.get("slug", ""))
        if not proj:
            return _text("Projeto não encontrado.")
        limit = int(args.get("limit", 10))
        if limit < 1:
            raise ValueError("limit deve ser maior que zero")
        rows = conn.execute(
            "SELECT a.id, t.task_number, t.title AS task_title, a.auditor, "
            "a.veredito, a.ressalva, a.created_at FROM auditorias a "
            "JOIN tasks t ON t.id=a.task_id WHERE t.project_id=? "
            "ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
            (proj["id"], limit)).fetchall()
        return _text(json.dumps([dict(r) for r in rows], ensure_ascii=False))

    if name == "ct2_agent_status":
        agents = [
            {key: agent.get(key) for key in
             ("agent", "status", "motor", "current_task_title", "updated_at")}
            for agent in get_agent_status(conn)
        ]
        return _text(json.dumps(agents, ensure_ascii=False))

    if name == "ct2_briefing_by_slug":
        from src.briefing import generate_briefing
        return _text(generate_briefing(args.get("slug", "")))

    if name == "ct2_stats":
        totals = conn.execute(
            "SELECT COUNT(*) AS total_tasks, "
            "SUM(CASE WHEN status='todo' THEN 1 ELSE 0 END) AS todo, "
            "SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done, "
            "SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) AS in_progress, "
            "SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) AS blocked "
            "FROM tasks").fetchone()
        projects = conn.execute(
            "SELECT p.slug, p.name, COUNT(t.id) AS total, "
            "SUM(CASE WHEN t.status='todo' THEN 1 ELSE 0 END) AS todo, "
            "SUM(CASE WHEN t.status='done' THEN 1 ELSE 0 END) AS done, "
            "SUM(CASE WHEN t.status='in_progress' THEN 1 ELSE 0 END) AS in_progress, "
            "SUM(CASE WHEN t.status='blocked' THEN 1 ELSE 0 END) AS blocked "
            "FROM projects p LEFT JOIN tasks t ON t.project_id=p.id "
            "GROUP BY p.id, p.slug, p.name ORDER BY p.name COLLATE NOCASE, p.slug"
        ).fetchall()
        agents = [
            {key: agent.get(key) for key in
             ("agent", "status", "motor", "current_task_title", "updated_at")}
            for agent in get_agent_status(conn)
        ]
        result = {
            "total_projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "total_tasks": totals["total_tasks"],
            "tasks_by_status": {
                key: totals[key] or 0
                for key in ("todo", "done", "in_progress", "blocked")
            },
            "tasks_by_project": [dict(row) for row in projects],
            "agents": agents,
        }
        return _text(json.dumps(result, ensure_ascii=False))

    raise ValueError("tool desconhecida: {}".format(name))


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_rpc(req, conn):
    method = req.get("method")
    rid = req.get("id")
    if method == "initialize":
        return _result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "control-tower-v2", "version": "2.0.0"},
        })
    if method == "tools/list":
        return _result(rid, {"tools": list_tools()})
    if method == "tools/call":
        params = req.get("params", {}) or {}
        try:
            content = call_tool(conn, params.get("name"), params.get("arguments"))
            return _result(rid, {"content": content})
        except Exception as e:  # erro de tool vira resultado isError, não crash
            return _result(rid, {"content": _text("Erro: {}".format(e)), "isError": True})
    if method and method.startswith("notifications/"):
        return None  # notifications JSON-RPC não têm resposta
    return _error(rid, -32601, "Método não encontrado: {}".format(method))


def serve_stdio():
    conn = get_connection()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        resp = handle_rpc(req, conn)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
