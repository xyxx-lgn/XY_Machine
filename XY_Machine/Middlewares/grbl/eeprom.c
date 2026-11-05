#include "AT24C64.h"
#include "settings.h"
#include "grbl_serial.h"

//从EEPROM读取一个字节，GRBL读取失败时返回0xFF
unsigned char eeprom_get_char(unsigned int addr)
{
    unsigned char data = 0xFFu;

    if (AT24C64_ReadOneByte((uint16_t)addr, &data) != 0u) {
        return 0xFFu;
    }

    return data;
}

//向EEPROM写入一个字节
void eeprom_put_char(unsigned int addr, unsigned char new_value)
{
  if (AT24C64_WriteOneByte((uint16_t)addr, new_value)  != 0) 
		{
    serial_write('A');
    serial_write('T');
    serial_write('2');
    serial_write('4');
    serial_write('C');
    serial_write('0');
    serial_write('2');
    serial_write('E');
    serial_write('E');
    serial_write('R');
    serial_write('O');
    serial_write('M');
  } 

}

//将一段数据写入EEPROM，并在末尾写入校验和
void memcpy_to_eeprom_with_checksum(unsigned int destination, char *source, unsigned int size)
{
  unsigned char checksum = 0;
  for (; size > 0; size--) {
    checksum = (checksum << 1) | (checksum >> 7);
    checksum += *source;
    eeprom_put_char(destination++, *(source++));
  }
  eeprom_put_char(destination, checksum);
}

//从EEPROM读取一段数据，并检查末尾校验和，返回1表示校验成功，返回0表示校验失败
int memcpy_from_eeprom_with_checksum(char *destination, unsigned int source, unsigned int size)
{
  unsigned char data, checksum = 0;
  for (; size > 0; size--) {
    data = eeprom_get_char(source++);
    checksum = (checksum << 1) | (checksum >> 7);
    checksum += data;
    *(destination++) = data;
  }
  return (checksum == eeprom_get_char(source));
}
