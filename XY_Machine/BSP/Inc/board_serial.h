#ifndef BOARD_SERIAL_H
#define BOARD_SERIAL_H

#include <stdbool.h>
#include <stdint.h>

/*
 * 初始化USART2软件接收缓冲区，并启动中断接收。
 *
 * 调用条件：
 * MX_USART2_UART_Init()已经执行。
 *
 * 只在启动时调用一次，不在每次主循环中调用。
 */
bool board_serial_init(void);

/*
 * 通过USART2发送以'\0'结尾的字符串。
 *
 * 本阶段仍使用阻塞发送。
 * 只允许在主循环调用，不允许在中断里调用。
 */
bool board_serial_write(const char *text);

/*
 * 从软件接收缓冲区取出一个字节。
 *
 * 返回true：
 * 成功取出一个字节，保存在*byte中。
 *
 * 返回false：
 * 缓冲区暂时为空，或者参数无效。
 *
 * 本函数不会等待新字节到来。
 * 只允许主循环这一个消费者调用。
 */
bool board_serial_read_byte(uint8_t *byte);

/*
 * 获取本次启动以来的接收统计。
 *
 * received：
 * 接收完成回调处理的字节数，
 * 包括软件缓冲区满后丢弃的字节。
 *
 * dropped：
 * 软件缓冲区满后丢弃的新字节数。
 *
 * errors：
 * HAL报告UART错误的回调次数，
 * 不等于实际丢失字节数量。
 *
 * 不关心的输出参数可以传NULL。
 */
void board_serial_get_rx_stats(uint32_t *received, uint32_t *dropped, uint32_t *errors);

#endif

