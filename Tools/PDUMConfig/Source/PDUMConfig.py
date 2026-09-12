#!/usr/bin/env python3

import datetime
import os
import re
import stat
import sys
from argparse import ArgumentParser
from textwrap import dedent, indent
from xml.etree import ElementTree

__VERSION__ = '1.1.0'

parser = ArgumentParser()
parser.add_argument(
    '-z',
    '--zigbee',
    dest='zigbee_node_name',
    default='',
    help='Output configuration for Zigbee Protocol Stack for the specified node.',
)
parser.add_argument(
    '-o',
    '--output',
    dest='output_dir',
    default=os.path.curdir,
    help='Path to output the configuration into.',
)
parser.add_argument(
    '-f',
    '--config-file',
    dest='config_filename',
    help='Input configuration file.',
)
parser.add_argument(
    '-e',
    '--endian',
    dest='endian',
    choices=('BIG_ENDIAN', 'LITTLE_ENDIAN'),
    default='BIG_ENDIAN',
    help='Processor endianness.',
)

options = parser.parse_args()


def parse_configuration(filename: str) -> ElementTree.Element:
    config = ElementTree.parse(filename).getroot()

    for element in config.iter():
        if element.tag.startswith('{'):
            element.tag = element.tag.rpartition('}')[2]

    if config.tag != 'ZigbeeWirelessNetwork':
        raise ValueError("The root element must be 'ZigbeeWirelessNetwork'.")

    return config


def check_for_duplicate_names(nodes: list, n1: ElementTree.Element) -> bool:
    name = n1.get('Name')
    if not name:
        return False

    for n2 in nodes:
        if n2 is not n1 and n2.get('Name') == name:
            return True

    return False


def validate_configuration(config_node: ElementTree.Element) -> bool:
    node_name = config_node.get('Name')
    name_check = re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*')

    pdu_config = config_node.find('PDUConfiguration')
    if pdu_config is None:
        print("ERROR: The node '%s' must have a PDU Manager element.\n" % node_name)
        return False

    if not pdu_config.get('NumNPDUs'):
        print("ERROR: The PDU Manager for node '%s' must have a NumNPDUs attribute.\n" % node_name)
        return False

    if int(pdu_config.attrib['NumNPDUs'], 10) < 8:
        print("ERROR: The PDU Manager for node '%s' must have at least 8 NPDUs configured" % node_name)
        return False

    apdus = pdu_config.findall('APDUs')
    if not apdus:
        print("ERROR: The PDU Manager for node '%s' does not have any APDUs.\n" % node_name)
        return False

    for apdu in apdus:
        apdu_name = apdu.get('Name')
        if not apdu_name:
            print("ERROR: An APDU for node '%s' does not have a Name specified.\n" % node_name)
            return False

        if name_check.fullmatch(apdu_name) is None:
            print("ERROR: The APDU '%s' for node '%s' is not a valid C identifier.\n" % (apdu_name, node_name))
            return False

        if not apdu.get('Id'):
            print("ERROR: The APDU '%s' for node '%s' does not have an Id specified.\n"% (apdu_name, node_name))
            return False

        if not apdu.get('Size'):
            print("ERROR: The APDU '%s' for node '%s' does not have a Size specified.\n" % (apdu_name, node_name))
            return False

        if int(apdu.attrib['Size'], 10) < 1:
            print("ERROR: The APDU '%s' for node '%s' must have a Size of at least 1.\n" % (apdu_name, node_name))
            return False

        if not apdu.get('Instances'):
            print(
                "ERROR: The APDU '%s' for node '%s' does not have a number of Instances specified.\n"
                % (apdu_name, node_name)
            )
            return False

        if int(apdu.attrib['Instances'], 10) < 1:
            print(
                "ERROR: The APDU '%s' for node '%s' must have a number of Instances of at least 1.\n"
                % (apdu_name, node_name)
            )
            return False

        if check_for_duplicate_names(apdus, apdu):
            print(
                "ERROR: There are one or more APDUs with the name '%s' for node '%s'. "
                "APDUs must have unique names.\n" % (apdu_name, node_name)
            )
            return False

    return True


def output_c(output_dir: str, pdum_config: ElementTree.Element) -> None:
    fsp = os.path.join(output_dir, 'pdum_gen.c')
    if os.path.exists(fsp):
        os.chmod(fsp, os.stat(fsp).st_mode | stat.S_IWUSR)

    npdu_pool_size = int(pdum_config.attrib['NumNPDUs'])
    apdus = pdum_config.findall('APDUs')
    num_apdus = len(apdus)

    with open(fsp, 'w') as c_file:
        c_file.write(dedent("""\
            /****************************************************************************
             *
             *                 THIS IS A GENERATED FILE. DO NOT EDIT!
             *
             * MODULE:         PDUMConfig
             *
             * COMPONENT:      pdum_gen.c
             *
             * DATE:           %s
             *
             * AUTHOR:         NXP PDU Manager Configuration Tool
             *
             * DESCRIPTION:    PDU definitions
             *
             ****************************************************************************
             *
             * This software is owned by NXP B.V. and/or its supplier and is protected
             * under applicable copyright laws. All rights are reserved. We grant You,
             * and any third parties, a license to use this software solely and
             * exclusively on NXP products [NXP Microcontrollers such as JN5168, JN5179].
             * You, and any third parties must reproduce the copyright and warranty notice
             * and any other legend of ownership on each copy or partial copy of the
             * software.
             *
             * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
             * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
             * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
             * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
             * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
             * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
             * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
             * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
             * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
             * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
             * POSSIBILITY OF SUCH DAMAGE.
             *
             * Copyright NXP B.V. 2016. All rights reserved
             ****************************************************************************/

            /****************************************************************************/
            /***        Include files                                                 ***/
            /****************************************************************************/

            #include <jendefs.h>
            #include <pdum_nwk.h>
            #include <pdum_apl.h>

            /****************************************************************************/
            /***        Macro Definitions                                             ***/
            /****************************************************************************/

            /****************************************************************************/
            /***        Type Definitions                                              ***/
            /****************************************************************************/

            struct pdum_tsAPdu_tag {
                struct pdum_tsAPduInstance_tag *psAPduInstances;
                uint16 u16FreeListHeadIdx;
                uint16 u16Size;
                uint16 u16NumInstances;
            };

            struct pdum_tsAPduInstance_tag {
                uint8 *au8Storage;
                uint16 u16Size;
                uint16 u16NextAPduInstIdx;
                uint16 u16APduIdx;
            };

            typedef struct pdum_tsAPduInstance_tag pdum_tsAPduInstance;
            typedef struct pdum_tsAPdu_tag pdum_tsAPdu;

            /****************************************************************************/
            /***        Function Prototypes                                           ***/
            /****************************************************************************/

            /****************************************************************************/
            /***        Local Variables                                               ***/
            /****************************************************************************/

            /* NPDU Pool */
            PRIVATE pdum_tsNPdu s_asNPduPool[%d];

            /* APDU Pool */
            """) % (datetime.datetime.now().ctime(), npdu_pool_size))

        for apdu_index, apdu in enumerate(apdus):
            apdu_name = apdu.attrib['Name']
            apdu_size = int(apdu.attrib['Size'])
            apdu_instances = int(apdu.attrib['Instances'])

            c_file.writelines(
                'PRIVATE uint8 s_au8%sInstance%dStorage[%d];\n'
                % (apdu_name, instance_index, apdu_size)
                for instance_index in range(apdu_instances)
            )

            c_file.write(
                'PUBLIC pdum_tsAPduInstance s_as%sInstances[%d] = {\n'
                % (apdu_name, apdu_instances)
            )

            c_file.writelines(
                '    { s_au8%sInstance%dStorage, 0, 0, %d },\n'
                % (apdu_name, instance_index, apdu_index)
                for instance_index in range(apdu_instances)
            )

            c_file.write('};\n\n')

        c_file.write(dedent("""\
            /****************************************************************************/
            /***        Exported Variables                                            ***/
            /****************************************************************************/

            extern pdum_tsAPdu s_asAPduPool[%d];

            /****************************************************************************/
            /***        Exported Functions                                            ***/
            /****************************************************************************/

            extern void pdum_vNPduInit(pdum_tsNPdu *psNPduPool, uint16 u16Size);
            extern void pdum_vAPduInit(pdum_tsAPdu *asAPduPool, uint16 u16NumAPdus);

            PUBLIC void PDUM_vInit(void)
            {
                uint32 i;
                for (i = 0; i < %d; i++) {
                    s_asAPduPool[i].u16FreeListHeadIdx = 0;
                }
                pdum_vNPduInit(s_asNPduPool, %d);
                pdum_vAPduInit(s_asAPduPool, %d);
            }

            /****************************************************************************/
            /***        Local Functions                                               ***/
            /****************************************************************************/

            /****************************************************************************/
            /***        END OF FILE                                                   ***/
            /****************************************************************************/
            """) % (num_apdus, num_apdus, npdu_pool_size, num_apdus))

    os.chmod(fsp, os.stat(fsp).st_mode & (~stat.S_IWUSR | stat.S_IRUSR))


def output_asm(output_dir: str, pdum_config: ElementTree.Element, endian: str) -> None:
    fsp = os.path.join(output_dir, 'pdum_apdu.S')
    if os.path.exists(fsp):
        os.chmod(fsp, os.stat(fsp).st_mode | stat.S_IWUSR)

    asm_prefix = '@' if endian == 'BIG_ENDIAN' else '%'
    apdus = pdum_config.findall('APDUs')

    with open(fsp, 'w') as asm_file:
        asm_file.write(dedent("""\
                .global s_asAPduPool
                .section .data.s_asAPduPool,"aw",%sprogbits
                .align 4
                .type s_asAPduPool, %sobject
                .size s_asAPduPool, %d
            s_asAPduPool:

            """) % (asm_prefix, asm_prefix, len(apdus) * 12))

        for apdu in apdus:
            apdu_name = apdu.attrib['Name']

            asm_file.write(dedent("""\
                    .global pdum_%s
                pdum_%s:
                    .long s_as%sInstances
                    .short 0
                    .short %d
                    .short %d
                    .zero 2

                """) % (apdu_name, apdu_name, apdu_name, int(apdu.attrib['Size']), int(apdu.attrib['Instances'])))

    os.chmod(fsp, os.stat(fsp).st_mode & (~stat.S_IWUSR | stat.S_IRUSR))


def output_header(output_dir: str, pdum_config: ElementTree.Element) -> None:
    fsp = os.path.join(output_dir, 'pdum_gen.h')
    if os.path.exists(fsp):
        os.chmod(fsp, os.stat(fsp).st_mode | stat.S_IWUSR)

    apdus = pdum_config.findall('APDUs')

    with open(fsp, 'w') as h_file:
        h_file.write(dedent("""\
            /****************************************************************************
             *
             *                 THIS IS A GENERATED FILE. DO NOT EDIT!
             *
             * MODULE:         PDUMConfig
             *
             * COMPONENT:      pdum_gen.h
             *
             * DATE:           %s
             *
             * AUTHOR:         NXP PDU Manager Configuration Tool
             *
             * DESCRIPTION:    PDU definitions
             *
             *****************************************************************************
             *
             * This software is owned by NXP B.V. and/or its supplier and is protected
             * under applicable copyright laws. All rights are reserved. We grant You,
             * and any third parties, a license to use this software solely and
             * exclusively on NXP products [NXP Microcontrollers such as JN5168, JN5179].
             * You, and any third parties must reproduce the copyright and warranty notice
             * and any other legend of ownership on each copy or partial copy of the
             * software.
             *
             * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
             * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
             * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
             * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
             * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
             * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
             * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
             * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
             * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
             * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
             * POSSIBILITY OF SUCH DAMAGE.
             *
             * Copyright NXP B.V. 2016. All rights reserved
             ****************************************************************************/

            #ifndef _PDUM_GEN_H
            #define _PDUM_GEN_H

            #include <jendefs.h>
            #include <pdum_apl.h>

            /****************************************************************************/
            /***        Macro Definitions                                             ***/
            /****************************************************************************/

            /* APDUs */
            """) % datetime.datetime.now().ctime())

        h_file.writelines(
            '#define %s &pdum_%s\n' % (apdu.attrib['Name'], apdu.attrib['Name'])
            for apdu in apdus
        )

        h_file.write(dedent("""\

            /****************************************************************************/
            /***        Type Definitions                                              ***/
            /****************************************************************************/

            /****************************************************************************/
            /***        External Variables                                            ***/
            /****************************************************************************/

            /* APDUs */
            """))

        h_file.writelines(
            'extern const struct pdum_tsAPdu_tag pdum_%s;\n' % apdu.attrib['Name']
            for apdu in apdus
        )

        h_file.write(dedent("""\

            /****************************************************************************/
            /***        Exported Functions                                            ***/
            /****************************************************************************/

            PUBLIC void PDUM_vInit(void);

            /****************************************************************************/
            /****************************************************************************/
            /****************************************************************************/

            #endif
            """))

    os.chmod(fsp, os.stat(fsp).st_mode & (~stat.S_IWUSR | stat.S_IRUSR))


print('PDUMConfig - PDU Manager Configuration Tool v%s\n' % __VERSION__)

if len(sys.argv) == 1:
    print(indent(dedent("""\
        This software is owned by Jennic and/or its supplier and is protected
        under applicable copyright laws. All rights are reserved. We grant You,
        and any third parties, a license to use this software solely and
        exclusively on Jennic products. You, and any third parties must reproduce
        the copyright and warranty notice and any other legend of ownership on each
        copy or partial copy of the software.

        THIS SOFTWARE IS PROVIDED "AS IS". JENNIC MAKES NO WARRANTIES, WHETHER
        EXPRESS, IMPLIED OR STATUTORY, INCLUDING, BUT NOT LIMITED TO, IMPLIED
        WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE,
        ACCURACY OR LACK OF NEGLIGENCE. JENNIC SHALL NOT, IN ANY CIRCUMSTANCES,
        BE LIABLE FOR ANY DAMAGES, INCLUDING, BUT NOT LIMITED TO, SPECIAL,
        INCIDENTAL OR CONSEQUENTIAL DAMAGES FOR ANY REASON WHATSOEVER.

        (c) Copyright Jennic Ltd 2008. All rights reserved.
        """), '    '))
    print('For help: %s --help' % sys.argv[0])
    sys.exit(0)

if options.config_filename is None:
    print('ERROR: A configuration file must be specified.\n')
    sys.exit(-1)

if not options.zigbee_node_name:
    print('ERROR: A node must be specified.\n')
    sys.exit(-1)

if not os.path.exists(options.config_filename):
    print("ERROR: Unable to open configuration file '%s'.\n" % options.config_filename)
    sys.exit(-1)

if not os.path.exists(options.output_dir):
    print("ERROR: Output directory '%s' does not exist.\n" % options.output_dir)
    sys.exit(-1)

config = parse_configuration(options.config_filename)

config_node = None
coordinator = config.find('Coordinator')
if coordinator is not None and coordinator.get('Name') == options.zigbee_node_name:
    config_node = coordinator

if config_node is None:
    for node in config.findall('ChildNodes'):
        if node.get('Name') == options.zigbee_node_name:
            config_node = node
            break

if config_node is None:
    print("ERROR: Unable to find node '%s' in input configuration file.\n" % options.zigbee_node_name)
    sys.exit(-1)

if not validate_configuration(config_node):
    sys.exit(1)

pdu_config = config_node.find('PDUConfiguration')
output_header(options.output_dir, pdu_config)
output_c(options.output_dir, pdu_config)
output_asm(options.output_dir, pdu_config, options.endian)

print('Done.\n')
