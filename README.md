# LogicAnalyzerTool

Hardware-In-the-Loop (HIL) testing framework for the **B-U585I-IOT02A IoT Discovery** board with **Saleae Logic** analyzer.

Captures UART signals from the STM32, decodes them with a pure-Python decoder, and displays live waveforms and validation results in a terminal dashboard.

---

## Hardware Setup

| Component | Connection |
|-----------|-----------|
| **B-U585I-IOT02A** | USB-C power + ST-LINK |
| **Logic Analyzer** | CH1 (D1) → PD8 (USART3 TX), GND → GND |
| **Baud Rate** | 115200 8N1 |
| **Sample Rate** | 12 MHz (recommended) |

---

## Quick Start

### 1. Flash Firmware

```bash
cd active_trial_1/Debug
make clean && make all
st-flash write Logic_Analyzer_USART3.bin 0x08000000
```

### 2. Run HIL Dashboard

```bash
# From logic analyzer (requires sigrok-cli + Saleae)
python3 hil_framework/dashboard.py --duration 3 --channel 1

# From VCP serial (just serial port)
python3 hil_framework/dashboard.py --vcp --port /dev/ttyACM0 --duration 5
```

### 3. Expected Output

The firmware outputs cycling test patterns every 100ms:
```
[0x55] 55 01010101 'U' _▄_▄_▄_▄_▄▄  #K1
[0xAA] AA 10101010 .. __-_-_-_--  #K1
[0xFF] FF 11111111 .. _---------  #K1
[0x00] 00 00000000 .. _________-  #K1
[CNT] 00
[ASCII] LOGIC_ANALYZER_TEST
```

The dashboard renders this with ANSI colors, waveforms, and byte histograms:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  LOGIC ANALYZER - LIVE DASHBOARD                                             ║
║   Saleae Logic @ 12MHz   |   115200 8N1   |   CH1 = PD8 (USART3 TX)         ║
║   [0xFF]  0xFF  11111111  '?'  _▄▄▄▄▄▄▄▄▄    #0899                          ║
║   BYTE DISTRIBUTION                                                           ║
║   0x30-0x3F  |  ███████████████████  (188)                                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║   ● LIVE  |  Decoded: 900 bytes  |  6.4s                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝

  ✓  PASS  Contains '[0x55]'
  ✓  PASS  Contains '[0xAA]'
  ✓  PASS  Contains '[0xFF]'
  ✓  PASS  Contains '[0x00]'
  ✓  PASS  Contains '[CNT]'
  ✓  PASS  Contains '[ASCII]'
  HIL RESULT: 6/6 PASSED
```

---

## HIL Framework

Located in `hil_framework/`:

| Module | Purpose |
|--------|---------|
| `capture.py` | sigrok-cli wrapper for Saleae capture |
| `decoder.py` | Pure-Python UART decoder (no numpy) |
| `validator.py` | Pattern/byte validation |
| `hardware.py` | Board flashing + VCP serial |
| `dashboard.py` | Terminal UI dashboard |
| `run_test.py` | CLI test runner |
| `timing.py` | Hardware timing analysis via VCD parsing |

### Direct Python Usage

```python
from hil_framework.capture import quick_capture
from hil_framework.validator import TestValidator

result = quick_capture(duration_s=2, sample_rate='12M', channel=1, baud=115200)
print(f"Decoded: {result['bytes_decoded']} bytes")

validator = TestValidator('USART3 Patterns')
for p in ['[0x55]', '[0xAA]', '[0xFF]', '[0x00]', '[CNT]', '[ASCII]']:
    validator.expect_pattern(p)
test_result = validator.validate(result['text'], result['raw_bytes'])
test_result.print_report()
```

### Decoder Self-Test

```bash
python3 hil_framework/decoder.py
# Tests: 1MHz, 10MHz, 12MHz — all 256 byte values round-trip correctly
```

### Hardware Timing Analysis

Analyzes physical-layer UART timing from the VCD export of a capture file.
Detects bit width deviations beyond the configurable tolerance (default: 5%).

```bash
# Analyze timing on a .sr capture file
python3 hil_framework/timing.py capture.sr --channel 1

# Stricter tolerance
python3 hil_framework/timing.py capture.sr --tolerance 0.03
```

Key checks:
- Single bit periods vs. ideal 8.68 µs (115200 baud)
- Consecutive same-state bits (e.g. `0xFF` stop+data bits = 2×, 3×, … multiples)
- Inter-packet idle gaps (> byte period) — flagged but not faulted

Result: **PASS** with 0 violations means all edge transitions are within ±5% of the ideal bit time.

```
  Pulse width histogram:
       1-bit (8.68us) │ ███████████████████████████████  (297)
      2-bit (17.36us) │ ██████████                       (86)   ← consecutive 1-bits
      3-bit (26.04us) │ █                                (13)
      5-bit (43.40us) │ █                                (15)

  ✓ PASS  0 physical timing violations found
```

Timing is automatically run as Step 6 in `run_test.py`, printing:
```
  ✓ PASS:  0 physical timing violations found
```

---

## Project Structure

```
logic_analyzer/
├── README.md                      # This file
├── CLAUDE.md                      # Developer notes + HIL loop
├── SUMMARY.md                     # Project changelog
├── LESSONS_LEARNED.md             # Debugging notes
├── hil_framework/                 # HIL testing framework
│   ├── __init__.py
│   ├── capture.py                 # sigrok-cli capture
│   ├── decoder.py                 # UART decoder
│   ├── validator.py               # Pattern validator
│   ├── hardware.py                # Board + flash
│   ├── dashboard.py                # Terminal dashboard
│   ├── run_test.py                # CLI runner
│   └── hil_skill.md               # Full documentation
├── active_trial_1/                # Primary project (CubeMX/CubeIDE)
│   ├── active_trial.ioc           # CubeMX config (USART3 enabled)
│   ├── Core/Src/main.c             # Firmware with test patterns
│   └── Debug/                     # Build output
├── BSP/                           # Reference BSP drivers
│   └── BSP/Drivers/BSP/B-U585I-IOT02A/
└── Active_Trial/                  # Legacy (deprecated)
```

---

## Firmware Test Patterns

| Pattern | Description |
|---------|-------------|
| `[0x55]` | Binary 01010101 — verifies single-ended decoding |
| `[0xAA]` | Binary 10101010 — verifies single-ended decoding |
| `[0xFF]` | All bits high — verifies mark/space |
| `[0x00]` | All bits low — verifies idle state |
| `[CNT]` | 00–FF counter — verifies full byte range |
| `[ASCII]` | "LOGIC_ANALYZER_TEST" — verifies ASCII decode |

Output goes to **USART3** (PD8 for logic analyzer) and **USART1** (PA9/PA10 for VCP terminal).

---

## Requirements

**Hardware:**
- B-U585I-IOT02A IoT Discovery
- Saleae Logic (or compatible sigrok device)
- USB-C cable + ST-LINK

**Software:**
- STM32 toolchain (`arm-none-eabi-gcc`)
- sigrok-cli + fx2lafw driver
- Python 3.8+

```bash
# Install sigrok on Ubuntu/Debian
sudo apt install sigrok sigrok-cli

# Install Python deps
pip3 install pyserial

# WSL2: pass through Saleae USB
usbipd list                          # find the device
usbipd bind --hardware-id "0925:3881"
usbipd attach --wsl --hardware-id "0925:3881"
```

---

## Troubleshooting

**Logic analyzer not detected:**
```bash
sigrok-cli --scan
# Should show: fx2lafw:conn=1.X - Saleae Logic ...
```

**No bytes decoded:**
- Check GND is connected between board and logic analyzer
- Verify CH1 probe is on PD8 (USART3 TX)
- Confirm 115200 baud, 8N1 in decoder settings

**Dashboard syntax errors:**
- Requires Python 3.8+
- Terminal must support ANSI escape codes (use a modern terminal)

**Flash verify fails (exit 255):**
- Normal on U5 series with RDP enabled — flash write succeeds despite verify error
