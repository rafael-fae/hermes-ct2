"""
src/metrics.py — Agent Performance Scorecard.

Agrega métricas de desempenho por agente a partir de dados que já existem
(tasks, auditorias, delegations). Alimenta a aba 'Desempenho' do dashboard.

Métricas por agente (agregado de TODOS os projetos):
  - total / executed / executed_pct / audited
  - approved / rejected / approval_rate (das auditorias)
  - scope_creep / scope_creep_pct
  - rework (proxy = rejeitadas)
  - avg_duration_min (das delegations, quando houver duração)
  - rework_tasks / total_rejections / avg_time_to_fix_h (F5 — loop de retrabalho)
"""

from collections import defaultdict
from datetime import datetime


def compute_agent_metrics(conn):
    """Retorna lista de dicts (um por agente), ordenada por nº de tasks desc."""
    task_rows = conn.execute("""
        SELECT agent,
               COUNT(*) AS total,
               SUM(CASE WHEN status_execucao = '✅' THEN 1 ELSE 0 END) AS executed,
               SUM(CASE WHEN status_auditoria = '👁' THEN 1 ELSE 0 END) AS audited
        FROM tasks
        WHERE agent IS NOT NULL AND TRIM(agent) != ''
        GROUP BY agent
    """).fetchall()

    audit_rows = conn.execute("""
        SELECT t.agent AS agent,
               COUNT(*) AS audit_count,
               SUM(CASE WHEN a.veredito IN ('aprovado', 'aprovado_ressalva') THEN 1 ELSE 0 END) AS approved,
               SUM(CASE WHEN a.veredito = 'rejeitado' THEN 1 ELSE 0 END) AS rejected,
               SUM(CASE WHEN a.scope_creep = 1 THEN 1 ELSE 0 END) AS scope_creep
        FROM auditorias a
        JOIN tasks t ON a.task_id = t.id
        WHERE t.agent IS NOT NULL AND TRIM(t.agent) != ''
        GROUP BY t.agent
    """).fetchall()

    deleg_rows = conn.execute("""
        SELECT agent,
               AVG(duration_seconds) AS avg_dur,
               COUNT(*) AS deleg_count
        FROM delegations
        WHERE agent IS NOT NULL AND TRIM(agent) != '' AND duration_seconds IS NOT NULL
        GROUP BY agent
    """).fetchall()

    motor_map = {
        r["agent"]: (r["motor"] or "")
        for r in conn.execute("SELECT agent, motor FROM agent_status").fetchall()
    }
    audit_map = {r["agent"]: r for r in audit_rows}
    deleg_map = {r["agent"]: r for r in deleg_rows}

    metrics = []
    for tr in task_rows:
        agent = tr["agent"]
        total = tr["total"] or 0
        executed = tr["executed"] or 0
        audited = tr["audited"] or 0

        ar = audit_map.get(agent)
        approved = (ar["approved"] or 0) if ar else 0
        rejected = (ar["rejected"] or 0) if ar else 0
        scope_creep = (ar["scope_creep"] or 0) if ar else 0
        audit_count = (ar["audit_count"] or 0) if ar else 0

        decided = approved + rejected
        approval_rate = round(approved / decided * 100, 1) if decided else None
        scope_creep_pct = round(scope_creep / audit_count * 100, 1) if audit_count else None
        executed_pct = round(executed / total * 100, 1) if total else 0.0

        dr = deleg_map.get(agent)
        avg_duration_min = round(dr["avg_dur"] / 60, 1) if dr and dr["avg_dur"] else None

        metrics.append({
            "agent": agent,
            "motor": motor_map.get(agent, ""),
            "total": total,
            "executed": executed,
            "executed_pct": executed_pct,
            "audited": audited,
            "approved": approved,
            "rejected": rejected,
            "approval_rate": approval_rate,
            "scope_creep": scope_creep,
            "scope_creep_pct": scope_creep_pct,
            "rework": rejected,
            "avg_duration_min": avg_duration_min,
        })

    # F5 — profundidade de retrabalho (rework loop) por agente
    agent_rework, _ = _compute_rework(conn)
    for m in metrics:
        rw = agent_rework.get(m["agent"])
        m["rework_tasks"] = rw["rework_tasks"] if rw else 0
        m["total_rejections"] = rw["total_rejections"] if rw else 0
        fixes = rw["fix_seconds"] if rw else []
        m["avg_time_to_fix_h"] = round(sum(fixes) / len(fixes) / 3600, 1) if fixes else None

    metrics.sort(key=lambda m: (-m["total"], m["agent"]))
    return metrics


# ─── F5: análise do loop de retrabalho (rejeição → correção) ──────────────

def _parse_dt(value):
    try:
        return datetime.fromisoformat(str(value).replace("T", " ").split(".")[0])
    except (ValueError, TypeError):
        return None


def _compute_rework(conn):
    """Analisa a sequência de auditorias por task. Retorna (by_agent, by_module).

    Uma task é "retrabalho" se teve ≥1 veredito 'rejeitado'. time-to-fix = tempo
    entre cada rejeição e a próxima aprovação da MESMA task.
    """
    rows = conn.execute("""
        SELECT t.id AS task_id, t.agent AS agent,
               COALESCE(NULLIF(TRIM(t.modulo), ''), 'Geral') AS modulo,
               a.veredito AS veredito, a.created_at AS created_at
        FROM auditorias a
        JOIN tasks t ON a.task_id = t.id
        ORDER BY t.id, a.created_at, a.id
    """).fetchall()

    by_task = defaultdict(list)
    meta = {}
    for r in rows:
        by_task[r["task_id"]].append((r["veredito"], r["created_at"]))
        meta[r["task_id"]] = (r["agent"], r["modulo"])

    by_agent = defaultdict(lambda: {"rework_tasks": 0, "total_rejections": 0, "fix_seconds": []})
    by_module = defaultdict(lambda: {"rework_tasks": 0, "total_rejections": 0})

    for task_id, seq in by_task.items():
        agent, modulo = meta[task_id]
        n_rej = sum(1 for v, _ in seq if v == "rejeitado")
        if n_rej == 0:
            continue
        if agent and str(agent).strip():
            by_agent[agent]["rework_tasks"] += 1
            by_agent[agent]["total_rejections"] += n_rej
        by_module[modulo]["rework_tasks"] += 1
        by_module[modulo]["total_rejections"] += n_rej
        for i, (v, ts) in enumerate(seq):
            if v != "rejeitado":
                continue
            for v2, ts2 in seq[i + 1:]:
                if v2 in ("aprovado", "aprovado_ressalva"):
                    d1, d2 = _parse_dt(ts), _parse_dt(ts2)
                    if d1 and d2 and (d2 - d1).total_seconds() >= 0 and agent:
                        by_agent[agent]["fix_seconds"].append((d2 - d1).total_seconds())
                    break
    return by_agent, by_module


def compute_module_rework(conn):
    """Retorna retrabalho agregado por módulo, ordenado por rejeições desc."""
    _, by_module = _compute_rework(conn)
    out = [
        {"modulo": k, "rework_tasks": v["rework_tasks"], "total_rejections": v["total_rejections"]}
        for k, v in by_module.items()
    ]
    out.sort(key=lambda x: (-x["total_rejections"], x["modulo"]))
    return out
