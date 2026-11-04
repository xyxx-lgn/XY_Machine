/*
  serial.c - Low level functions for sending and recieving bytes via the serial port
  Part of Grbl

  Copyright (c) 2011-2016 Sungeun K. Jeon for Gnea Research LLC
  Copyright (c) 2009-2011 Simen Svale Skogsrud

  Grbl is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Grbl is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with Grbl.  If not, see <http://www.gnu.org/licenses/>.
*/

#ifndef serial_h
#define serial_h

/* 当前G431工程的平台定义。 */
#include "main.h"
#include "usart.h"

#include <stdio.h>
#include <stdarg.h>

//接收发送缓冲区可用容量大小，数组中另外保留一个空位置。
#ifndef RX_BUFFER_SIZE
  #define RX_BUFFER_SIZE 127     //如果改成128，则需要对前面函数进行一些修改
#endif
#ifndef TX_BUFFER_SIZE
  #ifdef USE_LINE_NUMBERS
    #define TX_BUFFER_SIZE 112
  #else
    #define TX_BUFFER_SIZE 64
  #endif
#endif


// 实际数组长度比可用容量多1，保留一个空位置，用于区分缓冲区满和空
#define RX_RING_BUFFER (RX_BUFFER_SIZE+1)
#define TX_RING_BUFFER (TX_BUFFER_SIZE+1)

extern uint8_t serial_tx_buffer[];
extern volatile uint8_t serial_rx_buffer_head;
extern volatile uint8_t serial_rx_buffer_tail;

extern uint8_t serial_rx_buffer[];
extern volatile uint8_t serial_tx_buffer_head;
extern volatile uint8_t serial_tx_buffer_tail;

#define SERIAL_NO_DATA 0xff   //serial_read()在没有普通输入数据时返回此值


void serial_init();    //初始化GRBL串口接口

// Writes one byte to the TX serial buffer. Called by main program.
void serial_write(uint8_t data);   //发送一个字节，grbl接口

// Fetches the first byte in the serial read buffer. Called by main program.
uint8_t serial_read();             //从GRBL普通接收缓冲区取出一个字节，无数据时返回SERIAL_NO_DATA

// Reset and empty data in read buffer. Used by e-stop and reset.
void serial_reset_read_buffer();    //丢弃尚未处理的普通接收数据，不是复位串口外设

// Returns the number of bytes available in the RX serial buffer.
uint8_t serial_get_rx_buffer_available();  //获取普通接收缓冲区的剩余可用空间

// Returns the number of bytes used in the RX serial buffer.
// NOTE: Deprecated. Not used unless classic status reports are enabled in config.h.
uint8_t serial_get_rx_buffer_count();  //获取普通接收缓冲区当前已使用的空间

// Returns the number of bytes used in the TX serial buffer.
// NOTE: Not used except for debugging and ensuring no TX bottlenecks.
uint8_t serial_get_tx_buffer_count();   //获取发送缓冲区当前排队的字节数量


//增加的接口：从软件内部向普通接收缓冲区放入一个字符，不是硬件串口接收，也不能直接代替实时命令处理，
//比如后续做界面或者屏幕触摸到某个点位给的指令，在此生成送入grbl普通输入队列。
uint8_t serial_Inject_char(uint8_t c);
//增加的接口：从软件内部提交一段命令文本，缓冲区满时的处理需要在.c文件中核对
void serial_manual_Write(const char* message);




/*-------------------------------*/

//工程的辅助发送接口
void Serial_SendByte(uint8_t Byte);
void Serial_SendArray(uint8_t *Array, uint16_t Length);
void Serial_SendString(char *String);
void Serial_SendNumber(uint32_t Number, uint8_t Length);
void Serial_Printf(char *format, ...);









#endif
