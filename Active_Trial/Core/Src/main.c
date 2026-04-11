/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Logic Analyzer UART4 Test Firmware for B-U585I-IOT02A
  *                   UART4 TX connected to logic analyzer
  *                   Baud: 115200 8N1
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include <stdio.h>
#include <string.h>

/* USER CODE BEGIN PD */
/* UART4 Configuration - UART4 is configured in CubeMX for this project */
#define UART4_BAUD             115200u
#define TEST_PATTERN_INTERVAL  100U    /* ms between test patterns */

/* Test pattern types for logic analyzer verification */
typedef enum {
    TEST_PATTERN_0x55 = 0,    /* 01010101 - alternating bits */
    TEST_PATTERN_0xAA = 1,    /* 10101010 - inverted alternating */
    TEST_PATTERN_0xFF = 2,    /* 11111111 - all ones */
    TEST_PATTERN_0x00 = 3,    /* 00000000 - all zeros */
    TEST_PATTERN_COUNTER = 4, /* 0x00-0xFF counter */
    TEST_PATTERN_ASCII = 5     /* ASCII string test */
} test_pattern_t;
/* USER CODE END PD */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN PV */
extern UART_HandleTypeDef huart4;  /* UART4 is already configured in main project */

static uint8_t rx_byte;
static test_pattern_t current_pattern = TEST_PATTERN_0x55;
static uint32_t last_pattern_tick = 0U;
static uint8_t test_counter = 0;
static char test_string[] = "LOGIC_ANALYZER_TEST\r\n";
static uint16_t test_string_idx = 0;
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
/* Note: SystemClock_Config, MX_GPIO_Init, MX_UART4_Init are defined in parent main.c */
/* USER CODE BEGIN PFP */
static void Send_Test_Pattern_0x55(void);
static void Send_Test_Pattern_0xAA(void);
static void Send_Test_Pattern_0xFF(void);
static void Send_Test_Pattern_0x00(void);
static void Send_Test_Pattern_Counter(void);
static void Send_Test_Pattern_ASCII(void);
static void Send_Test_Pattern(test_pattern_t pattern);
/* USER CODE END PFP */

/* USER CODE BEGIN 0 */

/* USER CODE END 0 */

int main(void)
{
  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* Configure the System Power */
  SystemPower_Config();

  /* Configure the system clock */
  SystemClock_Config();

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_UART4_Init();

  /* USER CODE BEGIN 2 */
  /* Send initial test message via UART4 */
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"Logic Analyzer UART4 Test\r\n", 27, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"B-U585I-IOT02A IoT Discovery\r\n", 31, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"115200 8N1\r\n", 13, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"Patterns: 0x55, 0xAA, 0xFF, 0x00, Counter, ASCII\r\n", 51, HAL_MAX_DELAY);
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    /* Poll for incoming UART RX byte (non-blocking) */
    if (HAL_UART_Receive(&huart4, &rx_byte, 1, 0) == HAL_OK)
    {
      /* Echo received byte */
      (void)HAL_UART_Transmit(&huart4, &rx_byte, 1, HAL_MAX_DELAY);

      /* Change pattern based on received command */
      if (rx_byte >= '0' && rx_byte <= '5')
      {
        current_pattern = (test_pattern_t)(rx_byte - '0');
      }
    }

    /* Send test pattern every TEST_PATTERN_INTERVAL ms */
    uint32_t now = HAL_GetTick();
    if ((now - last_pattern_tick) >= TEST_PATTERN_INTERVAL)
    {
      last_pattern_tick = now;
      Send_Test_Pattern(current_pattern);

      /* Cycle through patterns */
      current_pattern = (test_pattern_t)((current_pattern + 1) % 6);
    }
    /* USER CODE END 3 */
  }
}

/* USER CODE BEGIN 3 */
/**
  * @brief Send specific test pattern
  * @retval None
  */
static void Send_Test_Pattern(test_pattern_t pattern)
{
  switch (pattern)
  {
    case TEST_PATTERN_0x55:
      Send_Test_Pattern_0x55();
      break;
    case TEST_PATTERN_0xAA:
      Send_Test_Pattern_0xAA();
      break;
    case TEST_PATTERN_0xFF:
      Send_Test_Pattern_0xFF();
      break;
    case TEST_PATTERN_0x00:
      Send_Test_Pattern_0x00();
      break;
    case TEST_PATTERN_COUNTER:
      Send_Test_Pattern_Counter();
      break;
    case TEST_PATTERN_ASCII:
      Send_Test_Pattern_ASCII();
      break;
    default:
      break;
  }
}

static void Send_Test_Pattern_0x55(void)
{
  uint8_t data = 0x55;
  char header[] = "[0x55] ";
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)header, 7, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, &data, 1, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"\r\n", 2, HAL_MAX_DELAY);
}

static void Send_Test_Pattern_0xAA(void)
{
  uint8_t data = 0xAA;
  char header[] = "[0xAA] ";
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)header, 7, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, &data, 1, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"\r\n", 2, HAL_MAX_DELAY);
}

static void Send_Test_Pattern_0xFF(void)
{
  uint8_t data = 0xFF;
  char header[] = "[0xFF] ";
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)header, 7, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, &data, 1, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"\r\n", 2, HAL_MAX_DELAY);
}

static void Send_Test_Pattern_0x00(void)
{
  uint8_t data = 0x00;
  char header[] = "[0x00] ";
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)header, 7, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, &data, 1, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"\r\n", 2, HAL_MAX_DELAY);
}

static void Send_Test_Pattern_Counter(void)
{
  char header[] = "[CNT] ";
  char hex_str[3];
  uint8_t hi = (test_counter >> 4) & 0x0F;
  uint8_t lo = test_counter & 0x0F;

  hex_str[0] = (hi < 10) ? ('0' + hi) : ('A' + hi - 10);
  hex_str[1] = (lo < 10) ? ('0' + lo) : ('A' + lo - 10);
  hex_str[2] = '\0';

  (void)HAL_UART_Transmit(&huart4, (uint8_t *)header, 6, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)hex_str, 2, HAL_MAX_DELAY);
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)"\r\n", 2, HAL_MAX_DELAY);

  test_counter++;
}

static void Send_Test_Pattern_ASCII(void)
{
  char prefix[] = "[ASCII] ";
  (void)HAL_UART_Transmit(&huart4, (uint8_t *)prefix, 8, HAL_MAX_DELAY);

  /* Send one character at a time, wrap around */
  uint8_t c = (uint8_t)test_string[test_string_idx];
  (void)HAL_UART_Transmit(&huart4, &c, 1, HAL_MAX_DELAY);

  test_string_idx++;
  if (test_string_idx >= strlen(test_string))
  {
    test_string_idx = 0;
  }
}
/* USER CODE END 3 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  while (1)
  {
    /* Blink LED or toggle GPIO for error indication */
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  (void)file;
  (void)line;
}
#endif /* USE_FULL_ASSERT */
