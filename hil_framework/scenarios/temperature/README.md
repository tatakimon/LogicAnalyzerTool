# Temperature Scenario — HTS221 on UART

## What it does
Streams live temperature readings from the HTS221 sensor over USART3 (logic analyzer) and USART1 (VCP).

## UART format
```
TEMP=30.24
TEMP=30.26
TEMP=30.30
```
Output: `TEMP=<int>.<2-digit-frac>\r\n` @ 115200 8N1, ~2 Hz

## Expected values
- Room temperature: ~20–35°C depending on environment
- Values should drift slightly (±0.1°C between readings)
- Cover the sensor or breathe on it → temperature rises noticeably

## Physical test
Cover the HTS221 sensor with your finger or blow on it. TEMP should rise within 2-3 readings.

## BSP include
`#include "b_u585i_iot02a_env_sensors.h"`

## Key init pattern
```c
BSP_ENV_SENSOR_Init(0, ENV_TEMPERATURE)   // instance 0 = HTS221 on I2C2
BSP_ENV_SENSOR_Enable(0, ENV_TEMPERATURE) // must call Enable!
BSP_ENV_SENSOR_GetValue(0, ENV_TEMPERATURE, &float_val)
```

## Makefile additions
- `b_u585i_iot02a_env_sensors` to BSP_BASE
- `hts221 hts221_reg` to BSP_BASE

## Version history
- v1 (2026-04-18): Initial — integer formatting avoids newlib-nano %f linking issue
  - Binary MD5: `5551ffd7515835abc37b49f25fc6192`
