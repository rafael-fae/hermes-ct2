#!/usr/bin/env -S uv run
"""
ct2.py — Control Tower V2 CLI

CLI para escanear projetos, gerar dashboard HTML e gerenciar
o workflow do Agent Team.

Uso:
    python3 ct2.py scan [--root ~/Dev] [--project slug] [--operational]
    python3 ct2.py build [--project slug] [--output output/dashboard.html]
    python3 ct2.py dash [--project slug]
    python3 ct2.py status [--project slug]
    python3 ct2.py project list
    python3 ct2.py project add <path> [--slug nome]
    python3 ct2.py project remove <slug>
    python3 ct2.py project config <slug> [--stack "Python/Django"] [--deploy "https://..."]
    python3 ct2.py project scan <slug>
    python3 ct2.py task start <projeto> <id>
    python3 ct2.py task done <projeto> <id> --hash <sha>
    python3 ct2.py task audit <projeto> <id> --veredito <aprovado|rejeitado>
    python3 ct2.py task next <projeto>
    python3 ct2.py fix-sprint1
    python3 ct2.py github sync <projeto>
    python3 ct2.py github refresh-cache [projeto]
    python3 ct2.py briefing <projeto>
    python3 ct2.py briefing --all
"""

import argparse
import hashlib
import os
import sys
import webbrowser
import subprocess
from datetime import datetime
import json
import time

# Adiciona o diretório raiz ao path para imports absolutos
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from src.db import get_connection, init_db, get_agent_status, search_tasks, insert_project
from src.db import get_project_by_slug, get_project_tasks, dedup_auditorias, relink_orphan_auditorias
# [C1.3] from src.builder import Builder — removido, builder.py movido para archive/
from src.github_integration import get_repo_info, get_issues, get_ci_status, link_issue_url
from src.project_discovery import discover_projects, add_project, list_projects, remove_project, update_project, get_project, extract_description, detect_deploy_url
from src.briefing import generate_briefing
from src.context_pack import build_context_pack, render_context_pack
from src.planner import run_planner_pipeline
from src.notifier import push_notification, get_notifications, clear_notifications, mark_as_read


# ─── Helpers ──────────────────────────────────────────────────────────

def _ensure_db():
    """Garante que o banco existe, inicializado e com migrations aplicadas."""
    db_path = os.path.join(PROJECT_ROOT, "state", "ct2.db")
    if not os.path.isfile(db_path):
        print("📦 Inicializando banco de dados...")
        init_db()
    # Bootstrap reprodutível: aplica migrations pendentes silenciosamente.
    # Sem isto, daily_plans/project_index e as colunas de monitor faltam num
    # clone novo e `build`/`task` quebram. (idempotente — no-op se nada pendente)
    try:
        conn = get_connection(db_path)
        applied = _apply_migrations(conn, verbose=False)
        conn.close()
        if applied:
            print(f"🗃️  {applied} migration(s) aplicada(s) automaticamente.")
    except Exception as e:
        print(f"⚠️  Aviso ao aplicar migrations: {e}")
    return db_path


def _get_project(conn, slug):
    """Obtém projeto ou aborta se não existir."""
    proj = get_project_by_slug(conn, slug)
    if not proj:
        print(f"❌ Projeto '{slug}' não encontrado. Use 'ct2.py project list' para ver disponíveis.")
        sys.exit(1)
    return proj


# ─── Commands ─────────────────────────────────────────────────────────

def cmd_scan(args):
    """Escaneia projetos e popula SQLite."""
    db = _ensure_db()
    conn = get_connection(db)

    root = os.path.expanduser(args.root) if args.root else os.path.expanduser("~/Dev")
    print(f"🔍 Escaneando projetos em: {root}")

    def _run_operational_scan(project_path, slug):
        """Executa scanners operacionais (agent seed + diario)."""
        if not os.path.isdir(project_path):
            return
        try:
            from src.db import seed_agents
            c = get_connection(db)
            count = seed_agents(conn=c)
            c.close()
            print(f"     Agent seed: {count} agentes")
        except ImportError:
            pass
        except Exception as e:
            print(f"     ⚠️  Agent seed warning: {e}")

        try:
            from src.diario_scanner import DiarioScanner
            diario_path = os.path.expanduser(
                "~/.hermes/profiles/orchestrator/operacional/DIARIO.md"
            )
            if os.path.isfile(diario_path):
                ds = DiarioScanner()
                c = get_connection(db)
                count = ds.scan(diario_path, c)
                c.close()
                print(f"     Diario scan: {count} auditorias")
        except ImportError:
            pass
        except Exception as e:
            print(f"     ⚠️  Operacional scan warning: {e}")

    if args.project:
        # Scan de projeto específico
        proj = _get_project(conn, args.project)
        print(f"  → Projeto: {proj['name']} ({proj['slug']})")
        print(f"  → Path: {proj['path']}")

        # Tentar carregar scanner
        try:
            from src.scanner import Scanner
            scanner = Scanner()
            result = scanner.scan(proj["path"])
            print(f"  ✅ Scan concluído: {result}")
        except ImportError:
            print("  ⚠️ Scanner não disponível (tasks 01-02 pendentes). Projeto registrado no banco.")
        except Exception as e:
            print(f"  ⚠️ Erro no scanner: {e}")

        # Scan operacional se solicitado
        if args.operational:
            _run_operational_scan(proj["path"], proj["slug"])
    else:
        # Scan de todos os projetos
        discovered = discover_projects(root)
        if not discovered:
            print("  Nenhum projeto encontrado.")
            conn.close()
            return

        for proj in discovered:
            from src.db import insert_project
            insert_project(
                conn,
                slug=proj["slug"],
                name=proj["name"],
                path=proj["path"],
                repo_url=proj["repo_url"],
                stack=proj["stack"],
            )
            print(f"  ✅ Projeto registrado: {proj['slug']} — {proj['name']}")

            # Tentar scanner
            if not args.no_scan:
                try:
                    from src.scanner import Scanner
                    scanner = Scanner()
                    scanner.scan(proj["path"])
                    print(f"     Scan concluído para {proj['slug']}")
                except ImportError:
                    pass  # Scanner não disponível — ok
                except Exception as e:
                    print(f"     ⚠️  Scan warning: {e}")

            # Scan operacional se solicitado
            if args.operational:
                _run_operational_scan(proj["path"], proj["slug"])

    # Seed fixo de agentes (sempre, não depende de --operational)
    try:
        from src.db import seed_agents
        c = get_connection(db)
        seed_count = seed_agents(conn=c)
        c.close()
        print(f"🤖 Agentes seed: {seed_count} agentes")
    except Exception as e:
        print(f"     ⚠️  Agent seed warning: {e}")

    # Fix: associar tasks da Sprint 1 (1-45) ao sprint_id correto
    try:
        c = get_connection(db)
        c.execute("""
            UPDATE tasks SET sprint_id = (SELECT id FROM sprints WHERE project_id=5 AND number=1)
            WHERE project_id = (SELECT id FROM projects WHERE slug='example-project')
            AND task_number BETWEEN 1 AND 45
            AND sprint_id IS DISTINCT FROM (SELECT id FROM sprints WHERE project_id=5 AND number=1)
        """)
        updated = c.execute("SELECT changes()").fetchone()[0]
        c.commit()
        c.close()
        if updated:
            print(f"🔄 Sprint 1 fix: {updated} tasks associadas")
    except Exception as e:
        print(f"     ⚠️  Sprint 1 fix warning: {e}")

    conn.close()
    print("✅ Scan concluído.")



# [C1.3] cmd_build removido — CT2 agora é API + MCP



# [C1.3] cmd_dash removido — CT2 agora é API + MCP


def cmd_status(args):
    """Exibe status rápido no terminal."""
    db = _ensure_db()
    conn = get_connection(db)

    proj_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    print(f"\n📊 Control Tower V2 — Status\n")
    print(f"Projetos monitorados: {proj_count}")

    if args.project:
        proj = _get_project(conn, args.project)
        print(f"\n── {proj['name']} ──")
        tasks = get_project_tasks(conn, args.project)
        if tasks:
            done = sum(1 for t in tasks if t["status"] == "done")
            blocked = sum(1 for t in tasks if t["status"] == "blocked")
            in_progress = sum(1 for t in tasks if t["status"] in ("in_progress", "running"))
            print(f"  Tasks: {len(tasks)} | ✅ {done} | 🔄 {in_progress} | 🚫 {blocked}")
        else:
            print("  Nenhuma task encontrada. Execute 'ct2.py scan' primeiro.")
    else:
        # Resumo de todos os projetos
        projects = conn.execute(
            "SELECT * FROM projects ORDER BY name"
        ).fetchall()
        for p in projects:
            p = dict(p)
            count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE project_id = ?", (p["id"],)
            ).fetchone()[0]
            print(f"  • {p['name']} ({p['slug']}) — {count} tasks")

    # Status dos agentes
    agents = get_agent_status(conn)
    if agents:
        print(f"\n── Agentes ──")
        for a in agents:
            icon = {"executing": "🟢", "idle": "⚪", "blocked": "🔴", "waiting": "🟡"}
            print(f"  {icon.get(a['status'], '⚪')} {a['agent']}: {a['status']}")
            if a.get("current_task_title"):
                print(f"     Task: {a['current_task_title']}")

    conn.close()


def cmd_agent(args):
    """Gerencia agentes (seed)."""
    db = _ensure_db()
    conn = get_connection(db)

    if args.action == "seed":
        from src.db import seed_agents
        count = seed_agents(conn=conn)
        print(f"✅ {count} agentes populados no banco.")
        print()
        rows = conn.execute(
            "SELECT agent, status, motor FROM agent_status ORDER BY agent"
        ).fetchall()
        for r in rows:
            print(f"  • {r['agent']}: {r['status']} ({r['motor']})")

    conn.close()


def cmd_project(args):
    """Gerencia projetos."""
    db = _ensure_db()

    if args.action == "list":
        projects = list_projects()
        if not projects:
            print("Nenhum projeto cadastrado.")
            return
        print(f"\n📁 Projetos ({len(projects)}):\n")
        for p in projects:
            stack = f" [{p['stack']}]" if p.get("stack") else ""
            repo = f" ({p['repo_url']})" if p.get("repo_url") else ""
            print(f"  • {p['name']} ({p['slug']}){stack}{repo}")
            print(f"    Path: {p['path']}")
            print()

    elif args.action == "add":
        path = os.path.expanduser(args.path)
        if not os.path.isdir(path):
            print(f"❌ Diretório não encontrado: {path}")
            return

        slug = args.slug if args.slug else os.path.basename(path)
        name = slug.replace("-", " ").replace("_", " ").title()

        from src.github_integration import detect_repo
        repo_info = detect_repo(path)
        repo_url = repo_info["repo_url"] if repo_info else None

        from src.project_discovery import _detect_stack
        stack = _detect_stack(path)

        conn = get_connection(db)
        insert_project(
            conn,
            slug=slug,
            name=name,
            path=path,
            repo_url=repo_url,
            stack=stack,
        )
        conn.close()
        print(f"✅ Projeto adicionado: {slug} ({name})")

        # Tentar scan
        try:
            from src.scanner import Scanner
            scanner = Scanner()
            scanner.scan(path)
            print(f"   Scan concluído para {slug}")
        except ImportError:
            print(f"   Scanner não disponível. Execute 'ct2.py scan' quando disponível.")
        except Exception as e:
            print(f"   ⚠️  Aviso: {e}")

    elif args.action == "remove":
        slug = args.slug
        if remove_project(slug):
            print(f"✅ Projeto removido: {slug}")
        else:
            print(f"❌ Projeto '{slug}' não encontrado.")

    elif args.action == "config":
        slug = args.slug
        proj = get_project(slug)
        if not proj:
            print(f"❌ Projeto '{slug}' não encontrado. Use 'ct2.py project list' para ver disponíveis.")
            return

        kwargs = {}
        if args.stack:
            kwargs["stack"] = args.stack
        if args.deploy:
            kwargs["deploy_url"] = args.deploy

        if not kwargs:
            print(f"📋 Configuração atual de '{slug}':")
            for k in ("name", "path", "stack", "repo_url", "deploy_url"):
                v = proj.get(k, "")
                print(f"  {k}: {v}")
            return

        if update_project(slug, **kwargs):
            print(f"✅ Projeto '{slug}' atualizado.")
            for k, v in kwargs.items():
                print(f"   {k}: {v}")
        else:
            print(f"⚠️ Nenhuma alteração em '{slug}'.")

    elif args.action == "scan":
        slug = args.slug
        proj = get_project(slug)
        if not proj:
            print(f"❌ Projeto '{slug}' não encontrado.")
            return

        print(f"🔍 Re-escanenado: {proj['name']} ({proj['path']})")
        path = proj["path"]

        try:
            from src.scanner import Scanner
            scanner = Scanner()
            result = scanner.scan(path)
            print(f"   ✅ Scan: {result}")
        except ImportError:
            print(f"   ⚠️ Scanner não disponível.")
        except Exception as e:
            print(f"   ⚠️  Erro no scan: {e}")

        # Atualizar last_scan
        conn = get_connection(db)
        conn.execute("UPDATE projects SET last_scan = datetime('now') WHERE slug = ?", (slug,))
        conn.commit()
        conn.close()
        print(f"   ✅ last_scan atualizado.")


# ─── Task Commands ──────────────────────────────────────────────────────

def cmd_task_list(args):
    """Lista tasks do projeto via SQLite."""
    db = _ensure_db()
    conn = get_connection(db)

    proj = get_project_by_slug(conn, args.project)
    if not proj:
        print(f"❌ Projeto '{args.project}' não encontrado. Use 'ct2.py project list' para ver disponíveis.")
        conn.close()
        sys.exit(1)

    query = "SELECT * FROM project_index WHERE project_id = ?"
    params = [proj["id"]]

    if args.date:
        query += " AND date = ?"
        params.append(args.date)

    if args.status == "done":
        query += " AND status_execucao = ?"
        params.append("✅")
    elif args.status == "todo":
        query += " AND (status_execucao IS NULL OR status_execucao = ? OR status_execucao = ?)"
        params.extend([":white_large_square:", "⬜"])

    if args.module:
        query += " AND module = ?"
        params.append(args.module)

    query += " ORDER BY task_number ASC"

    rows = conn.execute(query, params).fetchall()

    if not rows:
        print("Nenhuma task encontrada para os filtros informados.")
        conn.close()
        return

    print("| # | Task | Agente | Descricao | Modulo | Exec | Audit | Commit |")
    print("|:-:|:----:|--------|-----------|:------:|:----:|:-----:|:------:|")

    for idx, row in enumerate(rows, 1):
        row_dict = dict(row)
        task_num = row_dict.get("task_number")
        task_str = f"task_{task_num:02d}" if task_num is not None else ""
        agent = row_dict.get("agent") or ""
        description = row_dict.get("description") or ""
        description = description.replace("\n", " ").strip()
        module = row_dict.get("module") or ""
        exec_status = row_dict.get("status_execucao") or ""
        audit_status = row_dict.get("status_auditoria") or ""
        commit_hash = row_dict.get("commit_hash") or ""
        if commit_hash and len(commit_hash) > 7:
            commit_hash = commit_hash[:7]

        print(f"| {idx} | {task_str} | {agent} | {description} | {module} | {exec_status} | {audit_status} | {commit_hash} |")

    print(f"**{len(rows)} tasks encontradas**")
    conn.close()


def cmd_task_create(args):
    """Cria nova task no SQLite + gera task_XX.md com template completo."""
    db = _ensure_db()
    conn = get_connection(db)
    proj = _get_project(conn, args.project)
    valid_agents = ["Agent-Backend", "Agent-Product", "Agent-Frontend", "Agent-DevOps"]
    if args.agent not in valid_agents:
        print("Agente invalido. Use: Agent-Backend, Agent-Product, Agent-Frontend, Agent-DevOps")
        conn.close()
        return
    valid_motors = ["codex", "zai", "agy", "claude"]
    if args.motor not in valid_motors:
        print("Motor invalido. Use: codex, zai, agy, claude")
        conn.close()
        return
    valid_priorities = ["alta", "media", "baixa"]
    priority = args.priority if args.priority else "media"
    if priority not in valid_priorities:
        print("Prioridade invalida. Use: alta, media, baixa")
        conn.close()
        return
    # Find next available task number (loop to avoid collisions)
    base_num = conn.execute(
        "SELECT COALESCE(MAX(task_number), 0) FROM tasks WHERE project_id = ?",
        (proj["id"],)
    ).fetchone()[0]
    next_num = int(base_num) + 1
    while conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ? AND task_number = ?",
        (proj["id"], next_num)
    ).fetchone()[0] > 0:
        next_num += 1
    priority_emoji_map = {"alta": "\U0001f534", "media": "\U0001f7e1", "baixa": "\U0001f7e2"}
    p_emoji = priority_emoji_map.get(priority, "\U0001f7e1")
    motor_labels = {
        "codex": "Codex gpt-5.5 - EXCLUSIVO",
        "zai": "zai glm-5.2 - EXCLUSIVO",
        "agy": "agy Gemini 3.5 Flash (Medium) - EXCLUSIVO",
        "claude": "Claude Opus 4.7 - EXCLUSIVO",
    }
    motor_label = motor_labels.get(args.motor, f"{args.motor} - EXCLUSIVO")
    motor_commands = {
        "codex": "~/.local/bin/codex exec --skip-git-repo-check -m gpt-5.5",
        "zai": "~/.local/bin/zai -m glm-5.2",
        "agy": "~/.local/bin/agy --model Gemini 3.5 Flash (Medium) --print --dangerously-skip-permissions",
        "claude": "~/.local/bin/claude --print --dangerously-skip-permissions --effort max",
    }
    motor_cmd = motor_commands.get(args.motor, f"~/.local/bin/{args.motor}")
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute("""
        INSERT INTO tasks (project_id, project_slug, task_number, title, description,
                          agent, motor, modulo, priority, status_execucao, status_auditoria, day)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (proj["id"], proj["slug"], next_num, args.title,
         args.description or "", args.agent, args.motor,
         args.module or "", priority,
         ":white_large_square:", ":white_large_square:", today))
    conn.execute("""
        INSERT INTO project_index (project_id, date, task_number, title, agent, description, module,
                                   status_execucao, status_auditoria)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (proj["id"], today, next_num, args.title, args.agent,
         args.description or "", args.module or "",
         ":white_large_square:", ":white_large_square:"))
    conn.commit()
    task_file_name = f"task_{next_num:02d}.md"
    today_dir = today
    target_dir = os.path.join(PROJECT_ROOT, "planejamento-diario", today_dir)
    os.makedirs(target_dir, exist_ok=True)
    task_path = os.path.join(target_dir, task_file_name)
    if args.scope:
        scope_lines = args.scope.replace("\\n", "\n")
    else:
        scope_lines = "1. Item 1\n2. Item 2"
    template = f"""# Task {next_num:02d} - {args.title}

**Agente:** {args.agent}
**Motor:** {motor_label}
**Prioridade:** {p_emoji} {priority.upper()}

---

## Contexto

{args.description or "Task criada via ct2.py task create"}

---

## COMANDO

`{motor_cmd}`

\u26a0\ufe0f **REGRA ABSOLUTA:** O COMANDO cont\u00e9m APENAS o caminho absoluto do CLI + flags + modelo. NUNCA inclua o prompt/instru\u00e7\u00e3o textual dentro do COMANDO.

**Se o motor falhar, PARAR - reportar nesta thread e aguardar nova ordem.**

---

## ESCOPO

{scope_lines}

## RESTRICOES

- Nao modificar arquivos fora do escopo
- Commit padrao: `feat(ct2): task_{next_num:02d} - {args.title}`

## RECURSOS

- [Listar arquivos/diretorios relevantes]

## REGRAS

- Report nesta thread (MESMA thread)
- Motor exclusivo - falhou = PARAR + reportar + aguardar
- Atualizar task_XX.md checkboxes + Conclusao ANTES de reportar
- Aguardar auditoria do Orchestrator antes de fechar

---

## Checklist

- [ ] Implementar escopo
- [ ] Testar
- [ ] Commit + push

---

## Conclusao (preenchido pelo AGENTE)

**Agente:**
**Concluida em:** DD/MM/AAAA HH:MM
**Motor utilizado:**
**Observacoes:**
"""
    with open(task_path, "w", encoding="utf-8") as f:
        f.write(template)
    conn.close()
    print(f"Task #{next_num} criada no projeto '{args.project}':")
    print(f"  Titulo: {args.title}")
    print(f"  Agente: {args.agent}")
    print(f"  Motor: {args.motor}")
    print(f"  Prioridade: {priority}")
    print(f"  Task file: planejamento-diario/{today_dir}/{task_file_name}")


def cmd_task_close(args):
    """Fecha task completamente: done + audit + project_index em 1 comando."""
    import re
    db = _ensure_db()
    conn = get_connection(db)
    proj = _get_project(conn, args.project)
    task_num = int(args.id)

    # 1. Validar se task existe
    task = conn.execute(
        "SELECT * FROM tasks WHERE project_id = ? AND task_number = ?",
        (proj["id"], task_num),
    ).fetchone()
    if not task:
        print(f"❌ Task #{task_num} não encontrada no projeto '{args.project}'.")
        conn.close()
        return
    task = dict(task)

    # 2. Validar commit hash formato
    if not re.match(r'^[a-f0-9]{7,40}$', args.hash):
        print(f"❌ Hash de commit inválido: '{args.hash}'. Deve ser um SHA hexadecimal (7-40 caracteres).")
        conn.close()
        return

    # 3. Validar se já está concluída
    if task.get("status") == "done" and task.get("status_execucao") in ("✅", ":white_check_mark:"):
        print(f"⚠️  Task #{task_num} já está concluída (status=done, status_execucao={task['status_execucao']}).")
        print(f"   {task['title']}")
        conn.close()
        return

    # 4. Validar veredito
    veredito = args.veredito if args.veredito else "aprovado"
    if veredito not in ("aprovado", "rejeitado"):
        print("❌ Veredito inválido. Use --veredito aprovado ou --veredito rejeitado.")
        conn.close()
        return

    # 5. Executar updates (tudo no mesmo comando)
    audit_hash = args.audit_hash if args.audit_hash else ""

    # Update 1: done + commit_hash + data_conclusao
    conn.execute(
        """UPDATE tasks
           SET status='done', status_execucao='✅', commit_hash=?,
               data_conclusao=date('now'), updated_at=datetime('now')
           WHERE id=?""",
        (args.hash, task["id"]),
    )

    # Update 2: auditoria
    conn.execute(
        """UPDATE tasks
           SET status_auditoria='👁', audit_hash=?, veredito=?,
               audit_date=date('now'), updated_at=datetime('now')
           WHERE id=?""",
        (audit_hash, veredito, task["id"]),
    )

    # Update 3: project_index
    conn.execute(
        """UPDATE project_index
           SET status_execucao='✅', status_auditoria='👁', commit_hash=?, audit_hash=?
           WHERE project_id=? AND task_number=?""",
        (args.hash, audit_hash, proj["id"], task_num),
    )

    conn.commit()

    # 6. Notificação
    push_notification(
        "task_done",
        f"task_{task_num} concluida e auditada",
        task_data={
            "task_number": task_num,
            "agente": task.get("agent", ""),
            "hash": args.hash,
        },
    )

    # 7. Summary
    print(f"✅ Task #{task_num} fechada com sucesso!")
    print(f"   Projeto: {args.project}")
    print(f"   Título:  {task['title']}")
    print(f"   Status:  done | Execução: ✅ | Auditoria: 👁")
    print(f"   Commit:  {args.hash}")
    print(f"   Veredito: {veredito}")
    if audit_hash:
        print(f"   Audit Hash: {audit_hash}")
    print(f"   Project index: atualizado")

    conn.close()


def cmd_task_dispatch(args):
    """📡 Dispara task delegada: cria delegations entry + active.json + info para iniciar monitor."""
    db = _ensure_db()
    conn = get_connection(db)

    # 1. Validar se task existe
    proj = _get_project(conn, args.project)
    task_num = int(args.id)
    task = conn.execute(
        "SELECT * FROM tasks WHERE project_id = ? AND task_number = ?",
        (proj["id"], task_num),
    ).fetchone()
    if not task:
        print(f"\u274c Task #{task_num} não encontrada no projeto '{args.project}'.")
        conn.close()
        return
    task = dict(task)

    # 2. Gerar Context Pack antes de persistir a delegação. O recall do
    # Hindsight é best-effort e não bloqueia quando o serviço local está fora.
    context_pack = build_context_pack(conn, dict(proj), task)
    context_preview = render_context_pack(context_pack)

    # 3. Criar entrada em delegations
    import time as _time
    delegation_id_prefix = "dispatch-{}-{}".format(task_num, int(_time.time()))
    conn.execute(
        """INSERT INTO delegations
           (delegation_id, agent, motor, goal, channel, thread_ts,
            monitor_state, status, dispatched_at, context_preview,
            context_pack_json, context_pack_created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'waiting_auth', 'dispatched',
                   datetime('now'), ?, ?, datetime('now'))""",
        (delegation_id_prefix, task.get("agent", ""), task.get("motor", ""),
         "task_{}: {}".format(task_num, task.get("title", "")),
         args.channel, args.thread_ts, context_preview,
         json.dumps(context_pack, ensure_ascii=False)),
    )
    conn.commit()
    delegation_row = conn.execute(
        "SELECT id FROM delegations WHERE delegation_id = ?", (delegation_id_prefix,)
    ).fetchone()
    delegation_pk = delegation_row["id"]

    # 4. Injetar o handoff na thread Slack. Falha externa não perde o pack nem
    # impede que a delegação seja monitorada.
    delivery_status = "skipped"
    delivery_error = None
    if not getattr(args, "no_context_post", False):
        try:
            from src.slack_client import load_slack_token, post_thread_message

            post_thread_message(
                load_slack_token(), args.channel, args.thread_ts, context_preview
            )
            delivery_status = "posted"
        except Exception as exc:
            delivery_status = "failed"
            delivery_error = "{}: {}".format(type(exc).__name__, str(exc)[:300])
            print("⚠️  Context Pack persistido, mas não publicado no Slack: {}".format(exc))
    context_pack["delivery"] = {
        "status": delivery_status,
        "error": delivery_error,
    }
    conn.execute(
        "UPDATE delegations SET context_pack_json=? WHERE id=?",
        (json.dumps(context_pack, ensure_ascii=False), delegation_pk),
    )
    conn.execute(
        """
        INSERT INTO audit_log (
            actor, action, project_slug, entity_type, entity_id, detail
        ) VALUES ('ct2', ?, ?, 'delegation', ?, ?)
        """,
        (
            "context_pack_" + delivery_status,
            proj["slug"],
            delegation_pk,
            json.dumps(
                {
                    "task_number": task_num,
                    "hindsight": context_pack["hindsight"]["status"],
                    "files": len(context_pack["relevant_files"]),
                    "related_tasks": len(context_pack["related_tasks"]),
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()

    # 5. Salvar threads/active.json
    threads_dir = os.path.expanduser("~/.hermes/profiles/orchestrator/threads")
    os.makedirs(threads_dir, exist_ok=True)
    import json as _json
    active = {
        "thread_ts": args.thread_ts,
        "channel": args.channel,
        "task_number": task_num,
        "delegation_id": delegation_pk,
        "monitor_state": "waiting_auth",
        "project": args.project,
        "auto_authorizations": [],
        "session_heartbeat": None,
        "cron_last_tick": None,
        "context_pack": context_pack,
        "context_preview": context_preview,
    }
    with open(os.path.join(threads_dir, "active.json"), "w") as _f:
        _json.dump(active, _f, indent=2, ensure_ascii=False)

    # 6. Print info para iniciar bg monitor
    monitor_rel = "bin/ct2_monitor.py"
    print("\U0001f4e1 Task #{} delegada \u2014 monitor_state=waiting_auth".format(task_num))
    print("   Delega\u00e7\u00e3o: {} (id={})".format(delegation_id_prefix, delegation_pk))
    print("   Canal: {} | Thread: {}".format(args.channel, args.thread_ts))
    print("   active.json salvo em: {}".format(os.path.join(threads_dir, "active.json")))
    print("   Context Pack: {} | Hindsight: {} | Slack: {}".format(
        len(context_pack["relevant_files"]),
        context_pack["hindsight"]["status"],
        delivery_status,
    ))
    print("")
    print(context_preview)
    print("")
    print("   Para iniciar monitor em bg:")
    print("   uv run python {} --watch --thread {} --deleg-id {}".format(monitor_rel, args.thread_ts, delegation_pk))
    print("   Com watch_patterns: MONITORANDO, AUTORIZADO, concluida")

    conn.close()


def cmd_task(args):
    """Gerencia tasks (start, done, audit, next)."""
    db = _ensure_db()
    conn = get_connection(db)

    # --- task start <projeto> <id> ---
    if args.action == "start":
        proj = _get_project(conn, args.project)
        task_num = int(args.id)
        task = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND task_number = ?",
            (proj["id"], task_num),
        ).fetchone()
        if not task:
            print(f"❌ Task #{task_num} não encontrada no projeto '{args.project}'.")
            conn.close()
            return
        conn.execute(
            "UPDATE tasks SET status_execucao='✅', updated_at=datetime('now') WHERE id=?",
            (task["id"],),
        )
        conn.commit()
        print(f"✅ Task #{task_num} marcada como executada (status_execucao='✅').")
        print(f"   {task['title']}")

    # --- task done <projeto> <id> --hash <sha> ---
    elif args.action == "done":
        proj = _get_project(conn, args.project)
        task_num = int(args.id)
        task = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND task_number = ?",
            (proj["id"], task_num),
        ).fetchone()
        if not task:
            print(f"❌ Task #{task_num} não encontrada no projeto '{args.project}'.")
            conn.close()
            return
        task = dict(task)
        conn.execute(
            "UPDATE tasks SET status='done', status_execucao='✅', commit_hash=?, updated_at=datetime('now') WHERE id=?",
            (args.hash, task["id"]),
        )
        conn.commit()
        print(f"✅ Task #{task_num} marcada como executada com commit.")
        print(f"   {task['title']}")
        print(f"   Commit: {args.hash}")
        push_notification(
            "task_done",
            f"task_{task_num} concluída por {task.get('agent', '?')}",
            task_data={
                "task_number": task_num,
                "agente": task.get("agent", ""),
                "hash": args.hash,
            },
        )

    # --- task audit <projeto> <id> --veredito <aprovado|rejeitado> ---
    elif args.action == "audit":
        proj = _get_project(conn, args.project)
        task_num = int(args.id)
        task = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? AND task_number = ?",
            (proj["id"], task_num),
        ).fetchone()
        if not task:
            print(f"❌ Task #{task_num} não encontrada no projeto '{args.project}'.")
            conn.close()
            return
        task = dict(task)
        veredito = args.veredito
        if veredito not in ("aprovado", "rejeitado"):
            print("❌ Veredito inválido. Use --veredito aprovado ou --veredito rejeitado.")
            conn.close()
            return
        conn.execute(
            "UPDATE tasks SET status_auditoria='👁', veredito=?, audit_date=date('now'), audit_hash=?, updated_at=datetime('now') WHERE id=?",
            (veredito, args.hash, task["id"]),
        )
        conn.commit()
        print(f"👁 Task #{task_num} auditada (status_auditoria='👁', veredito={veredito}).")
        print(f"   {task['title']}")
        push_notification(
            "task_audit",
            f"task_{task_num} auditada — {veredito}",
            task_data={
                "task_number": task_num,
                "agente": task.get("agent", ""),
                "hash": task.get("commit_hash", ""),
            },
        )

    # --- task next <projeto> [--json] ---
    elif args.action == "next":
        proj = _get_project(conn, args.project)
        task = conn.execute("""
            SELECT * FROM tasks
            WHERE project_id = ? AND (status_execucao IS NULL OR status_execucao = '⬜')
            ORDER BY task_number ASC
            LIMIT 1
        """, (proj["id"],)).fetchone()
        if not task:
            if getattr(args, 'json', False):
                import json as json_lib
                print(json_lib.dumps({"done": True, "message": f"Todas as tasks de '{args.project}' estão executadas!"}, ensure_ascii=False))
            else:
                print(f"✅ Todas as tasks de '{args.project}' estão executadas!")
            conn.close()
            return
        if getattr(args, 'json', False):
            import json as json_lib
            output = {
                "task_number": task["task_number"],
                "title": task["title"],
                "agent": task["agent"],
                "motor": task["motor"],
                "day": task["day"],
                "modulo": task["modulo"],
            }
            print(json_lib.dumps(output, ensure_ascii=False))
        else:
            print(f"⏭  Próxima task a executar em '{args.project}':")
            print(f"   #{task['task_number']}: {task['title']}")
            if task['day']:
                print(f"   Dia: {task['day']}")
            if task['modulo']:
                print(f"   Módulo: {task['modulo']}")

    # --- task backfill-conclusao <projeto> ---
    elif args.action == "backfill-conclusao":
        proj = _get_project(conn, args.project)
        project_path = proj["path"]
        print(f"🔍 Buscando tasks done sem data_conclusao em '{args.project}'...")

        # Tasks que estão done mas não têm data_conclusao
        tasks = conn.execute("""
            SELECT * FROM tasks
            WHERE project_id = ? AND status_execucao = '✅' AND (data_conclusao IS NULL OR data_conclusao = '')
            ORDER BY task_number ASC
        """, (proj["id"],)).fetchall()

        if not tasks:
            print("✅ Nenhuma task pendente de backfill.")
            conn.close()
            return

        print(f"📋 {len(tasks)} tasks para backfill.")

        import re
        import subprocess
        updated = 0
        skipped = 0
        for t_row in tasks:
            t = dict(t_row)
            tnum = t["task_number"]
            raw_hash = (t.get("commit_hash") or "").strip()

            date_str = None

            # Helper: extrai primeiro hash hex de uma string
            def _first_hash(s):
                m = re.search(r'[a-f0-9]{7,40}', s)
                return m.group(0) if m else None

            # Estratégia 1: Usar commit_hash da DB
            if raw_hash and raw_hash != "—":
                clean = _first_hash(raw_hash)
                if clean:
                    try:
                        result = subprocess.run(
                            ["git", "show", "-s", "--format=%ai", clean],
                            cwd=project_path,
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            date_str = result.stdout.strip().split()[0]  # YYYY-MM-DD
                    except Exception:
                        pass

            # Estratégia 2: Fallback para git log no arquivo task_XX.md
            if not date_str:
                task_file = f"task_{tnum:02d}.md"
                try:
                    result = subprocess.run(
                        ["git", "log", "-1", "--format=%ai", "--", task_file],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        date_str = result.stdout.strip().split()[0]
                except FileNotFoundError:
                    print(f"  ⚠️ Task #{tnum}: git não encontrado ou diretório não é repositório")
                    break
                except subprocess.TimeoutExpired:
                    print(f"  ⚠️ Task #{tnum}: timeout ao executar git log")
                    break
                except Exception as e:
                    print(f"  ⚠️ Task #{tnum}: erro: {e}")

            if date_str:
                conn.execute(
                    "UPDATE tasks SET data_conclusao=?, updated_at=datetime('now') WHERE id=?",
                    (date_str, t["id"]),
                )
                conn.commit()
                method = "commit_hash" if _first_hash(raw_hash) else "task_file"
                print(f"  ✅ Task #{tnum}: {method} → data_conclusao={date_str}")
                updated += 1
            else:
                print(f"  ⚠️ Task #{tnum}: não foi possível determinar data — sem commit_hash válido nem git history para task_{tnum:02d}.md")
                skipped += 1

        print(f"✅ Backfill concluído: {updated} atualizadas, {skipped} ignoradas.")

    elif args.action == "create":
        cmd_task_create(args)

    elif args.action == "close":
        cmd_task_close(args)

    elif args.action == "list":
        cmd_task_list(args)

    elif args.action == "dispatch":
        cmd_task_dispatch(args)

    conn.close()


def cmd_audit(args):
    """Gerencia auditorias (relink + dedup)."""
    db = _ensure_db()
    conn = get_connection(db)

    if args.action == "dedup":
        # Phase 1: Relink orphan task_ids
        print("🔗 Buscando auditorias com task_id órfão...")
        relinked = relink_orphan_auditorias(conn)
        if relinked:
            print(f"  ✅ {len(relinked)} auditoria(s) relinkadas:")
            for aid, old, new in relinked:
                print(f"     - id={aid}: task_id {old} → {new}")
        else:
            print("  ✅ Nenhum órfão encontrado.")

        # Phase 2: Dedup
        print("\n🔍 Buscando duplicatas na tabela 'auditorias'...")
        deleted = dedup_auditorias(conn)
        print(f"  ✅ Deduplicação concluída: {deleted} registro(s) duplicado(s) removido(s).")

    conn.close()


# ─── Notify ──────────────────────────────────────────────────────────────

def cmd_notify(args):
    """Gerencia notificações (list, clear)."""
    if args.action == "list":
        notifications = get_notifications(limit=5)
        if not notifications:
            print("📭 Nenhuma notificação.")
            return
        print(f"📋 Últimas {len(notifications)} notificações:\n")
        for n in notifications:
            tipo = n.get("tipo", "?")
            msg = n.get("mensagem", "")
            ts = n.get("timestamp", "")[:19].replace("T", " ")
            tn = n.get("task_number")
            tn_str = f" #task_{tn}" if tn else ""
            print(f"  [{ts}] {tn_str}({tipo}) {msg}")
        print()

    elif args.action == "clear":
        clear_notifications()
        print("🗑️ Fila de notificações limpa.")

    else:
        print("❌ Ação desconhecida. Use: list | clear")


# ─── Fix Sprint 1 ───────────────────────────────────────────────────────

def cmd_fix_sprint1(args):
    """
    Corrige dados corrompidos do Sprint 1 (tasks 1-45 do example-project).

    O Sprint 1 (sprint_id=3, project_id=5) tem 45 tasks com `day` incorreto
    (todas com 2026-06-06 ou 2026-06-07). Distribui pelos dias reais baseado
    em sprint_dates e associa wave_id correto.
    """
    db = _ensure_db()
    conn = get_connection(db)

    proj = conn.execute("SELECT * FROM projects WHERE slug='example-project'").fetchone()
    if not proj:
        print("❌ Projeto 'example-project' não encontrado.")
        conn.close()
        return

    sprint = conn.execute(
        "SELECT * FROM sprints WHERE project_id=? AND number=1",
        (proj["id"],),
    ).fetchone()
    if not sprint:
        print("❌ Sprint 1 não encontrado para example-project.")
        conn.close()
        return
    sprint_id = sprint["id"]

    # --- 1) Buscar sprint_dates ordenadas por day_number ---
    sdates = conn.execute(
        "SELECT * FROM sprint_dates WHERE sprint_id=? ORDER BY day_number",
        (sprint_id,),
    ).fetchall()
    if not sdates:
        print("❌ Nenhum sprint_date encontrado para sprint_id=%s." % sprint_id)
        conn.close()
        return

    sd_map = {sd["day_number"]: sd["date"] for sd in sdates}
    print(f"📅 Sprint 1 dates: {sd_map}")

    # --- 2) Buscar tasks 1-45 ordenadas por task_number ---
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE project_id=? AND task_number BETWEEN 1 AND 45 ORDER BY task_number",
        (proj["id"],),
    ).fetchall()
    tasks = [dict(t) for t in tasks]
    if not tasks:
        print("❌ Nenhuma task encontrada (task_number 1-45).")
        conn.close()
        return
    print(f"📋 {len(tasks)} tasks encontradas para corrigir.")

    # --- 3) Distribuir tasks pelos days (grupos de ~7-8 tasks por dia) ---
    num_days = len(sdates)
    per_day = [len(tasks) // num_days] * num_days
    remainder = len(tasks) % num_days
    for i in range(remainder):
        per_day[i] += 1

    # Atribuir day a cada task baseado na distribuição
    task_day_map = {}  # task_number -> day_number
    idx = 0
    for day_num in range(num_days):
        for _ in range(per_day[day_num]):
            if idx < len(tasks):
                task_day_map[tasks[idx]["task_number"]] = day_num
                idx += 1

    print(f"📊 Distribuição: {per_day} tasks por dia")

    # --- 4) Buscar waves de project_id=5 no período do Sprint 1 ---
    sprint_dates_list = [sd["date"] for sd in sdates]
    waves = conn.execute("""
        SELECT * FROM waves
        WHERE project_id=?
          AND date IN ({})
        ORDER BY date, wave_number
    """.format(",".join("?" for _ in sprint_dates_list)),
        (proj["id"], *sprint_dates_list),
    ).fetchall()
    wave_map = {}  # (date, wave_number) -> wave_id
    for w in waves:
        wave_map[(w["date"], w["wave_number"])] = w["id"]
    print(f"🌊 {len(waves)} waves encontradas para Sprint 1")

    # Criar waves faltantes (2026-05-31 e 2026-06-05 podem não ter waves)
    created_waves = 0
    for dt in sprint_dates_list:
        # Verificar se já existe ao menos 1 wave para esta data
        existing = [w for w in waves if w["date"] == dt]
        if not existing:
            # Criar 2 waves padrão para esta data
            for wn in (1, 2):
                conn.execute(
                    "INSERT INTO waves (project_id, sprint_id, wave_number, date, status) VALUES (?, ?, ?, ?, 'planned')",
                    (proj["id"], sprint_id, wn, dt),
                )
                created_waves += 1
    if created_waves:
        conn.commit()
        # Recarregar waves
        waves = conn.execute("""
            SELECT * FROM waves
            WHERE project_id=?
              AND date IN ({})
            ORDER BY date, wave_number
        """.format(",".join("?" for _ in sprint_dates_list)),
            (proj["id"], *sprint_dates_list),
        ).fetchall()
        wave_map = {}
        for w in waves:
            wave_map[(w["date"], w["wave_number"])] = w["id"]
        print(f"  ➕ {created_waves} waves criadas para datas sem cobertura")

    # Link waves to sprint_id=3 if not already
    conn.execute(
        "UPDATE waves SET sprint_id=? WHERE project_id=? AND date IN ({}) AND sprint_id IS NULL".format(
            ",".join("?" for _ in sprint_dates_list)
        ),
        (sprint_id, proj["id"], *sprint_dates_list),
    )
    updated_waves = conn.execute("SELECT changes()").fetchone()[0]
    if updated_waves:
        print(f"🔄 {updated_waves} waves vinculadas ao sprint_id={sprint_id}")

    # --- 5) Determinar wave_number para cada task baseado no título ---
    def _detect_wave_number(title):
        """Detecta wave_number baseado no identificador no título da task (plano original)."""
        t = title or ""
        if "G11" in t or "Provisionamento" in t or "Provisioning" in t or "Healthcheck Tenant" in t:
            return 1  # W1 — Infra/Provisioning
        if "K1" in t or "Clínica" in t or "Clinica" in t or "RBAC" in t:
            return 2  # W2 — Clinica module
        if "K2" in t or "Profissional" in t or "Especialidade" in t:
            return 3  # W3 — Profissional module
        if "Auditoria" in t or "INDICE" in t or "PLANO" in t or "Revisão" in t or "Protocolos" in t:
            return 4  # W4 — Admin/Audit
        if "Piloto Cache" in t:
            return 1  # W1 — infra
        if "DS Teal" in t:
            return 3  # W3 — Profissional (Especialidades)
        return 2  # default to W2

    # --- 6) Executar updates ---
    updated_day = 0
    updated_wave = 0
    for t in tasks:
        tnum = t["task_number"]
        day_num = task_day_map.get(tnum)
        if day_num is None:
            continue
        correct_date = sd_map.get(day_num, "")
        if not correct_date:
            continue

        # Update day
        if t["day"] != correct_date:
            conn.execute(
                "UPDATE tasks SET day=?, updated_at=datetime('now') WHERE id=?",
                (correct_date, t["id"]),
            )
            updated_day += 1

        # Determine wave_number and find matching wave_id
        wave_number = _detect_wave_number(t["title"])
        wave_id = wave_map.get((correct_date, wave_number))
        if wave_id is not None and t.get("wave_id") != wave_id:
            conn.execute(
                "UPDATE tasks SET wave_id=?, updated_at=datetime('now') WHERE id=?",
                (wave_id, t["id"]),
            )
            updated_wave += 1

    conn.commit()
    conn.close()

    print(f"✅ Sprint 1 corrigido: {updated_day} days atualizados, {updated_wave} waves associadas.")
    print()
    print("   Distribuição final:")
    for day_num in range(num_days):
        date = sd_map.get(day_num, "?")
        tasks_on_day = [t for t in tasks if task_day_map.get(t["task_number"]) == day_num]
        print(f"     Day {day_num} ({date}): tasks {tasks_on_day[0]['task_number']}-{tasks_on_day[-1]['task_number']} ({len(tasks_on_day)} tasks)")


# ─── GitHub Sync ─────────────────────────────────────────────────────

def cmd_github_sync(args):
    """Sincroniza status de GitHub Issues com tasks locais.

    Varre tasks com gh_issue_number e sincroniza status.
    Se gh CLI disponível, faz 'gh issue close' para tasks marcadas como done.
    """
    db = _ensure_db()
    conn = get_connection(db)

    proj = _get_project(conn, args.project)
    project_path = proj.get("path", "")

    # Descobrir owner/repo
    repo_info = get_repo_info(project_path)
    if not repo_info:
        print(f"❌ Não foi possível detectar repositório GitHub para '{args.project}'.")
        conn.close()
        return

    owner = repo_info["owner"]
    repo = repo_info["repo_name"]

    print(f"🔗 GitHub Sync para {args.project} ({owner}/{repo})")
    print()

    # Buscar tasks com gh_issue_number
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE project_id=? AND gh_issue_number IS NOT NULL",
        (proj["id"],),
    ).fetchall()

    if not tasks:
        print("ℹ️  Nenhuma task com gh_issue_number encontrada.")
        conn.close()
        return

    print(f"📋 {len(tasks)} tasks com GitHub Issues vinculadas.")
    print()

    # Buscar issues abertas do GitHub
    issues = get_issues(owner, repo, state="all", limit=50)
    issue_map = {}
    for issue in issues:
        issue_map[issue["number"]] = issue["state"]

    # Verificar se gh CLI está disponível para fechar issues
    gh_available = False
    try:
        import subprocess
        subprocess.run(["gh", "--version"], capture_output=True, timeout=3)
        gh_available = True
    except (FileNotFoundError, Exception):
        pass

    closed_count = 0
    updated_count = 0

    for t in tasks:
        gh_num = t["gh_issue_number"]
        local_status = t.get("status") or ""
        gh_state = issue_map.get(gh_num, "unknown")

        print(f"  • Issue #{gh_num} — {t['title'][:60]}")
        print(f"    Local: {local_status} | GitHub: {gh_state}")

        # Se task local é done e issue está aberta, tentar fechar via gh CLI
        if local_status == "done" and gh_state == "open":
            if gh_available:
                try:
                    import subprocess
                    result = subprocess.run(
                        ["gh", "issue", "close", str(gh_num),
                         "--repo", f"{owner}/{repo}"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0:
                        print(f"    ✅ Issue #{gh_num} fechada no GitHub!")
                        closed_count += 1
                    else:
                        print(f"    ⚠️  Erro ao fechar issue #{gh_num}: {result.stderr.strip()}")
                except Exception as e:
                    print(f"    ⚠️  Erro ao fechar issue #{gh_num}: {e}")
            else:
                print(f"    ⚠️  gh CLI não disponível. Issue #{gh_num} permanece aberta.")

        # Se issue foi fechada no GitHub mas task não está done, avisar
        if gh_state == "closed" and local_status != "done":
            print(f"    💡 Issue #{gh_num} está fechada no GitHub, mas task local não está como 'done'.")

        print()

    if closed_count > 0:
        print(f"✅ {closed_count} issue(s) fechada(s) no GitHub.")
    print("✅ Sync concluído.")

    conn.close()


# ─── Serve ─────────────────────────────────────────────────────────────

def _format_http_log_message(format_string, args):
    """Formata logs do BaseHTTPRequestHandler com aridade variável."""
    try:
        return format_string % args
    except (TypeError, ValueError):
        return "{} {}".format(format_string, " ".join(map(str, args))).strip()


def cmd_serve(args):
    """Inicia servidor HTTP local com auto-refresh."""
    if args.daemon:
        pid = os.fork()
        if pid > 0:
            import time
            time.sleep(1)
            print(f"🚀 Servidor CT2 iniciado em daemon (PID {pid})")
            print(f"   Log: /tmp/ct2-server.log")
            sys.exit(0)
        log = open("/tmp/ct2-server.log", "w")
        os.dup2(log.fileno(), 1)  # stdout
        os.dup2(log.fileno(), 2)  # stderr
        log.close()

    import http.server
    import json as json_lib
    import urllib.parse
    import webbrowser

    output_dir = os.path.join(PROJECT_ROOT, "output")
    os.makedirs(output_dir, exist_ok=True)

    # [C1.3] Build inicial removido — CT2 agora é API + MCP
    # Dashboard HTML não é mais gerado localmente
    dashboard_path = os.path.join(output_dir, "dashboard.html")

    class CT2Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=output_dir, **kw)

        def _send_json(self, status_code, data):
            """Helper: send JSON response (same-origin; sem CORS permissivo)."""
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json_lib.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

        def _csrf_ok(self):
            """Anti-CSRF: exige o header custom X-CT2-Request nas rotas mutadoras.

            Um site externo não consegue enviar header custom cross-origin sem
            disparar um preflight CORS — que falha aqui (não há Access-Control
            permissivo). Logo, só o próprio dashboard (same-origin) passa.
            """
            return self.headers.get("X-CT2-Request") == "1"

        def _handle_slack_events(self):
            """Webhook do Slack Events API (F6). Verifica assinatura HMAC, responde
            o handshake de URL verification e dá ACK 200 nos eventos.

            Validado por assinatura (não por CSRF). Inerte até SLACK_SIGNING_SECRET
            estar configurado — assinatura inválida → 401."""
            try:
                from src.slack_events import (
                    load_signing_secret, verify_slack_signature, parse_event,
                )
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                ts = self.headers.get("X-Slack-Request-Timestamp", "")
                sig = self.headers.get("X-Slack-Signature", "")
                if not verify_slack_signature(load_signing_secret(), ts, raw, sig):
                    self._send_json(401, {"error": "assinatura Slack inválida"})
                    return
                try:
                    payload = json_lib.loads(raw) if raw else {}
                except ValueError:
                    self._send_json(400, {"error": "JSON inválido"})
                    return
                kind, value = parse_event(payload)
                if kind == "challenge":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(str(value).encode("utf-8"))
                    return
                # event/ignore → ACK rápido (Slack exige resposta <3s)
                self._send_json(200, {"ok": True, "kind": kind})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        def _get_db(self):
            """Helper: retorna (db_path, conn) com o banco atual."""
            db_path = _ensure_db()
            conn = get_connection(db_path)
            return db_path, conn

        def _get_proj_by_slug(self, conn, slug):
            """Helper: busca projeto por slug, retorna dict ou None."""
            row = conn.execute(
                "SELECT * FROM projects WHERE slug = ?", (slug,)
            ).fetchone()
            return dict(row) if row else None

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            # Redirect / → /dashboard.html
            if parsed.path == "/":
                self.send_response(302)
                self.send_header("Location", "/dashboard.html")
                self.end_headers()
                return

            if parsed.path == "/api/refresh":
                if not self._csrf_ok():
                    self._send_json(403, {"error": "Bloqueado (CSRF): header X-CT2-Request ausente."})
                    return
                try:
                    # [C1.3] Refresh via scan + consulta direta ao banco
                    # Build removido — CT2 agora é API + MCP
                    scan_args = argparse.Namespace(
                        root=args.root, project=args.project,
                        operational=True, no_scan=False,
                    )
                    cmd_scan(scan_args)

                    db_path = _ensure_db()
                    conn = get_connection(db_path)
                    try:
                        from src.metrics import compute_agent_metrics
                        agent_metrics = compute_agent_metrics(conn)
                    except Exception:
                        agent_metrics = []
                    try:
                        from src.approval_inbox import list_pending_approvals
                        approvals = list_pending_approvals(conn)
                    except Exception:
                        approvals = []
                    try:
                        from src.capacity import compute_motor_capacity
                        motor_capacity = compute_motor_capacity(conn)
                    except Exception:
                        motor_capacity = []
                    try:
                        from src.operations import compute_operations_room
                        operations = compute_operations_room(conn)
                    except Exception:
                        operations = {"lanes": [], "counts": {}}
                    try:
                        from src.notifier import get_notifications, get_notifications_html
                        notifs = get_notifications(limit=10)
                        n_html = get_notifications_html(limit=10)
                    except Exception:
                        notifs = []
                        n_html = ""
                    conn.close()
                    self._send_json(200, {
                        "success": True,
                        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "agentMetrics": agent_metrics,
                        "motorCapacity": motor_capacity,
                        "operations": operations,
                        "approvals": approvals,
                        "notifications": {
                            "html": n_html,
                            "unread_count": len([n for n in notifs if not n.get("read")]),
                            "items": notifs,
                        },
                    })
                except Exception as e:
                    import traceback
                    self._send_json(500, {"success": False, "error": f"{e}\n{traceback.format_exc()}"})
            elif parsed.path == "/api/status":
                try:
                    db_path = _ensure_db()
                    conn = get_connection(db_path)
                    proj_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                    task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                    conn.close()
                    self._send_json(200, {
                        "projects": proj_count,
                        "tasks": task_count,
                        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    })
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
            elif parsed.path.startswith("/api/task/") and parsed.path.endswith("/next"):
                # GET /api/task/<projeto>/next
                try:
                    # Extract project slug from path: /api/task/<projeto>/next
                    parts = parsed.path.split("/")
                    if len(parts) != 5:  # ["", "api", "task", "projeto", "next"]
                        self._send_json(400, {"error": "Formato inválido. Use /api/task/<projeto>/next"})
                        return
                    project_slug = parts[3]
                    db_path, conn = self._get_db()
                    proj = self._get_proj_by_slug(conn, project_slug)
                    if not proj:
                        conn.close()
                        self._send_json(404, {"error": f"Projeto '{project_slug}' não encontrado"})
                        return
                    task = conn.execute("""
                        SELECT * FROM tasks
                        WHERE project_id = ? AND (status_execucao IS NULL OR status_execucao = '⬜')
                        ORDER BY task_number ASC
                        LIMIT 1
                    """, (proj["id"],)).fetchone()
                    conn.close()
                    if not task:
                        self._send_json(200, {"done": True, "message": f"Todas as tasks de '{project_slug}' estão executadas!"})
                    else:
                        self._send_json(200, {
                            "task_number": task["task_number"],
                            "title": task["title"],
                            "day": task["day"],
                            "modulo": task["modulo"],
                        })
                except Exception as e:
                    import traceback
                    self._send_json(500, {"error": f"{e}\n{traceback.format_exc()}"})
            elif parsed.path == "/api/notifications":
                try:
                    from src.notifier import get_notifications, get_notifications_html
                    notifs = get_notifications(limit=20)
                    n_html = get_notifications_html(limit=20)
                    self._send_json(200, {
                        "html": n_html,
                        "unread_count": len([n for n in notifs if not n.get("read")]),
                        "items": notifs,
                    })
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
            elif parsed.path == "/api/approvals":
                try:
                    from src.approval_inbox import list_pending_approvals
                    _, conn = self._get_db()
                    approvals = list_pending_approvals(conn)
                    conn.close()
                    self._send_json(200, {"items": approvals, "count": len(approvals)})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
            elif parsed.path == "/api/operations":
                try:
                    from src.operations import compute_operations_room
                    _, conn = self._get_db()
                    operations = compute_operations_room(conn)
                    conn.close()
                    self._send_json(200, operations)
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
            else:
                super().do_GET()

        def do_POST(self):
            # Webhook do Slack: isento do gate CSRF (validado por assinatura HMAC).
            if urllib.parse.urlparse(self.path).path == "/api/slack/events":
                self._handle_slack_events()
                return
            if not self._csrf_ok():
                self._send_json(403, {"error": "Bloqueado (CSRF): header X-CT2-Request ausente."})
                return
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            parts = parsed.path.split("/")

            # ─── Approval Inbox ─────────────────────────────────────────
            if len(parts) == 5 and parts[1:3] == ["api", "approvals"]:
                try:
                    request_id = int(parts[3])
                except ValueError:
                    self._send_json(400, {"error": "ID de aprovação inválido"})
                    return
                decision = parts[4]
                if decision not in ("approve", "deny", "approve_once"):
                    self._send_json(400, {"error": "Decisão inválida"})
                    return

                from src.approval_inbox import (
                    ApprovalConflict,
                    ApprovalDeliveryError,
                    ApprovalNotFound,
                    decide_approval,
                )
                from src.slack_client import load_slack_token, post_thread_message

                conn = None
                try:
                    _, conn = self._get_db()
                    saved = decide_approval(
                        conn,
                        request_id,
                        decision,
                        actor="dashboard",
                        post_message=lambda channel, thread_ts, text: post_thread_message(
                            load_slack_token(), channel, thread_ts, text
                        ),
                    )
                    self._send_json(200, {"success": True, "approval": saved})
                except ApprovalNotFound as e:
                    self._send_json(404, {"error": str(e)})
                except ApprovalConflict as e:
                    self._send_json(409, {"error": str(e)})
                except (ApprovalDeliveryError, RuntimeError) as e:
                    self._send_json(502, {"error": str(e)})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})
                finally:
                    if conn is not None:
                        conn.close()
                return

            # ─── Notifications ────────────────────────────────────────────
            if len(parts) >= 4 and parts[1] == "api" and parts[2] == "notifications":
                action = parts[3]
                if action == "clear":
                    clear_notifications()
                    self._send_json(200, {"success": True, "message": "Todas as notificações foram limpas"})
                    return
                elif action == "read":
                    nid = params.get("id", [None])[0]
                    if not nid:
                        self._send_json(400, {"error": "Parâmetro ?id= é obrigatório para /api/notifications/read"})
                        return
                    ok = mark_as_read(nid)
                    if ok:
                        self._send_json(200, {"success": True, "message": "Notificação marcada como lida"})
                    else:
                        self._send_json(200, {"success": False, "message": "Notificação não encontrada"})
                    return
                else:
                    self._send_json(400, {"error": f"Ação inválida '{action}'. Use 'clear' ou 'read'"})
                    return

            # ─── Task actions ─────────────────────────────────────────────
            # Parse path: /api/task/<projeto>/<id>/<action>
            parts = parsed.path.split("/")
            if len(parts) != 6 or parts[1] != "api" or parts[2] != "task":
                self._send_json(400, {"error": "Formato inválido. Use /api/task/<projeto>/<id>/<action> ou /api/notifications/clear|read"})
                return

            project_slug = parts[3]
            try:
                task_num = int(parts[4])
            except ValueError:
                self._send_json(400, {"error": "ID da task deve ser um número"})
                return

            action = parts[5]

            if action not in ("start", "done", "audit"):
                self._send_json(400, {"error": f"Ação inválida '{action}'. Use start, done ou audit."})
                return

            try:
                db_path, conn = self._get_db()
                proj = self._get_proj_by_slug(conn, project_slug)
                if not proj:
                    conn.close()
                    self._send_json(404, {"error": f"Projeto '{project_slug}' não encontrado"})
                    return

                task = conn.execute(
                    "SELECT * FROM tasks WHERE project_id = ? AND task_number = ?",
                    (proj["id"], task_num),
                ).fetchone()
                if not task:
                    conn.close()
                    self._send_json(404, {"error": f"Task #{task_num} não encontrada no projeto '{project_slug}'"})
                    return
                task = dict(task)

                if action == "start":
                    conn.execute(
                        "UPDATE tasks SET status_execucao='✅', updated_at=datetime('now') WHERE id=?",
                        (task["id"],),
                    )
                    conn.commit()
                    conn.close()
                    self._send_json(200, {"success": True, "message": f"Task #{task_num} marcada como executada"})

                elif action == "done":
                    commit_hash = params.get("hash", [None])[0]
                    if not commit_hash:
                        conn.close()
                        self._send_json(400, {"error": "Parâmetro ?hash= é obrigatório para done"})
                        return
                    conn.execute(
                        "UPDATE tasks SET status_execucao='✅', commit_hash=?, updated_at=datetime('now') WHERE id=?",
                        (commit_hash, task["id"]),
                    )
                    conn.commit()
                    push_notification(
                        "task_done",
                        f"task_{task_num} concluída via dashboard",
                        task_data={
                            "task_number": task_num,
                            "agente": task.get("agent", ""),
                            "hash": commit_hash,
                        },
                    )
                    conn.close()
                    self._send_json(200, {"success": True, "message": f"Task #{task_num} marcada como executada com commit"})

                elif action == "audit":
                    veredito = params.get("veredito", [None])[0]
                    if not veredito or veredito not in ("aprovado", "rejeitado"):
                        conn.close()
                        self._send_json(400, {"error": "Parâmetro ?veredito=aprovado|rejeitado é obrigatório"})
                        return
                    conn.execute(
                        "UPDATE tasks SET status_auditoria='👁', veredito=?, updated_at=datetime('now') WHERE id=?",
                        (veredito, task["id"]),
                    )
                    conn.commit()
                    push_notification(
                        "task_audit",
                        f"task_{task_num} auditada — {veredito}",
                        task_data={
                            "task_number": task_num,
                            "agente": task.get("agent", ""),
                        },
                    )
                    conn.close()
                    self._send_json(200, {"success": True, "message": f"Task #{task_num} auditada (veredito={veredito})"})

            except Exception as e:
                import traceback
                self._send_json(500, {"error": f"{e}\n{traceback.format_exc()}"})

        def log_message(self, format, *args):
            print("  🌐 {}".format(_format_http_log_message(format, args)))

    port = args.port
    host = args.host

    print(f"\n{'='*50}")
    print(f"  🔥 Control Tower V2 — Servidor Local")
    print(f"  {'='*50}")
    print(f"  📍 http://{host}:{port}")
    print(f"  🔄 /api/refresh            — Atualizar dados")
    print(f"  📊 /api/status             — Status rápido")
    print(f"  ⏭  GET /api/task/<proj>/next   — Próxima task")
    print(f"  ✅ POST /api/task/<proj>/<id>/start — Iniciar task")
    print(f"  ✅ POST /api/task/<proj>/<id>/done  — Concluir task (?hash=xxx)")
    print(f"  👁  POST /api/task/<proj>/<id>/audit — Auditar task (?veredito=...)")
    print(f"  🧹 POST /api/notifications/clear   — Limpar notificações")
    print(f"  📨 POST /api/notifications/read    — Marcar lida (?id=xxx)")
    print(f"  🛡️  GET  /api/approvals            — Aprovações pendentes")
    print(f"  🛰️  GET  /api/operations           — Sala de Operações ao vivo")
    print(f"  🛡️  POST /api/approvals/<id>/<ação> — Aprovar/negar comando")
    print(f"  {'='*50}\n")

    if not args.no_open:
        webbrowser.open(f"http://{host}:{port}/dashboard.html")

    server = http.server.ThreadingHTTPServer((host, port), CT2Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Servidor encerrado.")
        server.server_close()


# ─── GitHub Commands ────────────────────────────────────────────────

def cmd_github(args):
    """Dispatcher para comandos github."""
    if args.action == "sync":
        cmd_github_sync(args)
    elif args.action == "refresh-cache":
        from src.github_cache import refresh_github_cache

        db = _ensure_db()
        conn = get_connection(db)
        if args.project:
            projects = [_get_project(conn, args.project)]
        else:
            projects = [
                dict(row) for row in conn.execute(
                    "SELECT * FROM projects WHERE repo_url LIKE '%github.com%' ORDER BY name"
                ).fetchall()
            ]

        if not projects:
            print("ℹ️  Nenhum projeto com repositório GitHub configurado.")
            conn.close()
            return

        refreshed = 0
        for project in projects:
            try:
                cache = refresh_github_cache(conn, project)
                refreshed += 1
                print(
                    f"✅ Cache GitHub atualizado: {project['slug']} "
                    f"({len(cache['github_issues'])} issues, "
                    f"{len(cache['github_data']['commits'])} commits)"
                )
            except Exception as exc:
                print(f"⚠️  Cache GitHub falhou para {project['slug']}: {exc}")
        conn.close()
        print(f"🐙 Cache atualizado para {refreshed}/{len(projects)} projeto(s).")
    else:
        print("❌ Ação desconhecida. Use: github sync | refresh-cache")


# ─── Briefing Command ──────────────────────────────────────────────────

def cmd_briefing(args):
    """Gera briefing markdown de um ou todos os projetos."""
    db = _ensure_db()

    if args.all:
        from src.project_discovery import list_projects
        projects = list_projects()
        if not projects:
            print("❌ Nenhum projeto cadastrado.")
            return
        for p in projects:
            print(generate_briefing(p["slug"]))
            print()
    else:
        print(generate_briefing(args.project))


def cmd_context_pack(args):
    """Gera um Context Pack sem criar delegação."""
    db = _ensure_db()
    conn = get_connection(db)
    try:
        project = _get_project(conn, args.project)
        task = conn.execute(
            "SELECT * FROM tasks WHERE project_id=? AND task_number=?",
            (project["id"], int(args.id)),
        ).fetchone()
        if not task:
            print(
                "❌ Task #{} não encontrada no projeto '{}'.".format(
                    args.id, args.project
                )
            )
            return
        pack = build_context_pack(conn, dict(project), dict(task))
        if args.json:
            print(json.dumps(pack, ensure_ascii=False, indent=2))
        else:
            print(render_context_pack(pack))
    finally:
        conn.close()


# ─── Plan Commands ──────────────────────────────────────────────────────

def cmd_plan(args):
    """Dispatcher para comandos plan (generate, apply, create, status)."""
    if args.action == "generate":
        _cmd_plan_generate(args)
    elif args.action == "apply":
        _cmd_plan_apply(args)
    elif args.action == "create":
        _cmd_plan_create(args)
    elif args.action == "status":
        _cmd_plan_status(args)
    elif args.action == "list":
        _cmd_plan_list(args)
    else:
        print("❌ Ação desconhecida. Use: generate | apply | create | status")


def _cmd_plan_generate(args):
    """Gera e exibe/salva o plano markdown."""
    result = run_planner_pipeline(args.project)
    if result.get("error"):
        print(f"❌ {result['error']}")
        return

    markdown = result["markdown"]

    if args.output:
        output_path = os.path.expanduser(args.output)
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"✅ Plano salvo em: {output_path}")
    else:
        print(markdown)


def _cmd_plan_apply(args):
    """Gera o plano e escreve PLANO.md + INDICE.md no diretório do dia."""
    db = _ensure_db()
    conn = get_connection(db)
    proj = _get_project(conn, args.project)
    conn.close()

    today = datetime.now().strftime("%Y-%m-%d")
    project_path = proj["path"]

    # Diretório: planejamento-diario/YYYY-MM-DD/ sob o path do projeto
    target_dir = os.path.join(project_path, "planejamento-diario", today)
    plano_path = os.path.join(target_dir, "PLANO.md")
    indece_path = os.path.join(target_dir, "INDICE.md")

    # Verificar se já existe (proteção)
    if os.path.isfile(plano_path) and not args.force:
        print(f"⚠️  PLANO.md já existe para {today} em {plano_path}")
        resposta = input("  Sobrescrever? (s/N): ").strip().lower()
        if resposta != "s":
            print("❌ Operação cancelada.")
            return

    # Gerar o plano
    result = run_planner_pipeline(args.project)
    if result.get("error"):
        print(f"❌ {result['error']}")
        return

    os.makedirs(target_dir, exist_ok=True)

    # Escrever PLANO.md
    with open(plano_path, "w", encoding="utf-8") as f:
        f.write(result["markdown"])
    print(f"✅ PLANO.md salvo: {plano_path}")

    # Escrever INDICE.md
    indece_md = _generate_indice(result, args.project, today)
    with open(indece_path, "w", encoding="utf-8") as f:
        f.write(indece_md)
    print(f"✅ INDICE.md salvo: {indece_path}")


def _cmd_plan_create(args):
    """Cria/atualiza plano do dia no SQLite com contagem de tasks."""
    db = _ensure_db()
    conn = get_connection(db)
    proj = _get_project(conn, args.project)

    today = datetime.now().strftime("%Y-%m-%d")

    # Contar tasks do projeto
    total = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ?",
        (proj["id"],)
    ).fetchone()[0]

    # Contar tasks concluídas hoje (data_conclusao)
    done = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ? AND data_conclusao = ?",
        (proj["id"], today)
    ).fetchone()[0]

    # Contar tasks auditadas hoje
    audited = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE project_id = ? AND data_conclusao = ? AND status_auditoria = '👁'",
        (proj["id"], today)
    ).fetchone()[0]

    # Insert ou update
    conn.execute("""
        INSERT INTO daily_plans (project_id, date, focus, total_tasks, done_tasks, audited_tasks, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id, date) DO UPDATE SET
            focus = excluded.focus,
            total_tasks = excluded.total_tasks,
            done_tasks = excluded.done_tasks,
            audited_tasks = excluded.audited_tasks,
            notes = excluded.notes
    """, (proj["id"], today, args.focus or "", total, done, audited, args.notes or ""))
    conn.commit()
    conn.close()

    print(f"📋 Plano do dia '{today}' criado para '{args.project}':")
    print(f"   🎯 Foco: {args.focus or '—'}")
    print(f"   📊 Total: {total} | Concluídas: {done} | Auditadas: {audited}")
    if args.notes:
        print(f"   📝 Notas: {args.notes}")


def _cmd_plan_status(args):
    """Mostra o plano do dia com foco, progresso e tasks."""
    db = _ensure_db()
    conn = get_connection(db)
    proj = _get_project(conn, args.project)

    today = datetime.now().strftime("%Y-%m-%d")

    plan = conn.execute(
        "SELECT * FROM daily_plans WHERE project_id = ? AND date = ?",
        (proj["id"], today)
    ).fetchone()

    if plan:
        plan = dict(plan)
        print(f"\n📋 Plano do Dia — {today}")
        print(f"   🎯 Foco: {plan['focus'] or '—'}")
        print(f"   📊 Progresso: {plan['done_tasks']}/{plan['total_tasks']} concluídas, {plan['audited_tasks']} auditadas")
        if plan.get("notes"):
            print(f"   📝 Notas: {plan['notes']}")
        print()

        # Listar tasks do dia
        tasks = conn.execute(
            "SELECT task_number, title, agent, status, status_execucao, status_auditoria "
            "FROM tasks WHERE project_id = ? AND data_conclusao = ? ORDER BY task_number",
            (proj["id"], today)
        ).fetchall()
        if tasks:
            for t in tasks:
                se = t["status_execucao"] or "⬜"
                sa = t["status_auditoria"] or "⬜"
                print(f"   #{t['task_number']}: {t['title']} | 👤 {t['agent'] or '—'} | {se} {sa}")
        else:
            print("   Nenhuma task concluída hoje.")
    else:
        print(f"Nenhum plano registrado para '{args.project}' em {today}.")
        print("Use: ct2.py plan create <projeto> --focus \"...\"")

    conn.close()


def _cmd_plan_list(args):
    """Exibe plano do dia e tasks em formato markdown."""
    db = _ensure_db()
    conn = get_connection(db)
    proj = _get_project(conn, args.project)
    
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    
    plan = conn.execute(
        "SELECT date, focus, total_tasks, done_tasks, audited_tasks, notes "
        "FROM daily_plans WHERE project_id = ? AND date = ?",
        (proj["id"], date_str)
    ).fetchone()
    
    if plan:
        p = dict(plan)
        audited_pct = round(p["audited_tasks"] / p["done_tasks"] * 100) if p["done_tasks"] > 0 else 0
        
        print(f"# Plano do Dia - {date_str}")
        print(f"Foco: {p['focus'] or '—'}")
        print(f"Progresso: {p['done_tasks']}/{p['total_tasks']} concluídas ({audited_pct}% auditadas)")
        print("---")
        
        tasks = conn.execute(
            "SELECT task_number, title, agent, module, status_execucao, status_auditoria, commit_hash "
            "FROM project_index WHERE project_id = ? AND date = ? ORDER BY task_number",
            (proj["id"], date_str)
        ).fetchall()
        
        if tasks:
            for t in tasks:
                se = t["status_execucao"] or ":white_large_square:"
                sa = t["status_auditoria"] or ":white_large_square:"
                agent = t["agent"] or "—"
                module = f" [{t['module']}]" if t["module"] else ""
                commit = f" `{t['commit_hash'][:7]}`" if t["commit_hash"] else ""
                print(f"- #{t['task_number']} {t['title']} |{module} 👤 {agent} | {se} {sa}{commit}")
    else:
        print(f"Nenhum plano registrado para '{args.project}' em {date_str}.")
        print('Use: ct2.py plan create <projeto> --focus "..."')
    
    conn.close()


def _generate_indice(result: dict, project_slug: str, today: str) -> str:
    """Gera o conteúdo do INDICE.md diário a partir do resultado do planner."""
    waves_data = result.get("waves_data", [])
    tasks = result.get("tasks", [])
    pending_count = len(tasks)

    # Formata data como DD/MM/YYYY
    try:
        dt = datetime.strptime(today, "%Y-%m-%d")
        date_br = dt.strftime("%d/%m/%Y")
    except ValueError:
        date_br = today

    # Mapa task_number -> task info
    task_map = {}
    for t in tasks:
        tn = t.get("task_number", 0)
        task_map[tn] = t

    lines = []
    lines.append(f"# Índice de Tasks — {date_br}")
    lines.append("")
    lines.append(f"**Tasks planejadas:** {pending_count}")
    lines.append("")
    lines.append("| # | Task | Agente | Descrição | Módulo |")
    lines.append("|:-:|:----:|--------|-----------|:------:|")

    row_num = 0
    for wave in waves_data:
        for tn in wave.get("tasks", []):
            row_num += 1
            task_info = task_map.get(tn, {})
            title = task_info.get("title", f"task_{tn:02d}")[:50]
            agent = task_info.get("suggested_agent", wave.get("agent_suggestions", {}).get(tn, "—"))
            modulo = task_info.get("modulo", "—")
            desc = f"task_{tn:02d}"  # Short description based on task number
            lines.append(f"| {row_num:02d} | task_{tn:02d} | {agent} | {desc} | {modulo} |")

    lines.append("")
    return "\n".join(lines) + "\n"


def cmd_auto_start(args):
    """🚀 Auto-start: carrega briefing, mostra próxima task e pergunta se quer iniciar"""
    db = _ensure_db()
    conn = get_connection(db)

    project_slug = args.project
    if not project_slug:
        projects = list_projects(conn)
        if not projects:
            print("❌ Nenhum projeto cadastrado no banco.")
            conn.close()
            return
        project_slug = projects[0]["slug"]

    proj = _get_project(conn, project_slug)

    # Load briefing for the project
    briefing = generate_briefing(project_slug)

    # Check pending tasks (tasks where status_execucao is NULL or '⬜')
    task = conn.execute("""
        SELECT * FROM tasks
        WHERE project_id = ? AND (status_execucao IS NULL OR status_execucao = '⬜')
        ORDER BY task_number ASC
        LIMIT 1
    """, (proj["id"],)).fetchone()

    if not task:
        print(f"✅ Todas as tasks de '{project_slug}' estão executadas!")
        conn.close()
        return

    # Show the next pending task number + title + day + modulo + motor
    print(f"\n⏭  Próxima task pendente em '{project_slug}':")
    print(f"   Número: #{task['task_number']}")
    print(f"   Título: {task['title']}")
    print(f"   Dia: {task['day'] or '—'}")
    print(f"   Módulo: {task['modulo'] or '—'}")
    print(f"   Motor: {task['motor'] or '—'}")
    print()

    # Print a summary
    print(f"📊 Resumo do Briefing de '{project_slug}':")
    print(briefing)
    print()

    # Ask if user wants to start this task
    resposta = input("Deseja iniciar esta task? (s/N): ").strip().lower()
    if resposta == "s":
        conn.execute(
            "UPDATE tasks SET status_execucao='✅', updated_at=datetime('now') WHERE id=?",
            (task["id"],),
        )
        conn.commit()
        print(f"✅ Task #{task['task_number']} marcada como executada (status_execucao='✅').")
    else:
        print("❌ Operação cancelada.")

    conn.close()


def cmd_cron_setup(args):
    """Configura cron Hermes para cache+scan+build a cada 15min."""
    hermes_bin = os.path.expanduser("~/Dev/Hermes/.venv/bin/hermes")
    if not os.path.isfile(hermes_bin):
        print(f"❌ Hermes CLI não encontrado em: {hermes_bin}")
        sys.exit(1)

    schedule = "*/15 * * * *"
    name = "ct2-scan-build"
    workdir = os.path.expanduser("~/Dev/control-tower-v2")
    prompt = (
        "cd ~/Dev/control-tower-v2 && "
        "uv run python ct2.py github refresh-cache && "
        "uv run python ct2.py scan"  # [C1.3] build removido
    )

    print(f"⏰ Criando cron job Hermes: {name}")
    print(f"   Schedule: {schedule}")
    print(f"   Workdir:  {workdir}")
    print(f"   Comando:  {prompt}")
    print()

    cmd = [hermes_bin, "cron", "create", schedule,
           "--name", name,
           "--workdir", workdir,
           prompt]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("✅ Cron job criado com sucesso!")
            if result.stdout:
                print(result.stdout)
        else:
            print(f"❌ Falha ao criar cron job (exit code {result.returncode})")
            if result.stderr:
                print(f"   Erro: {result.stderr.strip()}")
            sys.exit(1)
    except FileNotFoundError:
        print(f"❌ Hermes CLI não executável em: {hermes_bin}")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("❌ Timeout ao criar cron job (30s)")
        sys.exit(1)


# ─── CLI Setup ────────────────────────────────────────────────────────

def _apply_migrations(conn, verbose=True):
    """Aplica migrations SQL pendentes. Retorna o número aplicado.

    Idempotente: no-op se não houver pendentes. Usado tanto por `cmd_migrate`
    (verbose) quanto por `_ensure_db` (silencioso, para bootstrap automático).
    """
    # 1. Garantir existência da tabela schema_migrations
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            checksum TEXT
        )
    """)
    conn.commit()

    # 2. Localizar diretório de migrations
    migrations_dir = os.path.join(PROJECT_ROOT, 'src', 'migrations')
    if not os.path.isdir(migrations_dir):
        if verbose:
            print(f"❌ Diretório de migrations não encontrado: {migrations_dir}")
        return 0

    # Listar arquivos .sql ordenados
    sql_files = sorted(f for f in os.listdir(migrations_dir) if f.endswith('.sql'))
    if not sql_files:
        if verbose:
            print("Nenhuma migration encontrada.")
        return 0

    # 3. Buscar migrations já aplicadas e filtrar pendentes
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    applied = {row['name'] for row in rows}
    pending = [f for f in sql_files if f.replace('.sql', '') not in applied]
    if not pending:
        return 0

    if verbose:
        print(f"📦 Migrations pendentes: {len(pending)}")

    # 4. Aplicar cada migration em ordem
    count = 0
    for filename in pending:
        filepath = os.path.join(migrations_dir, filename)
        if verbose:
            print(f"  → Aplicando {filename}...")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                sql = f.read()
        except OSError as e:
            if verbose:
                print(f"    ❌ Erro ao ler {filename}: {e}")
            return count

        # Separar statements; ignorar vazios e referências a schema_migrations
        statements = []
        for stmt in sql.split(';'):
            stmt = stmt.strip()
            if stmt and 'schema_migrations' not in stmt.upper():
                statements.append(stmt)

        checksum = hashlib.sha256(sql.encode('utf-8')).hexdigest()[:12]
        migration_name = filename.replace('.sql', '')

        try:
            for stmt in statements:
                conn.execute(stmt)

            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations "
                "(name, applied_at, checksum) VALUES (?, datetime('now'), ?)",
                (migration_name, checksum)
            )
            conn.commit()
            count += 1
            if verbose:
                print(f"    ✅ {filename} aplicada (checksum: {checksum})")
        except Exception as e:
            conn.rollback()
            if verbose:
                print(f"    ❌ Erro ao aplicar {filename}: {e}")
            return count

    return count


def cmd_migrate(args):
    """Executa migrations SQL pendentes (saída verbosa)."""
    db = _ensure_db()
    conn = get_connection(db)
    try:
        applied = _apply_migrations(conn, verbose=True)
        if applied == 0:
            print("✅ Todas as migrations já foram aplicadas.")
        else:
            print("✅ Migrations concluídas.")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        prog="ct2",
        description="🗼 Control Tower V2 — Torre de Controle do Agent Team",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Comandos principais:
  scan           Escaneia markdown e popula SQLite
  build          Gera dashboard HTML
  dash           scan + build + abre no browser
  status         Exibe status no terminal
  project        Gerencia projetos (list, add, remove, config, scan)
  briefing       Gera briefing markdown do projeto
  context-pack   Gera handoff estruturado para uma task

Exemplos:
  ct2.py scan                              # Descobre e escaneia todos os projetos
  ct2.py scan --project example-project       # Escaneia projeto específico
  ct2.py scan --operational                # Inclui ESTADO-DA-EQUIPE + DIARIO.md
  ct2.py build --project example-project      # Gera dashboard de um projeto
  ct2.py dash                              # scan + build + abre browser
  ct2.py project list                      # Lista projetos cadastrados
  ct2.py project add ~/Dev/meu-projeto     # Adiciona projeto manualmente
  ct2.py project add ~/Dev/meu-projeto --slug alias  # Adiciona com slug customizado
  ct2.py project remove meu-projeto        # Remove projeto do banco
  ct2.py project config meu-projeto --stack "Python/Django"  # Configura stack
  ct2.py project config meu-projeto --stack "JS/React" --deploy "https://..."  # Configura stack + deploy
  ct2.py project scan meu-projeto          # Re-escanear projeto específico
  ct2.py briefing example-project             # Briefing de um projeto
  ct2.py briefing --all                    # Briefing de todos os projetos
  ct2.py context-pack control-tower-v2 57  # Preview do handoff da task
        """,
    )

    parser.add_argument("--root", default="~/Dev",
                        help="Diretório raiz para descoberta de projetos (default: ~/Dev)")
    parser.add_argument("--project", default=None,
                        help="Slug do projeto específico")
    parser.add_argument("--output", default=None,
                        help="Caminho de saída do HTML (default: output/dashboard.html)")
    parser.add_argument("--operational", action="store_true",
                        help="Inclui scan de ESTADO-DA-EQUIPE.md e DIARIO.md")
    parser.add_argument("--no-scan", action="store_true",
                        help="Pula scan (apenas registra projetos)")

    subparsers = parser.add_subparsers(dest="command", help="Comandos disponíveis")

    # scan
    p_scan = subparsers.add_parser("scan", help="Escaneia markdown e popula SQLite")
    p_scan.add_argument("--root", default="~/Dev")
    p_scan.add_argument("--project")
    p_scan.add_argument("--operational", action="store_true")
    p_scan.add_argument("--no-scan", action="store_true")

    # [C1.3] build removido — CT2 agora é API + MCP

    # [C1.3] dash removido — CT2 agora é API + MCP

    # status
    p_status = subparsers.add_parser("status", help="Status rápido no terminal")
    p_status.add_argument("--project")

    # project
    p_proj = subparsers.add_parser("project", help="Gerencia projetos (list, add, remove, config, scan)")
    p_proj_sub = p_proj.add_subparsers(dest="action", help="Ações de projeto")

    p_proj_list = p_proj_sub.add_parser("list", help="Listar projetos cadastrados")
    p_proj_add = p_proj_sub.add_parser("add", help="Adicionar projeto manualmente")
    p_proj_add.add_argument("path", help="Caminho do projeto")
    p_proj_add.add_argument("--slug", help="Slug customizado (opcional, default: nome do diretório)")
    p_proj_remove = p_proj_sub.add_parser("remove", help="Remover projeto do banco")
    p_proj_remove.add_argument("slug", help="Slug do projeto a remover")
    p_proj_config = p_proj_sub.add_parser("config", help="Configurar stack/url de deploy do projeto")
    p_proj_config.add_argument("slug", help="Slug do projeto")
    p_proj_config.add_argument("--stack", help="Stack tecnológica (ex: Python/Django)")
    p_proj_config.add_argument("--deploy", help="URL de deploy (ex: https://app.vercel.com)")
    p_proj_scan = p_proj_sub.add_parser("scan", help="Re-escanear projeto específico")
    p_proj_scan.add_argument("slug", help="Slug do projeto a re-escanear")

    # serve
    p_serve = subparsers.add_parser("serve", help="🔥 Inicia servidor HTTP local com auto-refresh")
    p_serve.add_argument("--port", type=int, default=7890, help="Porta do servidor (default: 7890)")
    p_serve.add_argument("--host", default="127.0.0.1", help="Host do servidor (default: 127.0.0.1)")
    p_serve.add_argument("--project")
    p_serve.add_argument("--no-open", action="store_true", help="Não abrir navegador automaticamente")
    p_serve.add_argument("--daemon", action="store_true", help="Rodar em background (daemon)")

    # agent
    p_agent = subparsers.add_parser("agent", help="🤖 Gerencia agentes (seed)")
    p_agent_sub = p_agent.add_subparsers(dest="action", help="Ações de agente")
    p_agent_seed = p_agent_sub.add_parser("seed", help="Popula os 6 agentes fixos no banco")

    # task
    p_task = subparsers.add_parser("task", help="✅ Gerencia tasks (start, done, audit, next)")
    p_task_sub = p_task.add_subparsers(dest="action", help="Ações de task")

    p_task_start = p_task_sub.add_parser("start", help="Marca task como executada")
    p_task_start.add_argument("project", help="Slug do projeto")
    p_task_start.add_argument("id", help="Número da task")

    p_task_done = p_task_sub.add_parser("done", help="Marca task como executada com hash de commit")
    p_task_done.add_argument("project", help="Slug do projeto")
    p_task_done.add_argument("id", help="Número da task")
    p_task_done.add_argument("--hash", required=True, help="SHA do commit")

    p_task_audit = p_task_sub.add_parser("audit", help="Marca task como auditada")
    p_task_audit.add_argument("project", help="Slug do projeto")
    p_task_audit.add_argument("id", help="Número da task")
    p_task_audit.add_argument("--veredito", required=True, choices=["aprovado", "rejeitado"],
                              help="Veredito da auditoria")
    p_task_audit.add_argument("--hash", default=None, help="Hash do commit da auditoria")

    p_task_next = p_task_sub.add_parser("next", help="Mostra próxima task não executada")
    p_task_next.add_argument("project", help="Slug do projeto")
    p_task_next.add_argument("--json", action="store_true",
                             help="Saída em formato JSON")

    p_task_backfill = p_task_sub.add_parser("backfill-conclusao",
        help="Preenche data_conclusao das tasks done usando git log do projeto")
    p_task_backfill.add_argument("project", help="Slug do projeto")

    p_task_create = p_task_sub.add_parser("create", help="Cria nova task + gera task_XX.md")
    p_task_create.add_argument("project", help="Slug do projeto")
    p_task_create.add_argument("-t", "--title", required=True, help="Titulo da task")
    p_task_create.add_argument("-a", "--agent", required=True, help="Agente (Agent-Backend, Agent-Product, Agent-Frontend, Agent-DevOps)")
    p_task_create.add_argument("-m", "--motor", required=True, help="Motor (codex, zai, agy, claude)")
    p_task_create.add_argument("-d", "--description", help="Descricao/contexto da task")
    p_task_create.add_argument("-M", "--module", help="Modulo do projeto")
    p_task_create.add_argument("-p", "--priority", choices=["alta", "media", "baixa"], default="media", help="Prioridade (default: media)")
    p_task_create.add_argument("-s", "--scope", help="Escopo detalhado da task (use aspas)")

    p_task_close = p_task_sub.add_parser("close", help="🔒 Fecha task completamente: done + audit + project_index em 1 comando")
    p_task_close.add_argument("project", help="Slug do projeto")
    p_task_close.add_argument("id", help="Número da task")
    p_task_close.add_argument("--hash", required=True, help="SHA do commit de conclusão")
    p_task_close.add_argument("--audit-hash", default=None, help="Hash da auditoria (opcional)")
    p_task_close.add_argument("--veredito", choices=["aprovado", "rejeitado"], default="aprovado",
                              help="Veredito da auditoria (default: aprovado)")

    p_task_list = p_task_sub.add_parser("list", help="📋 Lista tasks do projeto via SQLite (substituto do INDICE.md)")
    p_task_list.add_argument("project", help="Slug do projeto")
    p_task_list.add_argument("--date", help="Filtrar por data (YYYY-MM-DD)")
    p_task_list.add_argument("--status", choices=["todo", "done", "all"], default="all", help="Filtrar por status de execucao")
    p_task_list.add_argument("--module", help="Filtrar por modulo")

    p_task_dispatch = p_task_sub.add_parser("dispatch", help="\U0001f4e1 Dispara task: cria delegations + active.json + monitor bg")
    p_task_dispatch.add_argument("--id", required=True, help="N\u00famero da task")
    p_task_dispatch.add_argument("--channel", required=True, help="Canal Slack (ex: C0XXXXXX)")
    p_task_dispatch.add_argument("--thread-ts", required=True, help="Thread timestamp do Slack")
    p_task_dispatch.add_argument("--project", default="control-tower-v2", help="Slug do projeto (default: control-tower-v2)")
    p_task_dispatch.add_argument("--no-context-post", action="store_true",
                                 help="Persiste o Context Pack sem publicar na thread Slack")

    # audit
    p_audit = subparsers.add_parser("audit", help="Gerencia auditorias (dedup)")
    p_audit_sub = p_audit.add_subparsers(dest="action", help="Ações de auditoria")
    p_audit_sub.add_parser("dedup", help="Identifica e remove duplicatas de auditorias")

    # auto-start
    p_auto_start = subparsers.add_parser("auto-start", help="🚀 Auto-start: carrega briefing, mostra próxima task e pergunta se quer iniciar")
    p_auto_start.add_argument("--project", help="Slug do projeto (default: primeiro projeto)")

    # cron-setup
    p_cron = subparsers.add_parser("cron-setup", help="⏰ Configura cron Hermes para cache+scan+build a cada 15min")

    # fix-sprint1
    p_fix = subparsers.add_parser("fix-sprint1", help="🔧 Corrige dados corrompidos do Sprint 1 (day + wave)")

    # github
    p_github = subparsers.add_parser("github", help="🐙 Integração com GitHub (sync issues)")
    p_github_sub = p_github.add_subparsers(dest="action", help="Ações do GitHub")

    p_github_sync = p_github_sub.add_parser("sync", help="Sincroniza status de issues do GitHub com tasks locais")
    p_github_sync.add_argument("project", help="Slug do projeto")
    p_github_sync.add_argument("--close", action="store_true",
                               help="Fecha issues no GitHub para tasks done (via gh CLI)")

    p_github_cache = p_github_sub.add_parser(
        "refresh-cache", help="Atualiza cache GitHub fora do caminho do build"
    )
    p_github_cache.add_argument(
        "project", nargs="?", help="Slug do projeto (default: todos com GitHub)"
    )

    # briefing
    p_briefing = subparsers.add_parser("briefing", help="🗼 Gera briefing markdown do projeto")
    p_briefing.add_argument("project", nargs="?", help="Slug do projeto")
    p_briefing.add_argument("--all", action="store_true",
                            help="Briefing de todos os projetos")

    # context-pack
    p_context = subparsers.add_parser(
        "context-pack", help="🧳 Gera handoff estruturado para uma task"
    )
    p_context.add_argument("project", help="Slug do projeto")
    p_context.add_argument("id", help="Número da task")
    p_context.add_argument("--json", action="store_true", help="Saída JSON completa")

    # plan
    p_plan = subparsers.add_parser("plan", help="📋 Gera e aplica planos de execução (generate|apply|create|status|list)")
    p_plan_sub = p_plan.add_subparsers(dest="action", help="Ações do plano")

    p_plan_gen = p_plan_sub.add_parser("generate", help="Gera plano markdown com waves")
    p_plan_gen.add_argument("project", help="Slug do projeto")
    p_plan_gen.add_argument("--output", help="Caminho para salvar o plano (opcional, default: stdout)")

    p_plan_apply = p_plan_sub.add_parser("apply", help="Aplica plano: cria PLANO.md + INDICE.md no diretório do dia")
    p_plan_apply.add_argument("project", help="Slug do projeto")
    p_plan_apply.add_argument("--force", action="store_true",
                              help="Sobrescreve plano existente sem confirmação")

    p_plan_create = p_plan_sub.add_parser("create", help="📋 Cria/atualiza plano do dia no SQLite")
    p_plan_create.add_argument("project", help="Slug do projeto")
    p_plan_create.add_argument("--focus", help="Foco do dia")
    p_plan_create.add_argument("--notes", help="Notas/observações")

    p_plan_status = p_plan_sub.add_parser("status", help="📊 Mostra o plano do dia com foco e progresso")
    p_plan_status.add_argument("project", help="Slug do projeto")

    p_plan_list = p_plan_sub.add_parser("list", help="📋 Exibe plano do dia e tasks em markdown")
    p_plan_list.add_argument("project", help="Slug do projeto")
    p_plan_list.add_argument("--date", help="Data (YYYY-MM-DD, default: hoje)")

    # notify
    p_notify = subparsers.add_parser("notify", help="📬 Gerencia notificações (list, clear)")
    p_notify_sub = p_notify.add_subparsers(dest="action", help="Ações de notificação")
    p_notify_sub.add_parser("list", help="Lista últimas notificações")
    p_notify_sub.add_parser("clear", help="Limpa fila de notificações")

    # migrate
    p_migrate = subparsers.add_parser("migrate", help="🗃️  Executa migrations SQL pendentes")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Dispatch
    commands = {
        "scan": cmd_scan,
        # [C1.3] build removido
        # [C1.3] dash removido
        "status": cmd_status,
        "project": cmd_project,
        "serve": cmd_serve,
        "agent": cmd_agent,
        "task": cmd_task,
        "auto-start": cmd_auto_start,
        "cron-setup": cmd_cron_setup,
        "fix-sprint1": cmd_fix_sprint1,
        "github": cmd_github,
        "audit": cmd_audit,
        "briefing": cmd_briefing,
        "context-pack": cmd_context_pack,
        "plan": cmd_plan,
        "notify": cmd_notify,
        "migrate": cmd_migrate,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
