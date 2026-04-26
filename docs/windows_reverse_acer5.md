# AcerSense 5 Windows Reverse (Media Touchpad)

Date: 2026-04-25
Target package:
- `/home/chaos/Downloads/AcerSense_Acer_5.0.1971_W11x64/AcerSense5_RC27_5.0.1971/`

## 1) Runtime architecture (confirmed from binaries/strings)

- `AcerQAAgent.exe` runs as Windows service (`AcerQAAgentSvis`).
- `AQAUserPS.exe` runs per-user (UIAccess), handles RawInput and user-side OSD/UI interactions.
- Service starts/stops user process (`Start AQAUserPS. Path = ...` strings in service binary).
- `AQAUserPS.exe` has:
  - `OnRawInput(__int64)`
  - `OnRawInput_Touchpad(void)`
  - `NotifyServiceTouchpadStatusChanged(bool)`
- `AcerQAAgent.exe` has:
  - `OnUserProcessTouchpadKeyPressed(...)`
  - `OnUserProcessTouchpadStatusChanged(...)`
  - `OnWMIEvent_Touchpad(unsigned short)`
  - `GetTouchpadStatus()`
  - `UpdateInternalTouchpadState(bool)`

Transport indicators found:
- Named pipes:
  - `AgentUserPSPipe{69A0D80E-E1B8-46C0-8AEA-5AA88FF5BB04}`
  - `CCAgentUserPSPipe{9FE55B83-AB43-4ABB-81AB-D0510503C959}`
- Local secure WS is used (`wss://localhost` in user process).

## 2) Media Touchpad command protocol (from AcerSense `app.asar` main bundle)

Recovered command names (service requests):

- `GET_TOUCHPAD_DEVICE_MODEL`
- `GET_MEDIA_CONTROL_STATUS`
- `SET_MEDIA_CONTROL_STATUS`
- `GET_APP_AUTO_CONTROL_MODE`
- `SET_APP_AUTO_CONTROL_MODE`
- `GET_TOUCHPAD_STATUS_IN_MEDIA_MODE`
- `SET_TOUCHPAD_STATUS_IN_MEDIA_MODE`
- `GET_LED_STATUS_IN_MEDIA_MODE`
- `SET_LED_STATUS_IN_MEDIA_MODE`
- `GET_BACKLIGHT_LUMINANCE`
- `SET_BACKLIGHT_LUMINANCE`
- `GET_YOUTUBE_BUTTON_MODE`
- `SET_YOUTUBE_BUTTON_MODE`

Recovered request/response shape in JS:
- Request examples:
  - `{"Function":"GET_MEDIA_CONTROL_STATUS"}`
  - `{"Function":"SET_MEDIA_CONTROL_STATUS","Parameter":{"Status":0|1}}`
  - `{"Function":"SET_BACKLIGHT_LUMINANCE","Parameter":{"Luminance":<int>}}`
  - `{"Function":"SET_YOUTUBE_BUTTON_MODE","Parameter":{"Mode":<int>}}`
- Response handling checks:
  - `Result == 0` means success
  - parsed fields include `Data.Status`, `Data.Luminance`, `Data.Mode`, `Data.Model`

Model gate:
- `GET_TOUCHPAD_DEVICE_MODEL` result is checked via `Data.Model`.
- UI enables full media-touchpad settings path only when model check passes (`Model == 1` branch observed).

## 3) I2C register IDs used by AcerSense media-touchpad config

Recovered enum (`Jr`) from app bundle:

- `FUNCTION_KEY_CONTROL_EN = 118`
- `MEDIA_CONTROL = 119`
- `MEDIA_TP_FUNCTION_CONTROL = 120`
- `LIGHTING_FUNCTION_EN = 121`
- `BRIGHTNESS_CONTROL = 122`
- `YOUTUBE_FUNCTION_CONTROL = 123`

Second enum (`Qa`) is zero for all entries in this build:
- all fields `= 0` (used as second argument in `writeRegister`).

Default config in bundle:
- `FUNCTION_KEY_CONTROL_EN: 1`
- `MEDIA_CONTROL: 1`
- `MEDIA_TP_FUNCTION_CONTROL: 1`
- `LIGHTING_FUNCTION_EN: 1`
- `BRIGHTNESS_CONTROL: 50`
- `YOUTUBE_FUNCTION_CONTROL: 1`

### 3.1) Recovered register wire format (from `app.asar`, confirmed live)

In `MediaTouchpad` class (`app.asar` main bundle), register access is done with feature report `67` (`0x43`):

- `writeRegister(reg, qa, value)` sends:
  - `[0x43, reg, qa, value]`
- `readRegister(reg, qa)` sends:
  - `[0x43, reg, 0x10 | qa, 0x00]`
  - then reads feature report `0x43`

Observed in bundle:
- `Qa` enum values are all `0` in this build.
- effective packets used by app therefore are:
  - write: `[0x43, reg, 0x00, value]`
  - read: `[0x43, reg, 0x10, 0x00]`

### 3.2) Linux live validation on SFG16-74

On this host (`PIXA480A:00 093A:480A`, `/dev/hidraw0`) HID descriptor exposes:
- Feature report `0x43`, report count `3` (payload length `4` incl. report id).

Confirmed live reads (`[0x43, reg, 0x10, 0x00]` -> read `0x43`):
- `118 -> 1`
- `119 -> 1`
- `120 -> 1`
- `121 -> 1`
- `122 -> 50`
- `123 -> 1`

Confirmed live write:
- write `[0x43, 0x76, 0x00, 0x00]` then read `118 -> 0`
- write `[0x43, 0x76, 0x00, 0x01]` then immediate read `118 -> 1`

This matches AcerSense defaults and proves that register transport is `0x43` path on this model.

## 4) YouTube previous/next icon mode mapping

Recovered list:
- `YTcontrolList = ["SpeedDownUp","RewindForward","LastNextVideo"]`

UI writes mode as `index + 1`, so:
- `Mode=1` -> `SpeedDownUp`
- `Mode=2` -> `RewindForward`
- `Mode=3` -> `LastNextVideo`

## 5) What is still not fully resolved

- Direct static map `action-id -> semantic name` for Windows IDs `0x04`, `0x05`, `0x21`, `0x22` is not present as plain strings in `AQAUserPS.exe`/`AcerQAAgent.exe`.
- For Linux on SFG16-74, media-strip taps are exposed as touch coordinates in report `0x04` (see section 9), not as separate `0x0c` consumer packets.

## 6) AQAUserPS raw-input decode (confirmed in disassembly)

`AQAUserPS.exe` (`OnRawInput`) uses WinAPI RawInput path:
- `GetRawInputData(..., RID_INPUT=0x10000003, ...)`
- expects HID packet length `< 3` to be an error branch (`"expecting 3 bytes of HID report, but received: ..."` log string).

Decoded key value is assembled from payload bytes:
- `key = report[1] | (report[2] << 8)` (seen as reads from `[rdi+0x21]` and `[rdi+0x22]` in current build).
- Zero key is ignored.

Special key handling branches:
- `0xFF78` / `0xFF79`:
  - call touchpad status notify path (boolean based on key),
  - and are also mapped to key-action IDs.
- `0xFF80`:
  - dedicated touchpad-key path (`TouchpadKey` packet flow).
- `0xFF85`:
  - dedicated system-usage key path (`SystemUsageKey` packet flow).

Static map table built in this function (lazy-init branch):
- `0xFF78 -> 0x1F`
- `0xFF79 -> 0x20`
- `0xFF86 -> 0x04`
- `0xFF83 -> 0x05`
- `0xFF87 -> 0x21`
- `0xFF88 -> 0x22`

The mapped action ID is then dispatched to the user-process -> service IPC path.

## 7) AcerQAAgent websocket event routing (confirmed)

In `AcerQAAgent.exe` init table (callback registration block), event strings are bound as:
- `TouchpadKey` -> `0x140394fc0` (`AQAAgentSvis::OnUserProcessTouchpadKeyPressed`)
- `TouchpadStatus` -> `0x140394be0` (`AQAAgentSvis::OnUserProcessTouchpadStatusChanged`)
- `SystemUsageKey` -> `0x1403958d0`

Observed status-handler strings in service side:
- `"Get current Touchpad status failed!"`
- `"Set new Touchpad status failed!"`
- `"Touchpad status changed to: "`
- `"Enabled"` / `"Disabled"`

## 8) Current interpretation status

What is now hard-confirmed:
- Windows user process decodes vendor HID keys in `0xFFxx` space and converts them to internal action IDs.
- Service has separate handlers for `TouchpadKey`, `TouchpadStatus`, and `SystemUsageKey`.

What is still unresolved:
- exact static semantic names for Windows action IDs `0x04`, `0x05`, `0x21`, `0x22` are not directly labeled in PE strings/resources.

## 9) Live Linux mapping on SFG16-74 (confirmed by raw capture)

Controlled raw captures were done on `/dev/hidraw0` (PIXA480A touchpad) in media mode.
Taps were captured one-by-one and clustered by touch-down coordinates from report `0x04`.

Confirmed mapping:
- `prev` -> left cluster: `x≈1222`, `y≈970`
- `play/pause` -> center cluster: `x≈1800`, `y≈1000`
- `next` -> right cluster: `x≈2271`, `y≈1002`

Practical zone boundaries used in Linux tool:
- left (`PREVIOUSSONG`): `1000 <= x < 1510`
- center (`PLAYPAUSE`): `1510 <= x < 2035`
- right (`NEXTSONG`): `2035 <= x < 2500`
- media strip Y gate: `880 <= y <= 1180`
