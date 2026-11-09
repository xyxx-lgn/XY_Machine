/*
  cpu_map.h - CPU and pin mapping configuration file
  Part of Grbl

  Copyright (c) 2012-2016 Sungeun K. Jeon for Gnea Research LLC

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

/* The cpu_map.h files serve as a central pin mapping selection file for different
   processor types or alternative pin layouts. This version of Grbl officially supports
   only the Arduino Mega328p. */


#ifndef cpu_map_h
#define cpu_map_h

/*
 * 使用当前G431工程由CubeMX生成的引脚定义。
 *
 * main.h会引入STM32G4 HAL。
 */

#include "main.h"

#define STM32G431CBU6

#ifdef STM32G431CBU6 // (Arduino Uno) Officially supported by Grbl.

	// Define serial port pins and interrupt vectors. 
	//原版的串口中断机制和STM32不一致，此处可以直接屏蔽
//  #define SERIAL_RX     USART_RX_vect
//  #define SERIAL_UDRE   USART_UDRE_vect

  // Define step pulse output pins. NOTE: All step bit pins must be on the same port.
  //定义步进脉冲输出引脚。注：所有的步进脉冲引脚不必定义在同一个端口上，可以按照这个形式多定义端口，只需要对输出以及初始化处进行修改即可。
  #define STEP_GPIO_CLK_ENABLE()        	__HAL_RCC_GPIOA_CLK_ENABLE() //如果使用CubeIDE，按理说不用，但还是写上吧
  #define STEP_GPIO_PORT    				GPIOA       
	//实际引脚定义由CubeMX生成的main.h提供，对应的引脚宏也用main.h中自动生成的，避免与main.h冲突，因为我CubeMx上命名和下面一样所以注释
//  #define X_STEP_Pin      					GPIO_PIN_0  // PA0
//  #define Y_STEP_Pin      					GPIO_PIN_1  // PA1
//  #define Z_STEP_Pin      					GPIO_PIN_4  // PA4


	// 兼容原GRBL的位定义，这些是内部逻辑位编号，不是实际引脚编号，便于用变量一次性操作多个引脚,如果需要改位号需要对里面其他文件代码对应处也要修改，建议不修改
	#define X_STEP_BIT      			2
	#define Y_STEP_BIT      			3
	#define Z_STEP_BIT      			4
	#define STEP_MASK       			((1<<X_STEP_BIT)|(1<<Y_STEP_BIT)|(1<<Z_STEP_BIT)) //此处为11100

  // Define step direction output pins. NOTE: All direction pins must be on the same port.
  //定义方向电平输出引脚。注：所有的方向引脚不必定义在同一个端口上。
  #define DIRECTION_GPIO_CLK_ENABLE()    	__HAL_RCC_GPIOB_CLK_ENABLE()
  #define DIRECTION_GPIO_PORT    			GPIOB          //将CubeMx上生成的引脚名称对应过来，上面由于名称重合所以注释了
  #define X_DIRECTION_PIN        			X_DIR_Pin      // PB0
  #define Y_DIRECTION_PIN        			Y_DIR_Pin      // PB1
  #define Z_DIRECTION_PIN       			Z_DIR_Pin      // PB2

  // 兼容原GRBL的位定义
  #define X_DIRECTION_BIT   				5
  #define Y_DIRECTION_BIT   				6
  #define Z_DIRECTION_BIT   				7
  #define DIRECTION_MASK    				((1<<X_DIRECTION_BIT)|(1<<Y_DIRECTION_BIT)|(1<<Z_DIRECTION_BIT))


  // Define stepper driver enable/disable output pin.
  //定义三轴步进电机驱动器使能引脚
	//板端实际硬件：PA12，低电平使能，高电平禁用。
  #define STEPPERS_DISABLE_GPIO_CLK_ENABLE()    __HAL_RCC_GPIOA_CLK_ENABLE()
  #define STEPPERS_DISABLE_GPIO_PORT  			ENN_GPIO_Port
  #define STEPPERS_DISABLE_Pin    					ENN_Pin //PA12
	
  // 兼容原GRBL的位定义
  #define STEPPERS_DISABLE_BIT                  0
  #define STEPPERS_DISABLE_MASK                 (1 << STEPPERS_DISABLE_BIT)

  // Define homing/hard limit switch input pins and limit interrupt vectors.
  // NOTE: All limit bit pins must be on the same port, but not on a port with other input pins (CONTROL).
  //定义原点/硬限位开关输入引脚和限位中断向量
  //注意：所有的限位引脚必须使用同一个中断处理函数。
//  #define LIMIT_GPIO_CLK_ENABLE()           __HAL_RCC_GPIOC_CLK_ENABLE()
//  #define LIMIT_PORT       					GPIOC
//  #define X_LIMIT_PIN                       GPIO_PIN_1  // PC1
//  #define Y_LIMIT_PIN                       GPIO_PIN_2  // PC2
//  #define Z_LIMIT_PIN                       GPIO_PIN_3  // PC3

	//由于我的限位开关不是都在同一个端口上，所以需要对应进行修改
	//时钟初始化，把XYZ三轴的限位开关端口都初始化，但我们未使用Z轴的限位开关，但此处仍初始化
  #define LIMIT_GPIO_CLK_ENABLE()           \
	do{																				\
				__HAL_RCC_GPIOA_CLK_ENABLE();       \
				__HAL_RCC_GPIOB_CLK_ENABLE();  			\
		}while(0)
	#define X_LIMIT_GPIO_PORT    X_HOME_GPIO_Port   //每轴的端口单独定义，注意，其他文件使用限位开关也要修改
	#define Y_LIMIT_GPIO_PORT    Y_HOME_GPIO_Port
	#define Z_LIMIT_GPIO_PORT    Z_HOME_GPIO_Port
	//保留原GRBL引脚宏名称，引用CubeMX生成的实际引脚。
	#define X_LIMIT_PIN          X_HOME_Pin    //PB5
	#define Y_LIMIT_PIN          Y_HOME_Pin    //PA15
	#define Z_LIMIT_PIN          Z_HOME_Pin    //PA10
		
  // 原参考代码使用旧标准库的端口源和引脚源定义。
  // 当前由CubeMX生成的HAL_GPIO_Init配置EXTI路由，不再使用。
//  #define LIMIT_INT_PORTSOURCE 				GPIO_PortSourceGPIOC 	//中断源端口
//  #define X_LIMIT_INT_PINSOURCE				GPIO_PinSource1	   	//中断源引脚 注：这三个为标准库风格
//  #define Y_LIMIT_INT_PINSOURCE				GPIO_PinSource2		//中断源引脚
//  #define Z_LIMIT_INT_PINSOURCE				GPIO_PinSource3		//中断源引脚
		
	// EXTI线号：与GPIO引脚编号对应。
  #define X_LIMIT_INT_LINE					EXTI_LINE_5				//中断线
  #define Y_LIMIT_INT_LINE					EXTI_LINE_15			//中断线
  #define Z_LIMIT_INT_LINE					EXTI_LINE_10 			//中断线
		
  // NVIC中断号：引用CubeMX生成的定义
  // Y和Z使用同一个共享中断入口
  #define X_LIMIT_EXTI_IRQn                 X_HOME_EXTI_IRQn			    //中断请求号
  #define Y_LIMIT_EXTI_IRQn                 Y_HOME_EXTI_IRQn
  #define Z_LIMIT_EXTI_IRQn                 Z_HOME_EXTI_IRQn
  // 物理引脚掩码（用于GPIO操作）
//  #define LIMIT_PINS_MASK                   (X_LIMIT_PIN | Y_LIMIT_PIN | Z_LIMIT_PIN)

  // 物理引脚掩码：按GPIO端口分别组合。
  // 不再保留一个跨端口的LIMIT_PINS_MASK。
  #define LIMIT_GPIOA_PINS_MASK              (Y_LIMIT_PIN | Z_LIMIT_PIN)
  #define LIMIT_GPIOB_PINS_MASK              X_LIMIT_PIN

  // 中断优先级（必须定义）   (保留优先级配置描述,当前实际优先级由CubeMX配置，定义此宏不会自动生效)
  #define LIMIT_IRQ_PRIORITY               0  // 最高优先级
  // 触发边沿（必须定义）     (与当前CubeMX的上升沿配置保持一致,定义此宏不会自动修改MX_GPIO_Init中的触发方式)
  #define LIMIT_EXTI_TRIGGER               EXTI_TRIGGER_RISING  // 上降沿触发

  // 兼容原GRBL的位定义
  #define X_LIMIT_BIT      1
  #define Y_LIMIT_BIT      2
  #define Z_LIMIT_BIT      3
  #define LIMIT_MASK     ((1<<X_LIMIT_BIT)|(1<<Y_LIMIT_BIT)|(1<<Z_LIMIT_BIT)) // All limit bits
	
  // 本项目新修改：目前真正安装的限位，只包含XY。
  // 后续在grbl_limits.c中用于屏蔽未安装的Z限位。
  #define LIMIT_PRESENT_MASK                 ((1<<X_LIMIT_BIT)|(1<<Y_LIMIT_BIT))
	

  // Define user-control controls (cycle start, reset, feed hold) input pins.
  // NOTE: All CONTROLs pins must be on the same port and not on a port with other input pins (limits).
	// 定义实体控制输入：复位、进给暂停、启动/继续、门盖。
	
	// 当前PCB未配置这些实体输入。
  // 不使用原参考板的PB4~PB7，避免与已有功能冲突。
  // 后续通过GRBL串口实时命令进行相应控制。
	
	// 原参考板的GPIO映射，仅保留作参考，不启用。
//  #define CONTROL_GPIO_CLK_ENABLE()      __HAL_RCC_GPIOB_CLK_ENABLE()
//  #define CONTROL_GPIO_PORT              GPIOB
//  #define CONTROL_RESET_PIN              GPIO_PIN_4  // PB4
//  #define CONTROL_FEED_HOLD_PIN          GPIO_PIN_5  // PB5
//  #define CONTROL_CYCLE_START_PIN        GPIO_PIN_6  // PB6
//  #define CONTROL_SAFETY_DOOR_Pin 		 GPIO_PIN_7  // PB7

  // 未安装实体控制输入，不配置相应EXTI端口源和引脚源。
  // 以下原定义属于旧标准库风格。
//  #define CONTROL_INT_PORTSOURCE 		GPIO_PortSourceGPIOB 	//中断源端口
//  #define RESET_INT_PINSOURCE			GPIO_PinSource4			//中断源引脚：复位
//  #define FEED_HOLD_INT_PINSOURCE		GPIO_PinSource5			//中断源引脚：给进保持
//  #define CYCLE_START_INT_PINSOURCE	    GPIO_PinSource6			//中断源引脚：循环开始
//  #define SAFETY_DOOR_INT_PINSOURCE	    GPIO_PinSource7			//中断源引脚：紧急停车
	
  // 不为未安装的控制输入配置EXTI线。
//  #define RESET_INT_LINE				EXTI_LINE_4				//中断线：复位
//  #define FEED_HOLD_INT_LINE			EXTI_LINE_5				//中断线：给进保持
//  #define CYCLE_START_INT_LINE		    EXTI_LINE_6				//中断线：循环开始
//  #define SAFETY_DOOR_INT_LINE		    EXTI_LINE_7				//中断线：紧急停车

  // 不为未安装的控制输入启用NVIC中断。
  // 中断请求号
//  #define RESET_EXTI_IRQn                EXTI4_IRQn       // EXTI线4独立中断
//  #define FEED_HOLD_EXTI_IRQn            EXTI9_5_IRQn     // EXTI线5-9共享中断
//  #define CYCLE_START_EXTI_IRQn          EXTI9_5_IRQn     // 同上
//  #define SAFETY_DOOR_EXTI_IRQn          EXTI9_5_IRQn     // 同上

  // 兼容原GRBL的位定义，此处保留
  #define CONTROL_RESET_BIT         0  // Uno Analog Pin 0
  #define CONTROL_FEED_HOLD_BIT     1  // Uno Analog Pin 1
  #define CONTROL_CYCLE_START_BIT   2  // Uno Analog Pin 2
  #define CONTROL_SAFETY_DOOR_BIT   3  // Uno Analog Pin 1 NOTE: Safety door is shared with feed hold. Enabled by config define.
  #define CONTROL_MASK      ((1<<CONTROL_RESET_BIT)|(1<<CONTROL_FEED_HOLD_BIT)|(1<<CONTROL_CYCLE_START_BIT)|(1<<CONTROL_SAFETY_DOOR_BIT))
  #define CONTROL_INVERT_MASK   CONTROL_MASK // May be re-defined to only invert certain control pins.

  // Define probe switch input pin.
  //定义探针开关输入引脚
	//当前项目未按照也不使用探针
//  #define PROBE_GPIO_CLK_ENABLE()       __HAL_RCC_GPIOB_CLK_ENABLE()
//  #define PROBE_Pin                     GPIO_PIN_1	//PB1
//  #define PROBE_GPIO_Port 				GPIOB

  // 兼容原GRBL的位定义
  #define PROBE_BIT       5  // Uno Analog Pin 5
  #define PROBE_MASK      (1<<PROBE_BIT)

  // 写字机专用新增：抬笔/落笔控制
  // 抬落笔机构类型，仅用于编译配置。
  #define PEN_LIFT_STEPPER                  0
  #define PEN_LIFT_SERVO                    1

  /*
   * 当前选择Z轴步进电机。
   * 如果以后切换舵机，改为PEN_LIFT_SERVO并重新编译。
   */
  #define PEN_LIFT_MODE                     PEN_LIFT_STEPPER

  #if PEN_LIFT_MODE == PEN_LIFT_STEPPER
    /*
     * Z轴步进抬落笔。
     *
     * STEP、DIR、ENN继续使用前面已经定义的三轴映射，
     * 此处不再重复定义引脚。
     *
     * 约定Z正方向为抬笔。
     *
     * 以下位置属于当前工作坐标系：
     * 落笔位置：纸面接触位置Z=0。
     * 抬笔位置：高于纸面2mm。
     *
     * 数值暂定，需在确认Z传动参数和建立工作零点后调整。
     */
    #define PEN_UP_Z_MM                     4.0f
    #define PEN_DOWN_Z_MM                   (-0.4f)
		
		// Z机器坐标的允许范围，取当前已经实测过的范围。
	#define PEN_Z_MIN_POSITION      (-1.5f)
	#define PEN_Z_MAX_POSITION       5.0f
		
    /*
     * 抬落笔进给速度，单位mm/min。
     * 后续由运动请求使用，不直接决定定时器频率。
     * 当前数值暂定。
     */
    #define PEN_Z_FEED_MM_MIN               6.0f
		
		
  #elif PEN_LIFT_MODE == PEN_LIFT_SERVO

    /*
     * 舵机抬落笔接口。
     *
     * 当前PCB预留：
     * PB14，TIM15_CH1。
     *
     * 引脚定义引用CubeMX生成的main.h。
     * 实际PWM初始化后续在舵机驱动中处理。
     */
    #define PEN_SERVO_PWM_GPIO_Port         SERVO_PWM_GPIO_Port
    #define PEN_SERVO_PWM_PIN               SERVO_PWM_Pin

    /*
     * 保留参考工程的参数名称。
     * 单位为us，是PWM高电平宽度，不是角度。
     *
     * 抬笔和落笔的对应关系及安全范围后续实测调整。
     */
    #define PEN_UP_POSITION                 2000
    #define PEN_DOWN_POSITION               1000

  #else

    // 配置值必须是上面定义的两种机构之一。
    #error "Invalid PEN_LIFT_MODE"

  #endif
	
	
	
	//当前暂不启用激光输出，后续完成PWM引脚映射后改为1。
	#define GRBL_LASER_OUTPUT_ENABLE    0
	
	

#endif

/*
#ifdef CPU_MAP_CUSTOM_PROC
  // For a custom pin map or different processor, copy and edit one of the available cpu
  // map files and modify it to your needs. Make sure the defined name is also changed in
  // the config.h file.
#endif
*/

#endif
