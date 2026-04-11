# TRIAL_LOG - Logic Analyzer Project Development Log

## Format
```
- [YYYY-MM-DD HH:MM] vX.Y.Z : Description : Result (PASS/FAIL) : Notes.
```

---

## 2026-04-09

- [2026-04-09 15:00] v0.3.0 : Migrated to active_trial_1 (clean CubeMX project) : DONE : USART3 test patterns added to active_trial_1/Core/Src/main.c USER CODE blocks. PD8/PD9 GPIO config (AF7), clock init, banner messages, main loop with 6 test patterns. No HAL_Delay used. Handle name: huart3. Init function: MX_USART3_UART_Init(). Build via CubeIDE or `make -C active_trial_1/Debug all`.

- [2026-04-09 14:30] v0.2.0 : Switch from UART4 to USART3 for logic analyzer : DONE : Added USART3 to logic_analyzer.ioc (PD8/PD9, 115200 8N1). Updated main.c USER CODE blocks.

- [2026-04-09 HH:MM] v0.1.0 : Initial project setup for B-U585I-IOT02A : DONE : Created Active_Trial structure with CLAUDE.md, main.c, HAL drivers.

---

*Last updated: 2026-04-09*
