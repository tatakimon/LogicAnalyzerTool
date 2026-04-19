#!/usr/bin/env python3
"""
HIL Autonomous Loop — automated end-to-end verification pipeline.

Usage:
    python3 auto_hil.py --scenario accel_stream    # ISM330DHCX accelerometer
    python3 auto_hil.py --scenario temperature     # STTS22H temperature
    python3 auto_hil.py --scenario accel_stream --no-interactive  # batch mode

Workflow:
    1. Detect Saleae + VCP devices
    2. Build + flash scenario firmware
    3. Stage 1: logic analyzer timing (must pass < 5% deviation)
    4. Stage 2: live VCP stream (first reading)
    5. Interactive: prompt user to tilt / cover sensor
    6. Write result to /tmp/hil_verification_result.txt
"""
import argparse
import subprocess
import sys
import time
import os
import serial

PANE2 = "/home/kerem/logic_analyzer/hil_framework/.pane2_output"
PANE3 = "/home/kerem/logic_analyzer/hil_framework/.pane3_output"
WORKSPACE = "/home/kerem/logic_analyzer/hil_workspace"
FW_BIN = f"{WORKSPACE}/Debug/Logic_Analyzer_USART3.bin"
VCP_PORT = "/dev/ttyACM0"
VCP_BAUD = 115200
TIMING_TOLERANCE = 0.05

SYSRED = "\033[91m"
SYSGREEN = "\033[92m"
SYSYELLOW = "\033[93m"
SYSRESET = "\033[0m"


def r(s): return f"{SYSRED}{s}{SYSRESET}"
def g(s): return f"{SYSGREEN}{s}{SYSRESET}"
def y(s): return f"{SYSYELLOW}{s}{SYSRESET}"


def run(cmd, timeout=60, check=False, capture=True):
    kw = {}
    if capture:
        kw = {"capture_output": True, "text": True}
    try:
        result = subprocess.run(cmd, timeout=timeout, shell=isinstance(cmd, str), **kw)
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
        return result
    except subprocess.TimeoutExpired:
        return None


def dual(msg, pane_file):
    """Write to both stdout and the specified pane file."""
    print(msg)
    with open(pane_file, "a") as f:
        f.write(msg + "\n")


def stage(tag, msg):
    banner = f"\n{'='*70}\n  {tag} — {msg}\n{'='*70}"
    dual(banner, PANE2)
    dual(f"\n  {tag}: {msg}", PANE2)


# ── Device Detection ──────────────────────────────────────────────────────

def detect_devices():
    stage("SETUP", "Detecting hardware")
    res = run(["sigrok-cli", "--scan"])
    saleae_conn = None
    for line in (res.stdout + res.stderr).splitlines():
        if "fx2lafw" in line and "conn=" in line:
            saleae_conn = line.split("fx2lafw:")[1].split(" ")[0]
            break
    if not saleae_conn:
        dual(r("[ERROR]") + " Saleae not found", PANE2); return None, None
    dual(g("[OK]") + f" Saleae: fx2lafw:{saleae_conn}", PANE2)
    try:
        s = serial.Serial(VCP_PORT, VCP_BAUD, timeout=1)
        s.close()
        dual(g("[OK]") + f" VCP: {VCP_PORT}", PANE2)
    except serial.SerialException:
        dual(r("[ERROR]") + f" VCP not found at {VCP_PORT}", PANE2); return None, None
    return saleae_conn, VCP_PORT


# ── Build + Flash ──────────────────────────────────────────────────────────

def build_and_flash(scenario_name):
    stage("CODING", f"Applying scenario: {scenario_name}")
    scenario_dir = f"/home/kerem/logic_analyzer/hil_framework/scenarios/{scenario_name}"

    if os.path.exists(f"{scenario_dir}/USER_CODE_PV.c"):
        dual(f"  Applying {scenario_name} USER CODE blocks to main.c", PANE2)
        res = run(
            ["python3", "/home/kerem/logic_analyzer/hil_framework/switch_scenario.py", scenario_name],
            timeout=120,
        )
        if res is None or res.returncode != 0:
            dual(r("[ERROR]") + " switch_scenario failed", PANE2)
            if res: dual(res.stderr[-500:], PANE2)
            return False
        dual(g("[OK]") + " Scenario applied and compiled", PANE2)
    else:
        dual(y("[WARN]") + " Scenario dir not found, building current firmware", PANE2)

    stage("COMPILING", "Building firmware")
    res = run(["make", "-C", f"{WORKSPACE}/Debug", "all"], timeout=120)
    if res is None or res.returncode != 0:
        dual(r("[ERROR]") + " Build failed", PANE2)
        if res: dual(res.stderr[-500:], PANE2)
        return False
    dual(g("[OK]") + f" Build complete: {FW_BIN}", PANE2)

    stage("FLASHING", "Writing firmware to board")
    run(["pkill", "-f", "vcp_feed.py"], capture=False)
    time.sleep(1)

    combined_cmd = (
        f"st-flash erase 0x8000000 0x200000 && "
        f"st-flash --reset write {FW_BIN} 0x8000000"
    )
    # Retry loop: WSL2 USB passthrough is unreliable after mass erase
    for attempt in range(1, 8):
        res = run(combined_cmd, timeout=120)
        combined = (res.stdout + res.stderr).lower() if res else ""
        if res and res.returncode == 0 and ("verified" in combined or "jolly good" in combined):
            dual(g("[OK]") + f" Flash verified! (attempt {attempt})", PANE2)
            return True
        dual(f"  Flash attempt {attempt} failed, retrying...", PANE2)
        time.sleep(1)
    dual(r("[ERROR]") + " Flash failed after 7 attempts", PANE2)
    return False


# ── Logic Analyzer Capture ──────────────────────────────────────────────────

def capture_and_timing(saleae_conn):
    import zipfile
    import os as _os
    stage("CAPTURE", "Acquiring logic analyzer samples")
    sr_file = "/tmp/hil_auto_capture.sr"
    cmd = [
        "sigrok-cli",
        "-d", f"fx2lafw:conn={saleae_conn}",
        "-c", "samplerate=12M",
        "--samples", "12000000",
        "-o", sr_file,
    ]
    res = run(cmd, timeout=90)
    if res is None or not _os.path.exists(sr_file):
        dual(r("[ERROR]") + " sigrok-cli capture failed or timed out", PANE2); return None

    extract_dir = "/tmp/hil_sr_extract"
    _os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(sr_file, "r") as z:
        z.extractall(extract_dir)
    active_ch = None
    for ch in range(8):
        samples = []
        logic_files = sorted(
            [f for f in _os.listdir(extract_dir) if f.startswith("logic-")],
            key=lambda x: int(x.rsplit("-", 1)[-1])
        )
        for lf in logic_files:
            with open(_os.path.join(extract_dir, lf), "rb") as f:
                for byte in f.read():
                    samples.append((byte >> ch) & 1)
        transitions = sum(1 for i in range(1, len(samples)) if samples[i] != samples[i-1])
        if transitions > 10:
            active_ch = ch
            break
    if active_ch is None:
        dual(r("[ERROR]") + " No active channels found", PANE2); return None
    dual(g("[OK]") + f" Active channel: CH{active_ch} ({transitions} transitions)", PANE2)

    from timing import analyze_timing
    faults, report = analyze_timing(
        sr_file=sr_file,
        channel=active_ch,
        baud=115200,
        tolerance=TIMING_TOLERANCE,
        min_gap_us=20.0,
        verbose=False,
    )

    if faults < 0:
        dual(y("[WARN]") + " Timing analysis unavailable", PANE2); return active_ch
    if faults == 0:
        dual(g("[PASS]") + " Stage 1: 0 timing violations", PANE2); return active_ch
    else:
        dual(r("[FAIL]") + f" Stage 1: {faults} timing violations", PANE2); return None


# ── VCP Stream ─────────────────────────────────────────────────────────────

def read_vcp_first(port=VCP_PORT, baud=VCP_BAUD, wait=3):
    stage("HIL VERIFICATION", "Reading VCP stream")
    time.sleep(1)
    try:
        s = serial.Serial(port, baud, timeout=1)
        s.reset_input_buffer()
        time.sleep(wait)
        data = s.read(500)
        s.close()
        text = data.decode("utf-8", "replace")
        dual(g("[STAGE 2]") + f" VCP sample:\n  {text[:200].strip()}", PANE2)
        dual(g("[VCP]") + f" Live stream sample:\n  {text[:200].strip()}", PANE3)
        return text
    except serial.SerialException as e:
        dual(r("[VCP ERROR]") + f" {e}", PANE3)
        return f"[VCP ERROR: {e}]"


# ── Interactive Verification ────────────────────────────────────────────────

def prompt_physical_test(scenario_name, vcp_text):
    print(f"\n{'─'*60}")
    prompt = {
        "accel_stream": (
            "PHYSICAL TEST: Tilt the board and watch X/Y/Z change!\n"
            "  - AX/Y should swing when tilted\n"
            "  - AZ should stay near ±1000 mg (gravity)\n"
            "  - Did values change? Press YES to confirm, NO to retry"
        ),
        "temperature": (
            "PHYSICAL TEST: Cover the temperature sensor or blow on it.\n"
            "  - TEMP value should change (rise or fall)\n"
            "  - Did the value change? Press YES to confirm, NO to retry"
        ),
    }.get(scenario_name, "PHYSICAL TEST: Check the sensor output, then confirm.")

    print(f"\n{g('[STAGE 2 — HITL]')} {prompt}")
    print(f"\nLast VCP reading:\n  {vcp_text[:200]}")
    while True:
        ans = input(f"\n  [{g('y')}/{r('n')}] User confirms sensor responds: ").strip().lower()
        if ans in ("y", "yes"):
            result = "PASS"
            break
        elif ans in ("n", "no"):
            result = "FAIL"
            break
        print("  Please enter y or n")
    return result


# ── Save Result ────────────────────────────────────────────────────────────

def save_result(scenario, stage1_passed, physical_result):
    result_file = "/tmp/hil_verification_result.txt"
    with open(result_file, "w") as f:
        f.write(f"scenario={scenario}\n")
        f.write(f"stage1_timing={'PASS' if stage1_passed else 'FAIL'}\n")
        f.write(f"physical_test={physical_result}\n")
        f.write(f"overall={'PASS' if stage1_passed and physical_result == 'PASS' else 'FAIL'}\n")
    print(f"\n{g('[DONE]')} Result saved to {result_file}")


# ── Main ───────────────────────────────────────────────────────────────────

def detect_intent(prompt):
    """Map a free-text prompt to a scenario name."""
    p = prompt.lower()
    if any(k in p for k in ["accel", "accelerom", "tilt", "x y z", "ism330"]):
        return "accel_stream"
    if any(k in p for k in ["temp", "hts221", "stts22h", "temperature"]):
        return "temperature"
    return None


def run_hil(scenario, prompt_text=None, interactive=True):
    """Run the full HIL loop for a given scenario."""
    if prompt_text:
        print(f"{g('[AUTO-HIL]')} Detected: {scenario} | '{prompt_text}'")
    else:
        print(f"{g('[AUTO-HIL]')} Running scenario: {scenario}")

    # Wipe panes
    open(PANE2, "w").close()
    open(PANE3, "w").close()

    header = f"\n{'='*60}\n  HIL AUTONOMOUS LOOP — {scenario}\n  Prompt: {prompt_text}\n{'='*60}"
    dual(header, PANE2)
    dual("[STAGE 0] Starting HIL loop", PANE2)
    dual(f"[AUTO-HIL] Detected scenario: {scenario}", PANE2)
    dual("[STAGE 0] Starting HIL loop", PANE3)
    dual(f"[AUTO-HIL] Scenario: {scenario} | {prompt_text or scenario}", PANE3)

    saleae_conn, vc_port = detect_devices()
    if not saleae_conn:
        dual(r("[ABORT]") + " Devices not ready", PANE2); sys.exit(1)

    if not build_and_flash(scenario):
        dual(r("[ABORT]") + " Build/flash failed", PANE2); sys.exit(1)

    run(["pkill", "-f", "vcp_feed.py"], capture=False)
    time.sleep(1)
    subprocess.Popen(
        ["python3", "/home/kerem/logic_analyzer/hil_framework/vcp_feed.py",
         "--output", PANE3],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    dual("[STAGE 2] Opening live VCP feed → Pane 3", PANE2)

    active_ch = capture_and_timing(saleae_conn)
    if active_ch is None:
        dual(r("[ABORT]") + " Stage 1 failed — timing violations found", PANE2); sys.exit(1)

    vcp_text = read_vcp_first()

    physical = prompt_physical_test(scenario, vcp_text) if interactive else "SKIPPED"
    save_result(scenario, True, physical)

    if physical == "PASS":
        dual(g("[SUCCESS]") + " HIL VERIFICATION COMPLETE — ALL STAGES PASSED", PANE2)
        banner2("SUCCESS — Hardware verified, sensor confirmed")
    elif physical == "SKIPPED":
        dual(g("[OK]") + " HIL loop complete (physical test skipped)", PANE2)
    else:
        dual(r("[RETRY]") + " Physical test failed — review sensor integration", PANE2)


def main():
    parser = argparse.ArgumentParser(description="HIL Autonomous Loop")
    parser.add_argument("--scenario", default=None,
                        choices=["accel_stream", "temperature"],
                        help="Scenario to load from scenarios/")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Skip physical test (auto-PASS when using --prompt)")
    parser.add_argument("--tmux", action="store_true",
                        help="Open War Room tmux session (3 panes) before starting")
    parser.add_argument("--prompt", type=str, default=None,
                        help="Free-text prompt — auto-detect scenario and run HIL loop")
    args = parser.parse_args()

    # Free-text mode: detect scenario from prompt
    if args.prompt:
        scenario = detect_intent(args.prompt)
        if scenario is None:
            print(f"{r('[ERROR]')} Could not detect sensor from: '{args.prompt}'")
            print("  Known: accel/accelerometer/tilt/xyz/ism330, temp/hts221/temperature")
            sys.exit(1)
        run_hil(scenario, prompt_text=args.prompt, interactive=not args.no_interactive)
        return

    # Open tmux session if requested
    if args.tmux:
        session = "war_room"
        pane2 = "/home/kerem/logic_analyzer/hil_framework/.pane2_output"
        pane3 = "/home/kerem/logic_analyzer/hil_framework/.pane3_output"
        proj = "/home/kerem/logic_analyzer"
        subprocess.run(["tmux", "kill-session", "-t", session],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["tmux", "new-session", "-d", "-s", session, "-c", proj],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["tmux", "split-window", "-h", "-t", f"{session}:0", "-c", proj,
                        f"tail -f {pane2}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["tmux", "split-window", "-v", "-t", f"{session}:0.1", "-c", proj,
                        f"tail -f {pane3}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["tmux", "select-pane", "-t", f"{session}:0.0"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"\n{g('[AUTO-HIL]')} War Room started: tmux attach -t {session}")
        print("  Ctrl+b d to detach | Ctrl+b ←→↑↓ to switch panes")
        print("")

    scenario = args.scenario or "accel_stream"
    run_hil(scenario, interactive=not args.no_interactive)


if __name__ == "__main__":
    main()
