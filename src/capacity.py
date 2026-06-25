"""Telemetria de capacidade e rate-limit por motor.

O CT2 usa planos flat-rate. Por isso, a saturação abaixo é observável: deriva de
eventos reais encontrados no Slack, não de custo ou de uma quota inventada.
"""

import hashlib
import re
from datetime import datetime, timezone


RATE_LIMIT_PATTERNS = (
    (
        "http_429",
        re.compile(
            r"\b(?:http|error|status(?: code)?)\s*:?[\s-]*429\b"
            r"|\b429\s+(?:too many requests|resource exhausted)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "rate_limit",
        re.compile(
            r"\brate[\s_-]*limit(?:ed)?[\s_-]*(?:reached|exceeded|error)\b"
            r"|\brate[\s_-]*limited\b",
            re.IGNORECASE,
        ),
    ),
    (
        "quota_exceeded",
        re.compile(
            r"\bquota (?:reached|exceeded|exhausted)\b|\bcota (?:atingida|excedida|esgotada)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "plan_limit",
        re.compile(
            r"\b(?:usage|plan|weekly|daily|monthly) limit (?:reached|exceeded)\b"
            r"|\blimite (?:do plano |di[aá]rio |semanal |mensal )?(?:atingido|excedido)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cooldown",
        re.compile(
            r"\bcool[\s-]*down\s+(?:active|required|until|for|\d)"
            r"|\btoo many requests\b|\bmuitas requisi(?:ç|c)[õo]es\b",
            re.IGNORECASE,
        ),
    ),
    (
        "provider_limit",
        re.compile(
            r"\byou(?:'ve| have)? (?:hit|reached) (?:your|the) limit\b",
            re.IGNORECASE,
        ),
    ),
)


def canonical_motor(value):
    """Consolida rótulos de modelo/agente no plano operacional do motor."""
    text = str(value or "").strip().lower()
    aliases = (
        ("codex", ("codex",)),
        ("zai", ("zai", "glm")),
        ("agy", ("agy", "gemini")),
        ("claude", ("claude", "opus", "sonnet", "haiku")),
        ("deepseek", ("deepseek",)),
    )
    for canonical, markers in aliases:
        if any(marker in text for marker in markers):
            return canonical
    return text


def classify_rate_limit_message(text):
    """Retorna o tipo do sinal de saturação ou ``None``."""
    for event_type, pattern in RATE_LIMIT_PATTERNS:
        if pattern.search(text or ""):
            return event_type
    return None


def record_rate_limit_events(conn, delegation, messages):
    """Persiste, de forma idempotente, sinais encontrados numa thread Slack."""
    created = []
    delegation_id = delegation["id"]
    motor = canonical_motor(delegation["motor"]) or "desconhecido"

    for message in messages:
        source_ts = str(message.get("ts") or "").strip()
        text = str(message.get("text") or "").strip()
        event_type = classify_rate_limit_message(text)
        if not source_ts or not event_type:
            continue

        event_id = hashlib.sha256(
            "{}:{}".format(delegation_id, source_ts).encode("utf-8")
        ).hexdigest()[:24]
        try:
            occurred_at = datetime.fromtimestamp(
                float(source_ts), tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, TypeError, ValueError):
            occurred_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO motor_rate_limit_events (
                id, delegation_id, motor, event_type, source_message_ts,
                message_excerpt, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                delegation_id,
                motor,
                event_type,
                source_ts,
                text[:500],
                occurred_at,
            ),
        )
        if cursor.rowcount:
            conn.execute(
                """
                INSERT INTO audit_log (
                    actor, action, project_slug, entity_type, entity_id, detail
                ) VALUES ('ct2_monitor', 'rate_limit_detected',
                          'control-tower-v2', 'delegation', ?, ?)
                """,
                (
                    delegation_id,
                    "motor={} type={} source_ts={}".format(
                        motor, event_type, source_ts
                    ),
                ),
            )
            created.append(event_id)
    return created


def _saturation(events_hour, events_day, events_week):
    if events_hour:
        return min(100, 85 + 5 * events_hour), "critical"
    if events_day:
        return min(84, 65 + 5 * events_day), "high"
    if events_week:
        return min(64, 35 + 3 * events_week), "watch"
    return 0, "available"


def compute_motor_capacity(conn):
    """Calcula throughput, wall-clock e saturação observável por motor."""
    motors = {
        canonical_motor(row["motor"])
        for row in conn.execute(
            """
            SELECT motor FROM agent_status
            UNION SELECT motor FROM tasks
            UNION SELECT motor FROM delegations
            UNION SELECT motor FROM motor_rate_limit_events
            """
        ).fetchall()
        if canonical_motor(row["motor"])
    }
    if not motors:
        return []

    delegation_rows = conn.execute(
        """
        SELECT motor,
               SUM(CASE
                   WHEN completed_at IS NOT NULL
                    AND date(completed_at, 'localtime') = date('now', 'localtime')
                   THEN 1 ELSE 0 END) AS tasks_today,
               SUM(CASE
                   WHEN completed_at >= datetime('now', '-1 hour')
                   THEN 1 ELSE 0 END) AS tasks_last_hour,
               SUM(CASE
                   WHEN completed_at >= datetime('now', '-30 days')
                   THEN duration_seconds ELSE 0 END) AS duration_total,
               SUM(CASE
                   WHEN completed_at >= datetime('now', '-30 days')
                    AND duration_seconds IS NOT NULL
                   THEN 1 ELSE 0 END) AS duration_count,
               SUM(CASE
                   WHEN status IN ('dispatched', 'running')
                    AND COALESCE(monitor_state, '') NOT IN ('done', 'needs_review')
                   THEN 1 ELSE 0 END) AS active
        FROM delegations
        WHERE motor IS NOT NULL AND TRIM(motor) != ''
        GROUP BY motor
        """
    ).fetchall()
    event_rows = conn.execute(
        """
        SELECT motor,
               SUM(CASE WHEN occurred_at >= datetime('now', '-1 hour')
                   THEN 1 ELSE 0 END) AS events_hour,
               SUM(CASE WHEN occurred_at >= datetime('now', '-24 hours')
                   THEN 1 ELSE 0 END) AS events_day,
               SUM(CASE WHEN occurred_at >= datetime('now', '-7 days')
                   THEN 1 ELSE 0 END) AS events_week,
               MAX(occurred_at) AS last_event_at
        FROM motor_rate_limit_events
        GROUP BY motor
        """
    ).fetchall()

    deleg_by_motor = {}
    for row in delegation_rows:
        motor = canonical_motor(row["motor"])
        summary = deleg_by_motor.setdefault(
            motor,
            {
                "tasks_today": 0,
                "tasks_last_hour": 0,
                "duration_total": 0,
                "duration_count": 0,
                "active": 0,
            },
        )
        for key in summary:
            summary[key] += row[key] or 0

    events_by_motor = {}
    for row in event_rows:
        motor = canonical_motor(row["motor"])
        summary = events_by_motor.setdefault(
            motor,
            {
                "events_hour": 0,
                "events_day": 0,
                "events_week": 0,
                "last_event_at": None,
            },
        )
        for key in ("events_hour", "events_day", "events_week"):
            summary[key] += row[key] or 0
        if row["last_event_at"] and (
            summary["last_event_at"] is None
            or row["last_event_at"] > summary["last_event_at"]
        ):
            summary["last_event_at"] = row["last_event_at"]
    capacity = []
    for motor in sorted(motors):
        delegation = deleg_by_motor.get(motor)
        events = events_by_motor.get(motor)
        events_hour = (events["events_hour"] or 0) if events else 0
        events_day = (events["events_day"] or 0) if events else 0
        events_week = (events["events_week"] or 0) if events else 0
        saturation_score, saturation_status = _saturation(
            events_hour, events_day, events_week
        )
        avg_seconds = (
            delegation["duration_total"] / delegation["duration_count"]
            if delegation and delegation["duration_count"]
            else None
        )
        capacity.append(
            {
                "motor": motor,
                "tasks_today": (delegation["tasks_today"] or 0)
                if delegation
                else 0,
                "tasks_last_hour": (delegation["tasks_last_hour"] or 0)
                if delegation
                else 0,
                "active": (delegation["active"] or 0) if delegation else 0,
                "avg_duration_min": round(avg_seconds / 60, 1)
                if avg_seconds is not None
                else None,
                "rate_limit_events_1h": events_hour,
                "rate_limit_events_24h": events_day,
                "rate_limit_events_7d": events_week,
                "last_rate_limit_at": events["last_event_at"] if events else None,
                "saturation_score": saturation_score,
                "saturation_status": saturation_status,
                "is_bottleneck": False,
                "bottleneck_reason": None,
            }
        )

    candidates = [
        item
        for item in capacity
        if item["rate_limit_events_7d"]
        or item["active"]
        or item["tasks_today"]
    ]
    if candidates:
        bottleneck = max(
            candidates,
            key=lambda item: (
                item["saturation_score"],
                item["rate_limit_events_24h"],
                item["avg_duration_min"] or 0,
                item["active"],
            ),
        )
        bottleneck["is_bottleneck"] = True
        if bottleneck["rate_limit_events_1h"]:
            reason = "rate-limit na última hora"
        elif bottleneck["rate_limit_events_24h"]:
            reason = "rate-limit nas últimas 24h"
        elif bottleneck["rate_limit_events_7d"]:
            reason = "rate-limit nos últimos 7 dias"
        elif bottleneck["avg_duration_min"] is not None:
            reason = "maior wall-clock médio"
        else:
            reason = "maior carga ativa"
        bottleneck["bottleneck_reason"] = reason

    return capacity
