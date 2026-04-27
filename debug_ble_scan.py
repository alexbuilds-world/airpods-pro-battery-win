"""Print raw Apple BLE manufacturer packets for debugging AirPods detection."""

import argparse
import asyncio
import time

from bleak import BleakScanner

APPLE_COMPANY_ID = 0x004C
AIRPODS_MODELS = {
    0x2002: "AirPods 1",
    0x200F: "AirPods 2",
    0x2013: "AirPods 3",
    0x2014: "AirPods Pro 2",
    0x200E: "AirPods Pro",
    0x2061: "AirPods Pro 2",
    0x2062: "AirPods Pro 2 USB-C",
    0x200A: "AirPods Max",
}


def nibble_pct(value: int) -> str:
    if value == 0xF:
        return "disconnected"
    if 0 <= value <= 10:
        return f"~{value * 10}%"
    return f"invalid({value})"


def decode_airpods(data: bytes) -> str:
    if len(data) < 8 or data[0] != 0x07:
        return ""

    model_id = (data[4] << 8) | data[3]
    model = AIRPODS_MODELS.get(model_id)
    if model is None:
        return f" apple07 unknown_model=0x{model_id:04X}"

    subtype = data[2]
    status = data[5]
    batt_pods = data[6]
    batt_case_flags = data[7]

    flip = bool(status & 0x02)
    pod_a = (batt_pods >> 4) & 0xF
    pod_b = batt_pods & 0xF
    if flip:
        left_raw, right_raw = pod_a, pod_b
    else:
        left_raw, right_raw = pod_b, pod_a

    case_raw = (batt_case_flags >> 4) & 0xF
    flags = batt_case_flags & 0xF

    return (
        f" AIRPODS model={model} model_id=0x{model_id:04X}"
        f" subtype=0x{subtype:02X} status=0x{status:02X}"
        f" left={nibble_pct(left_raw)} rawL={left_raw}"
        f" right={nibble_pct(right_raw)} rawR={right_raw}"
        f" case={nibble_pct(case_raw)} rawCase={case_raw}"
        f" flags=0x{flags:X}"
    )


async def scan(seconds: int) -> None:
    seen: set[tuple[str, str]] = set()

    def on_advertisement(device, adv) -> None:
        data = adv.manufacturer_data.get(APPLE_COMPANY_ID)
        if not data:
            return

        hex_data = data.hex()
        key = (device.address, hex_data)
        if key in seen:
            return
        seen.add(key)

        name = device.name or ""
        print(
            f"{time.strftime('%H:%M:%S')} "
            f"addr={device.address} name={name!r} rssi={adv.rssi} "
            f"len={len(data)} apple={hex_data}{decode_airpods(data)}"
        )

    scanner = BleakScanner(detection_callback=on_advertisement)
    await scanner.start()
    print(
        f"Scanning Apple BLE packets for {seconds}s. "
        "Open the AirPods case lid now and keep them near the laptop..."
    )
    await asyncio.sleep(seconds)
    await scanner.stop()
    print(f"Unique Apple packets: {len(seen)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=25)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    if args.label:
        print(f"=== {args.label} ===")
    asyncio.run(scan(args.seconds))


if __name__ == "__main__":
    main()
