#!/usr/bin/env bash
# Startet die Scalable-Edition der Trading-App.
#
# Setzt TRADING_EDITION=scalable und TradingDB=<DataDir> und startet
# Streamlit auf einem eigenen Port (parallel zur vollen App möglich).
#
# Usage:
#   bash start_scalable.sh
#   bash start_scalable.sh /opt/scalable_data 8090

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

DATA_DIR="${1:-$HOME/scalable_data}"
PORT="${2:-8081}"

if [ ! -f "$ROOT/.venv/bin/activate" ]; then
    echo "[FEHLER] .venv nicht gefunden. Bitte zuerst install.sh ausfuehren." >&2
    exit 1
fi

if [ ! -f "$DATA_DIR/config.yaml" ]; then
    echo "[FEHLER] config.yaml fehlt in $DATA_DIR." >&2
    echo "Bitte zuerst das Setup ausfuehren:" >&2
    echo "  bash setup_scalable.sh \"$DATA_DIR\"" >&2
    exit 1
fi

export TRADING_EDITION=scalable
export TradingDB="$DATA_DIR"

echo "Scalable-Edition"
echo "  TRADING_EDITION = scalable"
echo "  TradingDB       = $DATA_DIR"
echo "  Port            = $PORT"
echo ""

# shellcheck source=/dev/null
source "$ROOT/.venv/bin/activate"
streamlit run asset_analyzer.py --server.port "$PORT"
