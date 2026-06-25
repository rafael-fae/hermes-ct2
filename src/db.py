"""
src/db.py — SQLite Schema + Connection Manager + Database Class

Schema completo do Control Tower V2 com 10 tabelas:
projects, sprints, waves, tasks, task_notes, sessions, version_history,
auditorias, agent_status, audit_log

Connection manager com WAL mode, FK enforcement e busy_timeout 5s.
"""

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

# ─── Caminho do banco ────────────────────────────────────────────────

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state", "ct2.db"
)


# ─── Connection Manager ───────────────────────────────────────────────

def get_connection(db_path=None):
    """Retorna conexão SQLite configurada (WAL, FK, busy_timeout)."""
    path = db_path or DEFAULT_DB_PATH
    # Garante que o diretório existe
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ─── Schema DDL ───────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Projects
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    repo_url TEXT,
    stack TEXT,
    last_scan TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sprints
CREATE TABLE IF NOT EXISTS sprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    number INTEGER NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'completed')),
    day_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Waves (parallel execution batches)
CREATE TABLE IF NOT EXISTS waves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    sprint_id INTEGER REFERENCES sprints(id),
    wave_number INTEGER NOT NULL,
    date TEXT NOT NULL DEFAULT (date('now')),
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'completed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sprint Dates (mapeia data → sprint para lookup rápido)
CREATE TABLE IF NOT EXISTS sprint_dates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id INTEGER NOT NULL REFERENCES sprints(id),
    date TEXT NOT NULL,
    day_number INTEGER NOT NULL,
    UNIQUE(sprint_id, date)
);

-- Tasks (core table — inclui colunas de extensão do nosso formato)
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    project_slug TEXT,
    sprint_id INTEGER REFERENCES sprints(id),
    wave_id INTEGER REFERENCES waves(id),
    task_number INTEGER,
    task_file TEXT,
    day TEXT,
    title TEXT,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'todo',
    priority TEXT DEFAULT 'media',
    labels TEXT,
    agent TEXT,
    motor TEXT,
    motor_real TEXT,
    modulo TEXT,
    status_execucao TEXT DEFAULT '⬜',
    status_auditoria TEXT DEFAULT '⬜',
    gh_issue_number INTEGER,
    veredito TEXT,
    audit_hash TEXT,
    audit_date TEXT,
    notes TEXT,
    source_file TEXT,
    commit_hash TEXT,
    data_conclusao TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Task notes (histórico de observações por task)
CREATE TABLE IF NOT EXISTS task_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    body TEXT NOT NULL,
    actor TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sessions
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    date TEXT NOT NULL,
    objective TEXT,
    done TEXT,
    next_steps TEXT,
    file_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Version history
CREATE TABLE IF NOT EXISTS version_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    version TEXT NOT NULL,
    tag TEXT,
    commit_sha TEXT,
    date TEXT,
    title TEXT,
    status TEXT DEFAULT 'released',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Auditorias (do DIARIO.md do Orchestrator)
CREATE TABLE IF NOT EXISTS auditorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    auditor TEXT NOT NULL DEFAULT 'Orchestrator',
    veredito TEXT NOT NULL CHECK (veredito IN ('aprovado', 'aprovado_ressalva', 'rejeitado')),
    ressalva TEXT,
    scope_creep INTEGER NOT NULL DEFAULT 0,
    task_file_ok INTEGER NOT NULL DEFAULT 0,
    indices_ok INTEGER NOT NULL DEFAULT 0,
    diff_hash TEXT NOT NULL,
    audit_hash TEXT NOT NULL,
    observacoes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Agent status (do ESTADO-DA-EQUIPE.md)
CREATE TABLE IF NOT EXISTS agent_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('idle', 'executing', 'blocked', 'waiting')),
    current_task_id INTEGER REFERENCES tasks(id),
    motor TEXT,
    started_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Delegations (rastreamento de tasks delegadas a subagentes)
CREATE TABLE IF NOT EXISTS delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delegation_id TEXT UNIQUE NOT NULL,
    agent TEXT, motor TEXT, goal TEXT,
    context_preview TEXT, toolsets TEXT,
    status TEXT DEFAULT 'dispatched',
    dispatched_at TEXT, completed_at TEXT,
    duration_seconds INTEGER,
    result_summary TEXT, commit_hash TEXT,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Audit log (log de ações do sistema)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    project_slug TEXT,
    entity_type TEXT,
    entity_id INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(day);
CREATE INDEX IF NOT EXISTS idx_tasks_sprint_id ON tasks(sprint_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent);
CREATE INDEX IF NOT EXISTS idx_tasks_wave_id ON tasks(wave_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_waves_project_date_number
    ON waves(project_id, date, wave_number);
CREATE INDEX IF NOT EXISTS idx_waves_project_id ON waves(project_id);
CREATE INDEX IF NOT EXISTS idx_sprints_project_id ON sprints(project_id);
CREATE INDEX IF NOT EXISTS idx_auditorias_task_id ON auditorias(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_auditorias_unique ON auditorias(task_id, audit_hash);
CREATE INDEX IF NOT EXISTS idx_agent_status_agent ON agent_status(agent);
CREATE INDEX IF NOT EXISTS idx_task_notes_task_id ON task_notes(task_id);
"""


# ─── Init DB ──────────────────────────────────────────────────────────

def init_db(db_path=None):
    """Cria todas as tabelas se não existirem."""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()


# ─── Database Class (usada pelos scanners) ────────────────────────────

class Database:
    """Encapsula conexão SQLite e métodos CRUD para o CT2 scanner."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def get_connection(self) -> sqlite3.Connection:
        """Retorna conexão (lazy, com cache)."""
        if self._conn is None:
            self._conn = get_connection(self.db_path)
        return self._conn

    def create_tables(self) -> None:
        """Cria todas as tabelas do schema."""
        conn = self.get_connection()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

    def upsert_project(self, slug: str, name: str, path: str,
                       repo_url: Optional[str] = None,
                       stack: Optional[str] = None) -> dict:
        """Insere ou atualiza um projeto. Retorna o registro."""
        conn = self.get_connection()
        insert_project(conn, slug, name, str(path), repo_url, stack)
        row = conn.execute(
            "SELECT * FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
        return dict(row) if row else {"slug": slug, "name": name}

    def upsert_task(self, project_slug: str, task_number: int,
                    **kwargs: Any) -> Optional[int]:
        """Insere ou atualiza uma task por (project_slug, task_number).

        Usa INSERT OR REPLACE para garantir idempotência.
        Retorna o id da task ou None se o projeto não existir.

        Se title não for fornecido ou estiver vazio, usa a description como fallback.
        """
        # Safety net: se title está vazio/faltando, usa description
        if not kwargs.get("title") and kwargs.get("description"):
            kwargs = dict(kwargs)
            kwargs["title"] = kwargs["description"]

        conn = self.get_connection()
        proj = conn.execute(
            "SELECT id FROM projects WHERE slug = ?", (project_slug,)
        ).fetchone()
        if not proj:
            return None
        project_id = proj["id"]

        # Check if task already exists
        existing = conn.execute(
            "SELECT id FROM tasks WHERE project_id = ? AND task_number = ?",
            (project_id, task_number)
        ).fetchone()

        if existing:
            # UPDATE existing
            allowed = {
                "task_file", "title", "description", "agent", "motor",
                "motor_real", "modulo", "priority", "status_execucao",
                "status_auditoria", "commit_hash", "wave_id", "sprint_id",
                "data_conclusao", "veredito", "audit_hash", "audit_date",
                "notes", "status", "day",
            }
            updates = {k: v for k, v in kwargs.items()
                       if k in allowed and v is not None}
            if updates:
                set_clause = ", ".join(
                    f"{k} = ?" for k in updates
                )
                values = list(updates.values()) + [existing["id"]]
                conn.execute(
                    f"UPDATE tasks SET {set_clause}, updated_at = datetime('now') "
                    f"WHERE id = ?",
                    values
                )
                conn.commit()
            return existing["id"]
        else:
            # INSERT new
            allowed_keys = {
                "task_file", "title", "description", "agent", "motor",
                "motor_real", "modulo", "priority", "status_execucao",
                "status_auditoria", "commit_hash", "wave_id", "sprint_id",
                "data_conclusao", "veredito", "audit_hash", "audit_date",
                "notes", "status", "day",
            }
            extras = {k: v for k, v in kwargs.items()
                      if k in allowed_keys and v is not None}

            cols = ["project_id", "project_slug", "task_number"] + list(extras.keys())
            placeholders = ["?", "?", "?"] + ["?"] * len(extras)
            values = [project_id, project_slug, task_number] + list(extras.values())

            cur = conn.execute(
                f"INSERT INTO tasks ({', '.join(cols)}, created_at, updated_at) "
                f"VALUES ({', '.join(placeholders)}, datetime('now'), datetime('now'))",
                values
            )
            conn.commit()
            return cur.lastrowid

    def upsert_wave(self, project_slug: str, wave_number: int,
                    **kwargs: Any) -> dict:
        """Insere ou atualiza uma wave. Retorna o registro."""
        conn = self.get_connection()
        proj = conn.execute(
            "SELECT id FROM projects WHERE slug = ?", (project_slug,)
        ).fetchone()
        if not proj:
            return {"wave_number": wave_number}
        project_id = proj["id"]

        date_val = kwargs.get("date", "") or ""
        existing = conn.execute(
            "SELECT * FROM waves WHERE project_id = ? AND wave_number = ? AND date = ?",
            (project_id, wave_number, date_val)
        ).fetchone()

        if existing:
            updates = {}
            if "date" in kwargs and kwargs["date"]:
                updates["date"] = kwargs["date"]
            if "status" in kwargs and kwargs["status"]:
                updates["status"] = kwargs["status"]
            if "sprint_id" in kwargs and kwargs["sprint_id"]:
                updates["sprint_id"] = kwargs["sprint_id"]
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                values = list(updates.values()) + [existing["id"]]
                conn.execute(
                    f"UPDATE waves SET {set_clause} WHERE id = ?", values
                )
                conn.commit()
            return dict(existing)
        else:
            status_val = kwargs.get("status", "planned")
            sprint_id = kwargs.get("sprint_id")
            cur = conn.execute(
                "INSERT INTO waves (project_id, wave_number, date, status, sprint_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, wave_number, date_val, status_val, sprint_id)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM waves WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row) if row else {"wave_number": wave_number}

    def get_tasks_by_project(self, slug: str) -> list[dict]:
        """Retorna todas as tasks de um projeto."""
        conn = self.get_connection()
        return get_project_tasks(conn, slug)

    def get_waves_by_project(self, slug: str) -> list[dict]:
        """Retorna todas as waves de um projeto."""
        conn = self.get_connection()
        rows = conn.execute("""
            SELECT w.* FROM waves w
            JOIN projects p ON w.project_id = p.id
            WHERE p.slug = ?
            ORDER BY w.wave_number
        """, (slug,)).fetchall()
        return [dict(r) for r in rows]

    def upsert_sprint(self, project_id: int, number: int,
                      title: str = "", status: str = "active") -> dict:
        """Insere ou atualiza uma sprint por (project_id, number).

        Retorna o registro salvo.
        """
        conn = self.get_connection()
        existing = conn.execute(
            "SELECT * FROM sprints WHERE project_id = ? AND number = ?",
            (project_id, number)
        ).fetchone()

        if existing:
            updates = {}
            if title:
                updates["title"] = title
            if status:
                updates["status"] = status
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                values = list(updates.values()) + [existing["id"]]
                conn.execute(
                    f"UPDATE sprints SET {set_clause} WHERE id = ?", values
                )
                conn.commit()
            return dict(existing)
        else:
            cur = conn.execute(
                "INSERT INTO sprints (project_id, number, title, status) "
                "VALUES (?, ?, ?, ?)",
                (project_id, number, title, status)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sprints WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row) if row else {
                "project_id": project_id, "number": number
            }

    def upsert_sprint_date(self, sprint_id: int, date: str,
                           day_number: int) -> dict:
        """Insere ou atualiza um registro em sprint_dates.
        Retorna o registro inserido/atualizado."""
        conn = self.get_connection()
        conn.execute("""
            INSERT INTO sprint_dates (sprint_id, date, day_number)
            VALUES (?, ?, ?)
            ON CONFLICT(sprint_id, date) DO UPDATE SET
                day_number = excluded.day_number
        """, (sprint_id, date, day_number))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM sprint_dates WHERE sprint_id = ? AND date = ?",
            (sprint_id, date)
        ).fetchone()
        return dict(row) if row else {
            "sprint_id": sprint_id, "date": date, "day_number": day_number
        }

    def get_sprint_by_date(self, date: str) -> Optional[dict]:
        """Retorna sprint_id e sprint_number para uma data."""
        conn = self.get_connection()
        row = conn.execute("""
            SELECT sd.sprint_id, s.number AS sprint_number, sd.date, sd.day_number
            FROM sprint_dates sd
            JOIN sprints s ON sd.sprint_id = s.id
            WHERE sd.date = ?
        """, (date,)).fetchone()
        return dict(row) if row else None

    def get_project(self, slug: str) -> Optional[dict]:
        """Retorna um projeto pelo slug."""
        conn = self.get_connection()
        return get_project_by_slug(conn, slug)

    def close(self) -> None:
        """Fecha a conexão."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ─── Helper Functions (modo funcional, compatibilidade) ───────────────

def insert_project(conn, slug, name, path, repo_url=None, stack=None):
    """Insere ou atualiza um projeto."""
    cur = conn.execute("""
        INSERT INTO projects (slug, name, path, repo_url, stack, last_scan)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(slug) DO UPDATE SET
            name=excluded.name,
            path=excluded.path,
            repo_url=COALESCE(excluded.repo_url, projects.repo_url),
            stack=COALESCE(excluded.stack, projects.stack),
            last_scan=datetime('now')
    """, (slug, name, path, repo_url, stack))
    conn.commit()
    return cur.lastrowid


def insert_task(conn, project_id, title, status="todo", **kwargs):
    """Insere uma nova task com campos opcionais."""
    allowed = {
        "project_slug", "sprint_id", "wave_id", "day", "task_number",
        "priority", "labels", "agent", "motor", "motor_real",
        "gh_issue_number", "notes", "source_file", "commit_hash",
        "data_conclusao", "veredito", "audit_hash", "audit_date"
    }
    extras = {k: v for k, v in kwargs.items() if k in allowed and v is not None}

    cols = ["project_id", "title", "status"] + list(extras.keys())
    placeholders = ["?", "?", "?"] + ["?"] * len(extras)
    values = [project_id, title, status] + list(extras.values())

    cur = conn.execute(f"""
        INSERT INTO tasks ({', '.join(cols)}, created_at, updated_at)
        VALUES ({', '.join(placeholders)}, datetime('now'), datetime('now'))
    """, values)
    conn.commit()
    return cur.lastrowid


def get_project_tasks(conn, project_slug, status_filter=None):
    """Retorna tasks de um projeto, opcionalmente filtradas por status."""
    if status_filter:
        if isinstance(status_filter, (list, tuple)):
            placeholders = ",".join("?" for _ in status_filter)
            rows = conn.execute(f"""
                SELECT t.* FROM tasks t
                JOIN projects p ON t.project_id = p.id
                WHERE p.slug = ? AND t.status IN ({placeholders})
                ORDER BY t.created_at DESC
            """, [project_slug] + list(status_filter)).fetchall()
        else:
            rows = conn.execute("""
                SELECT t.* FROM tasks t
                JOIN projects p ON t.project_id = p.id
                WHERE p.slug = ? AND t.status = ?
                ORDER BY t.created_at DESC
            """, (project_slug, status_filter)).fetchall()
    else:
        rows = conn.execute("""
            SELECT t.* FROM tasks t
            JOIN projects p ON t.project_id = p.id
            WHERE p.slug = ?
            ORDER BY t.created_at DESC
        """, (project_slug,)).fetchall()
    return [dict(r) for r in rows]


def seed_agents(conn=None, db_path=None):
    """Insere os 6 agentes fixos no banco (seed fixo).

    Substitui a dependência do ESTADO-DA-EQUIPE.md para popular agent_status.
    Os agentes são fixos: Orchestrator, Agent-Product, Agent-DevOps, Agent-Backend, Agent-Vault, Agent-Frontend.

    Args:
        conn:    Optional SQLite connection. Se não fornecido, abre um.
        db_path: Path to database (usado se conn não for fornecido).

    Returns:
        Número de linhas inseridas/atualizadas.
    """
    if conn is None:
        conn = get_connection(db_path)
        should_close = True
    else:
        should_close = False

    agents = [
        ("Orchestrator", "idle", "DeepSeek V4 Flash", ""),
        ("Agent-Product", "idle", "zai glm-5.2", ""),
        ("Agent-DevOps", "idle", "agy Gemini 3.5 Flash", ""),
        ("Agent-Backend", "idle", "Codex gpt-5.5", ""),
        ("Agent-Vault", "idle", "agy Gemini 3.5 Flash", ""),
        ("Agent-Frontend", "idle", "Opus 4.7", ""),
    ]

    count = 0
    for agent, status, motor, _task in agents:
        existing = conn.execute(
            "SELECT id FROM agent_status WHERE agent = ?", (agent,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE agent_status
                   SET status = ?,
                       motor = ?,
                       updated_at = datetime('now')
                   WHERE agent = ?""",
                (status, motor or None, agent),
            )
        else:
            conn.execute(
                """INSERT INTO agent_status (agent, status, motor)
                   VALUES (?, ?, ?)""",
                (agent, status, motor or None),
            )
        count += 1
    conn.commit()

    if should_close:
        conn.close()

    return count


def get_agent_status(conn):
    """Retorna status atual de todos os agentes."""
    rows = conn.execute("""
        SELECT a.*, t.title as current_task_title
        FROM agent_status a
        LEFT JOIN tasks t ON a.current_task_id = t.id
        ORDER BY a.agent
    """).fetchall()
    return [dict(r) for r in rows]


def get_project_by_slug(conn, slug):
    """Retorna um projeto pelo slug."""
    row = conn.execute(
        "SELECT * FROM projects WHERE slug = ?", (slug,)
    ).fetchone()
    return dict(row) if row else None


def search_tasks(conn, query, project_slug=None):
    """Busca global por tasks."""
    if project_slug:
        rows = conn.execute("""
            SELECT t.* FROM tasks t
            JOIN projects p ON t.project_id = p.id
            WHERE p.slug = ?
              AND (t.title LIKE ? OR t.task_number LIKE ? OR t.notes LIKE ?)
            ORDER BY t.created_at DESC
            LIMIT 30
        """, (project_slug, f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
    else:
        rows = conn.execute("""
            SELECT t.* FROM tasks t
            WHERE t.title LIKE ? OR t.task_number LIKE ? OR t.notes LIKE ?
            ORDER BY t.created_at DESC
            LIMIT 30
        """, (f"%{query}%", f"%{query}%", f"%{query}%")).fetchall()
    return [dict(r) for r in rows]


def dedup_auditorias(conn):
    """Identifica e remove duplicatas na tabela auditorias (mesmo task_id + diff_hash),
    mantendo apenas a mais recente (maior id).
    Retorna o número de registros removidos.
    """
    # Identifica todas as duplicatas que devem ser deletadas
    query = """
        SELECT id FROM auditorias
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM auditorias
            GROUP BY task_id, diff_hash
        )
    """
    ids_to_delete = [row[0] for row in conn.execute(query).fetchall()]
    deleted_count = 0
    if ids_to_delete:
        placeholders = ",".join("?" for _ in ids_to_delete)
        conn.execute(f"DELETE FROM auditorias WHERE id IN ({placeholders})", ids_to_delete)
        conn.commit()
        deleted_count = len(ids_to_delete)
    return deleted_count


def relink_orphan_auditorias(conn):
    """Relinks auditorias with orphan task_ids (where task_id uses task_number
    instead of the actual tasks.id). Matches by task_number with project priority:
    CT V2 (1) > example-project (5) > agent-ops-workflow (3).
    Returns list of (audit_id, old_task_id, new_task_id) for relinked rows.
    """
    orphans = conn.execute("""
        SELECT a.id, a.task_id
        FROM auditorias a
        LEFT JOIN tasks t ON a.task_id = t.id
        WHERE t.id IS NULL
        ORDER BY a.id
    """).fetchall()

    relinked = []
    for audit_id, orphan_task_id in orphans:
        candidates = conn.execute("""
            SELECT t.id, t.project_id
            FROM tasks t
            WHERE t.task_number = ?
            ORDER BY
                CASE t.project_id
                    WHEN 1 THEN 0
                    WHEN 5 THEN 1
                    WHEN 3 THEN 2
                    ELSE 99
                END,
                t.id
        """, (orphan_task_id,)).fetchall()

        if not candidates:
            print(f"  ⚠️  Nenhum candidato para task_number={orphan_task_id} (audit id={audit_id})")
            continue

        new_task_id = candidates[0][0]
        conn.execute("UPDATE auditorias SET task_id = ? WHERE id = ?", (new_task_id, audit_id))
        relinked.append((audit_id, orphan_task_id, new_task_id))

    if relinked:
        conn.commit()

    return relinked


def get_scorecards(conn, days=7, agent=None):
    """
    Retorna scorecards de performance por agente para o endpoint GET /api/scorecards.

    Métricas calculadas a partir de tasks e auditorias existentes.
    Agrupa agentes por prefixo (agent LIKE 'Agent-Product%' → 'Agent-Product') para
    normalizar nomes inconsistentes como 'Agent-Product (via delegate_task)'.
    """
    CORE_AGENTS = ['Orchestrator', 'Agent-Product', 'Agent-DevOps', 'Agent-Backend', 'Agent-Vault', 'Agent-Frontend']

    if agent:
        CORE_AGENTS = [a for a in CORE_AGENTS if a.lower() == agent.lower()]
        if not CORE_AGENTS:
            CORE_AGENTS = [agent]

    results = []

    for core_agent in CORE_AGENTS:
        pattern = core_agent + '%'

        # === TOTAL TASKS ===
        total_tasks = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE agent LIKE ?",
            (pattern,)
        ).fetchone()[0]

        # === VOLUME: tasks concluded in period ===
        vol = 0
        vol_30d = 0
        if days is not None and days > 0:
            vol = conn.execute(
                "SELECT COUNT(*) FROM tasks "
                "WHERE agent LIKE ? AND status = 'done' "
                "AND julianday('now') - julianday(COALESCE(data_conclusao, updated_at)) <= ?",
                (pattern, days)
            ).fetchone()[0]
        vol_30d = conn.execute(
            "SELECT COUNT(*) FROM tasks "
            "WHERE agent LIKE ? AND status = 'done' "
            "AND julianday('now') - julianday(COALESCE(data_conclusao, updated_at)) <= 30",
            (pattern,)
        ).fetchone()[0]

        # === AUDIT METRICS ===
        # Distinct tasks auditadas com suas estatísticas
        audit_rows = conn.execute(
            "SELECT a.task_id, COUNT(*) AS audit_count, "
            "MIN(a.created_at) AS first_audit_at, "
            "(SELECT a2.veredito FROM auditorias a2 "
            " WHERE a2.task_id = a.task_id "
            " ORDER BY a2.created_at ASC, a2.id ASC LIMIT 1) AS first_veredito, "
            "SUM(a.scope_creep) AS total_scope_creep "
            "FROM auditorias a "
            "JOIN tasks t ON t.id = a.task_id "
            "WHERE t.agent LIKE ? "
            "GROUP BY a.task_id",
            (pattern,)
        ).fetchall()

        audited_tasks = len(audit_rows)
        first_pass = 0
        rework_count = 0
        scope_creep_total = 0

        for row in audit_rows:
            scope_creep_total += (row['total_scope_creep'] or 0)
            if row['audit_count'] > 1:
                rework_count += 1
            # First-pass: approved on the very first audit
            first_veredito = row['first_veredito']
            if first_veredito and 'aprovado' in first_veredito:
                first_pass += 1

        first_pass_rate = round(
            first_pass / max(audited_tasks, 1) * 100, 1
        ) if audited_tasks > 0 else 0.0

        # === AVG TIME TASK→DONE ===
        # Skip rows where data_conclusao < created_at (historical import data)
        avg_row = conn.execute(
            "SELECT AVG("
            "  (julianday(COALESCE(data_conclusao||' 23:59:59', updated_at)) "
            "   - julianday(created_at)) * 24"
            ") AS avg_hours "
            "FROM tasks "
            "WHERE agent LIKE ? AND status = 'done' "
            "AND created_at IS NOT NULL "
            "AND (data_conclusao IS NULL OR data_conclusao >= date(created_at))",
            (pattern,)
        ).fetchone()

        avg_hours = round(avg_row['avg_hours'], 1) if (avg_row and avg_row['avg_hours'] is not None) else None

        results.append({
            'agent': core_agent,
            'first_pass_rate': first_pass_rate,
            'rework_count': rework_count,
            'scope_creep': scope_creep_total,
            'avg_time_to_done_hours': avg_hours,
            'volume': vol,
            'volume_30d': vol_30d,
            'total_tasks': total_tasks,
            'audited_tasks': audited_tasks,
        })

    return results

