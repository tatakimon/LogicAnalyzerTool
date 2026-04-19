#!/bin/bash
# war_room.sh — 3 separate terminal setup
# Run in 3 separate terminals after this script prints instructions.
# This script only prints the commands — it does NOT open terminals itself.

PROJ="/home/kerem/logic_analyzer"
PANE2="${PROJ}/hil_framework/.pane2_output"
PANE3="${PROJ}/hil_framework/.pane3_output"

echo "=========================================="
echo "  War Room — 3 Separate Terminals"
echo "=========================================="
echo ""
echo "Open 3 terminal windows, then run:"
echo ""
echo "  [TERMINAL 1 — This one]"
echo "    Use normally for Claude Code"
echo ""
echo "  [TERMINAL 2 — Logic Analyzer Output]"
echo "    tail -f ${PANE2}"
echo ""
echo "  [TERMINAL 3 — VCP Live Feed]"
echo "    tail -f ${PANE3}"
echo ""
echo "=========================================="
