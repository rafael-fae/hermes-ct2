"""Parser for PLANO.md planning files."""

import logging
import re
from pathlib import Path
from typing import Optional

from .db import Database

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class PlanoScanner:
    """Scan wave plans and sprint estimates from markdown tables."""

    EXPECTED_WAVE_HEADER = ["#", "task", "agente", "descrição", "prio", "motor"]

    # Formatos alternativos de cabeçalho (mais colunas são aceitas)
    FLEXIBLE_HEADERS = [
        ["#", "task", "agente", "descrição", "prio", "motor"],               # 6 col
        ["#", "task", "agente", "descrição", "prio", "motor", "hash", "status"],  # 8 col
        ["#", "task", "agente", "descrição", "motor"],                        # 5 col (Wave 1)
        ["#", "task", "agente", "descrição", "prio", "motor", "arquivos"],   # 7 col
        ["#", "task", "agente", "descrição", "prio", "motor", "commit"],     # 7 col (Commit)
    ]

    def __init__(self, db: Database):
        """Initialize the scanner with a database handle."""
        self.db = db

    def scan(self, plano_path: str, project_slug: str = "oeste-gestao") -> list[dict]:
        """Parse a PLANO.md file into wave dictionaries."""
        path = Path(plano_path).expanduser()
        if not path.exists():
            logger.warning("PLANO.md not found: %s", path)
            return []

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.error("Failed to read PLANO.md %s: %s", path, exc)
            return []

        # Extract date from parent directory name
        date = Path(plano_path).parent.name
        date_regex = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        if not date_regex.match(date):
            logger.warning(
                "Parent directory '%s' is not a valid date (YYYY-MM-DD), "
                "proceeding without date scoping", date
            )
            date = None

        # Resolve sprint_id from date
        sprint_id = None
        if date:
            sprint_info = self.db.get_sprint_by_date(date)
            if sprint_info:
                sprint_id = sprint_info["sprint_id"]
                logger.info("Resolved sprint_id=%s for date=%s", sprint_id, date)
            else:
                logger.info("No sprint found for date=%s", date)

        waves: list[dict] = []
        current: Optional[dict] = None
        in_table = False

        for line in lines:
            heading = re.match(r"^\s*###\s+.*?Wave\s+(\d+)\s*[—-]\s*(.*)$", line)
            if heading:
                if current:
                    waves.append(current)
                current = {"wave_number": int(heading.group(1)), "tasks": []}
                in_table = False
                continue

            if current is None or not line.strip().startswith("|"):
                continue

            cells = [self._clean_cell(cell) for cell in line.strip().strip("|").split("|")]
            lowered = [cell.lower() for cell in cells]
            if self._is_separator(lowered):
                continue
            if self._match_header(lowered):
                in_table = True
                continue
            if not in_table:
                logger.info("Skipping unmatched wave table row in %s: %s", path, line)
                continue
            if len(cells) < 5:
                logger.info("Skipping malformed wave table row in %s: %s", path, line)
                continue

            task = {}
            if len(cells) >= 6:
                task = {
                    "position": self._to_int(cells[0]),
                    "task_file": self._strip_code(cells[1]),
                    "agent": cells[2],
                    "description": cells[3],
                    "priority": cells[4],
                    "motor": cells[5],
                }
            else:  # 5 columns (no priority column)
                task = {
                    "position": self._to_int(cells[0]),
                    "task_file": self._strip_code(cells[1]),
                    "agent": cells[2],
                    "description": cells[3],
                    "priority": "",
                    "motor": cells[4],
                }
            current["tasks"].append(task)

        if current:
            waves.append(current)

        for wave in waves:
            try:
                saved_wave = self.db.upsert_wave(
                    project_slug, wave["wave_number"], date=date
                )
                for task in wave["tasks"]:
                    task_number = self._task_number_from_file(task["task_file"]) or task["position"]
                    self.db.upsert_task(
                        project_slug,
                        task_number,
                        wave_id=saved_wave.get("id"),
                        task_file=task["task_file"],
                        title=task["description"],
                        agent=task["agent"],
                        description=task["description"],
                        priority=task["priority"],
                        motor=task["motor"],
                        day=date,
                        sprint_id=sprint_id,
                    )
            except Exception as exc:
                logger.error("Failed to persist wave from %s: %s", path, exc)

        return waves

    def sprint2_scan(self, sprint2_path: str, project_slug: str = "oeste-gestao") -> dict:
        """Parse SPRINT2-PLANO.md module estimate tables."""
        del project_slug
        path = Path(sprint2_path).expanduser()
        if not path.exists():
            logger.warning("SPRINT2-PLANO.md not found: %s", path)
            return {"modules": []}

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.error("Failed to read SPRINT2-PLANO.md %s: %s", path, exc)
            return {"modules": []}

        headers: list[str] = []
        modules: list[dict] = []
        in_table = False

        for line in lines:
            if not line.strip().startswith("|"):
                continue
            cells = [self._clean_cell(cell) for cell in line.strip().strip("|").split("|")]
            lowered = [cell.lower() for cell in cells]
            if lowered and lowered[0] == "módulo":
                headers = cells
                in_table = True
                continue
            if self._is_separator(lowered):
                continue
            if not in_table or not headers or len(cells) != len(headers):
                continue

            row = {"module": cells[0]}
            for header, value in zip(headers[1:], cells[1:]):
                key = header.lower().replace("ó", "o")
                row[key] = value
            modules.append(row)

        return {"modules": modules}

    @staticmethod
    def _clean_cell(value: str) -> str:
        """Normalize a markdown table cell."""
        return value.strip().strip("`").strip()

    @staticmethod
    def _strip_code(value: str) -> str:
        """Remove markdown code delimiters."""
        return value.strip().strip("`").strip()

    @staticmethod
    def _to_int(value: str) -> Optional[int]:
        """Convert a cell value to int when possible."""
        digits = re.sub(r"\D", "", value)
        return int(digits) if digits else None

    @staticmethod
    def _task_number_from_file(value: str) -> Optional[int]:
        """Extract a task number from task_XX.md."""
        match = re.search(r"task_(\d+)\.md$", value)
        return int(match.group(1)) if match else None

    @staticmethod
    def _match_header(lowered: list[str]) -> bool:
        """Check if lowered matches any known header by first N columns (prefix match)."""
        for h in PlanoScanner.FLEXIBLE_HEADERS:
            if len(lowered) >= len(h) and lowered[:len(h)] == h:
                return True
        return False

    @staticmethod
    def _is_separator(cells: list[str]) -> bool:
        """Return true for markdown table separator rows."""
        return bool(cells) and all(re.fullmatch(r":?-+:?", cell.strip()) for cell in cells)
