"""BLE scanning and Apple Continuity Proximity Pairing packet parsing.

Advertisement layout (Apple manufacturer data, company ID 0x004C, after bleak
strips the company ID — this is `adv.manufacturer_data[0x004C]`):

  Offset  Field
  ------  -------------------------------------------------
  0       Message type   — must be 0x07 (Proximity Pairing)
  1       Length         — declared payload length (typically 0x19)
  2       Subtype        — varies by AirPods state (paired, in-case, …)
  3–4     Device model   — little-endian uint16 (must be a known AirPods model)
  5       Status         — bit 1 (0x02)=flip,  bit 6 (0x40)=lid open
  6       Battery pods   — high nibble pod_a, low nibble pod_b
                            flip=0 → pod_a=right, pod_b=left
                            flip=1 → pod_a=left,  pod_b=right
  7       Case+charging  — high nibble = case battery
                            low nibble  = charge flags (bit0=L, bit1=R, bit2=case)

Battery nibble values 0–10 → 0–100 % (×10).  Value 0xF = disconnected → None.

Filtering strategy
──────────────────
  Many non-AirPods Apple devices (iPhones, Macs, HomePods, AirTags) also
  broadcast under company ID 0x004C with various message types.  We accept
  a packet only when:
    1) byte[0] == 0x07
    2) bytes 3–4 decode to a *known* AirPods model in DEVICE_MODELS
    3) the payload is at least _MIN_PAYLOAD_LEN bytes long

  For exclusivity you can set ALLOWED_DEVICE_ADDRESSES in constants.py.
  When set, only those addresses are accepted.
"""

import asyncio
import logging
import time
import threading
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .constants import (
    APPLE_COMPANY_ID,
    PROXIMITY_PAIRING_TYPE,
    PROXIMITY_PAIRING_SUBTYPES,
    PROXIMITY_PAIRING_MIN_LENGTH,
    OFFSET_TYPE,
    OFFSET_LENGTH,
    OFFSET_SUBTYPE,
    OFFSET_MODEL_HI,
    OFFSET_MODEL_LO,
    OFFSET_STATUS,
    OFFSET_BATT_PODS,
    OFFSET_BATT_CC,
    STATUS_FLIP_BIT,
    LID_OPEN_BIT,
    CHARGING_LEFT,
    CHARGING_RIGHT,
    CHARGING_CASE,
    BATTERY_DISCONNECTED,
    STALE_TIMEOUT,
    DEVICE_MODELS,
    ALLOWED_MODEL_IDS,
    MIN_RSSI_DBM,
    ALLOWED_DEVICE_ADDRESSES,
    STRICT_DEVICE_LOCK,
    REQUIRE_WINDOWS_CONNECTED_HEADPHONES,
    WINDOWS_CONNECTED_REFRESH_SEC,
)

log = logging.getLogger(__name__)

# Need bytes 0..7 inclusive
_MIN_PAYLOAD_LEN = 8

# When tracking the "best" device, drop the lock if a stronger signal stays
# stronger for this many consecutive samples (prevents thrashing between two
# AirPods at similar RSSI).
_LOCK_RSSI_MARGIN = 8       # dBm — new device must beat current by this much
_LOCK_TIMEOUT     = 15.0    # seconds — drop lock if no advertisement
_ALLOWED_ADDRS    = {addr.upper() for addr in ALLOWED_DEVICE_ADDRESSES}
_DEV_ADDR_RE      = re.compile(r"DEV_([0-9A-F]{12})", re.IGNORECASE)
_PID_RE           = re.compile(r"PID&([0-9A-F]{4})", re.IGNORECASE)


def _hex12_to_mac(hex12: str) -> str:
    h = hex12.upper()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


@dataclass(frozen=True)
class WindowsHeadphones:
    name: str
    model_ids: frozenset[int]
    exact_battery: Optional[int] = None


def _normalize_headphone_name(name: str) -> str:
    lower = name.lower()
    for token in ("headphones", "headset", "hands-free", "stereo"):
        lower = lower.replace(token, " ")
    return " ".join(lower.replace("(", " ").replace(")", " ").split())


def _json_rows(stdout: str) -> list[dict]:
    if not stdout.strip():
        return []
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    rows = raw if isinstance(raw, list) else [raw]
    return [row for row in rows if isinstance(row, dict)]


def _run_pnp_json(pnp_class: str) -> list[dict]:
    cmd = (
        f"Get-PnpDevice -Class {pnp_class} | "
        "Select-Object FriendlyName,InstanceId,Status | "
        "ConvertTo-Json -Compress"
    )
    startupinfo = None
    creationflags = 0
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    return _json_rows(result.stdout)


def _run_powershell_json(cmd: str, timeout: int = 8) -> list[dict]:
    startupinfo = None
    creationflags = 0
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        log.debug("PowerShell query timed out")
        return []
    if result.returncode != 0:
        return []
    return _json_rows(result.stdout)


def _query_windows_exact_batteries() -> dict[str, int]:
    """Read exact battery percentages exposed by Windows device properties.

    This works only for devices whose driver/profile publishes
    System.Devices.BatteryLife or System.Devices.BatteryLifePercent. Genuine
    AirPods usually do not expose these values to Windows without a custom
    driver, but other Bluetooth headphones may.
    """
    cmd = r"""
$rows = @()
foreach ($class in @('AudioEndpoint', 'Bluetooth')) {
  Get-PnpDevice -Class $class | Where-Object {
      $_.Status -eq 'OK' -and $_.FriendlyName -match 'AirPods|Headphone|Headset|Earbuds'
  } | ForEach-Object {
    $dev = $_
    foreach ($key in @('System.Devices.BatteryLifePercent', 'System.Devices.BatteryLife')) {
        try {
            $prop = Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName $key -ErrorAction Stop
            if ($null -ne $prop.Data -and [int]$prop.Data -ge 0 -and [int]$prop.Data -le 100) {
                $rows += [pscustomobject]@{
                    FriendlyName = $dev.FriendlyName
                    Battery = [int]$prop.Data
                }
                break
            }
        } catch {}
    }
  }
}
$rows | ConvertTo-Json -Compress
"""
    batteries: dict[str, int] = {}
    for row in _run_powershell_json(cmd, timeout=3):
        name = str(row.get("FriendlyName") or "")
        battery = row.get("Battery")
        if not name:
            continue
        try:
            pct = int(battery)
        except (TypeError, ValueError):
            continue
        if 0 <= pct <= 100:
            batteries[_normalize_headphone_name(name)] = pct
    return batteries


def _query_windows_connected_airpods() -> WindowsHeadphones | None:
    """Return connected AirPods-like audio endpoint info from Windows.

    Windows does not expose AirPods battery natively, but it does expose active
    Bluetooth audio endpoints.  We use those endpoints as the "connected to this
    PC" signal and use Bluetooth PnP product IDs to narrow BLE packets by model.
    """
    audio_names: set[str] = set()
    for row in _run_pnp_json("AudioEndpoint"):
        name = str(row.get("FriendlyName") or "")
        status = str(row.get("Status") or "")
        if status.upper() != "OK":
            continue
        if "airpods" not in name.lower():
            continue
        audio_names.add(_normalize_headphone_name(name))

    if not audio_names:
        return None

    model_ids: set[int] = set()
    display_names: set[str] = set()
    exact_batteries = _query_windows_exact_batteries()
    for row in _run_pnp_json("Bluetooth"):
        name = str(row.get("FriendlyName") or "")
        instance_id = str(row.get("InstanceId") or "")
        status = str(row.get("Status") or "")
        if status and status.upper() not in {"OK", "UNKNOWN"}:
            continue

        normalized = _normalize_headphone_name(name)
        if not any(audio_name in normalized or normalized in audio_name for audio_name in audio_names):
            continue

        display_names.add(name.replace(" Avrcp Transport", ""))
        match = _PID_RE.search(instance_id)
        if match:
            model_id = int(match.group(1), 16)
            if model_id in DEVICE_MODELS:
                model_ids.add(model_id)

    # If PID lookup fails, still allow known AirPods packets while connected.
    display_name = sorted(display_names)[0] if display_names else sorted(audio_names)[0].title()
    exact_battery = None
    for audio_name in audio_names:
        for battery_name, pct in exact_batteries.items():
            if audio_name in battery_name or battery_name in audio_name:
                exact_battery = pct
                break
        if exact_battery is not None:
            break

    return WindowsHeadphones(display_name, frozenset(model_ids), exact_battery)


def _query_windows_paired_airpods_addresses() -> set[str]:
    """Best-effort query of paired AirPods addresses from Windows Bluetooth inventory."""
    rows = _run_pnp_json("Bluetooth")

    addrs: set[str] = set()
    for row in rows:
        name = str(row.get("FriendlyName") or "")
        instance_id = str(row.get("InstanceId") or "")
        status = str(row.get("Status") or "")

        # Keep only healthy paired Apple headset entries.
        if "airpods" not in name.lower() and "VID&0001004C" not in instance_id.upper():
            continue
        if status and status.upper() not in {"OK", "UNKNOWN"}:
            continue

        match = _DEV_ADDR_RE.search(instance_id)
        if match:
            addrs.add(_hex12_to_mac(match.group(1)))

    return addrs


# ---------------------------------------------------------------------------
# Public data type
# ---------------------------------------------------------------------------

@dataclass
class AirPodsBattery:
    left_battery:   Optional[int]
    right_battery:  Optional[int]
    case_battery:   Optional[int]
    left_charging:  bool
    right_charging: bool
    case_charging:  bool
    lid_open:       bool
    model:          str
    rssi:           int
    single_battery: Optional[int] = None
    exact:          bool = False

    def any_available(self) -> bool:
        return any(
            v is not None
            for v in (
                self.single_battery,
                self.left_battery,
                self.right_battery,
                self.case_battery,
            )
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AirPodsBattery):
            return NotImplemented
        return (
            self.left_battery   == other.left_battery
            and self.single_battery == other.single_battery
            and self.right_battery  == other.right_battery
            and self.case_battery   == other.case_battery
            and self.left_charging  == other.left_charging
            and self.right_charging == other.right_charging
            and self.case_charging  == other.case_charging
            and self.lid_open       == other.lid_open
            and self.exact          == other.exact
        )

    def __hash__(self) -> int:
        return hash((
            self.single_battery, self.left_battery, self.right_battery, self.case_battery,
            self.left_charging, self.right_charging, self.case_charging,
            self.lid_open, self.exact,
        ))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _nibble_to_pct(nibble: int) -> Optional[int]:
    if nibble == BATTERY_DISCONNECTED:
        return None
    # Clamp 0..10 → 0..100 %.  Values >10 are spec violations; treat as 100 %.
    return min(nibble * 10, 100)


def _parse_advertisement(
    device: BLEDevice, adv: AdvertisementData
) -> Optional[AirPodsBattery]:
    """Parse an advertisement.  Returns None if it isn't a known AirPods packet."""
    payload: Optional[bytes] = adv.manufacturer_data.get(APPLE_COMPANY_ID)
    if payload is None:
        return None

    if len(payload) < _MIN_PAYLOAD_LEN:
        return None

    # Filter 1 — message type must be Proximity Pairing
    if payload[OFFSET_TYPE] != PROXIMITY_PAIRING_TYPE:
        return None

    # Filter 1b — declared payload format checks (reject non-battery variants)
    if payload[OFFSET_LENGTH] < PROXIMITY_PAIRING_MIN_LENGTH:
        return None
    if payload[OFFSET_SUBTYPE] not in PROXIMITY_PAIRING_SUBTYPES:
        return None

    # Filter 2 — device model must be a known AirPods.  This is the critical
    # check that prevents iPhones / Macs / AirTags from being parsed as pods.
    model_id = (payload[OFFSET_MODEL_HI] << 8) | payload[OFFSET_MODEL_LO]
    model_name = DEVICE_MODELS.get(model_id)
    if model_name is None:
        log.debug(
            "Apple proximity pairing from %s with unknown model 0x%04X — payload=%s",
            device.address, model_id, payload.hex(),
        )
        return None

    # Filter 3 — restrict to the user's specific model(s).  This blocks other
    # people's AirPods of different models from being shown as ours.
    if ALLOWED_MODEL_IDS and model_id not in ALLOWED_MODEL_IDS:
        log.debug(
            "Ignoring %s @ %s — model 0x%04X not in ALLOWED_MODEL_IDS",
            model_name, device.address, model_id,
        )
        return None

    # Filter 4 — RSSI gate.  A passing stranger's AirPods (same model as ours)
    # advertise at low RSSI; ours sit on our head at -40..-55 dBm.  This is the
    # filter that distinguishes "mine" from "someone else nearby with the same
    # model".  Weaker than MIN_RSSI_DBM = treat as not ours.
    rssi = adv.rssi if adv.rssi is not None else -99
    if rssi < MIN_RSSI_DBM:
        log.debug(
            "Ignoring %s @ %s — rssi=%d below threshold %d (not on user)",
            model_name, device.address, rssi, MIN_RSSI_DBM,
        )
        return None

    # ---- flip + lid bits ----
    subtype = payload[OFFSET_SUBTYPE]
    status = payload[OFFSET_STATUS]
    flip   = bool(status & STATUS_FLIP_BIT)
    lid_open = bool(status & LID_OPEN_BIT)

    # ---- pods battery ----
    batt_pods = payload[OFFSET_BATT_PODS]
    pod_a = (batt_pods >> 4) & 0xF
    pod_b =  batt_pods       & 0xF
    if flip:
        left_raw, right_raw = pod_a, pod_b
    else:
        left_raw, right_raw = pod_b, pod_a

    # ---- case battery + charging flags ----
    batt_cc      = payload[OFFSET_BATT_CC]
    case_raw     = (batt_cc >> 4) & 0xF
    charge_flags =  batt_cc       & 0xF

    # Subtype 0x01 packets on this machine carry a case nibble that can be very
    # stale, even when the lid is open. Avoid presenting it as the live case
    # charge unless we later add a protocol path that can prove it is current.
    case_battery = None if subtype == 0x01 else _nibble_to_pct(case_raw)

    # Subtype 0x01 packets seen from AirPods Pro on this Windows machine use a
    # low nibble of 0xF while the user reports the pods are not charging, so do
    # not surface charging state from that packet form.
    if subtype == 0x01:
        left_charging = False
        right_charging = False
        case_charging = False
    else:
        left_charging = bool(charge_flags & CHARGING_LEFT)
        right_charging = bool(charge_flags & CHARGING_RIGHT)
        case_charging = bool(charge_flags & CHARGING_CASE)

    result = AirPodsBattery(
        single_battery = None,
        left_battery   = _nibble_to_pct(left_raw),
        right_battery  = _nibble_to_pct(right_raw),
        case_battery   = case_battery,
        left_charging  = left_charging,
        right_charging = right_charging,
        case_charging  = case_charging,
        lid_open       = lid_open,
        model          = model_name,
        rssi           = rssi,
        exact          = False,
    )

    log.debug(
        "Parsed %s @ %s rssi=%d  L=%s R=%s Case=%s  flip=%d lid=%d  raw=%s",
        model_name, device.address, rssi,
        result.left_battery, result.right_battery, result.case_battery,
        int(flip), int(lid_open), payload.hex(),
    )
    return result


# ---------------------------------------------------------------------------
# Continuous scanner
# ---------------------------------------------------------------------------

class AirPodsScanner:
    """Continuous BLE scan; locks onto the closest AirPods and reports changes.

    Behaviour
    ─────────
      • If STRICT_DEVICE_LOCK=True, scanner never switches to another device
        during the same app session.
      • If STRICT_DEVICE_LOCK=False, advertisements arriving for a non-locked
        device are ignored unless their RSSI beats locked RSSI by margin.
      • The lock is released after _LOCK_TIMEOUT seconds without seeing the
        locked device, after which the next valid AirPods packet (regardless
        of RSSI) takes the lock.
      • on_update fires only when the locked device's parsed values change.
      • on_update(None) fires when the locked device goes silent for
        STALE_TIMEOUT seconds (AirPods went away or out of range).
    """

    def __init__(
        self, on_update: Callable[[Optional[AirPodsBattery]], None]
    ) -> None:
        self._callback   = on_update
        self._last:       Optional[AirPodsBattery]  = None
        self._last_seen:  float = 0.0
        self._loop:       Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._thread:     Optional[threading.Thread] = None

        # Device-locking state
        self._locked_address:   Optional[str]   = None
        self._locked_last_seen: float           = 0.0
        self._locked_rssi:      int             = -127

        self._windows_allowed_addrs: set[str] = set()
        self._windows_connected: Optional[WindowsHeadphones] = None
        self._next_windows_refresh: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="airpods-ble"
        )
        self._thread.start()
        log.info("AirPods BLE scanner started")

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=6.0)
        log.info("AirPods BLE scanner stopped")

    # ── Thread entry ──────────────────────────────────────────────────────

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._scan_loop())
        except Exception:
            log.exception("BLE event loop crashed")
        finally:
            self._loop.close()

    async def _scan_loop(self) -> None:
        self._stop_event = asyncio.Event()

        def _on_advertisement(
            device: BLEDevice, adv: AdvertisementData
        ) -> None:
            try:
                self._handle_advertisement(device, adv)
            except Exception:
                log.exception("Error handling advertisement from %s", device.address)

        log.debug("Starting BleakScanner (continuous)")
        async with BleakScanner(detection_callback=_on_advertisement):
            while not self._stop_event.is_set():
                await asyncio.sleep(1.0)
                self._tick()

    # ── Per-advertisement handler ─────────────────────────────────────────

    def _handle_advertisement(
        self, device: BLEDevice, adv: AdvertisementData
    ) -> None:
        battery = _parse_advertisement(device, adv)
        if battery is None:
            return

        addr = device.address.upper()
        rssi = battery.rssi
        now  = time.monotonic()

        self._maybe_refresh_windows_state(now)

        if REQUIRE_WINDOWS_CONNECTED_HEADPHONES:
            if self._windows_connected is None:
                return
            if self._windows_connected.exact_battery is not None:
                return
            if (
                self._windows_connected.model_ids
                and _model_name_to_id(battery.model) not in self._windows_connected.model_ids
            ):
                log.debug(
                    "Ignoring %s @ %s — not one of connected Windows model IDs %s",
                    battery.model,
                    addr,
                    sorted(self._windows_connected.model_ids),
                )
                return
            battery.model = self._windows_connected.name

        if _ALLOWED_ADDRS and addr not in _ALLOWED_ADDRS:
            return

        # ── Device locking ────────────────────────────────────────────
        # Apple devices rotate their BLE address for privacy, so the same
        # AirPods can appear under different addresses.  We match on
        # *address-of-current-advertisement* and time out the lock if it
        # goes silent.
        if self._locked_address is None:
            self._locked_address   = addr
            self._locked_rssi      = rssi
            self._locked_last_seen = now
            log.info("Locked onto AirPods at %s (rssi=%d, %s)", addr, rssi, battery.model)
        elif addr == self._locked_address:
            self._locked_rssi      = rssi
            self._locked_last_seen = now
        else:
            if STRICT_DEVICE_LOCK:
                return
            # Different device — only switch if it's noticeably stronger
            if rssi >= self._locked_rssi + _LOCK_RSSI_MARGIN:
                log.info(
                    "Switching lock %s (rssi=%d) → %s (rssi=%d, %s)",
                    self._locked_address, self._locked_rssi,
                    addr, rssi, battery.model,
                )
                self._locked_address   = addr
                self._locked_rssi      = rssi
                self._locked_last_seen = now
            else:
                # Other AirPods nearby but weaker — ignore
                return

        # We're processing a packet from the locked device.
        self._last_seen = now

        if battery != self._last:
            self._last = battery
            log.info(
                "Battery — L:%s%% R:%s%% Case:%s%%  charging:L=%s R=%s C=%s  lid=%s",
                battery.left_battery, battery.right_battery, battery.case_battery,
                battery.left_charging, battery.right_charging, battery.case_charging,
                battery.lid_open,
            )
            self._callback(battery)

    # ── Periodic housekeeping ─────────────────────────────────────────────

    def _tick(self) -> None:
        """Called once per second from the scan loop."""
        now = time.monotonic()
        self._maybe_refresh_windows_state(now)

        # Drop the lock if the chosen device has been silent too long;
        # this lets a freshly-woken pair take the lock without waiting
        # for the much longer STALE_TIMEOUT.
        if (
            self._locked_address is not None
            and now - self._locked_last_seen > _LOCK_TIMEOUT
        ):
            log.info(
                "Releasing lock on %s (silent %.1fs)",
                self._locked_address, now - self._locked_last_seen,
            )
            self._locked_address = None
            self._locked_rssi    = -127

        # Notify UI when the locked device has gone fully stale
        if self._last is not None and now - self._last_seen > STALE_TIMEOUT:
            log.info("AirPods silent for %.0fs — marking disconnected", STALE_TIMEOUT)
            self._last = None
            self._callback(None)

    def _maybe_refresh_windows_state(self, now: float) -> None:
        if now < self._next_windows_refresh:
            return
        self._next_windows_refresh = now + WINDOWS_CONNECTED_REFRESH_SEC

        try:
            connected = _query_windows_connected_airpods()
            new_addrs = _query_windows_paired_airpods_addresses()
        except Exception as exc:
            log.debug("Windows device query failed: %s", exc)
            return

        if connected != self._windows_connected:
            self._windows_connected = connected
            self._locked_address = None
            self._locked_rssi = -127
            self._last = None
            if connected is None:
                log.info("No AirPods connected as a Windows audio endpoint")
                self._callback(None)
            else:
                log.info(
                    "Windows connected AirPods: %s model_ids=%s exact_battery=%s",
                    connected.name,
                    sorted(connected.model_ids) or "unknown",
                    connected.exact_battery,
                )
                if connected.exact_battery is not None:
                    self._last_seen = now
                    self._last = AirPodsBattery(
                        single_battery=connected.exact_battery,
                        left_battery=None,
                        right_battery=None,
                        case_battery=None,
                        left_charging=False,
                        right_charging=False,
                        case_charging=False,
                        lid_open=False,
                        model=connected.name,
                        rssi=0,
                        exact=True,
                    )
                    self._callback(self._last)

        if new_addrs != self._windows_allowed_addrs:
            self._windows_allowed_addrs = new_addrs
            if new_addrs:
                log.info(
                    "Windows paired-device filter active for %d AirPods address(es): %s",
                    len(new_addrs),
                    ", ".join(sorted(new_addrs)),
                )
            else:
                log.info("Windows paired-device filter found no AirPods addresses")


def _model_name_to_id(model_name: str) -> int | None:
    for model_id, known_name in DEVICE_MODELS.items():
        if model_name == known_name:
            return model_id
    return None
