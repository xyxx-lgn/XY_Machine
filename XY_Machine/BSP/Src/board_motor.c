#include "board_motor.h"
#include "main.h"
#include <stddef.h> // 使用NULL。

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



/*
 * 复用已经通过自检的TMC2209配置流程。
 * 只负责配置驱动，不启动步进定时器，也不使能电机输出。
 */
tmc2209_result_t board_motor_configure_drivers(uint8_t *failed_address)
{
    static const uint8_t irun_table[3] = {17u, 17u, 11u};
    static const uint8_t ihold_table[3] = {8u, 8u, 5u};

    uint8_t address;
    tmc2209_result_t result;

    // 三轴共用ENN，配置期间始终禁止驱动输出。
    board_motor_enable(false);
    board_motor_clear_steps();

    // 0xFF表示当前没有记录失败的驱动地址。
    if (failed_address != NULL)
    {
        *failed_address = 0xFFu;
    }

    // 先为三颗驱动设置回复延时，保持与原来验证成功的顺序一致。
    for (address = 0u; address < 3u; ++address)
    {
        result = tmc2209_set_send_delay(address);

        if (result != TMC2209_OK)
        {
            goto failed;
        }
    }

    // 地址0、1、2分别对应X、Y、Z。
    for (address = 0u; address < 3u; ++address)
    {
        // 通过寄存器选择16细分，底层函数会进行配置验证。
        result = tmc2209_set_microstep_16(address);

        if (result != TMC2209_OK)
        {
            goto failed;
        }

        // 使用内部参考、外部0.1欧姆采样电阻以及已验证的初期电流设置。
        result = tmc2209_set_test_current(address, irun_table[address], ihold_table[address]);

        if (result != TMC2209_OK)
        {
            goto failed;
        }
    }

    // 配置成功也不在这里使能，后续由运动控制模块决定何时使能。
    return TMC2209_OK;

failed:
    // 任意一颗配置失败，三轴继续保持关闭。
    board_motor_enable(false);
    board_motor_clear_steps();

    if (failed_address != NULL)
    {
        *failed_address = address;
    }

    return result;
}


