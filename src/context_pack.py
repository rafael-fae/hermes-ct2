"""Context Pack estruturado para handoff de tasks delegadas."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.planner import _paths_overlap, analyze_dependencies


PACK_VERSION = 1
DEFAULT_HINDSIGHT_URL = "http://127.0.0.1:9177"
DEFAULT_HINDSIGHT_BANK = "hermes"


def _task_spec_path(project, task):
    project_path = Path(os.path.expanduser(project.get("path") or ""))
    task_file = task.get("task_file") or "task_{:02d}.md".format(
        task["task_number"]
    )
    candidates = []
    if task.get("day"):
        candidates.append(
            project_path / "planejamento-diario" / str(task["day"]) / task_file
        )
    candidates.extend(
        sorted(
            (project_path / "planejamento-diario").glob("**/{}".format(task_file)),
            reverse=True,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _read_task_excerpt(path, max_chars=6000):
    if not path:
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + "\n… [task spec truncado]"


def recall_hindsight(query, timeout=1.5, opener=None):
    """Consulta Hindsight local via API oficial, sempre best-effort."""
    base_url = os.environ.get("CT2_HINDSIGHT_URL", DEFAULT_HINDSIGHT_URL).rstrip(
        "/"
    )
    bank_id = os.environ.get("CT2_HINDSIGHT_BANK_ID", DEFAULT_HINDSIGHT_BANK)
    url = "{}/v1/default/banks/{}/memories/recall".format(
        base_url, urllib.parse.quote(bank_id, safe="")
    )
    payload = json.dumps(
        {"query": query, "budget": "low", "max_tokens": 800}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    api_key = os.environ.get("CT2_HINDSIGHT_API_KEY")
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(
        url, data=payload, headers=headers, method="POST"
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        decisions = []
        for result in data.get("results") or []:
            text = str(result.get("text") or "").strip()
            if text and text not in decisions:
                decisions.append(text[:700])
            if len(decisions) >= 5:
                break
        return {
            "status": "ok",
            "query": query,
            "bank_id": bank_id,
            "decisions": decisions,
        }
    except (
        OSError,
        TimeoutError,
        ValueError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ) as exc:
        return {
            "status": "unavailable",
            "query": query,
            "bank_id": bank_id,
            "decisions": [],
            "error": "{}: {}".format(type(exc).__name__, str(exc)[:200]),
        }


def _relevant_and_related(conn, project, task):
    candidates = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM tasks
            WHERE project_id = ?
            ORDER BY task_number
            """,
            (project["id"],),
        ).fetchall()
    ]
    project_path = project.get("path") or ""
    dependency_map = analyze_dependencies(candidates, project_path=project_path)
    task_number = task["task_number"]
    relevant_files = dependency_map.get(task_number, [])
    target_module = (task.get("modulo") or "").strip().lower()

    ranked = []
    for candidate in candidates:
        number = candidate["task_number"]
        if candidate["id"] == task["id"]:
            continue
        candidate_paths = dependency_map.get(number, [])
        overlap = bool(
            relevant_files
            and candidate_paths
            and _paths_overlap(relevant_files, candidate_paths)
        )
        same_module = bool(
            target_module
            and (candidate.get("modulo") or "").strip().lower() == target_module
        )
        distance = abs((number or 0) - (task_number or 0))
        if not overlap and not same_module and distance > 2:
            continue
        score = (3 if overlap else 0) + (2 if same_module else 0)
        ranked.append((score, -distance, candidate))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

    related = []
    for _, _, candidate in ranked[:5]:
        related.append(
            {
                "task_number": candidate["task_number"],
                "title": candidate["title"],
                "status": candidate["status"],
                "agent": candidate["agent"] or "",
                "commit_hash": candidate["commit_hash"] or "",
                "paths": dependency_map.get(candidate["task_number"], [])[:8],
            }
        )
    return relevant_files[:12], related


def _recent_audits(conn, project_id, related_tasks):
    numbers = [item["task_number"] for item in related_tasks]
    if not numbers:
        return []
    placeholders = ",".join("?" for _ in numbers)
    rows = conn.execute(
        """
        SELECT t.task_number, a.veredito, a.ressalva, a.observacoes,
               a.audit_hash, a.created_at
        FROM auditorias a
        JOIN tasks t ON t.id = a.task_id
        WHERE t.project_id = ? AND t.task_number IN ({})
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 5
        """.format(placeholders),
        [project_id, *numbers],
    ).fetchall()
    return [dict(row) for row in rows]


def build_context_pack(conn, project, task, recall=None):
    """Monta o handoff sem alterar estado externo."""
    project = dict(project)
    task = dict(task)
    relevant_files, related_tasks = _relevant_and_related(
        conn, project, task
    )
    spec_path = _task_spec_path(project, task)
    spec_relative = ""
    if spec_path:
        try:
            spec_relative = str(spec_path.relative_to(Path(project["path"])))
        except (ValueError, KeyError):
            spec_relative = str(spec_path)
        if spec_relative not in relevant_files:
            relevant_files.insert(0, spec_relative)

    query_parts = [
        project["slug"],
        "task_{}".format(task["task_number"]),
        task.get("title") or "",
        task.get("modulo") or "",
        " ".join(relevant_files[:6]),
        "decisões padrões pitfalls",
    ]
    query = " ".join(part for part in query_parts if part).strip()
    hindsight = (recall or recall_hindsight)(query)

    return {
        "version": PACK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "id": project["id"],
            "slug": project["slug"],
            "path": project.get("path") or "",
            "stack": project.get("stack") or "",
        },
        "task": {
            "id": task["id"],
            "number": task["task_number"],
            "title": task.get("title") or "",
            "description": task.get("description") or "",
            "module": task.get("modulo") or "",
            "priority": task.get("priority") or "",
            "agent": task.get("agent") or "",
            "motor": task.get("motor") or "",
            "spec_path": spec_relative,
            "spec_excerpt": _read_task_excerpt(spec_path),
        },
        "relevant_files": relevant_files[:12],
        "related_tasks": related_tasks,
        "recent_audits": _recent_audits(
            conn, project["id"], related_tasks
        ),
        "hindsight": hindsight,
        "handoff_rules": [
            "Leia o task spec e os arquivos relevantes antes de editar.",
            "Use hindsight_recall com a query fornecida antes de buscar histórico em logs.",
            "Preserve alterações existentes fora do escopo.",
            "Execute os testes indicados pelo projeto antes do handoff.",
        ],
    }


def render_context_pack(pack, max_chars=3500):
    """Renderiza resumo Markdown adequado para a thread Slack."""
    task = pack["task"]
    project = pack["project"]
    lines = [
        "🧳 *Context Pack v{} — task_{}*".format(
            pack["version"], task["number"]
        ),
        "• Projeto: `{}` — `{}`".format(project["slug"], project["path"]),
        "• Objetivo: {}".format(task["title"]),
    ]
    if task.get("spec_path"):
        lines.append("• Spec: `{}`".format(task["spec_path"]))

    lines.append("\n*Arquivos relevantes (planner)*")
    if pack["relevant_files"]:
        lines.extend("• `{}`".format(path) for path in pack["relevant_files"])
    else:
        lines.append("• Nenhum path explícito detectado; use o task spec como fonte.")

    lines.append("\n*Tasks relacionadas*")
    if pack["related_tasks"]:
        for related in pack["related_tasks"]:
            commit = " [{}]".format(related["commit_hash"][:7]) if related["commit_hash"] else ""
            lines.append(
                "• task_{} — {} ({}){}".format(
                    related["task_number"],
                    related["title"],
                    related["status"],
                    commit,
                )
            )
    else:
        lines.append("• Nenhuma relação forte detectada.")

    lines.append("\n*Decisões do Hindsight*")
    decisions = pack["hindsight"].get("decisions") or []
    if decisions:
        lines.extend("• {}".format(text.replace("\n", " ")) for text in decisions)
    else:
        lines.append(
            "• Recall indisponível/vazio. Execute `hindsight_recall(\"{}\")` antes de começar.".format(
                pack["hindsight"]["query"]
            )
        )

    if pack["recent_audits"]:
        lines.append("\n*Auditorias relacionadas*")
        for audit in pack["recent_audits"]:
            detail = audit["ressalva"] or audit["observacoes"] or "sem observação"
            lines.append(
                "• task_{} — {}: {}".format(
                    audit["task_number"], audit["veredito"], detail[:240]
                )
            )

    lines.append("\n*Regras de handoff*")
    lines.extend("• {}".format(rule) for rule in pack["handoff_rules"])
    rendered = "\n".join(lines)
    if len(rendered) > max_chars:
        return rendered[: max_chars - 31].rstrip() + "\n… [Context Pack truncado]"
    return rendered
