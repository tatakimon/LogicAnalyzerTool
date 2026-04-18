#!/usr/bin/env python3
"""
Autonomous Hardware Co-Pilot — Textual War Room

AI agent that reads BSP, writes C, compiles, flashes, and verifies
physical hardware autonomously via a 3-panel async TUI.

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python3 hil_framework/copilot.py
    python3 hil_framework/copilot.py              # reads ANTHROPIC_API_KEY from env

Requires:
    pip3 install --break-system-packages anthropic textual
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

# ── Third-party ────────────────────────────────────────────────────
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header, Input, Log, Static

try:
    from anthropic import AsyncAnthropic
    from anthropic.types import Message, TextBlock, ToolUseBlock
except ImportError:
    sys.stderr.write("[ERROR] anthropic not installed: pip3 install --break-system-packages anthropic\n")
    sys.exit(1)

# ── HIL Framework ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from capture import quick_capture
from hardware import BoardHardware
from timing import estimate_baud_from_samples

# ─────────────────────────────────────────────────────────────────────────────
# Project paths
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_DIR = os.path.join(SCRIPT_DIR, "active_trial_1")      # preserved, never touched
WORKSPACE_DIR = os.path.join(SCRIPT_DIR, "hil_workspace")     # copilot's working copy
MAIN_C_PATH = os.path.join(WORKSPACE_DIR, "Core", "Src", "main.c")
BSP_DIR = os.path.join(SCRIPT_DIR, "BSP", "BSP", "Drivers", "BSP", "B-U585I-IOT02A")
PROJECT_DEBUG = os.path.join(WORKSPACE_DIR, "Debug")
FIRMWARE_BIN = os.path.join(PROJECT_DEBUG, "Logic_Analyzer_USART3.bin")
VCP_PORT = "/dev/ttyACM0"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
if not ANTHROPIC_API_KEY:
    sys.stderr.write(
        "[ERROR] ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN not set in environment.\n"
        "Hint: Claude Code sets ANTHROPIC_AUTH_TOKEN automatically.\n"
    )
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Tool Functions
# ─────────────────────────────────────────────────────────────────────────────

def tool_read_bsp(sensor: str) -> str:
    """Read BSP sensor driver files for RAG context."""
    results = []
    sensor_lower = sensor.lower()

    # Map sensor keywords to files
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

    # Also include main.h for pin/clock context (from workspace, not baseline)
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
    # Truncate if very long
    if len(content) > 8000:
        content = content[:8000] + f"\n// ... (truncated, {len(content)-8000} chars omitted)\n"
    return content


def tool_read_main_c() -> str:
    """Read the current main.c firmware file."""
    if not os.path.exists(MAIN_C_PATH):
        return f"ERROR: main.c not found at {MAIN_C_PATH}"
    with open(MAIN_C_PATH) as f:
        return f.read()


def tool_inject_code(block: str, code: str) -> str:
    """Inject C code into a USER CODE block in main.c."""
    if not os.path.exists(MAIN_C_PATH):
        return f"ERROR: main.c not found at {MAIN_C_PATH}"

    with open(MAIN_C_PATH) as f:
        content = f.read()

    # Normalize block name: '3' -> '3', 'PV' -> 'PV', etc.
    begin_marker = f"/* USER CODE BEGIN {block} */"
    end_marker = f"/* USER CODE END {block} */"

    if begin_marker not in content:
        available = re.findall(r"/\* USER CODE BEGIN (\S+) \*/", content)
        return f"ERROR: Block '{block}' not found. Available blocks: {available}"

    if end_marker not in content:
        return f"ERROR: Block '{block}' found but no matching END marker."

    # Replace content between BEGIN and END (including newlines after BEGIN, before END)
    pattern = rf"({re.escape(begin_marker)})[\s\S]*?({re.escape(end_marker)})"
    replacement = f"{begin_marker}\n{code}\n{end_marker}"

    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count == 0:
        return f"ERROR: Could not replace block '{block}'."

    with open(MAIN_C_PATH, "w") as f:
        f.write(new_content)

    lines_injected = len(code.strip().splitlines())
    return (
        f"OK: Injected {lines_injected} lines into USER CODE BEGIN {block}.\n"
        f"Code:\n{code[:500]}{'...' if len(code) > 500 else ''}"
    )


def tool_compile_firmware() -> str:
    """Run make to build the firmware."""
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
    else:
        return f"FAIL: Compilation errors:\n{output[-3000:]}"


def tool_flash_board() -> str:
    """Flash firmware to the board and verify VCP output."""
    if not os.path.exists(FIRMWARE_BIN):
        return f"ERROR: Firmware binary not found at {FIRMWARE_BIN}. Run compile_firmware first."

    # Kill any process using the port
    os.system(f"fuser -k {VCP_PORT} 2>/dev/null")
    time.sleep(0.5)

    # Erase flash before writing to avoid race condition on U5
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

    # Wait for board to boot
    time.sleep(2)

    # Read VCP
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


# ── Workspace Sync ─────────────────────────────────────────────────────
_BACKUP_DIR = os.path.join(SCRIPT_DIR, ".hil_backup")

def _sync_workspace_from_baseline() -> None:
    """Copy active_trial_1 → hil_workspace, overwriting workspace with fresh baseline."""
    import shutil

    # Remove workspace contents (except .gitkeep or hidden dirs)
    if os.path.exists(WORKSPACE_DIR):
        for item in os.listdir(WORKSPACE_DIR):
            if item.startswith('.'):
                continue
            path = os.path.join(WORKSPACE_DIR, item)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)

    # Copy from baseline
    for item in os.listdir(BASELINE_DIR):
        if item.startswith('.'):
            continue
        src = os.path.join(BASELINE_DIR, item)
        dst = os.path.join(WORKSPACE_DIR, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _backup_user_blocks() -> dict[str, str]:
    """Snapshot every USER CODE block in main.c before modifying."""
    content = tool_read_main_c()
    if content.startswith("ERROR"):
        return {}
    blocks: dict[str, str] = {}
    for m in re.finditer(r"/\* USER CODE BEGIN (\S+) \*/([\s\S]*?)/\* USER CODE END \1 \*/", content):
        blocks[m.group(1)] = m.group(2).rstrip()
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    import json
    backup_path = os.path.join(_BACKUP_DIR, "workspace_blocks.json")
    with open(backup_path, "w") as f:
        json.dump(blocks, f, indent=2)
    return blocks


def tool_reset_board() -> str:
    """
    Reset hil_workspace to a clean ruled whiteboard synced from active_trial_1.
    Copies the baseline fresh, compiles, and flashes.
    Use this FIRST before injecting any new code — ensures a clean slate.
    After this, inject_code() can write freely without affecting the baseline.
    """
    _sync_workspace_from_baseline()
    compile_result = tool_compile_firmware()
    if "FAIL" in compile_result or "error:" in compile_result.lower():
        return f"FAIL: baseline compile failed:\n{compile_result[-800:]}"
    flash_result = tool_flash_board()
    return (
        "OK: Workspace synced from active_trial_1 baseline.\n"
        "Baseline compiled and flashed.\n"
        f"{flash_result[-400:]}"
    )


def tool_restore_board() -> str:
    """
    Restore hil_workspace to the baseline from active_trial_1.
    Use this after a failed code injection attempt to return to known-good state.
    Discards all workspace changes since last reset_board().
    """
    _sync_workspace_from_baseline()
    compile_result = tool_compile_firmware()
    if "FAIL" in compile_result or "error:" in compile_result.lower():
        return f"WARN: restored but compile failed:\n{compile_result[-800:]}"
    flash_result = tool_flash_board()
    return f"OK: Workspace restored from active_trial_1 baseline.\n{flash_result[-400:]}"


def tool_capture_verify(duration_s: float = 2.0, channel: int = 1, baud: int = 115200) -> str:
    """Capture from logic analyzer and verify UART timing."""
    try:
        result = quick_capture(
            duration_s=duration_s,
            sample_rate="12M",
            channel=channel,
            baud=baud,
        )
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

    # Baud auto-detected inside quick_capture from signal transitions
    implied_baud, dev_pct = estimate_baud_from_samples(
        channel_samples, sample_rate_hz, baud,
    )

    fault_count = 0
    try:
        from timing import byte_timing_map
        sr_file = result.get("sr_filepath", "")
        if sr_file and channel_samples:
            tmap = byte_timing_map(sr_file, channel=channel, decoded_bytes=raw_bytes,
                                   channel_samples=channel_samples,
                                   sample_rate_hz=sample_rate_hz, baud=detected_baud)
            fault_count = sum(1 for bt in tmap if bt.fault_count > 0)
    except Exception:
        pass

    dev_color = "OK" if abs(dev_pct) <= 2.0 else "MISMATCH"
    summary = (
        f"CAPTURE SUMMARY\n"
        f"  Bytes decoded : {len(raw_bytes)}\n"
        f"  Sample rate   : {sample_rate_hz/1e6:.0f} MHz\n"
        f"  Declared baud : {baud}\n"
        f"  Detected baud : {detected_baud}  ({dev_pct:+.1f}%  [{dev_color}])\n"
        f"  Timing faults : {fault_count}/{len(raw_bytes)}\n"
        f"  Sample length : {len(channel_samples)/sample_rate_hz*1e6:.0f} µs\n"
        f"  First bytes   : {[hex(b) for b in raw_bytes[:8]]}\n"
    )
    return summary


def tool_hitl_verify(sensor: str, threshold: float = 500, duration_s: float = 10.0,
                     vcp_callback=None, whoiam: bool = False,
                     interactive: bool = False) -> str:
    """
    Human-in-the-Loop verification: stream VCP, detect motion/tilt, or WHO_I_AM smoke test.

    Args:
        sensor: Sensor type ('accelerometer', 'temperature', etc.)
        threshold: Min change in raw sensor units to count as detection.
                   For ISM330DHCX accel: try 500 for tilt, 2000 for shake.
        duration_s: How long to wait for interaction.
        vcp_callback: Optional callback(line) to stream each VCP line to the UI.
        whoiam: If True, run ISM330DHCX WHO_I_AM smoke test via VCP instead of tilt.
                Firmware must print 'WHOIAM=0x6B' or 'WHO_I_AM: 0x6B' (ISM330DHCX expected = 0x6B).
        interactive: If True, launch the interactive Textual verifier. The user sees
                     live data, then presses Y (looks correct) or N (looks wrong).
                     Result is written to /tmp/hil_interactive_verify_result.txt.
    """
    import serial

    # ── Interactive mode ─────────────────────────────────────────────────
    if interactive:
        result_file = "/tmp/hil_interactive_verify_result.txt"
        try:
            os.remove(result_file)
        except FileNotFoundError:
            pass

        interactive_script = os.path.join(os.path.dirname(__file__), "interactive_verify.py")
        launch_cmd = [sys.executable, interactive_script,
                       "--port", VCP_PORT, "--baud", "115200",
                       "--duration", str(duration_s)]
        proc = subprocess.run(
            launch_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Wait up to 120s for user to confirm (they may be reading the screen)
        for _ in range(120):
            time.sleep(1)
            if os.path.exists(result_file):
                with open(result_file) as f:
                    result = f.read().strip()
                if result == "PASS":
                    return (
                        "INTERACTIVE VERIFY: PASS\n"
                        "  User confirmed accelerometer data looks correct.\n"
                    )
                else:
                    return (
                        "INTERACTIVE VERIFY: FAIL\n"
                        "  User reported data is incorrect.\n"
                    )
        return (
            "INTERACTIVE VERIFY: TIMEOUT\n"
            "  User did not respond within 120s.\n"
        )

    samples: list[tuple[str, float]] = []  # (line, timestamp)
    accel_readings: list[dict] = []
    baseline = {}

    # Open serial port
    try:
        ser = serial.Serial(VCP_PORT, 115200, timeout=1)
        ser.reset_input_buffer()
        time.sleep(0.3)
    except Exception as e:
        return f"ERROR: Cannot open {VCP_PORT}: {e}"

    start = time.time()
    last_flush = start

    while time.time() - start < duration_s:
        elapsed = time.time() - start
        try:
            if ser.in_waiting > 0:
                line_bytes = ser.readline()
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line and vcp_callback:
                    vcp_callback(line)
                samples.append((line, elapsed))

                # Try to parse accelerometer values
                # Patterns: "ACCEL X=123 Y=456 Z=789" or "ax=123 ay=456 az=789"
                m = re.search(r"[Aa][Xx][^0-9-]*(-?\d+)[^0-9-]*[Aa][Yy][^0-9-]*(-?\d+)[^0-9-]*[Aa][Zz][^0-9-]*(-?\d+)", line)
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

    # Compute deltas from baseline
    bx, by, bz = baseline["x"], baseline["y"], baseline["z"]
    max_delta = 0
    max_axis = ""
    for r in accel_readings:
        for axis, base in [("X", bx), ("Y", by), ("Z", bz)]:
            delta = abs(r[axis.lower()] - base)
            if delta > max_delta:
                max_delta = delta
                max_axis = axis

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


# ─────────────────────────────────────────────────────────────────────────────
# Unified Tool Dispatcher (shared by API agent and CLI agent)
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_tool(tool_name: str, tool_input: dict, app=None) -> str:
    """
    Synchronous tool dispatch — single source of truth for all 9 tools.
    Used by both run_agent() (API) and run_agent_claude_cli().
    Pass `app` to get hardware-state panel updates for stateful tools.
    """
    # Strip MCP server prefix (e.g. "mcp__hil_tools__read_main_c" → "read_main_c")
    if tool_name.startswith("mcp__"):
        tool_name = tool_name.split("__", 2)[-1]

    try:
        if tool_name == "read_bsp_files":
            return tool_read_bsp(**tool_input)
        elif tool_name == "read_main_c":
            return tool_read_main_c()
        elif tool_name == "reset_board":
            result = tool_reset_board()
            if app:
                app.update_hw_state(f"[reset_board] {result[:300]}")
            return result
        elif tool_name == "restore_board":
            result = tool_restore_board()
            if app:
                app.update_hw_state(f"[restore_board] {result[:300]}")
            return result
        elif tool_name == "inject_code":
            return tool_inject_code(**tool_input)
        elif tool_name == "compile_firmware":
            return tool_compile_firmware()
        elif tool_name == "flash_board":
            return tool_flash_board()
        elif tool_name == "capture_verify":
            result = tool_capture_verify(**tool_input)
            if app:
                app.update_hw_state(f"[capture_verify] {result[:300]}")
            return result
        elif tool_name == "hitl_verify":
            def vcp_stream(line: str) -> None:
                if app:
                    app.post_vcp(line)
            return tool_hitl_verify(vcp_callback=vcp_stream, **tool_input)
        else:
            return f"ERROR: Unknown tool '{tool_name}'"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Agent Loop (Anthropic API)
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "read_bsp_files",
        "description": (
            "Read BSP sensor driver files for RAG context. Call this FIRST when the user "
            "asks about sensors (accelerometer, gyroscope, temperature, etc.) to understand "
            "the BSP API. Returns C header content showing available functions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor": {
                    "type": "string",
                    "description": (
                        "Sensor category: 'motion' or 'accel' or 'gyro' for ISM330DHCX, "
                        "'env' or 'temp' or 'humidity' or 'pressure' for environmental sensors."
                    ),
                },
            },
            "required": ["sensor"],
        },
    },
    {
        "name": "read_main_c",
        "description": (
            "Read the current main.c firmware file to understand what USER CODE blocks "
            "already contain. Always call this before injecting code."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reset_board",
        "description": (
            "Sync hil_workspace from the active_trial_1 baseline, compile, and flash.\n"
            "Use this FIRST before ANY new code injection to ensure a clean slate.\n"
            "active_trial_1 is NEVER modified — hil_workspace is always reset from it.\n"
            "Returns: compilation + flash result."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "restore_board",
        "description": (
            "Restore hil_workspace to the active_trial_1 baseline and flash.\n"
            "Use after a failed code injection attempt to return to known-good state.\n"
            "Discarts all workspace changes since the last reset_board() call."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "inject_code",
        "description": (
            "Inject C code into a USER CODE block in main.c. Use this after reading main.c "
            "to add new sensor initialization or data transmission. "
            "IMPORTANT: Only modify USER CODE BEGIN/END blocks — never touch HAL initialization."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {
                    "type": "string",
                    "description": (
                        "Block name as it appears in main.c, e.g. 'PV' (private variables), "
                        "'2' (after MX init), '3' (main loop), 'PFP' (function prototypes)."
                    ),
                },
                "code": {
                    "type": "string",
                    "description": (
                        "Complete C code to inject into the block (replaces existing block content). "
                        "Include full variable declarations or full loop body as needed."
                    ),
                },
            },
            "required": ["block", "code"],
        },
    },
    {
        "name": "compile_firmware",
        "description": (
            "Run 'make all' to compile the firmware. Call this after inject_code to verify "
            "the C code compiles without errors. Returns stdout/stderr."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "flash_board",
        "description": (
            "Flash the compiled firmware (.bin) to the board via STLink. Waits 2s for boot "
            "then reads VCP output. Returns flash status and first few lines of VCP output."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "capture_verify",
        "description": (
            "Run a logic analyzer capture and verify physical UART timing. Detects baud rate "
            "mismatches and per-byte timing faults. Use after flash_board to verify wire timing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_s": {
                    "type": "number",
                    "description": "Capture duration in seconds (default: 2.0).",
                    "default": 2.0,
                },
                "channel": {
                    "type": "integer",
                    "description": "Logic analyzer channel: 0=D0, 1=D1 (default: 1 = PD8 on B-U585I).",
                    "default": 1,
                },
                "baud": {
                    "type": "integer",
                    "description": "Expected UART baud rate (default: 115200).",
                    "default": 115200,
                },
            },
        },
    },
    {
        "name": "hitl_verify",
        "description": (
            "Human-in-the-Loop verification. Opens VCP, streams data, waits for user to "
            "interact (e.g. tilt board), then checks if sensor values changed by > threshold. "
            "IMPORTANT: Firmware must already be streaming sensor data via UART before calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sensor": {
                    "type": "string",
                    "description": "Sensor type being verified: 'accelerometer', 'temperature', etc.",
                },
                "threshold": {
                    "type": "number",
                    "description": (
                        "Minimum change in raw sensor units to count as a detection. "
                        "For ISM330DHCX accelerometer: try 500 for tilt, 2000 for shake. "
                        "For temperature: try 50-200 for breath proximity."
                    ),
                },
                "duration_s": {
                    "type": "number",
                    "description": "How long to wait for interaction (default: 10.0 seconds).",
                    "default": 10.0,
                },
                "interactive": {
                    "type": "boolean",
                    "description": (
                        "If true, launch an interactive Textual app that streams live data "
                        "and asks the user to press Y (correct) or N (incorrect). "
                        "Recommended for accelerometer verification."
                    ),
                    "default": False,
                },
            },
            "required": ["sensor", "threshold"],
        },
    },
]


@dataclass
class ToolResult:
    tool_id: str
    content: str


async def run_agent(prompt: str, app: HardwareCoPilotApp) -> None:
    """Async agent loop with multi-turn tool calling."""
    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY, base_url=ANTHROPIC_BASE_URL)

    system_prompt = (
        "You are an Autonomous Hardware Co-Pilot for the B-U585I-IOT02A IoT Discovery board.\n"
        "You accept ANY natural-language request about the hardware and autonomously fulfill it.\n"
        "Your goal: read sensors, toggle GPIOs, verify timing — whatever the user asks for.\n\n"

        "HARDWARE OVERVIEW:\n"
        "- Board: B-U585I-IOT02A (STM32U585AI IoT Discovery)\n"
        "- Logic analyzer UART: USART3 (PD8=TX, PD9=RX) @ 115200 8N1\n"
        "- VCP UART: USART1 (PA9=TX, PA10=RX) — for debug output\n"
        "- Saleae Logic: CH1 → PD8 (USART3 TX pin)\n"
        "- I2C2: pre-initialized by CubeMX — use hi2c2 for sensor I2C\n"
        "- On-board sensors (I2C2):\n"
        "  ISM330DHCX (instance 0): accelerometer + gyroscope\n"
        "  STTS22H (instance 0): temperature\n"
        "  HTS221 (instance 0): humidity\n"
        "  LPS22HH (instance 0): pressure\n"
        "  VL53L5CX (instance 1): ranging\n\n"

        "WORKSPACE RULES (CRITICAL):\n"
        "- Code changes go ONLY in hil_workspace/Core/Src/main.c USER CODE BEGIN/END blocks\n"
        "- active_trial_1/ is the PERMANENT BASELINE — NEVER modified\n"
        "- hil_workspace/ is the copilot's working copy — safe to modify\n"
        "- On failure: call restore_board() to revert to baseline\n\n"

        "GENERIC HIL LOOP — follow this order for EVERY request:\n\n"
        "STEP 1 — SIGNAL QUALITY CHECK (always first)\n"
        "Call: capture_verify(duration_s=2, channel=1, baud=115200)\n"
        "If: no bytes decoded → probe disconnected or board silent. Report to user and stop.\n"
        "If: baud mismatch → hardware timing issue. Report and stop.\n"
        "If: OK → proceed.\n\n"
        "STEP 2 — ANALYZE REQUEST\n"
        "Understand what the user wants:\n"
        "- Sensor data over UART → read_bsp_files + inject_code\n"
        "- GPIO toggle / PWM → read main.h for pin defines + inject_code\n"
        "- UART verify / timing check → capture_verify only\n"
        "- Communication test → inject_code to print test pattern\n"
        "- WHO_I_AM / smoke test → inject_code for I2C register read + UART print\n"
        "- Actuator (LED, motor) → read main.h for pin defines + inject_code\n\n"
        "STEP 3 — READ CONTEXT (as needed)\n"
        "Call: read_bsp_files(sensor) to get BSP API for the relevant sensor.\n"
        "Call: read_main_c() to see current workspace state.\n\n"
        "STEP 4 — INJECT CODE\n"
        "Map the request to the right USER CODE blocks:\n"
        "  USER CODE BEGIN PV    → variable declarations\n"
        "  USER CODE BEGIN 2     → init code (runs after MX_I2C2_Init)\n"
        "  USER CODE BEGIN 3     → main loop body (runs each iteration)\n"
        "  USER CODE BEGIN PFP   → function prototypes (if needed)\n"
        "Use: inject_code(block='PV', code='...') for each block.\n"
        "Print sensor/result data as UART text so capture_verify and VCP can read it.\n\n"
        "STEP 5 — BUILD\n"
        "Call: compile_firmware() — if it fails, read errors, fix inject_code, retry.\n\n"
        "STEP 6 — DEPLOY\n"
        "Call: flash_board() — wait 2s for boot, reads first VCP lines.\n\n"
        "STEP 7 — VERIFY\n"
        "Call: capture_verify(duration_s=2, channel=1, baud=115200)\n"
        "  → Confirms logic analyzer sees correct UART signal.\n"
        "For SENSOR REQUESTS (accelerometer, temperature, etc.):\n"
        "  ALWAYS call hitl_verify(sensor=..., threshold=..., interactive=True)\n"
        "  interactive=True launches a Textual app that streams live data and\n"
        "  asks the user to press Y (looks correct) or N (data is wrong).\n"
        "  The agent waits for the user's answer before continuing.\n\n"
        "STEP 8 — ON FAILURE\n"
        "If compilation fails: fix the inject_code calls based on error output.\n"
        "If flash fails: check that binary exists, retry.\n"
        "If capture shows wrong data: check UART pin wiring (PD8), fix inject_code.\n"
        "If hardware fails completely: call restore_board() to reset workspace to baseline.\n\n"

        "BSP QUICK REFERENCE (use read_bsp_files for full API):\n"
        "- ISM330DHCX accel: BSP_MOTION_SENSOR_Init(0, MOTION_ACCELERO)\n"
        "  BSP_MOTION_SENSOR_GetAxes(0, MOTION_ACCELERO, &accel)\n"
        "  Print as: 'ACCEL X=%d Y=%d Z=%d\\r\\n'\n"
        "- STTS22H temp: BSP_ENV_SENSOR_Init(0, ENV_TEMPERATURE)\n"
        "  BSP_ENV_SENSOR_GetValue(0, ENV_TEMPERATURE, &val)\n"
        "  Print as: 'TEMP=%.2f\\r\\n'\n"
        "- WHO_I_AM smoke test: read WHO_AM_I register via BSP, print result.\n"
        "  ISM330DHCX expected = 0x6B\n\n"

        "UART OUTPUT FORMAT RULES:\n"
        "- Always use HAL_UART_Transmit(&huart3, ..., HAL_MAX_DELAY) for logic analyzer output\n"
        "- Use \\r\\n line endings for clean decode\n"
        "- Print raw values as integers for hitl_verify to parse\n"
        "- Avoid floating-point in UART format strings unless necessary\n\n"

        "Be concise. Report each step's result to the user clearly.\n"
    )

    app.post_thought(f"\n{NW}[USER]{NC} {prompt}\n")

    messages: list[dict] = [{"role": "user", "content": prompt}]

    while True:
        try:
            response: Message = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system_prompt,
                tools=TOOLS,  # type: ignore
                messages=messages,  # type: ignore
            )
        except Exception as e:
            app.post_thought(f"\n{NR}[ERROR]{NC} Anthropic API error: {e}\n")
            return

        # Append assistant turn to history
        messages.append({
            "role": "assistant",
            "content": response.content,  # type: ignore
        })

        # Partition blocks by type
        text_blocks: list[TextBlock] = [b for b in response.content if b.type == "text"]
        tool_blocks: list[ToolUseBlock] = [b for b in response.content if b.type == "tool_use"]

        # Stream thoughts for text blocks
        for block in text_blocks:
            if block.text.strip():
                app.post_thought(f"\n{NW}[AGENT]{NC} {block.text}\n")

        # If no tool calls → done
        if not tool_blocks:
            # Final text
            for block in text_blocks:
                if block.text.strip():
                    app.update_hw_state(f"[AGENT] {block.text.strip()}")
            app.post_thought(f"\n{NY}[DONE]{NC} Agent finished.\n")
            break

        # Execute all tool calls (parallel) — delegate to _dispatch_tool
        async def run_single(block: ToolUseBlock) -> ToolResult:
            tool_name = block.name
            tool_input = block.input

            app.post_thought(f"\n{NM}[TOOL CALL]{NC} {tool_name}({ {k:v for k,v in tool_input.items()} })\n")
            result = await asyncio.to_thread(_dispatch_tool, tool_name, tool_input, app)
            app.post_thought(f"\n{NG}[TOOL RESULT]{NC} {str(result)[:500]}\n")
            return ToolResult(tool_id=block.id, content=str(result))

        tool_results = await asyncio.gather(*[run_single(b) for b in tool_blocks])

        # Append tool results as new user message
        messages.append({
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": r.tool_id, "content": r.content}
                for r in tool_results
            ],
        })


# ─────────────────────────────────────────────────────────────────────────────
# Claude CLI Agent — run_agent_claude_cli()
# ─────────────────────────────────────────────────────────────────────────────

import re

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def _strip_ansi(raw: bytes) -> str:
    """Strip ANSI escape codes from raw bytes."""
    return ANSI_RE.sub('', raw.decode("utf-8", errors="replace"))


def _parse_json_line(line: bytes) -> Optional[dict]:
    """Parse one JSON line from Claude CLI stdout. Returns None on parse failure."""
    try:
        return json.loads(_strip_ansi(line).strip())
    except json.JSONDecodeError:
        return None


def _build_system_prompt() -> str:
    """Same system prompt used by run_agent() — hardware context for Claude CLI."""
    return (
        "You are an Autonomous Hardware Co-Pilot for the B-U585I-IOT02A IoT Discovery board.\n"
        "You accept ANY natural-language request about the hardware and autonomously fulfill it.\n"
        "Your goal: read sensors, toggle GPIOs, verify timing — whatever the user asks for.\n\n"

        "HARDWARE OVERVIEW:\n"
        "- Board: B-U585I-IOT02A (STM32U585AI IoT Discovery)\n"
        "- Logic analyzer UART: USART3 (PD8=TX, PD9=RX) @ 115200 8N1\n"
        "- VCP UART: USART1 (PA9=TX, PA10=RX) — for debug output\n"
        "- Saleae Logic: CH1 → PD8 (USART3 TX pin)\n"
        "- I2C2: pre-initialized by CubeMX — use hi2c2 for sensor I2C\n"
        "- On-board sensors (I2C2):\n"
        "  ISM330DHCX (instance 0): accelerometer + gyroscope\n"
        "  STTS22H (instance 0): temperature\n"
        "  HTS221 (instance 0): humidity\n"
        "  LPS22HH (instance 0): pressure\n\n"

        "WORKSPACE RULES (CRITICAL):\n"
        "- Code changes go ONLY in hil_workspace/Core/Src/main.c USER CODE BEGIN/END blocks\n"
        "- active_trial_1/ is the PERMANENT BASELINE — NEVER modified\n"
        "- hil_workspace/ is the copilot's working copy — safe to modify\n"
        "- On failure: call restore_board() to revert to baseline\n\n"

        "GENERIC HIL LOOP — follow this order for EVERY request:\n\n"
        "STEP 1 — SIGNAL QUALITY CHECK (always first)\n"
        "Call: capture_verify(duration_s=2, channel=1, baud=115200)\n"
        "If: no bytes decoded → probe disconnected or board silent. Report to user and stop.\n"
        "If: baud mismatch → hardware timing issue. Report and stop.\n"
        "If: OK → proceed.\n\n"
        "STEP 2 — ANALYZE REQUEST\n"
        "Understand what the user wants:\n"
        "- Sensor data over UART → read_bsp_files + inject_code\n"
        "- GPIO toggle / PWM → read main.h for pin defines + inject_code\n"
        "- UART verify / timing check → capture_verify only\n"
        "- Communication test → inject_code to print test pattern\n"
        "- WHO_I_AM / smoke test → inject_code for I2C register read + UART print\n"
        "- Actuator (LED, motor) → read main.h for pin defines + inject_code\n\n"
        "STEP 3 — READ CONTEXT (as needed)\n"
        "Call: read_bsp_files(sensor) to get BSP API for the relevant sensor.\n"
        "Call: read_main_c() to see current workspace state.\n\n"
        "STEP 4 — INJECT CODE\n"
        "Map the request to the right USER CODE blocks:\n"
        "  USER CODE BEGIN PV    → variable declarations\n"
        "  USER CODE BEGIN 2     → init code (runs after MX_I2C2_Init)\n"
        "  USER CODE BEGIN 3     → main loop body (runs each iteration)\n"
        "  USER CODE BEGIN PFP   → function prototypes (if needed)\n"
        "Use: inject_code(block='PV', code='...') for each block.\n"
        "Print sensor/result data as UART text so capture_verify and VCP can read it.\n\n"
        "STEP 5 — BUILD\n"
        "Call: compile_firmware() — if it fails, read errors, fix inject_code, retry.\n\n"
        "STEP 6 — DEPLOY\n"
        "Call: flash_board() — wait 2s for boot, reads first VCP lines.\n\n"
        "STEP 7 — VERIFY\n"
        "Call: capture_verify(duration_s=2, channel=1, baud=115200)\n"
        "  → Confirms logic analyzer sees correct UART signal.\n"
        "For SENSOR REQUESTS (accelerometer, temperature, etc.):\n"
        "  ALWAYS call hitl_verify(sensor=..., threshold=..., interactive=True)\n"
        "  interactive=True launches a Textual app that streams live data and\n"
        "  asks the user to press Y (looks correct) or N (data is wrong).\n"
        "  The agent waits for the user's answer before continuing.\n\n"
        "STEP 8 — ON FAILURE\n"
        "If compilation fails: fix the inject_code calls based on error output.\n"
        "If flash fails: check that binary exists, retry.\n"
        "If capture shows wrong data: check UART pin wiring (PD8), fix inject_code.\n"
        "If hardware fails completely: call restore_board() to reset workspace to baseline.\n\n"

        "BSP QUICK REFERENCE (use read_bsp_files for full API):\n"
        "- ISM330DHCX accel: BSP_MOTION_SENSOR_Init(0, MOTION_ACCELERO)\n"
        "  BSP_MOTION_SENSOR_GetAxes(0, MOTION_ACCELERO, &accel)\n"
        "  Print as: 'ACCEL X=%d Y=%d Z=%d\\r\\n'\n"
        "- STTS22H temp: BSP_ENV_SENSOR_Init(0, ENV_TEMPERATURE)\n"
        "  BSP_ENV_SENSOR_GetValue(0, ENV_TEMPERATURE, &val)\n"
        "  Print as: 'TEMP=%.2f\\r\\n'\n"
        "- WHO_I_AM smoke test: read WHO_AM_I register via BSP, print result.\n"
        "  ISM330DHCX expected = 0x6B\n\n"

        "UART OUTPUT FORMAT RULES:\n"
        "- Always use HAL_UART_Transmit(&huart3, ..., HAL_MAX_DELAY) for logic analyzer output\n"
        "- Use \\r\\n line endings for clean decode\n"
        "- Print raw values as integers for hitl_verify to parse\n"
        "- Avoid floating-point in UART format strings unless necessary\n\n"

        "Available tools: read_bsp_files, read_main_c, reset_board, restore_board,\n"
        "inject_code, compile_firmware, flash_board, capture_verify, hitl_verify.\n\n"
        "Be concise. Report each step's result to the user clearly.\n"
    )


def _format_history(messages: list[dict]) -> str:
    """Format last few conversation turns for embedding in the CLI prompt."""
    if not messages:
        return ""
    lines = []
    for msg in messages[-6:]:
        role = msg.get("role", "").upper()
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " | ".join(
                c.get("text", "") or c.get("content", "")[:300]
                for c in content if isinstance(c, dict)
            )
        lines.append(f"[{role}] {str(content)[:500]}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tool Server — see hil_framework/mcp_server.py (standalone file)
# The old inline python -c approach had __file__ issues; moved to a real file.
# ─────────────────────────────────────────────────────────────────────────────


async def run_agent_claude_cli(prompt: str, app: HardwareCoPilotApp) -> None:
    """
    Run Claude Code CLI as an async subprocess with an MCP tool server.

    The MCP server (hil_tools) runs as a subprocess alongside the CLI and
    exposes all 9 HIL hardware tools. Claude Code calls them via the MCP
    protocol, results stream into the left panel in real time.
    """
    if os.environ.get("HIL_USE_API", "0") == "1":
        await run_agent(prompt, app)
        return

    # Start animation immediately — before any blocking I/O.
    # This ensures the ping-pong ball shows even if claude takes a moment to start.
    app._start_thinking_animation()

    app.post_thought(f"\n[USER] {prompt}\n")

    messages_so_far: list[dict] = [{"role": "user", "content": prompt}]
    history_text = _format_history(messages_so_far)

    extended_prompt = (
        f"{_build_system_prompt()}\n\n"
        f"CONVERSATION HISTORY:\n{history_text}\n\n"
        f"USER REQUEST: {prompt}"
    )

    claude_path = shutil.which("claude")
    if not claude_path:
        app.post_thought(
            f"\n{NR}[ERROR]{NC} `claude` CLI not found in PATH.\n"
            f"Set HIL_USE_API=1 to use Anthropic API instead.\n"
        )
        return

    # Build MCP config — hil_tools server points to the standalone mcp_server.py
    mcp_config = {
        "mcpServers": {
            "hil_tools": {
                "command": sys.executable,
                "args": [os.path.join(SCRIPT_DIR, "hil_framework", "mcp_server.py")],
            }
        }
    }
    mcp_config_json = json.dumps(mcp_config)
    mcp_config_file = os.path.join(SCRIPT_DIR, "hil_framework", "_mcp_config.json")
    with open(mcp_config_file, "w") as f:
        f.write(mcp_config_json)

    # Write prompt to a temp file — avoids stdin/FIFO deadlock entirely.
    # Named pipes + subprocess.PIPE creates a classic deadlock: bash blocks opening the
    # FIFO until someone has it open for writing, but the parent Python process is also
    # waiting.  Passing the prompt as a file content argument sidesteps this cleanly.
    prompt_file = os.path.join(SCRIPT_DIR, "hil_framework", "_mcp_prompt.txt")
    with open(prompt_file, "w") as f:
        f.write(extended_prompt + "\n")

    cmd = [
        "bash", "-c",
        f"exec {shutil.which('claude')} -p "
        f"--verbose --output-format stream-json "
        f"--dangerously-skip-permissions "
        f"--mcp-config {mcp_config_file} "
        f"--model sonnet "
        f"< {prompt_file}",
    ]

    env = {**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,  # bash reads from file, not pipe
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=SCRIPT_DIR,
        env=env,
    )

    done = False
    while not done:
        line = await proc.stdout.readline()
        if not line:
            stderr = await proc.stderr.read()
            if stderr:
                app.post_thought(f"\n{NR}[CLI STDERR]{NC} {stderr.decode()[-600:]}\n")
            app._stop_thinking_animation()
            break

        event = _parse_json_line(line)
        if event is None:
            continue

        msg = event.get("message") or {}
        content = msg.get("content") or event.get("content") or []
        event_type = event.get("type", "")
        subtype = event.get("subtype", "")

        if event_type == "system" and subtype == "init":
            pass  # silently skip session/model/tool-list banners

        elif event_type == "assistant":
            for block in (content if isinstance(content, list) else []):
                btype = block.get("type", "")

                if btype == "thinking":
                    thinking = block.get("thinking", "")[-800:]
                    app.post_thought(f"\n{NB}[THINKING]{NC} {thinking}\n")

                elif btype == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    tool_id = block.get("id", "")
                    app.post_thought(
                        f"\n{NM}[TOOL CALL]{NC} {tool_name}({ {k: v for k, v in tool_input.items()} })\n"
                    )
                    result = await asyncio.to_thread(_dispatch_tool, tool_name, tool_input, app)
                    app.post_thought(f"\n{NG}[TOOL RESULT]{NC} {str(result)[:500]}\n")

                    messages_so_far.append({
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": tool_name,
                                     "input": tool_input, "id": tool_id}],
                    })

                elif btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        app.post_thought(f"\n{NW}[AGENT]{NC} {text}\n")

        elif event_type == "result":
            result_text = event.get("result", "")
            app.post_thought(f"\n{NY}{result_text}{NC}\n")
            app._stop_thinking_animation()
            done = True

    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except Exception:
        pass

    messages_so_far.append({"role": "assistant", "content": f"[CLI Done] {prompt}"})

    # Keep context from growing unboundedly — keep last 10 messages (~5 turns)
    MAX_HISTORY = 10
    if len(messages_so_far) > MAX_HISTORY:
        messages_so_far[:] = messages_so_far[-MAX_HISTORY:]


# ─────────────────────────────────────────────────────────────────────────────
# Textual App
# ─────────────────────────────────────────────────────────────────────────────

# Neon ANSI palette
NC = "\033[0m"    # reset
NB = "\033[96m"   # neon cyan
NM = "\033[95m"   # neon magenta
NY = "\033[93m"   # neon yellow
NG = "\033[92m"   # neon green
NR = "\033[91m"   # neon red
NO = "\033[33m"   # neon orange
NW = "\033[97m"   # bright white
ND = "\033[2m"    # dim/faint


class HardwareCoPilotApp(App):
    """3-panel War Room TUI for the Autonomous Hardware Co-Pilot."""

    CSS = """
    Screen {
        background: #0a0a0f;
    }

    #edgecentri-bar {
        height: 3;
        background: #0f0f1a;
        color: #00ffff;
        content-align: center middle;
        text-style: bold;
        padding: 0 2;
    }

    #panels {
        height: 1fr;
    }

    #thoughts-panel {
        width: 50%;
        border: solid #1a1a3a;
        padding: 0 1;
    }

    #thoughts-header-row {
        height: 2;
        background: #0f0f1a;
    }

    #panel-label-thoughts {
        width: auto;
        color: #00ffff;
        text-style: bold;
        padding: 0 2;
    }

    #thinking-indicator {
        width: 1fr;
        max-width: 60%;
        color: #f0f000;
        content-align: center middle;
    }

    .panel-label-hw {
        color: #00ffff;
        text-style: bold;
        background: #0f0f1a;
        padding: 0 2;
    }

    #hw-log {
        color: #ff8800;
        background: #080810;
    }

    .panel-label-feed {
        color: #00ffff;
        text-style: bold;
        background: #0f0f1a;
        padding: 0 2;
    }

    #feed-log {
        color: #00ff88;
        background: #080810;
    }

    #prompt-row {
        height: 3;
        background: #0f0f1a;
        padding: 0 2;
    }

    Input {
        border: solid #1a1a3a;
        color: #ffffff;
        background: #080810;
    }

    Footer {
        background: #0f0f1a;
        color: #00ffff;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self._agent_task: asyncio.Task | None = None
        self._thinking_ticker: Any | None = None   # animation ticker handle
        self._anim_frame: int = 0                 # ping pong ball frame counter

    def compose(self) -> ComposeResult:
        yield Static(
            "[AncyrAI]   B-U585I-IOT02A  |  Saleae @ 12MHz  |  USART3 115200 8N1",
            id="edgecentri-bar",
        )

        # Three panels
        with Horizontal(id="panels"):
            with Container(id="thoughts-panel"):
                with Horizontal(id="thoughts-header-row"):
                    yield Static("ANCYRAI", id="panel-label-thoughts")
                    yield Static("", id="thinking-indicator")
                yield Log(id="thoughts-log")
            with Container(id="hw-panel"):
                yield Static("HARDWARE", classes="panel-label-hw")
                yield Log(id="hw-log")
            with Container(id="feed-panel"):
                yield Static("LIVE VCP", classes="panel-label-feed")
                yield Log(id="feed-log")

        # Prompt row
        with Horizontal(id="prompt-row"):
            yield Input(
                placeholder="Ask AncyrAI: e.g. 'Get accelerometer data over UART' ...",
                id="prompt-input",
            )

        yield Footer()

    # ── Thinking animation (ball bouncing) ────────────────────────────────

    BALL_FRAMES = [
        "◉              ",
        " ◉             ",
        "  ◉            ",
        "   ◉           ",
        "    ◉          ",
        "     ◉         ",
        "      ◉        ",
        "       ◉       ",
        "        ◉      ",
        "         ◉     ",
        "          ◉    ",
        "         ◉     ",
        "        ◉      ",
        "       ◉       ",
        "      ◉        ",
        "     ◉         ",
        "    ◉          ",
        "   ◉           ",
        "  ◉            ",
        " ◉             ",
    ]

    def _start_thinking_animation(self) -> None:
        """Begin the ◉ ball bouncing animation in the panel header."""
        self._anim_frame = 0

        def tick() -> None:
            frame = self.BALL_FRAMES[self._anim_frame % len(self.BALL_FRAMES)]
            self._anim_frame += 1
            indicator = self.query_one("#thinking-indicator", Static)
            indicator.update(f"{NY}{frame}{NC}")

        self._thinking_ticker = self.set_interval(0.08, tick)

    def _stop_thinking_animation(self) -> None:
        """Stop the animation and clear the indicator (thread-safe)."""
        if self._thinking_ticker is not None:
            self._thinking_ticker.remove()
            self._thinking_ticker = None

        # Clear the indicator immediately (safe to call from async agent context)
        indicator = self.query_one("#thinking-indicator", Static)
        indicator.update("")

    # ── Mount ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        """Initialize panels on startup."""
        thoughts = self.query_one("#thoughts-log", Log)
        thoughts.auto_scroll = True
        hw = self.query_one("#hw-log", Log)
        hw.auto_scroll = True
        feed = self.query_one("#feed-log", Log)
        feed.auto_scroll = True

        # Welcome message — AncyrAI title, neon colors, details in the log
        indicator = self.query_one("#thinking-indicator", Static)
        indicator.update(f"{NM}AncyrAI{NC}")

        thoughts.write(
            f"{NM}{ND}AncyrAI  —  War Room Ready{NC}\n\n"
            f"{ND}Tools available:{NC}\n"
            "  read_bsp_files, read_main_c, inject_code\n"
            "  compile_firmware, flash_board\n"
            "  capture_verify, hitl_verify, reset_board\n\n"
            f"{NB}Try:{NC}\n"
            "  'Get accelerometer data over UART'\n"
            "  'Verify UART timing'\n"
            "  'Print hello world via USART3'\n"
        )

        self.update_hw_state(
            f"{ND}[HW] B-U585I-IOT02A ready{NC}\n"
            f"{NO}[HW] USART3: PD8=TX, PD9=RX @ 115200 8N1{NC}\n"
            f"{NO}[HW] Saleae CH1 -> PD8 (logic analyzer){NC}\n"
            f"{NO}[HW] VCP: /dev/ttyACM0 @ 115200{NC}\n"
            f"{NO}[HW] ISM330DHCX on I2C2{NC}\n"
            f"{NG}[HW] Ready -- type a prompt below!{NC}"
        )

    # ── Thread-safe UI updates ─────────────────────────────────────────

    def _write_log(self, log: Log, text: str) -> None:
        """Write to a Log widget using raw ANSI codes for color/style.

        Called from async agent context which runs on the main asyncio thread —
        safe to call Log.write directly without call_from_thread.
        """
        log.write(text)

    def post_thought(self, text: str) -> None:
        """Append text to the Agent Thoughts log (thread-safe)."""
        # Animation runs until agent explicitly calls _stop_thinking_animation at the end.
        # Do NOT stop it here — post_thought is called for tool results and text blocks
        # throughout the agent run; stopping the timer would freeze the animation early.
        log = self.query_one("#thoughts-log", Log)
        self._write_log(log, text)

    def update_hw_state(self, text: str) -> None:
        """Append text to the Hardware State log (thread-safe)."""
        log = self.query_one("#hw-log", Log)
        self._write_log(log, text + "\n")

    def post_vcp(self, line: str) -> None:
        """Append VCP line to the Live Feed log (thread-safe)."""
        log = self.query_one("#feed-log", Log)
        timestamp = time.strftime("%H:%M:%S")
        self._write_log(log, f"\033[2m[{timestamp}]\033[0m {line}\n")

    # ── Input handling ─────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle prompt submission — launch CLI agent (default) or API agent."""
        prompt = event.value.strip()
        if not prompt:
            return

        # Clear the input field so it's ready for the next prompt
        input_widget = self.query_one("#prompt-input", Input)
        input_widget.value = ""

        # Cancel previous agent if running
        if self._agent_task and not self._agent_task.done():
            self._stop_thinking_animation()
            self.post_thought(f"{NR}[INTERRUPT]{NC} Previous agent cancelled.\n")
            self._agent_task.cancel()

        # Clear feed panel
        feed = self.query_one("#feed-log", Log)
        feed.clear()

        # Default: Claude CLI agent (streams thinking + tool calls to left panel).
        # Set HIL_USE_API=1 to fall back to Anthropic API.
        self._agent_task = asyncio.create_task(run_agent_claude_cli(prompt, self))


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Launching Autonomous Hardware Co-Pilot...")
    print(f"  API key: {'✓ set' if ANTHROPIC_API_KEY else '✗ MISSING — set ANTHROPIC_API_KEY'}")
    print(f"  main.c: {'✓ found' if os.path.exists(MAIN_C_PATH) else '✗ missing'}")
    print(f"  BSP dir: {'✓ found' if os.path.exists(BSP_DIR) else '✗ missing'}")
    print()
    app = HardwareCoPilotApp()
    app.run()
