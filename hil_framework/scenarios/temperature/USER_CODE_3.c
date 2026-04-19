    /* USER CODE BEGIN 3 */
    /* Read HTS221 temperature and stream over USART3 + USART1 (VCP) */
    if (temp_init_ok) {
      uint32_t now = HAL_GetTick();
      if ((now - temp_last_tick) >= TEMP_STREAM_INTERVAL_MS) {
        temp_last_tick = now;

        if (BSP_ENV_SENSOR_GetValue(0, ENV_TEMPERATURE, &temperature) == BSP_ERROR_NONE) {
          int int_part = (int)temperature;
          int frac_part = (int)((temperature - int_part) * 100.0f);
          if (frac_part < 0) frac_part = -frac_part;
          int len = snprintf(tx_buf, TX_BUF_SIZE,
            "TEMP=%d.%02d\r\n", int_part, frac_part);
          if (len > 0) {
            (void)HAL_UART_Transmit(&huart3, (uint8_t *)tx_buf, len, HAL_MAX_DELAY);
            (void)HAL_UART_Transmit(&huart1, (uint8_t *)tx_buf, len, HAL_MAX_DELAY);
          }
        }
      }
    }
    /* USER CODE END 3 */
