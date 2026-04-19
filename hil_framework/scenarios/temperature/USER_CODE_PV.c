/* USER CODE BEGIN PV */
/* HTS221 Temperature Sensor — live temperature over USART3 + USART1 (VCP) */
#define TEMP_STREAM_INTERVAL_MS  500U  /* ~2 Hz output */
#define TX_BUF_SIZE              128U

static uint32_t temp_last_tick = 0U;
static int32_t temp_init_ok = 0;
static float temperature = 0.0f;
static char tx_buf[TX_BUF_SIZE];
/* USER CODE END PV */
