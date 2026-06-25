"""Top-level Scanner for Control Tower V2.

Orchestrates all specialized scanners (indice, taskfile, plano)
and provides a unified scan() entry point.
Python 3.9+ stdlib only.
"""

import logging
import re
from pathlib import Path
from typing import Optional

from .db import Database, seed_agents
from .diario_scanner import DiarioScanner
from .plano_scanner import PlanoScanner
from .sprint_scanner import SprintScanner
from .taskfile_scanner import TaskFileScanner

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Default operational file paths (Dalinar's operacional dir)
OP_DIARIO_PATH = "~/.hermes/profiles/dalinar/operacional/DIARIO.md"


class Scanner:
    """Coordinate database setup and all project file scanners.

    Usage:
        scanner = Scanner()
        result = scanner.scan(project_root="~/Dev/oeste-gestao")
    """

    def __init__(self, db_path: Optional[str] = None):
        """Initialize scanner with a database path.

        Defaults to ``state/ct2.db`` relative to this project root.
        """
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent.parent / "state" / "ct2.db")
        self.db_path = Path(db_path).expanduser()
        self.db: Optional[Database] = None

    def scan(self, project_root: str = "", scan_operational: bool = False) -> dict:
        """Scan all known planning files under a project root.

        Args:
            project_root: Root path of the project (e.g. ~/Dev/oeste-gestao).
            scan_operational: If True, also scan ESTADO-DA-EQUIPE.md + DIARIO.md.

        Returns:
            Dict with scan results.
        """
        root = Path(project_root).expanduser().resolve()
        project_slug = root.name
        errors: list[str] = []
        self.db = Database(str(self.db_path))
        self.db.create_tables()

        project = self.db.upsert_project(project_slug, project_slug, str(root))
        result: dict = {
            "projects": [project],
            "errors": errors,
        }
        task_scanner = TaskFileScanner(self.db)
        plano_scanner_obj = PlanoScanner(self.db)
        sprint_scanner = SprintScanner(self.db)

        # Scan daily dirs: PLANO.md + task_XX.md (INDICE.md foi eliminado —
        # o banco CT V2 é a única fonte da verdade para status agregado)
        for date_dir, sprint_dir in self._discover_date_dirs(str(root)):
            if sprint_dir:
                daily_dir = root / "planejamento-diario" / sprint_dir / date_dir
            else:
                daily_dir = root / "planejamento-diario" / date_dir
            daily_plano = daily_dir / "PLANO.md"

            # SprintScanner: extrair sprint info ANTES de processar daily_dir
            sprint_info = sprint_scanner.scan(str(daily_plano), project_slug)
            sprint_id = sprint_info.get("sprint_id") if sprint_info else None

            self._scan_path(daily_plano, errors, plano_scanner_obj.scan, str(daily_plano), project_slug)

            for task_path in self._discover_task_files(str(daily_dir)):
                self._scan_path(Path(task_path), errors, task_scanner.scan, task_path, project_slug)

        # 3. SPRINT2-PLANO.md
        sprint2_path = root / "docs" / "planejamento" / "SPRINT2-PLANO.md"
        self._scan_path(sprint2_path, errors, plano_scanner_obj.sprint2_scan, str(sprint2_path), project_slug)

        # 4. Operational scans (DIARIO.md only — agent_status comes from seed fixo)
        if scan_operational:
            try:
                seed_count = seed_agents(conn=self.db.get_connection())
                result["agent_seed"] = {"status": "ok", "count": seed_count}
            except Exception as e:
                logger.error("seed_agents falhou: %s", e)
                result["agent_seed"] = {"status": "error", "count": 0, "error": str(e)}
                errors.append(f"seed_agents: {e}")

            try:
                diario_count = self.scan_diario()
                result["diario"] = {"status": "ok", "count": diario_count}
            except Exception as e:
                logger.error("scan_diario falhou: %s", e)
                result["diario"] = {"status": "error", "count": 0, "error": str(e)}
                errors.append(f"scan_diario: {e}")

        tasks_count = len(self.db.get_tasks_by_project(project_slug))
        waves_count = len(self.db.get_waves_by_project(project_slug))
        result["tasks_count"] = tasks_count
        result["waves_count"] = waves_count
        return result

    # --- Operational scanners ---

    def seed_agents(self) -> int:
        """Popula agent_status com os 6 agentes fixos (seed).

        Substitui a antiga dependência do ESTADO-DA-EQUIPE.md.
        Os agentes são: Dalinar, Jasnah, Kaladin, Navani, Pattern, Shallan.

        Returns:
            Number of rows upserted.
        """
        from .db import seed_agents as _seed
        return _seed(conn=self.db.get_connection())

    def scan_diario(self, path: Optional[str] = None) -> int:
        """Scan DIARIO.md and populate auditorias.

        Args:
            path: Path to DIARIO.md. Defaults to Dalinar's operacional dir.

        Returns:
            Number of auditorias inserted.
        """
        if path is None:
            path = OP_DIARIO_PATH
        scanner = DiarioScanner()
        conn = self.db.get_connection()
        return scanner.scan(path, conn)

    def _discover_date_dirs(self, project_root: str) -> list[tuple[str, str]]:
        """Return list of (date_dir, sprint_dir) tuples below ``planejamento-diario``.

        Scans both:
        1. Pastas YYYY-MM-DD na raiz (formato antigo, compatibilidade)
        2. Pastas sprint-N/YYYY-MM-DD (formato novo aninhado)
        """
        base = Path(project_root).expanduser() / "planejamento-diario"
        if not base.exists():
            logger.warning("planning directory not found: %s", base)
            return []
        dates: list[tuple[str, str]] = []
        # 1. Pastas YYYY-MM-DD na raiz (formato antigo, compatibilidade)
        for path in base.iterdir():
            if path.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name):
                dates.append((path.name, ""))
        # 2. Pastas sprint-N/YYYY-MM-DD (formato novo)
        for sprint_dir in base.iterdir():
            if sprint_dir.is_dir() and re.fullmatch(r"sprint-\d+", sprint_dir.name):
                for date_dir in sprint_dir.iterdir():
                    if date_dir.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_dir.name):
                        dates.append((date_dir.name, sprint_dir.name))
        dates.sort(key=lambda x: x[0])  # sort by date
        logger.debug(
            "Discovered %d date dirs: %s", len(dates), dates
        )
        return dates

    def _discover_task_files(self, date_dir: str) -> list[str]:
        """Return task_XX.md files inside a date directory."""
        path = Path(date_dir).expanduser()
        if not path.exists():
            logger.warning("date directory not found: %s", path)
            return []
        return [
            str(task_path)
            for task_path in sorted(path.glob("task_*.md"))
            if re.fullmatch(r"task_\d+\.md", task_path.name)
        ]

    @staticmethod
    def _scan_path(path: Path, errors: list[str], callback, *args) -> None:
        """Run one parser callback with graceful missing-file and error handling."""
        if not path.exists():
            logger.warning("skipping missing path: %s", path)
            return
        try:
            callback(*args)
        except Exception as exc:
            logger.error("scanner failed for %s: %s", path, exc)
            errors.append(str(path))
