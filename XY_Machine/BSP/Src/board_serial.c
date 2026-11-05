#include "board_serial.h"
#include "usart.h"
#include "grbl_serial.h"

#include <stddef.h>
#include <string.h>

/*
 * 环形缓冲区数组长度为256字节。
 *
 * 为了区分“空”和“满”，始终保留一个空位置，
 * 所以实际最多保存255个尚未处理的字节。
 */
#define BOARD_SERIAL_RX_SIZE    256u

/*
 * HAL每次接收一个字节，先写入这个暂存变量。
 * 接收完成回调再把它放进软件环形缓冲区。
 */
static uint8_t s_rx_byte;

/* 软件接收缓冲区。 */
static uint8_t s_rx_buffer[BOARD_SERIAL_RX_SIZE];

/*
 * head：中断下一次写入的位置。
 * tail：主循环下一次读取的位置。
 *
 * head == tail表示缓冲区为空。
 *
 * 本模块采用：
 * 单生产者：USART2接收中断。
 * 单消费者：主循环。
 */
static volatile uint16_t s_rx_head;
static volatile uint16_t s_rx_tail;

/* 本次启动以来的统计信息。 */
static volatile uint32_t s_rx_received;
static volatile uint32_t s_rx_dropped;
static volatile uint32_t s_rx_errors;

/*
 * 接收错误或重新启动接收失败后置位。
 * 主循环发现此标志后恢复接收。
 */
static volatile bool s_rx_need_recover;

/*
 * 恢复USART2接收。
 *
 * 只从主循环调用，不在中断内调用。
 * 当前方案使用普通中断接收，没有使用DMA。
 *
 * 注意：
 * 恢复接收不能找回错误期间已经丢失的数据。
 */
void board_serial_recover_rx(void)
{
    uint32_t saved_primask;
    HAL_StatusTypeDef status;

    if (!s_rx_need_recover)
    {
        return;
    }

    /*
     * 防止恢复过程中进入接收回调，
     * 同时修改同一个HAL接收状态。
     *
     * 保存原中断状态，退出时原样恢复，
     * 不无条件开启中断。
     *
     * 这里只进行短暂状态恢复，不打印、不延时。
     */
    saved_primask = __get_PRIMASK();
    __disable_irq();

    /*
     * 只终止接收，不终止发送。
     * 让旧的接收过程结束，准备重新挂接。
     */
    (void)HAL_UART_AbortReceive(&huart2);

    /* 清除可能残留的UART错误标志。 */
    __HAL_UART_CLEAR_OREFLAG(&huart2);
    __HAL_UART_CLEAR_FEFLAG(&huart2);
    __HAL_UART_CLEAR_NEFLAG(&huart2);
    __HAL_UART_CLEAR_PEFLAG(&huart2);

    s_rx_need_recover = false;

    status = HAL_UART_Receive_IT(&huart2, &s_rx_byte, 1u);

    if (status != HAL_OK)
    {
        /* 下次主循环再次尝试。 */
        s_rx_need_recover = true;
    }

    __set_PRIMASK(saved_primask);
}

bool board_serial_init(void)
{
    /*
     * 本函数只在启动时调用一次。
     * 调用前尚未开始USART2中断接收。
     */
    s_rx_head = 0u;
    s_rx_tail = 0u;

    s_rx_received = 0u;
    s_rx_dropped = 0u;
    s_rx_errors = 0u;

    s_rx_need_recover = false;

    /*
     * 启动第一次单字节中断接收。
     *
     * 此函数立即返回，不等待电脑发送数据。
     */
    if (HAL_UART_Receive_IT(&huart2, &s_rx_byte, 1u) != HAL_OK)
    {
        s_rx_need_recover = true;
        return false;
    }

    return true;
}

bool board_serial_write(const char *text)
{
    size_t length;

    if (text == NULL)
    {
        return false;
    }

    length = strlen(text);

    if (length == 0u)
    {
        return true;
    }

    /* HAL发送长度参数为uint16_t。 */
    if (length > 65535u)
    {
        return false;
    }

    /*
     * 发送暂时保持原来的阻塞方式。
     *
     * USART2接收中断仍可以在发送等待期间运行，
     * 因此发送文本时也可以把收到的字节存入缓冲区。
     */
    return HAL_UART_Transmit(&huart2, (uint8_t *)text, (uint16_t)length, 100u) == HAL_OK;
}

bool board_serial_read_byte(uint8_t *byte)
{
    uint16_t tail;

    if (byte == NULL)
    {
        return false;
    }

    /* UART发生接收错误后，在主循环恢复。 */
    board_serial_recover_rx();

    tail = s_rx_tail;

    if (tail == s_rx_head)
    {
        /* 没有数据，立即返回，不等待。 */
        return false;
    }

    /*
     * 先读取当前位置的字节，
     * 再把tail推进到下一个位置。
     */
    *byte = s_rx_buffer[tail];

    /*
     * 确保读取完成后再公布新的tail。
     * 中断看到tail前进后，才可以重新使用旧位置。
     */
    __DMB();

    s_rx_tail = (uint16_t)((tail + 1u) % BOARD_SERIAL_RX_SIZE);

    return true;
}

void board_serial_get_rx_stats(uint32_t *received, uint32_t *dropped, uint32_t *errors)
{
    uint32_t saved_primask;

    /*
     * 短暂保护统计快照，
     * 避免读取三个值的过程中被接收中断更新。
     */
    saved_primask = __get_PRIMASK();
    __disable_irq();

    if (received != NULL)
    {
        *received = s_rx_received;
    }

    if (dropped != NULL)
    {
        *dropped = s_rx_dropped;
    }

    if (errors != NULL)
    {
        *errors = s_rx_errors;
    }

    __set_PRIMASK(saved_primask);
}

/*
 * HAL接收完成回调。
 *
 * 当前工程尚未定义这个回调，可以在此添加。
 * 以后若其他模块也需要此回调，应合并外设分支，
 * 不能在两个.c文件里重复定义同名函数。
 */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
	#if !BOARD_SERIAL_GRBL_RX_ENABLE
    uint16_t head;
    uint16_t next;
	#endif

    // 只处理USART2，UART4的TMC2209通信不在这里处理
    if (huart->Instance != USART2)
    {
        return;
    }

    ++s_rx_received;   // 记录USART2接收完成的字节数，两种接收用途都统计
		
		
	#if BOARD_SERIAL_GRBL_RX_ENABLE
    // GRBL接收入口：识别实时指令，普通字符才进入GRBL缓冲区
    serial_receive_byte(s_rx_byte);
		
	#else
		// 自检接收入口：计算自检缓冲区下一个写入位置
    head = s_rx_head;
    next = (uint16_t)((head + 1u) % BOARD_SERIAL_RX_SIZE);

    if (next == s_rx_tail)
    {
        // 自检缓冲区已满，丢弃本次字节并记录次数
        ++s_rx_dropped;
    }
    else
    {
				//先保存收到的字节
        s_rx_buffer[head] = s_rx_byte;

        // 保证数据写入发生在发布新head之前
        __DMB();

        s_rx_head = next;
    }
		
	#endif

    // 单字节接收完成后，两种用途都需要重新挂接下一次接收
    if (!s_rx_need_recover)
    {
        if (HAL_UART_Receive_IT(&huart2, &s_rx_byte, 1u) != HAL_OK)
        {
            s_rx_need_recover = true;   // 挂接失败时，由主流程中的恢复逻辑处理
        }
    }
}

/*
 * HAL串口错误回调。
 *
 * 中断中只记录错误并请求恢复，
 * 不打印、不延时、不尝试解析收到的内容。
 */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance != USART2)
    {
        return;
    }

    ++s_rx_errors;
    s_rx_need_recover = true;
}
