"""API REST JSON do Control Tower V2, implementada apenas com a stdlib."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

try:  # Permite tanto ``python src/server.py`` quanto import como pacote.
    from .db import get_agent_status, get_connection, get_project_by_slug, get_scorecards
except ImportError:
    from db import get_agent_status, get_connection, get_project_by_slug, get_scorecards


ALLOWED_ORIGIN = "http://localhost:9119"
VALID_TASK_STATUSES = {"todo", "in_progress", "done", "blocked"}
DEFAULT_AUDIT_LIMIT = 10
MAX_LIMIT = 1000
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
TASK_REF_ID_PATTERN = re.compile(r"hermes_kanban_id\s*[=:]\s*(\d+)", re.IGNORECASE)
TASK_FILE_PATTERN = re.compile(r"task[_\s](\d+)", re.IGNORECASE)


def _iso_value(value: Any) -> Any:
    """Converte valores de data SQLite/Python para uma representação ISO 8601."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if not isinstance(value, str):
        return value

    candidate = value.strip()
    if not candidate:
        return value
    try:
        if len(candidate) == 10:
            return date.fromisoformat(candidate).isoformat()
        # SQLite normalmente usa um espaço entre data e hora.
        return datetime.fromisoformat(candidate).isoformat()
    except ValueError:
        return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, sqlite3.Row):
        value = dict(value)
    if isinstance(value, dict):
        return {
            key: _json_ready(_iso_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return _iso_value(value)


def _single_query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    return values[-1] if values else None


def _positive_int(
    query: dict[str, list[str]], name: str, default: int | None = None
) -> int | None:
    raw = _single_query_value(query, name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"parâmetro '{name}' deve ser um número inteiro") from exc
    if value <= 0:
        raise ValueError(f"parâmetro '{name}' deve ser maior que zero")
    return value


def _verify_github_signature(
    payload_body: bytes, signature_header: str
) -> bool:
    """Verifica assinatura HMAC-SHA256 do webhook do GitHub."""
    if not GITHUB_WEBHOOK_SECRET:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256="):], expected)


def _find_task_by_ref(body_text: str) -> int | None:
    """Busca referência a task ID no texto de PR/check_run."""
    m = TASK_REF_ID_PATTERN.search(body_text or "")
    if m:
        task_id = int(m.group(1))
        return task_id
    m = TASK_FILE_PATTERN.search(body_text or "")
    if m:
        task_num = int(m.group(1))
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE task_file LIKE ? LIMIT 1",
                (f"%task_{task_num}%",),
            ).fetchone()
            if row:
                return row["id"]
    return None


def _match_task_by_branch(branch: str) -> int | None:
    """Tenta associar branch a uma task pelo nome."""
    if not branch:
        return None
    m = TASK_FILE_PATTERN.search(branch)
    if m:
        task_num = int(m.group(1))
        with closing(get_connection()) as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE task_file LIKE ? LIMIT 1",
                (f"%task_{task_num}%",),
            ).fetchone()
            if row:
                return row["id"]
    return None


class ControlTowerHandler(BaseHTTPRequestHandler):
    """Handler HTTP sem estado; cada requisição abre sua própria conexão SQLite."""

    server_version = "ControlTowerV2/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        # O acesso é registrado por _send_json no formato exigido pela API.
        return

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(
            _json_ready(payload), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        path = urlsplit(self.path).path
        print(f"{self.command} {path} \u2192 {status}", flush=True)

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def do_OPTIONS(self) -> None:
        self._send_json(200, {})

    def do_GET(self) -> None:
        try:
            self._route_get()
        except ValueError as exc:
            self._error(400, str(exc))
        except sqlite3.Error as exc:
            print(f"erro SQLite: {exc}", file=sys.stderr, flush=True)
            self._error(500, "erro interno do servidor")
        except Exception as exc:  # Mantém todo erro HTTP no formato JSON.
            print(f"erro inesperado: {exc}", file=sys.stderr, flush=True)
            self._error(500, "erro interno do servidor")

    VALID_EVENTS = frozenset({
        "session_start", "session_end", "tool_call",
        "task_complete", "task_failed",
    })

    def do_POST(self) -> None:
        try:
            self._route_post()
        except ValueError as exc:
            self._error(400, str(exc))
        except sqlite3.Error as exc:
            print(f"erro SQLite: {exc}", file=sys.stderr, flush=True)
            self._error(500, "erro interno do servidor")
        except Exception as exc:
            print(f"erro inesperado: {exc}", file=sys.stderr, flush=True)
            self._error(500, "erro interno do servidor")

    def _route_post(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/events":
            self._post_event()
            return
        if path == "/api/github-webhook":
            self._post_github_webhook()
            return
        if path == "/api/kanban-callback":
            self._post_kanban_callback()
            return
        self._error(404, "endpoint não encontrado")

    def _post_event(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._error(400, "corpo da requisição é obrigatório")
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "JSON inválido")
            return
        event_type = body.get("event") or body.get("event_type")
        if not event_type:
            self._error(400, "campo 'event' é obrigatório")
            return
        if event_type not in self.VALID_EVENTS:
            self._error(400, f"tipo de evento inválido: {event_type}")
            return
        profile = body.get("profile")
        if not profile:
            self._error(400, "campo 'profile' é obrigatório")
            return
        event_id = body.get("event_id")
        if not event_id:
            event_id = hashlib.sha256(
                json.dumps(body, sort_keys=True).encode()
            ).hexdigest()[:16]
        detail = {**body, "event_id": event_id}
        detail_str = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
        with closing(get_connection()) as conn:
            existing = conn.execute(
                "SELECT id FROM audit_log WHERE actor = ? AND action = ? AND detail LIKE ?",
                (profile, event_type, f"%{event_id}%"),
            ).fetchone()
            if existing:
                self._send_json(200, {"status": "ok", "event_id": event_id, "duplicate": True})
                return
            conn.execute(
                "INSERT INTO audit_log (actor, action, project_slug, entity_type, detail) "
                "VALUES (?, ?, ?, 'event', ?)",
                (profile, event_type, body.get("project_slug", ""), detail_str),
            )
            conn.commit()
        self._send_json(200, {"status": "ok", "event_id": event_id})

    def _post_github_webhook(self) -> None:
        """Processa webhooks do GitHub (HMAC validation + PR/CI)."""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._error(400, "corpo da requisição é obrigatório")
            return

        # Lê body raw para HMAC validation
        raw_body = self.rfile.read(length)

        # HMAC validation
        signature = self.headers.get("X-Hub-Signature-256", "")
        if not _verify_github_signature(raw_body, signature):
            self._send_json(401, {"error": "assinatura inválida"})
            return

        # Parse JSON
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "JSON inválido")
            return

        event = self.headers.get("X-GitHub-Event", "")
        action = body.get("action", "")

        if event == "ping":
            self._send_json(200, {"status": "ok", "event": "ping"})
            return

        detail = {
            "event": event,
            "action": action,
            "repository": body.get("repository", {}).get("full_name", ""),
        }
        task_id = None

        if event == "pull_request" and action == "opened":
            pr = body.get("pull_request", {})
            pr_number = pr.get("number")
            pr_title = pr.get("title", "")
            pr_body = pr.get("body", "")
            branch = pr.get("head", {}).get("ref", "")

            detail["pr_number"] = pr_number
            detail["pr_title"] = pr_title
            detail["branch"] = branch

            task_id = _find_task_by_ref(pr_body)
            if task_id is None:
                task_id = _find_task_by_ref(pr_title)
            if task_id is None:
                task_id = _match_task_by_branch(branch)

            if task_id:
                with closing(get_connection()) as conn:
                    conn.execute(
                        "UPDATE tasks SET status = 'in_progress', "
                        "gh_issue_number = ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (pr_number, task_id),
                    )
                    conn.commit()
                detail["task_id"] = task_id
                detail["new_status"] = "in_progress"

        elif event == "check_run" and action == "completed":
            cr = body.get("check_run", {})
            conclusion = cr.get("conclusion", "")
            cr_name = cr.get("name", "")
            output = cr.get("output", {})
            output_text = (
                (output.get("title") or "")
                + " "
                + (output.get("summary") or "")
                + " "
                + (output.get("text") or "")
            )

            detail["check_name"] = cr_name
            detail["conclusion"] = conclusion

            task_id = _find_task_by_ref(output_text)

            if task_id:
                new_status = "done" if conclusion == "success" else "in_progress"
                with closing(get_connection()) as conn:
                    conn.execute(
                        "UPDATE tasks SET status = ?, updated_at = datetime('now') "
                        "WHERE id = ?",
                        (new_status, task_id),
                    )
                    conn.commit()
                detail["task_id"] = task_id
                detail["new_status"] = new_status

        # Log in audit_log
        action_log = f"webhook_{event}"
        detail_str = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
        with closing(get_connection()) as conn:
            conn.execute(
                "INSERT INTO audit_log (actor, action, project_slug, entity_type, detail) "
                "VALUES ('github', ?, '', 'webhook', ?)",
                (action_log, detail_str),
            )
            conn.commit()

        self._send_json(200, {
            "status": "ok", "event": event, "action": action,
            "task_id": task_id,
        })

    def _post_kanban_callback(self) -> None:
        """Recebe callback do Kanban Hermes quando uma task é concluída/auditada.

        Atualiza status_execucao (✅), status (done), data_conclusao e,
        se houver dados de auditoria, insere na tabela auditorias e
        atualiza status_auditoria (👁).

        Payload esperado (um ou dois eventos no mesmo JSON):
        {
          "event": "task_complete" | "task_audited",
          "project_slug": "...",
          "task_number": 103,
          "agent": "Navani",
          "commit_hash": "abc1234",
          "audit": {                       # opcional, presente em task_audited
            "veredito": "aprovado",
            "auditor": "Dalinar",
            "observacoes": "...",
            "ressalva": "...",
            "scope_creep": 0,
            "task_file_ok": 1,
            "indices_ok": 1,
            "diff_hash": "...",
            "audit_hash": "..."
          }
        }
        """
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._error(400, "corpo da requisição é obrigatório")
            return
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._error(400, "JSON inválido")
            return

        event = body.get("event")
        if not event:
            self._error(400, "campo 'event' é obrigatório")
            return
        if event not in ("task_complete", "task_audited"):
            self._error(
                400,
                f"evento inválido: '{event}'. Use 'task_complete' ou 'task_audited'",
            )
            return

        project_slug = body.get("project_slug")
        task_number = body.get("task_number")
        if not project_slug:
            self._error(400, "campo 'project_slug' é obrigatório")
            return
        if not task_number:
            self._error(400, "campo 'task_number' é obrigatório")
            return
        try:
            task_number = int(task_number)
        except (ValueError, TypeError):
            self._error(400, "task_number deve ser um número inteiro")
            return

        commit_hash = body.get("commit_hash")
        audit_data = body.get("audit") if event == "task_audited" else None

        with closing(get_connection()) as conn:
            # Look up project
            project = get_project_by_slug(conn, project_slug)
            if project is None:
                self._error(404, f"projeto '{project_slug}' não encontrado")
                return

            # Look up task by project_id + task_number
            task = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND task_number = ?",
                (project["id"], task_number),
            ).fetchone()
            if task is None:
                self._error(
                    404,
                    f"task {task_number} não encontrada no projeto '{project_slug}'",
                )
                return

            updates = {}
            params_update: list[Any] = []

            if event == "task_complete":
                updates["status_execucao"] = "✅"
                updates["status"] = "done"
                updates["data_conclusao"] = "date('now')"
                if commit_hash:
                    updates["commit_hash"] = commit_hash

            if event == "task_audited":
                updates["status_auditoria"] = "👁"
                # Build audit insert
                auditor = (
                    audit_data.get("auditor") or "Dalinar"
                    if audit_data
                    else "Dalinar"
                )
                veredito = (
                    audit_data.get("veredito") or "aprovado"
                    if audit_data
                    else "aprovado"
                )
                if veredito not in ("aprovado", "aprovado_ressalva", "rejeitado"):
                    self._error(
                        400,
                        f"veredito inválido: '{veredito}'. "
                        "Use 'aprovado', 'aprovado_ressalva' ou 'rejeitado'",
                    )
                    return

                fallback_diff = hashlib.sha256(
                    f"{task['id']}-{datetime.now(timezone.utc).isoformat()}".encode()
                ).hexdigest()[:16]
                diff_hash = (
                    audit_data.get("diff_hash") or fallback_diff
                    if audit_data
                    else fallback_diff
                )
                fallback_audit = hashlib.sha256(
                    f"audit-{task['id']}-{diff_hash}-{datetime.now(timezone.utc).isoformat()}".encode()
                ).hexdigest()[:16]
                audit_hash_val = (
                    audit_data.get("audit_hash") or fallback_audit
                    if audit_data
                    else fallback_audit
                )

                scope_creep = audit_data.get("scope_creep", 0) if audit_data else 0
                task_file_ok = audit_data.get("task_file_ok", 0) if audit_data else 0
                indices_ok = audit_data.get("indices_ok", 0) if audit_data else 0
                ressalva = audit_data.get("ressalva") if audit_data else None
                observacoes = audit_data.get("observacoes") if audit_data else None

                # Check for duplicate audit
                existing_audit = conn.execute(
                    "SELECT id FROM auditorias WHERE task_id = ? AND audit_hash = ?",
                    (task["id"], audit_hash_val),
                ).fetchone()
                if existing_audit:
                    self._send_json(
                        200,
                        {
                            "status": "ok",
                            "event": event,
                            "task_id": task["id"],
                            "duplicate": True,
                            "message": "auditoria já registrada para esta task",
                        },
                    )
                    return

                conn.execute(
                    "INSERT INTO auditorias "
                    "(task_id, auditor, veredito, ressalva, scope_creep, "
                    "task_file_ok, indices_ok, diff_hash, audit_hash, observacoes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task["id"],
                        auditor,
                        veredito,
                        ressalva,
                        int(scope_creep),
                        int(task_file_ok),
                        int(indices_ok),
                        diff_hash,
                        audit_hash_val,
                        observacoes,
                    ),
                )

            # Apply updates to tasks table
            if updates:
                set_clause = ", ".join(
                    f"{col} = {val}" if "(" in val else f"{col} = ?"
                    for col, val in updates.items()
                )
                params_update = [
                    v for v in updates.values() if "(" not in v
                ] + [task["id"]]
                conn.execute(
                    f"UPDATE tasks SET {set_clause}, "
                    f"updated_at = datetime('now') "
                    f"WHERE id = ?",
                    params_update,
                )

            conn.commit()

        self._send_json(
            200,
            {
                "status": "ok",
                "event": event,
                "task_id": task["id"],
                "task_number": task_number,
                "project_slug": project_slug,
            },
        )

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def _route_get(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query, keep_blank_values=True)
        parts = [unquote(part) for part in path.split("/") if part]

        if path == "/api/health":
            self._send_json(
                200,
                {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()},
            )
            return
        if path == "/api/projects":
            self._projects()
            return
        if path == "/api/agents":
            self._agents()
            return
        if path == "/api/stats":
            self._stats()
            return
        if len(parts) == 4 and parts[:2] == ["api", "projects"]:
            slug, resource = parts[2], parts[3]
            if resource == "tasks":
                self._project_tasks(slug, query)
                return
            if resource == "sprints":
                self._project_sprints(slug)
                return
            if resource == "auditorias":
                self._project_audits(slug, query)
                return
        if len(parts) == 3 and parts[:2] == ["api", "tasks"]:
            try:
                task_id = int(parts[2])
            except ValueError:
                raise ValueError("id da task deve ser um número inteiro")
            if task_id <= 0:
                raise ValueError("id da task deve ser maior que zero")
            self._task_detail(task_id)
            return
        if path == "/api/scorecards":
            self._scorecards(query)
            return
        if path == "/scorecards":
            self._serve_html("scorecards.html")
            return

        self._error(404, "endpoint não encontrado")

    def _projects(self) -> None:
        with closing(get_connection()) as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.slug, p.name, p.stack, COUNT(t.id) AS task_count,
                       p.last_scan
                FROM projects p
                LEFT JOIN tasks t ON t.project_id = p.id
                GROUP BY p.id, p.slug, p.name, p.stack, p.last_scan
                ORDER BY p.name COLLATE NOCASE, p.slug
                """
            ).fetchall()
        self._send_json(200, [dict(row) for row in rows])

    def _project_tasks(
        self, slug: str, query: dict[str, list[str]]
    ) -> None:
        status = _single_query_value(query, "status")
        if status and status not in VALID_TASK_STATUSES:
            raise ValueError(
                "parâmetro 'status' deve ser todo, in_progress, done ou blocked"
            )
        sprint = _positive_int(query, "sprint")
        wave = _positive_int(query, "wave")
        limit = _positive_int(query, "limit")
        if limit is not None:
            limit = min(limit, MAX_LIMIT)

        clauses = ["p.slug = ?"]
        params: list[Any] = [slug]
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        if sprint is not None:
            clauses.append("s.number = ?")
            params.append(sprint)
        if wave is not None:
            clauses.append("w.wave_number = ?")
            params.append(wave)

        sql = f"""
            SELECT t.*, s.title AS sprint_name, s.number AS sprint_number,
                   w.wave_number
            FROM tasks t
            JOIN projects p ON p.id = t.project_id
            LEFT JOIN sprints s ON s.id = t.sprint_id
            LEFT JOIN waves w ON w.id = t.wave_id
            WHERE {' AND '.join(clauses)}
            ORDER BY t.created_at DESC, t.id DESC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with closing(get_connection()) as conn:
            if get_project_by_slug(conn, slug) is None:
                self._error(404, "projeto não encontrado")
                return
            rows = conn.execute(sql, params).fetchall()
        self._send_json(200, [dict(row) for row in rows])

    def _project_sprints(self, slug: str) -> None:
        with closing(get_connection()) as conn:
            if get_project_by_slug(conn, slug) is None:
                self._error(404, "projeto não encontrado")
                return
            rows = conn.execute(
                """
                SELECT s.*,
                       COUNT(t.id) AS total,
                       SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS done,
                       SUM(CASE WHEN t.status = 'todo' THEN 1 ELSE 0 END) AS todo
                FROM sprints s
                JOIN projects p ON p.id = s.project_id
                LEFT JOIN tasks t ON t.sprint_id = s.id
                WHERE p.slug = ?
                GROUP BY s.id
                ORDER BY s.number DESC, s.id DESC
                """,
                (slug,),
            ).fetchall()
        self._send_json(200, [dict(row) for row in rows])

    def _project_audits(
        self, slug: str, query: dict[str, list[str]]
    ) -> None:
        limit = min(
            _positive_int(query, "limit", DEFAULT_AUDIT_LIMIT)
            or DEFAULT_AUDIT_LIMIT,
            MAX_LIMIT,
        )
        with closing(get_connection()) as conn:
            if get_project_by_slug(conn, slug) is None:
                self._error(404, "projeto não encontrado")
                return
            rows = conn.execute(
                """
                SELECT a.*, t.title AS task_title, t.task_number,
                       t.project_slug
                FROM auditorias a
                JOIN tasks t ON t.id = a.task_id
                JOIN projects p ON p.id = t.project_id
                WHERE p.slug = ?
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT ?
                """,
                (slug, limit),
            ).fetchall()
        self._send_json(200, [dict(row) for row in rows])

    def _agents(self) -> None:
        with closing(get_connection()) as conn:
            agents = get_agent_status(conn)
        self._send_json(200, agents)

    def _task_detail(self, task_id: int) -> None:
        with closing(get_connection()) as conn:
            row = conn.execute(
                """
                SELECT t.*, s.title AS sprint_name, s.number AS sprint_number,
                       w.wave_number
                FROM tasks t
                LEFT JOIN sprints s ON s.id = t.sprint_id
                LEFT JOIN waves w ON w.id = t.wave_id
                WHERE t.id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                self._error(404, "task não encontrada")
                return
            audits = conn.execute(
                """
                SELECT * FROM auditorias
                WHERE task_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (task_id,),
            ).fetchall()
        result = dict(row)
        result["auditorias"] = [dict(audit) for audit in audits]
        self._send_json(200, result)

    def _stats(self) -> None:
        with closing(get_connection()) as conn:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM tasks GROUP BY status"
            ).fetchall()
            project_rows = conn.execute(
                """
                SELECT p.id, p.slug, p.name, COUNT(t.id) AS total,
                       SUM(CASE WHEN t.status = 'todo' THEN 1 ELSE 0 END) AS todo,
                       SUM(CASE WHEN t.status = 'in_progress' THEN 1 ELSE 0 END)
                           AS in_progress,
                       SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END) AS done,
                       SUM(CASE WHEN t.status = 'blocked' THEN 1 ELSE 0 END)
                           AS blocked
                FROM projects p
                LEFT JOIN tasks t ON t.project_id = p.id
                GROUP BY p.id, p.slug, p.name
                ORDER BY p.name COLLATE NOCASE, p.slug
                """
            ).fetchall()

        by_status = {status: 0 for status in sorted(VALID_TASK_STATUSES)}
        for row in status_rows:
            by_status[row["status"]] = row["total"]
        self._send_json(
            200,
            {
                "total_tasks": sum(by_status.values()),
                "tasks_by_status": by_status,
                "tasks_by_project": [dict(row) for row in project_rows],
            },
        )

    def _serve_html(self, filename):
        """Serve um arquivo HTML da pasta output/."""
        html_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "output",
        )
        filepath = os.path.join(html_dir, filename)
        if not os.path.isfile(filepath):
            self._error(404, f"página não encontrada: {filename}")
            return
        with open(filepath, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)
        print(f"GET /{filename} → 200", flush=True)

    def _scorecards(self, query):
        days = _positive_int(query, "days", 7)
        agent = _single_query_value(query, "agent")
        with closing(get_connection()) as conn:
            result = get_scorecards(conn, days, agent)
        self._send_json(200, result)


def run_server(host: str = "0.0.0.0", port: int = 7890) -> None:
    server = ThreadingHTTPServer((host, port), ControlTowerHandler)
    print(f"Control Tower V2 API em http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7890
    host = sys.argv[2] if len(sys.argv) > 2 else "0.0.0.0"
    run_server(host, port)
