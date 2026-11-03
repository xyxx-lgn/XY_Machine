/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32g4xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

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
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define LCD_RST_Pin GPIO_PIN_13
#define LCD_RST_GPIO_Port GPIOC
#define X_STEP_Pin GPIO_PIN_0
#define X_STEP_GPIO_Port GPIOA
#define Y_STEP_Pin GPIO_PIN_1
#define Y_STEP_GPIO_Port GPIOA
#define Z_STEP_Pin GPIO_PIN_4
#define Z_STEP_GPIO_Port GPIOA
#define LCD_CLK_Pin GPIO_PIN_5
#define LCD_CLK_GPIO_Port GPIOA
#define Y_MS2_Pin GPIO_PIN_6
#define Y_MS2_GPIO_Port GPIOA
#define LCD_SDA_Pin GPIO_PIN_7
#define LCD_SDA_GPIO_Port GPIOA
#define TP_SCL_Pin GPIO_PIN_4
#define TP_SCL_GPIO_Port GPIOC
#define X_DIR_Pin GPIO_PIN_0
#define X_DIR_GPIO_Port GPIOB
#define Y_DIR_Pin GPIO_PIN_1
#define Y_DIR_GPIO_Port GPIOB
#define Z_DIR_Pin GPIO_PIN_2
#define Z_DIR_GPIO_Port GPIOB
#define ESPUSART_TX_Pin GPIO_PIN_10
#define ESPUSART_TX_GPIO_Port GPIOB
#define ESPUSART_RX_Pin GPIO_PIN_11
#define ESPUSART_RX_GPIO_Port GPIOB
#define LCD_CS_Pin GPIO_PIN_12
#define LCD_CS_GPIO_Port GPIOB
#define TP_RST_Pin GPIO_PIN_13
#define TP_RST_GPIO_Port GPIOB
#define SERVO_PWM_Pin GPIO_PIN_14
#define SERVO_PWM_GPIO_Port GPIOB
#define Z_MS1_Pin GPIO_PIN_15
#define Z_MS1_GPIO_Port GPIOB
#define Z_MS2_Pin GPIO_PIN_6
#define Z_MS2_GPIO_Port GPIOC
#define TP_SDA_Pin GPIO_PIN_8
#define TP_SDA_GPIO_Port GPIOA
#define LCD_BL_Pin GPIO_PIN_9
#define LCD_BL_GPIO_Port GPIOA
#define Z_HOME_Pin GPIO_PIN_10
#define Z_HOME_GPIO_Port GPIOA
#define Z_HOME_EXTI_IRQn EXTI15_10_IRQn
#define TP_INT_Pin GPIO_PIN_11
#define TP_INT_GPIO_Port GPIOA
#define TP_INT_EXTI_IRQn EXTI15_10_IRQn
#define ENN_Pin GPIO_PIN_12
#define ENN_GPIO_Port GPIOA
#define Y_HOME_Pin GPIO_PIN_15
#define Y_HOME_GPIO_Port GPIOA
#define Y_HOME_EXTI_IRQn EXTI15_10_IRQn
#define TMCUART_TX_Pin GPIO_PIN_10
#define TMCUART_TX_GPIO_Port GPIOC
#define TMCUART_RX_Pin GPIO_PIN_11
#define TMCUART_RX_GPIO_Port GPIOC
#define Y_MS1_Pin GPIO_PIN_3
#define Y_MS1_GPIO_Port GPIOB
#define LASER_PWM_Pin GPIO_PIN_4
#define LASER_PWM_GPIO_Port GPIOB
#define X_HOME_Pin GPIO_PIN_5
#define X_HOME_GPIO_Port GPIOB
#define X_HOME_EXTI_IRQn EXTI9_5_IRQn
#define X_MS1_Pin GPIO_PIN_6
#define X_MS1_GPIO_Port GPIOB
#define X_MS2_Pin GPIO_PIN_7
#define X_MS2_GPIO_Port GPIOB
#define LCD_DC_Pin GPIO_PIN_9
#define LCD_DC_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
