"""Parser for ESTADO-DA-EQUIPE.md — agent status extraction.

Extracts agent status from the operational ESTADO-DA-EQUIPE.md file,
with sections grouped by emoji headings and markdown table rows.
Compatible with the canonical db.py schema (agent_status table).
Python 3.10+ stdlib only.
"""

import logging
import os
import re
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# Mapping of section emoji headings to agent_status.status values
HEADINGS_MAP = {
    "🟢 EM EXECUÇÃO": "executing",
    "🟡 AGUARDANDO": "waiting",
    "🔴 BLOQUEADO": "blocked",
    "✅ CONCLUÍDAS": "done",
}


class EstadoScanner:
    """Scans ESTADO-DA-EQUIPE.md and populates the agent_status table.

    Parses sections delimited by ## headings with emoji markers,
    extracts markdown table rows, and upserts each row into agent_status.
    """

    RE_HEADING = re.compile(r"^##\s+(.+)$")
    RE_SEPARATOR = re.compile(r"^\|[\s\-:|]+\|\s*$")
    HEADER_WORDS = {"task", "agente", "descrição", "descricao", "motor", "hash", "motivo", "prio"}

    def scan(self, filepath: str, conn: sqlite3.Connection) -> int:
        """Parse ESTADO-DA-EQUIPE.md and upsert agent_status rows.

        Args:
            filepath: Path to ESTADO-DA-EQUIPE.md (supports ~ expansion).
            conn:     Active SQLite connection.

        Returns:
            Number of rows upserted.
        """
        path = os.path.expanduser(filepath)
        if not os.path.isfile(path):
            logger.warning("ESTADO-DA-EQUIPE.md não encontrado: %s", path)
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            logger.warning("Erro ao ler %s: %s", path, e)
            return 0

        lines = text.splitlines()
        current_status: Optional[str] = None
        in_data = False
        count = 0

        for raw_line in lines:
            stripped = raw_line.rstrip()
            if not stripped:
                continue

            # Section heading?
            m_h = self.RE_HEADING.match(stripped)
            if m_h:
                new_status = self._match_heading(m_h.group(1))
                if new_status is not None:
                    current_status = new_status
                    in_data = False  # Reset for new section
                continue

            if current_status is None:
                continue
            if not stripped.startswith("|"):
                continue
            if self.RE_SEPARATOR.match(stripped):
                in_data = True  # Separator seen → data rows follow
                continue

            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 4:
                logger.warning("Células insuficientes (%d): %s", len(cells), stripped)
                continue

            # First column check: skip if it's a known header word (header row detection)
            first_lower = cells[0].strip("*").lower().strip()
            if first_lower in self.HEADER_WORDS or first_lower in (":task:", "#", ":"):
                in_data = True  # mark that data follows
                continue

            # Before first separator: first row is a header
            if not in_data:
                in_data = True
                continue

            task_cell = cells[0].strip("*").strip()
            agent_cell = cells[1].strip("*").strip()
            desc_cell = cells[2].strip()
            motor_cell = cells[3].strip("*").strip() if len(cells) > 3 else ""

            if not agent_cell:
                logger.debug("Agente vazio, skip: %s", stripped)
                continue

            # Clean agent name: strip markdown bold (**), emoji, parenthetical notes
            agent_clean = re.sub(r'\*\*', '', agent_cell).strip()
            # Remove emoji/symbols that may follow the name (🔴 🟢 🟡 ✅ etc.)
            agent_clean = re.sub(r'[\U0001F534-\U0001F7E9\U00002705\U00002B55]+', '', agent_clean).strip()
            # Strip (OVH) style suffixes
            agent_clean = agent_clean.split("(")[0].split("/")[0].strip()
            if not agent_clean:
                logger.debug("Agente vazio após limpeza: %s", stripped)
                continue

            try:
                self._upsert_agent(conn, agent_clean, current_status,
                                   task_cell, desc_cell, motor_cell)
                count += 1
            except Exception as e:
                logger.warning("Erro upsert %s: %s", agent_clean, e)
                continue

        logger.info("ESTADO-DA-EQUIPE.md: %d linhas", count)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _match_heading(heading_text: str) -> Optional[str]:
        text = heading_text.strip()
        # Exact match
        if text in HEADINGS_MAP:
            return HEADINGS_MAP[text]
        # Prefix match (handles "🟢 EM EXECUÇÃO — Wave 1")
        for prefix, status in HEADINGS_MAP.items():
            if text.startswith(prefix):
                return status
        # Substring match
        for prefix, status in HEADINGS_MAP.items():
            if prefix in text:
                return status
        return None

    @staticmethod
    def _upsert_agent(conn, agent: str, status: str,
                      task: str, desc: str, motor: str) -> None:
        """Insert or update an agent_status row.

        Maps 'done' status to 'idle' since the canonical schema
        does not include a 'done' status.
        """
        db_status = "idle" if status == "done" else status

        existing = conn.execute(
            "SELECT id FROM agent_status WHERE agent = ?", (agent,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE agent_status
                   SET status = ?,
                       motor = ?,
                       updated_at = datetime('now')
                   WHERE agent = ?""",
                (db_status, motor or None, agent),
            )
        else:
            conn.execute(
                """INSERT INTO agent_status (agent, status, motor)
                   VALUES (?, ?, ?)""",
                (agent, db_status, motor or None),
            )
        conn.commit()
