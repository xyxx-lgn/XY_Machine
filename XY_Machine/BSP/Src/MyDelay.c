#include "MyDelay.h"

static uint8_t s_dwt_initialized = 0u;

void MyDelay_Init(void)
{
  if (s_dwt_initialized != 0u) {
    return;
  }

  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CYCCNT = 0u;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  s_dwt_initialized = 1u;
}

void MyDelay_us(uint32_t us)
{
  uint32_t start;
  uint32_t cycles;
  uint32_t cycles_per_us;

  MyDelay_Init();

  cycles_per_us = SystemCoreClock / 1000000u;

  if (cycles_per_us == 0u) {
    cycles_per_us = 1u;
  }

  cycles = us * cycles_per_us;
  start = DWT->CYCCNT;

  while ((uint32_t)(DWT->CYCCNT - start) < cycles) {
  }
}

void MyDelay_ms(uint32_t ms)
{
  HAL_Delay(ms);
}
