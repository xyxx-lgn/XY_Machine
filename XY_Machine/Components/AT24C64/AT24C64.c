#include "AT24C64.h"
#include "i2c.h"

/*
 * HAL函数的超时时间。
 *
 * AT24C64D单字节写入完成通常只需要几毫秒，
 * 这里给100ms是为了覆盖异常情况下的等待。
 */
#define AT24C64_TIMEOUT_MS    100u


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
 *
 * EEPROM收到写数据后，需要一段时间将数据写入内部存储单元。
 * 在这段时间内，器件通常不会应答新的访问请求。
 *
 * HAL_I2C_IsDeviceReady()会不断检测器件是否重新应答。
 */
static uint8_t AT24C64_WaitReady(void)
{
    if (HAL_I2C_IsDeviceReady(&hi2c2,AT24C64_HAL_ADDRESS,10u,AT24C64_TIMEOUT_MS) != HAL_OK) 
		{
        return 1u;
    }

    return 0u;
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