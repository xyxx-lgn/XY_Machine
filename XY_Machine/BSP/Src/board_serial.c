#include "board_serial.h"
#include "usart.h"
#include <string.h>

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

    /* HAL_UART_Transmit的长度参数为uint16_t。 */
    if (length > 65535u)
    {
        return false;
    }

    /*
     * 当前是人工单字符操作，消息很短，
     * 使用100ms超时足够。
     *
     * 后续接入GRBL时，会改用适合连续传输的缓冲机制。
     */
    return HAL_UART_Transmit(&huart2,(uint8_t *)text,(uint16_t)length,100u) == HAL_OK;
}

bool board_serial_read_byte(uint8_t *byte)
{
    if (byte == NULL)
    {
        return false;
    }

    /*
     * 如果人工测试期间接收过快发生溢出，
     * 清除ORE，让后续输入有机会恢复。
     *
     * 已丢失的字节不会因此恢复。
     * 当前模块不适合发送整段G-code。
     */
    if (__HAL_UART_GET_FLAG(&huart2, UART_FLAG_ORE) != RESET)
    {
        __HAL_UART_CLEAR_OREFLAG(&huart2);
    }

    /*
     * Timeout=0：
     * 有数据就读取，没有数据立即返回。
     *
     * 不要再同时对huart2调用Receive_IT或Receive_DMA，
     * 同一个接收通道应由一种接收方式管理。
     */
    return HAL_UART_Receive(&huart2,byte,1u,0u) == HAL_OK;
}
