# Trial Log — B-U585I-IOT02A HIL Framework

## Format
```
## YYYY-MM-DD — <Title>
**Board:** B-U585I-IOT02A  |  **Saleae:** fx2lafw:conn=?  |  **VCP:** /dev/ttyACM0
**Goal:** <what was being tested>
**Stage 1:** <logic analyzer result>  |  **Stage 2:** <VCP/accelerometer result>
**Outcome:** PASS / FAIL
**Notes:** <what changed, what broke, what was learned>
```

---

## 2026-04-18 — ISM330DHCX Live Accelerometer Streaming
**Board:** B-U585I-IOT02A  |  **Saleae:** fx2lafw:conn=1.10  |  **VCP:** /dev/ttyACM0
**Goal:** Stream live X/Y/Z accelerometer values over USART3 and USART1 (VCP)
**Stage 1:** CH1 (PD9), 4,507 edges, 0 timing faults, PASS
**Stage 2:** AX=-94 AY=-34 AZ=-981 mg — real sensor data, gravity confirmed on Z
**Outcome:** PASS
**Notes:**
- Root cause of zero values: `ISM330DHCX_Init()` leaves accel in power-down
- Fix: add `BSP_MOTION_SENSOR_Enable(0, MOTION_ACCELERO)` after init
- VCP shows AX≈0, AY≈0, AZ≈-981 mg when flat (gravity on Z, expected)
- Tilt confirmed: values change when board is tilted

---

## 2026-04-17 — UART Pattern Baseline Verification
**Board:** B-U585I-IOT02A  |  **Saleae:** fx2lafw:conn=1.7  |  **VCP:** /dev/ttyACM0
**Goal:** Verify UART output at 115200 8N1, establish timing baseline
**Stage 1:** CH1 (PD9), 1,961 edges, 0 timing faults, PASS
**Stage 2:** VCP confirmed streaming
**Outcome:** PASS
**Notes:**
- Demo device was limiting capture to 500K samples
- Saleae reconnected at conn=1.7 then later 1.10 (USB reassignment)
- Logic analyzer timing: mean pulse 15.9us (multi-byte), 1-bit pulses correctly centered

---

*Last updated: 2026-04-18*

---

## 2026-04-18 — UART + Accelerometer Live Stream Verification
**Board:** B-U585I-IOT02A  |  **Saleae:** fx2lafw:conn=1.10  |  **VCP:** /dev/ttyACM0
**Goal:** Verify UART accelerometer stream at 115200 baud, logic analyzer + VCP
**Stage 1:** CH1 (PD9), 1,537 edges, 0 timing faults, PASS
**Stage 2:** AX=-4  AY=-158  AZ=-977 mg — live, gravity confirmed on Z
**Outcome:** PASS
**Notes:**
- CH1 = PD9 (USART3_RX) — active channel
- Sparkline legend improved: ▌=1-bit, ═=multi-bit, X=fault
- Firmware: ISM330DHCX with BSP_MOTION_SENSOR_Enable() fix

---

## 2026-04-18 — HTS221 Temperature Sensor via UART
**Board:** B-U585I-IOT02A  |  **Saleae:** fx2lafw:conn=1.14  |  **VCP:** /dev/ttyACM0
**Goal:** Stream live temperature from HTS221 over USART3 + USART1
**Stage 1:** CH1 (PD9), 74 pulses, 153 edges, 0 timing faults, PASS
**Stage 2:** TEMP=30.24–30.34 °C — live, sensor drifting naturally
**Physical Test:** User covers/blows on HTS221 → TEMP rises
**Outcome:** PASS
**Notes:**
- First build with %f format → `TEMP=\r` (no value) — newlib-nano needs `-u _printf_float`
- Fix: integer math formatting `TEMP=%d.%02d\r\n`
- BSP init pattern: `BSP_ENV_SENSOR_Init()` + `BSP_ENV_SENSOR_Enable()` (not just Init)
- Makefile: added `b_u585i_iot02a_env_sensors`, `hts221 hts221_reg lps22hh lps22hh_reg`
- Binary MD5: `5551ffd75158035abc37b49f25fc6192`
- Scenario saved to `scenarios/temperature/`
