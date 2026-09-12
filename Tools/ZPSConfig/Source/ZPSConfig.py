#!/usr/bin/env python3

import datetime
import os
import re
import stat
import struct
import subprocess
import sys
from argparse import ArgumentParser
from textwrap import dedent, fill, indent
from typing import Optional, TextIO
from xml.etree import ElementTree

__VERSION__ = '1.3.0'

XSI_NAMESPACE = 'http://www.w3.org/2001/XMLSchema-instance'

parser = ArgumentParser()
parser.add_argument(
    '-n',
    '--node-name',
    dest='zigbee_node_name',
    default='',
    help='Name of node to generate configuration for.',
)
parser.add_argument(
    '-t',
    '--target',
    dest='target_hardware',
    default='JN5139',
    help='Target hardware platform for the node.',
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
    '-l',
    '--zigbee-nwk-lib',
    dest='zigbee_nwk_lib_fsp',
    default='libZPSNWK_JN513x.a',
    help='Zigbee target software library.',
)
parser.add_argument(
    '-e',
    '--Endian',
    dest='endian',
    choices=('BIG_ENDIAN', 'LITTLE_ENDIAN'),
    default='BIG_ENDIAN',
    help='Processor endianness.',
)
parser.add_argument(
    '-a',
    '--zigbee-apl-lib',
    dest='zigbee_apl_lib_fsp',
    default='libZPSAPL_JN513x.a',
    help='Zigbee target software library.',
)
parser.add_argument(
    '-y',
    '--optional_features',
    action='store_true',
    dest='optional_features',
    default=False,
    help='Enable optional features from the diagram.',
)
parser.add_argument(
    '-c',
    '--compiler-tools',
    dest='tools_dir',
    default='',
    help='Path to the compiler tools.',
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


def get_lib_obj_size(objdump: str, lib: str, endian: str) -> int:
    proc = subprocess.Popen(
        [objdump, '-s', '-j.libobjsize', lib],
        stdout=subprocess.PIPE,
    )
    data = proc.communicate()[0]
    idx = data.find(b'libobjsize')
    size = int('0x' + data[idx:idx + 100].split()[2].decode('ascii'), 16)

    if endian != 'BIG_ENDIAN':
        packed_size = struct.pack('<I', size)
        size = struct.unpack('>I', packed_size)[0]

    return size


def get_apl_server_context_size(server: str, objdump: str, lib: str, endian: str) -> int:
    section_name = 'zps_apl_Zdo%sContextSize' % server

    try:
        proc = subprocess.Popen(
            [objdump, '-s', '-j.' + section_name, lib],
            stdout=subprocess.PIPE,
        )
        data = proc.communicate()[0]
        idx = data.find(section_name.encode('ascii'))
        size = int('0x' + data[idx:idx + 100].split()[2].decode('ascii'), 16)

        if endian != 'BIG_ENDIAN':
            packed_size = struct.pack('<I', size)
            size = struct.unpack('>I', packed_size)[0]

        return size
    except Exception:  # noqa
        print("ERROR: Unable to locate '.%s' section in library file '%s'\n" % (section_name, lib))
        sys.exit(10)


def calculate_channel_mask(channel_mask: ElementTree.Element) -> int:
    channel_mask_value = 0

    for channel in range(11, 26 + 1):
        if channel_mask.attrib['Channel%d' % channel].lower() == 'true':
            channel_mask_value |= 1 << channel

    return channel_mask_value


def format_c_initializer(values: list) -> str:
    return fill(
        ', '.join(values),
        width=120,
        initial_indent='    ',
        subsequent_indent='    ',
        break_long_words=False,
        break_on_hyphens=False,
    )


def format_cluster_discovery_flags(clusters: list) -> str:
    discovery_flags = []
    discovery_mask = 0

    for cluster_index, cluster in enumerate(clusters):
        if cluster.get('Discoverable', 'true').lower() != 'false':
            discovery_mask |= 1 << (cluster_index & 7)

        if cluster_index & 7 == 7:
            discovery_flags.append('0x%02x' % discovery_mask)
            discovery_mask = 0

    if len(clusters) & 7:
        discovery_flags.append('0x%02x' % discovery_mask)

    return format_c_initializer(discovery_flags)


def find_profile(profile_name: str) -> Optional[ElementTree.Element]:
    for profile in config.findall('Profiles'):
        if profile.get('Name') == profile_name:
            return profile

    return None


def find_cluster(cluster_name: str) -> Optional[ElementTree.Element]:
    for profile in config.findall('Profiles'):
        for cluster in profile.findall('Clusters'):
            if cluster.get('Name') == cluster_name:
                return cluster

    return None


def find_network_key(key_id: str) -> Optional[ElementTree.Element]:
    ref = key_id.split('->', 2)
    node = find_node(ref[0])

    if node is None:
        return None

    trust_center = node.find('TrustCenter')
    if trust_center is None:
        return None

    key_name = 'zpscfg:' + ref[1]
    for key in trust_center.findall('Keys'):
        if key.get('{%s}type' % XSI_NAMESPACE) == key_name:
            return key

    return None


def network_key_str(key_node: ElementTree.Element) -> str:
    key = int(key_node.attrib['Key'], 0)
    key_str = ''

    for i in range(0, 16):
        if 0 != i:
            key_str += ', '
        key_str += '0x%02x' % ((key & 255 << 8 * i) >> 8 * i)

    return key_str


def find_apdu(node: Optional[ElementTree.Element], apdu_id: Optional[str]) -> Optional[ElementTree.Element]:
    if node is not None:
        pdu_config = node.find('PDUConfiguration')
        if pdu_config is not None:
            for apdu in pdu_config.findall('APDUs'):
                if apdu.get('Id') == apdu_id:
                    return apdu

    return None


def find_node(node_name: str) -> Optional[ElementTree.Element]:
    coordinator = config.find('Coordinator')
    if coordinator is not None and node_name == coordinator.get('Name'):
        return coordinator

    for child_node in config.findall('ChildNodes'):
        if node_name == child_node.get('Name'):
            return child_node

    return None


def check_for_duplicate_names(nodes: list, n1: ElementTree.Element) -> bool:
    name = n1.get('Name')
    if not name:
        return False

    for n2 in nodes:
        if n2 is not n1 and n2.get('Name') == name:
            return True

    return False


def check_for_duplicate_ids(nodes: list, n1: ElementTree.Element) -> bool:
    for n2 in nodes:
        if n2 is not n1 and n2.get('Id') and int(n1.attrib['Id'], 0) == int(n2.attrib['Id'], 0):
            return True

    return False


def validate_configuration(node_name: str) -> bool:
    name_check = re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*')

    # Network configuration

    if not config.get('Version'):
        print('ERROR: The input file does not specify a version\n')
        return False
    elif config.attrib['Version'] != '1.1':
        print("ERROR: Unrecognised input file version '%s'\n" % config.attrib['Version'])
        return False

    if 'DefaultExtendedPANId' not in config.attrib:
        print(
            'WARNING: The input configuration file does not contain a Default Extended '
            'PAN Id for the ZigBee PRO Wireless Network.\n'
        )

    if not config.get('MaxNumberNodes'):
        print(
            'ERROR: The input configuration file does specify a MaxNumberNodes for the ZigBee PRO Wireless Network.\n'
        )
        return False
    elif int(config.attrib['MaxNumberNodes'], 0) < 2:
        print("ERROR: The 'MaxNumberNodes' attribute for a ZigBee PRO Wireless Network must be at least 2 nodes.\n")
        return False

    # Profiles and clusters

    profiles = config.findall('Profiles')
    if not profiles:
        print('ERROR: The input configuration file does not contain any Profile elements.\n')
        return False

    zdp_profile_found = False

    for profile in profiles:
        profile_name = profile.get('Name')

        if not profile.get('Id'):
            if profile_name:
                print("ERROR: Profile '%s' does not have an Id attribute." % profile_name)
            else:
                print('ERROR: A Profile element in the input configuration file does not have an Id or Name attribute.')
            return False

        profile_id = int(profile.attrib['Id'], 0)
        if not profile_name:
            print("ERROR: Profile id '%d' does not have a Name attribute.\n" % profile_id)
            return False

        if name_check.fullmatch(profile_name) is None:
            print("ERROR: Profile name '%s' is not a valid C identifier.\n" % profile_name)
            return False

        if profile_id == 0:
            zdp_profile_found = True
        elif profile_id > 65535 or profile_id < 0:
            print("ERROR: The Id of Profile '%s' must be in the range 1-65535" % profile_name)
            return False

        if check_for_duplicate_names(profiles, profile):
            print(
                "ERROR: There are one or more Profiles with the name '%s'. "
                "Profiles must have unique names.\n" % profile_name
            )
            return False

        if check_for_duplicate_ids(profiles, profile):
            print(
                "ERROR: There are one or more Profiles with the Id '%s'. "
                "Profiles must have unique Ids.\n" % profile.attrib['Id']
            )
            return False

        clusters = profile.findall('Clusters')
        if not clusters:
            print("ERROR: The Profile '%s' does not contain any Cluster elements.\n" % profile_name)
            return False

        for cluster in clusters:
            cluster_name = cluster.get('Name')

            if not cluster.get('Id'):
                if cluster_name:
                    print(
                        "ERROR: Cluster '%s' for Profile '%s' does not have an Id attribute."
                        % (cluster_name, profile_name)
                    )
                else:
                    print("ERROR: A Cluster for Profile '%s' does not have an Id or Name attribute." % profile_name)
                return False

            cluster_id = int(cluster.attrib['Id'], 0)
            if not cluster_name:
                print(
                    "ERROR: Cluster Id '%d' in Profile id '%s' does not have a Name attribute.\n"
                    % (cluster_id, profile_name)
                )
                return False

            if name_check.fullmatch(cluster_name) is None:
                print("ERROR: Cluster name '%s' is not a valid C identifier.\n" % cluster_name)
                return False

            if cluster_id > 65535 or cluster_id < 0:
                print(
                    "ERROR: The Id of Cluster '%s' for Profile '%s' must be in the range 1-65535"
                    % (cluster_name, profile_name)
                )
                return False

            if check_for_duplicate_names(clusters, cluster):
                print(
                    "ERROR: There are one or more Clusters with the name '%s' in Profile '%s'. "
                    "Clusters must have unique names.\n" % (cluster_name, profile_name)
                )
                return False

            if check_for_duplicate_ids(clusters, cluster):
                print(
                    "ERROR: There are one or more Clusters with the Id '%s' in Profile '%s'. "
                    "Clusters must have unique Ids.\n" % (cluster.attrib['Id'], profile_name)
                )
                return False

    if not zdp_profile_found:
        print('ERROR: A ZDP Profile is not present in the input configuration file.')
        return False

    # Nodes and endpoints

    all_nodes = []
    coordinator = config.find('Coordinator')
    if coordinator is not None:
        all_nodes.append(coordinator)

    all_nodes.extend(config.findall('ChildNodes'))

    for node in all_nodes:
        if not node.get('Name'):
            print('ERROR: A node in the input configuration file does not have a Name attribute.\n')
            return False

        if name_check.fullmatch(node.attrib['Name']) is None:
            print(
                "ERROR: The node named '%s' in the input configuration file is not a valid C identifier.\n"
                % node.attrib['Name']
            )
            return False

        if check_for_duplicate_names(all_nodes, node):
            print(
                "ERROR: There are one or more Nodes with the name '%s'. Nodes must have unique names.\n"
                % node.attrib['Name']
            )
            return False

    found_node = find_node(node_name)
    if found_node is None:
        print("ERROR: The input configuration file does not contain a node named '%s'.\n" % node_name)
        return False

    endpoints = found_node.findall('Endpoints')
    if not endpoints:
        print("ERROR: The input configuration for node '%s' does not contain any 'Endpoint' elements.\n" % node_name)
        return False

    zdp_ep_found = False

    for endpoint in endpoints:
        endpoint_name = endpoint.get('Name')

        if not endpoint.get('Id'):
            if endpoint_name:
                print(
                    "ERROR: Endpoint '%s' for node '%s' does not have an Id attribute.\n"
                    % (endpoint_name, node_name)
                )
            else:
                print("ERROR: An Endpoint for node '%s' does not have either an Id or Name attribute.\n" % node_name)
            return False

        if not endpoint_name:
            print(
                "ERROR: Endpoint id '%s' for node '%s' does not have a Name attribute.\n"
                % (endpoint.attrib['Id'], node_name)
            )
            return False

        if name_check.fullmatch(endpoint_name) is None:
            print(
                "ERROR: Endpoint name '%s' for node '%s' is not a valid C identifier.\n"
                % (endpoint_name, node_name)
            )
            return False

        endpoint_id = int(endpoint.attrib['Id'], 0)
        if endpoint_id == 0:
            zdp_ep_found = True
        elif endpoint_id > 240:
            if endpoint_id != 242:
                print(
                    "ERROR: Endpoint '%s' for node '%s' has an invalid id '%d'. "
                    "Endpoint Ids must be in the range 1-240 or 242.\n"
                    % (endpoint_name, node_name, endpoint_id)
                )
                return False

        if check_for_duplicate_names(endpoints, endpoint):
            print(
                "ERROR: There are one or more Endpoints with the name '%s' for node '%s'. "
                "Endpoints must have unique names.\n" % (endpoint_name, found_node.attrib['Name'])
            )
            return False

        if check_for_duplicate_ids(endpoints, endpoint):
            print(
                "ERROR: There are one or more Endpoints with the Id '%s' for node '%s'. "
                "Endpoints must have unique Ids.\n" % (endpoint.attrib['Id'], found_node.attrib['Name'])
            )
            return False

        if not endpoint.get('Profile'):
            print("ERROR: Endpoint '%s' on node '%s' does not specify a Profile.\n" % (endpoint_name, node_name))
            return False

        io_cluster_found = False

        input_clusters = endpoint.findall('InputClusters')
        if input_clusters:
            io_cluster_found = True

            for cluster in input_clusters:
                if not cluster.get('Cluster'):
                    print(
                        "ERROR: An Input Cluster for endpoint '%s' on node '%s' "
                        "does not specify a Cluster.\n" % (endpoint_name, node_name)
                    )
                    return False

                if 'Discoverable' not in cluster.attrib:
                    print(
                        "WARNING: Input cluster '%s' for endpoint '%s' on node '%s' does not "
                        "have a 'Discoverable' attribute. Defaulting to discoverable.\n"
                        % (cluster.attrib['Cluster'], endpoint_name, node_name)
                    )

                if 'RxAPDU' not in cluster.attrib:
                    print(
                        "WARNING: Input cluster '%s' for endpoint '%s' on node '%s' does not "
                        "specify an RxAPDU. No data can be received for this cluster.\n"
                        % (cluster.attrib['Cluster'], endpoint_name, node_name)
                    )
                elif find_apdu(found_node, cluster.attrib['RxAPDU']) is None:
                    print(
                        "ERROR: Unable to find APDU '%s' for input cluster '%s'"
                        " for endpoint '%s' on node '%s'\n"
                        % (cluster.attrib['RxAPDU'], cluster.attrib['Cluster'], endpoint_name, node_name)
                    )
                    return False

        output_clusters = endpoint.findall('OutputClusters')
        if output_clusters:
            io_cluster_found = True

            for cluster in output_clusters:
                if not cluster.get('Cluster'):
                    print(
                        "WARNING: An Output Cluster for endpoint '%s' on node '%s' does not specify a Cluster.\n"
                        % (endpoint_name, node_name)
                    )
                    return False

                if 'Discoverable' not in cluster.attrib:
                    print(
                        "WARNING: Output cluster '%s' for endpoint '%s' on node '%s' does not "
                        "have a 'Discoverable' attribute. Defaulting to discoverable.\n"
                        % (cluster.attrib['Cluster'], endpoint_name, node_name)
                    )

                if 'TxAPDUs' not in cluster.attrib:
                    print(
                        "WARNING: Output cluster '%s' for endpoint '%s' on node '%s' does not specify any TxAPDUs.\n"
                        % (cluster.attrib['Cluster'], endpoint_name, node_name)
                    )

        if not io_cluster_found:
            print(
                "WARNING: Endpoint '%s' id %d for node '%s' does not contain any input or output clusters.\n"
                % (endpoint_name, endpoint_id, node_name)
            )

    if not zdp_ep_found:
        print("ERROR: The input configuration for node '%s' does not contain a ZDP endpoint.\n" % node_name)
        return False

    # Binding table

    binding_table = found_node.find('BindingTable')
    if binding_table is not None:
        if not binding_table.get('Size'):
            print("ERROR: The Binding Table for node '%s' must specify a table size.\n" % node_name)
            return False

        if int(binding_table.attrib['Size'], 0) < 1:
            print("ERROR: The Binding Table for node '%s' must contain at least 1 entry.\n" % node_name)
            return False

    # User descriptor

    user_descriptor = found_node.find('UserDescriptor')
    if user_descriptor is not None:
        if not user_descriptor.get('UserDescription'):
            print("ERROR: The User Descriptor for node '%s' must specify a descriptor.\n" % node_name)
            return False

        if len(user_descriptor.attrib['UserDescription']) > 16:
            print(
                "WARNING: The User Descriptor '%s' for node '%s' is longer than 16 characters and will be truncated.\n"
                % (user_descriptor.attrib['UserDescription'], node_name)
            )
        elif len(user_descriptor.attrib['UserDescription']) <= 0:
            print("ERROR: The User Descriptor for node '%s' does not specify a User Description.\n" % node_name)
            return False

    # PDU configuration

    pdu_config = found_node.find('PDUConfiguration')
    if pdu_config is None:
        print("ERROR: The node '%s' must have a PDU Manager element.\n" % node_name)
        return False

    if not pdu_config.get('NumNPDUs'):
        print("ERROR: The PDU Manager for node '%s' must have a NumNPDUs attribute.\n" % node_name)
        return False

    if int(pdu_config.attrib['NumNPDUs'], 0) < 8:
        print("ERROR: The PDU Manager for node '%s' must have at least 8 NPDUs configured" % node_name)
        return False

    if pdu_config.find('APDUs') is None:
        print("ERROR: The PDU Manager for node '%s' does not have any APDUs.\n" % node_name)
        return False

    # Group table

    group_table = found_node.find('GroupTable')
    if group_table is not None:
        if not group_table.get('Size'):
            print("ERROR: The Group Table for node '%s' must specify a table size.\n" % node_name)
            return False

        if int(group_table.attrib['Size'], 0) < 1:
            print("ERROR: The Group Table for node '%s' must contain at least 1 entry.\n" % node_name)
            return False

    # Node descriptors

    if found_node.find('NodeDescriptor') is None:
        print("ERROR: A Node Descriptor must be specified for node '%s'.\n" % node_name)
        return False

    node_power_descriptor = found_node.find('NodePowerDescriptor')
    if node_power_descriptor is None:
        print("ERROR: A Node Power Descriptor must be specified for node '%s'.\n" % node_name)
        return False

    default_power_source = node_power_descriptor.get('DefaultPowerSource', '').lower()
    power_source = {
        'disposable battery': ('DisposableBattery', 'Disposable Battery'),
        'rechargeable battery': ('RechargeableBattery', 'Rechargeable Battery'),
        'constant power': ('ConstantPower', 'Constant Power'),
    }.get(default_power_source)

    if power_source:
        attribute, name = power_source
        value = node_power_descriptor.get(attribute)

        if not value or value.lower() == 'false':
            print(
                "ERROR: Default power source '%s' is not available "
                "in the Node Power Descriptor for node '%s'.\n"
                % (name, node_name)
            )
            return False

    # ZDO servers

    zdo_servers = found_node.find('ZDOServers')
    if zdo_servers is None:
        print("ERROR: The node '%s' does not contain a ZDO Configuration.\n" % node_name)
        return False

    if zdo_servers.find('ZdoClient') is None:
        print("ERROR: The ZDO Configuration for node '%s' must contain a ZDO Client.\n" % node_name)
        return False

    if zdo_servers.find('DeviceAnnceServer') is None:
        print("ERROR: The ZDO Configuration for node '%s' must contain a Device Annce Server.\n" % node_name)
        return False

    if zdo_servers.find('ActiveEpServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a ActiveEpServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('NwkAddrServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a NwkAddrServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('IeeeAddrServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a IeeeAddrServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('SystemServerDiscoveryServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a SystemServerDiscoveryServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('NodeDescServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a NodeDescServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('PowerDescServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a PowerDescServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('MatchDescServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a MatchDescServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('SimpleDescServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a SimpleDescServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('MgmtLqiServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a MgmtLqiServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('MgmtLeaveServer') is None and 'Coordinator' != found_node.tag:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a MgmtLeaveServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if zdo_servers.find('MgmtNWKUpdateServer') is None:
        print(
            "WARNING: The ZDO Configuration for node '%s' should contain a MgmtNWKUpdateServer "
            "to meet the requirements for a ZigBee Compliant Platform (ZCP). \n" % node_name
        )

    if (
        'Coordinator' == found_node.tag
        or 'ChildNodes' == found_node.tag and 'zpscfg:Router' == found_node.get('{%s}type' % XSI_NAMESPACE)
    ):
        if zdo_servers.find('PermitJoiningServer') is None:
            print(
                "WARNING: The ZDO Configuration for node '%s' should contain a "
                "PermitJoiningServer to meet the requirements for a "
                "ZigBee Compliant Platform (ZCP). \n" % node_name
            )

        if zdo_servers.find('MgmtRtgServer') is None:
            print(
                "WARNING: The ZDO Configuration for node '%s' should contain a "
                "MgmtRtgServer to meet the requirements for a "
                "ZigBee Compliant Platform (ZCP). \n" % node_name
            )

    if 'Coordinator' == found_node.tag:
        if zdo_servers.find('EndDeviceBindServer') is None:
            print(
                "WARNING: The ZDO Configuration for node '%s' should contain a "
                "EndDeviceBindServer to meet the requirements for a "
                "ZigBee Compliant Platform (ZCP). \n" % node_name
            )

        if zdo_servers.find('MgmtMibIeeeServer') is None:
            print(
                "WARNING: The ZDO Configuration for node '%s' should contain a "
                "MgmtMibIeeeServer to meet the requirements for a "
                "ZigBee Compliant Platform (ZCP). \n" % node_name
            )

    # Node and APS configuration

    if not found_node.get('MacTableSize'):
        print("ERROR: MacTableSize is not set for node '%s'.\n" % node_name)
        return False

    mac_table_size = int(found_node.attrib['MacTableSize'], 0)

    if (
        'MaxNumSimultaneousApsdeAckReq' in found_node.attrib
        and int(found_node.attrib['MaxNumSimultaneousApsdeAckReq'], 0) == 0
    ):
        print(
            'WARNING: No Apsde requests with acknowledgements may be made because '
            'MaxNumSimultaneousApsdeAckReq is set to 0.\n'
        )

    if not found_node.get('MaxNumSimultaneousApsdeReq'):
        print(
            'ERROR: No Apsde requests with acknowledgements may be made because '
            'MaxNumSimultaneousApsdeAckReq is set to 0.\n'
        )
        return False

    if int(found_node.attrib['MaxNumSimultaneousApsdeReq'], 0) < 1:
        print("ERROR: MaxNumSimultaneousApsdeReq must be at least 1 or node '%s'.\n" % node_name)
        return False

    if not found_node.get('DefaultCallbackName'):
        print("ERROR: DefaultCallbackName is not set for node '%s'\n" % node_name)
        return False

    if name_check.fullmatch(found_node.attrib['DefaultCallbackName']) is None:
        print("ERROR: DefaultCallbackName is not a valid C identifier for node '%s'\n" % node_name)
        return False

    if 'Coordinator' == found_node.tag and found_node.get('apsDesignatedCoordinator', '').lower() != 'true':
        print("ERROR: apsDesignatedCoordinator must be set to true for Coordinator node '%s'\n" % node_name)
        return False

    if not found_node.get('apsMaxWindowSize'):
        print("ERROR: apsMaxWindowSize is not set for node '%s'\n" % node_name)
        return False

    aps_max_window_size = int(found_node.attrib['apsMaxWindowSize'], 0)
    if aps_max_window_size < 1 or aps_max_window_size > 8:
        print("ERROR: apsMaxWindowSize must be in the range 1-8 for node '%s'\n" % node_name)
        return False

    if not found_node.get('apsInterframeDelay'):
        print("ERROR: apsInterframeDelay is not set for node '%s'\n" % node_name)
        return False

    aps_interframe_delay = int(found_node.attrib['apsInterframeDelay'], 0)
    if aps_interframe_delay < 10 or aps_interframe_delay > 255:
        print("ERROR: apsInterframeDelay must be in the range 10-255 for node '%s'\n" % node_name)
        return False

    if not found_node.get('APSDuplicateTableSize'):
        print("ERROR: APSDuplicateTableSize is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['APSDuplicateTableSize'], 0) < 1:
        print("ERROR: APSDuplicateTableSize must be at least 1 for node '%s'\n" % node_name)
        return False

    if not found_node.get('apsSecurityTimeoutPeriod'):
        print("ERROR: apsSecurityTimeoutPeriod is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['apsSecurityTimeoutPeriod'], 0) < 1000:
        print("ERROR: apsSecurityTimeoutPeriod must be at least 1000 ms for node '%s'\n" % node_name)
        return False

    if not found_node.get('apsUseExtPANId'):
        print("ERROR: apsUseExtPANId is not set for node '%s'\n" % node_name)
        return False

    aps_use_ext_pan_id = int(found_node.attrib['apsUseExtPANId'], 0)
    if (
        config.get('DefaultExtendedPANId') is not None
        and aps_use_ext_pan_id != 0
        and aps_use_ext_pan_id != int(config.get('DefaultExtendedPANId'), 0)
    ):
        print(
            "WARNING: apsUseExtPANId for node '%s' does not match the DefaultExtenededPANId "
            "setting of the ZigBee Wireless Network.\n" % node_name
        )

    if not found_node.get('apsNonMemberRadius'):
        print("ERROR: apsNonMemberRadius is not set for node '%s'\n" % node_name)
        return False

    aps_non_member_radius = int(found_node.attrib['apsNonMemberRadius'], 0)
    if aps_non_member_radius < 0 or aps_non_member_radius > 7:
        print("ERROR: apsNonMemberRadius for node '%s' must be in the range 0-7.\n" % node_name)
        return False

    if (
        'Coordinator' == found_node.tag
        or 'ChildNodes' == found_node.tag and 'zpscfg:Router' == found_node.get('{%s}type' % XSI_NAMESPACE)
    ):
        if not found_node.get('PermitJoiningTime'):
            print("ERROR: PermitJoiningTime is not set for node '%s'\n" % node_name)
            return False

        permit_joining_time = int(found_node.attrib['PermitJoiningTime'], 0)
        if permit_joining_time < 0 or permit_joining_time > 255:
            print("ERROR: PermitJoiningTime for node '%s' must be in the range 0-255.\n" % node_name)
            return False

    if not found_node.get('SecurityEnabled'):
        print("ERROR: SecurityEnabled is not set for node '%s'\n" % node_name)
        return False

    if (
        config.get('DefaultSecurityEnabled') is not None
        and config.get('DefaultSecurityEnabled').lower()
        != found_node.attrib['SecurityEnabled'].lower()
    ):
        print(
            "WARNING: SecurityEnabled for node '%s' does not match the DefaultSecurityEnabled "
            "setting for the ZigBeeWirelessNetwork.\n" % node_name
        )

    # Network tables

    if not found_node.get('AddressMapTableSize'):
        print("ERROR: AddressMapTableSize is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['AddressMapTableSize'], 0) > mac_table_size:
        print(
            "ERROR: The AddressMapTableSize for node '%s' is greater then the MacTable "
            "setting for the ZigBeeWirelessNetwork.\n" % node_name
        )
        return False

    if not found_node.get('ActiveNeighbourTableSize'):
        print("ERROR: ActiveNeighbourTableSize is not set for node '%s'\n" % node_name)
        return False

    active_neighbour_table_size = int(found_node.attrib['ActiveNeighbourTableSize'], 0)

    if active_neighbour_table_size > mac_table_size:
        print(
            "ERROR: The ActiveNeighbourTableSize for node '%s' is greater then the MacTable "
            "setting for the ZigBeeWirelessNetwork.\n" % node_name
        )
        return False

    if active_neighbour_table_size < 1:
        print("ERROR: The ActiveNeighbourTableSize for node '%s' must be at least 1.\n" % node_name)
        return False

    if not found_node.get('DiscoveryNeighbourTableSize'):
        print("ERROR: DiscoveryNeighbourTableSize is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['DiscoveryNeighbourTableSize'], 0) < 1:
        print("ERROR: The DiscoveryNeighbourTableSize for node '%s' must be at least 1.\n" % node_name)
        return False

    if not found_node.get('RouteDiscoveryTableSize'):
        print("ERROR: RouteDiscoveryTableSize is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['RouteDiscoveryTableSize'], 0) < 1:
        print("ERROR: The RouteDiscoveryTableSize for node '%s' must be at least 1.\n" % node_name)
        return False

    if not found_node.get('RoutingTableSize'):
        print("ERROR: RoutingTableSize is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['RoutingTableSize'], 0) < 1:
        print("ERROR: The RoutingTableSize for node '%s' must be at least 1.\n" % node_name)
        return False

    if not found_node.get('BroadcastTransactionTableSize'):
        print("ERROR: BroadcastTransactionTableSize is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['BroadcastTransactionTableSize'], 0) < 1:
        print("ERROR: The BroadcastTransactionTableSize for node '%s' must be at least 1.\n" % node_name)
        return False

    if not found_node.get('RouteRecordTableSize'):
        print("ERROR: RouteRecordTableSize is not set for node '%s'\n" % node_name)
        return False

    if int(found_node.attrib['RouteRecordTableSize'], 0) < 1:
        print("ERROR: The RouteRecordTableSize for node '%s' must be at least 1.\n" % node_name)
        return False

    # Security material

    if found_node.attrib['SecurityEnabled'].lower() == 'true':
        if not found_node.get('SecurityMaterialSets'):
            print(
                "ERROR: SecurityMaterialSets is not set for node '%s' "
                "with SecurityEnabled set to true.\n" % node_name
            )
            return False

        if int(found_node.attrib['SecurityMaterialSets'], 0) < 1:
            print(
                "ERROR: SecurityMaterialSets must be at least 1 for node '%s' "
                "with SecurityEnabled set to true.\n" % node_name
            )
            return False

    # Polling and scanning

    if found_node.get('Sleeping') and found_node.attrib['Sleeping'].lower() == 'true':
        if not found_node.get('APSPollPeriod'):
            print("ERROR: APSPollPeriod is not set for node '%s'\n" % node_name)
            return False

        if int(found_node.attrib['APSPollPeriod'], 0) < 25:
            print("ERROR: The APSPollPeriod for node '%s' must be at least 25ms.\n" % node_name)
            return False

        if not found_node.get('NumPollFailuresBeforeRejoin'):
            print("ERROR: NumPollFailuresBeforeRejoin is not set for node '%s'\n" % node_name)
            return False

        if int(found_node.attrib['NumPollFailuresBeforeRejoin'], 0) == 0:
            print(
                "WARNING: The NumPollFailuresBeforeRejoin for node '%s' is set to 0. "
                "The node will not rejoin if its parent is lost.\n" % node_name
            )

    if found_node.get('ScanDuration'):
        scan_duration = int(found_node.attrib['ScanDuration'], 0)
        if scan_duration < 0 or scan_duration > 14:
            print("ERROR: The ScanDuration for node '%s' must be in the range 0-14\n" % node_name)
            return False

    return True


def output_c(output_dir: str, config_node: ElementTree.Element, endian: str) -> None:
    fsp = os.path.join(output_dir, 'zps_gen.c')
    if os.path.exists(fsp):
        os.chmod(fsp, os.stat(fsp).st_mode | stat.S_IWUSR)

    with open(fsp, 'w') as c_file:
        c_file.write(dedent("""\
            /****************************************************************************
             *
             *                 THIS IS A GENERATED FILE. DO NOT EDIT!
             *
             * MODULE:         ZPSConfig
             *
             * COMPONENT:      zps_gen.c
             *
             * DATE:           %s
             *
             * AUTHOR:         Jennic Zigbee Protocol Stack Configuration Tool
             *
             * DESCRIPTION:    ZPS definitions
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
            #include <pdum_gen.h>
            #include "ZQueue.h"
            #include <zps_gen.h>

            #include "zps_apl.h"
            #include "zps_apl_aib.h"
            #include "zps_apl_af.h"

            /****************************************************************************/
            /***        Macro Definitions                                             ***/
            /****************************************************************************/

            #define COMPILE_TIME_ASSERT(pred) switch (0) { case 0: case pred:; }

            #define ZPS_APL_ZDO_VSOUI_LENGTH 3

            /****************************************************************************/
            /***        Type Definitions                                              ***/
            /****************************************************************************/

            /*** ZDP Context **************************************************/

            typedef struct {
                uint8 u8ZdpSeqNum;
            } zps_tsZdpContext;

            /*** ZDO Context **************************************************/

            typedef bool (*zps_tprAplZdoServer)(void *pvApl, void *pvServerContext, ZPS_tsAfEvent *psZdoServerEvent);

            typedef struct
            {
                zps_tprAplZdoServer prServer;
                void *pvServerContext;
            } zps_tsAplZdoServer;

            typedef struct
            {
                uint8 au8Key[ZPS_NWK_KEY_LENGTH];
                uint8 u8KeySeqNum;
                uint8 u8KeyType;
            } zps_tsAplZdoInitSecKey;

            typedef struct
            {
                uint64 u64InitiatorAddr;
                uint64 u64ResponderAddr;
                ZPS_tsTsvTimer sTimer;
                uint8 au8Key[ZPS_NWK_KEY_LENGTH];
            } zps_tsRequestKeyRequests;

            typedef struct
            {
                uint8 au8VsOUIBytes[ZPS_APL_ZDO_VSOUI_LENGTH] __attribute__ ((aligned (16)));
                uint8 eNetworkState; /* ZPS_teZdoNetworkState */
                uint8 eZdoDeviceType; /* ZPS_teZdoDeviceType */
                uint8 eNwkKeyState; /* ZPS_teZdoNwkKeyState */
                uint8 u8PermitJoinTime;
                uint8 u8StackProfile;
                uint8 u8ZigbeeVersion;
                uint8 u8ScanDuration;
                bool_t bLookupTCAddr;
                const zps_tsAplZdoServer *psZdoServers;
                void (*prvZdoServersInit)(void);
                ZPS_tsTsvTimer sAuthenticationTimer;
                ZPS_tsTsvTimer sAuthenticationPollTimer;
                uint8 u8NumPollFailures;
                uint8 u8MaxNumPollFailures;
                bool_t bSecurityDisabled;
                zps_tsAplZdoInitSecKey *psInitSecKey;
                uint8 u8DevicePermissions;
                bool_t (*prvZdoReqFilter)(uint16);
                bool (*pfzps_bAplZdoBindRequestServer)(void *, void *, ZPS_tsAfEvent *);
                zps_tsRequestKeyRequests *psRequestKeyReqs;
                uint32 u32ReqKeyTimeout;
                uint8 u8MaxNumSimulRequestKeyReqs;

            } zps_tsZdoContext;

            /**** Context for the ZDO servers data confirms and acks***********/

            typedef struct
            {
                uint8 eState;
                uint8 u8SeqNum;
                uint8 u8ConfAck;
            } zps_tsZdoServerConfAckContext;

            /*** Trust Center Context *****************************************/

            typedef struct
            {
                uint16 u16AddrLkup;
                ZPS_teDevicePermissions eDevPermissions;
            } zps_tsAplTCDeviceTable;

            typedef struct
            {
                zps_tsAplTCDeviceTable *asTCDeviceTable;
                uint16 u16SizeOfTCDeviceTable;
            } zps_tsAplTCib;

            typedef struct
            {
                void (*prvTrustCenterInit)(void *);
                void (*prvTrustCenterUpdateDevice)(void *, uint64, uint64, uint8, uint16);
                void (*prvTrustCenterRequestKey)(void *, uint64, uint8, uint64);
                zps_tsAplTCib sTCib;
                bool_t bTcOverride;
                bool_t bChangeOverride;
            } zps_tsTrustCenterContext;

            /*** AF Context ***************************************************/

            typedef struct zps_tsAplAfDynamicContext zps_tsAplAfDynamicContext;

            typedef struct _zps_tsAplAfSimpleDescCont
            {
                ZPS_tsAplAfSimpleDescriptor sSimpleDesc;
                const PDUM_thAPdu *phAPduInClusters;
                bool_t bEnabled;
            } zps_tsAplAfSimpleDescCont;

            typedef struct
            {
                zps_tsAplAfDynamicContext *psDynamicContext;
                ZPS_tsAplAfNodeDescriptor *psNodeDescriptor;
                ZPS_tsAplAfNodePowerDescriptor *psNodePowerDescriptor;
                uint32 u32NumSimpleDescriptors;
                zps_tsAplAfSimpleDescCont *psSimpleDescConts;
                ZPS_tsAplAfUserDescriptor *psUserDescriptor;
                void *hOverrunMsg;
                uint8 zcp_u8FragApsAckValue;
                uint8 zcp_u8FragBlockControl;
            } zps_tsAfContext;

            /*** APS Context **************************************************/

            typedef struct
            {
                uint8 u8Type;
                uint8 u8ParamLength;
            } ZPS_tsAplApsmeDcfmIndHdr;

            typedef struct
            {
                uint8 u8Type;
                uint8 u8ParamLength;
            } ZPS_tsAplApsdeDcfmIndHdr;

            typedef struct
            {
                ZPS_tuAddress uDstAddr;
                PDUM_thAPduInstance hAPduInst;
                uint8 *pu8SeqCounter;
                uint16 u16ProfileId;
                uint16 u16ClusterId;
                uint8 u8DstEndpoint;
                uint8 u8SrcEndpoint;
                uint8 u8Radius;
                uint8 eDstAddrMode;
                uint8 eTxOptions;
            } ZPS_tsAplApsdeReqData;

            typedef union
            {
                ZPS_tsAplApsdeReqData  sReqData;
            } ZPS_tuAplApsdeReqRspParam;

            typedef struct
            {
                uint8 u8Type;
                uint8 u8ParamLength;
                uint16 u16Pad;
                ZPS_tuAplApsdeReqRspParam uParam;
            } ZPS_tsAplApsdeReqRsp;

            typedef struct
            {
                struct {
                    uint32 u6Reserved       : 6;
                    uint32 u2Fragmentation  : 2;
                    uint32 u24Padding       : 24;
                } sEFC;
                uint8 u8BlockNum;
                uint8 u8Ack;
            } zps_tsExtendedFrameControlField;

            typedef union
            {
                struct {
                    uint8   u8DstEndpoint;
                    uint16  u16ClusterId;
                    uint16  u16ProfileId;
                    uint8   u8SrcEndpoint;
                    uint8   u8ApsCounter;
                } sUnicast;

                struct {
                        uint16  u16GroupAddr;
                        uint16  u16ClusterId;
                        uint16  u16ProfileId;
                        uint8   u8SrcEndpoint;
                        uint8   u8ApsCounter;
                    } sGroup;
            } zps_tuApsAddressingField;

            typedef struct
            {
                uint16    *psDuplicateTableSrcAddr;
                uint32    *psDuplicateTableHash;
                uint8     *psDuplicateTableApsCnt;
                uint8     u8TableIndex;
            } zps_tsApsDuplicateTable;

            typedef struct zps_tsMsgRecord_tag
            {
                struct zps_tsMsgRecord_tag *psNext;
                ZPS_tsAplApsdeReqRsp sApsdeReqRsp;
                ZPS_tsTsvTimer sAckTimer; /* ack timer */
                uint8       u8ReTryCnt;
                uint8       u8ApsCount;
            } zps_tsMsgRecord;

            typedef struct zps_tsDcfmRecord_tag
            {
                union {
                    uint16 u16DstAddr;
                    uint64 u64DstAddr;
                };
                uint8   u8Handle;
                uint8   u8SrcEp;
                uint8   u8DstEp;
                uint8   u8DstAddrMode;
                uint8   u8SeqNum;
            } zps_tsDcfmRecord;

            typedef struct zps_tsDcfmRecordPool_tag
            {
                zps_tsDcfmRecord *psDcfmRecords;
                uint8 u8NextHandle;
                uint8 u8NumRecords;
            } zps_tsDcfmRecordPool;

            typedef struct zps_tsFragmentTransmit_tag
            {
                enum {
                    ZPS_FRAG_TX_STATE_IDLE,
                    ZPS_FRAG_TX_STATE_SENDING,
                    ZPS_FRAG_TX_STATE_RESENDING,
                    ZPS_FRAG_TX_STATE_WAIT_FOR_ACK
                } eState;
                PDUM_thAPduInstance hAPduInst;
                uint16  u16DstAddress;
                uint16  u16ProfileId;
                uint16  u16ClusterId;
                uint8   u8DstEndpoint;
                uint8   u8SrcEndpoint;
                uint8   u8Radius;
                uint8   u8SeqNum;

                ZPS_tsTsvTimer sAckTimer;
                uint8   u8CurrentBlock;
                uint8   u8SentBlocksInWindow;
                uint8   u8MinBlockNumber;
                uint8   u8MaxBlockNumber;
                uint8   u8TotalBlocksToSend;
                uint8   u8RetryCount;
                uint8   u8AckedBlocksInWindow;
                uint8   u8WindowSize;
                uint8   u8BlockSize;
                bool_t  bSecure;
            } zps_tsFragmentTransmit;

            typedef struct zps_tsfragTxPool_tag
            {
                zps_tsFragmentTransmit *psFragTxRecords;
                uint8   u8NumRecords;
            } zps_tsFragTxPool;

            typedef struct zps_tsFragmentReceive_tag
            {
                enum {
                    ZPS_FRAG_RX_STATE_IDLE,
                    ZPS_FRAG_RX_STATE_RECEIVING,
                    ZPS_FRAG_RX_STATE_PERSISTING
                } eState;
                PDUM_thAPduInstance hAPduInst;
                uint16  u16SrcAddress;
                uint16  u16ProfileId;
                uint16  u16ClusterId;
                uint8   u8DstEndpoint;
                uint8   u8SrcEndpoint;
                uint8   u8SeqNum;

                ZPS_tsTsvTimer  sWindowTimer;
                PDUM_thNPdu     hNPduPrevious;
                uint16  u16ReceivedBytes;
                uint8   u8TotalBlocksToReceive;
                uint8   u8ReceivedBlocksInWindow;
                uint8   u8MinBlockNumber;
                uint8   u8MaxBlockNumber;
                uint8   u8HighestUnAckedBlock;
                uint8   u8WindowSize;
                uint8   u8BlockSize;
                uint8   u8PreviousBlock;
            } zps_tsFragmentReceive;

            typedef struct zps_tsfragRxPool_tag
            {
                zps_tsFragmentReceive *psFragRxRecords;
                uint8   u8NumRecords;
                uint8   u8PersistanceTime;
            } zps_tsFragRxPool;

            typedef struct zps_tsApsPollTimer
            {
                ZPS_tsTsvTimer sPollTimer;
                uint16 u16PollInterval;
                uint8 u8PollActive;
            } zps_tsApsPollTimer;

            typedef struct zps_tsApsmeCmdContainer
            {
                struct zps_tsApsmeCmdContainer *psNext; /* must be first element of struct */
                ZPS_tsNwkNldeReqRsp sNldeReqRsp;
                ZPS_tsTsvTimer sTimer;
                PDUM_thNPdu hNPduCopy;
                uint8 u8Retries;
            } zps_tsApsmeCmdContainer;

            typedef struct
            {
                zps_tsApsmeCmdContainer *psFreeList;
                zps_tsApsmeCmdContainer *psSubmittedList;
            } zps_tsApsmeCmdMgr;

            typedef struct
            {
                void *pvParam;
                ZPS_tsAplApsdeDcfmIndHdr *psApsdeDcfmIndHdr;
            } zps_tsLoopbackDataContext;

            typedef struct
            {
                /* APSDE */
                void *pvParam;
                ZPS_tsAplApsdeDcfmIndHdr *(*prpsGetApsdeBuf)(void *);
                void (*prvPostApsdeDcfmInd)(void *, ZPS_tsAplApsdeDcfmIndHdr *);
                /* APSME */
                void *pvApsmeParam;
                ZPS_tsAplApsmeDcfmIndHdr *(*prpsGetApsmeBuf)(void *);
                void (*prvPostApsmeDcfmInd)(void *, ZPS_tsAplApsmeDcfmIndHdr *);

                zps_tsApsDuplicateTable *psApsDuplicateTable;
                zps_tsMsgRecord  *psSyncMsgPool;
                uint8 u8ApsDuplicateTableSize;
                uint8 u8SeqNum;
                uint8 u8SyncMsgPoolSize;
                uint8 u8MaxFragBlockSize;
                zps_tsDcfmRecordPool sDcfmRecordPool;
                zps_tsFragRxPool sFragRxPool;
                zps_tsFragTxPool sFragTxPool;
                ZPS_teStatus (*preStartFragmentTransmission)(void *, ZPS_tsAplApsdeReqRsp *, uint16, uint8);
                void (*prvHandleExtendedDataAck)(
                    void *,
                    ZPS_tsNwkNldeDcfmInd *,
                    zps_tuApsAddressingField *,
                    zps_tsExtendedFrameControlField *
                );
                void (*prvHandleDataFragmentReceive)(void *, ZPS_tsAplApsdeDcfmIndHdr *);
                zps_tsApsmeCmdMgr sApsmeCmdMgr;
                zps_tsApsPollTimer sApsPollTimer;
                zps_tsLoopbackDataContext sLoopbackContext;
                ZPS_tsTsvTimer sLoopbackTimer;
            } zps_tsApsContext;

            /*** APL Context **************************************************/

            typedef struct
            {
                void *pvNwk;
                const void *pvNwkTableSizes;
                const void *pvNwkTables;

                ZPS_tsNwkNib *psNib;
                ZPS_tsAplAib *psAib;

                void *hZpsMutex;
                void *hDefaultStackEventMsg;
                void *hMcpsDcfmIndMsg;
                void *hMlmeDcfmIndMsg;
                void *hTimeEventMsg;
                void *hMcpsDcfmMsg;
                /* sub-layer contexts */
                zps_tsZdpContext sZdpContext;
                zps_tsZdoContext sZdoContext;
                zps_tsAfContext  sAfContext;
                zps_tsApsContext sApsContext;

                /* trust center context if present */
                zps_tsTrustCenterContext *psTrustCenterContext;

            } zps_tsApl;

            /*** NIB Defaults **************************************************/

            typedef struct
            {
                uint32 u32VsOldRouteExpiryTime;
                uint8  u8MaxRouters;
                uint8  u8MaxChildren;
                uint8  u8MaxDepth;
                uint8  u8PassiveAckTimeout;
                uint8  u8MaxBroadcastRetries;
                uint8  u8MaxSourceRoute;
                uint8  u8NetworkBroadcastDeliveryTime;
                uint8  u8UniqueAddr;
                uint8  u8AddrAlloc;
                uint8  u8UseTreeRouting;
                uint8  u8SymLink;
                uint8  u8UseMulticast;
                uint8  u8LinkStatusPeriod;
                uint8  u8RouterAgeLimit;
                uint8  u8RouteDiscoveryRetriesPermitted;
                uint8  u8VsFormEdThreshold;
                uint8  u8SecurityLevel;
                uint8  u8AllFresh;
                uint8  u8SecureAllFrames;
                uint8  u8VsTxFailThreshold;
                uint8  u8VsMaxOutgoingCost;
                uint8  u8VsLeaveRejoin;
                uint8  u8ZedTimeout;
                uint8  u8ZedTimeoutDefault;
                uint16 u16VerifyLinkCostTransmitRate;

            } zps_tsNwkNibInitialValues;

            /****************************************************************************/
            /***        External Dependencies                                         ***/
            /****************************************************************************/

            PUBLIC ZPS_teStatus zps_eStartFragmentedTransmission(void *, ZPS_tsAplApsdeReqRsp *, uint16, uint8);
            PUBLIC void zps_vHandleExtendedDataAckFrame(
                void *,
                ZPS_tsNwkNldeDcfmInd *,
                zps_tuApsAddressingField *,
                zps_tsExtendedFrameControlField *
            );
            PUBLIC void zps_vHandleApsdeDataFragIndNotSupported(void *pvApl, ZPS_tsAplApsdeDcfmIndHdr *);
            PUBLIC void zps_vHandleApsdeDataFragInd(void *, ZPS_tsAplApsdeDcfmIndHdr *psApsdeDcfmInd);
            bool_t g_pbZpsMutex = FALSE;
            PUBLIC void *zps_vGetZpsMutex(void);
            extern PUBLIC bool_t APP_bMultimaskJoinCallBack(void *);
            """) % datetime.datetime.now().ctime())

        temp_interface = 0

        mac_interface_list = config_node.find('MacInterfaceList')
        if mac_interface_list is not None:
            channel_mask = 0x7FFF800
            channel_mask_config = config_node.find('ChannelMask')
            if channel_mask_config is not None:
                channel_mask = calculate_channel_mask(channel_mask_config) or channel_mask

            mac_interface_count = 0
            channel_mask_list_count = 0
            set_mask_for_index0 = 0
            mac_interfaces = mac_interface_list.findall('MacInterface')

            for interface in mac_interfaces:
                mac_interface_count += 1
                channel_mask_list_count += int(interface.attrib['ChannelListSize'])

                if interface.attrib['RadioType'] == 'RT2400MHz':
                    set_mask_for_index0 = 1

            if channel_mask_list_count > 15:
                print('channelMaskList count in the mac interface should be less than 15')
                return

            for interface in mac_interfaces:
                index_to_check = int(interface.attrib['index'])
                count_index = 0

                for candidate_interface in mac_interfaces:
                    if index_to_check == int(candidate_interface.attrib['index']):
                        count_index += 1

                    if count_index > 1:
                        print('ERROR: MAC interfaces should have unique index.\n')

            if mac_interface_count > 2:
                print('ERROR: Only two interfaces supported. You have set %d\n' % mac_interface_count)

            if mac_interface_count > 0:
                channel_masks = ['0xFFFFFFFF'] * channel_mask_list_count
                if set_mask_for_index0 == 1 and channel_masks:
                    channel_masks[0] = '0x%08xUL' % channel_mask

                c_file.write(dedent("""\
                    PRIVATE bool_t g_bIgnoreBroadcast[%d] = { %s };
                    PRIVATE uint32 g_u32MacTxUcastAvgRetry[%d] = { %s };
                    PRIVATE uint32 g_u32MacTxUcastAccRetry[%d];
                    PRIVATE uint32 g_u32MacTxUcastFail[%d];
                    PRIVATE uint32 g_u32MacTxUcast[%d];
                    PRIVATE uint32 g_u32MacCcaFail[%d];
                    PRIVATE uint32 g_u32ApsRetry[%d];
                    PUBLIC uint32 g_u32ChannelMaskList[%d] = { %s };
                    zps_tsAplAfMMServerContext s_sMultiMaskServer = {
                        ZPS_E_MULTIMASK_STATE_IDLE,
                        %d,
                        0,
                        APP_bMultimaskJoinCallBack
                    };
                    /* ... ZPS_MULTIMASK_SUPPORT */
                    /* The MAC Interface Table (default values) */
                    PRIVATE MAC_tsMacInterface g_sMacInterface[%d] = {
                    """) % (
                        mac_interface_count,
                        ', '.join(['TRUE'] * mac_interface_count),
                        mac_interface_count,
                        ', '.join(['1'] * mac_interface_count),
                        mac_interface_count,
                        mac_interface_count,
                        mac_interface_count,
                        mac_interface_count,
                        mac_interface_count,
                        channel_mask_list_count,
                        ', '.join(channel_masks),
                        channel_mask_list_count,
                        mac_interface_count,
                    ))

                for interface in mac_interfaces:
                    interface_radio_type = interface.attrib['RadioType']
                    interface_index = int(interface.attrib['index'])

                    radio_type = 'E_MAC_FREQ_NOT_KNOWN'
                    mac_bitmask = 1 if interface.attrib['Enabled'] == 'true' else 0
                    mac_bitmask |= int(interface.attrib['ChannelListSize']) << 2

                    if interface_radio_type == 'RT868MHz':
                        radio_type = 'E_MAC_FREQ_868'
                        temp_interface |= 1
                    elif interface_radio_type == 'RT2400MHz':
                        radio_type = 'E_MAC_FREQ_2400'
                        temp_interface |= 2

                    if interface_index == 0:
                        if interface_radio_type != 'RT2400MHz' and mac_interface_count > 1:
                            print('ERROR: interface index 0 should always be 2.4G in a multimac\n')

                        if interface_radio_type == 'RT2400MHz':
                            mac_type = 'E_MAC_TYPE_SOC'
                        else:
                            mac_type = 'E_MAC_TYPE_UART1'
                    elif interface_index > 0:
                        mac_type = 'E_MAC_TYPE_UART' + str(interface.attrib['index'])

                    if interface.get('RouterAllowed', '').lower() == 'true':
                        mac_bitmask |= 1 << 1

                    c_file.write('    { %d, 0x%x, %s, %s },\n' % (0, mac_bitmask, radio_type, mac_type))

                c_file.write(dedent("""\
                    };
                    PRIVATE MAC_tsMacInterfaceTable g_asMacInterfaceTable = {
                        &s_sMultiMaskServer, /* ZPS_MULTIMASK_SUPPORT ... */
                        g_sMacInterface,
                        &g_u32ChannelMaskList[0],
                        g_u32MacTxUcastAvgRetry,
                        g_u32MacTxUcastAccRetry,
                        g_u32MacTxUcastFail,
                        g_u32MacTxUcast,
                        g_u32MacCcaFail,
                        g_u32ApsRetry,
                        g_bIgnoreBroadcast,
                        %d
                    };
                    """) % mac_interface_count)

        endpoints = config_node.findall('Endpoints')
        endpoint_ids = []
        for endpoint in endpoints:
            endpoint_id = int(endpoint.attrib['Id'], 0)
            if endpoint_id > 0:
                endpoint_ids.append(endpoint_id)
        ep_total = len(endpoint_ids)

        c_file.write(dedent("""\
            PUBLIC uint8 u8MaxZpsConfigEp = %d;
            PUBLIC uint8 au8EpMapPresent[%d] = {%s};
            PUBLIC uint8 u8ZpsConfigStackProfileId = %d;
            PUBLIC const uint32 g_u32ApsFcSaveCountBitShift = %d;
            PUBLIC const uint32 g_u32NwkFcSaveCountBitShift = %d;
            """) % (
                ep_total,
                ep_total,
                ', '.join(map(str, endpoint_ids)),
                int(config_node.get('StackProfile', '2'), 0),
                int(config_node.get('ApsFcSaveCountBitShift', '10'), 0),
                int(config_node.get('NwkFcSaveCountBitShift', '10'), 0),
            ))

        gp_supported = 'NULL'

        if config_node.get('GreenPowerSupport', '').lower() == 'true':
            gp_sec_table_struct = 'NULL'
            gp_tx_queue_struct = 'NULL'
            gp_aging_timer = 'NULL'
            gp_bidir_timer = 'NULL'

            gp_tx_queue = config_node.find('GreenPowerTxQueue')
            if gp_tx_queue is not None:
                tx_queue_size = int(gp_tx_queue.attrib['Size'], 0)

                c_file.write(dedent("""\
                    PRIVATE ZPS_tsAfZgpTxGpQueueEntry aZgpTxGpQueue[%d];
                    ZPS_tsTsvTimer sTxAgingTimer;
                    TSV_Timer_s sTxBiDirTimer;
                    ZPS_tsAfZgpTxGpQueue sZgpTxGpQueue = { aZgpTxGpQueue, %d };
                    """) % (tx_queue_size, tx_queue_size))

                gp_aging_timer = '&sTxAgingTimer'
                gp_bidir_timer = '&sTxBiDirTimer'
                gp_tx_queue_struct = '&sZgpTxGpQueue'

            gp_security_table = config_node.find('GreenPowerSecurityTable')
            if gp_security_table is not None:
                security_table_size = int(gp_security_table.attrib['Size'], 0)

                c_file.write(dedent("""\
                    PRIVATE ZPS_tsAfZgpGpstEntry aZgpGpst[%d];
                    ZPS_tsAfZgpGpst sZgpGpst = { aZgpGpst, %d };
                    """) % (security_table_size, security_table_size))

                gp_sec_table_struct = '&sZgpGpst'

            gp_supported = '&gsGreenPowerContext'

            c_file.write(dedent("""\
                ZPS_tsAfZgpGreenPowerContext gsGreenPowerContext = {
                    %s,
                    %s,
                    %s,
                    %s,
                    100,
                    0
                };
                """) % (gp_sec_table_struct, gp_tx_queue_struct, gp_aging_timer, gp_bidir_timer))

        c_file.write(dedent("""\
            ZPS_tsAfZgpGreenPowerContext *g_psGreenPowerContext = %s;

            /****************************************************************************/
            /***        Local Function Prototypes                                     ***/
            /****************************************************************************/

            PRIVATE void vZdoServersInit(void);
            """) % gp_supported)

        zdo_config = config_node.find('ZDOServers')
        if zdo_config is not None:
            for server in zdo_config:
                if server.tag == 'MgmtNWKEnhanceUpdateServer':
                    continue

                _, output_param_types = ZDO_SERVERS[server.tag]

                c_file.write(dedent("""\
                    PUBLIC bool zps_bAplZdo%s(void *, void *, ZPS_tsAfEvent *);
                    PUBLIC void zps_vAplZdo%sInit(""") % (server.tag, server.tag))
                output_param_types(c_file)
                c_file.write(');\n')

        trust_center = config_node.find('TrustCenter')
        trust_center_present = trust_center is not None
        if trust_center_present:
            c_file.write(dedent("""\

                /* Trust Center */
                PUBLIC void zps_vAplTrustCenterInit(void *);
                PUBLIC void zps_vAplTrustCenterUpdateDevice(void *, uint64, uint64, uint8, uint16);
                PUBLIC void zps_vAplTrustCenterRequestKey(void *, uint64, uint8, uint64);
                """))

        c_file.write(dedent("""\

            /****************************************************************************/
            /***        Local Variables                                               ***/
            /****************************************************************************/

            """))

        binding_table = 'NULL'
        binding_table_config = config_node.find('BindingTable')
        if binding_table_config is not None:
            binding_table_size = int(binding_table_config.attrib['Size'], 0)
            if binding_table_size > 0:
                c_file.write(dedent("""\
                    PRIVATE ZPS_tsAplApsmeBindingTableStoreEntry s_bindingTableStorage[%d];
                    PRIVATE ZPS_tsAplApsmeBindingTable s_bindingTable = { s_bindingTableStorage, %d };
                    """) % (binding_table_size, binding_table_size))
                binding_table = '&s_bindingTable'

        binding_tables = 'NULL'
        if binding_table != 'NULL':
            c_file.write(
                'PRIVATE ZPS_tsAplApsmeBindingTableType s_bindingTables = { NULL, %s };\n'
                % binding_table
            )
            binding_tables = '&s_bindingTables'

        group_table = 'NULL'
        group_table_config = config_node.find('GroupTable')
        if group_table_config is not None:
            group_table = '&s_groupTable'
            group_table_size = int(group_table_config.attrib['Size'], 0)

            c_file.write(dedent("""\
                PRIVATE ZPS_tsAplApsmeGroupTableEntry s_groupTableStorage[%d];
                PRIVATE ZPS_tsAplApsmeAIBGroupTable s_groupTable = { s_groupTableStorage, %d };
                PRIVATE ZPS_tsAPdmGroupTableEntry s_groupTablePdmStorage[%d];
                PUBLIC ZPS_tsPdmGroupTable s_groupPdmTable = { s_groupTablePdmStorage, %d };
                """) % (group_table_size, group_table_size, group_table_size, group_table_size))
        else:
            c_file.write('PUBLIC ZPS_tsPdmGroupTable s_groupPdmTable = { NULL, 0 };\n')

        key_pair_table = 'NULL'
        key_pair_table_size = 0
        key_descriptor_table = config_node.find('KeyDescriptorTable')
        if key_descriptor_table is not None:
            key_pair_table_size = int(key_descriptor_table.attrib['Size'], 0)
            key_storage_size = key_pair_table_size + 3
            key_pair_table = '&s_keyPairTable'

            c_file.write('PRIVATE ZPS_tsAplApsKeyDescriptorEntry s_keyPairTableStorage[%d] = {\n' % key_storage_size)

            num_preconfigured_keys = 0
            for preconfigured_key in key_descriptor_table.findall('PreconfiguredKey'):
                if num_preconfigured_keys == key_pair_table_size:
                    print(
                        'WARNING: There are more PreconfiguredKeys than the KeyDescriptorTable size of %d.\n'
                        % key_pair_table_size
                    )
                    break

                key = int(preconfigured_key.attrib['Key'], 0)
                key_bytes = ', '.join('0x%02x' % ((key >> (8 * index)) & 0xFF) for index in range(16))

                c_file.write(
                    '    { 0x%016lxULL, { %s }, 0, 0, 0 },\n'
                    % (int(preconfigured_key.attrib['IEEEAddress'], 0), key_bytes)
                )
                num_preconfigured_keys += 1

            c_file.writelines(
                '    { 0, 0xFFFF, {} },\n'
                for _ in range(num_preconfigured_keys, key_storage_size)
            )

            c_file.write(dedent("""\
                };
                ZPS_tsAplApsKeyDescriptorEntry *psAplDefaultDistributedAPSLinkKey = &s_keyPairTableStorage[%d];
                ZPS_tsAplApsKeyDescriptorEntry *psAplDefaultGlobalAPSLinkKey = &s_keyPairTableStorage[%d];
                PRIVATE uint32 au32IncomingFrameCounter[%d];
                PRIVATE ZPS_tsAplApsKeyDescriptorTable s_keyPairTable = { s_keyPairTableStorage, %d };

                """) % (key_pair_table_size + 1, key_pair_table_size + 2, key_storage_size, key_pair_table_size))

        designated_coordinator = 'TRUE' if config_node.tag == 'Coordinator' else 'FALSE'
        if 'apsDesignatedCoordinator' in config_node.attrib:
            designated_coordinator = (
                'TRUE' if config_node.attrib['apsDesignatedCoordinator'].lower() == 'true' else 'FALSE'
            )

        use_insecure_join = 'TRUE' if config_node.get('apsUseInsecureJoin', 'true').lower() == 'true' else 'FALSE'

        key_pair_storage = 'NULL'
        incoming_frame_counter = 'NULL'
        if key_pair_table_size != 0:
            key_pair_storage = '&s_keyPairTableStorage[%d]' % key_pair_table_size
            incoming_frame_counter = 'au32IncomingFrameCounter'

        c_file.write(dedent("""\
            PRIVATE ZPS_tsAplAib s_sAplAib = {
                0,
                0x%016lxULL,
                %s,
                %s,
                FALSE,
                0,
                0x%02x,
                0x%02x,
                0,
                0,
                0,
                0x%02x,
                %s,
                %s,
                %s,
                %s,
                FALSE,
                FALSE,
                0x%04x,
                %s,
                g_u32ChannelMaskList
            };
            """) % (
                int(config_node.get('apsUseExtPANId', '0'), 0),
                designated_coordinator,
                use_insecure_join,
                int(config_node.get('apsNonMemberRadius', '2'), 0),
                int(config_node.get('apsInterframeDelay', '0'), 0),
                int(config_node.get('apsMaxWindowSize', '8'), 0),
                binding_tables,
                group_table,
                key_pair_table,
                key_pair_storage,
                int(config_node.get('apsSecurityTimeoutPeriod', '3000'), 0),
                incoming_frame_counter,
            ))

        if zdo_config is not None:
            zdo_servers = [
                server for server in zdo_config
                if server.tag != 'MgmtNWKEnhanceUpdateServer'
            ]

            for server in zdo_servers:
                context_size = get_apl_server_context_size(
                    server.tag,
                    objdump,
                    options.zigbee_apl_lib_fsp,
                    endian,
                )
                c_file.write(
                    'PRIVATE uint8 s_s%sContext[%d] __attribute__ ((aligned (4)));\n'
                    % (server.tag, context_size)
                )

                if server.tag == 'BindRequestServer' and 'SimultaneousRequests' in server.attrib:
                    simultaneous_requests = int(server.attrib['SimultaneousRequests'], 0)
                    c_file.write(
                        'PRIVATE zps_tsZdoServerConfAckContext s_s%sAcksDcfmContext[%d];\n'
                        % (server.tag, simultaneous_requests)
                    )

            def zdo_server_sort_key(server):
                if server.tag == 'DefaultServer':
                    return 1
                if server.tag == 'ZdoClient':
                    return -1
                return 0

            c_file.write(dedent("""\

                /* ZDO Servers */
                PRIVATE const zps_tsAplZdoServer s_asAplZdoServers[%d] = {
                """) % (len(zdo_servers) + 1))

            c_file.writelines(
                '    { zps_bAplZdo%s, s_s%sContext },\n' % (server.tag, server.tag)
                for server in sorted(zdo_servers, key=zdo_server_sort_key)
            )

            c_file.write(dedent("""\
                    { NULL, NULL }
                };
                """))

        c_file.write('\n/* Simple Descriptors */\n')

        for endpoint in endpoints:
            endpoint_id = int(endpoint.attrib['Id'], 0)
            input_clusters = endpoint.findall('InputClusters')
            output_clusters = endpoint.findall('OutputClusters')

            if input_clusters:
                input_cluster_values = []
                input_apdu_values = []

                for input_cluster in input_clusters:
                    cluster = find_cluster(input_cluster.attrib['Cluster'])
                    input_cluster_values.append('0x%04x' % int(cluster.attrib['Id'], 0))

                    if 'RxAPDU' in input_cluster.attrib:
                        apdu = find_apdu(config_node, input_cluster.attrib['RxAPDU'])
                        input_apdu_values.append(apdu.attrib['Name'])
                    else:
                        input_apdu_values.append('NULL')

                num_input_clusters = len(input_clusters)
                input_discovery_flags = format_cluster_discovery_flags(input_clusters)

                c_file.write(dedent("""\
                    PRIVATE const uint16 s_au16Endpoint%dInputClusterList[%d] = {
                    %s
                    };
                    PRIVATE const PDUM_thAPdu s_ahEndpoint%dInputClusterAPdus[%d] = {
                    %s
                    };
                    PRIVATE uint8 s_au8Endpoint%dInputClusterDiscFlags[%d] = {
                    %s
                    };

                    """) % (
                        endpoint_id,
                        num_input_clusters,
                        format_c_initializer(input_cluster_values),
                        endpoint_id,
                        num_input_clusters,
                        format_c_initializer(input_apdu_values),
                        endpoint_id,
                        (num_input_clusters + 7) // 8,
                        input_discovery_flags,
                    ))

            if output_clusters:
                output_cluster_values = []

                for output_cluster in output_clusters:
                    cluster = find_cluster(output_cluster.attrib['Cluster'])
                    output_cluster_values.append('0x%04x' % int(cluster.attrib['Id'], 0))

                num_output_clusters = len(output_clusters)
                output_discovery_flags = format_cluster_discovery_flags(output_clusters)

                c_file.write(dedent("""\
                    PRIVATE const uint16 s_au16Endpoint%dOutputClusterList[%d] = {
                    %s
                    };
                    PRIVATE uint8 s_au8Endpoint%dOutputClusterDiscFlags[%d] = {
                    %s
                    };

                    """) % (
                        endpoint_id,
                        num_output_clusters,
                        format_c_initializer(output_cluster_values),
                        endpoint_id,
                        (num_output_clusters + 7) // 8,
                        output_discovery_flags,
                    ))

        c_file.write(
            'PUBLIC void %s(uint8 u8Endpoint, ZPS_tsAfEvent *psStackEvent);\n'
            % config_node.attrib['DefaultCallbackName']
        )

        c_file.write(dedent("""\
            tszQueue zps_msgMlmeDcfmInd;
            tszQueue zps_msgMcpsDcfmInd;
            tszQueue zps_TimeEvents;
            tszQueue zps_msgMcpsDcfm;
            PRIVATE zps_tsAplAfSimpleDescCont s_asSimpleDescConts[%d] = {
            """) % len(endpoints))

        for endpoint in endpoints:
            endpoint_id = int(endpoint.attrib['Id'], 0)
            num_input_clusters = len(endpoint.findall('InputClusters'))
            num_output_clusters = len(endpoint.findall('OutputClusters'))
            profile = find_profile(endpoint.attrib['Profile'])
            flags = 1 if endpoint.attrib['Enabled'].lower() == 'true' else 0

            c_file.write(indent(dedent("""\
                {
                    {
                        0x%04x,
                        %s,
                        %s,
                        %d,
                        %d,
                        %d,
                        %s,
                        %s,
                        %s,
                        %s,
                    },
                    %s,
                    %d
                },
                """), '    ') % (
                    int(profile.attrib['Id'], 0),
                    endpoint.attrib['ApplicationDeviceId'],
                    endpoint.attrib['ApplicationDeviceVersion'],
                    endpoint_id,
                    num_input_clusters,
                    num_output_clusters,
                    's_au16Endpoint%dInputClusterList' % endpoint_id if num_input_clusters else 'NULL',
                    's_au16Endpoint%dOutputClusterList' % endpoint_id if num_output_clusters else 'NULL',
                    's_au8Endpoint%dInputClusterDiscFlags' % endpoint_id if num_input_clusters else 'NULL',
                    's_au8Endpoint%dOutputClusterDiscFlags' % endpoint_id if num_output_clusters else 'NULL',
                    's_ahEndpoint%dInputClusterAPdus' % endpoint_id if num_input_clusters else 'NULL',
                    flags,
                ))

        c_file.write(dedent("""\
            };

            /* Node Descriptor */
            PRIVATE ZPS_tsAplAfNodeDescriptor s_sNodeDescriptor = {
            """))

        node_descriptor = config_node.find('NodeDescriptor')

        logical_type = node_descriptor.attrib['LogicalType']
        if logical_type == 'ZC':
            c_file.write('    0,\n')
        elif logical_type == 'ZR':
            c_file.write('    1,\n')
        elif logical_type == 'ZED':
            c_file.write('    2,\n')

        c_file.write(indent(dedent("""\
            %s,
            %s,
            0,
            """), '    ') % (
                node_descriptor.attrib['ComplexDescriptorAvailable'].upper(),
                node_descriptor.attrib['UserDescriptorAvailable'].upper(),
            ))

        frequency_band = None

        if temp_interface > 0:
            frequency_band = 0
            if temp_interface & 1:
                frequency_band |= 16
            if temp_interface & 2:
                frequency_band |= 8
        elif node_descriptor.attrib['FrequencyBand'] == '868MHz':
            frequency_band = 0
        elif node_descriptor.attrib['FrequencyBand'] == '915MHz':
            frequency_band = 4
        elif node_descriptor.attrib['FrequencyBand'] == '2.4GHz':
            frequency_band = 8

        if frequency_band is not None:
            c_file.write('    0x%02x,\n' % frequency_band)

        mac_flags = sum(
            1 << bit
            for attribute, bit in (
                ('AlternatePANCoordinator', 0),
                ('DeviceType', 1),
                ('PowerSource', 2),
                ('RxOnWhenIdle', 3),
                ('Security', 6),
                ('AllocateAddress', 7),
            )
            if node_descriptor.attrib[attribute].lower() == 'true'
        )

        server_mask = (22 << 9) | sum(
            1 << bit
            for attribute, bit in (
                ('PrimaryTrustCenter', 0),
                ('BackupTrustCenter', 1),
                ('PrimaryBindingTableCache', 2),
                ('BackupBindingTableCache', 3),
                ('PrimaryDiscoveryCache', 4),
                ('BackupDiscoveryCache', 5),
                ('NetworkManager', 6),
            )
            if node_descriptor.attrib[attribute].lower() == 'true'
        )

        descriptor_capabilities = sum(
            1 << bit
            for attribute, bit in (
                ('ExtendedActiveEndpointListAvailable', 0),
                ('ExtendedSimpleDescriptorListAvailable', 1),
            )
            if node_descriptor.attrib[attribute].lower() == 'true'
        )

        c_file.write(dedent("""\
                %d,
                0x%02x,
                0x%04x,
                0x%02x,
                0x%04x,
                0x%04x,
                0x%04x,
                0x%02x,
            };
            """) % (
                int(node_descriptor.attrib['APSFlags'], 0),
                mac_flags,
                int(node_descriptor.attrib['ManufacturerCode'], 0),
                int(node_descriptor.attrib['MaximumBufferSize'], 0),
                int(node_descriptor.attrib['MaximumIncomingTransferSize'], 0),
                server_mask,
                int(node_descriptor.attrib['MaximumOutgoingTransferSize'], 0),
                descriptor_capabilities,
            ))

        node_power_descriptor = config_node.find('NodePowerDescriptor')

        current_power_mode = {
            'Synchronised with RxOnWhenIdle': 0,
            'Periodic': 1,
            'Stimulated': 2,
        }.get(node_power_descriptor.attrib['DefaultPowerMode'], 0)

        available_power_sources = sum(
            1 << bit
            for attribute, bit in (
                ('ConstantPower', 0),
                ('RechargeableBattery', 1),
                ('DisposableBattery', 2),
            )
            if node_power_descriptor.attrib[attribute].lower() == 'true'
        )

        current_power_source = {
            'Constant Power': 1,
            'Rechargeable Battery': 2,
            'Disposable Battery': 4,
        }.get(node_power_descriptor.attrib['DefaultPowerSource'], 0)

        power_descriptor_fields = [
            '0x%01x' % current_power_mode,
            '0x%01x' % available_power_sources,
            '0x%01x' % current_power_source,
            '0xC',
        ]

        if endian != 'BIG_ENDIAN':
            power_descriptor_fields.reverse()

        c_file.write(dedent("""\

            /* Node Power Descriptor */
            PRIVATE ZPS_tsAplAfNodePowerDescriptor s_sNodePowerDescriptor = {
                %s
            };
            """) % ',\n    '.join(power_descriptor_fields))

        user_descriptor = config_node.find('UserDescriptor')
        if user_descriptor is not None:
            user_description = ', '.join(
                "'%c'" % character
                for character in user_descriptor.attrib['UserDescription'][:16]
            )

            c_file.write(dedent("""\

                /* User Descriptor */
                PRIVATE ZPS_tsAplAfUserDescriptor s_sUserDescriptor = {
                    { %s },
                };
                """) % user_description)

        aps_duplicate_table_size = int(config_node.attrib['APSDuplicateTableSize'], 0)

        c_file.write(dedent("""\

            /* APSDE duplicate table */
            PRIVATE uint16 s_au16ApsDuplicateTableAddrs[%d];
            PRIVATE uint32 s_au32ApsDuplicateTableHash[%d];
            PRIVATE uint8 s_au8ApsDuplicateTableSeqCnts[%d];
            PRIVATE zps_tsApsDuplicateTable s_sApsDuplicateTable = {
                s_au16ApsDuplicateTableAddrs,
                s_au32ApsDuplicateTableHash,
                s_au8ApsDuplicateTableSeqCnts,
                0,
            };

            /* APSDE sync msg pool */
            PRIVATE zps_tsMsgRecord s_asApsSyncMsgPool[%d];

            /* APSDE dcfm record pool */
            PRIVATE zps_tsDcfmRecord s_asApsDcfmRecordPool[%d];
            """) % (
                max(aps_duplicate_table_size, 1),
                aps_duplicate_table_size,
                aps_duplicate_table_size,
                int(config_node.attrib['MaxNumSimultaneousApsdeAckReq'], 0),
                int(config_node.attrib['MaxNumSimultaneousApsdeReq'], 0),
            ))

        fragment_rx_pool_size = int(config_node.attrib['FragmentationMaxNumSimulRx'], 0)
        if fragment_rx_pool_size > 0:
            c_file.write(dedent("""\

                /* APSDE fragmentation rx pool */
                PRIVATE zps_tsFragmentReceive s_asApsFragRxPool[%d];
                """) % fragment_rx_pool_size)

        fragment_tx_pool_size = int(config_node.attrib['FragmentationMaxNumSimulTx'], 0)
        if fragment_tx_pool_size > 0:
            c_file.write(dedent("""\

                /* APSDE fragmentation tx pool */
                PRIVATE zps_tsFragmentTransmit s_asApsFragTxPool[%d];
                """) % fragment_tx_pool_size)

        num_apsme_cmd_containers = int(config_node.get('NumAPSMESimulCommands', '4'), 0)
        if num_apsme_cmd_containers <= 0:
            num_apsme_cmd_containers = 4

        c_file.write(dedent("""\

            /* APSME Command Manager Command Containers */
            PRIVATE zps_tsApsmeCmdContainer s_sApsmeCmdContainer_%d = {
                NULL, {}, {}, NULL, 0
            };
            """) % num_apsme_cmd_containers)

        c_file.writelines(
            dedent("""\
                PRIVATE zps_tsApsmeCmdContainer s_sApsmeCmdContainer_%d = {
                    &s_sApsmeCmdContainer_%d, {}, {}, NULL, 0
                };
                """) % (index, index + 1)
            for index in range(num_apsme_cmd_containers - 1, 0, -1)
        )

        security_disabled = False
        security_initial_key = False

        if not security_disabled and 'InitialNetworkKey' in config_node.attrib:
            init_key = find_network_key(config_node.attrib['InitialNetworkKey'])

            if init_key is not None:
                key_type = init_key.get('{%s}type' % XSI_NAMESPACE)

                if (
                    key_type == 'zpscfg:PreConfiguredNwkKey'
                    or trust_center_present and key_type == 'zpscfg:DefaultNwkKey'
                ):
                    c_file.write(dedent("""\

                        /* Initial Nwk Key */
                        zps_tsAplZdoInitSecKey s_sInitSecKey = {
                            { %s },
                            0x%02x,
                            ZPS_NWK_SEC_NETWORK_KEY,
                        };
                        """) % (
                            network_key_str(init_key),
                            int(init_key.attrib['KeySequenceNumber'], 0),
                        ))
                    security_initial_key = True

        if not security_disabled and trust_center_present:
            device_table_size = int(trust_center.attrib['DeviceTableSize'], 0)

            c_file.write(dedent("""\

                /* Trust Center */
                PRIVATE zps_tsAplTCDeviceTable s_asTrustCenterDeviceTable[%d] = {
                """) % device_table_size)

            c_file.writelines(
                '    { 0xFFFF, 0 },\n'
                for _ in range(device_table_size)
            )

            c_file.write(dedent("""\
                };
                PRIVATE zps_tsTrustCenterContext s_sTrustCenterContext = {
                    zps_vAplTrustCenterInit,
                    zps_vAplTrustCenterUpdateDevice,
                    zps_vAplTrustCenterRequestKey,
                    { s_asTrustCenterDeviceTable, %d },
                    FALSE,
                    FALSE,
                };

                """) % device_table_size)

        nwk_context_size = get_lib_obj_size(
            objdump,
            options.zigbee_nwk_lib_fsp,
            endian,
        )
        active_neighbour_table_size = int(config_node.attrib['ActiveNeighbourTableSize'], 0)
        address_map_table_size = int(config_node.attrib['AddressMapTableSize'], 0) + 4

        leave_allowed_default = 1
        if config_node.tag == 'ChildNodes' and config_node.get('{%s}type' % XSI_NAMESPACE) == 'zpscfg:EndDevice':
            leave_allowed_default = 0

        zed_timeout_table_size = active_neighbour_table_size
        if 'ChildTableSize' in config_node.attrib:
            zed_timeout_table_size = int(config_node.attrib['ChildTableSize'], 0)

        c_file.write(dedent("""\

            /* Network Layer Context */
            PRIVATE uint8                   s_sNwkContext[%d] __attribute__ ((aligned (4)));
            PRIVATE ZPS_tsNwkDiscNtEntry    s_asNwkNtDisc[%d];
            PRIVATE ZPS_tsNwkActvNtEntry    s_asNwkNtActv[%d];
            PRIVATE ZPS_tsNwkRtDiscEntry    s_asNwkRtDisc[%d];
            PRIVATE ZPS_tsNwkRtEntry        s_asNwkRt[%d];
            PRIVATE ZPS_tsNwkBtr            s_asNwkBtt[%d];
            PRIVATE ZPS_tsNwkRctEntry       s_asNwkRct[%d];
            PRIVATE ZPS_tsNwkSecMaterialSet s_asNwkSecMatSet[%d];
            PRIVATE uint32                  s_asNwkInFCSet[%d];
            PRIVATE uint16                  s_au16NwkAddrMapNwk[%d];
            PRIVATE uint16                  s_au16NwkAddrMapLookup[%d];
            PRIVATE uint64                  s_au64NwkAddrMapExt[%d];
            #ifdef ZPS_FRQAG
            PRIVATE uint32                  s_au32RxPacketCount[%d];
            PRIVATE uint32                  s_au32TxPacketCount[%d];
            #endif
            PRIVATE uint32                  s_au32ZedTimeoutCount[%d];
            PRIVATE uint8                   s_au8KeepAliveFlags[%d];
            """) % (
                nwk_context_size,
                int(config_node.attrib['DiscoveryNeighbourTableSize'], 0),
                active_neighbour_table_size,
                int(config_node.attrib['RouteDiscoveryTableSize'], 0),
                int(config_node.attrib['RoutingTableSize'], 0),
                int(config_node.attrib['BroadcastTransactionTableSize'], 0),
                int(config_node.attrib['RouteRecordTableSize'], 0),
                int(config_node.attrib['SecurityMaterialSets'], 0),
                active_neighbour_table_size,
                address_map_table_size,
                address_map_table_size,
                int(config_node.attrib['MacTableSize'], 0) + 4,
                active_neighbour_table_size,
                active_neighbour_table_size,
                zed_timeout_table_size,
                zed_timeout_table_size,
            ))

        if 'ChildTableSize' in config_node.attrib:
            child_table_size = int(config_node.attrib['ChildTableSize'], 0)
        elif config_node.tag == 'ChildNodes' and config_node.get('{%s}type' % XSI_NAMESPACE) == 'zpscfg:EndDevice':
            child_table_size = active_neighbour_table_size
        else:
            child_table_size = active_neighbour_table_size // 6

        c_file.write(dedent("""\

            PRIVATE const zps_tsNwkNibInitialValues s_sNibInitialValues = {
                600,
                05,
                7,
                15,
                1,
                2,
                11,
                18,
                0,
                2,
                0,
                1,
                0,
                15,
                3,
                3,
                255,
                5,
                TRUE,
                TRUE,
                5,
                4,
                %d,
                2,
                8,
                0, /* u16VerifyLinkCostTransmitRate */
            };

            PRIVATE const ZPS_tsNwkNibTblSize s_sNwkTblSize = {
                %d,
                %d,
                %d,
                %d,
                %d,
                %d,
                %d,
                %d,
                sizeof(s_sNibInitialValues),
                %d,
                %d,
            };

            PRIVATE const ZPS_tsNwkNibTbl s_sNwkTbl = {
                s_asNwkNtDisc,
                s_asNwkNtActv,
                s_asNwkRtDisc,
                s_asNwkRt,
                s_asNwkBtt,
                s_asNwkRct,
                s_asNwkSecMatSet,
                (ZPS_tsNwkNibInitialValues *)&s_sNibInitialValues,
                s_au16NwkAddrMapNwk,
                s_au16NwkAddrMapLookup,
                s_asNwkInFCSet,
            #if (defined JENNIC_CHIP_FAMILY_JN516x) || (JENNIC_CHIP_FAMILY_JN517x)
                0,
            #endif
                s_au64NwkAddrMapExt,
                s_au32ZedTimeoutCount,
                s_au8KeepAliveFlags,
            #ifdef ZPS_FRQAG
                s_au32RxPacketCount,
                s_au32TxPacketCount,
            #else
                NULL,
                NULL,
            #endif
            };

            /* Application Layer Context */
            """) % (
                leave_allowed_default,
                active_neighbour_table_size,
                int(config_node.attrib['RoutingTableSize'], 0),
                int(config_node.attrib['RouteRecordTableSize'], 0),
                int(config_node.attrib['AddressMapTableSize'], 0),
                int(config_node.attrib['DiscoveryNeighbourTableSize'], 0),
                int(config_node.attrib['RouteDiscoveryTableSize'], 0),
                int(config_node.attrib['BroadcastTransactionTableSize'], 0),
                int(config_node.attrib['SecurityMaterialSets'], 0),
                child_table_size,
                int(config_node.attrib['MacTableSize'], 0),
            ))

        num_request_key_requests = 4 if not security_disabled and trust_center_present else 1
        timeout_request_key = 15

        c_file.write(dedent("""\
            PRIVATE zps_tsRequestKeyRequests s_asRequestKeyRequests[%d];
            PRIVATE zps_tsApl s_sApl = {
                s_sNwkContext,
                &s_sNwkTblSize,
                &s_sNwkTbl,
                NULL,
                &s_sAplAib,
                zps_vGetZpsMutex,
                &%s,
                &zps_msgMcpsDcfmInd,
                &zps_msgMlmeDcfmInd,
                &zps_TimeEvents,
                &zps_msgMcpsDcfm,
                { 0 },
            """) % (
                num_request_key_requests,
                config_node.attrib['DefaultCallbackName'],
            ))

        node_type = '<undefined>'

        if config_node.tag == 'Coordinator':
            node_type = 'ZPS_ZDO_DEVICE_COORD'
        elif config_node.tag == 'ChildNodes':
            if config_node.get('{%s}type' % XSI_NAMESPACE) == 'zpscfg:Router':
                node_type = 'ZPS_ZDO_DEVICE_ROUTER'
            elif config_node.get('{%s}type' % XSI_NAMESPACE) == 'zpscfg:EndDevice':
                node_type = 'ZPS_ZDO_DEVICE_ENDDEVICE'

        permit_joining_time = int(config_node.get('PermitJoiningTime', '0'), 0)
        scan_duration = int(config_node.get('ScanDuration', '4'), 0)
        max_num_poll_failures = int(config_node.get('NumPollFailuresBeforeRejoin', '3'), 0)

        aps_persistence_time = int(config_node.get('APSPersistenceTime', '100'), 0)
        if aps_persistence_time <= 25:
            aps_persistence_time = 100

        aps_poll_period = int(config_node.get('APSPollPeriod', '100'), 0)
        if aps_poll_period <= 25:
            aps_poll_period = 100

        c_file.write(dedent("""\
                {
                    { 0x1B, 0x19, 0x4A },
                    0,
                    %s,
                    ZPS_ZDO_PRECONFIGURED_LINK_KEY,
                    0x%02x,
                    2,
                    2,
                    %d,
                    FALSE,
                    s_asAplZdoServers,
                    vZdoServersInit,
                    { /* timer struct */ },
                    { /* timer struct */ },
                    0,
                    %d,
                    %d,
                    %s,
                    0,
                    NULL,
                    NULL,
                    s_asRequestKeyRequests,
                    %d,
                    %d,
                },
                {
                    NULL,
                    &s_sNodeDescriptor,
                    &s_sNodePowerDescriptor,
                    %d,
                    s_asSimpleDescConts,
                    %s,
                    NULL,
                    0xff,
                    0x00,
                },
                {
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    NULL,
                    &s_sApsDuplicateTable,
                    s_asApsSyncMsgPool,
                    0x%02x,
                    0,
                    %d,
                    0,
                    { s_asApsDcfmRecordPool, 1, %d },
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    { &s_sApsmeCmdContainer_1, NULL },
                    { { /* Timer */ }, %d, 0 },
                    { NULL, NULL },
                    { /* Timer */ },
                },
                %s
            };
            """) % (
                node_type,
                permit_joining_time,
                scan_duration,
                max_num_poll_failures,
                security_disabled,
                '&s_sInitSecKey' if not security_disabled and security_initial_key else 'NULL',
                timeout_request_key * 62500,
                num_request_key_requests,
                len(endpoints),
                '&s_sUserDescriptor' if user_descriptor is not None else 'NULL',
                aps_duplicate_table_size,
                int(config_node.attrib['MaxNumSimultaneousApsdeAckReq'], 0),
                int(config_node.attrib['MaxNumSimultaneousApsdeReq'], 0),
                (
                    '{ s_asApsFragRxPool, %d, %d }' % (fragment_rx_pool_size, aps_persistence_time)
                    if fragment_rx_pool_size > 0
                    else '{ NULL, 0, 0 }'
                ),
                (
                    '{ s_asApsFragTxPool, %d }' % fragment_tx_pool_size
                    if fragment_tx_pool_size > 0
                    else '{ NULL, 0 }'
                ),
                'zps_eStartFragmentedTransmission' if fragment_tx_pool_size > 0 else 'NULL',
                'zps_vHandleExtendedDataAckFrame' if fragment_tx_pool_size > 0 else 'NULL',
                (
                    'zps_vHandleApsdeDataFragInd'
                    if fragment_rx_pool_size > 0
                    else 'zps_vHandleApsdeDataFragIndNotSupported'
                ),
                aps_poll_period,
                '&s_sTrustCenterContext' if not security_disabled and trust_center_present else 'NULL',
            ))

        c_file.write(dedent("""\

            const void *zps_g_pvApl = &s_sApl;

            /****************************************************************************/
            /***        Exported Variables                                            ***/
            /****************************************************************************/

            /****************************************************************************/
            /***        Exported Functions                                            ***/
            /****************************************************************************/

            /****************************************************************************/
            /***        Local Functions                                               ***/
            /****************************************************************************/

            /****************************************************************************
             *
             * NAME: ZPS_psMacIFTGetTable
             *
             * DESCRIPTION:
             * Obtain the pointer to the Mac interface table
             *
             * PARAMETERS             Name                 RW      Usage
             *
             * RETURNS:
             * Address of Mac interface table if successful, NULL otherwise
             *
             ****************************************************************************/
            PUBLIC MAC_tsMacInterfaceTable *ZPS_psMacIFTGetTable(void)
            {
                return &g_asMacInterfaceTable;
            }

            /****************************************************************************
             *
             * NAME: ZPS_psMacIFTGetInterface
             *
             * DESCRIPTION:
             * Get the Mac interface entry from the MAC interface table for the specified
             * Mac ID
             *
             * PARAMETERS     Name          RW      Usage
             *                u8MacID        R        The Mac Id for the interface
             * RETURNS:
             * Address of Mac interface structure if found, NULL otherwise
             *
             ****************************************************************************/
            PUBLIC MAC_tsMacInterface *ZPS_psMacIFTGetInterface(uint8 u8MacID)
            {
                MAC_tsMacInterface *pRet = NULL;
                if (u8MacID < g_asMacInterfaceTable.u8NumInterfaces)
                {
                    pRet = &g_asMacInterfaceTable.psMacInterfaces[u8MacID];
                }
                return pRet;
            }
            """))

        if zdo_config is not None:
            c_file.write(dedent("""\

                /* ZDO Server Initialisation */
                PRIVATE void vZdoServersInit(void)
                {
                """))

            version_instruction = (
                'l.addi r0,r0,hi(%s)'
                if endian == 'BIG_ENDIAN'
                else 'LDR R0, =%s'
            )

            c_file.write('    /* Version compatibility check */\n')
            c_file.writelines(
                (
                    '    asm(".extern %s" : );\n'
                    '    asm("%s" : );\n'
                ) % (version, version_instruction % version)
                for version in (
                    'ZPS_APL_Version_3v0',
                    'ZPS_NWK_Version_3v0',
                )
            )

            for server in zdo_config:
                if server.tag == 'MgmtNWKEnhanceUpdateServer':
                    continue

                output_params, _ = ZDO_SERVERS[server.tag]
                c_file.write('    zps_vAplZdo%sInit(' % server.tag)
                output_params(c_file, config_node, server.tag, server)
                c_file.write(');\n')

            if config_node.tag == 'Coordinator' or config_node.get('{%s}type' % XSI_NAMESPACE) == 'zpscfg:Router':
                c_file.write('    zps_vRegisterCallbackForSecondsTick(ZPS_vSecondTimerCallback);\n')

            c_file.write('}\n')

        c_file.write(dedent("""\

            PUBLIC void *ZPS_vGetGpContext(void)
            {
                return g_psGreenPowerContext;
            }

            PUBLIC void *zps_vGetZpsMutex(void)
            {
                return &g_pbZpsMutex;
            }

            PUBLIC void ZPS_vGetOptionalFeatures(void)
            {
            """))

        if config_node.get('InterPAN', '').lower() == 'true':
            c_file.write('    ZPS_vAfInterPanInit();\n')

        if config_node.get('GreenPowerSupport', '').lower() == 'true':
            c_file.write('    ZPS_vRegisterGreenPower();\n')

            if gp_tx_queue is not None:
                c_file.write('    ZPS_vZgpInitGpTxQueue();\n')

            if gp_security_table is not None:
                c_file.write('    ZPS_vZgpInitGpSecurityTable();\n')

        c_file.write(dedent("""\
            }

            /****************************************************************************/
            /***        END OF FILE                                                   ***/
            /****************************************************************************/
            """))

    os.chmod(fsp, os.stat(fsp).st_mode & (~stat.S_IWUSR | stat.S_IRUSR))


def output_header(output_dir: str, config_node: ElementTree.Element) -> None:
    fsp = os.path.join(output_dir, 'zps_gen.h')
    if os.path.exists(fsp):
        os.chmod(fsp, os.stat(fsp).st_mode | stat.S_IWUSR)

    with open(fsp, 'w') as h_file:
        h_file.write(dedent("""\
            /****************************************************************************
             *
             *                 THIS IS A GENERATED FILE. DO NOT EDIT!
             *
             * MODULE:         ZPSConfig
             *
             * COMPONENT:      zps_gen.h
             *
             * DATE:           %s
             *
             * AUTHOR:         NXP Zigbee Protocol Stack Configuration Tool
             *
             * DESCRIPTION:    ZPS definitions
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
             * Copyright NXP B.V. 2017. All rights reserved
             ****************************************************************************/

            #ifndef _ZPS_GEN_H
            #define _ZPS_GEN_H

            #include <jendefs.h>

            /****************************************************************************/
            /***        Macro Definitions                                             ***/
            /****************************************************************************/

            #define ZPS_NWK_OPT_ALL
            """) % datetime.datetime.now().ctime())

        if config_node.tag == 'Coordinator':
            h_file.write('#define ZPS_COORDINATOR\n')
        elif config_node.tag == 'ChildNodes':
            if config_node.get('{%s}type' % XSI_NAMESPACE) == 'zpscfg:Router':
                h_file.write('#define ZPS_ROUTER\n')
            elif config_node.get('{%s}type' % XSI_NAMESPACE) == 'zpscfg:EndDevice':
                h_file.write('#define ZPS_END_DEVICE\n')

        h_file.write('#define ZPS_NODE_%s\n' % config_node.attrib['Name'].upper())

        for profile in config.findall('Profiles'):
            profile_name = profile.attrib['Name']
            profile_macro = profile_name.upper()

            h_file.write(dedent("""\

                /* Profile '%s' */
                #define %s_PROFILE_ID (0x%04x)
                """) % (profile_name, profile_macro, int(profile.attrib['Id'], 0)))

            h_file.writelines(
                '#define %s_%s_CLUSTER_ID (0x%04x)\n'
                % (profile_macro, cluster.attrib['Name'].upper(), int(cluster.attrib['Id'], 0))
                for cluster in profile.findall('Clusters')
            )

        coordinator = config.find('Coordinator')
        if coordinator is not None:
            node_name = coordinator.attrib['Name']
            node_macro = node_name.upper()

            h_file.write(dedent("""\

                /* Node '%s' */
                /* Endpoints */
                """) % node_name)

            h_file.writelines(
                '#define %s_%s_ENDPOINT (%d)\n'
                % (node_macro, endpoint.attrib['Name'].upper(), int(endpoint.attrib['Id'], 0))
                for endpoint in coordinator.findall('Endpoints')
            )

        for node in config.findall('ChildNodes'):
            node_name = node.attrib['Name']
            node_macro = node_name.upper()

            h_file.write(dedent("""\

                /* Node '%s' */
                /* Endpoints */
                """) % node_name)

            h_file.writelines(
                '#define %s_%s_ENDPOINT (%d)\n'
                % (node_macro, endpoint.attrib['Name'].upper(), int(endpoint.attrib['Id'], 0))
                for endpoint in node.findall('Endpoints')
            )

        bind_table_size = 0
        group_table_size = 0
        child_table_size = 0
        channel_mask_list_count = 0

        binding_table = config_node.find('BindingTable')
        if binding_table is not None:
            bind_table_size = int(binding_table.attrib['Size'], 0)

        group_table = config_node.find('GroupTable')
        if group_table is not None:
            group_table_size = int(group_table.attrib['Size'], 0)

        if 'ChildTableSize' in config_node.attrib:
            child_table_size = int(config_node.attrib['ChildTableSize'], 0)

        mac_interface_list = config_node.find('MacInterfaceList')
        if mac_interface_list is not None:
            channel_mask_list_count = sum(
                int(interface.attrib['ChannelListSize'])
                for interface in mac_interface_list.findall('MacInterface')
            )

        h_file.write(dedent("""\

            /* Table Sizes */
            #define ZPS_NEIGHBOUR_TABLE_SIZE (%d)
            #define ZPS_ADDRESS_MAP_TABLE_SIZE (%d)
            #define ZPS_ROUTING_TABLE_SIZE (%d)
            #define ZPS_MAC_ADDRESS_TABLE_SIZE (%d)
            #define ZPS_BINDING_TABLE_SIZE (%d)
            #define ZPS_GROUP_TABLE_SIZE (%d)
            #define ZPS_CHILD_TABLE_SIZE (%d)
            #define ZPS_MAX_CHANNEL_LIST_SIZE (%d)

            /****************************************************************************/
            /***        Type Definitions                                              ***/
            /****************************************************************************/

            /****************************************************************************/
            /***        External Variables                                            ***/
            /****************************************************************************/

            extern void *g_pvApl;

            /****************************************************************************/
            /***        Exported Functions                                            ***/
            /****************************************************************************/

            PUBLIC void *ZPS_vGetGpContext(void);

            /****************************************************************************/
            /****************************************************************************/
            /****************************************************************************/

            #endif
            """) % (
                int(config_node.attrib['ActiveNeighbourTableSize'], 0),
                int(config_node.attrib['AddressMapTableSize'], 0),
                int(config_node.attrib['RoutingTableSize'], 0),
                int(config_node.attrib['MacTableSize'], 0),
                bind_table_size,
                group_table_size,
                child_table_size,
                channel_mask_list_count,
            ))

    os.chmod(fsp, os.stat(fsp).st_mode & (~stat.S_IWUSR | stat.S_IRUSR))


def output_default_server_init_params(
    output_file: TextIO,
    config_node: ElementTree.Element,
    node_name: str,
    node: ElementTree.Element,
) -> None:
    apdu = find_apdu(config_node, node.get('OutputAPdu'))
    if apdu is None:
        print("WARNING: Server '%s' has no output APDU defined.\n" % node_name)
        return

    output_file.write('&s_s%sContext, %s' % (node_name, apdu.attrib['Name']))


def output_default_server_init_param_types(output_file: TextIO) -> None:
    output_file.write('void *, PDUM_thAPdu')


def output_end_device_bind_server_init_params(
    output_file: TextIO,
    config_node: ElementTree.Element,
    node_name: str,
    node: ElementTree.Element,
) -> None:
    apdu = find_apdu(config_node, node.get('OutputAPdu'))
    if apdu is None:
        print("WARNING: Server '%s' has no output APDU defined.\n" % node_name)
        return

    output_file.write(
        '&s_s%sContext, %s, %d, %d'
        % (
            node_name, apdu.attrib['Name'],
            int(node.attrib['Timeout'], 0) * 62500, int(node.attrib['BindNumRetries'], 0)
        )
    )


def output_end_device_bind_server_init_param_types(output_file: TextIO) -> None:
    output_file.write('void *, PDUM_thAPdu, uint32, uint8')


def output_device_annce_server_init_params(
    output_file: TextIO,
    config_node: ElementTree.Element,
    node_name: str,
    node: ElementTree.Element,
) -> None:
    output_file.write('&s_s%sContext' % node_name)


def output_device_annce_server_init_param_types(output_file: TextIO) -> None:
    output_file.write('void *')


def output_mgmt_nwk_update_server_init_params(
    output_file: TextIO,
    config_node: ElementTree.Element,
    node_name: str,
    node: ElementTree.Element,
) -> None:
    apdu = find_apdu(config_node, node.get('OutputAPdu'))
    if apdu is None:
        print("WARNING: Server '%s' has no output APDU defined.\n" % node_name)
        return

    output_file.write('&s_s%sContext, %s, &s_sApl' % (node_name, apdu.attrib['Name']))


def output_mgmt_nwk_update_init_param_types(output_file: TextIO) -> None:
    output_file.write('void *, PDUM_thAPdu , void *')


def output_bind_request_server_init_params(
    output_file: TextIO,
    config_node: ElementTree.Element,
    node_name: str,
    node: ElementTree.Element,
) -> None:
    output_file.write(
        '&s_s%sContext, %d, %d, s_sBindRequestServerAcksDcfmContext'
        % (node_name, int(node.attrib['TimeInterval'], 0), int(node.attrib['SimultaneousRequests'], 0))
    )


def output_bind_request_server_init_param_types(output_file: TextIO) -> None:
    output_file.write('void *, uint8, uint8, zps_tsZdoServerConfAckContext *')


ZDO_SERVERS = {
    'EndDeviceBindServer': (
        output_end_device_bind_server_init_params,
        output_end_device_bind_server_init_param_types,
    ),
    'DefaultServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'DeviceAnnceServer': (
        output_device_annce_server_init_params,
        output_device_annce_server_init_param_types,
    ),
    'ActiveEpServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'NwkAddrServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'IeeeAddrServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'SystemServerDiscoveryServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'PermitJoiningServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'NodeDescServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'PowerDescServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'MatchDescServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'SimpleDescServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'MgmtLqiServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'MgmtRtgServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'MgmtLeaveServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'MgmtNWKUpdateServer': (
        output_mgmt_nwk_update_server_init_params,
        output_mgmt_nwk_update_init_param_types,
    ),
    'MgmtBindServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'BindUnbindServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'ExtendedActiveEpServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'ExtendedSimpleDescServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'ZdoClient': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'BindRequestServer': (
        output_bind_request_server_init_params,
        output_bind_request_server_init_param_types,
    ),
    'ParentAnnceServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'MgmtMibIeeeServer': (
        output_default_server_init_params,
        output_default_server_init_param_types,
    ),
    'MgmtNWKEnhanceUpdateServer': (
        output_mgmt_nwk_update_server_init_params,
        output_mgmt_nwk_update_init_param_types,
    ),
}

if not options.optional_features:
    print('ZPSConfig - Zigbee Protocol Stack Configuration Tool v%s\n' % __VERSION__)

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
    sys.exit(2)

if not options.zigbee_node_name:
    print('ERROR: A node must be specified.\n')
    sys.exit(9)

if not os.path.exists(options.config_filename):
    print("ERROR: Unable to open configuration file '%s'.\n" % options.config_filename)
    sys.exit(3)

config = parse_configuration(options.config_filename)

if options.optional_features:
    optional_features = 0
    node = find_node(options.zigbee_node_name)

    if node is not None:
        if 'InterPAN' in node.attrib:
            if 'true' == node.attrib['InterPAN'].lower():
                optional_features |= 1

        if 'GreenPowerSupport' in node.attrib:
            if 'true' == node.attrib['GreenPowerSupport'].lower():
                optional_features |= 2

    print(optional_features)
    sys.exit(0)

if not os.path.exists(options.zigbee_nwk_lib_fsp):
    print("ERROR: Unable to locate Zigbee target library file '%s'.\n" % options.zigbee_nwk_lib_fsp)
    sys.exit(4)

if not os.path.exists(options.zigbee_apl_lib_fsp):
    print("ERROR: Unable to locate Zigbee target library file '%s'.\n" % options.zigbee_apl_lib_fsp)
    sys.exit(5)

if not os.path.exists(options.output_dir):
    print("ERROR: Output directory '%s' does not exist.\n" % options.output_dir)
    sys.exit(6)

if not os.path.exists(options.tools_dir):
    print("ERROR: Unable to locate Compiler Tools directory '%s'.\n" % options.tools_dir)
    sys.exit(7)

objdump_name = 'ba-elf-objdump' if options.endian == 'BIG_ENDIAN' else 'arm-none-eabi-objdump'
objdump = os.path.normpath(os.path.join(options.tools_dir, 'bin', objdump_name))

if not validate_configuration(options.zigbee_node_name):
    sys.exit(1)

node = find_node(options.zigbee_node_name)
output_header(options.output_dir, node)
output_c(options.output_dir, node, options.endian)

print('Done.\n')
