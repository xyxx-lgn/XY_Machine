#include "app.h"
#include "board_motor.h"
#include "board_serial.h"
#include "main.h"
#include "tmc2209.h"
#include "board_step_test.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

/*
 * 只有三颗驱动全部完成测试电流配置，
 * 才允许执行带电转动命令。
 *
 * 这是软件流程标志，不是实际电流检测结果。
 */
static bool s_current_ready = false;

/* 用于主循环检测并报告一次脉冲测试完成。 */
static bool s_step_test_active = false;

/*
 * 这三个地址必须与board_motor_init()
 * 对MS1/MS2的实际设置一致。
 */
static tmc2209_device_t s_tmc_x = {
    .address = 0u,
    .online = false,
    .ioin = 0u
};

static tmc2209_device_t s_tmc_y = {
    .address = 1u,
    .online = false,
    .ioin = 0u
};

static tmc2209_device_t s_tmc_z = {
    .address = 2u,
    .online = false,
    .ioin = 0u
};

/*
 * 保存本课设置的方向电平。
 * false表示DIR低，true表示DIR高。
 */
static bool s_dir_high[BOARD_AXIS_COUNT];

/* 打印命令帮助。 */
static void app_print_help(void)
{
    board_serial_write(
        "\r\n"
        "Commands:\r\n"
        "  h : help\r\n"
        "  ? : show HOME raw levels\r\n"
        "  x : toggle X DIR\r\n"
        "  y : toggle Y DIR\r\n"
        "  z : toggle Z DIR\r\n"
        "  1 : one X STEP pulse, driver disabled\r\n"
        "  2 : one Y STEP pulse, driver disabled\r\n"
        "  3 : one Z STEP pulse, driver disabled\r\n"
        "  s : disable drivers and clear STEP\r\n"
				"  t : read IOIN from three TMC2209 drivers\r\n"
				"  c : configure test currents, keep drivers disabled\r\n"
				"  4/5/6 : X/Y/Z waveform test, 100 pulses, drivers disabled\r\n"
        "  a/b : X motor test, DIR low/high, 320 pulses\r\n"
        "  d/e : Y motor test, DIR low/high, 320 pulses\r\n"
        "  f/g : Z motor test, DIR low/high, 320 pulses\r\n"
				"  v : show USART2 RX statistics\r\n"
        "  u : RX test byte, no action\r\n"
    );
}

/*
 * 读取原点开关的原始电平。
 *
 * 本课暂不做GRBL意义上的有效电平转换。
 * 先确认“实际开关”和“程序读到的引脚”一一对应。
 */
static void app_print_home_levels(void)
{
    unsigned int x;
    unsigned int y;
    unsigned int z;
    char message[80];

    x = (HAL_GPIO_ReadPin(X_HOME_GPIO_Port, X_HOME_Pin) == GPIO_PIN_SET) ? 1u : 0u;

    y = (HAL_GPIO_ReadPin(Y_HOME_GPIO_Port, Y_HOME_Pin) == GPIO_PIN_SET) ? 1u : 0u;

    z = (HAL_GPIO_ReadPin(Z_HOME_GPIO_Port, Z_HOME_Pin) == GPIO_PIN_SET) ? 1u : 0u;

    /*
     * 只打印整数，不需要开启浮点printf支持。
     * 当前格式的最长输出远小于message容量。
     */
    (void)snprintf(message,sizeof(message),"HOME RAW: X=%u Y=%u Z=%u\r\n",x, y, z);

    board_serial_write(message);
}

static void app_toggle_direction(board_axis_t axis)
{
    char message[48];
    char axis_name;

    if ((unsigned int)axis >=(unsigned int)BOARD_AXIS_COUNT)
    {
        return;
    }

    /*
     * 本课不允许使能驱动。
     * 改变DIR前仍明确保持禁用。
     */
    board_motor_enable(false);

    s_dir_high[axis] = !s_dir_high[axis];

    board_motor_set_direction(axis,s_dir_high[axis]);

    axis_name = (char)('X' + (int)axis);

    (void)snprintf(message,sizeof(message),"%c DIR=%u\r\n",axis_name,s_dir_high[axis] ? 1u : 0u);

    board_serial_write(message);
}

/*
 * 输出一个方便观察的GPIO测试脉冲。
 *
 * 重要：
 * 1. ENN始终为高，电机不应该转动；
 * 2. 1ms只是方便逻辑分析仪观察；
 * 3. 这不是最终步进驱动实现；
 * 4. 后续由TIM2/TIM4生成真实运动脉冲，
 *    不再使用HAL_Delay控制步进时序。
 */
static void app_test_one_pulse(board_axis_t axis)
{
    board_motor_enable(false);
    board_motor_clear_steps();

    board_motor_set_step(axis, true);
    HAL_Delay(1u);
    board_motor_set_step(axis, false);

    board_serial_write("STEP pulse sent; ENN remains HIGH.\r\n");
}

void app_init(void)
{
    unsigned int i;

    board_motor_init();
    board_step_test_init();

    s_current_ready = false;
    s_step_test_active = false;

    for (i = 0u; i < (unsigned int)BOARD_AXIS_COUNT; ++i)
    {
        s_dir_high[i] = false;
    }

    /*
     * 屏幕暂时不用。
     * CS保持高，避免误选中。
     */
    HAL_GPIO_WritePin(LCD_CS_GPIO_Port, LCD_CS_Pin, GPIO_PIN_SET);

    /*
     * CubeMX已初始化USART2硬件。
     * 这里启动软件接收缓冲区和中断接收。
     *
     * 必须在启动信息打印前调用，
     * 这样打印期间收到的数据也能暂存。
     */
    if (!board_serial_init())
    {
        board_serial_write("USART2 RX initialization failed.\r\n");
    }

    board_serial_write(
        "\r\n"
        "XY_MACHINE: BOARD SELF TEST\r\n"
        "Drivers disabled: ENN=HIGH\r\n"
        "MS address GPIO configured: X=0 Y=1 Z=2\r\n"
        "TMC UART communication has NOT been tested yet.\r\n"
    );

    app_print_help();
    app_print_home_levels();
}

/*
 * 探测一颗驱动，并打印通信结果和部分输入状态。
 *
 * IOIN位定义：
 * bit0：ENN
 * bit2：MS1
 * bit3：MS2
 * bit31～24：VERSION
 */
static void app_probe_one_tmc(const char *name, tmc2209_device_t *device)
{
    tmc2209_result_t result;
    char message[128];

    result = tmc2209_probe(device);

    (void)snprintf(message, sizeof(message), "%s addr=%u result=%s IOIN=0x%08lX\r\n",
                   name,
                   (unsigned int)device->address,
                   tmc2209_result_string(result),
                   (unsigned long)device->ioin);

    board_serial_write(message);

    /*
     * 只有读操作和版本检查都成功，
     * 才将下面的数值作为已验证的输入状态打印。
     */
    if (result == TMC2209_OK)
    {
        unsigned int enn = (unsigned int)(device->ioin & 0x01u);
        unsigned int ms1 = (unsigned int)((device->ioin >> 2) & 0x01u);
        unsigned int ms2 = (unsigned int)((device->ioin >> 3) & 0x01u);
        unsigned int version = (unsigned int)((device->ioin >> 24) & 0xFFu);

        (void)snprintf(message, sizeof(message), "  VERSION=0x%02X ENN=%u MS1=%u MS2=%u\r\n",
                       version, enn, ms1, ms2);

        board_serial_write(message);
    }
}

static void app_probe_all_tmc(void)
{
    /*
     * 本课只读寄存器，不允许开启电机。
     * 即使重复发送探测命令，也先明确禁用驱动。
     */
    board_motor_enable(false);
    board_motor_clear_steps();

    board_serial_write("\r\nTMC2209 read test begin\r\n");

    app_probe_one_tmc("X", &s_tmc_x);

    /*
     * 调试阶段在事务之间留出余量。
     * 延时位于主循环，不位于中断。
     */
    HAL_Delay(2u);

    app_probe_one_tmc("Y", &s_tmc_y);
    HAL_Delay(2u);

    app_probe_one_tmc("Z", &s_tmc_z);

    board_serial_write("TMC2209 read test end; drivers remain disabled\r\n");
}

/*
 * 配置一颗驱动，并打印关键寄存器。
 *
 * 第一轮先把GCONF原值写回，验证写入链路；
 * 然后再修改细分配置。
 */
static bool app_configure_one_tmc(const char *name, uint8_t address)
{
    uint32_t original_gconf;
    uint32_t gconf;
    uint32_t chopconf;
    uint32_t ifcnt;
    tmc2209_result_t result;
    char message[160];

    /* 先读取GCONF原值。 */
    result = tmc2209_read_register(address, TMC2209_REG_GCONF, &original_gconf);

    if (result != TMC2209_OK)
    {
        goto failed;
    }

    /*
     * 原值写回，不主动改变配置。
     * 通过IFCNT确认写入确实被芯片接受。
     */
    result = tmc2209_write_checked(address, TMC2209_REG_GCONF, original_gconf);

    if (result != TMC2209_OK)
    {
        goto failed;
    }

    board_serial_write("  GCONF same-value write: IFCNT verified\r\n");

    /* 再设置寄存器控制的16细分。 */
    result = tmc2209_set_microstep_16(address);

    if (result != TMC2209_OK)
    {
        goto failed;
    }

    result = tmc2209_read_register(address, TMC2209_REG_GCONF, &gconf);

    if (result != TMC2209_OK)
    {
        goto failed;
    }

    result = tmc2209_read_register(address, TMC2209_REG_CHOPCONF, &chopconf);

    if (result != TMC2209_OK)
    {
        goto failed;
    }

    result = tmc2209_read_register(address, TMC2209_REG_IFCNT, &ifcnt);

    if (result != TMC2209_OK)
    {
        goto failed;
    }

    (void)snprintf(message, sizeof(message), "%s addr=%u: OK GCONF=0x%08lX CHOPCONF=0x%08lX IFCNT=%u MRES=%u\r\n",
                   name,
                   (unsigned int)address,
                   (unsigned long)gconf,
                   (unsigned long)chopconf,
                   (unsigned int)(ifcnt & 0xFFu),
                   (unsigned int)((chopconf >> 24) & 0x0Fu));

    board_serial_write(message);

    return true;

failed:
    /*
     * 多处检查失败都汇总到这里打印。
     * 不继续执行后续配置，也不使能电机。
     */
    (void)snprintf(message, sizeof(message), "%s addr=%u: FAIL %s\r\n",
                   name, (unsigned int)address, tmc2209_result_string(result));

    board_serial_write(message);

    return false;
}

/*
 * 为三颗驱动设置UART回复延时。
 *
 * 所有驱动必须成功，才继续后面的细分配置。
 * 当前程序在主循环中调用，不存在并发通信。
 */
static bool app_configure_tmc_bus(void)
{
    uint8_t address;
    tmc2209_result_t result;
    char message[96];

    for (address = 0u; address < 3u; ++address)
    {
        result = tmc2209_set_send_delay(address);

        (void)snprintf(message, sizeof(message), "TMC addr=%u SENDDELAY=2: %s\r\n",
                       (unsigned int)address, tmc2209_result_string(result));

        board_serial_write(message);

        if (result != TMC2209_OK)
        {
            /*
             * 任意一颗失败就停止。
             * 调用者仍保持ENN为高，不允许电机运动。
             */
            return false;
        }
    }

    return true;
}

static void app_configure_all_tmc(void)
{
	s_current_ready = false;
    /*
     * 在配置前明确禁止输出。
     * MS1/MS2已经由board_motor_init设置好，
     * 本函数不再改变地址脚。
     */
    board_motor_enable(false);
    board_motor_clear_steps();

    board_serial_write("\r\nTMC2209 write and microstep test begin\r\n");
	
	
		/*
     * 先给三颗节点设置合适的回复延时。
     */
    if (!app_configure_tmc_bus())
    {
        goto finished;
    }

    if (!app_configure_one_tmc("X", 0u))
    {
        goto finished;
    }

    if (!app_configure_one_tmc("Y", 1u))
    {
        goto finished;
    }

    if (!app_configure_one_tmc("Z", 2u))
    {
        goto finished;
    }

    board_serial_write("All drivers: register-controlled 16 microsteps verified.\r\n");

finished:
    /*
     * 成功或失败都保持禁用。
     * 本课尚未设置和验证电机电流。
     */
    board_motor_enable(false);
    board_serial_write("Drivers remain disabled; current setup is not completed.\r\n");
}

/*
 * 配置三颗驱动的首次调试电流。
 *
 * X/Y：
 * IRUN=17，约0.597A RMS；
 * IHOLD=8，约0.298A RMS。
 *
 * Z：
 * IRUN=11，约0.398A RMS；
 * IHOLD=5，约0.199A RMS。
 *
 * 计算前提：
 * 外部采样电阻每只0.1Ω；
 * 内部参考；
 * VSENSE=1。
 */
static void app_configure_test_currents(void)
{
	
		 /*
		 * 一旦重新配置，就先取消运动许可。
		 * 中间任何一步失败，都不能沿用上一次的成功状态。
		 */
		s_current_ready = false;
	
    static const uint8_t irun_table[3] = {17u, 17u, 11u};
    static const uint8_t ihold_table[3] = {8u, 8u, 5u};

    uint8_t address;
    uint32_t gconf;
    uint32_t chopconf;
    tmc2209_result_t result;
    char message[160];

    /*
     * 三轴共用ENN。
     * 任何一颗配置失败，都不能开始运动。
     */
    board_motor_enable(false);
    board_motor_clear_steps();

    board_serial_write("\r\nTMC2209 test-current configuration begin\r\n");

    /*
     * 复用上一课已经通过的总线初始化。
     * 该函数会打印各节点SENDDELAY配置结果。
     */
    if (!app_configure_tmc_bus())
    {
        board_serial_write("Bus configuration failed; drivers remain disabled.\r\n");
        return;
    }

    for (address = 0u; address < 3u; ++address)
    {
        /*
         * 再次明确使用寄存器16细分，
         * 不要求用户事先执行m命令。
         */
        result = tmc2209_set_microstep_16(address);

        if (result != TMC2209_OK)
        {
            goto failed;
        }

        result = tmc2209_set_test_current(address, irun_table[address], ihold_table[address]);

        if (result != TMC2209_OK)
        {
            goto failed;
        }

        /* 回读能够读取的配置寄存器。 */
        result = tmc2209_read_register(address, TMC2209_REG_GCONF, &gconf);

        if (result != TMC2209_OK)
        {
            goto failed;
        }

        result = tmc2209_read_register(address, TMC2209_REG_CHOPCONF, &chopconf);

        if (result != TMC2209_OK)
        {
            goto failed;
        }

        /*
         * 打印的IRUN/IHOLD是已通过IFCNT验证的写入值，
         * 不是从IHOLD_IRUN中回读出来的数值。
         */
        (void)snprintf(message, sizeof(message), "addr=%u OK GCONF=0x%08lX CHOPCONF=0x%08lX IRUN_written=%u IHOLD_written=%u\r\n",
                       (unsigned int)address,
                       (unsigned long)gconf,
                       (unsigned long)chopconf,
                       (unsigned int)irun_table[address],
                       (unsigned int)ihold_table[address]);

        board_serial_write(message);
    }

		/* 只有完整配置三颗驱动并全部成功，才开放运动测试。 */
		s_current_ready = true;
		
    board_serial_write("All test currents configured; drivers remain disabled.\r\n");
    return;

failed:
    board_motor_enable(false);

    (void)snprintf(message, sizeof(message), "Current configuration FAIL: addr=%u result=%s\r\n",
                   (unsigned int)address, tmc2209_result_string(result));

    board_serial_write(message);
}


/*
 * 启动一次单轴脉冲测试。
 *
 * 不带电：
 *     500Hz，100个脉冲，用于示波器验证。
 *
 * 带电：
 *     100Hz，16个脉冲，用于首次短距离转动。
 */
static void app_start_step_test(board_axis_t axis, bool direction_high, bool run_motor)
{
    uint32_t pulse_count;
    uint32_t frequency_hz;
    char message[128];

    if (run_motor && !s_current_ready)
    {
        board_serial_write("Rejected: run c successfully before motor movement.\r\n");
        return;
    }

    if (run_motor)
    {
        pulse_count = 320u;
        frequency_hz = 100u;
    }
    else
    {
        pulse_count = 100u;
        frequency_hz = 500u;
    }

    /*
     * 在启动前打印。
     * 避免为了打印启动信息而影响运动期间的主循环响应。
     */
    (void)snprintf(message, sizeof(message), "STEP TEST: axis=%c DIR=%u pulses=%lu frequency=%luHz motor=%u\r\n",
                   (char)('X' + (int)axis),
                   direction_high ? 1u : 0u,
                   (unsigned long)pulse_count,
                   (unsigned long)frequency_hz,
                   run_motor ? 1u : 0u);

    board_serial_write(message);

    if (!board_step_test_start(axis, direction_high, pulse_count, frequency_hz, run_motor))
    {
        board_serial_write("STEP TEST start failed.\r\n");
        return;
    }

    s_step_test_active = true;
}

/*
 * 主循环查询完成状态。
 *
 * 中断只更新状态；
 * 串口输出留给主循环处理。
 */
static void app_poll_step_test(void)
{
    char message[96];

    if (!s_step_test_active)
    {
        return;
    }

    if (board_step_test_is_busy())
    {
        return;
    }

    s_step_test_active = false;

    (void)snprintf(message, sizeof(message), "STEP TEST done: emitted=%lu; ENN unchanged. Send s to disable.\r\n",
                   (unsigned long)board_step_test_get_count());

    board_serial_write(message);
}

/*
 * 打印主机串口的累计接收统计。
 *
 * 不要求received固定为某个值：
 * 命令字符、回车和换行都会计入。
 *
 * 正常测试应满足：
 * dropped = 0
 * errors = 0
 */
static void app_print_serial_stats(void)
{
    uint32_t received;
    uint32_t dropped;
    uint32_t errors;
    char message[128];

    board_serial_get_rx_stats(&received, &dropped, &errors);

    (void)snprintf(message, sizeof(message), "USART2 RX: received=%lu dropped=%lu errors=%lu\r\n",
                   (unsigned long)received,
                   (unsigned long)dropped,
                   (unsigned long)errors);

    board_serial_write(message);
}


void app_process(void)
{
    uint8_t command;

    /*
     * 必须先处理完成状态，再查询串口。
     *
     * 如果放在串口读取成功之后，
     * 没有新命令时就不会打印完成信息。
     */
    app_poll_step_test();

    if (!board_serial_read_byte(&command))
    {
        return;
    }

    /* 忽略串口工具附加的换行符。 */
    if ((command == '\r') || (command == '\n'))
    {
        return;
    }

    /*
     * s随时有效。
     *
     * 先停止定时器，再禁用驱动并清STEP，
     * 防止清零以后又被定时器中断重新拉高。
     */
    if (command == 's')
    {
        board_step_test_stop();
        s_step_test_active = false;

        board_serial_write("Stopped: drivers disabled; STEP cleared.\r\n");
        return;
    }

    /*
     * 测试尚未结束时，不执行旧的方向切换、
     * 单脉冲、TMC配置和阻塞式串口打印命令。
     *
     * 这些命令原来会清STEP或改变ENN，
     * 不能与当前定时器测试同时运行。
     */
    if (board_step_test_is_busy())
    {
        return;
    }

    switch (command)
    {
        case 'h':
        case 'H':
            app_print_help();
            break;

        case '?':
            app_print_home_levels();
            break;

        case 'x':
            app_toggle_direction(BOARD_AXIS_X);
            break;

        case 'y':
            app_toggle_direction(BOARD_AXIS_Y);
            break;

        case 'z':
            app_toggle_direction(BOARD_AXIS_Z);
            break;

        case '1':
            app_test_one_pulse(BOARD_AXIS_X);
            break;

        case '2':
            app_test_one_pulse(BOARD_AXIS_Y);
            break;

        case '3':
            app_test_one_pulse(BOARD_AXIS_Z);
            break;
        /*
         * 不使能电机，只输出连续脉冲。
         * 用示波器依次验证X/Y/Z。
         */
				
        case '4':
            app_start_step_test(BOARD_AXIS_X, false, false);
            break;

        case '5':
            app_start_step_test(BOARD_AXIS_Y, false, false);
            break;

        case '6':
            app_start_step_test(BOARD_AXIS_Z, false, false);
            break;

        /* X轴两种DIR电平的实际转动测试。 */
        case 'a':
            app_start_step_test(BOARD_AXIS_X, false, true);
            break;

        case 'b':
            app_start_step_test(BOARD_AXIS_X, true, true);
            break;

        /* Y轴两种DIR电平的实际转动测试。 */
        case 'd':
            app_start_step_test(BOARD_AXIS_Y, false, true);
            break;

        case 'e':
            app_start_step_test(BOARD_AXIS_Y, true, true);
            break;

        /* Z轴两种DIR电平的实际转动测试。 */
        case 'f':
            app_start_step_test(BOARD_AXIS_Z, false, true);
            break;

        case 'g':
            app_start_step_test(BOARD_AXIS_Z, true, true);
            break;
				

        case '\r':
        case '\n':
            /* 忽略串口工具附带的换行符。 */
            break;
				
				case 't':
				case 'T':
						app_probe_all_tmc();
						break;
				
				case 'm':
				case 'M':
						app_configure_all_tmc();
						break;
				
				case 'c':
				case 'C':
						app_configure_test_currents();
						break;
				
				/* 查看USART2接收统计。 */
        case 'v':
            app_print_serial_stats();
            break;

        /*
         * 接收吞吐测试字符。
         *
         * 收到后不运动、不打印，
         * 避免大量打印干扰接收测试。
         */
        case 'u':
            break;

        default:
            board_serial_write("Unknown command. Send h for help.\r\n");
            break;
    }
}
