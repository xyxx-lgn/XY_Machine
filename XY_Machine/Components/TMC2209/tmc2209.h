#ifndef TMC2209_H
#define TMC2209_H

#include <stdbool.h>
#include <stdint.h>

/* 用到的寄存器。 */
#define TMC2209_REG_GCONF          0x00u
#define TMC2209_REG_IFCNT          0x02u
#define TMC2209_REG_IOIN           0x06u
#define TMC2209_REG_CHOPCONF       0x6Cu
/*
 * NODECONF：节点通信配置寄存器。
 *
 * 地址为0x03，只写。
 * bit11～8是SENDDELAY字段。
 */
#define TMC2209_REG_NODECONF               0x03u

/*
 * SENDDELAY=2。
 *
 * 因为字段位于bit11～8，
 * 所以需要将数值2左移8位。
 *
 * 最终写入值为0x00000200。
 */
#define TMC2209_NODECONF_SENDDELAY_2        (2UL << 8)

/* 运行/保持电流控制寄存器，只写。 */
#define TMC2209_REG_IHOLD_IRUN             0x10u

/* 内部速度发生器控制寄存器，只写。 */
#define TMC2209_REG_VACTUAL                0x22u

/*
 * GCONF.bit0：
 * 0：使用内部参考；
 * 1：使用VREF进行模拟电流缩放。
 */
#define TMC2209_GCONF_I_SCALE_ANALOG       (1UL << 0)

/*
 * GCONF.bit1：
 * 0：使用外部采样电阻；
 * 1：使用内部采样方式。
 */
#define TMC2209_GCONF_INTERNAL_RSENSE      (1UL << 1)

/*
 * CHOPCONF.bit17：
 * 1：使用较低的采样参考电压范围，
 * 本次计算采用约0.180V。
 */
#define TMC2209_CHOPCONF_VSENSE            (1UL << 17)

/* GCONF中的配置位。 */
#define TMC2209_GCONF_PDN_DISABLE       (1UL << 6)
#define TMC2209_GCONF_MSTEP_REG_SELECT  (1UL << 7)

/* CHOPCONF中，bit27～24为MRES细分选择字段。 */
#define TMC2209_CHOPCONF_MRES_MASK      (0x0FUL << 24)

/* MRES=4：每个整步使用16个外部STEP脉冲。 */
#define TMC2209_CHOPCONF_MRES_16        (4UL << 24)

/*
 * IOIN最高8位是芯片版本号。
 * 提供的TMC2209手册列出的版本值为0x21。
 */
#define TMC2209_EXPECTED_VERSION  0x21u

/*
 * 通信结果。
 *
 * 不只返回true/false，是为了调试时能区分：
 * 参数错误、超时、回显错误、CRC错误等。
 */
typedef enum
{
    TMC2209_OK = 0,

    TMC2209_ERROR_ARGUMENT,
    TMC2209_ERROR_BUSY,
    TMC2209_ERROR_TX,
    TMC2209_ERROR_RX_START,
    TMC2209_ERROR_TIMEOUT,
    TMC2209_ERROR_UART,
    TMC2209_ERROR_ECHO,
    TMC2209_ERROR_FRAME,
    TMC2209_ERROR_CRC,
    TMC2209_ERROR_VERSION,
    TMC2209_ERROR_VERIFY  /* 芯片没有按预期接受写入，或回读配置不一致。 */
} tmc2209_result_t;

/*
 * 一颗TMC2209的软件描述。
 *
 * address：由MS1/MS2设置的UART地址；
 * online ：最近一次探测是否成功；
 * ioin   ：最近一次探测读到的IOIN值。
 *
 * online只是最近一次探测结果，
 * 不代表以后每次通信都一定正常。
 */
typedef struct
{
    uint8_t address;
    bool online;
    uint32_t ioin;
} tmc2209_device_t;

/*
 * 读取一个寄存器。
 *
 * address：0～3；
 * reg    ：寄存器地址，最高位必须为0；
 * value  ：成功时保存读取结果。
 *
 * 当前实现供主循环或任务调用。
 * 不允许在中断中调用，也不能多任务同时调用。
 */
tmc2209_result_t tmc2209_read_register(uint8_t address, uint8_t reg, uint32_t *value);

/*
 * 读取IOIN，并检查芯片版本号。
 * 调用后更新device中的online和ioin。
 */
tmc2209_result_t tmc2209_probe(tmc2209_device_t *device);

/* 将错误码转换为便于串口打印的字符串。 */
const char *tmc2209_result_string(tmc2209_result_t result);


/*
 * 发送一个写寄存器报文，并检查发送回显。
 *
 * 注意：返回OK只代表传输和回显正常，
 * 不代表驱动已经接受写入。
 */
tmc2209_result_t tmc2209_write_register(uint8_t address, uint8_t reg, uint32_t value);

/*
 * 写入前后读取IFCNT，要求计数增加1。
 *
 * 当前没有其他代码同时向UART4写寄存器，
 * 才能使用这种严格的“增加1”检查。
 */
tmc2209_result_t tmc2209_write_checked(uint8_t address, uint8_t reg, uint32_t value);

/*
 * 使用寄存器选择细分，并设置为16细分。
 *
 * 只修改相关字段，保留其余配置。
 * 不设置电流，不使能电机。
 */
tmc2209_result_t tmc2209_set_microstep_16(uint8_t address);

/*
 * 设置当前节点的UART读回复延时。
 *
 * 多节点共用总线时，将SENDDELAY设为2。
 * 通过IFCNT验证写入是否被芯片接受。
 *
 * 不修改电流、不使能电机。
 */
tmc2209_result_t tmc2209_set_send_delay(uint8_t address);

/*
 * 配置首次低速测试使用的电流。
 *
 * irun、ihold都是0～31的寄存器编码，不是安培数。
 *
 * 本函数：
 * 1. 使用外部STEP/DIR；
 * 2. 使用内部参考；
 * 3. 使用外部采样电阻；
 * 4. 设置VSENSE=1；
 * 5. 设置运行、保持电流。
 *
 * 调用前必须保持ENN高，禁止电机输出。
 */
tmc2209_result_t tmc2209_set_test_current(uint8_t address, uint8_t irun, uint8_t ihold);

#endif
