"""SprintScanner — lê cabeçalho do PLANO.md e popula sprints + sprint_dates.

Extrai sprint_number e day_number do cabeçalho no formato:
    # Plano Diário — 17/06/2026 (D5 — Sprint 2)

A data é obtida do nome do diretório pai (YYYY-MM-DD).
Ignora PLANO.md no formato CT V2 (sem sprint info).
"""

import logging
import re
from pathlib import Path
from typing import Optional

from .db import Database

logger = logging.getLogger(__name__)

# Regex para cabeçalho com sprint info:
# # Plano Diário — 17/06/2026 (D5 — Sprint 2)
HEADER_SPRINT_RE = re.compile(
    r"#\s*Plano\s+Diário\s*[—–-]\s*"
    r"\d{2}/\d{2}/\d{4}\s*"
    r"\(D(\d+)\s*[—–-]\s*Sprint\s*(\d+)\)"
)

# Regex para data no formato dd/mm/aaaa
DATE_BR_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# Fallback map: (project_slug, date) -> (sprint_number, day_number)
# Usado quando o cabeçalho não tem o formato padronizado (D<N> — Sprint <N>)
SPRINT_FALLBACK_MAP: dict[tuple[str, str], tuple[int, int]] = {
    # Sprint 1 — oeste-gestao
    ("oeste-gestao", "2026-05-31"): (1, 0),
    ("oeste-gestao", "2026-06-01"): (1, 1),
    ("oeste-gestao", "2026-06-02"): (1, 2),
    ("oeste-gestao", "2026-06-03"): (1, 3),
    ("oeste-gestao", "2026-06-04"): (1, 4),
    ("oeste-gestao", "2026-06-05"): (1, 5),
    # Sprint 2 — oeste-gestao (dias que o regex não pega)
    ("oeste-gestao", "2026-06-06"): (2, 1),
    ("oeste-gestao", "2026-06-07"): (2, 2),
}


class SprintScanner:
    """Scan PLANO.md headers to populate sprints and sprint_dates."""

    def __init__(self, db: Database):
        self.db = db

    def scan(self, plano_path: str, project_slug: str) -> Optional[dict]:
        """Read a PLANO.md, extract sprint info, and persist to DB.

        Args:
            plano_path: Full path to the PLANO.md file.
            project_slug: Project slug (e.g. "oeste-gestao").

        Returns:
            Dict with extracted data if sprint info found, else None.
            Keys: date, sprint_number, day_number, sprint_title, sprint_id
        """
        path = Path(plano_path).expanduser()
        if not path.exists():
            logger.warning("SprintScanner: PLANO.md not found: %s", path)
            return None

        # 1. Extrair data do diretório pai (YYYY-MM-DD)
        date_str = path.parent.name
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
            logger.debug(
                "SprintScanner: parent dir isn't a date (%s), skipping", date_str
            )
            return None

        # 2. Ler cabeçalho do PLANO.md
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
        except (IndexError, Exception) as exc:
            logger.warning("SprintScanner: failed to read %s: %s", path, exc)
            return None

        # 3. Extrair sprint_number e day_number via regex ou fallback map
        m = HEADER_SPRINT_RE.match(first_line)
        if m:
            day_number = int(m.group(1))
            sprint_number = int(m.group(2))
        else:
            # Tenta fallback map para cabeçalhos não padronizados
            fallback = SPRINT_FALLBACK_MAP.get((project_slug, date_str))
            if fallback:
                sprint_number, day_number = fallback
                logger.info(
                    "SprintScanner: fallback map resolved sprint %d, day %d"
                    " for %s (line: %r)",
                    sprint_number, day_number, date_str, first_line,
                )
            else:
                logger.debug(
                    "SprintScanner: no sprint header in %s (line: %r), skipping",
                    path, first_line
                )
                return None

        sprint_title = f"Sprint {sprint_number}"

        logger.info(
            "SprintScanner: found sprint %d, day %d, date %s in %s",
            sprint_number, day_number, date_str, path
        )

        # 4. Resolver project_id
        project = self.db.get_project(project_slug)
        if not project:
            logger.warning(
                "SprintScanner: project '%s' not found, skipping", project_slug
            )
            return None
        project_id = project["id"]

        # 5. Criar/atualizar sprint
        # Sprint 1 é marcada como completed, sprints ativas como active
        sprint_status = "completed" if sprint_number == 1 else "active"
        sprint = self.db.upsert_sprint(
            project_id=project_id,
            number=sprint_number,
            title=sprint_title,
            status=sprint_status,
        )
        sprint_id = sprint["id"]
        logger.info(
            "SprintScanner: sprint %d (id=%d) ready", sprint_number, sprint_id
        )

        # 6. Atualizar day_count na sprint (máximo day_number visto)
        existing_days = self.db.get_connection().execute(
            "SELECT COUNT(*) AS cnt FROM sprint_dates WHERE sprint_id = ?",
            (sprint_id,)
        ).fetchone()
        current_day_count = existing_days["cnt"] if existing_days else 0
        if day_number > current_day_count:
            self.db.get_connection().execute(
                "UPDATE sprints SET day_count = ? WHERE id = ?",
                (max(day_number, current_day_count), sprint_id)
            )
            self.db.get_connection().commit()

        # 7. Criar registro em sprint_dates
        sprint_date = self.db.upsert_sprint_date(
            sprint_id=sprint_id,
            date=date_str,
            day_number=day_number,
        )
        logger.info(
            "SprintScanner: sprint_date created for %s (day %d)",
            date_str, day_number
        )

        return {
            "date": date_str,
            "sprint_number": sprint_number,
            "day_number": day_number,
            "sprint_title": sprint_title,
            "sprint_id": sprint_id,
            "sprint_date": sprint_date,
        }
