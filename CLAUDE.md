# Autonomous Embedded Engineer — War Room HIL Framework

## Architecture: The Tri-Pane War Room

You run in **3 simultaneous terminal panes**:

| Pane | Terminal | What it shows | How to open |
|------|----------|---------------|-------------|
| **Pane 1 — The Brain** | This terminal (Claude Code) | You. Thinking aloud. Short [THINKING] tags only. | — |
| **Pane 2 — Physics** | `tail -f .pane2_output` | [DEMO STAGE] tags, logic analyzer reports, timing analysis | Terminal 2 |
| **Pane 3 — Live Feed** | `tail -f .pane3_output` | Live VCP UART stream for HITL tests | Terminal 3 |

> **Rule**: Pane 3 is **only opened** after the logic analyzer confirms deviation < 5%.
> Never open Pane 3 before the signal is verified clean.

---

## 1. Communication Rules

### Stage Announcer (MANDATORY before every major step)
```bash
echo -e "\n[DEMO STAGE: <NAME>] <Action description>\n" > .pane2_output
```
The double newline before the tag makes it visually distinct when `tail -f` streams it.

### Thinking Tags — Keep Brutally Short
```
[THINKING] Board is silent. Checking probe connection...
```
Max 1–2 sentences. The panes do the heavy lifting. Your job is to narrate decisions, not data.

### Pane 2 Pipeline (Logic Analyzer)
All output from `run_test.py`, `capture.py`, and `timing.py` goes to `.pane2_output`:
```bash
# Full capture + timing report → Pane 2
python3 hil_framework/run_test.py --duration 3 --channel 1 --patterns > .pane2_output 2>&1
```

### Pane 3 Pipeline (Live VCP — HITL Only)
After verifying signal quality in Pane 2, open the serial feed:
```bash
# Continuous VCP stream → Pane 3
python3 hil_framework/vcp_feed.py > .pane3_output 2>&1
```
Kill it with `pkill -f vcp_feed.py` when the HITL test is done.

---

## 2. The 2-Stage Verification Rule (CRITICAL)

```
STAGE 1 — Signal Quality (Logic Analyzer)
├── Run: python3 hil_framework/run_test.py --duration 3 --channel 1 > .pane2_output 2>&1
├── Parse the deviation % from the output
└── PASS (deviation < 5%): proceed to STAGE 2
    FAIL (deviation ≥ 5%): report to user, stop — do NOT open Pane 3

STAGE 2 — Live Sensor Feed (VCP — HITL)
├── Only run this if STAGE 1 passed
├── python3 hil_framework/vcp_feed.py > .pane3_output 2>&1 &
├── For interactive verification: python3 hil_framework/interactive_verify.py (opens own terminal)
└── Wait for user interaction, then evaluate result
```

---

## 3. The Autonomous HIL Loop

> **Before starting:** Check `scenarios/` for a similar verified baseline, and scan `LESSONS_LEARNED.md` for relevant hardware rules. This prevents re-learning hard lessons.

### PHASE 1: SETUP — Clean Slate
```bash
echo -e "\n[DEMO STAGE: ARCHITECTURE] Syncing workspace from baseline...\n" > .pane2_output
# Sync hil_workspace from active_trial_1 baseline
python3 hil_framework/hardware.py  # check board connection
```

### PHASE 2: RESEARCH — BSP Scan
```bash
echo -e "\n[DEMO STAGE: ARCHITECTURE] Scanning BSP drivers for reference...\n" > .pane2_output
# Read relevant BSP .h files, report findings to Pane 2
```

### PHASE 3: CODING — Inject USER CODE
```bash
echo -e "\n[DEMO STAGE: CODING] Injecting code into hil_workspace/main.c...\n" > .pane2_output
# Edit hil_workspace/Core/Src/main.c within USER CODE BEGIN/END blocks ONLY
```

### PHASE 4: BUILD — Compile
```bash
echo -e "\n[DEMO STAGE: COMPILING] Building firmware...\n" > .pane2_output
make -C hil_workspace/Debug all > .pane2_output 2>&1
```

### PHASE 5: DEPLOY — Flash
```bash
echo -e "\n[DEMO STAGE: FLASHING] Flashing to board...\n" > .pane2_output
st-flash erase 0x8000000 0x200000 >> .pane2_output 2>&1
st-flash --reset write hil_workspace/Debug/Logic_Analyzer_USART3.bin 0x8000000 >> .pane2_output 2>&1
```

### PHASE 6: HIL VERIFICATION — Stage 1 (Logic Analyzer)
```bash
echo -e "\n[DEMO STAGE: HIL VERIFICATION] Verifying UART signal on logic analyzer...\n" > .pane2_output
python3 hil_framework/run_test.py --duration 3 --channel 1 >> .pane2_output 2>&1
# Parse: if "deviation" or "fault" lines show deviation < 5% → proceed to Stage 2
# If deviation ≥ 5%: [DEMO STAGE: SELF-CORRECTION] and fix
```

### PHASE 7: HIL VERIFICATION — Stage 2 (Live VCP — HITL)
```bash
# ONLY if Stage 1 passed (deviation < 5%)
echo -e "\n[DEMO STAGE: HIL VERIFICATION] Signal clean. Opening live VCP feed...\n" > .pane2_output
python3 hil_framework/vcp_feed.py > .pane3_output 2>&1 &
# For sensor confirmation: python3 hil_framework/interactive_verify.py (opens own window)
# Read /tmp/hil_interactive_verify_result.txt for user decision
```

### PHASE 8: SUCCESS
```bash
echo -e "\n[DEMO STAGE: SUCCESS] Hardware verified! Sensor data confirmed.\n" > .pane2_output
```
**Then:**
1. Save verified USER CODE blocks to `scenarios/<name>/` (see Section 6)
2. Append to `trial_log.md` with timestamp, board, result, and notes
3. Update `scenarios/<name>/verified.bin.md5` with the new binary's checksum

---

## 4. Code Modification Boundaries (ABSOLUTE RULE)

When editing `hil_workspace/Core/Src/main.c`, **FORBIDDEN from modifying any code outside USER CODE blocks**.

Only these regions are safe:
- `/* USER CODE BEGIN PV */` ... `/* USER CODE END PV */` — Private Variables
- `/* USER CODE BEGIN 2 */` ... `/* USER CODE END 2 */` — After MX init
- `/* USER CODE BEGIN 3 */` ... `/* USER CODE END 3 */` — Main loop body
- `/* USER CODE BEGIN PFP */` ... `/* USER CODE END PFP */` — Function prototypes
- `/* USER CODE BEGIN 4 */` ... `/* USER CODE END 4 */`

All HAL initialization, peripheral setup, and CubeMX-generated code is untouchable.

---

## 5. Hardware Target

| Property | Value |
|----------|-------|
| Board | B-U585I-IOT02A (STM32U585AI IoT Discovery) |
| Logic analyzer UART | USART3 (PD8=TX, PD9=RX) @ 115200 8N1 |
| VCP UART | USART1 (PA9=TX, PA10=RX) @ 115200 |
| Saleae probe | CH1 → PD8 |
| Sensors (I2C2) | ISM330DHCX, STTS22H, HTS221, LPS22HH |

**NEVER use `HAL_Delay()`**. Always use non-blocking `HAL_GetTick()` delta:
```c
if ((HAL_GetTick() - last_tick) >= desired_interval_ms) {
    last_tick = HAL_GetTick();
    // action
}
```

---

## 6. Autonomous HIL Workflow — Standard Response to Sensor Prompts

When the user says "accelerometer", "temperature", "sensor data", or similar:
**do NOT start from scratch.** Run the autonomous loop.

### The `auto_hil.py` Script
```bash
python3 hil_framework/auto_hil.py --scenario accel_stream   # accelerometer
python3 hil_framework/auto_hil.py --scenario temperature      # temperature sensor
```

### What It Does (automatically, in order)
1. **Detect** — Saleae + VCP present → abort if not
2. **Build** — compile firmware from current `hil_workspace/`
3. **Flash** — erase + write to 0x08000000
4. **Stage 1** — logic analyzer capture → timing analysis → **must pass < 5% deviation**
5. **Stage 2** — first VCP reading → print to Pane 3
6. **Physical test** — ask user to tilt (accel) or cover (temp) → confirm Y/N
7. **Save result** — `/tmp/hil_verification_result.txt`

### Scenario Detection (what triggers it)
| User prompt | Scenario | Physical verification |
|------------|----------|---------------------|
| "accelerometer", "tilt", "X Y Z", "ISM330DHCX" | `accel_stream` | Tilt board — X/Y swing |
| "temperature", "STTS22H", "temp sensor" | `temperature` | Cover sensor — TEMP changes |
| "UART data", "stream", "115200" | `accel_stream` | Default to accel if running |

### If no scenario matches
Follow the full HIL Loop (Section 3): research → inject USER CODE → build → flash → Stage 1 → Stage 2 → ask user to verify → save to scenario on success.

### Scenarios Directory
```
hil_framework/scenarios/
  accel_stream/     — ISM330DHCX live axes (verified 2026-04-18)
  temperature/      — STTS22H temperature stream (pending)
```

---

## 7. Verified Scenarios — Save Working Code as Base

When HIL verification passes, **save the verified USER CODE blocks** to `scenarios/` so the next similar request starts from a working baseline.

### Save a Verified Scenario
After a successful HIL run, save to `scenarios/<name>/`:
```
scenarios/<name>/
  README.md           — what it does, expected values, UART format
  USER_CODE_PV.c     — PV block (variables, defines)
  USER_CODE_INIT.c   — after-MX-init block (sensor init)
  USER_CODE_LOOP.c   — main loop block
  verified.bin.md5   — MD5 of the working .bin
```

### Start From a Scenario
When the user asks for something similar to a known scenario:
1. Read `scenarios/<name>/README.md` — understand the baseline
2. Read the `USER_CODE_*.c` blocks — apply to `hil_workspace/Core/Src/main.c`
3. Build, flash, verify — if it works, increment the scenario version

### Scenario: accel_stream (verified 2026-04-18)
`scenarios/accel_stream/` — ISM330DHCX live accelerometer
- Format: `AX=%d  AY=%d  AZ=%d\r\n` @ 115200, ~10 Hz
- Expected: AX≈0, AY≈0, AZ≈-1000 mg (gravity on Z, board flat)
- **KEY:** `BSP_MOTION_SENSOR_Init()` + `BSP_MOTION_SENSOR_Enable()` — not just Init alone

---

## 8. Lessons Learned — Permanent Hardware Rules

Before starting a new sensor integration, **check `hil_framework/LESSONS_LEARNED.md`** — it contains hard-earned rules that prevent wasted debugging time.

Key rules for B-U585I-IOT02A:
- `ISM330DHCX_Init()` leaves accel in power-down — must call `BSP_MOTION_SENSOR_Enable()` after
- Demo sigrok device doesn't support `--time` — use `--samples` instead
- Saleae `conn=` string changes on USB reconnect — always re-run `sigrok-cli --scan`
- VCP output files live in `hil_framework/` — use full path in `tail -f`
- UART: always use `snprintf()` + tracked length, never bare `strlen()` on unterminated buffers

---

## 9. HIL Framework Scripts

All scripts live in `hil_framework/` and are called via **native Bash** — never via MCP.

| Script | Purpose | Stdout target |
|--------|---------|---------------|
| `auto_hil.py` | **Autonomous HIL loop** — build, flash, timing, VCP, physical test, result | `.pane2_output` + `.pane3_output` |
| `run_test.py` | Full capture + decode + timing report | `.pane2_output` |
| `capture.py` | Saleae sigrok-cli capture wrapper | `.pane2_output` |
| `timing.py` | VCD-based per-byte timing analysis | `.pane2_output` |
| `hardware.py` | Board detection, flash, VCP read | `.pane2_output` |
| `interactive_verify.py` | Live-accelerometer Textual TUI with tilt countdown + Y/N | Own terminal window |
| `vcp_feed.py` | Continuous VCP serial → stdout | `.pane3_output` |
| `scenarios/` | Verified working baselines (save your USER CODE blocks here after a PASS) | — |
| `LESSONS_LEARNED.md` | Permanent hardware rules — read before starting new sensor work | — |
| `trial_log.md` | Timestamped log of every HIL run — append after each verification | — |

### Quick capture (one-liner)
```bash
python3 hil_framework/capture.py --duration 2 --rate 12M --channel 1 > .pane2_output 2>&1
```

### Full HIL test
```bash
python3 hil_framework/run_test.py --duration 3 --channel 1 --patterns > .pane2_output 2>&1
```

### Flash firmware
```bash
st-flash erase 0x8000000 0x200000 > .pane2_output 2>&1
st-flash --reset write hil_workspace/Debug/Logic_Analyzer_USART3.bin 0x8000000 >> .pane2_output 2>&1
```

### Live VCP stream (Pane 3)
```bash
python3 hil_framework/vcp_feed.py > .pane3_output 2>&1
```

---

## 10. Glue Script: `vcp_feed.py`

This script does not exist yet. It is the **only new file needed**. See Section 9 for the spec.

---

## 11. BSP Quick Reference

### ISM330DHCX (Accelerometer) — Correct Init Pattern
```c
// 1. Init (leaves accel in power-down — MUST call Enable after)
if (BSP_MOTION_SENSOR_Init(0, MOTION_ACCELERO) == BSP_ERROR_NONE) {
    // 2. Explicitly enable — THIS IS REQUIRED or all axes read 0
    if (BSP_MOTION_SENSOR_Enable(0, MOTION_ACCELERO) == BSP_ERROR_NONE) {
        accel_init_ok = 1;
    }
}
// 3. Read axes
BSP_MOTION_SENSOR_Axes_t accel;
BSP_MOTION_SENSOR_GetAxes(0, MOTION_ACCELERO, &accel);
// Values in mg: AX≈0, AY≈0, AZ≈-1000 when flat (gravity on Z)
// Transmit: "AX=%d  AY=%d  AZ=%d\r\n"
```

### STTS22H (Temperature)
```c
BSP_ENV_SENSOR_Init(0, ENV_TEMPERATURE);
float temp;
BSP_ENV_SENSOR_GetValue(0, ENV_TEMPERATURE, &temp);
// Print: "TEMP=%.2f\r\n" via HAL_UART_Transmit(&huart3, ...)
```

---

## 12. Glue Script Spec — `vcp_feed.py`

**Purpose**: Stream VCP serial data to stdout for `tail -f .pane3_output`.

**Implementation** (write to `hil_framework/vcp_feed.py`):
```python
#!/usr/bin/env python3
"""VCP Serial Feed — streams /dev/ttyACM0 to stdout for tail -f .pane3_output."""
import serial, sys, time

PORT = "/dev/ttyACM0"
BAUD = 115200

try:
    with serial.Serial(PORT, BAUD, timeout=1) as ser:
        ser.reset_input_buffer()
        sys.stderr.write(f"[vcp_feed] Streaming {PORT} @ {BAUD} baud...\n")
        sys.stderr.flush()
        while True:
            if ser.in_waiting:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line:
                    ts = time.strftime("%H:%M:%S")
                    sys.stdout.write(f"[{ts}] {line}\n")
                    sys.stdout.flush()
            else:
                time.sleep(0.05)
except serial.SerialException as e:
    sys.stderr.write(f"[vcp_feed] Error: {e}\n")
    sys.exit(1)
```

**Usage**: `python3 hil_framework/vcp_feed.py > .pane3_output 2>&1`

---

## 13. UART Test Patterns (Baseline Firmware)

| Pattern | Description |
|---------|-------------|
| `[0x55]` | Binary 01010101 — verify single-ended decode |
| `[0xAA]` | Binary 10101010 — verify single-ended decode |
| `[0xFF]` | All bits high — verify mark/space |
| `[0x00]` | All bits low — verify idle state |
| `[CNT]` | 00–FF counter — verify data pattern |
| `[ASCII]` | `LOGIC_ANALYZER_TEST` — verify ASCII decode |

---

*Last updated: 2026-04-18 — War Room HIL Framework v3.0*
