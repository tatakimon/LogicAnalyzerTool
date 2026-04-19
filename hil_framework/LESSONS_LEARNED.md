# Lessons Learned — B-U585I-IOT02A HIL Framework

## Permanent Hardware & Firmware Rules

---

### ISM330DHCX Accelerometer: Init Does NOT Enable the Sensor

**Symptom:** `BSP_MOTION_SENSOR_Init()` returns `BSP_ERROR_NONE`, but all axes read 0.

**Root Cause:** `ISM330DHCX_Init()` leaves the accelerometer in **power-down mode** after init. The ODR is set to `XL_ODR_OFF`.

**Fix:** Call `BSP_MOTION_SENSOR_Enable()` after `BSP_MOTION_SENSOR_Init()`:
```c
if (BSP_MOTION_SENSOR_Init(0, MOTION_ACCELERO) == BSP_ERROR_NONE) {
    if (BSP_MOTION_SENSOR_Enable(0, MOTION_ACCELERO) == BSP_ERROR_NONE) {
        accel_init_ok = 1;  // NOW accel is truly ready
    }
}
```
**When to check:** Every new accelerometer integration.

---

### UART Transmission: Always Check strlen Before Sending

**Symptom:** Garbage or truncated output on UART.

**Root Cause:** Using `strlen()` on a buffer that hasn't been null-terminated. Or passing the wrong length.

**Fix:** Use `snprintf()` which always null-terminates, or manually track length:
```c
int len = snprintf(tx_buf, TX_BUF_SIZE, "AX=%d  AY=%d  AZ=%d\r\n",
    (int)accel_axes.xval, (int)accel_axes.yval, (int)accel_axes.zval);
if (len > 0) {
    (void)HAL_UART_Transmit(&huart3, (uint8_t *)tx_buf, len, HAL_MAX_DELAY);
}
```
**NEVER** do `HAL_UART_Transmit(&huart3, (uint8_t *)banner[i], strlen(banner[i]), ...)`.

---

### sigrok-cli: Demo Device Does Not Support `--time`

**Symptom:** sigrok-cli hangs indefinitely with demo device even for short captures.

**Root Cause:** The demo device driver doesn't implement time-based capture.

**Fix:** Use `--samples` instead of `--time`:
```python
# Demo device — cap at ~500K samples, 15s timeout
cmd = ['sigrok-cli', '-d', conn_str, '-c', f'samplerate={rate_hz}',
       '--samples', '500000', '-o', output_file]
timeout_val = 15
```
**Real Saleae:** Use `--time` or `--samples` freely.

---

### Saleae USB Connection String Changes on Reconnect

**Symptom:** `fx2lafw:conn=1.7` one session, `fx2lafw:conn=1.10` next session.

**Root Cause:** WSL2 USB passthrough assigns different connection IDs on each physical reconnect.

**Fix:** Always re-run `sigrok-cli --scan` after reconnect to get the current `conn=` string.
Never hardcode `fx2lafw:conn=1.7` in scripts — read it dynamically.

---

### VCP Output Files Are Relative to hil_framework/ Directory

**Symptom:** `tail -f .pane2_output` shows nothing.

**Root Cause:** `.pane2_output` and `.pane3_output` live in `hil_framework/`, not the project root.

**Fix:** Run `tail -f` with full path:
```bash
tail -f /home/kerem/logic_analyzer/hil_framework/.pane2_output
tail -f /home/kerem/logic_analyzer/hil_framework/.pane3_output
```
Or `cd` to `hil_framework/` before running `tail -f .pane2_output`.

---

### Logic Analyzer: CH1 = PD9 (USART3_RX), CH0 = PD8 (USART3_TX)

**Symptom:** No transitions seen on CH0, but VCP shows live data.

**Root Cause:** The probe was on PD9 (RX) not PD8 (TX). RX captures incoming characters; TX only shows outbound.

**Fix:** Confirm probe placement. RX (PD9) is the reliable channel for capturing UART from the MCU's perspective.
- CH0 = PD8 = USART3_TX (MCU transmits — probe here to see MCU output)
- CH1 = PD9 = USART3_RX (MCU receives — probe here to see all traffic including echo)

---

### sigrok-cli USB Claim Race on Repeated Scans

**Symptom:** "Unable to claim USB interface" error when running `list_devices()` then immediately `capture()`.

**Root Cause:** Calling `sigrok-cli --scan` (which claims USB) and then running capture (which also claims USB) creates a race.

**Fix:** Call `list_devices()` once, cache the result, reuse the cached `_devices` list for subsequent captures. Never call `list_devices()` inside `capture()`.

---

### ISM330DHCX Sensitivity: 2g Scale → ~0.061 mg per LSB

**Symptom:** Raw axis values look tiny (e.g., `-94`) vs. expected ±16000.

**Root Cause:** Different full-scale range set in `ISM330DHCX_Init()`:
- `ISM330DHCX_2g`  → sensitivity ≈ 0.061 mg/LSB → values ±1000 for ±1g
- `ISM330DHCX_4g`  → sensitivity ≈ 0.122 mg/LSB → values ±2000 for ±1g
- `ISM330DHCX_8g`  → sensitivity ≈ 0.244 mg/LSB → values ±4000 for ±1g
- `ISM330DHCX_16g` → sensitivity ≈ 0.488 mg/LSB → values ±8000 for ±1g

**Expected values (2g, board flat):**
- AX ≈ 0 mg (at rest)
- AY ≈ 0 mg (at rest)
- AZ ≈ -1000 mg (gravity on Z when flat)
- If tilted: X/Y swing to ±500–800 mg

---

*Last updated: 2026-04-18*

---

### newlib-nano: %f Printf Float Formatting Produces Empty Output

**Symptom:** `snprintf(tx_buf, SIZE, "TEMP=%.2f\r\n", temperature)` outputs `TEMP=\r\n` with no value.

**Root Cause:** newlib-nano (the C library for ARM Cortex-M) does not include floating-point printf support by default. The format string is parsed but `%f` outputs nothing.

**Fix option A (best):** Use integer math — avoid `%f` entirely:
```c
int int_part = (int)temperature;
int frac_part = (int)((temperature - int_part) * 100.0f);
if (frac_part < 0) frac_part = -frac_part;
snprintf(tx_buf, TX_BUF_SIZE, "TEMP=%d.%02d\r\n", int_part, frac_part);
```

**Fix option B:** Link with `-u _printf_float` to force float support (increases binary size):
```
LDFLAGS += -u _printf_float
```

**When to check:** Any time printing a `float` or `double` from a sensor.
