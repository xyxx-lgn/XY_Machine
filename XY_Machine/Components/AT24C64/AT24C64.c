#include "AT24C64.h"
#include "i2c.h"

#include <stdio.h>
#include "usart.h"
#include "board_serial.h"


#define AT24C64_TIMEOUT_MS    100u   //HAL函数的超时时间


//检测AT24C64读写是否正常
//void AT24C64_DebugCheck(void)
//{
//    HAL_StatusTypeDef status;
//    uint32_t error_code;

//    status = HAL_I2C_IsDeviceReady(&hi2c2, AT24C64_HAL_ADDRESS, 10u, 100u);
//    error_code = HAL_I2C_GetError(&hi2c2);

//    if (status == HAL_OK)
//    {
//        board_serial_write("AT24C64 IsDeviceReady: OK\r\n");
//    }
//    else
//    {
//        char message[80];

//        (void)snprintf(message, sizeof(message),
//                       "AT24C64 IsDeviceReady: FAIL status=%d error=0x%08lX\r\n",
//                       (int)status,
//                       (unsigned long)error_code);

//        board_serial_write(message);
//    }
//}

//void AT24C64_DebugWriteRead(void)
//{
//    HAL_StatusTypeDef status;
//    uint32_t error_code;
//    uint8_t write_value = 0x5Au;
//    uint8_t read_value = 0u;
//    char message[100];

//    status = HAL_I2C_Mem_Write(&hi2c2,
//                               AT24C64_HAL_ADDRESS,
//                               0x1000u,
//                               I2C_MEMADD_SIZE_16BIT,
//                               &write_value,
//                               1u,
//                               AT24C64_TIMEOUT_MS);

//    error_code = HAL_I2C_GetError(&hi2c2);

//    (void)snprintf(message, sizeof(message),
//                   "EEPROM raw write: status=%d error=0x%08lX\r\n",
//                   (int)status,
//                   (unsigned long)error_code);

//    board_serial_write(message);

//    if (status != HAL_OK)
//    {
//        return;
//    }

//    HAL_Delay(10u);

//    status = HAL_I2C_Mem_Read(&hi2c2,
//                              AT24C64_HAL_ADDRESS,
//                              0x1000u,
//                              I2C_MEMADD_SIZE_16BIT,
//                              &read_value,
//                              1u,
//                              AT24C64_TIMEOUT_MS);

//    error_code = HAL_I2C_GetError(&hi2c2);

//    (void)snprintf(message, sizeof(message),
//                   "EEPROM raw read: status=%d error=0x%08lX value=0x%02X\r\n",
//                   (int)status,
//                   (unsigned long)error_code,
//                   (unsigned int)read_value);

//    board_serial_write(message);
//}




/*
 * 检查存储地址是否有效。
 *
 * AT24C64D只有0x0000到0x1FFF范围可用。
 */
static uint8_t AT24C64_AddressValid(uint16_t address)
{
    if (address > AT24C64_MAX_ADDR) {
        return 0u;
    }

    return 1u;
}


/*
 * 等待AT24C64D完成内部写周期。
 * 每次检测之间间隔1ms，避免在EEPROM仍忙时连续耗尽HAL重试次数
 */
static uint8_t AT24C64_WaitReady(void)
{
    uint32_t retry;

    for (retry = 0u; retry < 100u; ++retry)
    {
        if (HAL_I2C_IsDeviceReady(&hi2c2,AT24C64_HAL_ADDRESS,1u,2u) == HAL_OK)
        {
            return 0u;
        }

        HAL_Delay(1u);
    }

    return 1u;
}


/*
 * 向AT24C64D写入一个字节。
 *
 * AT24C64D使用16位存储地址，
 * 因此必须使用I2C_MEMADD_SIZE_16BIT。
 */
uint8_t AT24C64_WriteOneByte(uint16_t write_addr, uint8_t data)
{
    if (!AT24C64_AddressValid(write_addr)) {
        return 2u;
    }

    if (HAL_I2C_Mem_Write(&hi2c2,
                          AT24C64_HAL_ADDRESS,
                          write_addr,
                          I2C_MEMADD_SIZE_16BIT,
                          &data,
                          1u,
                          AT24C64_TIMEOUT_MS) != HAL_OK) 
		{
        return 1u;
    }

    //等待本次写入真正完成，防止下一次访问过早开始
    return AT24C64_WaitReady();
}


//从AT24C64D读取一个字节。
uint8_t AT24C64_ReadOneByte(uint16_t read_addr, uint8_t *data)
{
    if (data == NULL) {
        return 1u;
    }

    if (!AT24C64_AddressValid(read_addr)) {
        return 2u;
    }

    if (HAL_I2C_Mem_Read(&hi2c2,
                         AT24C64_HAL_ADDRESS,
                         read_addr,
                         I2C_MEMADD_SIZE_16BIT,
                         data,
                         1u,
                         AT24C64_TIMEOUT_MS) != HAL_OK) {
        return 1u;
    }

    return 0u;
}


/*
 * 在同一页内写入多个字节。
 *
 * AT24C64D每页为32字节。
 * 如果本次写入跨过页边界，必须返回错误，
 * 由上层的AT24C64_WriteBytes()负责拆分。
 */
uint8_t AT24C64_WritePage(uint16_t write_addr,
                          uint8_t *data,
                          uint16_t length)
{
    uint16_t page_offset;
    uint16_t page_remaining;

    if (data == NULL || length == 0u) {
        return 1u;
    }

    if (!AT24C64_AddressValid(write_addr)) {
        return 2u;
    }

    if (((uint32_t)write_addr + length) > AT24C64_SIZE) {
        return 2u;
    }

    page_offset = write_addr % AT24C64_PAGE_SIZE;
    page_remaining = AT24C64_PAGE_SIZE - page_offset;

    if (length > page_remaining) {
        return 3u;
    }

    if (HAL_I2C_Mem_Write(&hi2c2,
                          AT24C64_HAL_ADDRESS,
                          write_addr,
                          I2C_MEMADD_SIZE_16BIT,
                          data,
                          length,
                          AT24C64_TIMEOUT_MS) != HAL_OK) {
        return 4u;
    }

    return AT24C64_WaitReady();
}


/*
 * 写入任意长度的数据。
 *
 * 函数会自动将数据拆成多个不跨页的写入操作。
 */
uint8_t AT24C64_WriteBytes(uint16_t write_addr,
                           uint8_t *data,
                           uint16_t length)
{
    uint16_t offset = 0u;
    uint16_t chunk_length;
    uint16_t page_offset;
    uint16_t page_remaining;
    uint8_t result;

    if (data == NULL || length == 0u) {
        return 1u;
    }

    if (!AT24C64_AddressValid(write_addr)) {
        return 2u;
    }

    if (((uint32_t)write_addr + length) > AT24C64_SIZE) {
        return 2u;
    }

    while (offset < length) {
        page_offset = write_addr % AT24C64_PAGE_SIZE;
        page_remaining = AT24C64_PAGE_SIZE - page_offset;
        chunk_length = length - offset;

        if (chunk_length > page_remaining) {
            chunk_length = page_remaining;
        }

        result = AT24C64_WritePage(write_addr,
                                   &data[offset],
                                   chunk_length);

        if (result != 0u) {
            return result;
        }

        write_addr += chunk_length;
        offset += chunk_length;
    }

    return 0u;
}


/*
 * 从AT24C64D连续读取多个字节。
 *
 * 连续读取不受页边界限制。
 */
uint8_t AT24C64_ReadBytes(uint16_t read_addr,
                          uint8_t *data,
                          uint16_t length)
{
    if (data == NULL || length == 0u) {
        return 1u;
    }

    if (!AT24C64_AddressValid(read_addr)) {
        return 2u;
    }

    if (((uint32_t)read_addr + length) > AT24C64_SIZE) {
        return 2u;
    }

    if (HAL_I2C_Mem_Read(&hi2c2,
                         AT24C64_HAL_ADDRESS,
                         read_addr,
                         I2C_MEMADD_SIZE_16BIT,
                         data,
                         length,
                         AT24C64_TIMEOUT_MS) != HAL_OK) {
        return 1u;
    }

    return 0u;
}


/*
 * 检查AT24C64D是否在线。
 *
 * 返回0表示能够收到器件应答。
 */
uint8_t AT24C64_Check(void)
{
    return AT24C64_WaitReady();
}