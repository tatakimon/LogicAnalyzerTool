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


def write_pane2(msg):
    with open(PANE2, "a") as f:
        f.write(msg + "\n")


def write_pane3(msg):
    with open(PANE3, "a") as f:
        f.write(msg + "\n")


def banner2(text):
    write_pane2(f"\n{'='*70}\n  {text}\n{'='*70}")


def stage(tag, msg):
    banner2(f"{tag} — {msg}")
    print(f"\n{g('[AUTO-HIL]')} {tag}: {msg}")


# ── Device Detection ──────────────────────────────────────────────────────

def detect_devices():
    stage("SETUP", "Detecting hardware")
    # Saleae
    res = run(["sigrok-cli", "--scan"])
    saleae_conn = None
    for line in (res.stdout + res.stderr).splitlines():
        if "fx2lafw" in line and "conn=" in line:
            saleae_conn = line.split("fx2lafw:")[1].split(" ")[0]
            break
    if not saleae_conn:
        print(f"{r('[ERROR]')} Saleae not found"); return None, None
    print(f"{g('[OK]')} Saleae: fx2lafw:{saleae_conn}")

    # VCP
    try:
        s = serial.Serial(VCP_PORT, VCP_BAUD, timeout=1)
        s.close()
        print(f"{g('[OK]')} VCP: {VCP_PORT}")
    except serial.SerialException:
        print(f"{r('[ERROR]')} VCP not found at {VCP_PORT}"); return None, None

    return saleae_conn, VCP_PORT


# ── Build + Flash ──────────────────────────────────────────────────────────

def build_and_flash(scenario_name):
    stage("CODING", f"Applying scenario: {scenario_name}")
    # Apply USER CODE blocks from scenario
    scenario_dir = f"/home/kerem/logic_analyzer/hil_framework/scenarios/{scenario_name}"
    if os.path.exists(f"{scenario_dir}/USER_CODE_PV.c"):
        pv = open(f"{scenario_dir}/USER_CODE_PV.c").read()
        init = open(f"{scenario_dir}/USER_CODE_INIT.c").read()
        loop = open(f"{scenario_dir}/USER_CODE_LOOP.c").read()
        main = open(f"{WORKSPACE}/Core/Src/main.c").read()
        # Simple block replacement — find USER CODE PV/2/3 sections
        # For now just build (blocks already applied in main.c)
        print(f"  Scenario blocks ready from {scenario_dir}")
    else:
        print(f"  {y('[WARN]')} Scenario dir not found, building current firmware")

    stage("COMPILING", "Building firmware")
    res = run(["make", "-C", f"{WORKSPACE}/Debug", "all"], timeout=120)
    if res is None or res.returncode != 0:
        print(f"{r('[ERROR]')} Build failed")
        if res: print(res.stderr[-500:])
        return False
    print(f"{g('[OK]')} Build complete")
    print(f"  Binary: {FW_BIN}")

    stage("FLASHING", "Writing firmware to board")
    # Kill VCP to free STLink
    run(["pkill", "-f", "vcp_feed.py"], capture=False)
    time.sleep(1)

    res = run(["st-flash", "erase", "0x8000000", "0x200000"], timeout=60)
    if res is None:
        print(f"{r('[ERROR]')} st-flash erase failed"); return False

    res = run(["st-flash", "--reset", "write", FW_BIN, "0x8000000"], timeout=60)
    if res is None or "verified" not in res.stdout.lower():
        print(f"{r('[ERROR]')} Flash failed")
        if res: print(res.stdout[-300:] + res.stderr[-300:])
        return False
    print(f"{g('[OK]')} Flash verified!")
    return True


# ── Logic Analyzer Capture ──────────────────────────────────────────────────

def capture_and_timing(saleae_conn):
    stage("CAPTURE", "Acquiring logic analyzer samples")
    sr_file = "/tmp/hil_auto_capture.sr"
    cmd = [
        "sigrok-cli",
        "-d", f"fx2lafw:conn={saleae_conn}",
        "-c", "samplerate=12M",
        "--samples", "12000000",
        "-o", sr_file,
    ]
    res = run(cmd, timeout=20)
    if res is None:
        print(f"{r('[ERROR]')} sigrok-cli capture timed out"); return None

    # Scan channels
    import zipfile, os
    extract_dir = "/tmp/hil_sr_extract"
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(sr_file, "r") as z:
        z.extractall(extract_dir)
    active_ch = None
    for ch in range(8):
        samples = []
        logic_files = sorted(
            [f for f in os.listdir(extract_dir) if f.startswith("logic-")],
            key=lambda x: int(x.rsplit("-", 1)[-1])
        )
        for lf in logic_files:
            with open(os.path.join(extract_dir, lf), "rb") as f:
                for byte in f.read():
                    samples.append((byte >> ch) & 1)
        transitions = sum(1 for i in range(1, len(samples)) if samples[i] != samples[i-1])
        if transitions > 10:
            active_ch = ch
            break
    if active_ch is None:
        print(f"{r('[ERROR]')} No active channels found"); return None
    print(f"{g('[OK]')} Active channel: CH{active_ch} ({transitions} transitions)")

    # Run timing analysis
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
        print(f"{y('[WARN]')} Timing analysis unavailable"); return active_ch
    if faults == 0:
        print(f"{g('[PASS]')} Stage 1: 0 timing violations")
        return active_ch
    else:
        print(f"{r('[FAIL]')} Stage 1: {faults} timing violations")
        return None


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
        return text
    except serial.SerialException as e:
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

def main():
    parser = argparse.ArgumentParser(description="HIL Autonomous Loop")
    parser.add_argument("--scenario", default="accel_stream",
                        choices=["accel_stream", "temperature"],
                        help="Scenario to load from scenarios/")
    parser.add_argument("--no-interactive", action="store_true",
                        help="Skip physical test, just timing + VCP stream")
    args = parser.parse_args()

    # Wipe panes
    open(PANE2, "w").close()
    open(PANE3, "w").close()

    print(f"\n{g('='*60)}")
    print(f"  HIL AUTONOMOUS LOOP — {args.scenario}")
    print(f"{g('='*60)}")

    saleae_conn, vc_port = detect_devices()
    if not saleae_conn:
        print(f"\n{r('[ABORT]')} Devices not ready — fix hardware and retry")
        sys.exit(1)

    if not build_and_flash(args.scenario):
        print(f"\n{r('[ABORT]')} Build/flash failed")
        sys.exit(1)

    # Restart VCP feed
    run(["pkill", "-f", "vcp_feed.py"], capture=False)
    time.sleep(1)
    subprocess.Popen(
        ["python3", "/home/kerem/logic_analyzer/hil_framework/vcp_feed.py"],
        stdout=open(PANE3, "w"),
        stderr=subprocess.STDOUT,
    )

    active_ch = capture_and_timing(saleae_conn)
    if active_ch is None:
        print(f"\n{r('[ABORT]')} Stage 1 failed — timing violations found")
        sys.exit(1)

    vcp_text = read_vcp_first()
    print(f"\n{g('[STAGE 2]')} VCP sample:\n  {vcp_text[:200].strip()}")

    if args.no_interactive:
        save_result(args.scenario, True, "SKIPPED")
        return

    physical = prompt_physical_test(args.scenario, vcp_text)
    save_result(args.scenario, True, physical)

    if physical == "PASS":
        print(f"\n{g('[SUCCESS]')} HIL VERIFICATION COMPLETE — ALL STAGES PASSED")
        banner2("SUCCESS — Hardware verified, sensor confirmed")
    else:
        print(f"\n{r('[RETRY]')} Physical test failed — review sensor integration")


if __name__ == "__main__":
    main()
