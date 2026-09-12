# JN-SW-4170

NXP Zigbee SDK for JN516x devices, based on build 1840, with updated configuration generators
and build-system fixes.
Used by [Lumi Router](https://github.com/igorlistopad/Lumi-Router-JN5169).

## Configuration generators

Both generators create files for the selected node from an XML `.zpscfg` configuration.

- **PDUMConfig** generates PDU manager C, header, and assembly files.
- **ZPSConfig** generates Zigbee stack C and header files.

Both generators require **Python 3.5 or newer**, use only the Python standard library,
and can run on Linux, macOS, and Windows.

No additional Python packages are required. Configurations without a `Coordinator` element are supported.

Run from the SDK directory to see the available options:

```sh
python3 Tools/PDUMConfig/Source/PDUMConfig.py --help
python3 Tools/ZPSConfig/Source/ZPSConfig.py --help
```
