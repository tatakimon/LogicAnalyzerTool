# SUMMARY - Completed Environments

## Project: Logic Analyzer HIL for B-U585I-IOT02A

### Architecture Version: v0.3.0
- **Date**: 2026-04-09
- **Board**: B-U585I-IOT02A IoT Discovery
- **Status**: READY - Test patterns implemented in active_trial_1/
- **Primary Project**: `active_trial_1/` (CubeMX/CubeIDE workspace)
- **Logic Analyzer UART**: USART3 (PD8=TX, PD9=RX, 115200 8N1)

---

## Environment Nodes

### (none yet - future promotions)

---

## Changelog

| Date | Version | Change |
|------|---------|--------|
| 2026-04-11 | v0.4.0 | Added HIL Framework (hil_framework/). Pure-Python UART decoder, sigrok-cli capture wrapper, test validator. Full end-to-end verification: 6/6 patterns PASS. USART1 mirror for VCP output added. |
| 2026-04-09 | v0.3.0 | Migrated to active_trial_1/ (clean CubeMX project). Full USART3 test patterns in USER CODE blocks. Build via CubeIDE. |
| 2026-04-09 | v0.2.0 | Switched to USART3 (PD8/PD9) for logic analyzer. Added test pattern firmware to main.c USER CODE blocks. Updated IOC and CLAUDE.md. |
| 2026-04-09 | v0.1.0 | Initial project setup with Active_Trial structure |

---

## HIL Framework Status (2026-04-11)

**VERIFIED** — Full pipeline working:
- Saleae Logic → sigrok-cli → UART decoder → Validator → PASS

```python
from hil_framework.capture import quick_capture
from hil_framework.validator import TestValidator

result = quick_capture(duration_s=2, sample_rate='12M', channel=1, baud=115200)
# 197 bytes decoded, all 6 patterns validated
```

*Last updated: 2026-04-11*
