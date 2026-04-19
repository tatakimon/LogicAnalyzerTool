  /* USER CODE BEGIN 2 */
  /* ISM330DHCX init banner — USART3 (logic analyzer) + USART1 (VCP) */
  const char *banner[] = {
    "\r\n",
    "  I S M 3 3 0 D H C X  A C C E L E R O M E T E R\r\n",
    "  ====================================================\r\n",
    "  Board : B-U585I-IOT02A\r\n",
    "  USART3: PD8 logic analyzer @ 115200 8N1\r\n",
    "  USART1: PA9 VCP        @ 115200 8N1\r\n",
    "  Sensor: ISM330DHCX on I2C2 @ 104 kHz\r\n",
    "  ====================================================\r\n",
  };
  for (size_t i = 0; i < sizeof(banner)/sizeof(banner[0]); i++) {
    size_t len = 0; while (banner[i][len]) len++;
    (void)HAL_UART_Transmit(&huart3, (uint8_t *)banner[i], len, HAL_MAX_DELAY);
    (void)HAL_UART_Transmit(&huart1, (uint8_t *)banner[i], len, HAL_MAX_DELAY);
  }
  /* Init ISM330DHCX accelerometer — instance 0, accel function */
  if (BSP_MOTION_SENSOR_Init(0, MOTION_ACCELERO) == BSP_ERROR_NONE) {
    /* ISM330DHCX_Init leaves accel in power-down — must explicitly enable */
    if (BSP_MOTION_SENSOR_Enable(0, MOTION_ACCELERO) == BSP_ERROR_NONE) {
      const char *ok = "  [OK] ISM330DHCX ready on I2C2\r\n\r\n";
      size_t len = 0; while (ok[len]) len++;
      (void)HAL_UART_Transmit(&huart3, (uint8_t *)ok, len, HAL_MAX_DELAY);
      (void)HAL_UART_Transmit(&huart1, (uint8_t *)ok, len, HAL_MAX_DELAY);
      accel_init_ok = 1;
    } else {
      const char *fail = "  [FAIL] ISM330DHCX enable failed\r\n\r\n";
      size_t len = 0; while (fail[len]) len++;
      (void)HAL_UART_Transmit(&huart3, (uint8_t *)fail, len, HAL_MAX_DELAY);
      (void)HAL_UART_Transmit(&huart1, (uint8_t *)fail, len, HAL_MAX_DELAY);
    }
  } else {
    const char *fail = "  [FAIL] ISM330DHCX init failed\r\n\r\n";
    size_t len = 0; while (fail[len]) len++;
    (void)HAL_UART_Transmit(&huart3, (uint8_t *)fail, len, HAL_MAX_DELAY);
    (void)HAL_UART_Transmit(&huart1, (uint8_t *)fail, len, HAL_MAX_DELAY);
  }
  /* USER CODE END 2 */
