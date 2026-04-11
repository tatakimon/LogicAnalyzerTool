# HIL Skill - Hardware-In-the-Loop Testing

## Overview

The HIL Framework provides automated firmware verification using a Saleae logic analyzer + sigrok-cli. It captures UART signals, decodes them, and validates against expected patterns — all without manual inspection.

## Quick Start

### Run a HIL test
```bash
cd /home/kerem/logic_analyzer
python3 hil_framework/run_test.py --duration 3 --channel 1 --patterns
```

### Flash + verify
```bash
python3 hil_framework/run_test.py \
    --flash active_trial_1/Debug/Logic_Analyzer_USART3.bin \
    --duration 3 \
    --patterns
```

### Programmatic usage
```python
from hil_framework import quick_capture

result = quick_capture(duration_s=2, sample_rate='12M', channel=1, baud=115200)
print(result['text'])       # Decoded text
print(result['success'])    # True/False
print(result['raw_bytes'])   # List of byte values
```

## Architecture

```
hil_framework/
├── __init__.py        # Package entry point
├── capture.py         # Logic analyzer capture (sigrok-cli wrapper)
├── decoder.py         # UART decoder (pure Python, no numpy)
├── validator.py       # Test validation (pass/fail reporting)
├── hardware.py        # Board detection, flash, VCP communication
└── run_test.py        # CLI test runner
```

## Module Reference

### LogicAnalyzerCapture

Auto-detects Saleae devices and captures UART signals.

```python
from hil_framework import LogicAnalyzerCapture

cap = LogicAnalyzerCapture()

# Find devices
devices = cap.list_devices()
print(devices)  # [DeviceInfo(name='Saleae Logic', channels=['D0'...], ...)]

# Capture at 12MHz for 2 seconds on channel 1
result = cap.capture(duration_s=2.0, sample_rate='12M', channel=1)

# result.channel_samples = {1: [0,1,1,1,0,0,...]}  # D1 samples
print(f"Samples: {len(result.channel_samples[1])}")
print(f"Duration: {result.duration_s}s")
print(f"Rate: {result.sample_rate_hz} Hz")
```

### UARTDecoder

Decodes UART frames from sample streams.

```python
from hil_framework import UARTDecoder

decoder = UARTDecoder(baud=115200, databits=8, parity='N', stopbits=1)

# Decode from samples at 12MHz
samples = [1,1,1,0,0,1,...]  # 0/1 per sample
frames = decoder.decode_stream(samples, sample_rate=12_000_000)
bytes_data = [f.byte_value for f in frames]
text = decoder.decode_text(samples, sample_rate=12_000_000)

# Quick roundtrip verification
encoder = Encoder(baud=115200)
sig = encoder.encode_byte(0x55, 12_000_000)
decoded = decoder.decode_bytes(sig, 12_000_000)
assert decoded == [0x55]  # Always passes
```

### TestValidator

Validates decoded output against expected patterns.

```python
from hil_framework import TestValidator

validator = TestValidator("USART3 Patterns")
validator.expect_pattern('[0x55]', 'Binary 0x55')
validator.expect_pattern('[0xAA]', 'Binary 0xAA')
validator.expect_pattern_sequence(['[0x55]', '[0xAA]', '[0xFF]', '[0x00]'], 'Full cycle')
validator.expect_no_zeros('Real data')

result = validator.validate(decoded_text, raw_bytes, duration_s=3.0)
result.print_report()  # Formatted output

print(result.passed)  # True/False
```

### BoardHardware

Board detection, flashing, and VCP communication.

```python
from hil_framework import BoardHardware

hw = BoardHardware()

# Flash firmware
hw.flash('firmware.bin')

# Reset
hw.reset()

# Read VCP output
hw.vcp_read(baud=115200, timeout=3.0)
# Returns: (success, text, raw_bytes)

# Line-by-line reader
for line in hw.vcp_reader(duration=5.0):
    print(line)
```

## Validation Profiles

### UART Test (test patterns)
```bash
python3 run_test.py --patterns --duration 3 --channel 1
```
Expects: `[0x55]`, `[0xAA]`, `[0xFF]`, `[0x00]`, `[CNT]`, `[ASCII]`

### Sensor Test
```bash
python3 run_test.py --validate sensor_test
```
Expects: Non-zero bytes, realistic sensor value ranges

### Quick Smoke Test
```bash
python3 run_test.py --quick
```
Expects: Any non-zero data decoded

## Hardware Setup

| Probe | Pin | Purpose |
|-------|-----|---------|
| D0 (CH0) | PD8 | USART3 TX |
| D1 (CH1) | PD9 | USART3 RX |
| GND | GND | Ground reference |

## Known Issues

- **WSL2 USB passthrough**: Saleae must be attached to WSL via `usbipd` on Windows
- **Verify fails but flash succeeds**: STLink readout protection (RDP) blocks verification. Flash still works.
- **No devices found**: Re-plug USB, then run: `usbipd attach --wsl --hardware-id 0925:3881` (Windows)
