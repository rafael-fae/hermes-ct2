"""Persistência e decisões human-in-the-loop para comandos escalados."""

import json


DECISIONS = {
    "approve": (
        "approved",
        "approval_approved",
        "✅ Comando aprovado por Rafael via CT2 Approval Inbox. Pode prosseguir.",
    ),
    "deny": (
        "denied",
        "approval_denied",
        "⛔ Comando negado por Rafael via CT2 Approval Inbox. Não execute.",
    ),
    "approve_once": (
        "approved_once",
        "approval_approved_once",
        "✅ Comando aprovado UMA VEZ por Rafael via CT2 Approval Inbox. "
        "Execute somente esta ocorrência.",
    ),
}


class ApprovalNotFound(LookupError):
    pass


class ApprovalConflict(RuntimeError):
    pass


class ApprovalDeliveryError(RuntimeError):
    pass


def create_approval_request(
    conn, *, delegation_id, command, reason, source_message_ts
):
    """Cria uma solicitação pendente uma vez por mensagem do Slack."""
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO approval_requests (
            delegation_id, command, reason, source_message_ts
        ) VALUES (?, ?, ?, ?)
        """,
        (delegation_id, command, reason, str(source_message_ts)),
    )
    created = cursor.rowcount > 0
    row = conn.execute(
        """
        SELECT * FROM approval_requests
        WHERE delegation_id = ? AND source_message_ts = ?
        """,
        (delegation_id, str(source_message_ts)),
    ).fetchone()
    if created:
        conn.execute(
            """
            INSERT INTO audit_log (
                actor, action, project_slug, entity_type, entity_id, detail
            ) VALUES ('ct2_monitor', 'approval_requested', 'control-tower-v2',
                      'approval_request', ?, ?)
            """,
            (
                row["id"],
                json.dumps(
                    {"command": command, "reason": reason}, ensure_ascii=False
                ),
            ),
        )
    conn.commit()
    return dict(row), created


def list_pending_approvals(conn):
    rows = conn.execute(
        """
        SELECT ar.*, d.delegation_id AS delegation_ref, d.agent, d.motor,
               d.goal, d.channel, d.thread_ts, d.monitor_state
        FROM approval_requests ar
        JOIN delegations d ON d.id = ar.delegation_id
        WHERE ar.status = 'pending'
        ORDER BY ar.requested_at ASC, ar.id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def decide_approval(conn, request_id, decision, *, actor, post_message):
    """Reserva, entrega no Slack e audita uma decisão humana.

    ``post_message`` recebe ``channel, thread_ts, text``. A reserva evita dois
    cliques concorrentes; falhas externas restauram o estado ``pending``.
    """
    if decision not in DECISIONS:
        raise ValueError("decisão inválida: {}".format(decision))

    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """
        SELECT ar.*, d.channel, d.thread_ts, d.delegation_id AS delegation_ref
        FROM approval_requests ar
        JOIN delegations d ON d.id = ar.delegation_id
        WHERE ar.id = ?
        """,
        (request_id,),
    ).fetchone()
    if not row:
        conn.rollback()
        raise ApprovalNotFound("solicitação não encontrada")
    if row["status"] != "pending":
        conn.rollback()
        raise ApprovalConflict(
            "solicitação já processada ({})".format(row["status"])
        )
    conn.execute(
        "UPDATE approval_requests SET status='processing' WHERE id=?",
        (request_id,),
    )
    conn.commit()

    final_status, audit_action, message = DECISIONS[decision]
    message = "{}\nComando: `{}`".format(message, row["command"])
    try:
        post_message(row["channel"], row["thread_ts"], message)
    except Exception as exc:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE approval_requests SET status='pending' "
            "WHERE id=? AND status='processing'",
            (request_id,),
        )
        conn.execute(
            """
            INSERT INTO audit_log (
                actor, action, project_slug, entity_type, entity_id, detail
            ) VALUES (?, 'approval_delivery_failed', 'control-tower-v2',
                      'approval_request', ?, ?)
            """,
            (actor, request_id, str(exc)),
        )
        conn.commit()
        raise ApprovalDeliveryError(str(exc)) from exc

    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        UPDATE approval_requests
        SET status=?, decided_at=datetime('now'), decided_by=?
        WHERE id=? AND status='processing'
        """,
        (final_status, actor, request_id),
    )
    conn.execute(
        """
        INSERT INTO audit_log (
            actor, action, project_slug, entity_type, entity_id, detail
        ) VALUES (?, ?, 'control-tower-v2', 'approval_request', ?, ?)
        """,
        (
            actor,
            audit_action,
            request_id,
            json.dumps(
                {
                    "command": row["command"],
                    "delegation": row["delegation_ref"],
                    "decision": decision,
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    saved = conn.execute(
        "SELECT * FROM approval_requests WHERE id=?", (request_id,)
    ).fetchone()
    return dict(saved)
