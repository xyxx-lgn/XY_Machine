#include "tmc2209.h"
#include "usart.h"

#include <stddef.h>
#include <string.h>

/* UART读请求4字节，TMC回复8字节。 */
#define TMC2209_REQUEST_SIZE       4u
#define TMC2209_REPLY_SIZE         8u

/* 当前硬件会收到4字节回显，所以总共接收12字节。 */
#define TMC2209_RX_SIZE            (TMC2209_REQUEST_SIZE + TMC2209_REPLY_SIZE)

/*
 * 115200波特率下，一次正常读取只需较短时间。
 * 调试阶段使用50ms超时，留出充足余量。
 */
#define TMC2209_TIMEOUT_MS         50u

/*
 * 使用静态缓冲区。
 *
 * HAL中断接收会在函数执行期间不断写入该数组。
 * 本驱动不支持重入，同一时刻只能进行一次事务。
 */
static uint8_t s_rx_buffer[TMC2209_RX_SIZE];

/*
 * 计算TMC2209使用的CRC8。
 *
 * 多项式为0x07。
 * 每个输入字节按最低位优先参与计算，
 * CRC寄存器则根据最高位判断是否异或多项式。
 *
 * 不能随意替换成其他CRC8算法：
 * 多项式相同，也可能因为位顺序不同而得到不同结果。
 */
static uint8_t tmc2209_crc8(const uint8_t *data, uint8_t length)
{
    uint8_t crc = 0u;
    uint8_t i;
    uint8_t bit;

    for (i = 0u; i < length; ++i)
    {
        uint8_t current = data[i];

        for (bit = 0u; bit < 8u; ++bit)
        {
            uint8_t mix = (uint8_t)(((crc >> 7) ^ (current & 0x01u)) & 0x01u);

            crc = (uint8_t)(crc << 1);

            if (mix != 0u)
            {
                crc ^= 0x07u;
            }

            current >>= 1;
        }
    }

    return crc;
}

/*
 * 清理上一次通信残留。
 *
 * 只能在UART4空闲时调用。
 * 这里清除串口错误标志，并丢弃接收寄存器残留数据。
 *
 * 注意：
 * 该操作会丢弃数据，所以UART4必须由本驱动独占。
 */
static void tmc2209_clear_rx(void)
{
    __HAL_UART_CLEAR_FLAG(&huart4, UART_CLEAR_PEF | UART_CLEAR_FEF | UART_CLEAR_NEF | UART_CLEAR_OREF);
    __HAL_UART_SEND_REQ(&huart4, UART_RXDATA_FLUSH_REQUEST);
}

/*
 * UART4通用传输函数。
 *
 * tx_length：发送报文长度；
 * rx_length：本次预期收到的总字节数，包含发送回显。
 *
 * 仍然采用：
 * 先启动中断接收，再阻塞发送，最后等待接收完成。
 *
 * 只能在主循环或任务中调用。
 * UART4由本驱动独占，不支持并发调用。
 */
static tmc2209_result_t tmc2209_transfer(uint8_t *request, uint16_t tx_length, uint16_t rx_length)
{
    uint32_t start_tick;
    HAL_StatusTypeDef hal_result;

    if ((request == NULL) || (tx_length == 0u) || (rx_length == 0u))
    {
        return TMC2209_ERROR_ARGUMENT;
    }

    /* 接收长度不能超过现有12字节缓冲区。 */
    if (rx_length > sizeof(s_rx_buffer))
    {
        return TMC2209_ERROR_ARGUMENT;
    }

    if ((huart4.RxState != HAL_UART_STATE_READY) || (huart4.gState != HAL_UART_STATE_READY))
    {
        return TMC2209_ERROR_BUSY;
    }

		/*
		 * 上一次读取结束后，TMC2209不会立即释放总线。
		 *
		 * 调试阶段统一在下一次事务前等待1ms，
		 * 覆盖回复结束后的总线释放时间，
		 * 也为异常通信后的恢复留出余量。
		 *
		 * 这不是把接收超时加长，而是在发送前等待总线空闲。
		 * 后续需要提高配置效率时，再改成微秒级时间管理。
		 *
		 * 此函数只能在主循环或任务中调用，
		 * 不能在中断中调用HAL_Delay。
		 */
		HAL_Delay(1u);

		/*
		 * 等待结束后，再清除接收残留并启动新事务。
		 */
    tmc2209_clear_rx();
    memset(s_rx_buffer, 0, sizeof(s_rx_buffer));

    /*
     * 先接收，避免丢失发送期间产生的回显。
     * 读取时接收12字节，写入时接收8字节。
     */
    hal_result = HAL_UART_Receive_IT(&huart4, s_rx_buffer, rx_length);

    if (hal_result != HAL_OK)
    {
        return TMC2209_ERROR_RX_START;
    }

    start_tick = HAL_GetTick();

    hal_result = HAL_UART_Transmit(&huart4, request, tx_length, TMC2209_TIMEOUT_MS);

    if (hal_result != HAL_OK)
    {
        (void)HAL_UART_AbortReceive(&huart4);
        return TMC2209_ERROR_TX;
    }

    while (huart4.RxState == HAL_UART_STATE_BUSY_RX)
    {
        if (HAL_UART_GetError(&huart4) != HAL_UART_ERROR_NONE)
        {
            (void)HAL_UART_AbortReceive(&huart4);
            return TMC2209_ERROR_UART;
        }

        if ((uint32_t)(HAL_GetTick() - start_tick) >= TMC2209_TIMEOUT_MS)
        {
            (void)HAL_UART_AbortReceive(&huart4);
            return TMC2209_ERROR_TIMEOUT;
        }
    }

    /*
     * 接收状态退出BUSY，可能是收齐，也可能是发生错误。
     * 因此仍需要检查错误码和剩余数量。
     */
    if (HAL_UART_GetError(&huart4) != HAL_UART_ERROR_NONE)
    {
        (void)HAL_UART_AbortReceive(&huart4);
        return TMC2209_ERROR_UART;
    }

    if (huart4.RxXferCount != 0u)
    {
        (void)HAL_UART_AbortReceive(&huart4);
        return TMC2209_ERROR_UART;
    }

    __DMB();

    return TMC2209_OK;
}

tmc2209_result_t tmc2209_read_register(uint8_t address, uint8_t reg, uint32_t *value)
{
    uint8_t request[TMC2209_REQUEST_SIZE];
    const uint8_t *reply;
    tmc2209_result_t result;

    if ((address > 3u) || (reg > 0x7Fu) || (value == NULL))
    {
        return TMC2209_ERROR_ARGUMENT;
    }

    /*
     * 读取失败时先给调用者一个确定值。
     * 调用者仍然必须检查返回状态，
     * 不能把这个0误认为真实寄存器值。
     */
    *value = 0u;

    request[0] = 0x05u;
    request[1] = address;
    request[2] = reg;
    request[3] = tmc2209_crc8(request, 3u);

		result = tmc2209_transfer(request, (uint16_t)TMC2209_REQUEST_SIZE, (uint16_t)TMC2209_RX_SIZE);

    if (result != TMC2209_OK)
    {
        return result;
    }

    /*
     * 前4字节应该与自己的发送内容完全一致。
     * 不一致时，说明回显位置、数据或接线存在问题。
     */
    if (memcmp(s_rx_buffer, request, TMC2209_REQUEST_SIZE) != 0)
    {
        return TMC2209_ERROR_ECHO;
    }

    /* 剩余8字节才是TMC2209的回复。 */
    reply = &s_rx_buffer[TMC2209_REQUEST_SIZE];

    /*
     * 回复第二字节固定为主机地址0xFF。
     * 不是这颗驱动的0/1/2地址。
     */
    if ((reply[0] != 0x05u) || (reply[1] != 0xFFu) || (reply[2] != reg))
    {
        return TMC2209_ERROR_FRAME;
    }

    /* 最后一个字节是前7字节的CRC。 */
    if (tmc2209_crc8(reply, 7u) != reply[7])
    {
        return TMC2209_ERROR_CRC;
    }

    /*
     * 数据高字节先传：
     * reply[3]对应bit31～24，
     * reply[6]对应bit7～0。
     *
     * 先转为uint32_t再左移，避免整数提升带来的问题。
     */
    *value = ((uint32_t)reply[3] << 24)
           | ((uint32_t)reply[4] << 16)
           | ((uint32_t)reply[5] << 8)
           |  (uint32_t)reply[6];

    return TMC2209_OK;
}

/*
 * 写寄存器：
 * 构建8字节写入报文，发送并检查自身回显。
 *
 * 本函数不检查IFCNT。
 * 回显正常只证明STM32收到了自己发出的数据。
 */
tmc2209_result_t tmc2209_write_register(uint8_t address, uint8_t reg, uint32_t value)
{
    uint8_t request[8];
    tmc2209_result_t result;

    if ((address > 3u) || (reg > 0x7Fu))
    {
        return TMC2209_ERROR_ARGUMENT;
    }

    request[0] = 0x05u;
    request[1] = address;

    /* bit7=1表示写入，其余7位是寄存器地址。 */
    request[2] = (uint8_t)(reg | 0x80u);

    /* 32位数据按高字节到低字节排列。 */
    request[3] = (uint8_t)(value >> 24);
    request[4] = (uint8_t)(value >> 16);
    request[5] = (uint8_t)(value >> 8);
    request[6] = (uint8_t)value;

    request[7] = tmc2209_crc8(request, 7u);

    /*
     * 写入没有8字节回复。
     * 本次接收8字节，全部是发送回显。
     */
    result = tmc2209_transfer(request, 8u, 8u);

    if (result != TMC2209_OK)
    {
        return result;
    }

    if (memcmp(s_rx_buffer, request, sizeof(request)) != 0)
    {
        return TMC2209_ERROR_ECHO;
    }

    return TMC2209_OK;
}

/*
 * 通过IFCNT验证一次写入。
 *
 * 前提：
 * 1. 同一时刻没有其他事务访问UART4；
 * 2. 本函数期间只执行一次写入；
 * 3. 驱动没有在事务中途复位。
 */
tmc2209_result_t tmc2209_write_checked(uint8_t address, uint8_t reg, uint32_t value)
{
    uint32_t before;
    uint32_t after;
    uint8_t difference;
    tmc2209_result_t result;

    result = tmc2209_read_register(address, TMC2209_REG_IFCNT, &before);

    if (result != TMC2209_OK)
    {
        return result;
    }

    result = tmc2209_write_register(address, reg, value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    /*
     * 调试阶段留出短暂间隔。
     * 此函数只在静止配置阶段使用，不用于运动中断。
     */
    HAL_Delay(1u);

    result = tmc2209_read_register(address, TMC2209_REG_IFCNT, &after);

    if (result != TMC2209_OK)
    {
        return result;
    }

    /*
     * IFCNT只有低8位有效。
     *
     * 转换成uint8_t后按256取模：
     * before=255，after=0，差值仍为1。
     */
    difference = (uint8_t)((uint8_t)after - (uint8_t)before);

    if (difference != 1u)
    {
        return TMC2209_ERROR_VERIFY;
    }

    return TMC2209_OK;
}

/*
 * 设置SENDDELAY=2。
 *
 * NODECONF是只写寄存器：
 * 不能先读取原值，也不能要求写后读回。
 *
 * 因此直接构造配置值，
 * 再通过已经验证过的IFCNT机制检查写入。
 *
 * address仍然是MS1/MS2决定的0、1、2，
 * 此寄存器不会替代地址脚设置。
 */
tmc2209_result_t tmc2209_set_send_delay(uint8_t address)
{
    return tmc2209_write_checked(address, TMC2209_REG_NODECONF, TMC2209_NODECONF_SENDDELAY_2);
}

/*
 * 设置寄存器控制的16细分。
 *
 * 只修改：
 * GCONF.pdn_disable
 * GCONF.mstep_reg_select
 * CHOPCONF.MRES
 *
 * 不修改电流，不修改VREF配置，不使能电机。
 */
tmc2209_result_t tmc2209_set_microstep_16(uint8_t address)
{
    uint32_t value;
    uint32_t readback;
    uint32_t gconf_mask;
    tmc2209_result_t result;

    gconf_mask = TMC2209_GCONF_PDN_DISABLE | TMC2209_GCONF_MSTEP_REG_SELECT;

    /* 第一步：让细分由寄存器决定。 */
    result = tmc2209_read_register(address, TMC2209_REG_GCONF, &value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    value |= gconf_mask;

    result = tmc2209_write_checked(address, TMC2209_REG_GCONF, value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    result = tmc2209_read_register(address, TMC2209_REG_GCONF, &readback);

    if (result != TMC2209_OK)
    {
        return result;
    }

    if ((readback & gconf_mask) != gconf_mask)
    {
        return TMC2209_ERROR_VERIFY;
    }

    /* 第二步：修改MRES字段，保留CHOPCONF其他字段。 */
    result = tmc2209_read_register(address, TMC2209_REG_CHOPCONF, &value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    value &= ~TMC2209_CHOPCONF_MRES_MASK;
    value |= TMC2209_CHOPCONF_MRES_16;

    result = tmc2209_write_checked(address, TMC2209_REG_CHOPCONF, value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    result = tmc2209_read_register(address, TMC2209_REG_CHOPCONF, &readback);

    if (result != TMC2209_OK)
    {
        return result;
    }

    if ((readback & TMC2209_CHOPCONF_MRES_MASK) != TMC2209_CHOPCONF_MRES_16)
    {
        return TMC2209_ERROR_VERIFY;
    }

    return TMC2209_OK;
}

tmc2209_result_t tmc2209_probe(tmc2209_device_t *device)
{
    tmc2209_result_t result;
    uint32_t value;
    uint8_t version;

    if (device == NULL)
    {
        return TMC2209_ERROR_ARGUMENT;
    }

    device->online = false;
    device->ioin = 0u;

    result = tmc2209_read_register(device->address, TMC2209_REG_IOIN, &value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    device->ioin = value;

    /* 提取IOIN的bit31～24。 */
    version = (uint8_t)(value >> 24);

    if (version != TMC2209_EXPECTED_VERSION)
    {
        /*
         * VERSION不符时保留IOIN原值，方便检查。
         * 不能直接认定芯片损坏，
         * 还需要核对型号、版本和通信数据。
         */
        return TMC2209_ERROR_VERSION;
    }

    device->online = true;

    return TMC2209_OK;
}

/*
 * 配置测试电流。
 *
 * 此函数只访问寄存器，不直接控制ENN。
 * 调用者必须先禁用驱动，并保证机器静止。
 *
 * 本函数不改动MRES细分字段，
 * 保留前面已经设置好的16细分。
 */
tmc2209_result_t tmc2209_set_test_current(uint8_t address, uint8_t irun, uint8_t ihold)
{
    uint32_t value;
    uint32_t readback;
    uint32_t current_register;
    uint32_t current_source_mask;
    tmc2209_result_t result;

    /*
     * IRUN/IHOLD均为5位编码。
     * 本阶段不允许保持电流大于运行电流。
     */
    if ((address > 3u) || (irun > 31u) || (ihold > 31u) || (ihold > irun))
    {
        return TMC2209_ERROR_ARGUMENT;
    }

    /*
     * 第一步：明确关闭内部速度发生器。
     *
     * VACTUAL=0时，驱动响应外部STEP输入。
     * VACTUAL非0时可能通过内部发生器产生运动，
     * 当前项目不使用这种方式。
     *
     * VACTUAL只写，使用IFCNT验证，不要求回读。
     */
    result = tmc2209_write_checked(address, TMC2209_REG_VACTUAL, 0u);

    if (result != TMC2209_OK)
    {
        return result;
    }

    /*
     * 第二步：设置电流参考来源。
     *
     * i_scale_analog=0：关闭VREF模拟缩放；
     * internal_Rsense=0：使用PCB上的外部采样电阻。
     *
     * 采用读—修改—写，保留其他GCONF设置。
     */
    result = tmc2209_read_register(address, TMC2209_REG_GCONF, &value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    current_source_mask = TMC2209_GCONF_I_SCALE_ANALOG | TMC2209_GCONF_INTERNAL_RSENSE;
    value &= ~current_source_mask;

    result = tmc2209_write_checked(address, TMC2209_REG_GCONF, value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    result = tmc2209_read_register(address, TMC2209_REG_GCONF, &readback);

    if (result != TMC2209_OK)
    {
        return result;
    }

    if ((readback & current_source_mask) != 0u)
    {
        return TMC2209_ERROR_VERIFY;
    }

    /*
     * 第三步：选择VSENSE=1。
     *
     * 只修改bit17，
     * 保留细分、插值和其他斩波参数。
     */
    result = tmc2209_read_register(address, TMC2209_REG_CHOPCONF, &value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    value |= TMC2209_CHOPCONF_VSENSE;

    result = tmc2209_write_checked(address, TMC2209_REG_CHOPCONF, value);

    if (result != TMC2209_OK)
    {
        return result;
    }

    result = tmc2209_read_register(address, TMC2209_REG_CHOPCONF, &readback);

    if (result != TMC2209_OK)
    {
        return result;
    }

    if ((readback & TMC2209_CHOPCONF_VSENSE) == 0u)
    {
        return TMC2209_ERROR_VERIFY;
    }

    /*
     * 第四步：构造IHOLD_IRUN。
     *
     * bit4～0   ：IHOLD
     * bit12～8  ：IRUN
     * bit19～16 ：IHOLDDELAY
     *
     * IHOLDDELAY先设为6。
     * 它不是“6ms”，实际降流时间取决于内部时钟、
     * 电流下降级数和TPOWERDOWN等参数。
     *
     * TPOWERDOWN本课保持现有值，不额外修改。
     */
    current_register = (uint32_t)ihold
                     | ((uint32_t)irun << 8)
                     | (6UL << 16);

    /*
     * IHOLD_IRUN只写。
     * IFCNT增加可以证明芯片接受写报文，
     * 不能通过读取该寄存器获得可靠的配置回显。
     */
    result = tmc2209_write_checked(address, TMC2209_REG_IHOLD_IRUN, current_register);

    if (result != TMC2209_OK)
    {
        return result;
    }

    return TMC2209_OK;
}

const char *tmc2209_result_string(tmc2209_result_t result)
{
    switch (result)
    {
        case TMC2209_OK:
            return "OK";

        case TMC2209_ERROR_ARGUMENT:
            return "ARGUMENT";

        case TMC2209_ERROR_BUSY:
            return "BUSY";

        case TMC2209_ERROR_TX:
            return "TX";

        case TMC2209_ERROR_RX_START:
            return "RX_START";

        case TMC2209_ERROR_TIMEOUT:
            return "TIMEOUT";

        case TMC2209_ERROR_UART:
            return "UART";

        case TMC2209_ERROR_ECHO:
            return "ECHO";

        case TMC2209_ERROR_FRAME:
            return "FRAME";

        case TMC2209_ERROR_CRC:
            return "CRC";

        case TMC2209_ERROR_VERSION:
            return "VERSION";
				
				case TMC2209_ERROR_VERIFY:
						return "VERIFY";

        default:
            return "UNKNOWN";
    }
}
