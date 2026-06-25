"""Sala de Operações: visão atual das delegações por estado operacional."""

import re
from datetime import datetime, timezone


LANES = (
    ("waiting_auth", "Aguardando autorização"),
    ("monitoring", "Em execução"),
    ("needs_review", "Requer intervenção"),
    ("done", "Concluído"),
)
ACTIVE_STATES = {"waiting_auth", "monitoring"}


def _normalize_state(row):
    monitor_state = (row["monitor_state"] or "").strip().lower()
    status = (row["status"] or "").strip().lower()
    if status in {"error", "failed", "blocked"}:
        return "needs_review"
    if monitor_state in {state for state, _ in LANES}:
        return monitor_state
    if status == "completed":
        return "done"
    if status == "needs_review":
        return "needs_review"
    if status == "running":
        return "monitoring"
    return "waiting_auth"


def _parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _task_details(goal, delegation_ref):
    goal = (goal or "").strip()
    match = re.search(r"\btask[_\s#-]*(\d+)\b", goal, re.IGNORECASE)
    if not match:
        match = re.search(r"\bdispatch-(\d+)-", delegation_ref or "")
    task_number = int(match.group(1)) if match else None
    title = goal
    if ":" in goal:
        title = goal.split(":", 1)[1].strip()
    return task_number, title or goal or "Delegação sem descrição"


def _elapsed_seconds(row, state, now):
    if row["duration_seconds"] is not None and state not in ACTIVE_STATES:
        return max(0, int(row["duration_seconds"]))
    started = _parse_timestamp(
        row["started_at"]
        or row["monitor_started_at"]
        or row["dispatched_at"]
        or row["created_at"]
    )
    completed = _parse_timestamp(row["completed_at"] or row["monitor_completed_at"])
    if not started:
        return 0
    end = now if state in ACTIVE_STATES else (completed or now)
    return max(0, int((end - started).total_seconds()))


def _attention_reason(row, state, elapsed_seconds, pending_approvals):
    status = (row["status"] or "").strip().lower()
    if status in {"error", "failed", "blocked"}:
        return row["error"] or "Falha reportada pela delegação"
    if state == "needs_review":
        return row["result_summary"] or "Revisão humana necessária"
    if pending_approvals:
        return "Comando aguardando aprovação humana"
    if state == "waiting_auth" and elapsed_seconds >= 300:
        return "Autorização pendente há mais de 5 minutos"
    if state == "monitoring" and elapsed_seconds >= 7200:
        return "Execução em andamento há mais de 2 horas"
    return None


def _serialize(row, state, now, pending_approvals):
    elapsed_seconds = _elapsed_seconds(row, state, now)
    task_number, task_title = _task_details(row["goal"], row["delegation_id"])
    return {
        "id": row["id"],
        "delegation_id": row["delegation_id"],
        "agent": row["agent"] or "Sem agente",
        "motor": row["motor"] or "",
        "task_number": task_number,
        "task_title": task_title,
        "goal": row["goal"] or "",
        "state": state,
        "status": row["status"] or "",
        "outcome": row["outcome"],
        "elapsed_seconds": elapsed_seconds,
        "is_live": state in ACTIVE_STATES,
        "started_at": row["started_at"]
        or row["monitor_started_at"]
        or row["dispatched_at"],
        "completed_at": row["completed_at"] or row["monitor_completed_at"],
        "pending_approvals": pending_approvals,
        "attention_reason": _attention_reason(
            row, state, elapsed_seconds, pending_approvals
        ),
        "channel": row["channel"] or "",
        "thread_ts": row["thread_ts"] or "",
    }


def compute_operations_room(conn, now=None):
    """Retorna WIP agrupado e a conclusão mais recente dos agentes ociosos."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    rows = conn.execute(
        """
        SELECT * FROM delegations
        ORDER BY COALESCE(started_at, dispatched_at, created_at) DESC, id DESC
        """
    ).fetchall()
    approval_counts = {
        row["delegation_id"]: row["total"]
        for row in conn.execute(
            """
            SELECT delegation_id, COUNT(*) AS total
            FROM approval_requests
            WHERE status = 'pending'
            GROUP BY delegation_id
            """
        ).fetchall()
    }

    active_agents = {
        (row["agent"] or "Sem agente").strip().lower()
        for row in rows
        if _normalize_state(row) in ACTIVE_STATES | {"needs_review"}
    }
    terminal_seen = set()
    selected = []
    for row in rows:
        state = _normalize_state(row)
        agent_key = (row["agent"] or "Sem agente").strip().lower()
        if state == "done":
            if agent_key in active_agents or agent_key in terminal_seen:
                continue
            terminal_seen.add(agent_key)
        selected.append(
            _serialize(
                row,
                state,
                now,
                approval_counts.get(row["id"], 0),
            )
        )

    grouped = {state: [] for state, _ in LANES}
    for item in selected:
        grouped[item["state"]].append(item)

    lanes = [
        {
            "state": state,
            "label": label,
            "count": len(grouped[state]),
            "items": grouped[state],
        }
        for state, label in LANES
    ]
    return {
        "lanes": lanes,
        "counts": {lane["state"]: lane["count"] for lane in lanes},
        "active_count": sum(len(grouped[state]) for state in ACTIVE_STATES),
        "attention_count": len(grouped["needs_review"]),
        "fetched_at_epoch": int(now.timestamp()),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
