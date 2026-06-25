"""
src/project_discovery.py — Descoberta Automática de Projetos

Varre ~/Dev/*/planejamento-diario/INDICE.md para descobrir projetos
monitoráveis pelo Control Tower V2.
"""

import json
import logging
import os
import re
import subprocess
try:
    import tomllib
except ImportError:
    # Python <3.11 — parser TOML minimalista inline
    import re as _toml_re

    def _parse_toml_simple(text):
        """Parser TOML minimalista para pyproject.toml (apenas tabelas e chaves string)."""
        result = {}
        current_section = result
        current_path = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            table_match = _toml_re.match(r'^\[([^\]]+)\]$', line)
            if table_match:
                current_path = table_match.group(1).split(".")
                current_section = result
                for p in current_path:
                    p = p.strip()
                    if p not in current_section:
                        current_section[p] = {}
                    current_section = current_section[p]
                continue
            kv_match = _toml_re.match(r'^"([^"]+)"\s*=\s*"([^"]*)"$', line)
            if kv_match:
                current_section[kv_match.group(1)] = kv_match.group(2)
                continue
            kv_match = _toml_re.match(r"^'([^']+)'\s*=\s*'([^']*)'$", line)
            if kv_match:
                current_section[kv_match.group(1)] = kv_match.group(2)
                continue
            kv_match = _toml_re.match(r'^(\w+)\s*=\s*"([^"]*)"$', line)
            if kv_match:
                current_section[kv_match.group(1)] = kv_match.group(2)
                continue
            kv_match = _toml_re.match(r'^(\w+)\s*=\s*true$', line)
            if kv_match:
                current_section[kv_match.group(1)] = True
                continue
            kv_match = _toml_re.match(r'^(\w+)\s*=\s*false$', line)
            if kv_match:
                current_section[kv_match.group(1)] = False
                continue
            # Array simples [a, b, c]
            arr_match = _toml_re.match(r'^(\w+)\s*=\s*\[(.+)\]$', line)
            if arr_match:
                arr_content = arr_match.group(2)
                items = [x.strip().strip('"\'') for x in arr_content.split(",") if x.strip()]
                current_section[arr_match.group(1)] = items
                continue
        return result

    class _TomlLib:
        class TOMLDecodeError(ValueError):
            pass

        @staticmethod
        def load(f):
            content = f.read()
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return _parse_toml_simple(content)

    tomllib = _TomlLib()

from src.db import get_connection, get_project_by_slug, insert_project
from src.github_integration import detect_repo

logger = logging.getLogger(__name__)


def discover_projects(root_dir=None):
    """
    Varre diretórios em busca de projetos com planejamento-diario/INDICE.md.

    Args:
        root_dir: Diretório raiz para busca (default: ~/Dev)

    Returns:
        list de dicts com projetos descobertos
        [{slug, name, path, repo_url, stack}, ...]
    """
    root = root_dir or os.path.expanduser("~/Dev")
    projects = []

    if not os.path.isdir(root):
        return projects

    for entry in os.listdir(root):
        project_path = os.path.join(root, entry)

        if not os.path.isdir(project_path) or entry.startswith("."):
            continue

        indice_path = os.path.join(project_path, "planejamento-diario", "INDICE.md")
        if not os.path.isfile(indice_path):
            continue

        name = entry.replace("-", " ").replace("_", " ").title()

        repo_info = detect_repo(project_path)
        repo_url = repo_info["repo_url"] if repo_info else None

        stack = _detect_stack(project_path)

        projects.append({
            "slug": entry,
            "name": name,
            "path": project_path,
            "repo_url": repo_url,
            "stack": stack,
        })

    return projects


def _detect_stack(project_path):
    """Tenta detectar a stack do projeto por manifests e README.md."""
    pyproject_stack = _detect_stack_from_pyproject(project_path)
    if pyproject_stack:
        return pyproject_stack

    package_stack = _detect_stack_from_package_json(project_path)
    if package_stack:
        return package_stack

    composer_stack = _detect_stack_from_composer(project_path)
    if composer_stack:
        return composer_stack

    return _detect_stack_from_readme(project_path)


def _detect_stack_from_pyproject(project_path):
    pyproject_path = os.path.join(project_path, "pyproject.toml")
    if not os.path.isfile(pyproject_path):
        return None

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.debug("Could not read pyproject.toml at %s: %s", pyproject_path, exc)
        return None

    dependencies = []
    build_requires = data.get("build-system", {}).get("requires", [])
    if isinstance(build_requires, list):
        dependencies.extend(str(dep).lower() for dep in build_requires)

    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    if isinstance(poetry_deps, dict):
        dependencies.extend(str(dep).lower() for dep in poetry_deps.keys())

    project_deps = data.get("project", {}).get("dependencies", [])
    if isinstance(project_deps, list):
        dependencies.extend(str(dep).lower() for dep in project_deps)

    dep_text = " ".join(dependencies)
    if "django" in dep_text:
        return "Python/Django"
    if "fastapi" in dep_text:
        return "Python/FastAPI"
    if "flask" in dep_text:
        return "Python/Flask"
    if "poetry" in dep_text or poetry_deps:
        return "Python/Poetry"
    if "setuptools" in dep_text or "pip" in dep_text or project_deps:
        return "Python/pip"
    return "Python/pip"


def _detect_stack_from_package_json(project_path):
    package_path = os.path.join(project_path, "package.json")
    if not os.path.isfile(package_path):
        return None

    try:
        with open(package_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.debug("Could not read package.json at %s: %s", package_path, exc)
        return None

    dependencies = data.get("dependencies", {})
    dev_dependencies = data.get("devDependencies", {})
    if not isinstance(dependencies, dict):
        dependencies = {}
    if not isinstance(dev_dependencies, dict):
        dev_dependencies = {}
    if not dependencies and not dev_dependencies:
        return None

    packages = {pkg.lower() for pkg in dependencies}
    dev_packages = {pkg.lower() for pkg in dev_dependencies}
    all_packages = packages | dev_packages

    language = "TypeScript" if "typescript" in dev_packages or "typescript" in packages else "JavaScript"
    if "next" in all_packages:
        return f"{language}/Next.js"
    if "react" in all_packages:
        return f"{language}/React"
    if "vue" in all_packages:
        return f"{language}/Vue"
    if "@angular/core" in all_packages:
        return f"{language}/Angular"
    if "express" in all_packages:
        return f"{language}/Express"
    return f"{language}/Node.js"


def _detect_stack_from_composer(project_path):
    composer_path = os.path.join(project_path, "composer.json")
    if not os.path.isfile(composer_path):
        return None

    try:
        with open(composer_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.debug("Could not read composer.json at %s: %s", composer_path, exc)
        return None

    requires = data.get("require", {})
    dev_requires = data.get("require-dev", {})
    if not isinstance(requires, dict):
        requires = {}
    if not isinstance(dev_requires, dict):
        dev_requires = {}

    packages = {pkg.lower() for pkg in (requires | dev_requires)}
    if "laravel/framework" in packages:
        return "PHP/Laravel"
    if "symfony/framework-bundle" in packages or any(pkg.startswith("symfony/") for pkg in packages):
        return "PHP/Symfony"
    if packages:
        return "PHP/Composer"
    return None


def _detect_stack_from_readme(project_path):
    """Tenta detectar a stack do projeto pelo README.md."""
    for readme_name in ("README.md", "readme.md"):
        readme_path = os.path.join(project_path, readme_name)
        if not os.path.isfile(readme_path):
            continue
        try:
            with open(readme_path, "r", encoding="utf-8") as f:
                for line in f:
                    line_stripped = line.strip()
                    if line_stripped.lower().startswith("stack"):
                        return line_stripped.split(":", 1)[1].strip()
                    if re.match(r"^#{1,3}\s+stack", line_stripped, re.IGNORECASE):
                        next_line = next(f, "").strip()
                        if next_line and not next_line.startswith("#"):
                            return next_line
                    inferred = _infer_stack_from_text(line_stripped)
                    if inferred:
                        return inferred
        except (IOError, UnicodeDecodeError) as exc:
            logger.debug("Could not read README at %s: %s", readme_path, exc)
    return None


def _infer_stack_from_text(text):
    normalized = text.lower()
    if "django" in normalized:
        return "Python/Django"
    if "fastapi" in normalized:
        return "Python/FastAPI"
    if "flask" in normalized:
        return "Python/Flask"
    if "laravel" in normalized:
        return "PHP/Laravel"
    if "symfony" in normalized:
        return "PHP/Symfony"
    if "next.js" in normalized or "nextjs" in normalized:
        return "JavaScript/Next.js"
    if "react" in normalized:
        return "JavaScript/React"
    if "vue" in normalized:
        return "JavaScript/Vue"
    if "node.js" in normalized or "nodejs" in normalized:
        return "JavaScript/Node.js"
    return None


def extract_description(project_path):
    """Retorna o primeiro heading de README.md após o título."""
    headings = []
    for readme_name in ("README.md", "readme.md"):
        readme_path = os.path.join(project_path, readme_name)
        if not os.path.isfile(readme_path):
            continue
        try:
            with open(readme_path, "r", encoding="utf-8") as f:
                for line in f:
                    match = re.match(r"^#{1,2}\s+(.+?)\s*$", line)
                    if match:
                        headings.append(match.group(1).strip())
        except (IOError, UnicodeDecodeError) as exc:
            logger.debug("Could not extract README description at %s: %s", readme_path, exc)
            return None
        break

    if len(headings) >= 2:
        return headings[1]
    return None


def detect_deploy_url(project_path):
    """Detecta URL de deploy por .env ou remotes Git conhecidos."""
    env_path = os.path.join(project_path, ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    match = re.match(r"^\s*(DEPLOY_URL|APP_URL|SITE_URL)\s*=\s*(.+?)\s*$", line)
                    if match:
                        value = match.group(2).strip().strip("'\"")
                        if value:
                            return value
        except (IOError, UnicodeDecodeError) as exc:
            logger.debug("Could not read .env at %s: %s", env_path, exc)

    try:
        result = subprocess.run(
            ["git", "-C", project_path, "remote", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("Could not inspect git remotes for %s: %s", project_path, exc)
        return None

    for line in result.stdout.splitlines():
        if any(provider in line.lower() for provider in ("heroku", "vercel", "netlify", "render", "fly")):
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
    return None


def add_project(projects_root, conn=None):
    """
    Descobre e insere projetos no banco SQLite.

    Args:
        projects_root: Diretório raiz para escanear
        conn: Conexão SQLite (cria uma nova se None)

    Returns:
        list de slugs inseridos/atualizados
    """
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        discovered = discover_projects(projects_root)
        inserted = []
        for proj in discovered:
            insert_project(
                conn,
                slug=proj["slug"],
                name=proj["name"],
                path=proj["path"],
                repo_url=proj["repo_url"],
                stack=proj["stack"],
            )
            inserted.append(proj["slug"])
        return inserted
    finally:
        if close_conn:
            conn.close()


def list_projects(conn=None):
    """Lista todos os projetos cadastrados no banco."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if close_conn:
            conn.close()


def remove_project(slug, conn=None):
    """Remove um projeto pelo slug. Retorna True se removeu."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        cur = conn.execute("DELETE FROM projects WHERE slug = ?", (slug,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        if close_conn:
            conn.close()


def update_project(slug, conn=None, **kwargs):
    """Atualiza campos permitidos de um projeto pelo slug."""
    allowed = {"stack", "repo_url", "deploy_url", "name", "path"}

    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(projects)").fetchall()
        }
        updates = {
            key: value
            for key, value in kwargs.items()
            if key in allowed and key in columns and value is not None
        }
        if not updates:
            return False

        set_clause = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [slug]
        cur = conn.execute(
            f"UPDATE projects SET {set_clause} WHERE slug = ?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        if close_conn:
            conn.close()


def get_project(slug, conn=None):
    """Retorna um projeto pelo slug."""
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        return get_project_by_slug(conn, slug)
    finally:
        if close_conn:
            conn.close()
