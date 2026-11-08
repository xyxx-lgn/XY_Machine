#ifndef AT24C64_H
#define AT24C64_H

#include "main.h"

//AT24C64D的A0、A1、A2全部接地，所以器件的7位I2C地址为0x50，STM32 HAL要求传入左移一位后的地址，因此实际传给HAL的地址为0xA0
#define AT24C64_HAL_ADDRESS    (0x50u << 1)
#define AT24C64_PAGE_SIZE      32u     //AT24C64D一页可以写入32个字节
#define AT24C64_SIZE           8192u   //AT24C64D容量为64Kbit，也就是8192字节
#define AT24C64_MAX_ADDR       (AT24C64_SIZE - 1u)   //AT24C64D的有效存储地址范围为0x0000到0x1FFF

//写入一个字节，返回值：0成功，1通信失败，2地址超出范围
uint8_t AT24C64_WriteOneByte(uint16_t write_addr, uint8_t data);
//读取一个字节，返回值：0成功，1通信失败，2地址超出范围
uint8_t AT24C64_ReadOneByte(uint16_t read_addr, uint8_t *data);

//在同一页内写入多个字节，一次写入不能跨越32字节页边界
uint8_t AT24C64_WritePage(uint16_t write_addr, uint8_t *data, uint16_t length);
//写入任意长度的数据，函数内部会自动按照32字节页边界拆分写入
uint8_t AT24C64_WriteBytes(uint16_t write_addr, uint8_t *data, uint16_t length);
//连续读取任意长度的数据
uint8_t AT24C64_ReadBytes(uint16_t read_addr, uint8_t *data, uint16_t length);

//检查AT24C64D是否能够响应I2C地址，返回0表示器件在线
uint8_t AT24C64_Check(void);

//检测AT24C64读写是否正常
//void AT24C64_DebugCheck(void);
//void AT24C64_DebugWriteRead(void);

#endif
