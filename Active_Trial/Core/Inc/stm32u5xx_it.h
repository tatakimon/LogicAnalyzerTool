/* USER CODE BEGIN 0 */
/**
  * @brief This file contains the headers of the interrupt handlers for STM32U5xx.
  ******************************************************************************
  */
/* USER CODE END 0 */

#ifndef __STM32U5xx_IT_H
#define __STM32U5xx_IT_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* USER CODE END 0 */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */
/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */
/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */
/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
/* USER CODE BEGIN 0 */
/* USER CODE END 0 */

void NMI_Handler(void);
void HardFault_Handler(void);
void MemManage_Handler(void);
void BusFault_Handler(void);
void UsageFault_Handler(void);
void SecureFault_Handler(void);
void SVC_Handler(void);
void DebugMon_Handler(void);
void PendSV_Handler(void);
void SysTick_Handler(void);

/* USER CODE BEGIN 1 */
/* USER CODE END 1 */

#ifdef __cplusplus
}
#endif

#endif /* __STM32U5xx_IT_H */
