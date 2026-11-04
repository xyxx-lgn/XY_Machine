/*
  grbl.h - main Grbl include file
  Part of Grbl

  Copyright (c) 2015-2016 Sungeun K. Jeon for Gnea Research LLC

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

#ifndef grbl_h
#define grbl_h

// Grbl versioning system
#define GRBL_VERSION "1.1h"
#define GRBL_VERSION_BUILD "20190830"

// Define standard libraries used by Grbl.
//AVR引用的头文件，在STM32中没有，直接注释，替换成STM32中的头文件
//#include <avr/io.h>
//#include <avr/pgmspace.h>
//#include <avr/interrupt.h>
//#include <avr/wdt.h>
//#include <util/delay.h>
#include <math.h>
#include <inttypes.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>

/*
 * 当前STM32G431工程的平台头文件。
 * 提供STM32G4 HAL和CubeMX生成的引脚定义。
 */
#include "main.h"

// Define the Grbl system include files. NOTE: Do not alter organization.
#include "config.h"
#include "nuts_bolts.h"
#include "settings.h"
#include "system.h"
#include "defaults.h"
#include "cpu_map.h"
#include "planner.h"
#include "coolant_control.h"
#include "eeprom.h"
#include "gcode.h"
#include "grbl_limits.h"
#include "motion_control.h"
#include "print.h"
#include "probe.h"
#include "protocol.h"
#include "report.h"
#include "grbl_serial.h"
#include "spindle_control.h"
#include "stepper.h"
#include "jog.h"


int grbl_Start(void);                 //GRBL运行入口
void printPgmString(const char *s);   //字符串输出接口
#endif



