#include "board_motor.h"
#include "main.h"

/*
 * 使用CubeMX在main.h中生成的引脚宏，也可参考board_motor.h文件中的解释。
 * 不在这里重新写GPIO_PIN_0等具体编号，
 * 以后调整引脚时更容易保持一致。
 */

void board_motor_enable(bool enable)
{
    /*
     * TMC2209的ENN低有效：
     * 要使能驱动，实际输出低电平。
     */
    HAL_GPIO_WritePin(ENN_GPIO_Port , ENN_Pin , enable ? GPIO_PIN_RESET : GPIO_PIN_SET);
}

void board_motor_set_direction(board_axis_t axis, bool high)
{
    GPIO_PinState level = high ? GPIO_PIN_SET : GPIO_PIN_RESET;

    switch (axis)
    {
        case BOARD_AXIS_X:
            HAL_GPIO_WritePin(X_DIR_GPIO_Port, X_DIR_Pin, level);
            break;

        case BOARD_AXIS_Y:
            HAL_GPIO_WritePin(Y_DIR_GPIO_Port, Y_DIR_Pin, level);
            break;

        case BOARD_AXIS_Z:
            HAL_GPIO_WritePin(Z_DIR_GPIO_Port, Z_DIR_Pin, level);
            break;

        default:
            /* 非法轴编号不操作任何引脚。 */
            break;
    }
}

void board_motor_set_step(board_axis_t axis, bool high)
{
    GPIO_PinState level = high ? GPIO_PIN_SET : GPIO_PIN_RESET;

    switch (axis)
    {
        case BOARD_AXIS_X:
            HAL_GPIO_WritePin(X_STEP_GPIO_Port, X_STEP_Pin, level);
            break;

        case BOARD_AXIS_Y:
            HAL_GPIO_WritePin(Y_STEP_GPIO_Port, Y_STEP_Pin, level);
            break;

        case BOARD_AXIS_Z:
            HAL_GPIO_WritePin(Z_STEP_GPIO_Port, Z_STEP_Pin, level);
            break;

        default:
            break;
    }
}

void board_motor_clear_steps(void)
{
    board_motor_set_step(BOARD_AXIS_X, false);
    board_motor_set_step(BOARD_AXIS_Y, false);
    board_motor_set_step(BOARD_AXIS_Z, false);
}

void board_motor_init(void)
{
    /*
     * 第一件事就是禁止驱动输出。
     * PCB还应有ENN外部上拉，
     * 软件不能替代MCU启动前的硬件默认状态。
     */
    board_motor_enable(false);

    board_motor_clear_steps();

    board_motor_set_direction(BOARD_AXIS_X, false);
    board_motor_set_direction(BOARD_AXIS_Y, false);
    board_motor_set_direction(BOARD_AXIS_Z, false);

    /*
     * 设置UART地址：
     * 地址 = MS1_AD0 + 2 * MS2_AD1
     *
     * X：MS1=0，MS2=0 -> 地址0
     */
    HAL_GPIO_WritePin(X_MS1_GPIO_Port, X_MS1_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(X_MS2_GPIO_Port, X_MS2_Pin, GPIO_PIN_RESET);

    /* Y：MS1=1，MS2=0 -> 地址1 */
    HAL_GPIO_WritePin(Y_MS1_GPIO_Port, Y_MS1_Pin, GPIO_PIN_SET);
    HAL_GPIO_WritePin(Y_MS2_GPIO_Port, Y_MS2_Pin, GPIO_PIN_RESET);

    /* Z：MS1=0，MS2=1 -> 地址2 */
    HAL_GPIO_WritePin(Z_MS1_GPIO_Port, Z_MS1_Pin, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(Z_MS2_GPIO_Port, Z_MS2_Pin, GPIO_PIN_SET);
}
