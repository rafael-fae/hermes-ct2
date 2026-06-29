#!/usr/bin/env bash
# sync-db.sh — Sincroniza o banco CT2 local com o CT2-Public
# Executar: ./sync-db.sh
# Pode ser chamado como hook pelo kanban_ct2_sync.py

set -e
SRC="$HOME/Dev/control-tower-v2/state/ct2.db"
DST="$HOME/Dev/ct2-public/state/ct2.db"
BACKUP="$HOME/Dev/ct2-public/state/ct2.db.bak"

if [ ! -f "$SRC" ]; then
  echo "ERRO: Banco origem nao encontrado: $SRC"
  exit 1
fi

# Backup
cp "$DST" "$BACKUP.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true

# Copiar
cp "$SRC" "$DST"
echo "OK: ct2.db sincronizado para ct2-public"
