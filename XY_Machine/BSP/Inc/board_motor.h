#ifndef BOARD_MOTOR_H
#define BOARD_MOTOR_H

#include <stdbool.h>
#include <stdint.h>

#include "tmc2209.h" // 使用TMC2209通信结果类型



/*
	引脚宏定义，如果引脚有修改在main.h和此处解释修改，
	函数内部不再使用GPIO_PIN_0这种具体引脚号
*/

/*

//TMC2209的三电机使能脚宏定义
#define ENN_Pin 								GPIO_PIN_12
#define ENN_GPIO_Port 					GPIOA

//Motor宏定义
#define X_STEP_Pin              GPIO_PIN_0
#define X_STEP_GPIO_Port        GPIOA
#define Y_STEP_Pin              GPIO_PIN_1
#define Y_STEP_GPIO_Port        GPIOA
#define Z_STEP_Pin              GPIO_PIN_4
#define Z_STEP_GPIO_Port        GPIOA
#define X_DIR_Pin 							GPIO_PIN_0
#define X_DIR_GPIO_Port 				GPIOB
#define Y_DIR_Pin 							GPIO_PIN_1
#define Y_DIR_GPIO_Port 				GPIOB
#define Z_DIR_Pin 							GPIO_PIN_2
#define Z_DIR_GPIO_Port 				GPIOB
#define X_MS1_Pin 							GPIO_PIN_6
#define X_MS1_GPIO_Port 				GPIOB
#define X_MS2_Pin 							GPIO_PIN_7
#define X_MS2_GPIO_Port 				GPIOB
#define Y_MS1_Pin 							GPIO_PIN_3
#define Y_MS1_GPIO_Port 				GPIOB
#define Y_MS2_Pin 							GPIO_PIN_6
#define Y_MS2_GPIO_Port					GPIOA
#define Z_MS1_Pin 							GPIO_PIN_15
#define Z_MS1_GPIO_Port 				GPIOB
#define Z_MS2_Pin 							GPIO_PIN_6
#define Z_MS2_GPIO_Port 				GPIOC
#define X_HOME_Pin 							GPIO_PIN_5
#define X_HOME_GPIO_Port 				GPIOB
#define X_HOME_EXTI_IRQn 				EXTI9_5_IRQn
#define Y_HOME_Pin 							GPIO_PIN_15
#define Y_HOME_GPIO_Port 				GPIOA
#define Y_HOME_EXTI_IRQn 				EXTI15_10_IRQn
#define Z_HOME_Pin 							GPIO_PIN_10
#define Z_HOME_GPIO_Port 				GPIOA
#define Z_HOME_EXTI_IRQn 				EXTI15_10_IRQn

//TMC2209串口宏定义
#define TMCUART_TX_Pin 					GPIO_PIN_10
#define TMCUART_TX_GPIO_Port 		GPIOC
#define TMCUART_RX_Pin 					GPIO_PIN_11
#define TMCUART_RX_GPIO_Port 		GPIOC


//屏幕宏定义
#define LCD_RST_Pin             GPIO_PIN_13
#define LCD_RST_GPIO_Port       GPIOC
#define LCD_CLK_Pin             GPIO_PIN_5
#define LCD_CLK_GPIO_Port 			GPIOA
#define LCD_SDA_Pin 						GPIO_PIN_7
#define LCD_SDA_GPIO_Port 			GPIOA
#define LCD_CS_Pin 							GPIO_PIN_12
#define LCD_CS_GPIO_Port 				GPIOB
#define LCD_BL_Pin 							GPIO_PIN_9
#define LCD_BL_GPIO_Port 				GPIOA
#define LCD_DC_Pin 							GPIO_PIN_9
#define LCD_DC_GPIO_Port 				GPIOB

#define TP_SCL_Pin 							GPIO_PIN_4   //触摸屏IIC宏定义和EEPROM共用一个
#define TP_SCL_GPIO_Port 				GPIOC
#define TP_SDA_Pin 							GPIO_PIN_8
#define TP_SDA_GPIO_Port 				GPIOA
#define TP_RST_Pin				 			GPIO_PIN_13
#define TP_RST_GPIO_Port 				GPIOB
#define TP_INT_Pin 							GPIO_PIN_11
#define TP_INT_GPIO_Port 				GPIOA
#define TP_INT_EXTI_IRQn 				EXTI15_10_IRQn

//舵机PWM引脚宏定义
#define SERVO_PWM_Pin 					GPIO_PIN_14
#define SERVO_PWM_GPIO_Port 		GPIOB

//激光PWM宏定义
#define LASER_PWM_Pin 					GPIO_PIN_4
#define LASER_PWM_GPIO_Port 		GPIOB

//ESP32通信串口宏定义
#define ESPUSART_TX_Pin 				GPIO_PIN_10
#define ESPUSART_TX_GPIO_Port 	GPIOB
#define ESPUSART_RX_Pin 				GPIO_PIN_11
#define ESPUSART_RX_GPIO_Port 	GPIOB

*/


/*
 * 逻辑轴编号。
 *
 * 这里描述“哪个轴”，不描述STM32的具体引脚。
 * 以后上层代码使用BOARD_AXIS_X，不必知道X_STEP是PA0。
 */
typedef enum
{
    BOARD_AXIS_X = 0,
    BOARD_AXIS_Y,
    BOARD_AXIS_Z,
    BOARD_AXIS_COUNT
} board_axis_t;

/*
 * 板级安全初始化：
 * 1. 禁用三颗驱动；
 * 2. STEP全部置低；
 * 3. DIR设为已知状态；
 * 4. 设置三颗TMC2209的UART地址。
 *
 * 必须在MX_GPIO_Init()之后调用。
 */
void board_motor_init(void);

/*
 * 配置本板三颗TMC2209：通信回复延时、16细分和初期运行电流。
 * 调用前必须完成外设初始化及board_motor_init()，整个配置过程保持ENN关闭。
 * failed_address可传NULL；非NULL时，失败返回对应驱动地址。
 */
tmc2209_result_t board_motor_configure_drivers(uint8_t *failed_address);

/*
 * 三轴共用ENN：
 * enable=true  -> ENN低，允许驱动输出；
 * enable=false -> ENN高，禁止驱动输出。
 */
void board_motor_enable(bool enable);

/*
 * 设置某一轴的DIR电平。
 *
 * high仅表示引脚高低，不直接称作“正转/反转”。
 * 实际机械正方向要结合电机接线确定。
 */
void board_motor_set_direction(board_axis_t axis, bool high);

/*
 * 设置某一轴STEP电平。
 *
 * false -> 低电平；
 * true  -> 高电平。
 *
 * 本函数只操作GPIO，不负责脉宽、频率和步数。
 */
void board_motor_set_step(board_axis_t axis, bool high);

/* 将三轴STEP全部恢复为低电平。 */
void board_motor_clear_steps(void);

#endif
