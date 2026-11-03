#include "board_step_test.h"
#include "tim.h"

/*
 * TIM2和TIM4均配置为1MHz计数频率。
 * 每个计数单位对应1us。
 */
#define STEP_TEST_TIMER_HZ         1000000UL
#define STEP_TEST_PULSE_HIGH_US    10UL

/*
 * 主循环与中断共同访问的状态。
 *
 * volatile保证每次访问都读取/写入实际内存。
 * 它不能替代临界区；需要整体修改状态时，
 * 仍然通过短暂关闭中断保护。
 */
static volatile bool s_busy;
static volatile uint32_t s_sent_count;
static volatile uint32_t s_target_count;

/*
 * 仅在停止状态由主循环设置，
 * 在一次测试过程中不再改变。
 */
static board_axis_t s_axis;

/*
 * 停止一个测试定时器。
 *
 * 先禁止更新中断，再关闭计数器，
 * 最后清除遗留的更新标志。
 *
 * 本模块直接管理计数器和更新中断：
 * 不要再在其他地方对TIM2/TIM4调用Start_IT。
 */
static void step_test_timer_stop(TIM_HandleTypeDef *timer)
{
    __HAL_TIM_DISABLE_IT(timer, TIM_IT_UPDATE);
    __HAL_TIM_DISABLE(timer);
    __HAL_TIM_CLEAR_FLAG(timer, TIM_FLAG_UPDATE);
}

/*
 * 从完整的新周期开始运行一个定时器。
 *
 * UG的作用：
 * 1. 装载预分频器；
 * 2. 重置预分频计数相位；
 * 3. 让新ARR配置生效。
 *
 * UG也可能置位更新标志，因此必须在开启中断前清除，
 * 否则可能刚启动就错误地进入一次更新中断。
 */
static void step_test_timer_start(TIM_HandleTypeDef *timer, uint32_t period_us)
{
    step_test_timer_stop(timer);

    __HAL_TIM_SET_AUTORELOAD(timer, period_us - 1u);
    __HAL_TIM_SET_COUNTER(timer, 0u);

    timer->Instance->EGR = TIM_EGR_UG;

    __HAL_TIM_CLEAR_FLAG(timer, TIM_FLAG_UPDATE);
    __HAL_TIM_ENABLE_IT(timer, TIM_IT_UPDATE);
    __HAL_TIM_ENABLE(timer);
}

void board_step_test_init(void)
{
    board_step_test_stop();

    s_axis = BOARD_AXIS_X;
    s_sent_count = 0u;
    s_target_count = 0u;
}

bool board_step_test_is_busy(void)
{
    return s_busy;
}

uint32_t board_step_test_get_count(void)
{
    return s_sent_count;
}

void board_step_test_stop(void)
{
    uint32_t saved_primask;

    /*
     * 保存调用之前的中断屏蔽状态。
     * 不能退出时无条件开中断，否则可能破坏外层临界区。
     */
    saved_primask = __get_PRIMASK();
    __disable_irq();

    step_test_timer_stop(&htim2);
    step_test_timer_stop(&htim4);

    board_motor_enable(false);
    board_motor_clear_steps();

    s_busy = false;

    __set_PRIMASK(saved_primask);
}

bool board_step_test_start(board_axis_t axis, bool direction_high, uint32_t pulse_count, uint32_t frequency_hz, bool enable_driver)
{
    uint32_t period_us;

    if ((unsigned int)axis >= (unsigned int)BOARD_AXIS_COUNT)
    {
        return false;
    }

    if ((pulse_count == 0u) || (pulse_count > 10000u))
    {
        return false;
    }

    if ((frequency_hz < 10u) || (frequency_hz > 1000u))
    {
        return false;
    }

    if (s_busy)
    {
        return false;
    }

    /*
     * 当前只支持低速测试，频率转换采用整数除法。
     * 100Hz、500Hz均可以得到准确的整数周期。
     */
    period_us = STEP_TEST_TIMER_HZ / frequency_hz;

    step_test_timer_stop(&htim2);
    step_test_timer_stop(&htim4);

    /*
     * 改方向前先禁止驱动并清空STEP。
     * 此时没有正在进行的脉冲。
     */
    board_motor_enable(false);
    board_motor_clear_steps();
    board_motor_set_direction(axis, direction_high);

    /*
     * 给DIR充分的建立时间。
     * 这里只在主循环中延时，不在定时器中断中延时。
     */
    HAL_Delay(1u);

    s_axis = axis;
    s_sent_count = 0u;
    s_target_count = pulse_count;

    /*
     * 三颗驱动共用ENN：
     * true时三颗驱动都会使能，
     * 但只有被选中的轴会收到STEP。
     */
    board_motor_enable(enable_driver);

    /* 首次使能后留出建立时间，再开始输出脉冲。 */
    HAL_Delay(2u);

    s_busy = true;

    /*
     * 启动TIM2。
     * 第一个STEP将在一个完整周期后发出。
     */
    step_test_timer_start(&htim2, period_us);

    return true;
}

void board_step_test_tim2_irq(void)
{
    if (!s_busy)
    {
        step_test_timer_stop(&htim2);
        return;
    }

    /*
     * 一个TIM2更新事件对应一个STEP上升沿。
     * TMC2209收到这个上升沿后，前进一个外部微步。
     */
    board_motor_set_step(s_axis, true);

    ++s_sent_count;

    /*
     * 发出最后一个上升沿后，
     * 立即停止TIM2，避免产生第N+1个脉冲。
     *
     * 但此时STEP仍然是高电平，
     * 不能马上把整次测试标记为完成。
     */
    if (s_sent_count >= s_target_count)
    {
        step_test_timer_stop(&htim2);
    }

    /*
     * 启动TIM4，在约10us后结束本次高电平。
     *
     * 计数和最后一脉冲判断已经处理完，
     * 即使TIM4优先级较高，也不会读到未更新的计数。
     */
    step_test_timer_start(&htim4, STEP_TEST_PULSE_HIGH_US);
}

void board_step_test_tim4_irq(void)
{
    /*
     * 每次只需要一次脉冲结束事件。
     * 本次进中断后立即停止TIM4。
     */
    step_test_timer_stop(&htim4);

    if (!s_busy)
    {
        return;
    }

    board_motor_set_step(s_axis, false);

    /*
     * 必须等最后一个脉冲恢复低电平，
     * 才能宣布整次测试完成。
     */
    if (s_sent_count >= s_target_count)
    {
        s_busy = false;

        /*
         * 正常结束时不改变ENN。
         *
         * 波形测试：原本就是禁用，继续禁用。
         * 电机测试：保持使能，提供静止保持力矩。
         *
         * 用户发送s后才禁用全部驱动。
         */
    }
}
