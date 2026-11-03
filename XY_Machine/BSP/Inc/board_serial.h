#ifndef BOARD_SERIAL_H
#define BOARD_SERIAL_H

#include <stdbool.h>
#include <stdint.h>

/*
 * 通过USART2发送字符串。
 * 字符串需要以'\0'结尾。
 *
 * 当前采用阻塞发送，只用于低速人工自检。
 */
bool board_serial_write(const char *text);

/*
 * 尝试接收一个字节：
 * 收到 -> 返回true，并写入*byte；
 * 没收到或发生错误 -> 返回false。
 *
 * 当前使用轮询方式，不等待数据到来。
 */
bool board_serial_read_byte(uint8_t *byte);

#endif
