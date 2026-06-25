"""
src/briefing.py — Gera briefing markdown de um projeto CT V2

Uso:
    from src.briefing import generate_briefing
    print(generate_briefing("oeste-gestao"))
"""

import os
import sys
from datetime import datetime

from src.db import get_connection, DEFAULT_DB_PATH


def _short_motor(motor):
    """Reduz motor longo para versão curta (ex: 'zai CLI glm-5.2')."""
    if not motor:
        return ""
    # Pega só o primeiro pedaço significativo antes de ' —' ou ','
    import re
    m = re.match(r"^([a-zA-Z0-9_.\-\+/ ]+?)(?:\s*[—\-–,;]|$)", motor.strip())
    if m:
        return m.group(1).strip()
    return motor.strip()


def _clean_hash(raw_hash):
    """Extrai o primeiro hash hex limpo de uma string."""
    if not raw_hash:
        return ""
    import re
    m = re.search(r"[a-f0-9]{7,40}", raw_hash)
    return m.group(0) if m else raw_hash.strip()


def _fmt_veredito(veredito):
    """Formata veredito com ícone."""
    mapping = {
        "aprovado": "✅ APROVADO",
        "aprovado_ressalva": "⚠️ APROVADO RESSALVA",
        "rejeitado": "❌ REJEITADO",
    }
    return mapping.get(veredito, veredito or "—")


def generate_briefing(project_slug):
    """Gera briefing markdown para um projeto específico."""
    db = DEFAULT_DB_PATH
    conn = get_connection(db)

    # Verifica se projeto existe
    proj = conn.execute(
        "SELECT * FROM projects WHERE slug = ?", (project_slug,)
    ).fetchone()
    if not proj:
        conn.close()
        return f"❌ Projeto '{project_slug}' não encontrado."

    proj = dict(proj)
    today = datetime.now().strftime("%d/%m/%Y")

    lines = []
    lines.append(f"🗼 BRIEFING — {project_slug} ({today})")
    lines.append("")

    # ── 📍 ONDE PARAMOS: última task concluída ──
    last_done = conn.execute("""
        SELECT task_number, title, agent, motor, commit_hash
        FROM tasks
        WHERE project_id = ?
          AND status_execucao = '✅'
        ORDER BY COALESCE(NULLIF(data_conclusao,''), updated_at) DESC
        LIMIT 1
    """, (proj["id"],)).fetchone()

    lines.append("📍 ONDE PARAMOS")
    if last_done:
        d = dict(last_done)
        agent_part = f" ({d['agent']})" if d.get("agent") else ""
        hash_part = f" [{_clean_hash(d['commit_hash'])}]" if d.get("commit_hash") else ""
        lines.append(f"Última: task_{d['task_number']} — {d['title']}{agent_part}{hash_part}")
    else:
        lines.append("Nenhuma task concluída ainda.")
    lines.append("")

    # ── 📋 PRÓXIMA TASK: todo com task_number ASC ──
    next_task = conn.execute("""
        SELECT task_number, title, agent, motor
        FROM tasks
        WHERE project_id = ?
          AND status = 'todo'
          AND (status_execucao IS NULL OR status_execucao = '⬜')
        ORDER BY task_number ASC
        LIMIT 1
    """, (proj["id"],)).fetchone()

    lines.append("📋 PRÓXIMA TASK")
    if next_task:
        nt = dict(next_task)
        agent_part = nt.get("agent", "") or ""
        motor_part = _short_motor(nt.get("motor", "") or "")
        if agent_part and motor_part:
            agent_motor = f" ({agent_part}/{motor_part})"
        elif agent_part:
            agent_motor = f" ({agent_part})"
        else:
            agent_motor = ""
        lines.append(f"task_{nt['task_number']} — {nt['title']}{agent_motor}")
    else:
        lines.append("🎉 Todas as tasks concluídas!")
    lines.append("")

    # ── 📊 STATUS ──
    total = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ?",
        (proj["id"],)
    ).fetchone()[0]

    done_count = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ? AND status_execucao = '✅'",
        (proj["id"],)
    ).fetchone()[0]

    pending = total - done_count
    pct = round((done_count / total) * 100) if total > 0 else 0

    lines.append("📊 STATUS")
    lines.append(f"{total} tasks | {done_count} concluídas ({pct}%) | {pending} pendentes")
    lines.append("")

    # ── 🔍 ÚLTIMAS AUDITORIAS ──
    audits = conn.execute("""
        SELECT a.veredito, a.audit_hash, t.task_number
        FROM auditorias a
        JOIN tasks t ON a.task_id = t.id
        WHERE t.project_id = ?
        ORDER BY a.id DESC
        LIMIT 3
    """, (proj["id"],)).fetchall()

    lines.append("🔍 ÚLTIMAS AUDITORIAS")
    if audits:
        for a_row in audits:
            a = dict(a_row)
            tnum = a.get("task_number")
            veredito = a["veredito"]
            ahash = a.get("audit_hash", "") or ""
            lines.append(f"• task_{tnum} — {_fmt_veredito(veredito)} — {ahash}")
    else:
        lines.append("Nenhuma auditoria registrada.")
    lines.append("")

    conn.close()
    return "\n".join(lines)


def main():
    """CLI entry point: python3 -m src.briefing <projeto>"""
    if len(sys.argv) < 2:
        print("Uso: uv run python -m src.briefing <project_slug> [--all]")
        sys.exit(1)

    slug = sys.argv[1]

    if slug == "--all":
        from src.project_discovery import list_projects
        projects = list_projects()
        if not projects:
            print("Nenhum projeto cadastrado.")
            return
        for p in projects:
            print(generate_briefing(p["slug"]))
            print()
    else:
        print(generate_briefing(slug))


if __name__ == "__main__":
    main()


def generate_briefing_html(project_data: dict) -> str:
    """Gera HTML do card '🎯 Bora Codar' para o dashboard.

    project_data: dict with keys like allTasks, stats, etc.
    Returns HTML string with Tailwind CSS classes (DS Teal).
    """
    tasks = project_data.get("allTasks") or []
    today_date = datetime.now().strftime("%d/%m/%Y")

    # ── Next pending task (todo with blank/⬜ status_execucao, ordered by task_number ASC) ──
    next_tasks = [t for t in tasks
                  if t.get("status") == "todo"
                  or t.get("status_execucao") == "⬜"]
    next_tasks.sort(key=lambda t: t.get("task_number") or 0)
    next_task = next_tasks[0] if next_tasks else None

    # ── Tasks do dia (today's date) ──
    day_tasks = [t for t in tasks if t.get("day") == today_date]
    day_count = len(day_tasks)
    day_done = sum(1 for t in day_tasks if t.get("status_execucao") == "✅")
    day_planned = sum(1 for t in day_tasks if (t.get("status_execucao") or "") in ("", "⬜"))

    # ── Last completed task ──
    done_tasks = [t for t in tasks if t.get("status") == "done"]
    done_tasks.sort(
        key=lambda t: str(t.get("updated_at") or t.get("data_conclusao") or ""),
        reverse=True,
    )
    last_done = done_tasks[0] if done_tasks else None

    # ── Blockers ──
    blockers = [t for t in tasks if t.get("status") == "blocked"]

    # ── Build HTML ──
    parts = []

    # Card wrapper
    has_blockers = len(blockers) > 0
    border_class = "border-danger-300 dark:border-danger-700/60" if has_blockers else "border-neutral-200 dark:border-neutral-800"
    bg_class = "bg-danger-50/30 dark:bg-danger-950/20" if has_blockers else "bg-white dark:bg-neutral-900/70"

    parts.append(f'<div class="{bg_class} {border_class} rounded-xl p-4 mb-6">')

    # Header
    parts.append('  <div class="flex items-center justify-between mb-3">')
    parts.append('    <h3 class="text-sm font-bold text-neutral-900 dark:text-white flex items-center gap-2">🎯 Bora Codar</h3>')
    if day_count > 0:
        parts.append(f'    <span class="text-[11px] font-mono tabular-nums px-2 py-0.5 rounded-full bg-primary-50 dark:bg-primary-600/20 text-primary-600 dark:text-primary-400 border border-primary-200 dark:border-primary-500/30">{day_count} tasks hoje</span>')
    parts.append('  </div>')

    # Grid: next task + day stats
    parts.append('  <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">')

    # ── Next task card ──
    if next_task:
        nt = next_task
        nt_num = str(nt.get("task_number") or "?")
        nt_title = nt.get("title") or "(sem título)"
        nt_agent = nt.get("agent") or ""
        nt_motor_raw = nt.get("motor") or ""
        # Short motor
        nt_motor = _short_motor(nt_motor_raw)
        # Clean hash not applicable here since task isn't done

        parts.append('    <div class="bg-primary-50/50 dark:bg-primary-950/30 border border-primary-200 dark:border-primary-800/50 rounded-lg p-3.5">')
        parts.append('      <p class="text-[10px] font-semibold uppercase tracking-wider text-primary-600 dark:text-primary-400 mb-1.5 flex items-center gap-1.5">')
        parts.append('        <span class="w-1.5 h-1.5 rounded-full bg-primary-500 animate-pulse-slow"></span>')
        parts.append('        Próxima task')
        parts.append('      </p>')
        parts.append(f'      <p class="text-sm font-bold text-neutral-900 dark:text-white leading-snug">')
        parts.append(f'        <span class="font-mono text-primary-600 dark:text-primary-400">task_{nt_num.zfill(2)}</span>')
        parts.append(f'        — {nt_title}')
        parts.append('      </p>')
        parts.append(f'      <div class="flex items-center gap-2 mt-1.5 text-[11px] text-neutral-500">')
        if nt_agent:
            parts.append(f'        <span>👤 {nt_agent}</span>')
        if nt_motor:
            parts.append(f'        <span>⚙️ {nt_motor}</span>')
        parts.append('      </div>')
        parts.append('    </div>')
    else:
        parts.append('    <div class="bg-neutral-50 dark:bg-neutral-800/30 border border-neutral-200 dark:border-neutral-700/50 rounded-lg p-3.5">')
        parts.append('      <p class="text-[10px] font-semibold uppercase tracking-wider text-neutral-500 mb-1.5">⬜ Próxima task</p>')
        parts.append('      <p class="text-sm text-primary-600 dark:text-primary-400 font-medium">🎉 Todas as tasks concluídas</p>')
        parts.append('    </div>')

    # ── Day stats + last executed ──
    parts.append('    <div class="space-y-2">')

    # Last completed
    if last_done:
        ld = last_done
        ld_num = str(ld.get("task_number") or "?")
        ld_title = ld.get("title") or "(sem título)"
        ld_hash = ld.get("commit_hash") or ""
        ld_hash_short = _clean_hash(str(ld_hash))[:7]
        ld_date = str(ld.get("updated_at") or ld.get("data_conclusao") or "")

        parts.append('      <div class="bg-neutral-50 dark:bg-neutral-800/30 border border-neutral-200 dark:border-neutral-700/50 rounded-lg p-3">')
        parts.append('        <p class="text-[10px] font-semibold uppercase tracking-wider text-neutral-500 mb-1">✅ Última executada</p>')
        parts.append(f'        <p class="text-xs font-medium text-neutral-900 dark:text-neutral-100 truncate">')
        parts.append(f'          <span class="font-mono text-primary-600/70 dark:text-primary-400/70">task_{ld_num.zfill(2)}</span>')
        parts.append(f'          — {ld_title}')
        parts.append('        </p>')
        if ld_date or ld_hash_short:
            parts.append(f'        <p class="text-[10px] text-neutral-500 mt-0.5 font-mono">{ld_date} · <span class="text-primary-600/60 dark:text-primary-400/60">🔗 {ld_hash_short}</span></p>')
        parts.append('      </div>')
    else:
        parts.append('      <div class="bg-neutral-50 dark:bg-neutral-800/30 border border-neutral-200 dark:border-neutral-700/50 rounded-lg p-3">')
        parts.append('        <p class="text-[10px] font-semibold uppercase tracking-wider text-neutral-500 mb-1">✅ Última executada</p>')
        parts.append('        <p class="text-xs text-neutral-400 italic">Nenhuma task concluída</p>')
        parts.append('      </div>')

    # Planned count
    if day_count > 0:
        parts.append('      <div class="flex items-center gap-3 text-xs text-neutral-500">')
        parts.append(f'        <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-neutral-400"></span> Planejadas: <strong class="text-neutral-900 dark:text-white font-mono">{day_planned}</strong></span>')
        parts.append(f'        <span class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-success-500"></span> Executadas: <strong class="text-neutral-900 dark:text-white font-mono">{day_done}</strong></span>')
        parts.append('      </div>')
    else:
        parts.append('      <p class="text-xs text-neutral-500">Nenhuma task planejada para hoje</p>')

    parts.append('    </div>')
    parts.append('  </div>')

    # ── Blockers alert ──
    if blockers:
        parts.append('  <div class="mt-3 pt-3 border-t border-danger-200 dark:border-danger-800/50">')
        parts.append(f'    <div class="flex items-start gap-2 bg-danger-50 dark:bg-danger-950/30 border border-danger-200 dark:border-danger-800/50 rounded-lg p-3">')
        parts.append('      <span class="text-base shrink-0 mt-0.5">🚫</span>')
        parts.append('      <div>')
        parts.append(f'        <p class="text-xs font-bold text-danger-600 dark:text-danger-400 mb-1">{len(blockers)} bloqueio(s)</p>')
        for b in blockers:
            b_title = b.get("title") or "(sem título)"
            b_num = str(b.get("task_number") or "?")
            parts.append(f'        <p class="text-xs text-danger-600/80 dark:text-danger-400/80">task_{b_num.zfill(2)} — {b_title}</p>')
        parts.append('      </div>')
        parts.append('    </div>')
        parts.append('  </div>')

    parts.append('</div>')
    return "\n".join(parts)
