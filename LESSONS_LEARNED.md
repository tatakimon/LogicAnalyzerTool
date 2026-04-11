# LESSONS_LEARNED - Logic Analyzer HIL Project

## Hardware Lessons

### B-U585I-IOT02A IoT Board Specifics
- **USART3 is the logic analyzer UART** (PD8=TX, PD9=RX, AF7)
- **UART4** (PC10/PC11) is used by the WRLS wireless module
- **USART1** (PA9/PA10) is the VCP debug port via ST-LINK
- Always check `active_trial_1/active_trial.ioc` for actual pin assignments
- The IoT board has different pinout than plain STM32U585 Discovery
- **Always use CubeMX-generated handle names** (`huart3`, not `husart3`)

### Logic Analyzer Connection
- Connect GND first, then signal pins
- Use 115200 8N1 for standard debug output
- Sample at 10MHz+ for reliable decoding

### Sensor BSP on IoT Board
- ISM330DHCX accelerometer uses SPI/I2C
- STTS22H temperature sensor on I2C bus
- BSP drivers available in `BSP/BSP/Drivers/BSP/B-U585I-IOT02A/`

---

## HIL Framework Lessons

### Capture Pipeline
- sigrok-cli captures to `.sr` files (ZIP format containing channel data files)
- Sample rate must match device capability (Saleae: 20K-48M Hz max 12M)
- Capture file size = channels × duration × sample_rate / 8
- 12MHz × 2s = 192.5M samples ≈ 47KB compressed

### UART Decoding
- Decoder must use **cumulative integer sample counts** — drifting from rounding causes wrong bytes
- Encoder and decoder must use **consistent `round(spb)`** for all bit periods
- Majority vote (7-sample window) handles noise at high sample rates
- spb = bit_time_us / sample_time_us (e.g., 8.68us / 0.083us ≈ 104.2 samples/bit)

### Device Detection
- Always re-scan devices before capture — cached results go stale
- Pick first non-demo device, demo device is for testing only
- Saleae connection string: `fx2lafw:conn=bus.device` (e.g., `fx2lafw:conn=1.14`)

### Validation
- Pattern matching works on decoded text: `'[0x55]' in text`
- Counter test catches decoder bugs: all 256 byte values must round-trip
- WSL2 USB passthrough: use `usbipd` on Windows to attach Saleae to WSL

---

## Software Lessons

### HAL Configuration
- Always use working hal_conf.h from verified projects
- Module enable/disable must match available source files
- STM32U585xx requires `USE_HAL_DRIVER` and `STM32U585xx` defines

### Build System
- Makefile pattern rules must properly map source paths to object paths
- Use `patsubst` or explicit paths to avoid "no rule to make target" errors
- Clean build directory when switching makefiles

### Code Modification
- Only modify USER CODE blocks to preserve CubeMX regeneration capability
- Never modify HAL initialization functions directly

---

## HIL Framework Lessons

### Verification
- Always run auto_verify.py after flashing
- Check for realistic sensor values (not 0, not garbage)
- 5-second minimum read for validation

### Loop Structure
- ARCHITECTURE → CODING → COMPILING → FLASHING → HIL_VERIFICATION → SELF-CORRECTION
- Loop until HARDWARE VERIFIED

---

*Last updated: 2026-04-09*
