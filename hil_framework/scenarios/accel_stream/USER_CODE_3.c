    /* USER CODE BEGIN 3 */
    /* Read accelerometer and stream over USART3 + USART1 (VCP) */
    if (accel_init_ok) {
      uint32_t now = HAL_GetTick();
      if ((now - accel_last_tick) >= ACCEL_STREAM_INTERVAL_MS) {
        accel_last_tick = now;

        if (BSP_MOTION_SENSOR_GetAxes(0, MOTION_ACCELERO, &accel_axes) == BSP_ERROR_NONE) {
          int len = snprintf(tx_buf, TX_BUF_SIZE,
            "AX=%d  AY=%d  AZ=%d\r\n",
            (int)accel_axes.xval, (int)accel_axes.yval, (int)accel_axes.zval);
          if (len > 0) {
            (void)HAL_UART_Transmit(&huart3, (uint8_t *)tx_buf, len, HAL_MAX_DELAY);
            (void)HAL_UART_Transmit(&huart1, (uint8_t *)tx_buf, len, HAL_MAX_DELAY);
          }
        }
      }
    }
    /* USER CODE END 3 */
