###############################################################################
#
# MODULE:      Config.mk
#
# DESCRIPTION: ZBPro stack configuration. Defines tool, library and
#              header file details for building an app using the ZBPro stack
#
###############################################################################
# This software is owned by NXP B.V. and/or its supplier and is protected
# under applicable copyright laws. All rights are reserved. We grant You,
# and any third parties, a license to use this software solely and
# exclusively on NXP products [NXP Microcontrollers such as JN514x, JN516x, JN517x].
# You, and any third parties must reproduce the copyright and warranty notice
# and any other legend of ownership on each copy or partial copy of the software.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# Copyright NXP B.V. 2015-2017. All rights reserved
#
###############################################################################

###############################################################################
# Tools

PYTHON ?= $(shell command -v python3 2> /dev/null)

PDUMCONFIG = $(PYTHON) $(TOOL_BASE_DIR)/PDUMConfig/Source/PDUMConfig.py
ZPSCONFIG = $(PYTHON) $(TOOL_BASE_DIR)/ZPSConfig/Source/ZPSConfig.py
JET = $(PYTHON) $(TOOL_BASE_DIR)/OTAUtils/Source/JET.py

STACK_SIZE ?= 5000
MINIMUM_HEAP_SIZE ?= 2000

###############################################################################
# ROM based software components

INCFLAGS += -I$(COMPONENTS_BASE_DIR)/Mac/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/MicroSpecific/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/MiniMAC/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/MMAC/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/TimerServer/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/Random/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/ZigbeeCommon/Include

ifeq ($(JENNIC_MAC), MAC)
    APPLIBS += ZPSMAC
    CFLAGS  += -DREDUCED_ZIGBEE_MAC_BUILD
    REDUCED_MAC_LIB_SUFFIX = ZIGBEE_
else
    JENNIC_MAC = MiniMacShim
    JENNIC_MAC_PLATFORM ?= SOC
    #APPLIBS += ZPSMAC_Mini

    # Determine correct MAC library for platform
    ifeq ($(JENNIC_MAC_PLATFORM), SOC)
        APPLIBS += ZPSMAC_Mini_SOC
    else
        ifeq ($(JENNIC_MAC_PLATFORM), SERIAL)
            APPLIBS += ZPSMAC_Mini_SERIAL
            APPLIBS += SerialMiniMacUpper
        else
            ifeq ($(JENNIC_MAC_PLATFORM), MULTI)
                APPLIBS += ZPSMAC_Mini_MULTI
                APPLIBS += SerialMiniMacUpper
            endif
        endif
    endif
endif

###############################################################################
# RAM based software components

CFLAGS += -DPDM_USER_SUPPLIED_ID
CFLAGS += -DPDM_NO_RTOS

ifeq ($(PDM_BUILD_TYPE), _EEPROM)
    CFLAGS += -DPDM$(PDM_BUILD_TYPE)
else
    ifeq ($(PDM_BUILD_TYPE), _EXTERNAL_FLASH)
        CFLAGS += -DPDM$(PDM_BUILD_TYPE)
    else
        ifeq ($(PDM_BUILD_TYPE), _NONE)
            CFLAGS += -DPDM$(PDM_BUILD_TYPE)
        else
            $(error PDM_BUILD_TYPE must be defined please define PDM_BUILD_TYPE=_EEPROM or PDM_BUILD_TYPE=_EXTERNAL_FLASH)
        endif
    endif
endif

# NB Order is significant for GNU linker

APPLIBS += PWRM
APPLIBS += ZPSTSV
APPLIBS += AES_SW
APPLIBS += PDUM
APPLIBS += ZPSAPL
APPLIBS += Random

INCFLAGS += $(addsuffix /Include,$(addprefix -I$(COMPONENTS_BASE_DIR)/,$(APPLIBS)))
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/PDM/Include

ifneq ($(PDM_BUILD_TYPE), _NONE)
    APPLIBS +=PDM$(PDM_BUILD_TYPE)_NO_RTOS
endif

ifeq ($(TRACE), 1)
    CFLAGS += -DDBG_ENABLE
    $(info Building trace version ...)
    APPLIBS +=DBG
else
    INCFLAGS += -I$(COMPONENTS_BASE_DIR)/DBG/Include
endif

ifeq ($(OPTIONAL_STACK_FEATURES), 1)
    ifneq ($(ZBPRO_DEVICE_TYPE), ZED)
        APPLIBS += ZPSIPAN
    else
        APPLIBS += ZPSIPAN_ZED
    endif
endif

ifeq ($(OPTIONAL_STACK_FEATURES), 2)
    ifneq ($(ZBPRO_DEVICE_TYPE), ZED)
        APPLIBS += ZPSGP
    else
        APPLIBS += ZPSGP_ZED
    endif
endif

ifeq ($(OPTIONAL_STACK_FEATURES), 3)
    ifneq ($(ZBPRO_DEVICE_TYPE), ZED)
        APPLIBS += ZPSGP
        APPLIBS += ZPSIPAN
    else
        APPLIBS += ZPSGP_ZED
        APPLIBS += ZPSIPAN_ZED
    endif
endif

###############################################################################
# Paths to components provided as source

APPSRC += ZQueue.c
APPSRC += ZTimer.c
APPSRC += app_zps_link_keys.c

###############################################################################
# Paths to network and application layer libs for stack config tools

INCFLAGS += -I$(COMPONENTS_BASE_DIR)/ZPSMAC/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/ZPSNWK/Include
INCFLAGS += -I$(COMPONENTS_BASE_DIR)/ZigbeeCommon/Include

ifeq ($(ZBPRO_DEVICE_TYPE), ZCR)
    APPLIBS += ZPSNWK
else
    ifeq ($(ZBPRO_DEVICE_TYPE), ZED)
        APPLIBS += ZPSNWK_ZED
    else
        $(error ZBPRO_DEVICE_TYPE must be set to either ZCR or ZED)
    endif
endif

ifeq ($(ZBPRO_DEVICE_TYPE), ZCR)
    ZPS_NWK_LIB = $(COMPONENTS_BASE_DIR)/Library/libZPSNWK_$(JENNIC_CHIP_FAMILY).a
endif

ifeq ($(ZBPRO_DEVICE_TYPE), ZED)
    ZPS_NWK_LIB = $(COMPONENTS_BASE_DIR)/Library/libZPSNWK_ZED_$(JENNIC_CHIP_FAMILY).a
endif

ZPS_APL_LIB = $(COMPONENTS_BASE_DIR)/Library/libZPSAPL_$(JENNIC_CHIP_FAMILY).a

LDFLAGS += -Wl,--gc-sections

###############################################################################
