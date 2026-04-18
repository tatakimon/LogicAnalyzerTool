# Scenario: accel_stream — ISM330DHCX Live Accelerometer

**Board:** B-U585I-IOT02A
**Sensor:** ISM330DHCX on I2C2, instance 0, I2C address 0x6B
**Output:** USART3 (PD8=TX) + USART1 (VCP via STLink)
**Format:** `AX=%d  AY=%d  AZ=%d\r\n`
**Baud:** 115200 8N1
**Sample rate:** ~10 Hz (100ms interval)
**Last verified:** 2026-04-18

## Expected Values

| Orientation | AX | AY | AZ |
|-------------|----|----|----|
| Flat, resting | ~0 | ~0 | ~-1000 mg |
| Tilted | swings ±500–800 mg | swings ±500–800 mg | varies |

Sensitivity: ±2g full scale → 0.061 mg/LSB → gravity ≈ 1000 mg on Z when flat

## What This Scenario Covers

1. ISM330DHCX init via `BSP_MOTION_SENSOR_Init()` + `BSP_MOTION_SENSOR_Enable()`
2. Non-blocking `HAL_GetTick()` delta timing (never `HAL_Delay()`)
3. Banner output on both USART3 and USART1 simultaneously
4. snprintf-based UART transmit with correct length tracking
5. 100ms accel polling interval (~10 Hz output)

## Files

| File | Purpose |
|------|---------|
| `USER_CODE_PV.c` | PV (private variables) block |
| `USER_CODE_INIT.c` | After-MX-init block (banner + sensor init) |
| `USER_CODE_LOOP.c` | Main loop block (accel read + UART TX) |
| `verified_main.c` | Full verified `main.c` for hil_workspace |
| `verified.bin.md5` | MD5 of last known working binary |
| `v0.1.0/` | Versioned working snapshot |

## Version History

### v0.1.0 (2026-04-18) — FIRST PASS
- ISM330DHCX returns real mg values
- Non-blocking timing, banner on both UARTs
- Logic analyzer: 0 timing faults, CH1 active
- VCP: AX=-94 AY=-34 AZ=-981 mg (confirmed gravity on Z)
