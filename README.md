# AirPods Battery Monitor for Windows

Displays AirPods Pro earbud battery levels in the Windows system tray via Bluetooth Low Energy (BLE).

Tested with AirPods Pro 2 on Windows. Other AirPods models may work, but battery accuracy is not guaranteed.

For the moment, the app does not show case battery. AirPods case battery data exposed through BLE on Windows can be stale or generic, so it is hidden until a reliable exact source is implemented.

## How it works

Windows does not expose original AirPods battery levels natively. This app first checks whether Windows has AirPods connected as an audio endpoint, then listens for nearby Apple BLE advertisement packets and accepts only packets matching the connected AirPods model.

## Requirements

- Windows 10/11 with Bluetooth LE support
- AirPods paired to a nearby Apple device (they must be advertising)
- Python 3.10+

## Setup

```bash
pip install -r requirements.txt
python -m src.main
```

## Build standalone EXE

```bat
build.bat
```

The output EXE will be in `dist/`.

## Connected AirPods Only

By default, the app requires Windows to report AirPods as connected before it shows BLE battery data:

```python
REQUIRE_WINDOWS_CONNECTED_HEADPHONES = True
```

This is closer to how apps like MagicPods behave: connected Windows headphones are the gate, and BLE is used only as the AirPods battery source.

## Debug BLE Packets

If detection looks wrong, run:

```bash
python debug_ble_scan.py --seconds 25
```

Open your AirPods case lid near your PC while scanning. Do not rely on a BLE address as a permanent identity; AirPods can rotate BLE advertisement addresses for privacy.

## Battery data format

Apple's Proximity Pairing Message (type `0x07`) within the manufacturer data encodes:
- Left earbud battery (0–10, multiply by 10 for %)
- Right earbud battery (0–10, multiply by 10 for %)
- Charging status flags

Case battery may also appear in raw BLE data, but this app currently does not display it because it has not been reliable enough on Windows.

## Project structure

```
src/
  main.py           — entry point, wires everything together
  ble_scanner.py    — BLE scanning and Apple packet parsing
  tray_app.py       — system tray icon and menu
  battery_ui.py     — popup window with battery details
  icon_generator.py — renders dynamic tray icon from battery %
  constants.py      — Apple BLE protocol constants
```

## Contributing

See `CONTRIBUTING.md` for local setup, build, and PR guidelines.

## License

This project is licensed under the MIT License. See `LICENSE`.
