"""Parser for DIARIO.md — audit entry extraction.

Parses the operational DIARIO.md (Dalinar's log) and extracts auditoria
records with veredito, commit hash, ressalva and observations.
Compatible with the canonical db.py schema (auditorias table).
Python 3.10+ stdlib only.
"""

import logging
import os
import re
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# Regex patterns
RE_DATE_HEADER = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\b")
RE_AUDITORIA_ENTRY = re.compile(
    r"^###\s+(?:\d{4}-\d{2}-\d{2}\s+)?~?\d{2}:\d{2}\s*[—–-]+\s*Auditoria\s+(TASK-(\d+))",
    re.IGNORECASE,
)
RE_TASK_IN_TEXT = re.compile(r"Auditoria\s+TASK-(\d+)", re.IGNORECASE)
RE_COMMIT = re.compile(r"\*\*Commit:\*\*\s*[`#]?\s*([0-9a-fA-F]{6,40})\b")
RE_VEREDITO = re.compile(r"\*\*Veredito:\*\*\s*(.+)")
RE_RESSALVA = re.compile(r"\*\*Ressalva:\*\*\s*(.+)")
RE_BULLET = re.compile(r"^[\s]*[-•]\s+(.*)$")


class DiarioScanner:
    """Scans DIARIO.md and populates the auditorias table.

    Detects date headers, audit entries with TASK-NNN identifiers,
    extracts commit hashes, vereditos, ressalvas, and observations.
    """

    def scan(self, filepath: str, conn: sqlite3.Connection) -> int:
        """Parse DIARIO.md and insert auditoria records.

        Args:
            filepath: Path to DIARIO.md (supports ~ expansion).
            conn:     Active SQLite connection.

        Returns:
            Number of auditorias inserted.
        """
        path = os.path.expanduser(filepath)
        if not os.path.isfile(path):
            logger.warning("DIARIO.md não encontrado: %s", path)
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            logger.warning("Erro ao ler %s: %s", path, e)
            return 0

        # Split into ###-delimited blocks
        blocks = re.split(r"^(?=^###\s)", text, flags=re.MULTILINE)

        current_date: Optional[str] = None
        count = 0

        for block in blocks:
            block = block.strip()
            if not block:
                continue

            # Date-only header line?
            date_match = RE_DATE_HEADER.match(block)
            if date_match:
                current_date = date_match.group(1)
                if block.count("\n") < 1:
                    continue

            # Does this block reference an auditoria TASK-NNN?
            task_match = RE_TASK_IN_TEXT.search(block)
            if not task_match:
                continue

            task_num = task_match.group(1)  # numeric part, e.g. "91"

            commit_hash = self._extract_commit(block)
            veredito_text = self._extract_veredito(block)
            ressalva = self._extract_ressalva(block)
            observacoes = self._extract_observacoes(block)

            if not veredito_text or not commit_hash:
                logger.debug("TASK-%s sem veredito/commit — skip", task_num)
                continue

            veredito = self._normalize_veredito(veredito_text)
            full_text = f"{veredito_text} {ressalva or ''}".lower()
            scope_creep = "scope creep" in full_text or "escopo" in full_text
            task_file_ok = 1 if "aprovado" in veredito_text.lower() else 0
            indices_ok = task_file_ok

            # Extract ressalva from veredito string if not explicit
            if veredito == "aprovado_ressalva" and not ressalva:
                m = re.search(r"com\s+ressalva[:\s]*(.+)", veredito_text, re.IGNORECASE)
                if m:
                    ressalva = m.group(1).strip()

            try:
                # task_number repete entre projetos. Resolvemos de forma
                # DETERMINÍSTICA preferindo control-tower-v2 (projeto do DIARIO
                # operacional do Dalinar) — evita linkar a auditoria à task de
                # mesmo número de outro projeto (bug M7).
                task_row = conn.execute(
                    """SELECT t.id FROM tasks t
                       JOIN projects p ON t.project_id = p.id
                       WHERE t.task_number = ?
                       ORDER BY CASE p.slug WHEN 'control-tower-v2' THEN 0 ELSE 1 END, t.id
                       LIMIT 1""",
                    (task_num,),
                ).fetchone()
                if not task_row:
                    logger.debug("TASK-%s não encontrada em tasks — skip (task_01 DB vazio)", task_num)
                    continue

                conn.execute(
                    """INSERT OR IGNORE INTO auditorias
                       (task_id, auditor, veredito, ressalva, scope_creep,
                        task_file_ok, indices_ok, diff_hash, audit_hash, observacoes)
                       VALUES (?, 'Dalinar', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task_row["id"], veredito, ressalva,
                     1 if scope_creep else 0,
                     task_file_ok, indices_ok,
                     commit_hash, commit_hash, observacoes),
                )
                conn.commit()
                count += 1
            except Exception as e:
                logger.warning("Erro inserir TASK-%s: %s", task_num, e)

        logger.info("DIARIO.md: %d auditorias", count)
        return count

    # --- Extraction helpers ---

    @staticmethod
    def _extract_commit(block: str) -> Optional[str]:
        m = RE_COMMIT.search(block)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_veredito(block: str) -> Optional[str]:
        m = RE_VEREDITO.search(block)
        if m:
            return m.group(1).strip()
        alt = re.search(r"[✅⚠️❌]\s*(APROVADO|REJEITADO)", block, re.IGNORECASE)
        return alt.group(0).strip() if alt else None

    @staticmethod
    def _extract_ressalva(block: str) -> Optional[str]:
        m = RE_RESSALVA.search(block)
        return m.group(1).strip() if m else None

    @staticmethod
    def _extract_observacoes(block: str) -> Optional[str]:
        lines = []
        for line in block.split("\n"):
            ls = line.strip()
            m = RE_BULLET.match(ls)
            if m:
                lines.append(m.group(1).strip())
        return "\n".join(lines) if lines else None

    @staticmethod
    def _normalize_veredito(text: str) -> str:
        upper = text.upper()
        if "REJEITADO" in upper:
            return "rejeitado"
        if "RESSALVA" in upper:
            return "aprovado_ressalva"
        if "APROVADO" in upper:
            return "aprovado"
        logger.warning("Veredito não reconhecido: %s", text)
        return "aprovado"
