#!/usr/bin/env python3
"""Fila transacional de notificações do Control Tower V2."""

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from src.db import DEFAULT_DB_PATH, get_connection


DB_PATH = DEFAULT_DB_PATH
QUEUE_PATH = os.path.expanduser(
    "~/.hermes/profiles/dalinar/notifications/queue.json"
)
MAX_QUEUE_SIZE = 50
LEGACY_MIGRATION_NAME = "legacy_notifications_queue_import"

logger = logging.getLogger(__name__)


def _timestamp_now():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _entry_from_row(row):
    """Mantém o formato público anterior, que expunha ``timestamp``."""
    return {
        "id": row["id"],
        "tipo": row["tipo"],
        "mensagem": row["mensagem"],
        "task_number": row["task_number"],
        "agente": row["agente"] or "",
        "hash": row["hash"] or "",
        "read": int(row["read"] or 0),
        "timestamp": row["created_at"],
    }


def _read_legacy_queue():
    """Lê o JSON legado sem alterá-lo; falhas são tratadas como best-effort."""
    if not os.path.isfile(QUEUE_PATH):
        return []
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("Não foi possível importar queue.json legado: %s", exc)
        return []
    return data if isinstance(data, list) else []


def _migrate_legacy_queue(conn):
    """Importa ``queue.json`` uma única vez, de forma idempotente."""
    already_migrated = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?",
        (LEGACY_MIGRATION_NAME,),
    ).fetchone()
    if already_migrated:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        # Outro processo pode ter concluído a importação enquanto aguardávamos
        # o lock de escrita.
        already_migrated = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?",
            (LEGACY_MIGRATION_NAME,),
        ).fetchone()
        if already_migrated:
            conn.commit()
            return

        for legacy in _read_legacy_queue():
            if not isinstance(legacy, dict):
                continue
            timestamp = legacy.get("timestamp") or legacy.get("created_at") or _timestamp_now()
            entry_id = str(legacy.get("id") or f"{timestamp}-{uuid.uuid4().hex[:8]}")
            conn.execute(
                """
                INSERT OR IGNORE INTO notifications
                    (id, tipo, mensagem, task_number, agente, hash, read, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    str(legacy.get("tipo") or ""),
                    str(legacy.get("mensagem") or ""),
                    legacy.get("task_number"),
                    str(legacy.get("agente") or ""),
                    str(legacy.get("hash") or ""),
                    1 if legacy.get("read") else 0,
                    str(timestamp),
                ),
            )

        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (name, checksum) VALUES (?, ?)",
            (LEGACY_MIGRATION_NAME, "runtime-import-v1"),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _open_db():
    conn = get_connection(DB_PATH)
    try:
        _migrate_legacy_queue(conn)
    except Exception:
        conn.close()
        raise
    return conn


def push_notification(tipo, mensagem, task_data=None):
    """Adiciona uma notificação à fila."""
    timestamp = _timestamp_now()
    entry = {
        "id": f"{timestamp}-{uuid.uuid4().hex[:8]}",
        "tipo": tipo,
        "mensagem": mensagem,
        "task_number": None,
        "agente": "",
        "hash": "",
        "read": 0,
        "timestamp": timestamp,
    }

    if task_data:
        entry["task_number"] = task_data.get("task_number")
        entry["agente"] = task_data.get("agente", "")
        entry["hash"] = task_data.get("hash", "")

    conn = _open_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO notifications
                (id, tipo, mensagem, task_number, agente, hash, read, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                entry["id"], entry["tipo"], entry["mensagem"],
                entry["task_number"], entry["agente"], entry["hash"],
                entry["timestamp"],
            ),
        )
        conn.execute(
            """
            DELETE FROM notifications
            WHERE id IN (
                SELECT id FROM notifications
                ORDER BY created_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (MAX_QUEUE_SIZE,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return entry


def get_notifications(limit=5):
    """Retorna as últimas N notificações não lidas, mais recentes primeiro."""
    limit = max(0, int(limit))
    conn = _open_db()
    try:
        rows = conn.execute(
            """
            SELECT id, tipo, mensagem, task_number, agente, hash, read, created_at
            FROM notifications
            WHERE read = 0
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_entry_from_row(row) for row in rows]
    finally:
        conn.close()


def clear_notifications():
    """Limpa toda a fila de notificações."""
    conn = _open_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM notifications")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_TIPO_ICONS = {
    "task_done": "✅",
    "task_audit": "👁",
    "error": "❌",
    "daily_summary": "📋",
    "test": "🔔",
}

_TIPO_LABELS = {
    "task_done": "Task concluída",
    "task_audit": "Auditoria",
    "error": "Erro",
    "daily_summary": "Resumo diário",
    "test": "Teste",
}


def _icon_for(tipo):
    return _TIPO_ICONS.get(tipo, "🔔")


def _label_for(tipo):
    return _TIPO_LABELS.get(tipo, tipo or "Notificação")


def _relative_time(timestamp):
    if not timestamp:
        return ""
    try:
        ts = str(timestamp).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if secs < 60:
        return "agora"
    mins = secs // 60
    if mins < 60:
        return f"há {mins} min"
    hours = mins // 60
    if hours < 24:
        return f"há {hours} h"
    return f"há {hours // 24} d"


def _escape_html(text):
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def get_notifications_html(limit=10):
    """Retorna HTML formatado das últimas notificações não lidas."""
    notifications = get_notifications(limit=limit)
    if not notifications:
        return (
            '<div class="notification-empty px-4 py-8 text-center '
            'text-sm text-neutral-500 dark:text-neutral-400">'
            'Nenhuma notificação 🔕</div>'
        )

    items = []
    for notification in notifications:
        tipo = notification.get("tipo", "")
        icon = _icon_for(tipo)
        label = _escape_html(_label_for(tipo))
        mensagem = _escape_html(notification.get("mensagem", ""))
        agente = _escape_html(notification.get("agente", "") or "")
        notification_id = _escape_html(notification.get("id", ""))
        relative = _escape_html(_relative_time(notification.get("timestamp")))
        agente_html = (
            f'<span class="notification-agent text-[10px] text-neutral-400 '
            f'dark:text-neutral-500">👤 {agente}</span>' if agente else ""
        )
        items.append(
            f'<div class="notification-item flex items-start gap-2.5 px-3 py-2.5 '
            f'border-b border-neutral-100 dark:border-neutral-800 '
            f'hover:bg-neutral-50 dark:hover:bg-neutral-800/50 transition-colors" '
            f'data-id="{notification_id}">'
            f'<span class="notification-icon text-base shrink-0 mt-0.5">{icon}</span>'
            f'<div class="notification-body flex-1 min-w-0">'
            f'<p class="notification-message text-[13px] font-medium leading-snug '
            f'text-neutral-800 dark:text-neutral-100 break-words">{mensagem}</p>'
            f'<div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-0.5">'
            f'<span class="notification-type text-[10px] uppercase tracking-wide '
            f'font-semibold text-primary-600/70 dark:text-primary-400/70">{label}</span>'
            f'<span class="notification-time text-[10px] text-neutral-400 '
            f'dark:text-neutral-500">{relative}</span>{agente_html}'
            f'</div></div>'
            f'<button type="button" class="notification-read-btn shrink-0 mt-0.5 w-5 h-5 '
            f'flex items-center justify-center rounded-md text-neutral-300 '
            f'hover:text-primary-600 hover:bg-neutral-100 dark:text-neutral-600 '
            f'dark:hover:text-primary-400 dark:hover:bg-neutral-800 transition-colors" '
            f'data-notification-id="{notification_id}" title="Marcar como lida" '
            f'aria-label="Marcar como lida">✓</button></div>'
        )
    return '<div class="notification-list">' + "".join(items) + "</div>"


def mark_as_read(notification_id):
    """Marca uma notificação como lida; ``all`` limpa toda a fila."""
    if notification_id == "all":
        clear_notifications()
        return True

    conn = _open_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ? AND read = 0",
            (notification_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
