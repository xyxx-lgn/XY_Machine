#ifndef BOARD_STEP_TEST_H
#define BOARD_STEP_TEST_H

#include "board_motor.h"

#include <stdbool.h>
#include <stdint.h>

/*
 * 初始化测试模块。
 *
 * 必须在以下初始化完成之后调用：
 * 1. MX_GPIO_Init()
 * 2. MX_TIM2_Init()
 * 3. MX_TIM4_Init()
 *
 * 初始化后：
 * 定时器停止，STEP为低，驱动禁用。
 */
void board_step_test_init(void);

/*
 * 启动一次单轴、固定频率、固定脉冲数量的测试。
 *
 * axis：
 *     选择X、Y或Z。
 *
 * direction_high：
 *     DIR引脚电平，不直接代表机械正方向。
 *
 * pulse_count：
 *     本次发出的STEP上升沿数量，范围1~10000。
 *
 * frequency_hz：
 *     STEP频率，范围10~1000Hz。
 *
 * enable_driver：
 *     false：ENN保持高，只测试波形。
 *     true ：ENN拉低，允许电机实际转动。
 *
 * 返回：
 *     true：启动成功。
 *     false：参数不正确或上一次测试尚未结束。
 *
 * 本函数只在主循环调用，不在中断中调用。
 */
bool board_step_test_start(board_axis_t axis, bool direction_high, uint32_t pulse_count, uint32_t frequency_hz, bool enable_driver);

/* 是否仍在输出本次脉冲。 */
bool board_step_test_is_busy(void);

/* 已经输出的STEP上升沿数量。 */
uint32_t board_step_test_get_count(void);

/*
 * 立即停止脉冲并禁用全部驱动。
 *
 * 这是测试停止功能，不是带减速规划的进给暂停。
 */
void board_step_test_stop(void);

/* 分别由TIM2、TIM4的更新中断回调调用。 */
void board_step_test_tim2_irq(void);
void board_step_test_tim4_irq(void);

#endif
