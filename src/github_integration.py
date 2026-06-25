"""
src/github_integration.py — GitHub Detection & Issues Stub

Fornece funções para detectar repositório git, obter status CI/CD
e construir links clicáveis para GitHub Issues.
"""

import os
import subprocess
import re


def detect_repo(project_path):
    """
    Detecta a URL do repositório git de um diretório de projeto.

    Args:
        project_path: Caminho absoluto para o diretório do projeto

    Returns:
        dict com {"repo_url": str, "repo_name": str, "provider": str}
        ou None se não for um repositório git
    """
    git_dir = os.path.join(project_path, ".git")
    if not os.path.isdir(git_dir):
        return None

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        repo_url = result.stdout.strip()

        repo_name = None
        provider = "unknown"

        if "github.com" in repo_url:
            provider = "github"
            match = re.search(r'(?:github\.com[:/])([^/]+/[^/.]+)(?:\.git)?$', repo_url)
            if match:
                repo_name = match.group(1)
        elif "gitlab.com" in repo_url:
            provider = "gitlab"
            match = re.search(r'(?:gitlab\.com[:/])([^/]+/[^/.]+)(?:\.git)?$', repo_url)
            if match:
                repo_name = match.group(1)
        elif "bitbucket.org" in repo_url:
            provider = "bitbucket"
            match = re.search(r'(?:bitbucket\.org[:/])([^/]+/[^/.]+)(?:\.git)?$', repo_url)
            if match:
                repo_name = match.group(1)

        if not repo_name:
            parts = repo_url.rstrip("/").split("/")
            if parts:
                repo_name = parts[-1].replace(".git", "")

        return {
            "repo_url": repo_url,
            "repo_name": repo_name,
            "provider": provider,
        }

    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_repo_info(project_path):
    """
    Detecta informações do repositório git do projeto (GitHub).

    Args:
        project_path: Caminho para o diretório do projeto

    Returns:
        dict com {"repo_url": str, "owner": str, "repo_name": str, "default_branch": str}
        ou None se não for repositório git ou não possuir remote GitHub.
    """
    project_path = os.path.expanduser(project_path)
    git_dir = os.path.join(project_path, ".git")
    if not os.path.isdir(git_dir):
        return None

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            print(f"⚠️  [github_integration] Sem remote origin para o projeto em {project_path}")
            return None

        repo_url = result.stdout.strip()
        if "github.com" not in repo_url:
            print(f"⚠️  [github_integration] Remote origin em {project_path} não é do GitHub: {repo_url}")
            return None

        # Parse owner e repo_name da URL do GitHub (suporta HTTPS e SSH)
        # e.g., https://github.com/rafael-fae/oeste-gestao.git ou git@github.com:rafael-fae/oeste-gestao.git
        match = re.search(r'github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$', repo_url)
        if not match:
            print(f"⚠️  [github_integration] Não foi possível extrair owner/repo da URL: {repo_url}")
            return None

        owner = match.group(1)
        repo_name = match.group(2)

        # Detectar default branch
        default_branch = "main"
        try:
            symbolic_res = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if symbolic_res.returncode == 0:
                ref = symbolic_res.stdout.strip()
                default_branch = ref.split("/")[-1]
            else:
                rev_parse_res = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "origin/HEAD"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if rev_parse_res.returncode == 0:
                    ref = rev_parse_res.stdout.strip()
                    if "/" in ref:
                        default_branch = ref.split("/", 1)[1]
                    else:
                        default_branch = ref
        except Exception:
            pass

        return {
            "repo_url": repo_url,
            "owner": owner,
            "repo_name": repo_name,
            "default_branch": default_branch,
        }

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        print(f"⚠️  [github_integration] Erro ao detectar repositório: {e}")
        return None


def get_issues(owner, repo, state='open', limit=10):
    """
    Obtém a lista de issues do GitHub via gh CLI.

    Args:
        owner: Dono do repositório
        repo: Nome do repositório
        state: Estado das issues ('open', 'closed', 'all')
        limit: Quantidade limite de issues

    Returns:
        Lista de dicts com as issues, ou [] se gh CLI não disponível ou em caso de erro.
    """
    try:
        # Verificar se gh CLI está instalada
        subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            timeout=3,
        )
    except (FileNotFoundError, Exception):
        print("⚠️  [github_integration] gh CLI não instalada. Pulando carregamento de issues.")
        return []

    repo_fullname = f"{owner}/{repo}"
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", repo_fullname,
                "--state", state,
                "--limit", str(limit),
                "--json", "number,title,state,labels"
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(f"⚠️  [github_integration] Erro gh issue list para {repo_fullname}: {result.stderr.strip()}")
            return []

        import json
        if result.stdout.strip():
            return json.loads(result.stdout)
        return []

    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"⚠️  [github_integration] Erro ao buscar issues para {repo_fullname}: {e}")
        return []


def get_ci_status(owner, repo, branch='main'):
    """
    Obtém status do CI/CD (GitHub Actions) via gh CLI.

    Args:
        owner: Dono do repositório
        repo: Nome do repositório
        branch: Branch a verificar (default: 'main')

    Returns:
        dict com {'status': passing/failing/pending, 'title': str, 'date': str} ou None
    """
    try:
        # Verificar se gh CLI está instalada
        subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            timeout=3,
        )
    except (FileNotFoundError, Exception):
        print("⚠️  [github_integration] gh CLI não instalada. Pulando status do CI.")
        return None

    repo_fullname = f"{owner}/{repo}"
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                "--repo", repo_fullname,
                "--branch", branch,
                "--limit", "1",
                "--json", "conclusion,displayTitle,createdAt"
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            print(f"⚠️  [github_integration] Erro gh run list para {repo_fullname}: {result.stderr.strip()}")
            return None

        import json
        if result.stdout.strip():
            runs = json.loads(result.stdout)
            if runs:
                run = runs[0]
                conclusion = run.get("conclusion", "")
                
                # Mapeamento: conclusion -> passing/failing/pending
                if conclusion == "success":
                    status = "passing"
                elif conclusion in ("failure", "timed_out", "action_required"):
                    status = "failing"
                else:
                    status = "pending"

                return {
                    "status": status,
                    "title": run.get("displayTitle", ""),
                    "date": run.get("createdAt", ""),
                }
            
            # Se não houver runs
            return {
                "status": "pending",
                "title": "Nenhum workflow executado",
                "date": "",
            }
        return None

    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"⚠️  [github_integration] Erro ao buscar status do CI para {repo_fullname}: {e}")
        return None


def link_issue_url(repo_url, issue_number):
    """
    Constrói URL clicável HTTPS para uma GitHub Issue.

    Args:
        repo_url: URL do repositório
        issue_number: Número da issue (int ou str)

    Returns:
        str com URL completa ou None se não for possível determinar
    """
    if not repo_url or not issue_number:
        return None

    issue_str = str(issue_number).strip().lstrip('#')

    # Parse de URLs do GitHub
    match = re.search(r'github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$', repo_url)
    if match:
        owner = match.group(1)
        repo = match.group(2)
        return f"https://github.com/{owner}/{repo}/issues/{issue_str}"

    # Fallback genérico para outros provedores
    match = re.search(r'(https?://[^/]+/[^/]+/[^/.]+?)(?:\.git)?/?$', repo_url)
    if match:
        base = match.group(1)
        return f"{base}/issues/{issue_str}"

    return None


def _parse_owner_repo(repo_url):
    """
    Auxiliar para parsear o owner e repo de uma URL do GitHub.
    """
    if not repo_url:
        return None, None
    match = re.search(r'github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$', repo_url)
    if match:
        return match.group(1), match.group(2)
    
    parts = [p for p in repo_url.strip().split('/') if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1].replace('.git', '')
    return None, None


def get_recent_commits(repo_url, limit=5):
    """
    Obtém os commits recentes de um repositório.
    """
    owner, repo = _parse_owner_repo(repo_url)
    if not owner or not repo:
        return []

    # Tenta gh CLI
    try:
        result = subprocess.run(
            [
                'gh', 'api', f'repos/{owner}/{repo}/commits?per_page={limit}',
                '--jq', '.[] | {sha: .sha[0:7], message: .commit.message, author: .commit.author.name, date: .commit.author.date, url: .html_url}'
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            lines = result.stdout.strip().splitlines()
            commits = []
            for line in lines:
                if line.strip():
                    try:
                        commits.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return commits
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Fallback para urllib.request
    try:
        import urllib.request
        import urllib.error
        import json
        
        url = f"https://api.github.com/repos/{owner}/{repo}/commits?per_page={limit}"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "ControlTower-v2")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"token {token}")
            
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list):
                return []
            commits = []
            for item in data:
                commit_info = item.get("commit", {})
                author_info = commit_info.get("author", {})
                commits.append({
                    "sha": item.get("sha", "")[:7],
                    "message": commit_info.get("message", ""),
                    "author": author_info.get("name", ""),
                    "date": author_info.get("date", ""),
                    "url": item.get("html_url", "")
                })
            return commits
    except Exception:
        return []


def get_open_prs(repo_url):
    """
    Obtém os pull requests abertos de um repositório.
    """
    owner, repo = _parse_owner_repo(repo_url)
    if not owner or not repo:
        return []

    # Tenta gh CLI
    try:
        result = subprocess.run(
            [
                'gh', 'pr', 'list',
                '--repo', f'{owner}/{repo}',
                '--state', 'open',
                '--limit', '10',
                '--json', 'number,title,headRefName,author,createdAt,url'
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            raw_prs = json.loads(result.stdout)
            if not isinstance(raw_prs, list):
                return []
            prs = []
            for pr in raw_prs:
                author_val = pr.get("author", {})
                if isinstance(author_val, dict):
                    author_name = author_val.get("login", "")
                else:
                    author_name = str(author_val)
                prs.append({
                    "number": pr.get("number"),
                    "title": pr.get("title", ""),
                    "branch": pr.get("headRefName", ""),
                    "author": author_name,
                    "created_at": pr.get("createdAt", ""),
                    "url": pr.get("url", "")
                })
            return prs
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Fallback para urllib.request
    try:
        import urllib.request
        import urllib.error
        import json
        
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&per_page=10"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "ControlTower-v2")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"token {token}")
            
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list):
                return []
            prs = []
            for item in data:
                user_info = item.get("user", {})
                head_info = item.get("head", {})
                prs.append({
                    "number": item.get("number"),
                    "title": item.get("title", ""),
                    "branch": head_info.get("ref", ""),
                    "author": user_info.get("login", ""),
                    "created_at": item.get("created_at", ""),
                    "url": item.get("html_url", "")
                })
            return prs
    except Exception:
        return []


def get_branches(repo_url):
    """
    Obtém os branches de um repositório.
    """
    owner, repo = _parse_owner_repo(repo_url)
    if not owner or not repo:
        return []

    # Tenta gh CLI
    try:
        result = subprocess.run(
            [
                'gh', 'api', f'repos/{owner}/{repo}/branches?per_page=10',
                '--jq', '.[] | {name: .name, commit: .commit.sha[0:7], protected: .protected}'
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            lines = result.stdout.strip().splitlines()
            branches = []
            for line in lines:
                if line.strip():
                    try:
                        item = json.loads(line)
                        branches.append({
                            "name": item.get("name", ""),
                            "last_commit_sha": item.get("commit", ""),
                            "is_protected": item.get("protected", False)
                        })
                    except json.JSONDecodeError:
                        pass
            return branches
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Fallback para urllib.request
    try:
        import urllib.request
        import urllib.error
        import json
        
        url = f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=10"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "ControlTower-v2")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"token {token}")
            
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, list):
                return []
            branches = []
            for item in data:
                commit_info = item.get("commit", {})
                branches.append({
                    "name": item.get("name", ""),
                    "last_commit_sha": commit_info.get("sha", "")[:7],
                    "is_protected": item.get("protected", False)
                })
            return branches
    except Exception:
        return []

