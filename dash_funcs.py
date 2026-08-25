import time
import struct
import json
import socket
import requests
import urllib.request
from datetime import datetime


# constants for io-link hub
HUB_URL = "http://192.168.99.162"
HUB_USERNAME="washer_test_user"
HUB_PASSWORD="washer_test_password"
COLD_TEMP_ALIAS = "master1port1"
HOT_TEMP_ALIAS = "master1port2"
COLD_PRESSURE_ALIAS = "master1port3"
HOT_PRESSURE_ALIAS = "master1port4"
COLD_FLOW_SENSOR_ALIAS = "master1port5"
HOT_FLOW_SENSOR_ALIAS = "master1port6"
NEAR_AMBIENT_ALIAS = "master1port7"
FAR_AMBIENT_ALIAS = "master1port8"


# caches for slow response of ambient temp and humidity sensors
AMBIENT_TEMP_MODE_CACHE = {}
AMBIENT_ONLINE_CACHE = {}


# timestamp helper functions
def ordinal_suffix(day: int) -> str:
    """Return a day number with its English ordinal suffix."""
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

    return f"{day}{suffix}"

def make_timestamp(sample_number: int) -> dict:
    """Generate identifiers and timestamps for one logged sample."""
    now = datetime.now()

    epoch_ms = time.time_ns() // 1_000_000

    readable_time = (
        f"{now.strftime('%B')} "
        f"{ordinal_suffix(now.day)} "
        f"{now.year} at "
        f"{now.strftime('%H:%M:%S')}"
    )

    return {
        "sample_num": sample_number,
        "epoch_timestamp_ms": epoch_ms,
        "human_timestamp": readable_time,
    }


# general function for getting bytes from iolink hub
def _get_byte_array(url: str) -> list[int]:
    """GET an IO-Link value and return its byte array."""
    with urllib.request.urlopen(url, timeout=1.0) as response:
        data = json.load(response)

    if not isinstance(data, dict):
        raise TypeError(f"Expected a dictionary, got {data!r}")

    # Process-data responses are wrapped inside "iolink".
    payload = data.get("iolink", data)

    if not isinstance(payload, dict):
        raise TypeError(f"Unexpected response structure: {data!r}")

    # Some responses include a validity flag; parameter reads may not.
    if payload.get("valid") is False:
        raise RuntimeError(f"IO-Link value is marked invalid: {data!r}")

    value = payload.get("value")

    if value is None:
        raise KeyError(f"No 'value' field in hub response: {data!r}")

    if not isinstance(value, list):
        raise TypeError(
            f"Expected 'value' to be a list, got {type(value).__name__}: {value!r}"
        )

    return value


# get water temperature functions
def _get_tm311_unit(sensor_alias: str) -> str:
    """
    Read and return the TM311's configured engineering unit.

    TM311 parameter index 5121 (0x1401):
      32 = °C
      33 = °F
      35 = K
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/parameters/5121/value/?format=byteArray"
    )

    units = {
        32: "°C",
        33: "°F",
        35: "K",
    }

    try:
        unit_bytes = _get_byte_array(url)

        if len(unit_bytes) != 1:
            raise ValueError(
                f"Expected 1 TM311 unit byte, got {len(unit_bytes)}."
            )

        unit_number = unit_bytes[0]

        return units.get(unit_number, f"unknown unit ({unit_number})")

    except Exception as exc:
        print(f"Failed to read TM311 unit from {sensor_alias}: {exc}")
        return "offline"

def _get_tm311_temperature(sensor_alias: str):
    """
    Return the current TM311 temperature in its configured engineering unit.

    The first two process-data bytes are a signed, big-endian 16-bit
    temperature value with a scale factor of 0.1. The numeric value already
    follows the unit selected in TM311 parameter 5121.
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/processdata/getdata/value?format=byteArray"
    )

    try:
        process_data = _get_byte_array(url)

        if len(process_data) != 4:
            raise ValueError(
                f"Expected 4 TM311 process-data bytes, got {len(process_data)}."
            )

        raw_temperature = struct.unpack(">h", bytes(process_data[0:2]))[0]

        # Special TM311 process-data values.
        if raw_temperature == 32764:
            raise RuntimeError("TM311 reports no measurement data.")
        if raw_temperature == -32760:
            raise RuntimeError("TM311 temperature is below its measuring range.")
        if raw_temperature == 32760:
            raise RuntimeError("TM311 temperature is above its measuring range.")

        return round(raw_temperature / 10.0, 1)

    except Exception as exc:
        print(f"Failed to read TM311 temperature from {sensor_alias}: {exc}")
        return "UNKNOWN"

def get_cold_temp_value():
    return _get_tm311_temperature(COLD_TEMP_ALIAS)

def get_cold_temp_unit():
    return _get_tm311_unit(COLD_TEMP_ALIAS)

def get_hot_temp_value():
    return _get_tm311_temperature(HOT_TEMP_ALIAS)

def get_hot_temp_unit():
    return _get_tm311_unit(HOT_TEMP_ALIAS)


# get water pressure functions
def _get_ptouch_pressure_psig(sensor_alias: str):
    """
    Return pressure from a 0-100 psig MP Sensor P.Touch transmitter.

    The four-byte cyclic input begins with a signed, big-endian 16-bit
    pressure value in kPa. Convert kPa to psi using the manufacturer
    multiplier 0.29.
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/processdata/getdata/value?format=byteArray"
    )

    try:
        process_data = _get_byte_array(url)

        if len(process_data) != 4:
            raise ValueError(
                f"Expected 4 P.Touch process-data bytes, got {len(process_data)}."
            )

        raw_pressure_kpa = struct.unpack(">h", bytes(process_data[0:2]))[0]

        # Special process-data values defined by the P.Touch IO-Link interface.
        if raw_pressure_kpa == 32760:
            raise RuntimeError("P.Touch pressure is above the process-data range.")
        if raw_pressure_kpa == 32764:
            raise RuntimeError("P.Touch reports no measurement data.")

        pressure_psig = raw_pressure_kpa * 0.29

        return round(pressure_psig, 2)

    except Exception as exc:
        print(f"Failed to read P.Touch pressure from {sensor_alias}: {exc}")
        return "UNKNOWN"

def _get_ptouch_pressure_unit(sensor_alias: str) -> str:
    """
    Return PSIG after confirming that the P.Touch sensor is communicating.
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/processdata/getdata/value?format=byteArray"
    )

    try:
        process_data = _get_byte_array(url)

        if len(process_data) != 4:
            raise ValueError(
                f"Expected 4 P.Touch process-data bytes, got {len(process_data)}."
            )

        return "psig"

    except Exception as exc:
        print(f"Failed to read P.Touch unit from {sensor_alias}: {exc}")
        return "offline"

def get_cold_pres_value():
    return _get_ptouch_pressure_psig(COLD_PRESSURE_ALIAS)

def get_cold_pres_unit():
    return _get_ptouch_pressure_unit(COLD_PRESSURE_ALIAS)

def get_hot_pres_value():
    return _get_ptouch_pressure_psig(HOT_PRESSURE_ALIAS)

def get_hot_pres_unit():
    return _get_ptouch_pressure_unit(HOT_PRESSURE_ALIAS)


# get water flow functions
def _get_picomag_flow(sensor_alias: str):
    """
    Return the current Picomag volume flow rate.

    The Picomag cyclic process data contains 15 bytes. Bytes 8-11 are
    the volume-flow value encoded as a big-endian IEEE-754 float.
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/processdata/getdata/value?format=byteArray"
    )

    try:
        process_data = _get_byte_array(url)

        if len(process_data) != 15:
            raise ValueError(
                f"Expected 15 Picomag process-data bytes, got {len(process_data)}."
            )

        flow_value = struct.unpack(">f", bytes(process_data[8:12]))[0]

        return round(flow_value, 3)

    except Exception as exc:
        print(f"Failed to read Picomag flow from {sensor_alias}: {exc}")
        return "UNKNOWN"

def _get_picomag_flow_unit(sensor_alias: str) -> str:
    """
    Return the volume-flow unit selected in the Picomag configuration.
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/parameters/550/value/?format=byteArray"
    )

    units = {
        0: "L/s",
        1: "m³/h",
        2: "L/min",
        3: "gal/min",
        4: "fl oz/min",
        5: "L/h",
    }

    try:
        unit_bytes = _get_byte_array(url)

        if len(unit_bytes) != 2:
            raise ValueError(
                f"Expected 2 Picomag unit bytes, got {len(unit_bytes)}."
            )

        unit_number = int.from_bytes(unit_bytes, byteorder="big", signed=False)

        return units.get(unit_number, f"unknown unit ({unit_number})")

    except Exception as exc:
        print(f"Failed to read Picomag flow unit from {sensor_alias}: {exc}")
        return "offline"

def get_cold_flow_value():
    return _get_picomag_flow(COLD_FLOW_SENSOR_ALIAS)

def get_cold_flow_unit():
    return _get_picomag_flow_unit(COLD_FLOW_SENSOR_ALIAS)

def get_hot_flow_value():
    return _get_picomag_flow(HOT_FLOW_SENSOR_ALIAS)

def get_hot_flow_unit():
    return _get_picomag_flow_unit(HOT_FLOW_SENSOR_ALIAS)


# get ambient temperature and RH funcitons
def _get_ambient_temp_mode(sensor_alias: str) -> int:
    """
    Read and cache CSS 014 temperature mode.

    0 = Celsius
    1 = Fahrenheit
    """
    if sensor_alias in AMBIENT_TEMP_MODE_CACHE:
        return AMBIENT_TEMP_MODE_CACHE[sensor_alias]

    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/parameters/66/value/?format=byteArray"
    )

    mode_bytes = _get_byte_array(url)

    if len(mode_bytes) != 1:
        raise ValueError(
            f"Expected 1 temperature-mode byte, got {len(mode_bytes)}."
        )

    mode = mode_bytes[0]

    if mode not in (0, 1):
        raise ValueError(f"Unknown CSS 014 temperature mode: {mode}")

    AMBIENT_TEMP_MODE_CACHE[sensor_alias] = mode
    return mode

def _get_ambient_temp_rh(sensor_alias: str) -> str:
    """
    Return ambient temperature and RH as "temperature : humidity".
    Temperature is always returned in degrees Fahrenheit.
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/processdata/getdata/value?format=byteArray"
    )

    try:
        process_data = _get_byte_array(url)

        if len(process_data) != 6:
            raise ValueError(
                f"Expected 6 CSS 014 process-data bytes, "
                f"got {len(process_data)}."
            )

        raw_temperature = struct.unpack(
            ">h", bytes(process_data[0:2])
        )[0]

        raw_humidity = struct.unpack(
            ">h", bytes(process_data[3:5])
        )[0]

        temperature = raw_temperature / 10.0
        humidity = raw_humidity / 10.0

        temperature_mode = _get_ambient_temp_mode(sensor_alias)

        if temperature_mode == 0:
            temperature_f = temperature * 9.0 / 5.0 + 32.0
        else:
            temperature_f = temperature

        AMBIENT_ONLINE_CACHE[sensor_alias] = True

        return f"{temperature_f:.1f} : {humidity:.1f}"

    except Exception as exc:
        AMBIENT_ONLINE_CACHE[sensor_alias] = False
        print(
            f"Failed to read ambient temperature/RH "
            f"from {sensor_alias}: {exc}"
        )
        return "UNKNOWN"

def _get_ambient_temp_rh_unit(sensor_alias: str) -> str:
    """
    Return the ambient units without making another HTTP request.
    """
    if AMBIENT_ONLINE_CACHE.get(sensor_alias, True):
        return "°F : %RH"

    return "offline"

def get_temp_rh_near_value():
    return _get_ambient_temp_rh(NEAR_AMBIENT_ALIAS)


def get_temp_rh_near_unit():
    return _get_ambient_temp_rh_unit(NEAR_AMBIENT_ALIAS)


def get_temp_rh_far_value():
    return _get_ambient_temp_rh(FAR_AMBIENT_ALIAS)


def get_temp_rh_far_unit():
    return _get_ambient_temp_rh_unit(FAR_AMBIENT_ALIAS)


# functions to set the water temperature unit to °F
def _set_temperature_unit_fahrenheit(sensor_alias: str) -> bool:
    """
    Set one Endress+Hauser TM311 temperature sensor to degrees Fahrenheit.

    TM311 IO-Link parameter:
      index 5121 (0x1401), Unit
      32 = °C
      33 = °F
      35 = K
    """
    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/parameters/5121/value"
    )

    fahrenheit_value = 33
    payload = {
        "value": list(fahrenheit_value.to_bytes(1, byteorder="big"))
    }

    try:
        response = requests.post(
            url,
            json=payload,
            auth=(HUB_USERNAME, HUB_PASSWORD),
            timeout=5,
        )

        if not response.ok:
            print(
                f"Failed to set {sensor_alias} to °F: "
                f"HTTP {response.status_code}"
            )
            print(f"Hub response: {response.text}")
            return False

        print(f"{sensor_alias} set to °F")
        return True

    except requests.RequestException as exc:
        print(f"Request failed for {sensor_alias}: {exc}")
        return False

def set_temperature_units_fahrenheit() -> bool:
    """
    Set both TM311 water-temperature sensors to degrees Fahrenheit.

    Returns True only if both writes succeed.
    """
    cold_success = _set_temperature_unit_fahrenheit(COLD_TEMP_ALIAS)
    hot_success = _set_temperature_unit_fahrenheit(HOT_TEMP_ALIAS)

    return cold_success and hot_success


# functions to set the flow meter to gal/min
def _set_flow_unit_gpm(sensor_alias: str) -> bool:
    """Set one Picomag flow sensor to US gal/min."""

    url = (
        f"{HUB_URL}/iolink/v1/devices/{sensor_alias}"
        "/parameters/550/value"
    )

    gpm_value = 3
    payload = {
        "value": list(gpm_value.to_bytes(2, byteorder="big"))
    }

    try:
        response = requests.post(
            url,
            json=payload,
            auth=(HUB_USERNAME, HUB_PASSWORD),
            timeout=5,
        )

        if not response.ok:
            print(
                f"Failed to set {sensor_alias} to gal/min: "
                f"HTTP {response.status_code}"
            )
            print(f"Hub response: {response.text}")
            return False

        print(f"{sensor_alias} set to gal/min")
        return True

    except requests.RequestException as exc:
        print(f"Request failed for {sensor_alias}: {exc}")
        return False

def set_flow_units_gpm() -> bool:
    """
    Set both the cold and hot Picomag flow sensors to gal/min.

    Returns True only if both writes succeed.
    """

    cold_success = _set_flow_unit_gpm(COLD_FLOW_SENSOR_ALIAS)
    hot_success = _set_flow_unit_gpm(HOT_FLOW_SENSOR_ALIAS)

    return cold_success and hot_success