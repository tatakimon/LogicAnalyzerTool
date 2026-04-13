#!/bin/bash
# flash.sh — Build, flash, and verify B-U585I-IOT02A firmware
#
#   ./flash.sh              # interactive menu
#   ./flash.sh --flash      # build + flash only
#   ./flash.sh --dashboard  # build + flash + run dashboard.py
#   ./flash.sh --rich       # build + flash + run rich_live.py
#   ./flash.sh --test       # build + flash + quick capture test

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/active_trial_1"
BIN="$PROJECT_DIR/Debug/Logic_Analyzer_USART3.bin"
HEX="$PROJECT_DIR/Debug/Logic_Analyzer_USART3.hex"

# ── Helpers ────────────────────────────────────────────────────────
info()  { echo -e "  \033[36m[INFO]\033[0m  $1"; }
ok()    { echo -e "  \033[32m[ OK ]\033[0m  $1"; }
warn()  { echo -e "  \033[33m[WARN]\033[0m $1"; }
fail()  { echo -e "  \033[31m[FAIL]\033[0m $1"; }

# ── Build ──────────────────────────────────────────────────────────
build() {
    echo ""
    echo "  \033[1m[\033[35m1/4\033[0m\033[1m] BUILDING firmware...\033[0m"
    make -C "$PROJECT_DIR/Debug" all -s
    if [ ! -f "$BIN" ]; then
        fail "Build failed — $BIN not found"
        exit 1
    fi
    ok "Build complete"
}

# ── Flash ──────────────────────────────────────────────────────────
flash_board() {
    echo ""
    echo "  \033[1m[\033[35m2/4\033[0m\033[1m] FLASHING to /dev/ttyACM0...\033[0m"
    st-flash --reset write "$BIN" 0x8000000 2>&1 | grep -E "written|ERROR|verified|WARN" | head -10
    ok "Flash complete"
}

# ── Wait ───────────────────────────────────────────────────────────
wait_boot() {
    echo ""
    echo "  \033[1m[\033[35m3/4\033[0m\033[1m] WAITING for board to initialize (2s)...\033[0m"
    sleep 2
    ok "Board ready"
}

# ── Verify ─────────────────────────────────────────────────────────
verify() {
    echo ""
    echo "  \033[1m[\033[35m4/4\033[0m\033[1m] VERIFYING output via VCP...\033[0m"
    python3 -c "
import serial, time, os
try:
    os.system('fuser -k /dev/ttyACM0 2>/dev/null')
    time.sleep(0.5)
except: pass
try:
    with serial.Serial('/dev/ttyACM0', 115200, timeout=3) as ser:
        ser.reset_input_buffer()
        time.sleep(1)
        data = ser.read(200)
        if data and len(data) > 10:
            print(f'  \033[32m[ OK ]\033[0m  Board alive — {len(data)} bytes received')
            text = data.decode(\"utf-8\", errors=\"replace\")
            lines = [l.strip() for l in text.split(\"\r\n\") if l.strip()]
            if lines:
                print(f'  \033[36m[INFO]\033[0m  First line: {repr(lines[0][:60])}')
        else:
            print('  \033[33m[WARN]\033[0m No data — board may be busy or disconnected')
except Exception as e:
    print(f'  \033[33m[WARN]\033[0m VCP verify skipped: {e}')
    print('  \033[36m[INFO]\033[0m Build and flash succeeded — run tools manually')
" 2>&1
}

# ── Post-flash tools ────────────────────────────────────────────────
run_dashboard() {
    echo ""
    echo "  \033[1m─────────────────────────────────────────────\033[0m"
    echo "  \033[1m  Running dashboard.py...\033[0m"
    echo "  \033[1m─────────────────────────────────────────────\033[0m"
    python3 "$SCRIPT_DIR/hil_framework/dashboard.py" --duration 3 --channel 1
}

run_rich() {
    echo ""
    echo "  \033[1m─────────────────────────────────────────────\033[0m"
    echo "  \033[1m  Running rich_live.py...\033[0m"
    echo "  \033[1m─────────────────────────────────────────────\033[0m"
    python3 "$SCRIPT_DIR/hil_framework/rich_live.py" --duration 3 --channel 1
}

run_test() {
    echo ""
    echo "  \033[1m─────────────────────────────────────────────\033[0m"
    echo "  \033[1m  Quick capture test...\033[0m"
    echo "  \033[1m─────────────────────────────────────────────\033[0m"
    python3 -c "
from hil_framework.capture import quick_capture
from hil_framework.timing import estimate_baud_from_samples
r = quick_capture(duration_s=2, sample_rate='12M', channel=1, baud=115200)
implied, dev = estimate_baud_from_samples(r['channel_samples'], 12_000_000, 115200)
faulted = 0
if r.get('sr_filepath'):
    from hil_framework.timing import byte_timing_map
    tmap = byte_timing_map(r['sr_filepath'], channel=1, decoded_bytes=r['raw_bytes'],
                           channel_samples=r['channel_samples'],
                           sample_rate_hz=12_000_000, baud=115200, tolerance=0.05)
    faulted = sum(1 for bt in tmap if bt.fault_count > 0)
n = len(r['raw_bytes'])
color = '\033[32m' if faulted == 0 else '\033[31m'
print(f'  \033[36m[INFO]\033[0m  Bytes: {n}  |  Implied: {implied:.0f} baud  |  Dev: {dev:+.1f}%')
print(f'  {color}[INFO]\033[0m  Timing faults: {faulted}/{n}')
" 2>&1
}

# ── Interactive menu ────────────────────────────────────────────────
menu() {
    echo ""
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║   HIL FIRMWARE — FLASH MENU           ║"
    echo "  ╠═══════════════════════════════════════╣"
    echo "  ║  1)  Build + Flash + Verify only      ║"
    echo "  ║  2)  Build + Flash + Dashboard       ║"
    echo "  ║  3)  Build + Flash + Rich Live      ║"
    echo "  ║  4)  Build + Flash + Quick Test     ║"
    echo "  ║  5)  Build + Flash + ALL tools       ║"
    echo "  ║                                       ║"
    echo "  ║  0)  Exit                            ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo ""
    echo -n "  Select option [0-5]: "
    read -r choice
    echo ""

    case "$choice" in
        1) build; flash_board; wait_boot; verify ;;
        2) build; flash_board; wait_boot; verify; run_dashboard ;;
        3) build; flash_board; wait_boot; verify; run_rich ;;
        4) build; flash_board; wait_boot; verify; run_test ;;
        5)
            build; flash_board; wait_boot; verify
            echo ""
            echo "  \033[1m─────────────────────────────────────────────\033[0m"
            echo "  \033[1m  Running all tools sequentially...\033[0m"
            echo "  \033[1m─────────────────────────────────────────────\033[0m"
            echo ""
            echo "  \033[1m[1/3] Dashboard\033[0m"
            python3 "$SCRIPT_DIR/hil_framework/dashboard.py" --duration 2 --channel 1
            echo ""
            echo "  \033[1m[2/3] Rich Live\033[0m"
            python3 "$SCRIPT_DIR/hil_framework/rich_live.py" --duration 2 --channel 1
            echo ""
            echo "  \033[1m[3/3] Quick Test\033[0m"
            run_test
            ;;
        0) echo "  Exiting."; exit 0 ;;
        *) echo "  Invalid option: $choice"; exit 1 ;;
    esac
}

# ── CLI shortcut mode (no menu) ────────────────────────────────────
cli_mode() {
    build; flash_board; wait_boot; verify
    case "$1" in
        --dashboard) run_dashboard ;;
        --rich)      run_rich ;;
        --test)      run_test ;;
        --flash)     ;;  # flash only, no post-run tool
        *)           echo "  Done!" ;;
    esac
}

# ── Entry point ────────────────────────────────────────────────────
echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║   HIL FIRMWARE — BUILD & FLASH        ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""
echo "  Usage:  ./flash.sh          (interactive menu)"
echo "          ./flash.sh --flash      (build + flash only)"
echo "          ./flash.sh --dashboard  (build + flash + dashboard)"
echo "          ./flash.sh --rich       (build + flash + rich_live)"
echo "          ./flash.sh --test       (build + flash + quick test)"
echo ""

if [ -z "$1" ]; then
    menu
else
    cli_mode "$1"
fi
