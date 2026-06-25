"""
src/planner.py — Análise de Dependências + Alocação de Agentes

Módulo de planejamento do CT V2 que analisa tasks pendentes no banco,
identifica dependências entre elas (mesmo módulo, mesmos arquivos),
agrupa em waves independentes e sugere qual agente alocar para cada task.

Usado pelo comando `ct2.py plan generate` (task_23).

Motor exclusivo: zai glm-5.2 (Jasnah)
"""

import glob
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ├── Caminhos ──────────────────────────────────────────────────────────

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "ct2.db",
)

OESTE_TASKS_DIR = os.path.expanduser(
    "~/Dev/oeste-gestao/planejamento-diario"
)

# ─── Seções a varrer nos task files ────────────────────────────────────

SECTIONS_TO_SCAN = ("RECURSOS", "ESCOPO")

# ─── Padrão de extensões de path a extrair ─────────────────────────────

PATH_EXTENSIONS = re.compile(r"\.(?:py|html|css|js|md|jinja2|toml|yaml|yml|cfg|ini|sh|env)$", re.IGNORECASE)

# ─── Padrão para extrair módulo do título ──────────────────────────────

# Pattern 1: "K1:", "G10:", "Orçamento:" — prefixo antes de dois-pontos
RE_MODULE_COLON = re.compile(r"^([A-Za-zÀ-ÿ0-9]+)\s*[:：]\s*")

# Pattern 2: "K1 something", "G10 Task" — código alfanumérico no início
RE_MODULE_CODE = re.compile(r"^([A-Za-z]\d+(?:[./]\d+)?)\s+")

# Pattern 3: detecta título de task que começa com "#" (arquivo md de review)
RE_HASH_TITLE = re.compile(r"^#\s+")

# ─── Agentes disponíveis ───────────────────────────────────────────────

AGENTS = ["Navani", "Jasnah", "Shallan", "Kaladin"]

# Motor fixo por agente (definido pela task_22.md)
AGENT_MOTOR_MAP = {
    "Navani": "Codex gpt-5.5",
    "Jasnah": "zai glm-5.2",
    "Shallan": "Opus 4.7",
    "Kaladin": "agy Gemini 3.5 Flash",
}


# ═══════════════════════════════════════════════════════════════════════
# a) get_pending_tasks
# ═══════════════════════════════════════════════════════════════════════

def get_pending_tasks(
    project_slug: str,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Retorna tasks com status='todo' do banco, ordenadas por task_number.

    Args:
        project_slug: Slug do projeto (ex: 'oeste-gestao')
        db_path: Caminho alternativo para o SQLite

    Returns:
        Lista de dicts: [{task_number, title, modulo, agent, motor, sprint_id}]
    """
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT t.task_number, t.title, t.modulo, t.agent, t.motor, t.sprint_id
        FROM tasks t
        JOIN projects p ON t.project_id = p.id
        WHERE p.slug = ? AND t.status = 'todo'
        ORDER BY t.task_number ASC
        """,
        (project_slug,),
    ).fetchall()

    conn.close()

    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# b) analyze_dependencies
# ═══════════════════════════════════════════════════════════════════════

def _extract_module_from_title(title: str) -> str:
    """Extrai código de módulo do título da task.

    Estratégias (mesmo fallback do builder.py):
    1. Prefixo antes de ':' (ex: 'K1:' → 'K1', 'Orçamento:' → 'Orçamento')
    2. Código alfanumérico no início (ex: 'K1 algo' → 'K1')
    3. Fallback: 'Geral'
    """
    if not title:
        return "Geral"

    # Ignora títulos que começam com '#' (arquivos md de revisão)
    if RE_HASH_TITLE.match(title):
        return "Geral"

    m = RE_MODULE_COLON.match(title)
    if m:
        return m.group(1)

    m = RE_MODULE_CODE.match(title)
    if m:
        return m.group(1)

    return "Geral"


def _scan_task_file(task_number: int, tasks_base_dir: str) -> list[str]:
    """Procura task_XX.md em 'sprint-*/YYYY-MM-DD/' e extrai paths.

    Args:
        task_number: Número da task (ex: 99, 100)
        tasks_base_dir: Diretório base (~/Dev/oeste-gestao/planejamento-diario)

    Returns:
        Lista de paths encontrados nas seções RECURSOS e ESCOPO
    """
    pattern = os.path.join(tasks_base_dir, "**", f"task_{task_number:02d}.md")
    matches = sorted(glob.glob(pattern, recursive=True))

    if not matches:
        return []

    # Pega o arquivo mais recente (último no sort)
    filepath = matches[-1]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return []

    paths: list[str] = []
    current_section: str | None = None

    for line in content.splitlines():
        stripped = line.strip()

        # Detecta seção markdown "## NOME" ou "### NOME"
        sec_match = re.match(r"^#{1,3}\s+(.+)$", stripped)
        if sec_match:
            section_name = sec_match.group(1).strip().upper()
            if section_name in ("RECURSOS", "ESCOPO"):
                current_section = section_name
            else:
                current_section = None
            continue

        if current_section is None:
            continue

        # Ignora linhas vazias, listas de checklist e separadores
        if not stripped or stripped.startswith("- [") or stripped.startswith("|---"):
            continue

        # Extrai paths: linhas com referências a arquivos
        # Pega tudo entre backticks ou após "- " que contiver extensão conhecida
        tokens = re.findall(r"`([^`]+)`", stripped)
        for token in tokens:
            token = token.strip()
            if _looks_like_filepath(token):
                paths.append(token)

        # Também tenta capturar paths inline sem backticks
        # (ex: "Ver apps/agenda/urls.py como referência")
        inline_paths = re.findall(
            r"\b([\w./-]+\.(?:py|html|css|js|md|jinja2|toml|yaml|yml|cfg|ini|sh|env))\b",
            stripped,
            re.IGNORECASE,
        )
        for p in inline_paths:
            if p not in paths:
                paths.append(p)

    return paths


def _looks_like_filepath(token: str) -> bool:
    """Checa se um token parece um caminho de arquivo relevante."""
    # Tem extensão de arquivo de código
    if PATH_EXTENSIONS.search(token):
        return True
    # É um path relativo com '/'
    if "/" in token and "." in token.split("/")[-1]:
        return True
    return False


def analyze_dependencies(
    tasks: list[dict[str, Any]],
    project_path: str = "",
) -> dict[int, list[str]]:
    """Para cada task, detecta paths de dependência.

    Procura o arquivo task_XX.md no diretório de planejamento do projeto.
    Se não encontrar, usa o campo 'modulo' da task como fallback.
    Se 'modulo' vazio, extrai do título.

    Args:
        tasks: Lista de tasks (saída de get_pending_tasks)
        project_path: Ignorado (usamos caminho fixo OESTE_TASKS_DIR)

    Returns:
        dict: {task_number: [paths detectados]}
    """
    result: dict[int, list[str]] = {}

    # Prefere o projeto solicitado; o caminho legado de Oeste é só fallback.
    candidate = os.path.join(os.path.expanduser(project_path), "planejamento-diario")
    tasks_base_dir = candidate if os.path.isdir(candidate) else OESTE_TASKS_DIR

    for task in tasks:
        task_number = task.get("task_number", 0)

        # Tenta ler o arquivo task_XX.md
        paths = _scan_task_file(task_number, tasks_base_dir)

        # Fallback: se não encontrou arquivo, usa 'modulo' da task
        if not paths:
            modulo = (task.get("modulo") or "").strip()
            if not modulo:
                modulo = _extract_module_from_title(task.get("title", ""))
            if modulo and modulo != "Geral":
                paths = [modulo]
            else:
                paths = []

        result[task_number] = paths

    return result


# ═══════════════════════════════════════════════════════════════════════
# c) group_waves
# ═══════════════════════════════════════════════════════════════════════

def _paths_overlap(paths_a: list[str], paths_b: list[str]) -> bool:
    """Verifica se duas listas de paths têm overlap significativo.

    Overlap se:
    - Algum path é idêntico
    - Um path é substring do outro
    - Ambos no mesmo módulo (string simples como 'Orçamento', 'CRC')
    """
    set_a = set(p.lower() for p in paths_a)
    set_b = set(p.lower() for p in paths_b)

    # Idênticos
    if set_a & set_b:
        return True

    # Substring (ex: 'apps/orcamento/models.py' overlap com 'apps/orcamento/')
    for a in paths_a:
        a_lower = a.lower()
        for b in paths_b:
            b_lower = b.lower()
            if a_lower in b_lower or b_lower in a_lower:
                return True

    return False


def group_waves(
    dependency_map: dict[int, list[str]],
    max_waves: int = 6,
    max_tasks_per_wave: int = 4,
) -> list[dict[str, Any]]:
    """Agrupa tasks em waves baseado em dependências.

    Tasks com paths overlapping NUNCA ficam na mesma wave.
    Máximo de 4 tasks por wave.

    Args:
        dependency_map: {task_number: [paths]}
        max_waves: Número máximo de waves (default: 6)
        max_tasks_per_wave: Máximo de tasks por wave (default: 4)

    Returns:
        Lista de waves: [{wave_number, tasks, agent_suggestions}]
    """
    task_numbers = sorted(dependency_map.keys())
    if not task_numbers:
        return []

    # Constrói matriz de conflito: tasks que NÃO podem ficar juntas
    conflict_pairs: set[tuple[int, int]] = set()
    for i, tn_a in enumerate(task_numbers):
        for tn_b in task_numbers[i + 1:]:
            if _paths_overlap(dependency_map.get(tn_a, []), dependency_map.get(tn_b, [])):
                conflict_pairs.add((tn_a, tn_b))
                conflict_pairs.add((tn_b, tn_a))

    # Algoritmo guloso: preenche waves sequencialmente
    pending = set(task_numbers)
    waves: list[dict[str, Any]] = []

    wave_num = 1
    while pending and wave_num <= max_waves:
        wave_tasks: list[int] = []
        candidates = sorted(pending)

        for tn in candidates:
            if len(wave_tasks) >= max_tasks_per_wave:
                break

            # Verifica se tn conflita com alguma task já na wave
            has_conflict = any(
                (tn, wt) in conflict_pairs for wt in wave_tasks
            )
            if not has_conflict:
                wave_tasks.append(tn)

        for tn in wave_tasks:
            pending.remove(tn)

        # Gera sugestões de agente para cada task da wave
        agent_suggestions: dict[int, str] = {}
        for tn in wave_tasks:
            # Usamos dados limitados aqui — o caller pode sobrescrever
            agent_suggestions[tn] = ""

        waves.append({
            "wave_number": wave_num,
            "tasks": sorted(wave_tasks),
            "agent_suggestions": agent_suggestions,
        })
        wave_num += 1

    # Tasks restantes (se excedeu max_waves) são colocadas na última wave
    if pending:
        for tn in sorted(pending):
            if len(waves[-1]["tasks"]) < max_tasks_per_wave:
                waves[-1]["tasks"].append(tn)
                waves[-1]["agent_suggestions"][tn] = ""
            else:
                waves.append({
                    "wave_number": wave_num,
                    "tasks": [tn],
                    "agent_suggestions": {tn: ""},
                })
                wave_num += 1
                pending.remove(tn)

    return waves


# ═══════════════════════════════════════════════════════════════════════
# d) suggest_agent
# ═══════════════════════════════════════════════════════════════════════

def suggest_agent(
    task_title: str,
    task_modulo: str,
    detected_paths: list[str],
) -> str:
    """Sugere qual agente alocar para uma task baseado no tipo de trabalho.

    Regras (da task_22.md):
    - Se path contém 'templates/' → Shallan
    - Se path contém 'models.py', 'serializers.py', 'admin.py' → Navani
    - Se path contém 'tests/', 'docs/' → Jasnah
    - Se path contém 'bin/', 'scripts/', 'deploy' → Kaladin
    - Se title contém 'test' → Jasnah
    - Se title contém 'template' ou 'html' → Shallan
    - Se title contém 'model' ou 'migration' ou 'admin' → Navani
    - Se title contém 'cron' ou 'script' ou 'deploy' → Kaladin
    - Fallback: Navani

    Args:
        task_title: Título da task
        task_modulo: Módulo da task
        detected_paths: Paths detectados no task file

    Returns:
        Nome do agente sugerido
    """
    title_lower = (task_title or "").lower()
    paths_str = " ".join(p.lower() for p in detected_paths)
    combined = f"{title_lower} {paths_str}"

    # ── Regras por path ──
    for path in detected_paths:
        p_lower = path.lower()

        if "templates/" in p_lower:
            return "Shallan"

        if any(kw in p_lower for kw in ("models.py", "serializers.py", "admin.py")):
            return "Navani"

        if any(kw in p_lower for kw in ("tests/", "docs/")):
            return "Jasnah"

        if any(kw in p_lower for kw in ("bin/", "scripts/", "deploy")):
            return "Kaladin"

    # ── Regras por título ──
    if re.search(r"\btest[s]?\b", title_lower, re.IGNORECASE):
        return "Jasnah"

    if re.search(r"\b(template|html)\b", title_lower, re.IGNORECASE):
        return "Shallan"

    if re.search(r"\b(model|migration|admin)\b", title_lower, re.IGNORECASE):
        return "Navani"

    if re.search(r"\b(cron|script|deploy)\b", title_lower, re.IGNORECASE):
        return "Kaladin"

    # ── Regras por combined (path + title) — captura casos extras ──
    if re.search(r"\btests?\b", combined, re.IGNORECASE):
        return "Jasnah"

    if re.search(r"\btemplate\b", combined, re.IGNORECASE):
        return "Shallan"

    if re.search(r"\bmodel\b", combined, re.IGNORECASE):
        return "Navani"

    # ── Fallback ──
    return "Navani"


# ═══════════════════════════════════════════════════════════════════════
# e) generate_plan_markdown
# ═══════════════════════════════════════════════════════════════════════

def generate_plan_markdown(
    project_slug: str,
    waves_data: list[dict[str, Any]],
    pending_count: int,
    done_count: int,
    focus: str = "Auto-plano gerado pelo CT V2 planner",
) -> str:
    """Gera o markdown do plano diário estilo PLANO.md.

    Formato similar ao TEMPLATE_PLANO.md:
    - Cabeçalho com data, foco, total de tasks
    - Tabela de distribuição por agente
    - Waves com tabelas #/Task/Agente/Descrição/Prio/Motor

    Args:
        project_slug: Slug do projeto
        waves_data: Lista de waves do group_waves()
        pending_count: Número de tasks pendentes
        done_count: Número de tasks concluídas
        focus: Texto de foco do dia

    Returns:
        String markdown formatada
    """
    today = date_type.today().strftime("%Y-%m-%d")
    total_tasks = pending_count + done_count
    num_waves = len(waves_data)

    # ── Contagem de tasks por agente ──
    agent_task_count: dict[str, int] = defaultdict(int)
    for wave in waves_data:
        for tn, agent_name in wave.get("agent_suggestions", {}).items():
            if agent_name:
                agent_task_count[agent_name] += 1

    # Se agent_suggestions vazio, distribui equitativamente
    if not any(agent_task_count.values()):
        # Tenta inferir pela ordem: Navani, Jasnah, Shallan, Kaladin
        all_tasks = []
        for wave in waves_data:
            all_tasks.extend(wave.get("tasks", []))
        for i, tn in enumerate(all_tasks):
            agent = AGENTS[i % len(AGENTS)]
            agent_task_count[agent] += 1
            # Atualiza agent_suggestions na wave
            for wave in waves_data:
                if tn in wave.get("tasks", []):
                    wave["agent_suggestions"][tn] = agent

    lines: list[str] = []
    lines.append(f"# PLANO — {today} — {project_slug}")
    lines.append("")
    lines.append(f"**Aprovado por:** Rafael")
    lines.append(f"**Foco:** {focus}")
    lines.append(f"**Total de tasks:** {total_tasks}")
    lines.append(f"**Waves:** {num_waves}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Distribuição por Agente")
    lines.append("")
    lines.append("| Agente | Tasks | Foco | Motor |")
    lines.append("|--------|:-----:|------|:-----:|")

    agent_focuses = {
        "Navani": "Backend — models, serializers, admin",
        "Jasnah": "Testes, docs, análise",
        "Shallan": "Frontend — templates, HTML, CSS",
        "Kaladin": "Infra, scripts, deploy, índices",
    }

    for agent in AGENTS:
        count = agent_task_count.get(agent, 0)
        motor = AGENT_MOTOR_MAP.get(agent, "—")
        focus_text = agent_focuses.get(agent, "")
        lines.append(f"| {agent} | {count} | {focus_text} | {motor} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Waves")
    lines.append("")

    for wave in waves_data:
        wnum = wave["wave_number"]
        tasks = wave.get("tasks", [])
        agent_suggestions = wave.get("agent_suggestions", {})

        lines.append(f"### Wave {wnum}")
        lines.append("")
        lines.append("| # | Task | Agente | Descrição | Prio | Motor |")
        lines.append("|:-:|:----:|--------|-----------|:----:|:-----:|")

        for i, tn in enumerate(tasks):
            agent = agent_suggestions.get(tn, "—")
            motor = AGENT_MOTOR_MAP.get(agent, "—")
            desc = f"task_{tn:02d}"
            priority = "🔴"
            lines.append(
                f"| {i + 1:02d} | task_{tn:02d} | {agent} | {desc} | {priority} | {motor} |"
            )

        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Restrições do Dia")
    lines.append("")
    lines.append(f"- Commits em `~/Dev/{project_slug}/`")
    lines.append("- Motor exclusivo — falhou = PARAR + reportar + aguardar")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Ao Final do Dia")
    lines.append("")
    lines.append("- [ ] Todas as tasks concluídas e auditadas")
    lines.append("- [ ] INDICE.md atualizado com ✅👁 + hash")
    lines.append("- [ ] Dashboard funcional gerado e testado")
    lines.append("- [ ] Report a Rafael")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════
# Utilitário: pipeline completo
# ═══════════════════════════════════════════════════════════════════════

def run_planner_pipeline(
    project_slug: str,
    db_path: Optional[str] = None,
    tasks_base_dir: Optional[str] = None,
    max_waves: int = 6,
) -> dict[str, Any]:
    """Executa o pipeline completo do planner.

    1. get_pending_tasks
    2. analyze_dependencies
    3. suggest_agent para cada task
    4. group_waves
    5. generate_plan_markdown

    Args:
        project_slug: Slug do projeto
        db_path: Caminho do SQLite
        tasks_base_dir: Diretório base dos task files
        max_waves: Máximo de waves

    Returns:
        Dict com: tasks, dependency_map, waves_data, markdown
    """
    global OESTE_TASKS_DIR
    if tasks_base_dir:
        OESTE_TASKS_DIR = tasks_base_dir

    # 1. Pending tasks
    tasks = get_pending_tasks(project_slug, db_path=db_path)
    if not tasks:
        return {
            "tasks": [],
            "dependency_map": {},
            "waves_data": [],
            "markdown": "",
            "error": "Nenhuma task pendente encontrada",
        }

    # 2. Analyze dependencies
    dep_map = analyze_dependencies(tasks)

    # 3. Suggest agent for each task
    enriched_tasks = []
    for task in tasks:
        tn = task["task_number"]
        paths = dep_map.get(tn, [])
        suggested = suggest_agent(
            task_title=task.get("title", ""),
            task_modulo=task.get("modulo") or "",
            detected_paths=paths,
        )
        # Se a task já tem agent no banco, mantém o agente do banco
        actual_agent = task.get("agent") or suggested
        enriched_tasks.append({
            **task,
            "suggested_agent": actual_agent,
            "detected_paths": paths,
        })

    # 4. Group waves
    waves_data = group_waves(dep_map, max_waves=max_waves)

    # Preenche agent_suggestions nas waves
    task_agent_map = {t["task_number"]: t.get("suggested_agent", "") for t in enriched_tasks}
    for wave in waves_data:
        for tn in wave.get("tasks", []):
            if tn in task_agent_map:
                wave["agent_suggestions"][tn] = task_agent_map[tn]

    # 5. Generate markdown
    done_count = _count_done(project_slug, db_path)
    markdown = generate_plan_markdown(
        project_slug=project_slug,
        waves_data=waves_data,
        pending_count=len(tasks),
        done_count=done_count,
    )

    return {
        "tasks": enriched_tasks,
        "dependency_map": dep_map,
        "waves_data": waves_data,
        "markdown": markdown,
    }


def _count_done(project_slug: str, db_path: Optional[str] = None) -> int:
    """Conta tasks concluídas de um projeto."""
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM tasks t
        JOIN projects p ON t.project_id = p.id
        WHERE p.slug = ? AND t.status = 'done'
        """,
        (project_slug,),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0
