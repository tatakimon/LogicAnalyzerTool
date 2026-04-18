/* USER CODE BEGIN Includes */
#include "b_u585i_iot02a_motion_sensors.h"
/* USER CODE END Includes */

/* USER CODE BEGIN PV */
/* ISM330DHCX Accelerometer — live axes over USART3 */
#define ACCEL_STREAM_INTERVAL_MS  100U  /* ~10 Hz output */
#define TX_BUF_SIZE               128U

static uint32_t accel_last_tick = 0U;
static int32_t accel_init_ok = 0;
static BSP_MOTION_SENSOR_Axes_t accel_axes;
static char tx_buf[TX_BUF_SIZE];
/* USER CODE END PV */
