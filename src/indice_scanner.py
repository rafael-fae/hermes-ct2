"""Parser for planejamento-diario INDICE.md files."""

import logging
import re
from pathlib import Path
from typing import Optional

from .db import Database

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class IndiceScanner:
    """Scan daily index markdown tables into task dictionaries."""

    EXPECTED_HEADER = ["#", "task", "agente", "descrição", "sp", "✅", "👁", "commit"]

    def __init__(self, db: Database):
        """Initialize the scanner with a database handle."""
        self.db = db

    def scan(self, indice_path: str, project_slug: str = "oeste-gestao") -> list[dict]:
        """Parse an INDICE.md file and upsert discovered tasks."""
        path = Path(indice_path).expanduser()
        if not path.exists():
            logger.warning("INDICE.md not found: %s", path)
            return []

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.error("Failed to read INDICE.md %s: %s", path, exc)
            return []

        tasks: list[dict] = []
        current_date: Optional[str] = None
        section_lines: list[str] = []

        for line in lines:
            if "Progresso Sprint" in line:
                break
            if current_date and line.strip() == "---":
                # End current section, save tasks, continue to next section
                tasks.extend(self._extract_tasks(current_date, "", section_lines))
                current_date = None
                section_lines = []
                continue

            heading = re.match(r"^\s*##\s+(.+?)\s*$", line)
            h1_heading = re.match(r"^#\s+Índice de Tasks\s*[—–-]\s*(\d{2})/(\d{2})/(\d{4})", line)
            if heading:
                if current_date:
                    tasks.extend(self._extract_tasks(current_date, "", section_lines))
                current_date = self._parse_date_from_heading(line)
                section_lines = []
                continue
            if h1_heading and not heading:
                # Daily INDICE format: # Índice de Tasks — DD/MM/YYYY
                if current_date:
                    tasks.extend(self._extract_tasks(current_date, "", section_lines))
                day, month, year = h1_heading.groups()
                current_date = f"{year}-{month}-{day}"
                section_lines = []
                continue

            if current_date:
                section_lines.append(line)

        if current_date:
            tasks.extend(self._extract_tasks(current_date, "", section_lines))

        # Resolve sprint_id and day from each task's date
        for task in tasks:
            try:
                task_data = dict(task)
                task_number = task_data.pop("task_number")
                task_date = task_data.pop("date", None)
                # Map INDICE status_execucao (✅/⬜) → tasks.status (done/todo)
                status_exec = task_data.get("status_execucao", "⬜")
                status_map = {"✅": "done", "⬜": "todo",
                              "🟢": "in_progress", "🔴": "blocked"}
                task_data["status"] = status_map.get(status_exec, "todo")

                # Resolve sprint_id from task's own date
                task_sprint_id = None
                if task_date:
                    sprint_info = self.db.get_sprint_by_date(task_date)
                    if sprint_info:
                        task_sprint_id = sprint_info["sprint_id"]

                self.db.upsert_task(
                    project_slug,
                    task_number,
                    sprint_id=task_sprint_id,
                    day=task_date,
                    **task_data,
                )
            except Exception as exc:
                logger.error("Failed to upsert task from %s: %s", path, exc)

        return tasks

    def _parse_date_from_heading(self, heading: str) -> Optional[str]:
        """Extract an ISO date from a ``## DD/MM/YYYY`` heading."""
        match = re.search(r"##\s*(\d{2})/(\d{2})/(\d{4})", heading)
        if not match:
            return None
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"

    def _parse_table_line(self, line: str, date: str) -> Optional[dict]:
        """Parse one task table row into a normalized task dictionary."""
        if not line.strip().startswith("|"):
            return None
        columns = [self._clean_cell(cell) for cell in line.strip().strip("|").split("|")]
        if len(columns) != 8:
            return None
        if columns[0].strip("- ") in ("", "#") or columns[0].lower() == "#":
            return None

        task_number_text = re.sub(r"\D", "", columns[0])
        if not task_number_text:
            return None

        return {
            "task_number": int(task_number_text),
            "task_file": self._strip_code(columns[1]),
            "agent": columns[2],
            "description": columns[3],
            "modulo": columns[4],
            "status_execucao": columns[5] or "⬜",
            "status_auditoria": columns[6] or "⬜",
            "commit_hash": self._strip_code(columns[7]),
            "date": date,
        }

    def _extract_tasks(self, date: str, header: str, lines: list[str]) -> list[dict]:
        """Process all task table rows inside a date section."""
        del header
        tasks: list[dict] = []
        in_expected_table = False

        for line in lines:
            if "Progresso Sprint" in line or line.strip() == "---":
                break
            if not line.strip().startswith("|"):
                continue

            cells = [self._clean_cell(cell).lower() for cell in line.strip().strip("|").split("|")]
            if self._is_separator(cells):
                continue
            if cells == self.EXPECTED_HEADER:
                in_expected_table = True
                continue
            if not in_expected_table:
                continue

            task = self._parse_table_line(line, date)
            if task:
                tasks.append(task)

        return tasks

    @staticmethod
    def _clean_cell(value: str) -> str:
        """Normalize markdown table cell text."""
        value = value.strip()
        value = re.sub(r"^\*\*(.*?)\*\*$", r"\1", value)
        return value.strip()

    @staticmethod
    def _strip_code(value: str) -> str:
        """Remove markdown backticks around a cell value."""
        return value.strip().strip("`").strip()

    @staticmethod
    def _is_separator(cells: list[str]) -> bool:
        """Return true for markdown separator rows."""
        return bool(cells) and all(re.fullmatch(r":?-+:?", cell.strip()) for cell in cells)
