#!/usr/bin/env python3
import argparse
import fcntl
import os
import re
import select
import struct
import sys
import time
from pathlib import Path

CONSUMER_USAGE_MAP = {
    0x00: "RELEASE",
    0x00E2: "MUTE",
    0x00E9: "VOLUMEUP",
    0x00EA: "VOLUMEDOWN",
    0x00B0: "PLAY",
    0x00B1: "PAUSE",
    0x00B5: "NEXTSONG",
    0x00B6: "PREVIOUSSONG",
    0x00CD: "PLAYPAUSE",
}

OSD_FLAG_MAP = {
    0: "Init",
    1: "Stay",
    2: "Start",
    3: "Stop",
}

USAGE_TO_KEYCODE = {
    0x00B6: 165,  # KEY_PREVIOUSSONG
    0x00CD: 164,  # KEY_PLAYPAUSE
    0x00B5: 163,  # KEY_NEXTSONG
}

# Acer Swift SFG16-74 media strip (touch report 0x04) empirical zones.
# Coordinates are derived from controlled taps on PREV/PLAYPAUSE/NEXT.
MEDIA_STRIP_Y_MIN = 880
MEDIA_STRIP_Y_MAX = 1180
MEDIA_STRIP_ZONES = (
    (1000, 1510, 0x00B6, "PREVIOUSSONG"),
    (1510, 2035, 0x00CD, "PLAYPAUSE"),
    (2035, 2500, 0x00B5, "NEXTSONG"),
)

WINDOWS_MTP_COMMANDS = (
    "GET_TOUCHPAD_DEVICE_MODEL",
    "GET_MEDIA_CONTROL_STATUS",
    "SET_MEDIA_CONTROL_STATUS",
    "GET_APP_AUTO_CONTROL_MODE",
    "SET_APP_AUTO_CONTROL_MODE",
    "GET_TOUCHPAD_STATUS_IN_MEDIA_MODE",
    "SET_TOUCHPAD_STATUS_IN_MEDIA_MODE",
    "GET_LED_STATUS_IN_MEDIA_MODE",
    "SET_LED_STATUS_IN_MEDIA_MODE",
    "GET_BACKLIGHT_LUMINANCE",
    "SET_BACKLIGHT_LUMINANCE",
    "GET_YOUTUBE_BUTTON_MODE",
    "SET_YOUTUBE_BUTTON_MODE",
)

# Windows AcerSense MediaTouchPad register transport (confirmed from app.asar):
# write: [0x43, reg, qa, value]
# read : [0x43, reg, 0x10|qa, 0x00] then get feature report 0x43
MTP_REPORT_ID = 0x43
MTP_REPORT_LENGTH = 4
MTP_READ_FLAG = 0x10

MEDIA_CONFIG_REGISTERS = {
    118: "FUNCTION_KEY_CONTROL_EN",
    119: "MEDIA_CONTROL",
    120: "MEDIA_TP_FUNCTION_CONTROL",
    121: "LIGHTING_FUNCTION_EN",
    122: "BRIGHTNESS_CONTROL",
    123: "YOUTUBE_FUNCTION_CONTROL",
}

DEFAULT_REGISTER_VALUES = {
    118: (0, 1),
    119: (0, 1),
    120: (0, 1),
    121: (0, 1),
    122: (0, 50),
    123: (1, 2, 3),
}

REGISTER_PROBE_VARIANTS = (
    "feat0b-rv",
    "feat0b-vr",
    "out0b-rv",
    "out0b-vr",
    "featA0-77-rv",
    "featA0-77-vr",
    "featA0-78-rv",
    "featA0-78-vr",
)

WINDOWS_FUNCTION_TO_REGISTER = {
    "GET_APP_AUTO_CONTROL_MODE": 118,
    "SET_APP_AUTO_CONTROL_MODE": 118,
    "GET_MEDIA_CONTROL_STATUS": 119,
    "SET_MEDIA_CONTROL_STATUS": 119,
    "GET_TOUCHPAD_STATUS_IN_MEDIA_MODE": 120,
    "SET_TOUCHPAD_STATUS_IN_MEDIA_MODE": 120,
    "GET_LED_STATUS_IN_MEDIA_MODE": 121,
    "SET_LED_STATUS_IN_MEDIA_MODE": 121,
    "GET_BACKLIGHT_LUMINANCE": 122,
    "SET_BACKLIGHT_LUMINANCE": 122,
    "GET_YOUTUBE_BUTTON_MODE": 123,
    "SET_YOUTUBE_BUTTON_MODE": 123,
}


# Linux ioctl encoding helpers (asm-generic/ioctl.h)
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2

# input/uinput constants
EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0

BUS_USB = 0x03
UINPUT_IOCTL_BASE = ord("U")
UINPUT_MAX_NAME_SIZE = 80
ABS_CNT = 64


def _IOC(direction: int, ioc_type: int, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (ioc_type << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IO(ioc_type: int, nr: int) -> int:
    return _IOC(_IOC_NONE, ioc_type, nr, 0)


def _IOW(ioc_type: int, nr: int, size: int) -> int:
    return _IOC(_IOC_WRITE, ioc_type, nr, size)


def HIDIOCGFEATURE(length: int) -> int:
    return _IOC(_IOC_READ | _IOC_WRITE, ord("H"), 0x07, length)


def HIDIOCSFEATURE(length: int) -> int:
    return _IOC(_IOC_READ | _IOC_WRITE, ord("H"), 0x06, length)


def HIDIOCGINPUT(length: int) -> int:
    return _IOC(_IOC_READ | _IOC_WRITE, ord("H"), 0x0A, length)


UI_DEV_CREATE = _IO(UINPUT_IOCTL_BASE, 1)
UI_DEV_DESTROY = _IO(UINPUT_IOCTL_BASE, 2)
UI_DEV_SETUP = _IOW(UINPUT_IOCTL_BASE, 3, struct.calcsize("HHHH80sI"))
UI_SET_EVBIT = _IOW(UINPUT_IOCTL_BASE, 100, struct.calcsize("I"))
UI_SET_KEYBIT = _IOW(UINPUT_IOCTL_BASE, 101, struct.calcsize("I"))


def hex_bytes(data: bytes, limit: int = 0) -> str:
    if limit > 0:
        data = data[:limit]
    return " ".join(f"{b:02x}" for b in data)


def parse_hex_stream(text: str) -> bytes:
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if not parts:
        raise ValueError("empty byte list")
    out = bytearray()
    for p in parts:
        out.append(int(p, 16))
    return bytes(out)


def parse_a0_status(data: bytes) -> str:
    if len(data) < 6:
        return "short"
    # Observed patterns:
    # accepted: a0 00 e0 <grp> <cmd> <st1> <st2> ...
    # rejected: a0 ff ff <grp> <cmd> ff ff ...
    b1, b2, grp, cmd = data[1], data[2], data[3], data[4]
    if b1 == 0x00 and b2 == 0xE0:
        st1 = data[5] if len(data) > 5 else 0
        st2 = data[6] if len(data) > 6 else 0
        return f"accepted grp=0x{grp:02x} cmd=0x{cmd:02x} st1=0x{st1:02x} st2=0x{st2:02x}"
    if b1 == 0xFF and b2 == 0xFF:
        return f"rejected grp=0x{grp:02x} cmd=0x{cmd:02x}"
    return f"unknown b1=0x{b1:02x} b2=0x{b2:02x} grp=0x{grp:02x} cmd=0x{cmd:02x}"


def decode_touch04_report(rep: bytes) -> dict:
    if len(rep) < 29 or rep[0] != 0x04:
        raise ValueError("expected report 0x04 with length >= 29")
    reserved = rep[1]
    scan_time = rep[2] | (rep[3] << 8)
    props = rep[4]
    x = rep[5] | (rep[6] << 8)
    y = rep[7] | (rep[8] << 8)
    return {
        "report_id": rep[0],
        "reserved": reserved,
        "scan_time": scan_time,
        "contact_count": (reserved >> 4) & 0x0F,
        "button": reserved & 0x01,
        "properties": props,
        "confidence": props & 0x01,
        "tip_switch": (props >> 1) & 0x01,
        "osd_flag": (props >> 2) & 0x03,
        "contact_id": (props >> 4) & 0x0F,
        "x": x,
        "y": y,
    }


def map_media_strip_zone(x: int, y: int) -> tuple[int, str] | None:
    if y < MEDIA_STRIP_Y_MIN or y > MEDIA_STRIP_Y_MAX:
        return None
    for x_min, x_max, usage, name in MEDIA_STRIP_ZONES:
        if x_min <= x < x_max:
            return usage, name
    return None


def read_uevent(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    except FileNotFoundError:
        pass
    return out


def parse_hid_id(value: str) -> tuple[int, int] | None:
    # Format: BUS:VID:PID (hex), e.g. 0018:00001025:0000174B
    m = re.fullmatch(r"[0-9A-Fa-f]{4}:([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})", value or "")
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2), 16)


def iter_hidraw() -> list[dict]:
    nodes = []
    for p in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        uevent = read_uevent(p / "device" / "uevent")
        hid_id = parse_hid_id(uevent.get("HID_ID", ""))
        vid, pid = hid_id if hid_id else (None, None)
        nodes.append(
            {
                "hidraw": f"/dev/{p.name}",
                "driver": uevent.get("DRIVER", ""),
                "hid_name": uevent.get("HID_NAME", ""),
                "hid_id": uevent.get("HID_ID", ""),
                "vid": vid,
                "pid": pid,
            }
        )
    return nodes


def pick_default_device(override: str | None) -> str:
    if override:
        return override
    nodes = iter_hidraw()
    for n in nodes:
        if n["vid"] == 0x1025:
            return n["hidraw"]
    for n in nodes:
        if n["vid"] == 0x093A:
            return n["hidraw"]
    if nodes:
        return nodes[0]["hidraw"]
    raise RuntimeError("no hidraw devices found")


def pick_touchpad_device(override: str | None) -> str:
    if override:
        return override
    nodes = iter_hidraw()
    for n in nodes:
        if n["vid"] == 0x093A:
            return n["hidraw"]
    if nodes:
        return nodes[0]["hidraw"]
    raise RuntimeError("no hidraw devices found")


def hid_ioctl(dev: str, request: int, length: int, payload: bytes | None = None) -> bytes:
    if length <= 0:
        raise ValueError("length must be > 0")
    buf = bytearray(length)
    if payload:
        if len(payload) > length:
            raise ValueError("payload is longer than length")
        buf[: len(payload)] = payload

    with open(dev, "rb+", buffering=0) as f:
        fcntl.ioctl(f.fileno(), request, buf, True)
    return bytes(buf)


def parse_int_list_csv(value: str) -> list[int]:
    out = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        out.append(int(token, 0))
    if not out:
        raise ValueError("empty integer list")
    return out


def pad_payload(payload: bytes, length: int) -> bytes:
    if len(payload) > length:
        raise ValueError(f"payload length {len(payload)} exceeds report length {length}")
    return payload + b"\x00" * (length - len(payload))


def hid_write_output(dev: str, payload: bytes) -> int:
    with open(dev, "rb+", buffering=0) as f:
        return f.write(payload)


def read_feature_report(dev: str, report_id: int, length: int) -> bytes:
    return hid_ioctl(
        dev,
        HIDIOCGFEATURE(length),
        length,
        bytes([report_id & 0xFF]) + b"\x00" * (length - 1),
    )


def read_probe_pair(dev: str, length_a0: int, length_0b: int) -> tuple[bytes, bytes]:
    a0 = read_feature_report(dev, 0xA0, length_a0)
    r0b = read_feature_report(dev, 0x0B, length_0b)
    return a0, r0b


def build_register_probe_payload(variant: str, reg: int, val: int) -> tuple[str, bytes]:
    reg_b = reg & 0xFF
    val_b = val & 0xFF
    if variant == "feat0b-rv":
        return "feature", bytes([0x0B, 0x70, 0x41, reg_b, val_b])
    if variant == "feat0b-vr":
        return "feature", bytes([0x0B, 0x70, 0x41, val_b, reg_b])
    if variant == "out0b-rv":
        return "output", bytes([0x0B, 0x70, 0x41, reg_b, val_b])
    if variant == "out0b-vr":
        return "output", bytes([0x0B, 0x70, 0x41, val_b, reg_b])
    if variant == "featA0-77-rv":
        return "feature", bytes([0xA0, 0x00, 0xA0, 0x77, reg_b, 0x01, 0x00, val_b])
    if variant == "featA0-77-vr":
        return "feature", bytes([0xA0, 0x00, 0xA0, 0x77, val_b, 0x01, 0x00, reg_b])
    if variant == "featA0-78-rv":
        return "feature", bytes([0xA0, 0x00, 0xA0, 0x78, reg_b, 0x01, 0x00, val_b])
    if variant == "featA0-78-vr":
        return "feature", bytes([0xA0, 0x00, 0xA0, 0x78, val_b, 0x01, 0x00, reg_b])
    raise ValueError(f"unknown probe variant {variant}")


def mtp_feature_get(dev: str, length: int = MTP_REPORT_LENGTH) -> bytes:
    return read_feature_report(dev, MTP_REPORT_ID, length)


def mtp_read_register(dev: str, reg: int, qa: int = 0, length: int = MTP_REPORT_LENGTH) -> bytes:
    payload = bytes([MTP_REPORT_ID, reg & 0xFF, (MTP_READ_FLAG | (qa & 0x0F)) & 0xFF, 0x00])
    hid_ioctl(dev, HIDIOCSFEATURE(length), length, payload)
    return mtp_feature_get(dev, length)


def mtp_write_register(dev: str, reg: int, value: int, qa: int = 0, length: int = MTP_REPORT_LENGTH) -> bytes:
    payload = bytes([MTP_REPORT_ID, reg & 0xFF, qa & 0xFF, value & 0xFF])
    hid_ioctl(dev, HIDIOCSFEATURE(length), length, payload)
    return mtp_feature_get(dev, length)


def cmd_scan(_: argparse.Namespace) -> int:
    nodes = iter_hidraw()
    if not nodes:
        print("No hidraw devices found.")
        return 1

    print("hidraw devices:")
    for n in nodes:
        mark = ""
        if n["vid"] == 0x1025:
            mark = " [Acer vendor candidate]"
        elif n["vid"] == 0x093A:
            mark = " [Touchpad candidate]"
        print(
            f"- {n['hidraw']}: {n['hid_name']} | {n['hid_id']} | driver={n['driver']}{mark}"
        )
    return 0


def cmd_raw_get(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    report_id = args.report & 0xFF
    payload = bytes([report_id]) + b"\x00" * max(0, args.length - 1)
    if args.kind == "feature":
        req = HIDIOCGFEATURE(args.length)
    else:
        req = HIDIOCGINPUT(args.length)
    data = hid_ioctl(dev, req, args.length, payload)
    print(f"dev={dev} kind={args.kind} len={args.length}")
    print(hex_bytes(data))
    return 0


def cmd_raw_set(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    payload = parse_hex_stream(args.bytes)
    data = hid_ioctl(dev, HIDIOCSFEATURE(args.length), args.length, payload)
    print(f"dev={dev} kind=feature-set len={args.length}")
    print(hex_bytes(data))
    return 0


def cmd_a0_send(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    payload = parse_hex_stream(args.cmd)
    set_data = hid_ioctl(dev, HIDIOCSFEATURE(args.length), args.length, payload)
    get_a0 = hid_ioctl(
        dev,
        HIDIOCGFEATURE(args.length),
        args.length,
        bytes([0xA0]) + b"\x00" * (args.length - 1),
    )
    print(f"dev={dev} a0-send")
    print(f"set  : {hex_bytes(set_data, args.print_bytes)}")
    print(f"getA0: {hex_bytes(get_a0, args.print_bytes)}")
    print(f"status: {parse_a0_status(get_a0)}")
    return 0


def cmd_register_probe(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    regs = parse_int_list_csv(args.regs)
    for reg in regs:
        if reg not in MEDIA_CONFIG_REGISTERS:
            raise ValueError(f"unsupported register {reg}; expected one of {sorted(MEDIA_CONFIG_REGISTERS)}")

    if args.values:
        custom_values = tuple(parse_int_list_csv(args.values))
    else:
        custom_values = ()

    if args.variants:
        variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    else:
        variants = list(REGISTER_PROBE_VARIANTS)
    unknown_variants = [v for v in variants if v not in REGISTER_PROBE_VARIANTS]
    if unknown_variants:
        raise ValueError(
            f"unknown variants: {', '.join(unknown_variants)}; expected one of {', '.join(REGISTER_PROBE_VARIANTS)}"
        )

    log_path = Path(args.log) if args.log else None
    out = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        out = log_path.open("w", encoding="utf-8")

    def emit(line: str) -> None:
        print(line)
        if out:
            out.write(line + "\n")

    try:
        if args.channel_check:
            check_cmd = acer_app_status_cmd(True)
            check_set = hid_ioctl(
                dev,
                HIDIOCSFEATURE(args.length_a0),
                args.length_a0,
                pad_payload(check_cmd, args.length_a0),
            )
            check_a0 = read_feature_report(dev, 0xA0, args.length_a0)
            emit("channel-check:")
            emit(f"  set  {hex_bytes(check_set, args.print_bytes)}")
            emit(f"  getA0 {hex_bytes(check_a0, args.print_bytes)}")
            emit(f"  status {parse_a0_status(check_a0)}")

        attempt = 0
        emit(
            f"register-probe dev={dev} regs={','.join(str(r) for r in regs)} variants={','.join(variants)}"
        )
        for reg in regs:
            values = custom_values if custom_values else DEFAULT_REGISTER_VALUES[reg]
            for val in values:
                for variant in variants:
                    attempt += 1
                    transport, payload = build_register_probe_payload(variant, reg, val)
                    pre_a0, pre_0b = read_probe_pair(dev, args.length_a0, args.length_0b)
                    write_result = "-"
                    err = ""
                    try:
                        if transport == "feature":
                            length = args.length_a0 if payload[0] == 0xA0 else args.length_0b
                            set_data = hid_ioctl(
                                dev,
                                HIDIOCSFEATURE(length),
                                length,
                                pad_payload(payload, length),
                            )
                            write_result = hex_bytes(set_data, args.print_bytes)
                        else:
                            written = hid_write_output(dev, pad_payload(payload, args.length_out0b))
                            write_result = f"written={written}"
                    except OSError as e:
                        err = f"write:{e.errno}:{e.strerror}"
                    except ValueError as e:
                        err = f"write:{e}"

                    if args.delay > 0:
                        time.sleep(args.delay)

                    post_a0, post_0b = pre_a0, pre_0b
                    if not err or args.read_after_error:
                        try:
                            post_a0, post_0b = read_probe_pair(dev, args.length_a0, args.length_0b)
                        except OSError as e:
                            err = f"{err} read:{e.errno}:{e.strerror}".strip()

                    a0_changed = pre_a0[: args.print_bytes] != post_a0[: args.print_bytes]
                    r0b_changed = pre_0b[: args.print_bytes] != post_0b[: args.print_bytes]
                    changes = []
                    if a0_changed:
                        changes.append("A0")
                    if r0b_changed:
                        changes.append("0B")
                    status = parse_a0_status(post_a0)
                    reg_name = MEDIA_CONFIG_REGISTERS.get(reg, "UNKNOWN")

                    emit(
                        f"[{attempt:03d}] {variant} reg={reg}({reg_name}) val={val} "
                        f"transport={transport} changes={','.join(changes) if changes else '-'} "
                        f"status=\"{status}\" err={err or '-'}"
                    )
                    emit(f"  tx   {hex_bytes(payload)}")
                    emit(f"  set  {write_result}")
                    emit(f"  preA0 {hex_bytes(pre_a0, args.print_bytes)}")
                    emit(f"  postA0 {hex_bytes(post_a0, args.print_bytes)}")
                    emit(f"  pre0B {hex_bytes(pre_0b, args.print_bytes)}")
                    emit(f"  post0B {hex_bytes(post_0b, args.print_bytes)}")
    finally:
        if out:
            out.close()
    return 0


def cmd_mtp_read(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    reg = args.reg
    data = mtp_read_register(dev, reg, args.qa, args.length)
    name = MEDIA_CONFIG_REGISTERS.get(reg, "UNKNOWN")
    print(f"dev={dev} mtp-read reg={reg}({name}) qa=0x{args.qa:02x}")
    print(hex_bytes(data, args.print_bytes))
    return 0


def cmd_mtp_write(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    reg = args.reg
    value = args.value & 0xFF
    write_back = mtp_write_register(dev, reg, value, args.qa, args.length)
    read_back = mtp_read_register(dev, reg, args.qa, args.length)
    name = MEDIA_CONFIG_REGISTERS.get(reg, "UNKNOWN")
    print(
        f"dev={dev} mtp-write reg={reg}({name}) value=0x{value:02x} qa=0x{args.qa:02x}"
    )
    print(f"write-back: {hex_bytes(write_back, args.print_bytes)}")
    print(f"read-back : {hex_bytes(read_back, args.print_bytes)}")
    return 0


def cmd_mtp_dump(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    print(f"dev={dev} mtp-dump qa=0x{args.qa:02x}")
    for reg in sorted(MEDIA_CONFIG_REGISTERS):
        data = mtp_read_register(dev, reg, args.qa, args.length)
        value = data[3] if len(data) > 3 else -1
        print(
            f"reg={reg} ({MEDIA_CONFIG_REGISTERS[reg]}) value={value} "
            f"raw={hex_bytes(data, args.print_bytes)}"
        )
    return 0


def acer_touchpad_cmd(state: str) -> bytes:
    # Command family extracted from AcerSense strings:
    # query: a0 00 a0 04 00 01 00 00 00
    # set  : a0 00 a0 04 00 02 00 00 {00|02}
    if state == "query":
        return bytes.fromhex("a0 00 a0 04 00 01 00 00 00")
    if state == "disable":
        return bytes.fromhex("a0 00 a0 04 00 02 00 00 00")
    if state == "enable":
        return bytes.fromhex("a0 00 a0 04 00 02 00 00 02")
    raise ValueError(f"unknown state {state}")


def acer_app_status_cmd(enabled: bool) -> bytes:
    # Seen in Acer binaries:
    # 160,00,160,00,00,01,00,00
    # 160,00,160,00,00,01,00,01
    return bytes.fromhex("a0 00 a0 00 00 01 00 01" if enabled else "a0 00 a0 00 00 01 00 00")


def cmd_touchpad_action(args: argparse.Namespace, action: str) -> int:
    dev = pick_default_device(args.dev)
    cmd = acer_touchpad_cmd(action)
    set_data = hid_ioctl(dev, HIDIOCSFEATURE(args.length), args.length, cmd)
    get_a0 = hid_ioctl(
        dev,
        HIDIOCGFEATURE(args.length),
        args.length,
        bytes([0xA0]) + b"\x00" * (args.length - 1),
    )
    status = hid_ioctl(
        dev,
        HIDIOCGFEATURE(args.status_length),
        args.status_length,
        bytes([0x0B]) + b"\x00" * (args.status_length - 1),
    )

    print(f"dev={dev} action={action}")
    print(f"set  : {hex_bytes(set_data, args.print_bytes)}")
    print(f"getA0: {hex_bytes(get_a0, args.print_bytes)}")
    print(f"get0B: {hex_bytes(status, args.print_bytes)}")
    return 0


def cmd_touchpad_set(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    val = args.value & 0xFF
    cmd = bytes.fromhex("a0 00 a0 04 00 02 00 00") + bytes([val])
    set_data = hid_ioctl(dev, HIDIOCSFEATURE(args.length), args.length, cmd)
    get_a0 = hid_ioctl(
        dev,
        HIDIOCGFEATURE(args.length),
        args.length,
        bytes([0xA0]) + b"\x00" * (args.length - 1),
    )
    print(f"dev={dev} touchpad-set value=0x{val:02x}")
    print(f"set  : {hex_bytes(set_data, args.print_bytes)}")
    print(f"getA0: {hex_bytes(get_a0, args.print_bytes)}")
    return 0


def cmd_appstatus(args: argparse.Namespace, enabled: bool) -> int:
    dev = pick_default_device(args.dev)
    cmd = acer_app_status_cmd(enabled)
    set_data = hid_ioctl(dev, HIDIOCSFEATURE(args.length), args.length, cmd)
    get_a0 = hid_ioctl(
        dev,
        HIDIOCGFEATURE(args.length),
        args.length,
        bytes([0xA0]) + b"\x00" * (args.length - 1),
    )
    print(f"dev={dev} appstatus={'on' if enabled else 'off'}")
    print(f"set  : {hex_bytes(set_data, args.print_bytes)}")
    print(f"getA0: {hex_bytes(get_a0, args.print_bytes)}")
    return 0


def cmd_win_get_touchpad_status(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    reg = WINDOWS_FUNCTION_TO_REGISTER["GET_TOUCHPAD_STATUS_IN_MEDIA_MODE"]
    data = mtp_read_register(dev, reg, 0, args.mtp_length)
    value = data[3] if len(data) > 3 else -1
    print("GET_TOUCHPAD_STATUS_IN_MEDIA_MODE:")
    print(f"dev={dev} reg={reg} value={value} raw={hex_bytes(data, args.print_bytes)}")
    return 0


def cmd_win_set_touchpad_status(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    reg = WINDOWS_FUNCTION_TO_REGISTER["SET_TOUCHPAD_STATUS_IN_MEDIA_MODE"]
    write_data = mtp_write_register(dev, reg, args.status, 0, args.mtp_length)
    read_data = mtp_read_register(dev, reg, 0, args.mtp_length)
    value = read_data[3] if len(read_data) > 3 else -1
    print("SET_TOUCHPAD_STATUS_IN_MEDIA_MODE:")
    print(f"dev={dev} reg={reg} set={args.status} value={value}")
    print(f"write={hex_bytes(write_data, args.print_bytes)}")
    print(f"read ={hex_bytes(read_data, args.print_bytes)}")
    return 0


def cmd_win_set_app_auto_mode(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    reg = WINDOWS_FUNCTION_TO_REGISTER["SET_APP_AUTO_CONTROL_MODE"]
    write_data = mtp_write_register(dev, reg, args.mode, 0, args.mtp_length)
    read_data = mtp_read_register(dev, reg, 0, args.mtp_length)
    value = read_data[3] if len(read_data) > 3 else -1
    print("SET_APP_AUTO_CONTROL_MODE:")
    print(f"dev={dev} reg={reg} set={args.mode} value={value}")
    print(f"write={hex_bytes(write_data, args.print_bytes)}")
    print(f"read ={hex_bytes(read_data, args.print_bytes)}")
    return 0


def cmd_windows_mtp(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    function = args.function
    if function == "GET_TOUCHPAD_DEVICE_MODEL":
        # Windows app gates UI by Model==1. We consider model supported if report 0x43 responds.
        probe = mtp_feature_get(dev, args.mtp_length)
        model = 1 if len(probe) > 0 and probe[0] == MTP_REPORT_ID else 0
        print(f"{function}: Result=0 Data.Model={model} raw={hex_bytes(probe, args.print_bytes)}")
        return 0

    if function in (
        "GET_APP_AUTO_CONTROL_MODE",
        "GET_MEDIA_CONTROL_STATUS",
        "GET_TOUCHPAD_STATUS_IN_MEDIA_MODE",
        "GET_LED_STATUS_IN_MEDIA_MODE",
        "GET_BACKLIGHT_LUMINANCE",
        "GET_YOUTUBE_BUTTON_MODE",
    ):
        reg = WINDOWS_FUNCTION_TO_REGISTER[function]
        data = mtp_read_register(dev, reg, 0, args.mtp_length)
        value = data[3] if len(data) > 3 else -1
        field = "Luminance" if function == "GET_BACKLIGHT_LUMINANCE" else "Mode" if function == "GET_YOUTUBE_BUTTON_MODE" else "Status"
        print(f"{function}: Result=0 Data.{field}={value} reg={reg} raw={hex_bytes(data, args.print_bytes)}")
        return 0

    if function in (
        "SET_MEDIA_CONTROL_STATUS",
        "SET_TOUCHPAD_STATUS_IN_MEDIA_MODE",
        "SET_LED_STATUS_IN_MEDIA_MODE",
    ):
        if args.status is None:
            raise ValueError("--status {0,1} is required for this function")
        reg = WINDOWS_FUNCTION_TO_REGISTER[function]
        write_data = mtp_write_register(dev, reg, args.status, 0, args.mtp_length)
        read_data = mtp_read_register(dev, reg, 0, args.mtp_length)
        value = read_data[3] if len(read_data) > 3 else -1
        print(
            f"{function}: Result=0 Data.Status={value} reg={reg} "
            f"write={hex_bytes(write_data, args.print_bytes)} read={hex_bytes(read_data, args.print_bytes)}"
        )
        return 0

    if function == "SET_APP_AUTO_CONTROL_MODE":
        if args.mode is None:
            raise ValueError("--mode {0,1} is required for this function")
        reg = WINDOWS_FUNCTION_TO_REGISTER[function]
        write_data = mtp_write_register(dev, reg, args.mode, 0, args.mtp_length)
        read_data = mtp_read_register(dev, reg, 0, args.mtp_length)
        value = read_data[3] if len(read_data) > 3 else -1
        print(
            f"{function}: Result=0 Data.Status={value} reg={reg} "
            f"write={hex_bytes(write_data, args.print_bytes)} read={hex_bytes(read_data, args.print_bytes)}"
        )
        return 0

    if function == "SET_BACKLIGHT_LUMINANCE":
        if args.luminance is None:
            raise ValueError("--luminance <int> is required for this function")
        reg = WINDOWS_FUNCTION_TO_REGISTER[function]
        lum = args.luminance & 0xFF
        write_data = mtp_write_register(dev, reg, lum, 0, args.mtp_length)
        read_data = mtp_read_register(dev, reg, 0, args.mtp_length)
        value = read_data[3] if len(read_data) > 3 else -1
        print(
            f"{function}: Result=0 Data.Luminance={value} reg={reg} "
            f"write={hex_bytes(write_data, args.print_bytes)} read={hex_bytes(read_data, args.print_bytes)}"
        )
        return 0

    if function == "SET_YOUTUBE_BUTTON_MODE":
        if args.mode is None:
            raise ValueError("--mode {1,2,3} is required for this function")
        reg = WINDOWS_FUNCTION_TO_REGISTER[function]
        mode = args.mode & 0xFF
        write_data = mtp_write_register(dev, reg, mode, 0, args.mtp_length)
        read_data = mtp_read_register(dev, reg, 0, args.mtp_length)
        value = read_data[3] if len(read_data) > 3 else -1
        print(
            f"{function}: Result=0 Data.Mode={value} reg={reg} "
            f"write={hex_bytes(write_data, args.print_bytes)} read={hex_bytes(read_data, args.print_bytes)}"
        )
        return 0

    raise ValueError(f"unsupported function {function}")


def cmd_monitor(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    start = time.monotonic()
    prev = {}
    while time.monotonic() - start < args.seconds:
        rows = []
        for rid, length in ((0xA0, args.length_a0), (0x0B, args.length_0b)):
            data = hid_ioctl(
                dev,
                HIDIOCGFEATURE(length),
                length,
                bytes([rid]) + b"\x00" * (length - 1),
            )
            short = data[: args.print_bytes]
            if prev.get(rid) != short:
                prev[rid] = short
                rows.append(f"rid=0x{rid:02x} {hex_bytes(short)}")
        if rows:
            print(time.strftime("%H:%M:%S"), "|", " | ".join(rows))
        time.sleep(args.interval)
    return 0


def guess_event_devices() -> list[str]:
    # On this host: event8 (Acer keyboard), event11 (wireless radio)
    out = []
    for dev in sorted(Path("/dev/input").glob("event*")):
        name_path = Path("/sys/class/input") / dev.name / "device" / "name"
        try:
            name = name_path.read_text().strip()
        except FileNotFoundError:
            continue
        if "1025174B" in name or "PIXA480A" in name or "Intel HID" in name:
            out.append(str(dev))
    return out


def cmd_watch_events(args: argparse.Namespace) -> int:
    devices = args.devices or guess_event_devices()
    if not devices:
        print("No candidate event devices found. Pass explicit paths with --devices.")
        return 1

    fds = []
    for d in devices:
        try:
            fd = os.open(d, os.O_RDONLY | os.O_NONBLOCK)
            fds.append((d, fd))
        except OSError as e:
            print(f"skip {d}: {e}")

    if not fds:
        print("No readable input devices.")
        return 1

    by_fd = {fd: path for path, fd in fds}
    print("Watching:", ", ".join(path for path, _ in fds))

    fmt = "llHHI"
    event_size = struct.calcsize(fmt)
    end = time.monotonic() + args.seconds

    try:
        while time.monotonic() < end:
            ready, _, _ = select.select(list(by_fd.keys()), [], [], 0.25)
            for fd in ready:
                while True:
                    try:
                        raw = os.read(fd, event_size)
                    except BlockingIOError:
                        break
                    if len(raw) < event_size:
                        break
                    sec, usec, ev_type, code, value = struct.unpack(fmt, raw)
                    if ev_type in (0x01, 0x04) or args.all:
                        print(
                            f"{by_fd[fd]} {sec}.{usec:06d} type=0x{ev_type:02x} "
                            f"code=0x{code:03x} value={value}"
                        )
    finally:
        for _, fd in fds:
            os.close(fd)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    devices = args.devices or guess_event_devices()
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fds = []
    for d in devices:
        try:
            fd = os.open(d, os.O_RDONLY | os.O_NONBLOCK)
            fds.append((d, fd))
        except OSError:
            continue
    by_fd = {fd: path for path, fd in fds}

    prev = {}
    start = time.monotonic()
    end = start + args.seconds

    with log_path.open("w", encoding="utf-8") as out:
        out.write(f"# probe start dev={dev}\n")
        out.write(f"# event devices: {', '.join(path for path, _ in fds) or 'none'}\n")

        fmt = "llHHI"
        event_size = struct.calcsize(fmt)

        try:
            while time.monotonic() < end:
                now = time.time()

                # Poll feature reports.
                for rid, length in ((0xA0, args.length_a0), (0x0B, args.length_0b)):
                    data = hid_ioctl(
                        dev,
                        HIDIOCGFEATURE(length),
                        length,
                        bytes([rid]) + b"\x00" * (length - 1),
                    )
                    short = data[: args.print_bytes]
                    if prev.get(rid) != short:
                        prev[rid] = short
                        out.write(f"{now:.6f} feature rid=0x{rid:02x} {hex_bytes(short)}\n")

                # Poll input events.
                if by_fd:
                    ready, _, _ = select.select(list(by_fd.keys()), [], [], args.interval)
                    for fd in ready:
                        while True:
                            try:
                                raw = os.read(fd, event_size)
                            except BlockingIOError:
                                break
                            if len(raw) < event_size:
                                break
                            sec, usec, ev_type, code, value = struct.unpack(fmt, raw)
                            if ev_type in (0x01, 0x04) or args.all:
                                out.write(
                                    f"{sec}.{usec:06d} ev {by_fd[fd]} "
                                    f"type=0x{ev_type:02x} code=0x{code:03x} value={value}\n"
                                )
                else:
                    time.sleep(args.interval)
        finally:
            for _, fd in fds:
                os.close(fd)

    print(f"Probe complete: {log_path}")
    return 0


def cmd_sniff_hidraw(args: argparse.Namespace) -> int:
    dev = pick_default_device(args.dev)
    end = time.monotonic() + args.seconds
    log_path = Path(args.log) if args.log else None
    out = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        out = log_path.open("w", encoding="utf-8")
        out.write(f"# sniff start dev={dev}\n")

    def emit(line: str) -> None:
        print(line)
        if out:
            out.write(line + "\n")

    try:
        fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        if out:
            out.close()
        raise e

    prev = None
    count = 0
    try:
        while time.monotonic() < end:
            ready, _, _ = select.select([fd], [], [], 0.25)
            if not ready:
                continue
            try:
                data = os.read(fd, args.read_size)
            except BlockingIOError:
                continue
            if not data:
                continue
            short = data[: args.print_bytes]
            if args.only_changes and short == prev:
                continue
            prev = short
            count += 1
            emit(f"{time.time():.6f} len={len(data)} {hex_bytes(short)}")
    finally:
        os.close(fd)
        if out:
            out.write(f"# sniff end packets={count}\n")
            out.close()

    return 0


def cmd_watch_consumer(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    end = time.monotonic() + args.seconds if args.seconds > 0 else None

    log_path = Path(args.log) if args.log else None
    out = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        out = log_path.open("w", encoding="utf-8")
        out.write(f"# consumer watch start dev={dev}\n")

    def emit(line: str) -> None:
        print(line)
        if out:
            out.write(line + "\n")

    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    prev_touch_state = 0
    try:
        while True:
            if end is not None and time.monotonic() >= end:
                break
            ready, _, _ = select.select([fd], [], [], 0.25)
            if not ready:
                continue
            try:
                data = os.read(fd, args.read_size)
            except BlockingIOError:
                continue
            if not data:
                continue

            # i2c-hid may return one report or a coalesced chunk.
            # This device currently emits:
            # - report 0x04, 29 bytes (touch stream)
            # - report 0x0c, 3 bytes  (consumer/media key)
            i = 0
            n = len(data)
            while i < n:
                rid = data[i]
                if rid == 0x0C:
                    if i + 3 > n:
                        break
                    usage = data[i + 1] | (data[i + 2] << 8)
                    if not (args.only_press and usage == 0):
                        name = CONSUMER_USAGE_MAP.get(usage, "UNKNOWN")
                        emit(
                            f"{time.time():.6f} usage=0x{usage:04x} "
                            f"name={name} raw={hex_bytes(data[i:i+3])}"
                        )
                    i += 3
                elif rid == 0x04:
                    if i + 29 > n:
                        break
                    rep = data[i : i + 29]
                    touch_state = rep[4]
                    x = rep[5] | (rep[6] << 8)
                    y = rep[7] | (rep[8] << 8)
                    # On this model Linux does not expose report 0x0c media keys.
                    # Media strip taps arrive as report 0x04 coordinates.
                    if touch_state == 0x03 and prev_touch_state != 0x03:
                        mapped = map_media_strip_zone(x, y)
                        if mapped:
                            usage, name = mapped
                            emit(
                                f"{time.time():.6f} usage=0x{usage:04x} name={name} "
                                f"source=touch-04 x={x} y={y} raw={hex_bytes(rep)}"
                            )
                    prev_touch_state = touch_state
                    i += 29
                else:
                    # Unknown alignment in this chunk: stop to avoid false positives.
                    break
    finally:
        os.close(fd)
        if out:
            out.write("# consumer watch end\n")
            out.close()
    return 0


def cmd_watch_media_runtime(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    end = time.monotonic() + args.seconds if args.seconds > 0 else None

    log_path = Path(args.log) if args.log else None
    out = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        out = log_path.open("w", encoding="utf-8")
        out.write(f"# media-runtime watch start dev={dev}\n")

    def emit(line: str) -> None:
        print(line)
        if out:
            out.write(line + "\n")

    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    last_osd_flag = None
    mode_active = None
    touch_session = None

    def start_session(ts: float, info: dict) -> dict:
        return {
            "t0": ts,
            "t1": ts,
            "count": 1,
            "x_min": info["x"],
            "x_max": info["x"],
            "y_min": info["y"],
            "y_max": info["y"],
            "x_last": info["x"],
            "y_last": info["y"],
            "only_osd0": info["osd_flag"] == 0,
            "saw_osd_enable": info["osd_flag"] in (1, 2),
        }

    def update_session(sess: dict, ts: float, info: dict) -> None:
        sess["t1"] = ts
        sess["count"] += 1
        sess["x_min"] = min(sess["x_min"], info["x"])
        sess["x_max"] = max(sess["x_max"], info["x"])
        sess["y_min"] = min(sess["y_min"], info["y"])
        sess["y_max"] = max(sess["y_max"], info["y"])
        sess["x_last"] = info["x"]
        sess["y_last"] = info["y"]
        if info["osd_flag"] != 0:
            sess["only_osd0"] = False
        if info["osd_flag"] in (1, 2):
            sess["saw_osd_enable"] = True

    def maybe_finish_session(sess: dict, release_info: dict) -> tuple[bool | None, str | None]:
        if sess is None:
            return mode_active, None
        if sess["saw_osd_enable"]:
            return True, "osd-enable"

        dx = sess["x_max"] - sess["x_min"]
        dy = sess["y_max"] - sess["y_min"]
        in_off_zone = sess["x_last"] >= args.off_x_min and sess["y_last"] >= args.off_y_min
        off_signature = (
            mode_active is True
            and sess["only_osd0"]
            and sess["count"] >= args.off_min_frames
            and dx <= args.off_stationary_max
            and dy <= args.off_stationary_max
            and in_off_zone
            and release_info["properties"] == 0x00
        )
        if off_signature:
            return False, "tap-off-signature"
        return mode_active, None

    try:
        while True:
            if end is not None and time.monotonic() >= end:
                break
            ready, _, _ = select.select([fd], [], [], 0.25)
            if not ready:
                continue
            try:
                data = os.read(fd, args.read_size)
            except BlockingIOError:
                continue
            if not data:
                continue

            i = 0
            n = len(data)
            while i < n:
                rid = data[i]
                if rid == 0x04:
                    if i + 29 > n:
                        break
                    rep = data[i : i + 29]
                    info = decode_touch04_report(rep)
                    now = time.time()
                    osd_flag = info["osd_flag"]

                    if osd_flag in (1, 2):
                        new_mode_active = True
                    elif osd_flag == 3:
                        new_mode_active = False
                    else:
                        new_mode_active = mode_active

                    tip = info["tip_switch"]
                    reason = None
                    if tip == 1:
                        if touch_session is None:
                            touch_session = start_session(now, info)
                        else:
                            update_session(touch_session, now, info)
                    else:
                        if touch_session is not None:
                            update_session(touch_session, now, info)
                            session_mode, reason = maybe_finish_session(touch_session, info)
                            new_mode_active = session_mode
                        touch_session = None

                    mode_changed = new_mode_active != mode_active and new_mode_active is not None
                    osd_changed = osd_flag != last_osd_flag

                    if args.all or osd_changed or mode_changed:
                        flag_name = OSD_FLAG_MAP.get(osd_flag, "Unknown")
                        mode_text = (
                            "unknown"
                            if new_mode_active is None
                            else ("active" if new_mode_active else "inactive")
                        )
                        tail = f" reason={reason}" if reason and mode_changed else ""
                        emit(
                            f"{now:.6f} mode={mode_text} osd={osd_flag}({flag_name}) "
                            f"props=0x{info['properties']:02x} tip={info['tip_switch']} "
                            f"cnt={info['contact_count']} id={info['contact_id']} "
                            f"x={info['x']} y={info['y']}{tail} raw={hex_bytes(rep)}"
                        )
                    last_osd_flag = osd_flag
                    mode_active = new_mode_active
                    i += 29
                elif rid == 0x0C:
                    if i + 3 > n:
                        break
                    i += 3
                else:
                    break
    finally:
        os.close(fd)
        if out:
            out.write("# media-runtime watch end\n")
            out.close()
    return 0


class UinputMediaKeys:
    def __init__(self, dev: str = "/dev/uinput", name: str = "swift-touch-media") -> None:
        self.dev_path = dev
        self.name = name
        self.fd = None

    @staticmethod
    def _event_pack(ev_type: int, code: int, value: int) -> bytes:
        sec = int(time.time())
        usec = int((time.time() - sec) * 1_000_000)
        return struct.pack("llHHi", sec, usec, ev_type, code, value)

    def open(self) -> None:
        fd = os.open(self.dev_path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            for keycode in sorted(set(USAGE_TO_KEYCODE.values())):
                fcntl.ioctl(fd, UI_SET_KEYBIT, keycode)

            name = self.name.encode("utf-8")[: UINPUT_MAX_NAME_SIZE - 1].ljust(UINPUT_MAX_NAME_SIZE, b"\x00")
            try:
                setup = struct.pack("HHHH80sI", BUS_USB, 0x1025, 0x480A, 1, name, 0)
                fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            except OSError:
                # Legacy fallback for kernels/drivers that reject UI_DEV_SETUP.
                zeros = [0] * ABS_CNT
                uidev = struct.pack(
                    f"{UINPUT_MAX_NAME_SIZE}sHHHHi{ABS_CNT}i{ABS_CNT}i{ABS_CNT}i{ABS_CNT}i",
                    name,
                    BUS_USB,
                    0x1025,
                    0x480A,
                    1,
                    0,
                    *zeros,
                    *zeros,
                    *zeros,
                    *zeros,
                )
                os.write(fd, uidev)
            fcntl.ioctl(fd, UI_DEV_CREATE)
            self.fd = fd
        except Exception:
            os.close(fd)
            raise

    def emit_key(self, keycode: int) -> None:
        if self.fd is None:
            raise RuntimeError("uinput device is not open")
        os.write(self.fd, self._event_pack(EV_KEY, keycode, 1))
        os.write(self.fd, self._event_pack(EV_SYN, SYN_REPORT, 0))
        os.write(self.fd, self._event_pack(EV_KEY, keycode, 0))
        os.write(self.fd, self._event_pack(EV_SYN, SYN_REPORT, 0))

    def close(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)
            self.fd = None


def cmd_run_media_keys(args: argparse.Namespace) -> int:
    dev = pick_touchpad_device(args.dev)
    end = time.monotonic() + args.seconds if args.seconds > 0 else None

    log_path = Path(args.log) if args.log else None
    out = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        out = log_path.open("w", encoding="utf-8")
        out.write(f"# media-keys start dev={dev}\n")

    def emit(line: str) -> None:
        print(line)
        if out:
            out.write(line + "\n")

    sender = None
    if args.emit == "uinput":
        sender = UinputMediaKeys(args.uinput_dev, args.uinput_name)
        sender.open()
        emit(f"sender=uinput dev={args.uinput_dev} name={args.uinput_name}")
    else:
        emit("sender=print")

    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    last_tip = 0
    mode_active = None
    touch_session = None

    def start_session(ts: float, info: dict) -> dict:
        return {
            "t0": ts,
            "t1": ts,
            "count": 1,
            "x_min": info["x"],
            "x_max": info["x"],
            "y_min": info["y"],
            "y_max": info["y"],
            "x_last": info["x"],
            "y_last": info["y"],
            "only_osd0": info["osd_flag"] == 0,
            "saw_osd_enable": info["osd_flag"] in (1, 2),
        }

    def update_session(sess: dict, ts: float, info: dict) -> None:
        sess["t1"] = ts
        sess["count"] += 1
        sess["x_min"] = min(sess["x_min"], info["x"])
        sess["x_max"] = max(sess["x_max"], info["x"])
        sess["y_min"] = min(sess["y_min"], info["y"])
        sess["y_max"] = max(sess["y_max"], info["y"])
        sess["x_last"] = info["x"]
        sess["y_last"] = info["y"]
        if info["osd_flag"] != 0:
            sess["only_osd0"] = False
        if info["osd_flag"] in (1, 2):
            sess["saw_osd_enable"] = True

    def finalize_mode(sess: dict, release_info: dict, current_mode: bool | None) -> tuple[bool | None, str | None]:
        if sess is None:
            return current_mode, None
        if sess["saw_osd_enable"]:
            return True, "osd-enable"
        dx = sess["x_max"] - sess["x_min"]
        dy = sess["y_max"] - sess["y_min"]
        in_off_zone = sess["x_last"] >= args.off_x_min and sess["y_last"] >= args.off_y_min
        off_signature = (
            current_mode is True
            and sess["only_osd0"]
            and sess["count"] >= args.off_min_frames
            and dx <= args.off_stationary_max
            and dy <= args.off_stationary_max
            and in_off_zone
            and release_info["properties"] == 0x00
        )
        if off_signature:
            return False, "tap-off-signature"
        return current_mode, None

    try:
        while True:
            if end is not None and time.monotonic() >= end:
                break
            ready, _, _ = select.select([fd], [], [], 0.25)
            if not ready:
                continue
            try:
                data = os.read(fd, args.read_size)
            except BlockingIOError:
                continue
            if not data:
                continue

            i = 0
            n = len(data)
            while i < n:
                rid = data[i]
                if rid == 0x04:
                    if i + 29 > n:
                        break
                    rep = data[i : i + 29]
                    info = decode_touch04_report(rep)
                    now = time.time()

                    osd_flag = info["osd_flag"]
                    new_mode_active = mode_active
                    reason = None
                    if osd_flag in (1, 2):
                        new_mode_active = True
                    elif osd_flag == 3:
                        new_mode_active = False

                    tip = info["tip_switch"]
                    if tip == 1:
                        if touch_session is None:
                            touch_session = start_session(now, info)
                        else:
                            update_session(touch_session, now, info)
                    else:
                        if touch_session is not None:
                            update_session(touch_session, now, info)
                            new_mode_active, reason = finalize_mode(touch_session, info, new_mode_active)
                        touch_session = None

                    if new_mode_active != mode_active and new_mode_active is not None:
                        state = "active" if new_mode_active else "inactive"
                        emit(f"{now:.6f} mode={state} reason={reason or OSD_FLAG_MAP.get(osd_flag, 'unknown')}")
                    mode_active = new_mode_active

                    rising_edge = tip == 1 and last_tip == 0
                    if rising_edge:
                        mapped = map_media_strip_zone(info["x"], info["y"])
                        if mapped:
                            usage, name = mapped
                            allowed = (not args.require_active) or (mode_active is True)
                            if allowed:
                                keycode = USAGE_TO_KEYCODE.get(usage)
                                if args.emit == "uinput":
                                    if keycode is None:
                                        emit(f"{now:.6f} skip usage=0x{usage:04x} name={name} (no keycode)")
                                    else:
                                        sender.emit_key(keycode)
                                        emit(
                                            f"{now:.6f} key name={name} usage=0x{usage:04x} "
                                            f"keycode={keycode} mode={'active' if mode_active else 'inactive'}"
                                        )
                                else:
                                    emit(
                                        f"{now:.6f} key name={name} usage=0x{usage:04x} "
                                        f"mode={'active' if mode_active else 'inactive'}"
                                    )
                            elif args.verbose_skips:
                                emit(
                                    f"{now:.6f} skip name={name} usage=0x{usage:04x} "
                                    f"mode={'active' if mode_active else 'inactive'}"
                                )

                    last_tip = tip
                    i += 29
                elif rid == 0x0C:
                    if i + 3 > n:
                        break
                    i += 3
                else:
                    break
    finally:
        os.close(fd)
        if sender is not None:
            sender.close()
        if out:
            out.write("# media-keys end\n")
            out.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Acer Swift touch/media HID toolkit for Linux/KDE (reverse + control)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="list hidraw devices and candidates")
    ps.set_defaults(func=cmd_scan)

    pg = sub.add_parser("raw-get", help="read raw feature/input report")
    pg.add_argument("--dev", help="hidraw node (default auto)")
    pg.add_argument("--kind", choices=["feature", "input"], default="feature")
    pg.add_argument("--report", type=lambda x: int(x, 0), required=True, help="report id")
    pg.add_argument("--length", type=int, required=True, help="report length")
    pg.set_defaults(func=cmd_raw_get)

    pw = sub.add_parser("raw-set", help="write raw feature report")
    pw.add_argument("--dev", help="hidraw node (default auto)")
    pw.add_argument("--length", type=int, required=True, help="report length")
    pw.add_argument(
        "--bytes",
        required=True,
        help='hex bytes, e.g. "a0 00 a0 04 00 02 00 00 02"',
    )
    pw.set_defaults(func=cmd_raw_set)

    pa0 = sub.add_parser("a0-send", help="send A0 command and decode A0 status response")
    pa0.add_argument("--dev", help="hidraw node (default auto)")
    pa0.add_argument("--length", type=int, default=65, help="A0 report length")
    pa0.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    pa0.add_argument("--cmd", required=True, help='hex command, e.g. "a0 00 a0 00 00 01 00 01"')
    pa0.set_defaults(func=cmd_a0_send)

    pmr = sub.add_parser("mtp-read", help="read MediaTouchPad register via report 0x43")
    pmr.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    pmr.add_argument("--reg", type=lambda x: int(x, 0), required=True, help="register id")
    pmr.add_argument("--qa", type=lambda x: int(x, 0), default=0, help="second arg (Qa enum)")
    pmr.add_argument("--length", type=int, default=4, help="feature report length (default 4)")
    pmr.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    pmr.set_defaults(func=cmd_mtp_read)

    pmw = sub.add_parser("mtp-write", help="write MediaTouchPad register via report 0x43")
    pmw.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    pmw.add_argument("--reg", type=lambda x: int(x, 0), required=True, help="register id")
    pmw.add_argument("--value", type=lambda x: int(x, 0), required=True, help="value byte")
    pmw.add_argument("--qa", type=lambda x: int(x, 0), default=0, help="second arg (Qa enum)")
    pmw.add_argument("--length", type=int, default=4, help="feature report length (default 4)")
    pmw.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    pmw.set_defaults(func=cmd_mtp_write)

    pmd = sub.add_parser("mtp-dump", help="dump known MediaTouchPad registers 118..123")
    pmd.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    pmd.add_argument("--qa", type=lambda x: int(x, 0), default=0, help="second arg (Qa enum)")
    pmd.add_argument("--length", type=int, default=4, help="feature report length (default 4)")
    pmd.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    pmd.set_defaults(func=cmd_mtp_dump)

    pr = sub.add_parser(
        "register-probe",
        help="probe candidate register write formats for Acer media config IDs 118..123",
    )
    pr.add_argument("--dev", default="/dev/hidraw1", help="hidraw node (default /dev/hidraw1)")
    pr.add_argument("--regs", default="118,119,120,121,122,123", help="comma list of register IDs")
    pr.add_argument(
        "--values",
        help=(
            "override values for all selected regs, e.g. \"0,1\"; "
            "if omitted, per-register defaults are used"
        ),
    )
    pr.add_argument(
        "--variants",
        default=",".join(REGISTER_PROBE_VARIANTS),
        help=f"comma list of probe variants (default: {','.join(REGISTER_PROBE_VARIANTS)})",
    )
    pr.add_argument("--length-a0", type=int, default=65, help="A0 feature report length")
    pr.add_argument("--length-0b", type=int, default=64, help="0B feature report length")
    pr.add_argument("--length-out0b", type=int, default=42, help="0B output report length")
    pr.add_argument("--print-bytes", type=int, default=16, help="bytes to print per report")
    pr.add_argument("--delay", type=float, default=0.05, help="delay between write and readback")
    pr.add_argument(
        "--read-after-error",
        action="store_true",
        help="still attempt readback when write call fails",
    )
    pr.add_argument(
        "--channel-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="run A0 appstatus pre-check (default: enabled)",
    )
    pr.add_argument("--log", default="/tmp/swift-touch-register-probe.log", help="output log path")
    pr.set_defaults(func=cmd_register_probe)

    for name, action in (
        ("touchpad-query", "query"),
        ("touchpad-enable", "enable"),
        ("touchpad-disable", "disable"),
    ):
        pt = sub.add_parser(name, help=f"Acer touchpad action: {action}")
        pt.add_argument("--dev", help="hidraw node (default auto)")
        pt.add_argument("--length", type=int, default=65, help="A0 report length")
        pt.add_argument("--status-length", type=int, default=64, help="0B report length")
        pt.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
        pt.set_defaults(func=lambda a, act=action: cmd_touchpad_action(a, act))

    ps = sub.add_parser("touchpad-set", help="set raw touchpad mode value in A0...04...02...VV")
    ps.add_argument("--dev", help="hidraw node (default auto)")
    ps.add_argument("--value", type=lambda x: int(x, 0), required=True, help="mode value byte")
    ps.add_argument("--length", type=int, default=65, help="A0 report length")
    ps.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    ps.set_defaults(func=cmd_touchpad_set)

    pa_on = sub.add_parser("appstatus-on", help="send Acer SetAppStatus ON command")
    pa_on.add_argument("--dev", help="hidraw node (default auto)")
    pa_on.add_argument("--length", type=int, default=65, help="A0 report length")
    pa_on.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    pa_on.set_defaults(func=lambda a: cmd_appstatus(a, True))

    pa_off = sub.add_parser("appstatus-off", help="send Acer SetAppStatus OFF command")
    pa_off.add_argument("--dev", help="hidraw node (default auto)")
    pa_off.add_argument("--length", type=int, default=65, help="A0 report length")
    pa_off.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    pa_off.set_defaults(func=lambda a: cmd_appstatus(a, False))

    p_get_tpm = sub.add_parser(
        "get-touchpad-status-in-media-mode",
        help="Windows-compatible GET_TOUCHPAD_STATUS_IN_MEDIA_MODE alias",
    )
    p_get_tpm.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    p_get_tpm.add_argument("--mtp-length", type=int, default=4, help="MTP report length (default 4)")
    p_get_tpm.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    p_get_tpm.set_defaults(func=cmd_win_get_touchpad_status)

    p_set_tpm = sub.add_parser(
        "set-touchpad-status-in-media-mode",
        help="Windows-compatible SET_TOUCHPAD_STATUS_IN_MEDIA_MODE alias",
    )
    p_set_tpm.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    p_set_tpm.add_argument(
        "--status",
        type=int,
        choices=[0, 1],
        required=True,
        help="0 = disable media touchpad mode, 1 = enable media touchpad mode",
    )
    p_set_tpm.add_argument("--mtp-length", type=int, default=4, help="MTP report length (default 4)")
    p_set_tpm.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    p_set_tpm.set_defaults(func=cmd_win_set_touchpad_status)

    p_set_auto = sub.add_parser(
        "set-app-auto-control-mode",
        help="Windows-compatible SET_APP_AUTO_CONTROL_MODE alias",
    )
    p_set_auto.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    p_set_auto.add_argument(
        "--mode",
        type=int,
        choices=[0, 1],
        required=True,
        help="0 = app auto mode OFF, 1 = app auto mode ON",
    )
    p_set_auto.add_argument("--mtp-length", type=int, default=4, help="MTP report length (default 4)")
    p_set_auto.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    p_set_auto.set_defaults(func=cmd_win_set_app_auto_mode)

    p_win = sub.add_parser(
        "windows-mtp",
        help="run a recovered Windows MediaTouchPad Function name against Linux mappings",
    )
    p_win.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    p_win.add_argument("--function", choices=WINDOWS_MTP_COMMANDS, required=True)
    p_win.add_argument("--status", type=int, choices=[0, 1], help="SET_* status parameter")
    p_win.add_argument(
        "--mode",
        type=int,
        help="SET_APP_AUTO_CONTROL_MODE mode (0/1) or SET_YOUTUBE_BUTTON_MODE mode (1..3)",
    )
    p_win.add_argument("--luminance", type=int, help="SET_BACKLIGHT_LUMINANCE value")
    p_win.add_argument("--mtp-length", type=int, default=4, help="MTP report length (default 4)")
    p_win.add_argument("--print-bytes", type=int, default=16, help="bytes to print")
    p_win.set_defaults(func=cmd_windows_mtp)

    pm = sub.add_parser("monitor", help="poll and print feature-report changes")
    pm.add_argument("--dev", help="hidraw node (default auto)")
    pm.add_argument("--seconds", type=int, default=30)
    pm.add_argument("--interval", type=float, default=0.15)
    pm.add_argument("--length-a0", type=int, default=65)
    pm.add_argument("--length-0b", type=int, default=64)
    pm.add_argument("--print-bytes", type=int, default=16)
    pm.set_defaults(func=cmd_monitor)

    pe = sub.add_parser("watch-events", help="watch input event devices")
    pe.add_argument("--seconds", type=int, default=30)
    pe.add_argument("--devices", nargs="*", help="explicit /dev/input/eventX list")
    pe.add_argument("--all", action="store_true", help="print all event types")
    pe.set_defaults(func=cmd_watch_events)

    pp = sub.add_parser("probe", help="capture feature and evdev data into one log file")
    pp.add_argument("--dev", help="hidraw node (default auto)")
    pp.add_argument("--seconds", type=int, default=30)
    pp.add_argument("--interval", type=float, default=0.15)
    pp.add_argument("--length-a0", type=int, default=65)
    pp.add_argument("--length-0b", type=int, default=64)
    pp.add_argument("--print-bytes", type=int, default=16)
    pp.add_argument("--devices", nargs="*", help="explicit /dev/input/eventX list")
    pp.add_argument("--all", action="store_true", help="log all event types")
    pp.add_argument("--log", default="/tmp/swift-touch-probe.log", help="output log path")
    pp.set_defaults(func=cmd_probe)

    ph = sub.add_parser("sniff-hidraw", help="log raw hidraw input reports with timestamps")
    ph.add_argument("--dev", help="hidraw node (default auto)")
    ph.add_argument("--seconds", type=int, default=30)
    ph.add_argument("--read-size", type=int, default=512)
    ph.add_argument("--print-bytes", type=int, default=32)
    ph.add_argument("--only-changes", action="store_true", help="print only changed packet prefixes")
    ph.add_argument("--log", help="optional log path")
    ph.set_defaults(func=cmd_sniff_hidraw)

    pc = sub.add_parser("watch-consumer", help="watch decoded consumer (report 0x0c) events")
    pc.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    pc.add_argument("--seconds", type=int, default=30, help="0 = infinite")
    pc.add_argument("--read-size", type=int, default=128)
    pc.add_argument("--only-press", action="store_true", help="skip RELEASE events")
    pc.add_argument("--log", help="optional log path")
    pc.set_defaults(func=cmd_watch_consumer)

    pmr_watch = sub.add_parser(
        "watch-media-runtime",
        help="watch report 0x04 OSD flags and infer live media mode active/inactive",
    )
    pmr_watch.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    pmr_watch.add_argument("--seconds", type=int, default=30, help="0 = infinite")
    pmr_watch.add_argument("--read-size", type=int, default=128)
    pmr_watch.add_argument("--all", action="store_true", help="print every parsed 0x04 report")
    pmr_watch.add_argument(
        "--off-x-min",
        type=int,
        default=3000,
        help="minimum X for off-signature tap zone (default 3000)",
    )
    pmr_watch.add_argument(
        "--off-y-min",
        type=int,
        default=1850,
        help="minimum Y for off-signature tap zone (default 1850)",
    )
    pmr_watch.add_argument(
        "--off-stationary-max",
        type=int,
        default=24,
        help="max dx/dy in one touch session for off-signature (default 24)",
    )
    pmr_watch.add_argument(
        "--off-min-frames",
        type=int,
        default=3,
        help="minimum frames in one touch session for off-signature (default 3)",
    )
    pmr_watch.add_argument("--log", help="optional log path")
    pmr_watch.set_defaults(func=cmd_watch_media_runtime)

    prmk = sub.add_parser(
        "run-media-keys",
        help="emit play/pause/next/previous key events from multimedia strip taps",
    )
    prmk.add_argument("--dev", help="hidraw node (default touchpad candidate)")
    prmk.add_argument("--seconds", type=int, default=0, help="0 = infinite")
    prmk.add_argument("--read-size", type=int, default=128)
    prmk.add_argument(
        "--emit",
        choices=["uinput", "print"],
        default="uinput",
        help="event sink (default: uinput)",
    )
    prmk.add_argument(
        "--require-active",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="emit keys only when media mode is active (default: enabled)",
    )
    prmk.add_argument(
        "--verbose-skips",
        action="store_true",
        help="print skipped strip taps when mode is not active",
    )
    prmk.add_argument("--uinput-dev", default="/dev/uinput", help="uinput device path")
    prmk.add_argument("--uinput-name", default="swift-touch-media", help="virtual input device name")
    prmk.add_argument(
        "--off-x-min",
        type=int,
        default=3000,
        help="minimum X for off-signature tap zone (default 3000)",
    )
    prmk.add_argument(
        "--off-y-min",
        type=int,
        default=1850,
        help="minimum Y for off-signature tap zone (default 1850)",
    )
    prmk.add_argument(
        "--off-stationary-max",
        type=int,
        default=24,
        help="max dx/dy in one touch session for off-signature (default 24)",
    )
    prmk.add_argument(
        "--off-min-frames",
        type=int,
        default=3,
        help="minimum frames in one touch session for off-signature (default 3)",
    )
    prmk.add_argument("--log", help="optional log path")
    prmk.set_defaults(func=cmd_run_media_keys)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except PermissionError as e:
        print(f"Permission denied: {e}", file=sys.stderr)
        print("Try running with sudo/root.", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Not found: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"OS error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Bad input: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
