#!/usr/bin/env python3
"""Relink orphan auditorias and remove duplicate audit rows.

Default mode is a dry run:
    python src/audit_dedup.py

Apply changes:
    python src/audit_dedup.py --execute

Ask before each applied change:
    python src/audit_dedup.py --execute --interactive
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "state" / "ct2.db"

PROJECT_PRIORITY = {
    1: "control-tower-v2",
    5: "oeste-gestao",
    3: "agent-ops-workflow",
}


@dataclass(frozen=True)
class RelinkChange:
    audit_id: int
    old_task_id: int
    new_task_id: int
    new_task_number: int | None
    project_id: int
    project_name: str
    title: str | None
    candidate_count: int


@dataclass(frozen=True)
class DeleteChange:
    task_id: int
    diff_hash: str
    keep_id: int
    delete_id: int
    group_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fix orphan auditorias.task_id values and remove duplicate auditorias."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned SQL changes without applying them. This is the default.",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Apply SQL changes to state/ct2.db.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="When executing, ask for confirmation before each update or delete.",
    )
    return parser.parse_args()


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERROR: database not found: {DB_PATH}", file=sys.stderr)
        raise SystemExit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def print_header(execute: bool, interactive: bool) -> None:
    mode = "EXECUTE" if execute else "DRY RUN"
    if interactive:
        mode += " + INTERACTIVE"

    print("=" * 72)
    print("AUDITORIA DEDUP AUDIT")
    print("=" * 72)
    print(f"Database: {DB_PATH}")
    print(f"Mode: {mode}")
    if interactive and not execute:
        print("Note: --interactive only prompts when --execute is also set.")
    print()


def row_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    audit_count = conn.execute("SELECT COUNT(*) FROM auditorias").fetchone()[0]
    task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    return audit_count, task_count


def find_orphans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT a.id, a.task_id
        FROM auditorias a
        LEFT JOIN tasks t ON a.task_id = t.id
        WHERE t.id IS NULL
        ORDER BY a.id
        """
    ).fetchall()


def find_task_candidates(conn: sqlite3.Connection, task_number: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            t.id,
            t.task_number,
            t.project_id,
            COALESCE(p.slug, p.name, t.project_slug, '') AS project_name,
            t.title
        FROM tasks t
        LEFT JOIN projects p ON p.id = t.project_id
        WHERE t.task_number = ?
        ORDER BY
            CASE t.project_id
                WHEN 1 THEN 0
                WHEN 5 THEN 1
                WHEN 3 THEN 2
                ELSE 99
            END,
            t.id
        """,
        (task_number,),
    ).fetchall()


def choose_candidate(candidates: list[sqlite3.Row]) -> sqlite3.Row | None:
    return candidates[0] if candidates else None


def collect_relink_changes(conn: sqlite3.Connection) -> tuple[list[RelinkChange], list[sqlite3.Row]]:
    changes: list[RelinkChange] = []
    unresolved: list[sqlite3.Row] = []

    for orphan in find_orphans(conn):
        candidates = find_task_candidates(conn, orphan["task_id"])
        chosen = choose_candidate(candidates)
        if chosen is None:
            unresolved.append(orphan)
            continue

        changes.append(
            RelinkChange(
                audit_id=orphan["id"],
                old_task_id=orphan["task_id"],
                new_task_id=chosen["id"],
                new_task_number=chosen["task_number"],
                project_id=chosen["project_id"],
                project_name=chosen["project_name"],
                title=chosen["title"],
                candidate_count=len(candidates),
            )
        )

    return changes, unresolved


def find_duplicate_groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT task_id, diff_hash, COUNT(*) AS row_count, MAX(id) AS keep_id
        FROM auditorias
        GROUP BY task_id, diff_hash
        HAVING COUNT(*) > 1
        ORDER BY task_id, diff_hash
        """
    ).fetchall()


def duplicate_delete_ids(
    conn: sqlite3.Connection, task_id: int, diff_hash: str, keep_id: int
) -> list[int]:
    rows = conn.execute(
        """
        SELECT id
        FROM auditorias
        WHERE task_id = ?
          AND diff_hash = ?
          AND id <> ?
        ORDER BY id
        """,
        (task_id, diff_hash, keep_id),
    ).fetchall()
    return [row["id"] for row in rows]


def collect_delete_changes(conn: sqlite3.Connection) -> list[DeleteChange]:
    changes: list[DeleteChange] = []

    for group in find_duplicate_groups(conn):
        delete_ids = duplicate_delete_ids(
            conn,
            task_id=group["task_id"],
            diff_hash=group["diff_hash"],
            keep_id=group["keep_id"],
        )
        for delete_id in delete_ids:
            changes.append(
                DeleteChange(
                    task_id=group["task_id"],
                    diff_hash=group["diff_hash"],
                    keep_id=group["keep_id"],
                    delete_id=delete_id,
                    group_count=group["row_count"],
                )
            )

    return changes


def confirm(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [y/N] ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"", "n", "no"}:
            return False
        print("Please answer y or n.")


def print_relink_plan(changes: list[RelinkChange], unresolved: list[sqlite3.Row]) -> None:
    print("Phase 1: orphan auditorias")
    print("-" * 72)
    print(f"Found orphan rows: {len(changes) + len(unresolved)}")

    if not changes and not unresolved:
        print("No orphan auditorias found.")
        print()
        return

    for change in changes:
        title = change.title or "(no title)"
        project_label = PROJECT_PRIORITY.get(change.project_id, change.project_name)
        note = "multiple candidates" if change.candidate_count > 1 else "single candidate"
        print(
            "UPDATE auditorias "
            f"SET task_id={change.new_task_id} "
            f"WHERE id={change.audit_id};"
        )
        print(
            f"  audit id {change.audit_id}: old task_id {change.old_task_id} "
            f"-> task id {change.new_task_id} "
            f"(task_number={change.new_task_number}, project_id={change.project_id} "
            f"{project_label}, {note})"
        )
        print(f"  title: {title}")

    for orphan in unresolved:
        print(
            f"SKIP audit id {orphan['id']}: no task found with "
            f"task_number={orphan['task_id']}"
        )

    print()


def print_delete_plan(changes: list[DeleteChange]) -> None:
    print("Phase 2: duplicate auditorias")
    print("-" * 72)
    duplicate_groups = {(c.task_id, c.diff_hash, c.keep_id, c.group_count) for c in changes}
    print(f"Found duplicate groups: {len(duplicate_groups)}")

    if not changes:
        print("No duplicate auditorias found.")
        print()
        return

    for change in changes:
        print(f"DELETE FROM auditorias WHERE id={change.delete_id};")
        print(
            f"  task_id={change.task_id}, diff_hash={change.diff_hash}, "
            f"group_count={change.group_count}, keeping max id={change.keep_id}"
        )

    print()


def apply_relinks(
    conn: sqlite3.Connection, changes: list[RelinkChange], interactive: bool
) -> int:
    applied = 0
    for change in changes:
        prompt = (
            f"Apply UPDATE for audit id {change.audit_id}: "
            f"{change.old_task_id} -> {change.new_task_id}?"
        )
        if interactive and not confirm(prompt):
            print(f"Skipped UPDATE for audit id {change.audit_id}")
            continue

        conn.execute(
            "UPDATE auditorias SET task_id = ? WHERE id = ?",
            (change.new_task_id, change.audit_id),
        )
        applied += 1
        print(f"Applied UPDATE for audit id {change.audit_id}")
    return applied


def stage_relinks(conn: sqlite3.Connection, changes: list[RelinkChange]) -> None:
    for change in changes:
        conn.execute(
            "UPDATE auditorias SET task_id = ? WHERE id = ?",
            (change.new_task_id, change.audit_id),
        )


def apply_deletes(
    conn: sqlite3.Connection, changes: list[DeleteChange], interactive: bool
) -> int:
    applied = 0
    for change in changes:
        prompt = (
            f"Delete duplicate auditoria id {change.delete_id} "
            f"(keep id {change.keep_id})?"
        )
        if interactive and not confirm(prompt):
            print(f"Skipped DELETE for audit id {change.delete_id}")
            continue

        conn.execute("DELETE FROM auditorias WHERE id = ?", (change.delete_id,))
        applied += 1
        print(f"Applied DELETE for audit id {change.delete_id}")
    return applied


def main() -> int:
    args = parse_args()
    execute = bool(args.execute)

    print_header(execute=execute, interactive=args.interactive)

    with connect() as conn:
        before_audits, before_tasks = row_counts(conn)
        print(f"Initial rows: auditorias={before_audits}, tasks={before_tasks}")
        print()

        relinks, unresolved = collect_relink_changes(conn)
        print_relink_plan(relinks, unresolved)

        applied_relinks = 0
        applied_deletes = 0

        if execute:
            applied_relinks = apply_relinks(conn, relinks, args.interactive)
        else:
            stage_relinks(conn, relinks)

        # Recompute duplicates after relinking so repaired rows are included.
        deletes = collect_delete_changes(conn)
        print_delete_plan(deletes)

        if execute:
            applied_deletes = apply_deletes(conn, deletes, args.interactive)
            conn.commit()
            after_audits, after_tasks = row_counts(conn)
            print("Committed changes.")
            print(
                f"Applied: updates={applied_relinks}, deletes={applied_deletes}, "
                f"unresolved_orphans={len(unresolved)}"
            )
            print(f"Final rows: auditorias={after_audits}, tasks={after_tasks}")
        else:
            print("Dry run complete. No changes were written.")
            print(
                f"Would apply: updates={len(relinks)}, deletes={len(deletes)}, "
                f"unresolved_orphans={len(unresolved)}"
            )
            conn.rollback()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
