"""Transições persistentes do ciclo de vida de uma delegação."""


TERMINAL_STATES = {"done", "needs_review"}
OUTCOMES = {"aprovado", "ressalva", "rejeitado"}


def start_delegation(conn, delegation_id):
    """Transiciona uma delegação despachada para execução monitorada."""
    conn.execute(
        """
        UPDATE delegations
        SET monitor_state = 'monitoring',
            status = 'running',
            started_at = COALESCE(started_at, datetime('now')),
            monitor_started_at = COALESCE(monitor_started_at, datetime('now'))
        WHERE id = ?
          AND (monitor_state IS NULL OR monitor_state IN ('waiting_auth', 'monitoring'))
        """,
        (delegation_id,),
    )


def finish_delegation(
    conn,
    delegation_id,
    *,
    monitor_state,
    outcome=None,
    commit_hash=None,
    auditoria_status=None,
    result_summary=None,
):
    """Finaliza monitoramento e calcula duração real no próprio SQLite."""
    if monitor_state not in TERMINAL_STATES:
        raise ValueError("estado terminal inválido: {}".format(monitor_state))
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError("outcome inválido: {}".format(outcome))

    status = "completed" if monitor_state == "done" else "needs_review"
    conn.execute(
        """
        UPDATE delegations
        SET monitor_state = ?,
            status = ?,
            completed_at = datetime('now'),
            monitor_completed_at = datetime('now'),
            duration_seconds = MAX(
                0,
                CAST(strftime('%s', 'now') AS INTEGER) -
                CAST(strftime(
                    '%s',
                    COALESCE(started_at, dispatched_at, created_at, datetime('now'))
                ) AS INTEGER)
            ),
            outcome = ?,
            commit_hash = COALESCE(?, commit_hash),
            auditoria_status = COALESCE(?, auditoria_status),
            result_summary = COALESCE(?, result_summary)
        WHERE id = ?
        """,
        (
            monitor_state,
            status,
            outcome,
            commit_hash,
            auditoria_status,
            result_summary,
            delegation_id,
        ),
    )


def outcome_from_status(status):
    """Normaliza o texto detectado pelo monitor para o domínio do lifecycle."""
    normalized = (status or "").upper()
    if any(
        marker in normalized
        for marker in (
            "NAO_APROVADO",
            "NÃO_APROVADO",
            "REPROVADO",
            "REJEITADO",
            "REFAZER",
            "CORRIGIR",
        )
    ):
        return "rejeitado"
    if "APROVADO_RESSALVA" in normalized:
        return "ressalva"
    return "aprovado"


def auditoria_status_from_outcome(outcome):
    return {
        "aprovado": "aprovado",
        "ressalva": "aprovado_ressalva",
        "rejeitado": "rejeitado",
    }.get(outcome)
