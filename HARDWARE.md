# carechair sensor setup

## Confirmed hardware

The board attached to this laptop was identified by Arduino CLI and its printed label as **Arduino UNO Q**, not UNO R4 WiFi. Windows exposes its USB serial interface as COM10 (VID 2341, PID 0078). Port numbers can change. The user confirmed **Modulino Buttons ABX00110** and **Modulino Thermo ABX00103**.

Use a USB **data** cable from the laptop to the UNO Q. Connect the two modules in either order to the board's **3.3 V Qwiic connector**. This is `Wire1`; the header I²C bus is different. The LEDs establish that power is present, not that I²C communication works.

Thermo measures the **air at the sensor**, including relative humidity. It is not a body thermometer or a fever detector. Heat from your hand, the laptop, or the board will affect the reading. Keep the Thermo away from heat sources when measuring room conditions.

## First test: USB and sensor communication

1. Run `npm install`, then `npm run hardware:list`. The connected UNO Q should be listed. This command does not upload or alter firmware.
2. Stop `npm run dev` and close Arduino IDE Serial Monitor before uploading or running the standalone sensor monitor. Only one program can own the port.
3. In Arduino IDE, add `https://downloads.arduino.cc/packages/package_zephyr_index.json` under Additional Boards Manager URLs. Install the official **Arduino Zephyr Boards** core (0.55.0 or later) plus **Arduino_RouterBridge** and **Arduino_RPClite** from Library Manager, then select **Arduino UNO Q**, not R4. The current sketch uses standard `Serial`, which these versions route through the UNO Q Linux bridge.
4. Open `arduino/carechair_sensors/carechair_sensors.ino`, compile and upload. On UNO Q, the official uploader uses the USB ADB connection to program its microcontroller. Let the Linux side finish booting; use Arduino App Lab to finish initial board setup if the uploader reports the device is not ready. Do not flash R4 firmware onto the Q.
5. Run `npm run hardware:watch -- --port COM10`. Expect actual JSON readings, `addresses:[62,68]` (0x3E Buttons and 0x44 Thermo), and a finite room temperature. The firmware scans addresses 0x08–0x77 at startup and periodically thereafter. Addresses alone are not a unique device fingerprint; the identified models also depend on the confirmed physical labels and successful data reads. Different addresses may require updating the sketch if module addresses were changed previously.
6. Press and release A, B, and C individually. The terminal should print exactly one `BUTTON N PRESSED` per press. Holding a button should not repeat the event. A held button at startup/reconnect establishes a baseline and does not trigger an action.
7. Warm the air near the Thermo and watch its real reading change gradually. Do not place it in water. Disconnect a sensor or USB: the display must stop showing a live value.
8. Stop the standalone monitor with Ctrl+C. Run `npm run dev` and open the dashboard. The **Vitals & comfort** section updates automatically. No separate vitals page remains.

CLI equivalents, when `arduino-cli` is on PATH:

```powershell
arduino-cli core update-index --additional-urls https://downloads.arduino.cc/packages/package_zephyr_index.json
arduino-cli core install arduino:zephyr --additional-urls https://downloads.arduino.cc/packages/package_zephyr_index.json
arduino-cli lib update-index
arduino-cli lib install Arduino_RouterBridge Arduino_RPClite
arduino-cli board list
arduino-cli compile --fqbn arduino:zephyr:unoq arduino/carechair_sensors
arduino-cli upload --fqbn arduino:zephyr:unoq --port COM10 arduino/carechair_sensors
```

If an older Arduino CLI leaves `{upload.port.properties.serialNumber}` unresolved, run `arduino-cli board list --format json`, copy this board's `port.properties.serialNumber`, and add `--upload-property upload.port.properties.serialNumber=YOUR_DEVICE_SERIAL` to the upload command. Match it to `adb devices` before uploading. This fixes device selection; do not substitute another board's identifier.

The sketch uses the core's `Wire` support and reads the modules' documented I²C data directly. It does not require the full Modulino library or initialize unrelated actuators. The HS3003 calculation uses the manufacturer's 14-bit temperature/humidity conversion and checks the data-status bits. Missing, stale, short, and invalid responses become null values, never zero-degree placeholders.

## Laptop flow

Verified on this laptop: the UNO Q sketch compiled with Arduino Zephyr 1.0.0, Arduino_RouterBridge 0.4.3, and Arduino_RPClite 0.3.1, then uploaded successfully over USB. Live packets reported addresses 0x3E and 0x44, all three button states, approximately 23.3°C (74.0°F), and 45% humidity. The dashboard displayed these actual readings. These are observed room readings, not fixed example values used by the app. All 32 automated sensor and voice tests pass. The user also confirmed that A starts/stops the microphone and produces a reply, and tested B/C presses.

```text
Thermo + Buttons → Qwiic/Wire1 → UNO Q microcontroller
 → UNO Q USB serial bridge → Node SerialPort → localhost HTTP/SSE → carechair
```

`hardware-serial.js` auto-selects a single supported Arduino USB device and reconnects when unplugged. If multiple boards are attached, set `ARDUINO_PORT=COM10` in the server's `.env` and restart. The port name is local setup only. It supports the verified UNO Q IDs plus UNO R4 WiFi IDs; the R4 path has not been hardware-tested on this laptop.

`hardware-state.js` validates newline-delimited JSON, bounds line size, deduplicates sequence numbers, ages readings out after five seconds, and converts debounced button states into press events. `GET /api/vitals` returns the current snapshot; `GET /api/hardware/events` streams snapshots and new button presses. There is no public HTTP endpoint for inventing sensor measurements. Data stays in memory.

The browser receives live temperature in °C/°F and actual humidity. The 20–26°C band is a simple UI comfort guide, not a health threshold or a statement that everyone will be comfortable. Missing readings show a dash. The **Chair connection** details show which modules respond and the live A/B/C states.

Button **A / 1** uses the same Start/Stop microphone handler as the screen button. First press records; second press stops and submits to Deepgram, then the normal Meta/reply pipeline runs. The existing 20-second recording limit still applies. The browser must have microphone permission and the carechair tab must be visible. A hidden tab disconnects its event stream. Only the most recently connected visible tab receives button actions. While a care form is open, close it first; while a request is processing, extra presses are ignored rather than queued. B and C are observed but have no assigned action. No button directly triggers robot movement or medication delivery.

## USB or Wi-Fi?

**Keep USB for the hackathon.** One data cable provides power and a direct stream, with no Wi-Fi credentials, shared-network discovery, or wireless dropouts to debug. The laptop still needs internet for Deepgram and Meta.

Wireless is possible later: give the UNO Q its own power supply, put it and the laptop on a reachable network, and have a board-side service send the same sensor messages through an authenticated connection. The laptop server currently binds to localhost, so a wireless bridge needs explicit network/authentication design. Simply unplugging USB does not move the data to Wi-Fi. No wireless mode is implemented here.

## References

- [Arduino UNO Q manual](https://docs.arduino.cc/tutorials/uno-q/user-manual/)
- [UNO Q Serial support from Zephyr 0.55.0](https://support.arduino.cc/hc/en-us/articles/27251870677916-Migrating-to-Zephyr-core-0-55-0-on-UNO-Q)
- [Modulino Thermo](https://docs.arduino.cc/hardware/modulino-thermo/)
- [Modulino Buttons](https://docs.arduino.cc/hardware/modulino-buttons/)
- [Arduino module protocol implementation](https://github.com/arduino-libraries/Arduino_Modulino/blob/main/src/Modulino.h)
- [HS300x datasheet](https://docs.arduino.cc/resources/datasheets/REN_HS300x-Datasheet_DST.pdf)
- [Node SerialPort](https://serialport.io/docs/guide-usage/)
