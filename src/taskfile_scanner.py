"""Parser for individual task_XX.md files.

Extracts structured metadata and derives execution/audit status directly
from the task file contents. This is the primary status source now that
INDICE.md files have been eliminated (the DB is the single source of truth).
"""

import logging
import re
from pathlib import Path
from typing import Optional

from .db import Database

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class TaskFileScanner:
    """Extract structured metadata from task markdown files.

    Derives ``status_execucao`` and ``status_auditoria``:
    - ``status_execucao = ✅`` if the task's Conclusão section has at least
      *agente* + *data* + *hash* filled (meaning the agent completed it).
    - ``status_execucao = ⬜`` otherwise.
    - ``status_auditoria`` is NOT set here — it is managed by the Kanban hook
      or the audit-lifecycle pipeline via the ``auditorias`` table.
    """

    FIELD_RE = re.compile(r"\*\*(.+?):\*\*\s*(.*)")

    def __init__(self, db: Database):
        """Initialize the scanner with a database handle."""
        self.db = db

    def scan(self, task_path: str, project_slug: str = "oeste-gestao") -> dict:
        """Parse a task markdown file and upsert the extracted task fields."""
        path = Path(task_path).expanduser()
        if not path.exists():
            logger.warning("Task file not found: %s", path)
            return {}

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            data = self._parse_lines(lines)
        except Exception as exc:
            logger.error("Failed to parse task file %s: %s", path, exc)
            return {}

        try:
            task_number = self._task_number_from_path(path)
            if task_number is not None:
                # ── Derive execution status from conclusão section ──
                is_done = data.get("conclusao_complete", False)
                status_exec = "✅" if is_done else "⬜"

                update = {
                    "task_file": path.name,
                    "title": data.get("title"),
                    "agent": data.get("agent"),
                    "motor": data.get("motor"),
                    "motor_real": data.get("conclusao_motor"),
                    "data_conclusao": data.get("conclusao_date"),
                    "commit_hash": data.get("conclusao_hash"),
                    "status_execucao": status_exec,
                    "status": "done" if is_done else "todo",
                }
                self.db.upsert_task(project_slug, task_number, **update)
        except Exception as exc:
            logger.error("Failed to persist task file %s: %s", path, exc)

        return data

    def _parse_lines(self, lines: list[str]) -> dict:
        """Parse task file lines into structured fields."""
        data = {
            "title": None,
            "agent": None,
            "motor": None,
            "scope_items": [],
            "checklist": {},
            "conclusao_agent": None,
            "conclusao_date": None,
            "conclusao_motor": None,
            "conclusao_obs": None,
            "conclusao_hash": None,
            "conclusao_complete": False,
        }
        section: Optional[str] = None

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# "):
                data["title"] = self._parse_title(stripped)
                continue
            if stripped.startswith("## "):
                section = stripped[3:].strip().lower()
                continue

            field = self.FIELD_RE.match(stripped)
            if field:
                key, value = field.groups()
                self._apply_field(data, key.lower(), value.strip(), section)
                continue

            if section == "escopo":
                item = self._parse_scope_item(stripped)
                if item:
                    data["scope_items"].append(item)
            elif section == "checklist":
                checklist_item = self._parse_checklist_item(stripped)
                if checklist_item:
                    text, done = checklist_item
                    data["checklist"][text] = done

        # ── Derive conclusao_complete ──
        # A task é considerada concluída se a seção Conclusão tem
        # pelo menos agente + data preenchidos (hash era opcional em
        # tasks antigas que colocavam o hash apenas nas observações),
        # OU se todos os checkboxes do checklist estão marcados.
        conclusao_ok = (
            data.get("conclusao_agent")
            and data.get("conclusao_date")
        )
        checklist_done = (
            bool(data.get("checklist"))
            and all(data["checklist"].values())
        )
        data["conclusao_complete"] = conclusao_ok or checklist_done

        return data

    @staticmethod
    def _parse_title(line: str) -> str:
        """Extract title text from the H1 task heading."""
        title = re.sub(r"^#\s*Task\s+\d+\s*[—-]\s*", "", line).strip()
        return title or line.lstrip("#").strip()

    @staticmethod
    def _parse_scope_item(line: str) -> Optional[str]:
        """Extract a bullet or numbered scope item."""
        match = re.match(r"^(?:[-*]\s+|\d+\.\s+)(.+)$", line)
        return match.group(1).strip() if match else None

    @staticmethod
    def _parse_checklist_item(line: str) -> Optional[tuple]:
        """Extract a checklist item and completion boolean."""
        match = re.match(r"^(?:[-*]\s*)?\[(x|X| )\]\s*(.+)$", line)
        if not match:
            return None
        marker, text = match.groups()
        return text.strip(), marker.lower() == "x"

    @staticmethod
    def _apply_field(data: dict, key: str, value: str, section: Optional[str]) -> None:
        """Apply a bold markdown field to top-level or conclusion fields.

        Matches keys flexibly (case-insensitive, partial match) so keys
        like ``Concluída em``, ``Motor utilizado``, ``Observações`` all
        map to the correct internal fields.
        """
        key_norm = key.strip().lower()
        if section in ("conclusao", "conclusão"):
            if key_norm in ("agente",):
                data["conclusao_agent"] = value
            elif key_norm.startswith("conclu"):   # Concluída em, Conclusão
                data["conclusao_date"] = value
            elif key_norm.startswith("motor"):     # Motor, Motor utilizado
                data["conclusao_motor"] = value
            elif key_norm.startswith("obs"):       # Obs, Observações
                data["conclusao_obs"] = value
            elif key_norm in ("hash",):            # Hash
                data["conclusao_hash"] = value
        elif key in ("agente", "motor"):
            data["agent" if key == "agente" else "motor"] = value

    @staticmethod
    def _task_number_from_path(path: Path) -> Optional[int]:
        """Extract task number from a task_XX.md filename."""
        match = re.search(r"task_(\d+)\.md$", path.name)
        return int(match.group(1)) if match else None
