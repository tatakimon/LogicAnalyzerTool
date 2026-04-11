/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    stm32u5xx_hal_msp.c
  * @brief   HAL MSP module.
  ******************************************************************************
  */
/* USER CODE END Header */

#include "main.h"

/* USER CODE BEGIN 0 */
/* USER CODE END 0 */

/* External variables --------------------------------------------------------*/
/* USER CODE BEGIN EV */
/* USER CODE END EV */

/**
  * @brief UART4 MSP Initialization
  *        This function configures the hardware resources used in this project:
  *           - Peripheral's clock enable
  *           - Peripheral's GPIO Configuration
  * @param huart: UART handle pointer
  * @retval None
  */
void HAL_UART_MspInit(UART_HandleTypeDef* huart)
{
  /* USER CODE BEGIN UART4_MspInit 0 */

  /* USER CODE END UART4_MspInit 0 */
  if (huart->Instance == UART4)
  {
    /* USER CODE BEGIN UART4_MspInit 1 */

    /* USER CODE END UART4_MspInit 1 */
    /* UART4 clock enable */
    __HAL_RCC_UART4_CLK_ENABLE();

    /* UART4 GPIO Configuration
     * Note: actual pins depend on CubeMX configuration
     * Typical for B-U585I-IOT02A: check logic_analyzer.ioc for UART4 pins
     */
    /* USER CODE BEGIN UART4_MspInit 2 */

    /* USER CODE END UART4_MspInit 2 */
  }
  /* USER CODE BEGIN UART4_MspInit_Other */

  /* USER CODE END UART4_MspInit_Other */
}

/**
  * @brief UART4 MSP De-Initialization
  * @param huart: UART handle pointer
  * @retval None
  */
void HAL_UART_MspDeInit(UART_HandleTypeDef* huart)
{
  /* USER CODE BEGIN UART4_MspDeInit 0 */

  /* USER CODE END UART4_MspDeInit 0 */
  if (huart->Instance == UART4)
  {
    /* USER CODE BEGIN UART4_MspDeInit 1 */

    /* USER CODE END UART4_MspDeInit 1 */
    /* UART4 clock disable */
    __HAL_RCC_UART4_CLK_DISABLE();
    /* USER CODE BEGIN UART4_MspDeInit 2 */

    /* USER CODE END UART4_MspDeInit 2 */
  }
  /* USER CODE BEGIN UART4_MspDeInit_Other */

  /* USER CODE END UART4_MspDeInit_Other */
}

/* USER CODE BEGIN 1 */

/* USER CODE END 1 */
