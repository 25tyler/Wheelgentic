// carechair: Modulino Thermo ABX00103 + Modulino Buttons ABX00110.
// Both modules connect to the board's 3.3 V Qwiic port (Wire1).
// UNO Q: install the official Arduino Zephyr core, version 0.55.0 or newer.
// Serial on that core is routed through the UNO Q's Linux USB bridge.
#include <Wire.h>
#include <math.h>

#if !defined(ARDUINO_UNO_Q) && !defined(ARDUINO_UNOR4_WIFI)
#error "Select Arduino UNO Q or Arduino UNO R4 WiFi before compiling."
#endif

const uint8_t THERMO_ADDRESS = 0x44;
const uint8_t BUTTONS_ADDRESS = 0x3E;
bool addresses[128] = {};
bool rawButtons[3] = {}, stableButtons[3] = {};
unsigned long changedAt[3] = {};
bool buttonsValid = false, temperatureValid = false, conversionPending = false;
float temperatureC = NAN, humidityPct = NAN;
unsigned long temperatureAt = 0, conversionAt = 0, lastTemperatureRequest = 0;
unsigned long lastButtonPoll = 0, lastFrame = 0, lastScan = 0;
uint32_t sequenceNumber = 0;

void drainI2C() { while (Wire1.available()) Wire1.read(); }

void scanSensors() {
  // ACKs locate devices; model labels come from the confirmed physical module labels.
  for (uint8_t address = 8; address < 120; address++) {
    Wire1.beginTransmission(address);
    addresses[address] = Wire1.endTransmission() == 0;
  }
  if (!addresses[THERMO_ADDRESS]) temperatureValid = false;
  if (!addresses[BUTTONS_ADDRESS]) buttonsValid = false;
  lastScan = millis();
}

bool pollButtons(unsigned long now) {
  bool wasValid = buttonsValid;
  // Read the module's address byte followed by all three actual button states.
  const auto count = Wire1.requestFrom(BUTTONS_ADDRESS, (uint8_t)4);
  if (count != 4 || Wire1.available() != 4) {
    drainI2C(); buttonsValid = false; return wasValid;
  }
  Wire1.read();
  uint8_t values[3];
  for (uint8_t i = 0; i < 3; i++) values[i] = Wire1.read();
  for (uint8_t i = 0; i < 3; i++) if (values[i] > 1) { buttonsValid = false; return wasValid; }
  addresses[BUTTONS_ADDRESS] = true;
  buttonsValid = true;
  bool changed = !wasValid;
  for (uint8_t i = 0; i < 3; i++) {
    bool down = values[i] != 0;
    if (!wasValid) { rawButtons[i] = stableButtons[i] = down; changedAt[i] = now; }
    if (down != rawButtons[i]) { rawButtons[i] = down; changedAt[i] = now; }
    if (stableButtons[i] != down && (unsigned long)(now - changedAt[i]) >= 30) {
      stableButtons[i] = down; changed = true;
    }
  }
  return changed;
}

void updateTemperature(unsigned long now) {
  if (!conversionPending && (unsigned long)(now - lastTemperatureRequest) >= 1000) {
    lastTemperatureRequest = now;
    Wire1.beginTransmission(THERMO_ADDRESS);
    Wire1.write((uint8_t)0); // HS3003 measurement request.
    if (Wire1.endTransmission() != 0) { temperatureValid = false; return; }
    conversionPending = true; conversionAt = now;
  }
  if (!conversionPending || (unsigned long)(now - conversionAt) < 40) return;
  const auto count = Wire1.requestFrom(THERMO_ADDRESS, (uint8_t)4);
  if (count != 4 || Wire1.available() != 4) {
    drainI2C(); conversionPending = false; temperatureValid = false; return;
  }
  uint8_t data[4];
  for (uint8_t i = 0; i < 4; i++) data[i] = Wire1.read();
  const uint8_t status = data[0] >> 6;
  if (status == 1 && (unsigned long)(now - conversionAt) < 150) return; // Stale conversion: retry briefly.
  conversionPending = false;
  if (status != 0) { temperatureValid = false; return; }
  const uint16_t humidityRaw = ((uint16_t)(data[0] & 0x3F) << 8) | data[1];
  const uint16_t temperatureRaw = (((uint16_t)data[2] << 8) | data[3]) >> 2;
  temperatureC = temperatureRaw * (165.0f / 16383.0f) - 40.0f;
  humidityPct = humidityRaw * (100.0f / 16383.0f);
  temperatureValid = isfinite(temperatureC) && isfinite(humidityPct);
  if (temperatureValid) { temperatureAt = now; addresses[THERMO_ADDRESS] = true; }
}

void printFrame(unsigned long now) {
  Serial.print("{\"protocol\":\"carechair/1\",\"type\":\"sample\",\"seq\":"); Serial.print(sequenceNumber++);
  Serial.print(",\"uptime_ms\":"); Serial.print(now);
  Serial.print(",\"temperature_c\":"); if (temperatureValid) Serial.print(temperatureC, 2); else Serial.print("null");
  Serial.print(",\"humidity_pct\":"); if (temperatureValid) Serial.print(humidityPct, 2); else Serial.print("null");
  Serial.print(",\"temperature_age_ms\":"); if (temperatureValid) Serial.print((unsigned long)(now - temperatureAt)); else Serial.print("null");
  Serial.print(",\"buttons\":");
  if (buttonsValid) {
    Serial.print('[');
    for (uint8_t i = 0; i < 3; i++) { if(i)Serial.print(','); Serial.print(stableButtons[i] ? "true" : "false"); }
    Serial.print(']');
  } else Serial.print("null");
  Serial.print(",\"i2c_addresses\":["); bool first = true;
  for (uint8_t i = 8; i < 120; i++) if (addresses[i]) { if(!first)Serial.print(','); Serial.print(i); first = false; }
  Serial.println("]}"); lastFrame = now;
}

void setup() {
  Serial.begin(115200);
  Serial.println("{\"protocol\":\"carechair/1\",\"type\":\"boot\",\"firmware\":\"carechair-sensors/1\"}");
  // Do not wait for a laptop: keep polling sensors if USB is temporarily disconnected.
  Wire1.begin(); Wire1.setClock(100000);
  scanSensors();
}

void loop() {
  unsigned long now = millis();
  bool changed = false;
  if ((unsigned long)(now - lastButtonPoll) >= 10) { lastButtonPoll = now; changed = pollButtons(now); }
  updateTemperature(now);
  if (!conversionPending && (unsigned long)(now - lastScan) >= 10000) scanSensors();
  // One heartbeat/second and immediate debounced button changes. No robot outputs.
  if (changed || (unsigned long)(now - lastFrame) >= 1000) printFrame(millis());
  delay(1);
}
