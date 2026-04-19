/* USER CODE BEGIN 2 */
  /* HTS221 init banner — USART3 (logic analyzer) + USART1 (VCP) */
  const char *banner[] = {
    "\r\n",
    "  H T S 2 2 1  T E M P E R A T U R E  S E N S O R\r\n",
    "  ====================================================\r\n",
    "  Board : B-U585I-IOT02A\r\n",
    "  USART3: PD8 logic analyzer @ 115200 8N1\r\n",
    "  USART1: PA9 VCP        @ 115200 8N1\r\n",
    "  Sensor: HTS221 on I2C2 @ 104 kHz\r\n",
    "  ====================================================\r\n",
  };
  for (size_t i = 0; i < sizeof(banner)/sizeof(banner[0]); i++) {
    size_t len = 0; while (banner[i][len]) len++;
    (void)HAL_UART_Transmit(&huart3, (uint8_t *)banner[i], len, HAL_MAX_DELAY);
    (void)HAL_UART_Transmit(&huart1, (uint8_t *)banner[i], len, HAL_MAX_DELAY);
  }
  /* Init HTS221 temperature sensor — instance 0, ENV_TEMPERATURE */
  if (BSP_ENV_SENSOR_Init(0, ENV_TEMPERATURE) == BSP_ERROR_NONE) {
    if (BSP_ENV_SENSOR_Enable(0, ENV_TEMPERATURE) == BSP_ERROR_NONE) {
      const char *ok = "  [OK] HTS221 ready on I2C2\r\n\r\n";
      size_t len = 0; while (ok[len]) len++;
      (void)HAL_UART_Transmit(&huart3, (uint8_t *)ok, len, HAL_MAX_DELAY);
      (void)HAL_UART_Transmit(&huart1, (uint8_t *)ok, len, HAL_MAX_DELAY);
      temp_init_ok = 1;
    } else {
      const char *fail = "  [FAIL] HTS221 enable failed\r\n\r\n";
      size_t len = 0; while (fail[len]) len++;
      (void)HAL_UART_Transmit(&huart3, (uint8_t *)fail, len, HAL_MAX_DELAY);
      (void)HAL_UART_Transmit(&huart1, (uint8_t *)fail, len, HAL_MAX_DELAY);
    }
  } else {
    const char *fail = "  [FAIL] HTS221 init failed\r\n\r\n";
    size_t len = 0; while (fail[len]) len++;
    (void)HAL_UART_Transmit(&huart3, (uint8_t *)fail, len, HAL_MAX_DELAY);
    (void)HAL_UART_Transmit(&huart1, (uint8_t *)fail, len, HAL_MAX_DELAY);
  }
  /* USER CODE END 2 */
