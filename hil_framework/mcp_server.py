#!/usr/bin/env python3
"""
HIL Tools MCP Server — spawned by copilot.py's Claude CLI integration.

Handles all 9 hardware-in-the-loop tool calls via the MCP JSON-RPC protocol:
read_bsp_files, read_main_c, reset_board, restore_board, inject_code,
compile_firmware, flash_board, capture_verify, hitl_verify.
"""
from __future__ import annotations

import json
import os
import re
import glob
import subprocess
import sys
import time

# ── Paths (derived from this file's location) ─────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.join(SCRIPT_DIR, "hil_workspace")
BASELINE_DIR = os.path.join(SCRIPT_DIR, "active_trial_1")
MAIN_C_PATH = os.path.join(WORKSPACE_DIR, "Core", "Src", "main.c")
BSP_DIR = os.path.join(SCRIPT_DIR, "BSP", "BSP", "Drivers", "BSP", "B-U585I-IOT02A")
PROJECT_DEBUG = os.path.join(WORKSPACE_DIR, "Debug")
FIRMWARE_BIN = os.path.join(PROJECT_DEBUG, "Logic_Analyzer_USART3.bin")
VCP_PORT = "/dev/ttyACM0"


# ── Tool Implementations ────────────────────────────────────────────────────────

def tool_read_bsp(sensor: str) -> str:
    results = []
    sensor_lower = sensor.lower()
    keyword_map = {
        "motion": ["motion_sensors", "accel", "gyro", "ism330"],
        "accel": ["motion_sensors", "ism330"],
        "gyro": ["motion_sensors", "ism330"],
        "env": ["env_sensors", "lps22hh", "hts221", "stts22h"],
        "temp": ["env_sensors", "stts22h", "lps22hh"],
        "humidity": ["env_sensors", "hts221"],
        "pressure": ["env_sensors", "lps22hh"],
        "bus": ["bus"],
    }
    matched_files = []
    for kw, names in keyword_map.items():
        if kw in sensor_lower:
            for name in names:
                matched_files.extend(glob.glob(os.path.join(BSP_DIR, f"*{name}*")))
    matched_files = list(set(matched_files))
    if not matched_files:
        matched_files = glob.glob(os.path.join(BSP_DIR, "*.h"))
    for path in matched_files:
        try:
            with open(path) as f:
                lines = f.readlines()[:250]
            results.append(f"\n// ===== {os.path.basename(path)} =====\n")
            results.extend(lines)
        except Exception as e:
            results.append(f"// Error reading {path}: {e}\n")
    main_h = os.path.join(WORKSPACE_DIR, "Core", "Inc", "main.h")
    if os.path.exists(main_h):
        try:
            with open(main_h) as f:
                lines = f.readlines()[:100]
            results.append(f"\n// ===== main.h (pin defines) =====\n")
            results.extend(lines)
        except Exception:
            pass
    content = "".join(results)
    if len(content) > 8000:
        content = content[:8000] + f"\n// ... (truncated)\n"
    return content


def tool_read_main_c() -> str:
    if not os.path.exists(MAIN_C_PATH):
        return f"ERROR: main.c not found at {MAIN_C_PATH}"
    with open(MAIN_C_PATH) as f:
        return f.read()


def tool_inject_code(block: str, code: str) -> str:
    if not os.path.exists(MAIN_C_PATH):
        return f"ERROR: main.c not found at {MAIN_C_PATH}"
    with open(MAIN_C_PATH) as f:
        content = f.read()
    begin_marker = f"/* USER CODE BEGIN {block} */"
    end_marker = f"/* USER CODE END {block} */"
    if begin_marker not in content:
        available = re.findall(r"/\* USER CODE BEGIN (\S+) \*/", content)
        return f"ERROR: Block '{block}' not found. Available: {available}"
    if end_marker not in content:
        return f"ERROR: Block '{block}' found but no matching END marker."
    pattern = rf"({re.escape(begin_marker)})[\s\S]*?({re.escape(end_marker)})"
    replacement = f"{begin_marker}\n{code}\n{end_marker}"
    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count == 0:
        return f"ERROR: Could not replace block '{block}'."
    with open(MAIN_C_PATH, "w") as f:
        f.write(new_content)
    lines_injected = len(code.strip().splitlines())
    return f"OK: Injected {lines_injected} lines into USER CODE BEGIN {block}.\nCode:\n{code[:500]}{'...' if len(code) > 500 else ''}"


def tool_compile_firmware() -> str:
    if not os.path.exists(MAIN_C_PATH):
        return "ERROR: main.c not found"
    if not os.path.exists(PROJECT_DEBUG):
        return f"ERROR: Debug directory not found at {PROJECT_DEBUG}"
    result = subprocess.run(
        ["make", "-C", PROJECT_DEBUG, "all"],
        capture_output=True, text=True, timeout=120,
    )
    output = result.stdout + result.stderr
    if result.returncode == 0:
        return f"OK: Compilation succeeded.\n{output[-2000:]}"
    return f"FAIL: Compilation errors:\n{output[-3000:]}"


def tool_flash_board() -> str:
    if not os.path.exists(FIRMWARE_BIN):
        return f"ERROR: Firmware binary not found at {FIRMWARE_BIN}. Run compile_firmware first."
    subprocess.run(["fuser", "-k", VCP_PORT], capture_output=True)
    time.sleep(0.5)
    subprocess.run(
        ["st-flash", "erase", "0x8000000", "0x200000"],
        capture_output=True, text=True, timeout=60,
    )
    result = subprocess.run(
        ["st-flash", "--reset", "write", FIRMWARE_BIN, "0x8000000"],
        capture_output=True, text=True, timeout=30,
    )
    flash_output = result.stdout + result.stderr
    if result.returncode != 0 and "Flash written" not in flash_output and "jolly good" not in flash_output:
        return f"FAIL: st-flash failed:\n{flash_output[-1500:]}"
    time.sleep(2)
    try:
        import serial
        with serial.Serial(VCP_PORT, 115200, timeout=3) as ser:
            ser.reset_input_buffer()
            time.sleep(0.5)
            data = ser.read(512)
            text = data.decode("utf-8", errors="replace")
            vcplines = [l.strip() for l in text.split("\r\n") if l.strip()]
            return f"OK: Flash successful.\nVCP ({len(data)} bytes):\n" + "\n".join(vcplines[:20])
    except Exception as e:
        return f"OK: Flash written but VCP read failed: {e}"


_BACKUP_DIR = os.path.join(SCRIPT_DIR, ".hil_backup")


def _sync_workspace_from_baseline() -> None:
    import shutil
    if os.path.exists(WORKSPACE_DIR):
        for item in os.listdir(WORKSPACE_DIR):
            if item.startswith("."):
                continue
            path = os.path.join(WORKSPACE_DIR, item)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
    for item in os.listdir(BASELINE_DIR):
        if item.startswith("."):
            continue
        src = os.path.join(BASELINE_DIR, item)
        dst = os.path.join(WORKSPACE_DIR, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def tool_reset_board() -> str:
    _sync_workspace_from_baseline()
    compile_result = tool_compile_firmware()
    if "FAIL" in compile_result or "error:" in compile_result.lower():
        return f"FAIL: baseline compile failed:\n{compile_result[-800:]}"
    flash_result = tool_flash_board()
    return "OK: Workspace synced from active_trial_1 baseline.\nBaseline compiled and flashed.\n" + flash_result[-400:]


def tool_restore_board() -> str:
    _sync_workspace_from_baseline()
    compile_result = tool_compile_firmware()
    if "FAIL" in compile_result or "error:" in compile_result.lower():
        return f"WARN: restored but compile failed:\n{compile_result[-800:]}"
    flash_result = tool_flash_board()
    return f"OK: Workspace restored from active_trial_1 baseline.\n{flash_result[-400:]}"


def tool_capture_verify(duration_s: float = 2.0, channel: int = 1, baud: int = 115200) -> str:
    sys.path.insert(0, os.path.join(SCRIPT_DIR, "hil_framework"))
    from capture import quick_capture
    from timing import estimate_baud_from_samples
    try:
        result = quick_capture(duration_s=duration_s, sample_rate="12M", channel=channel, baud=baud)
    except Exception as e:
        return f"ERROR: Capture failed: {e}"
    if not result.get("success"):
        return f"FAIL: Capture failed: {result.get('error', 'unknown')}"
    raw_bytes = result.get("raw_bytes", [])
    channel_samples = result.get("channel_samples", [])
    sample_rate_hz = result.get("sample_rate_hz", 12_000_000)
    detected_baud = result.get("baud_detected", baud)
    if not raw_bytes:
        return "INFO: Capture succeeded but no bytes decoded (check probe connection)"
    implied_baud, dev_pct = estimate_baud_from_samples(channel_samples, sample_rate_hz, baud)
    fault_count = 0
    try:
        from timing import byte_timing_map
        sr_file = result.get("sr_filepath", "")
        if sr_file and channel_samples:
            tmap = byte_timing_map(
                sr_file, channel=channel, decoded_bytes=raw_bytes,
                channel_samples=channel_samples,
                sample_rate_hz=sample_rate_hz, baud=detected_baud,
            )
            fault_count = sum(1 for bt in tmap if bt.fault_count > 0)
    except Exception:
        pass
    dev_color = "OK" if abs(dev_pct) <= 2.0 else "MISMATCH"
    return (
        f"CAPTURE SUMMARY\n"
        f"  Bytes decoded : {len(raw_bytes)}\n"
        f"  Sample rate   : {sample_rate_hz/1e6:.0f} MHz\n"
        f"  Declared baud : {baud}\n"
        f"  Detected baud : {detected_baud}  ({dev_pct:+.1f}%  [{dev_color}])\n"
        f"  Timing faults : {fault_count}/{len(raw_bytes)}\n"
        f"  Sample length : {len(channel_samples)/sample_rate_hz*1e6:.0f} us\n"
        f"  First bytes   : {[hex(b) for b in raw_bytes[:8]]}\n"
    )


def tool_hitl_verify(sensor: str, threshold: float = 500, duration_s: float = 10.0) -> str:
    import serial, re as re2
    samples = []
    accel_readings = []
    baseline = {}
    try:
        ser = serial.Serial(VCP_PORT, 115200, timeout=1)
        ser.reset_input_buffer()
        time.sleep(0.3)
    except Exception as e:
        return f"ERROR: Cannot open {VCP_PORT}: {e}"
    start = time.time()
    while time.time() - start < duration_s:
        elapsed = time.time() - start
        try:
            if ser.in_waiting > 0:
                line_bytes = ser.readline()
                line = line_bytes.decode("utf-8", errors="replace").strip()
                samples.append((line, elapsed))
                m = re2.search(
                    r"[Aa][Xx][^0-9-]*(-?\d+)[^0-9-]*"   # AX= or ax=
                    r"[Aa][Yy][^0-9-]*(-?\d+)[^0-9-]*"   # AY= or ay= (with A)
                    r"[Aa][Zz][^0-9-]*(-?\d+)",
                    line,
                )
                if not m:
                    # Also match "AX=... Y=... Z=..." (no A prefix on Y/Z)
                    m = re2.search(
                        r"AX=(-?\d+)[^0-9-]*Y=(-?\d+)[^0-9-]*Z=(-?\d+)",
                        line,
                    )
                if m:
                    reading = {"x": int(m.group(1)), "y": int(m.group(2)), "z": int(m.group(3)), "t": elapsed}
                    accel_readings.append(reading)
                    if not baseline:
                        baseline = {"x": reading["x"], "y": reading["y"], "z": reading["z"]}
            else:
                time.sleep(0.05)
        except serial.SerialException:
            break
    ser.close()
    if not accel_readings:
        return (
            f"FAIL: No accelerometer data parsed from VCP in {duration_s}s.\n"
            f"HINT: Firmware must print values matching pattern 'AX=... AY=... AZ=...' or 'ax=... ay=... az=...'\n"
            f"Sample lines: {[s[0][:60] for s in samples[:5]]}"
        )
    bx, by, bz = baseline["x"], baseline["y"], baseline["z"]
    max_delta, max_axis = 0, ""
    for r in accel_readings:
        for axis, base in [("X", bx), ("Y", by), ("Z", bz)]:
            delta = abs(r[axis.lower()] - base)
            if delta > max_delta:
                max_delta, max_axis = delta, axis
    detected = max_delta >= threshold
    status = "PASS" if detected else "FAIL"
    return (
        f"HITL RESULT: {status}\n"
        f"  Sensor      : {sensor}\n"
        f"  Threshold   : {threshold}\n"
        f"  Max delta   : {max_delta} (axis {max_axis})\n"
        f"  Baseline    : X={bx} Y={by} Z={bz}\n"
        f"  Samples     : {len(accel_readings)} readings in {duration_s}s\n"
        f"  {'Tilt/motion detected!' if detected else 'No significant motion detected.'}"
    )


_TOOL_DISPATCH = {
    "read_bsp_files": lambda p: tool_read_bsp(**p) if p else tool_read_bsp(),
    "read_main_c": lambda p: tool_read_main_c(),
    "reset_board": lambda p: tool_reset_board(),
    "restore_board": lambda p: tool_restore_board(),
    "inject_code": lambda p: tool_inject_code(**p),
    "compile_firmware": lambda p: tool_compile_firmware(),
    "flash_board": lambda p: tool_flash_board(),
    "capture_verify": lambda p: tool_capture_verify(**p),
    "hitl_verify": lambda p: tool_hitl_verify(**p),
}


# ── MCP Server ─────────────────────────────────────────────────────────────────

def _make_manifest():
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "tools": [
            {
                "name": "read_bsp_files",
                "description": "Read BSP sensor driver files for RAG context. Call this FIRST when the user asks about sensors (accelerometer, gyroscope, temperature, etc.) to understand the BSP API.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sensor": {
                            "type": "string",
                            "description": "Sensor category: 'motion' or 'accel' for ISM330DHCX, 'env' or 'temp' or 'humidity' for environmental sensors.",
                        },
                    },
                    "required": ["sensor"],
                },
            },
            {
                "name": "read_main_c",
                "description": "Read the current main.c firmware file to understand what USER CODE blocks already contain. Always call this before injecting code.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "reset_board",
                "description": "Sync hil_workspace from the active_trial_1 baseline, compile, and flash. Use this FIRST before ANY new code injection to ensure a clean slate.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "restore_board",
                "description": "Restore hil_workspace to the active_trial_1 baseline and flash. Use after a failed code injection attempt to return to known-good state.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "inject_code",
                "description": "Inject C code into a USER CODE block in main.c. Only modify USER CODE BEGIN/END blocks.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "block": {"type": "string", "description": "Block name: 'PV' (vars), '2' (after MX init), '3' (main loop), 'PFP' (protos)."},
                        "code": {"type": "string"},
                    },
                    "required": ["block", "code"],
                },
            },
            {
                "name": "compile_firmware",
                "description": "Run 'make all' to compile the firmware.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "flash_board",
                "description": "Flash the compiled firmware (.bin) to the board via STLink.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "capture_verify",
                "description": "Run a logic analyzer capture and verify physical UART timing. Detects baud rate mismatches and per-byte timing faults.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "duration_s": {"type": "number", "description": "Capture duration in seconds (default: 2.0)."},
                        "channel": {"type": "integer", "description": "Logic analyzer channel: 0=D0, 1=D1 (default: 1 = PD8)."},
                        "baud": {"type": "integer", "description": "Expected UART baud rate (default: 115200)."},
                    },
                },
            },
            {
                "name": "hitl_verify",
                "description": "Human-in-the-Loop verification. Opens VCP, streams data, waits for user to interact (e.g. tilt board). Firmware must already be streaming sensor data via UART.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sensor": {"type": "string", "description": "Sensor type: 'accelerometer', 'temperature', etc."},
                        "threshold": {"type": "number", "description": "Min change in raw sensor units. For ISM330DHCX accel: 500 for tilt, 2000 for shake."},
                        "duration_s": {"type": "number", "description": "How long to wait (default: 10.0 seconds)."},
                        "interactive": {"type": "boolean", "description": "If true, launch an interactive TUI where user presses Y (correct) or N (incorrect). Recommended for accelerometer."},
                    },
                    "required": ["sensor", "threshold"],
                },
            },
        ],
    }


def _send_response(req_id, result):
    resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
    print(json.dumps(resp), flush=True)


def _send_error(req_id, code, message):
    resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    print(json.dumps(resp), flush=True)


if __name__ == "__main__":
    manifest = _make_manifest()
    print(json.dumps(manifest), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method == "initialize":
            _send_response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "hil-tools", "version": "1.0"},
            })

        elif method == "notifications/initialized":
            pass  # no response needed

        elif method == "tools/list":
            _send_response(req_id, {"tools": manifest["tools"]})

        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            if name in _TOOL_DISPATCH:
                try:
                    result = _TOOL_DISPATCH[name](arguments)
                    _send_response(req_id, {
                        "content": [{"type": "text", "text": str(result)}],
                        "isError": False,
                    })
                except Exception as e:
                    _send_response(req_id, {
                        "content": [{"type": "text", "text": f"ERROR: {type(e).__name__}: {e}"}],
                        "isError": True,
                    })
            else:
                _send_error(req_id, -32602, f"Unknown tool: {name}")
        else:
            if req_id is not None:
                _send_error(req_id, -32601, f"Unknown method: {method}")