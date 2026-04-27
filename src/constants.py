APPLE_COMPANY_ID = 0x004C

# Apple Continuity Proximity Pairing message type
PROXIMITY_PAIRING_TYPE = 0x07
PROXIMITY_PAIRING_SUBTYPES = {0x01, 0x07}
# Minimum declared length field value we accept (25 bytes of content)
PROXIMITY_PAIRING_MIN_LENGTH = 0x19

# Byte offsets measured from payload[0] (the type byte)
OFFSET_TYPE         = 0
OFFSET_LENGTH       = 1
OFFSET_SUBTYPE      = 2   # Proximity Pairing subtype, commonly 0x01 or 0x07
OFFSET_MODEL_LO     = 3   # little-endian device model, low byte
OFFSET_MODEL_HI     = 4   # little-endian device model, high byte
OFFSET_STATUS       = 5   # bit 1 (0x02) = flip: upper nibble of BATT_PODS is left
OFFSET_BATT_PODS    = 6   # upper nibble = pod_a, lower nibble = pod_b (see flip)
OFFSET_BATT_CC      = 7   # upper nibble = case battery, lower nibble = charge flags

# Bit masks
STATUS_FLIP_BIT  = 0x02
LID_OPEN_BIT     = 0x40

# Charging flag bits in the low nibble of OFFSET_BATT_CC
CHARGING_LEFT    = 0x01
CHARGING_RIGHT   = 0x02
CHARGING_CASE    = 0x04

# Battery sentinel meaning "disconnected / not in ear"
BATTERY_DISCONNECTED = 0xF

# How long (seconds) without an advertisement before we declare AirPods gone
STALE_TIMEOUT = 10.0

# Known AirPods device model IDs
DEVICE_MODELS = {
    0x2002: "AirPods (1st gen)",
    0x200F: "AirPods (2nd gen)",
    0x2013: "AirPods (3rd gen)",
    0x2014: "AirPods Pro (2nd gen)",
    0x200E: "AirPods Pro",
    0x2061: "AirPods Pro (2nd gen)",
    0x2062: "AirPods Pro (2nd gen, USB-C)",
    0x200A: "AirPods Max",
}

# ── Connected-device filter ───────────────────────────────────────────────────
# When set, only advertisements matching ALL the following filters are
# accepted; everyone else's AirPods (and far-away ones) are ignored.
#
# REQUIRE_WINDOWS_CONNECTED_HEADPHONES
#                    only show battery while Windows has AirPods connected as
#                    an audio endpoint. This is the main "show my connected
#                    AirPods only" filter.
# ALLOWED_MODEL_IDS  set of model IDs to accept ({} or None = accept all known)
# MIN_RSSI_DBM       minimum signal strength in dBm; AirPods advertisements
#                    weaker than this are treated as "not mine".
#                    Typical RSSI values:
#                      • on your head / in your pocket   -40 to -60 dBm
#                      • across the room                 -65 to -75 dBm
#                      • through a wall / next room      < -80 dBm
#                    -60 is a good "must be on me" cutoff.

REQUIRE_WINDOWS_CONNECTED_HEADPHONES = True
WINDOWS_CONNECTED_REFRESH_SEC = 5.0

ALLOWED_MODEL_IDS = {0x200E, 0x2014, 0x2061, 0x2062}   # AirPods Pro + Pro 2 variants
MIN_RSSI_DBM      = -75

# ALLOWED_DEVICE_ADDRESSES
#   Optional strict allowlist of BLE addresses (uppercase with ':'), e.g.
#     {"AA:BB:CC:DD:EE:FF"}
#   When non-empty, packets from any other address are ignored.
#   Use debug_ble_scan.py to discover your AirPods address.
ALLOWED_DEVICE_ADDRESSES = set()

# STRICT_DEVICE_LOCK
#   AirPods often rotate BLE advertisement addresses for privacy. Keep this
#   False so a connected AirPods session can keep updating when the BLE address
#   changes.
STRICT_DEVICE_LOCK = False
