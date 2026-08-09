/*
 * MARK L — M5StickC Firmware
 * ==========================
 * USB Serial JSON protocol for communication with the Python host.
 * 
 * Supports: M5StickC, M5StickC Plus, M5StickC Plus2
 * 
 * Setup (Arduino IDE):
 *   1. Install ESP32 board support (v2.x)
 *   2. Install libraries: M5StickCPlus (or M5StickC), ArduinoJson
 *   3. Select board: "M5StickC" or "M5StickC-Plus"
 *   4. Upload this sketch
 *   5. Connect USB → Python will auto-detect
 *
 * Protocol (115200 baud, JSON lines):
 *   PC → M5:  {"cmd":"text","text":"Hello","x":0,"y":0,"size":2,"color":"#FFF"}
 *   M5 → PC:  {"type":"button","btn":"A","action":"press"}
 *   M5 → PC:  {"type":"imu","ax":0.1,"ay":-0.05,"az":0.98,"gx":1.2,"gy":-0.3,"gz":0.5}
 */

#include <M5StickCPlus.h>
#include <ArduinoJson.h>

// ── Configuration ────────────────────────────────────────────────────────

#define FIRMWARE_VERSION "1.0.0"
#define SERIAL_BAUD 115200
#define IMU_RATE_HZ 30
#define BATTERY_CHECK_SEC 30

// Screen dimensions (M5StickC Plus: 135x240, M5StickC: 80x160)
#define SCREEN_W 240
#define SCREEN_H 135

// Colors
#define COLOR_BG      TFT_BLACK
#define COLOR_TEXT    TFT_WHITE
#define COLOR_ACCENT  0x06FF  // Cyan
#define COLOR_SUCCESS TFT_GREEN
#define COLOR_ERROR   TFT_RED
#define COLOR_WARNING TFT_YELLOW

// LED pin (M5StickC Plus: GPIO19 for IR, use M5.Axp for power LED)
#define LED_PIN 10  // Built-in LED on some models

// ── State ────────────────────────────────────────────────────────────────

unsigned long lastIMU = 0;
unsigned long lastBattery = 0;
unsigned long lastButtonA = 0;
unsigned long lastButtonB = 0;
unsigned long lastButtonHome = 0;
unsigned long buttonAPressTime = 0;
unsigned long buttonBPressTime = 0;

bool btnAWasPressed = false;
bool btnBWasPressed = false;
bool btnHomeWasPressed = false;

float ax = 0, ay = 0, az = 0;
float gx = 0, gy = 0, gz = 0;

int batteryLevel = 100;
float temperature = 0;

String currentFace = "happy";

// ── Icon bitmaps (simple 8x8 patterns scaled up) ─────────────────────────

void drawIconMic(int x, int y, int size) {
  int s = size / 8;
  M5.Lcd.fillRect(x + 3*s, y + 0*s, 2*s, 5*s, COLOR_TEXT);
  M5.Lcd.fillRect(x + 2*s, y + 1*s, 4*s, 3*s, COLOR_TEXT);
  M5.Lcd.fillRect(x + 1*s, y + 5*s, 6*s, 1*s, COLOR_ACCENT);
  M5.Lcd.fillRect(x + 3*s, y + 6*s, 2*s, 2*s, COLOR_ACCENT);
}

void drawIconSpeaker(int x, int y, int size) {
  int s = size / 8;
  M5.Lcd.fillRect(x + 1*s, y + 2*s, 2*s, 4*s, COLOR_TEXT);
  M5.Lcd.fillTriangle(x + 3*s, y + 1*s, x + 6*s, y + 0*s, x + 6*s, y + 7*s, COLOR_TEXT);
  M5.Lcd.fillTriangle(x + 3*s, y + 1*s, x + 6*s, y + 7*s, x + 3*s, y + 6*s, COLOR_TEXT);
}

void drawIconCheck(int x, int y, int size) {
  int s = size / 8;
  M5.Lcd.drawLine(x + 1*s, y + 4*s, x + 3*s, y + 6*s, COLOR_SUCCESS);
  M5.Lcd.drawLine(x + 2*s, y + 4*s, x + 4*s, y + 6*s, COLOR_SUCCESS);
  M5.Lcd.drawLine(x + 3*s, y + 6*s, x + 7*s, y + 1*s, COLOR_SUCCESS);
  M5.Lcd.drawLine(x + 4*s, y + 6*s, x + 7*s, y + 2*s, COLOR_SUCCESS);
}

void drawIconError(int x, int y, int size) {
  int s = size / 8;
  M5.Lcd.drawLine(x + 1*s, y + 1*s, x + 7*s, y + 7*s, COLOR_ERROR);
  M5.Lcd.drawLine(x + 2*s, y + 1*s, x + 7*s, y + 6*s, COLOR_ERROR);
  M5.Lcd.drawLine(x + 7*s, y + 1*s, x + 1*s, y + 7*s, COLOR_ERROR);
  M5.Lcd.drawLine(x + 7*s, y + 2*s, x + 2*s, y + 7*s, COLOR_ERROR);
}

void drawIconWarning(int x, int y, int size) {
  int s = size / 8;
  M5.Lcd.fillTriangle(x + 4*s, y + 0*s, x + 0*s, y + 7*s, x + 7*s, y + 7*s, COLOR_WARNING);
  M5.Lcd.fillRect(x + 3*s, y + 2*s, 2*s, 3*s, COLOR_BG);
  M5.Lcd.fillRect(x + 3*s, y + 6*s, 2*s, 1*s, COLOR_BG);
}

void drawIconBattery(int x, int y, int size) {
  int s = size / 8;
  M5.Lcd.drawRect(x + 0*s, y + 2*s, 6*s, 4*s, COLOR_TEXT);
  M5.Lcd.fillRect(x + 6*s, y + 3*s, 2*s, 2*s, COLOR_TEXT);
  int fill = (batteryLevel * 5) / 100;
  uint16_t col = batteryLevel > 20 ? COLOR_SUCCESS : COLOR_ERROR;
  M5.Lcd.fillRect(x + 1*s, y + 3*s, fill*s, 2*s, col);
}

void drawIconByName(const char* name, int x, int y, int size) {
  if (strcmp(name, "mic") == 0) drawIconMic(x, y, size);
  else if (strcmp(name, "speaker") == 0) drawIconSpeaker(x, y, size);
  else if (strcmp(name, "check") == 0) drawIconCheck(x, y, size);
  else if (strcmp(name, "error") == 0) drawIconError(x, y, size);
  else if (strcmp(name, "warning") == 0) drawIconWarning(x, y, size);
  else if (strcmp(name, "battery") == 0) drawIconBattery(x, y, size);
  else {
    // Default: question mark
    M5.Lcd.setTextSize(size / 16);
    M5.Lcd.drawChar('?', x, y, COLOR_TEXT, COLOR_BG, size / 16);
  }
}

// ── Face expressions ─────────────────────────────────────────────────────

void drawFace(const char* expression) {
  M5.Lcd.fillScreen(COLOR_BG);
  currentFace = expression;
  
  int cx = SCREEN_W / 2;
  int cy = SCREEN_H / 2;
  
  if (strcmp(expression, "happy") == 0) {
    // Eyes
    M5.Lcd.fillCircle(cx - 25, cy - 15, 8, COLOR_ACCENT);
    M5.Lcd.fillCircle(cx + 25, cy - 15, 8, COLOR_ACCENT);
    // Smile
    M5.Lcd.drawArc(cx, cy + 5, 30, 20, 0, 180, COLOR_ACCENT, COLOR_BG);
    M5.Lcd.drawArc(cx, cy + 5, 32, 22, 0, 180, COLOR_ACCENT, COLOR_BG);
    
  } else if (strcmp(expression, "sad") == 0) {
    M5.Lcd.fillCircle(cx - 25, cy - 15, 8, 0x6B4D);
    M5.Lcd.fillCircle(cx + 25, cy - 15, 8, 0x6B4D);
    M5.Lcd.drawArc(cx, cy + 30, 25, 15, 180, 360, 0x6B4D, COLOR_BG);
    
  } else if (strcmp(expression, "surprised") == 0) {
    M5.Lcd.fillCircle(cx - 25, cy - 15, 10, COLOR_WARNING);
    M5.Lcd.fillCircle(cx + 25, cy - 15, 10, COLOR_WARNING);
    M5.Lcd.fillCircle(cx, cy + 20, 12, COLOR_WARNING);
    
  } else if (strcmp(expression, "angry") == 0) {
    M5.Lcd.fillCircle(cx - 25, cy - 15, 8, COLOR_ERROR);
    M5.Lcd.fillCircle(cx + 25, cy - 15, 8, COLOR_ERROR);
    // Angry brows
    M5.Lcd.drawLine(cx - 35, cy - 30, cx - 15, cy - 25, COLOR_ERROR);
    M5.Lcd.drawLine(cx + 35, cy - 30, cx + 15, cy - 25, COLOR_ERROR);
    // Frown
    M5.Lcd.drawArc(cx, cy + 30, 20, 15, 180, 360, COLOR_ERROR, COLOR_BG);
    
  } else if (strcmp(expression, "sleepy") == 0) {
    M5.Lcd.drawLine(cx - 33, cy - 15, cx - 17, cy - 15, 0x6B4D);
    M5.Lcd.drawLine(cx + 17, cy - 15, cx + 33, cy - 15, 0x6B4D);
    M5.Lcd.drawArc(cx, cy + 15, 15, 8, 0, 180, 0x6B4D, COLOR_BG);
    // Zzz
    M5.Lcd.setTextSize(2);
    M5.Lcd.drawChar('Z', cx + 40, cy - 35, 0x6B4D, COLOR_BG, 2);
    M5.Lcd.drawChar('z', cx + 55, cy - 45, 0x6B4D, COLOR_BG, 2);
    
  } else if (strcmp(expression, "cool") == 0) {
    // Sunglasses
    M5.Lcd.fillRect(cx - 40, cy - 22, 30, 16, COLOR_TEXT);
    M5.Lcd.fillRect(cx + 10, cy - 22, 30, 16, COLOR_TEXT);
    M5.Lcd.drawLine(cx - 10, cy - 14, cx + 10, cy - 14, COLOR_TEXT);
    // Smile
    M5.Lcd.drawArc(cx, cy + 15, 25, 18, 0, 180, COLOR_ACCENT, COLOR_BG);
    
  } else if (strcmp(expression, "love") == 0) {
    // Heart eyes (simplified as filled circles in red)
    M5.Lcd.fillCircle(cx - 25, cy - 15, 10, 0xF800);
    M5.Lcd.fillCircle(cx + 25, cy - 15, 10, 0xF800);
    // Big smile
    M5.Lcd.drawArc(cx, cy + 10, 30, 20, 0, 180, 0xF800, COLOR_BG);
    
  } else {
    // Default: neutral
    M5.Lcd.fillCircle(cx - 25, cy - 15, 8, COLOR_TEXT);
    M5.Lcd.fillCircle(cx + 25, cy - 15, 8, COLOR_TEXT);
    M5.Lcd.drawLine(cx - 20, cy + 15, cx + 20, cy + 15, COLOR_TEXT);
  }
}

// ── Color parsing ────────────────────────────────────────────────────────

uint16_t parseColor(const char* str) {
  if (!str || str[0] == '\0') return COLOR_TEXT;
  
  // Named colors
  if (strcmp(str, "red") == 0)     return TFT_RED;
  if (strcmp(str, "green") == 0)   return TFT_GREEN;
  if (strcmp(str, "blue") == 0)    return TFT_BLUE;
  if (strcmp(str, "yellow") == 0)  return TFT_YELLOW;
  if (strcmp(str, "cyan") == 0)    return TFT_CYAN;
  if (strcmp(str, "magenta") == 0) return TFT_MAGENTA;
  if (strcmp(str, "white") == 0)   return TFT_WHITE;
  if (strcmp(str, "black") == 0)   return TFT_BLACK;
  if (strcmp(str, "off") == 0)     return TFT_BLACK;
  
  // Hex color (#RRGGBB → RGB565)
  if (str[0] == '#' && strlen(str) >= 7) {
    long rgb = strtol(str + 1, NULL, 16);
    int r = (rgb >> 16) & 0xFF;
    int g = (rgb >> 8) & 0xFF;
    int b = rgb & 0xFF;
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
  }
  
  return COLOR_TEXT;
}

// ── JSON command processing ──────────────────────────────────────────────

void processCommand(JsonDocument& doc) {
  const char* cmd = doc["cmd"];
  if (!cmd) return;
  
  if (strcmp(cmd, "ping") == 0) {
    sendPong();
    
  } else if (strcmp(cmd, "info") == 0) {
    sendInfo();
    
  } else if (strcmp(cmd, "text") == 0) {
    const char* text = doc["text"] | "";
    int x = doc["x"] | 0;
    int y = doc["y"] | 0;
    int size = doc["size"] | 2;
    uint16_t color = parseColor(doc["color"] | "#FFFFFF");
    uint16_t bg = parseColor(doc["bg"] | "#000000");
    M5.Lcd.setTextSize(size);
    M5.Lcd.setTextColor(color, bg);
    M5.Lcd.setCursor(x, y);
    M5.Lcd.print(text);
    
  } else if (strcmp(cmd, "clear") == 0) {
    uint16_t color = parseColor(doc["color"] | "#000000");
    M5.Lcd.fillScreen(color);
    
  } else if (strcmp(cmd, "title") == 0) {
    const char* title = doc["title"] | "";
    const char* subtitle = doc["subtitle"] | "";
    
    M5.Lcd.fillScreen(COLOR_BG);
    // Title bar
    M5.Lcd.fillRect(0, 0, SCREEN_W, 30, COLOR_ACCENT);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setTextColor(TFT_BLACK, COLOR_ACCENT);
    M5.Lcd.setCursor(10, 8);
    M5.Lcd.print(title);
    
    if (strlen(subtitle) > 0) {
      M5.Lcd.setTextSize(1);
      M5.Lcd.setTextColor(COLOR_TEXT, COLOR_BG);
      M5.Lcd.setCursor(10, 40);
      M5.Lcd.print(subtitle);
    }
    
  } else if (strcmp(cmd, "icon") == 0) {
    const char* icon = doc["icon"] | "check";
    int x = doc["x"] | 0;
    int y = doc["y"] | 0;
    int size = doc["size"] | 32;
    drawIconByName(icon, x, y, size);
    
  } else if (strcmp(cmd, "progress") == 0) {
    float value = doc["value"] | 0.0;
    const char* label = doc["label"] | "";
    
    int barW = SCREEN_W - 20;
    int barH = 16;
    int barX = 10;
    int barY = SCREEN_H / 2 - barH / 2;
    
    M5.Lcd.drawRect(barX, barY, barW, barH, COLOR_TEXT);
    int fillW = (int)(value * (barW - 2));
    M5.Lcd.fillRect(barX + 1, barY + 1, fillW, barH - 2, COLOR_ACCENT);
    
    if (strlen(label) > 0) {
      M5.Lcd.setTextSize(1);
      M5.Lcd.setTextColor(COLOR_TEXT, COLOR_BG);
      M5.Lcd.setCursor(10, barY - 12);
      M5.Lcd.print(label);
    }
    
  } else if (strcmp(cmd, "status") == 0) {
    JsonArray lines = doc["lines"].as<JsonArray>();
    M5.Lcd.fillScreen(COLOR_BG);
    M5.Lcd.setTextSize(1);
    M5.Lcd.setTextColor(COLOR_TEXT, COLOR_BG);
    int y = 10;
    for (JsonVariant v : lines) {
      const char* line = v.as<const char*>();
      M5.Lcd.setCursor(10, y);
      M5.Lcd.print(line);
      y += 14;
    }
    
  } else if (strcmp(cmd, "face") == 0) {
    const char* expression = doc["expression"] | "happy";
    drawFace(expression);
    
  } else if (strcmp(cmd, "led") == 0) {
    const char* color = doc["color"] | "off";
    int brightness = doc["brightness"] | 50;
    // M5StickC Plus doesn't have RGB LED, use screen corner indicator
    uint16_t col = parseColor(color);
    if (strcmp(color, "off") == 0) {
      M5.Lcd.fillRect(0, 0, 4, 4, COLOR_BG);
    } else {
      M5.Lcd.fillRect(0, 0, 4, 4, col);
    }
    
  } else if (strcmp(cmd, "blink") == 0) {
    const char* color = doc["color"] | "white";
    int times = doc["times"] | 3;
    int interval = doc["interval"] | 200;
    uint16_t col = parseColor(color);
    for (int i = 0; i < times; i++) {
      M5.Lcd.fillRect(0, 0, 6, 6, col);
      delay(interval);
      M5.Lcd.fillRect(0, 0, 6, 6, COLOR_BG);
      delay(interval);
    }
    
  } else if (strcmp(cmd, "pulse") == 0) {
    const char* color = doc["color"] | "blue";
    int duration = doc["duration"] | 2000;
    uint16_t col = parseColor(color);
    int steps = duration / 50;
    // Simple pulse: flash with fading
    for (int i = 0; i < steps && i < 40; i++) {
      float fade = sin((float)i / steps * 3.14159);
      if (fade > 0.3) {
        M5.Lcd.fillRect(0, 0, 6, 6, col);
      } else {
        M5.Lcd.fillRect(0, 0, 6, 6, COLOR_BG);
      }
      delay(50);
    }
    M5.Lcd.fillRect(0, 0, 6, 6, COLOR_BG);
    
  } else if (strcmp(cmd, "brightness") == 0) {
    int level = doc["level"] | 128;
    // Map 0-255 to LCD voltage (M5StickC Plus: 2400-3300mV)
    int voltage = 2400 + (level * 900) / 255;
    M5.Axp.ScreenBreath(voltage);
    
  } else if (strcmp(cmd, "battery") == 0) {
    sendBattery();
    
  } else if (strcmp(cmd, "temperature") == 0) {
    sendTemperature();
    
  } else if (strcmp(cmd, "mic_start") == 0) {
    // Microphone streaming placeholder
    sendJson("mic_status", "started");
    
  } else if (strcmp(cmd, "mic_stop") == 0) {
    sendJson("mic_status", "stopped");
    
  } else if (strcmp(cmd, "sleep") == 0) {
    int seconds = doc["seconds"] | 0;
    M5.Lcd.fillScreen(COLOR_BG);
    M5.Lcd.setTextSize(2);
    M5.Lcd.setTextColor(0x6B4D, COLOR_BG);
    M5.Lcd.setCursor(60, 55);
    M5.Lcd.print("Sleep...");
    if (seconds > 0) {
      delay(seconds * 1000);
    }
    M5.Axp.DeepSleep(seconds > 0 ? 0 : seconds);
    
  } else if (strcmp(cmd, "reset") == 0) {
    ESP.restart();
  }
}

// ── JSON sending helpers ─────────────────────────────────────────────────

void sendJson(const char* type, const char* value) {
  StaticJsonDocument<128> doc;
  doc["type"] = type;
  doc["value"] = value;
  serializeJson(doc, Serial);
  Serial.println();
}

void sendPong() {
  StaticJsonDocument<128> doc;
  doc["type"] = "pong";
  doc["device"] = "M5StickCPlus";
  doc["fw"] = FIRMWARE_VERSION;
  serializeJson(doc, Serial);
  Serial.println();
}

void sendInfo() {
  StaticJsonDocument<256> doc;
  doc["type"] = "info";
  doc["model"] = "M5StickC Plus";
  doc["fw"] = FIRMWARE_VERSION;
  doc["bat"] = batteryLevel;
  doc["temp"] = temperature;
  doc["screen_w"] = SCREEN_W;
  doc["screen_h"] = SCREEN_H;
  serializeJson(doc, Serial);
  Serial.println();
}

void sendIMU() {
  StaticJsonDocument<256> doc;
  doc["type"] = "imu";
  doc["ax"] = ax;
  doc["ay"] = ay;
  doc["az"] = az;
  doc["gx"] = gx;
  doc["gy"] = gy;
  doc["gz"] = gz;
  serializeJson(doc, Serial);
  Serial.println();
}

void sendButton(const char* btn, const char* action) {
  StaticJsonDocument<128> doc;
  doc["type"] = "button";
  doc["btn"] = btn;
  doc["action"] = action;
  serializeJson(doc, Serial);
  Serial.println();
}

void sendBattery() {
  StaticJsonDocument<128> doc;
  doc["type"] = "battery";
  doc["level"] = batteryLevel;
  doc["voltage"] = M5.Axp.GetBatVoltage();
  serializeJson(doc, Serial);
  Serial.println();
}

void sendTemperature() {
  StaticJsonDocument<128> doc;
  doc["type"] = "temperature";
  doc["value"] = temperature;
  serializeJson(doc, Serial);
  Serial.println();
}

// ── Button handling ──────────────────────────────────────────────────────

void checkButtons() {
  unsigned long now = millis();
  
  // Button A (front, large)
  bool btnA = M5.BtnA.isPressed();
  if (btnA && !btnAWasPressed) {
    buttonAPressTime = now;
    sendButton("A", "press");
  }
  if (!btnA && btnAWasPressed) {
    unsigned long held = now - buttonAPressTime;
    if (held > 1000) {
      sendButton("A", "long");
    } else {
      sendButton("A", "release");
    }
  }
  btnAWasPressed = btnA;
  
  // Button B (side)
  bool btnB = M5.BtnB.isPressed();
  if (btnB && !btnBWasPressed) {
    buttonBPressTime = now;
    sendButton("B", "press");
  }
  if (!btnB && btnBWasPressed) {
    unsigned long held = now - buttonBPressTime;
    if (held > 1000) {
      sendButton("B", "long");
    } else {
      sendButton("B", "release");
    }
  }
  btnBWasPressed = btnB;
}

// ── Setup ────────────────────────────────────────────────────────────────

void setup() {
  M5.begin();
  Serial.begin(SERIAL_BAUD);
  
  // Screen setup
  M5.Lcd.setRotation(1);  // Landscape
  M5.Lcd.fillScreen(COLOR_BG);
  
  // Show boot screen
  M5.Lcd.setTextSize(2);
  M5.Lcd.setTextColor(COLOR_ACCENT, COLOR_BG);
  M5.Lcd.setCursor(30, 30);
  M5.Lcd.print("MARK L");
  M5.Lcd.setTextSize(1);
  M5.Lcd.setTextColor(COLOR_TEXT, COLOR_BG);
  M5.Lcd.setCursor(30, 55);
  M5.Lcd.print("M5StickC Plus");
  M5.Lcd.setCursor(30, 70);
  M5.Lcd.print("FW: ");
  M5.Lcd.print(FIRMWARE_VERSION);
  M5.Lcd.setCursor(30, 90);
  M5.Lcd.setTextColor(0x6B4D, COLOR_BG);
  M5.Lcd.print("Connecting USB...");
  
  // Initialize IMU
  M5.IMU.Init();
  
  // Read initial battery
  batteryLevel = M5.Axp.GetBatteryLevel();
  temperature = M5.Axp.GetTempInAXP192();
  
  delay(1500);
  
  // Show idle face
  drawFace("happy");
  
  Serial.println("{\"type\":\"ready\",\"device\":\"M5StickCPlus\",\"fw\":\"" FIRMWARE_VERSION "\"}");
}

// ── Main loop ────────────────────────────────────────────────────────────

void loop() {
  M5.update();
  
  unsigned long now = millis();
  
  // Read incoming commands
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      StaticJsonDocument<512> doc;
      DeserializationError err = deserializeJson(doc, line);
      if (!err) {
        processCommand(doc);
      }
    }
  }
  
  // Send IMU data at configured rate
  if (now - lastIMU >= (1000 / IMU_RATE_HZ)) {
    lastIMU = now;
    M5.IMU.getAccelData(&ax, &ay, &az);
    M5.IMU.getGyroData(&gx, &gy, &gz);
    sendIMU();
  }
  
  // Check buttons
  checkButtons();
  
  // Periodic battery check
  if (now - lastBattery >= (BATTERY_CHECK_SEC * 1000UL)) {
    lastBattery = now;
    batteryLevel = M5.Axp.GetBatteryLevel();
    temperature = M5.Axp.GetTempInAXP192();
    
    // Low battery warning
    if (batteryLevel < 15) {
      drawFace("sleepy");
    }
  }
  
  delay(1);
}
