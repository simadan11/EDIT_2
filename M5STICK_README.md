# 🔌 M5Stick USB Controller — Полное руководство

Модуль для подключения и управления M5StickC/Plus по USB в проекте MARK L.

## 📋 Содержание

1. [Что такое M5Stick](#-что-такое-m5stick)
2. [Возможности](#-возможности)
3. [Установка](#-установка)
4. [Прошивка M5Stick](#-прошивка-m5stick)
5. [Подключение](#-подключение)
6. [Голосовые команды](#-голосовые-команды)
7. [API Python](#-api-python)
8. [События и жесты](#-события-и-жесты)
9. [Решение проблем](#-решение-проблем)

---

## 🔧 Что такое M5Stick

**M5StickC** — это компактный микроконтроллер на базе ESP32 от M5Stack:

| Компонент | M5StickC | M5StickC Plus | M5StickC Plus2 |
|-----------|----------|---------------|----------------|
| Процессор | ESP32-PICO | ESP32-PICO | ESP32-PICO-V3 |
| Экран | 80×160 TFT | 135×240 TFT | 135×240 TFT |
| IMU | MPU6886 | MPU6886 | BMI270 |
| Микрофон | SPM1423 | SPM1423 | SPM1423 |
| Кнопки | A, B, Home | A, B, Home | A, B, Home |
| Батарея | 80 mAh | 120 mAh | 200 mAh |
| USB | USB-C | USB-C | USB-C |
| Вибро | ❌ | ❌ | ✅ |
| LED | ❌ | ИК (GPIO19) | ИК (GPIO19) |

### Зачем M5Stick в MARK L?

- **Второй экран** — отображение статуса, уведомлений, времени
- **Физические кнопки** — быстрый доступ к функциям ассистента
- **IMU датчики** — управление жестами (наклон, тряска, переворот)
- **Мобильность** — компактное устройство на столе
- **Уведомления** — LED и вибрация для тихих алертов

---

## ✨ Возможности

### 📺 Экран (TFT Display)
- Отображение текста любого размера и цвета
- 7 выражений лица (happy, sad, angry, surprised, sleepy, cool, love)
- Прогресс-бары
- Заголовки с подзаголовками
- Иконки (mic, speaker, check, error, warning, battery)
- Статусные строки

### 🎮 Кнопки
- **Button A** (большая, спереди) — основное действие (режим прослушивания)
- **Button B** (сбоку) — показать статус
- **Долгое нажатие** — дополнительные функции
- Отправка событий на ПК через USB

### 📐 IMU датчики
- **Акселерометр** — наклон, ориентация
- **Гироскоп** — вращение, тряска
- **Обнаружение жестов**:
  - Shake (тряска)
  - Flip (переворот)
  - Tilt left/right (наклон)

### 💡 LED уведомления
- 8 предустановленных цветов
- Мигание с настройкой частоты
- Пульсация (breathing effect)
- Индикатор на экране (для моделей без LED)

### 📳 Вибромотор (Plus2)
- Настраиваемая длительность
- Регулируемая сила
- Паттерны вибрации

### 🔋 Системная информация
- Уровень заряда батареи
- Температура процессора
- Версия прошивки
- Модель устройства

---

## 📦 Установка

### 1. Установите зависимости Python

```bash
pip install pyserial
```

Или используйте requirements.txt:
```bash
pip install -r requirements.txt
```

### 2. Установите Arduino IDE (для прошивки)

1. Скачайте [Arduino IDE](https://www.arduino.cc/en/software)
2. Добавьте поддержку ESP32:
   - File → Preferences → Additional Board Manager URLs
   - Добавьте: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Tools → Board → Boards Manager → установите "esp32"

### 3. Установите библиотеки

В Arduino IDE:
- Sketch → Include Library → Manage Libraries
- Установите:
  - `M5StickCPlus` (или `M5StickC` для базовой модели)
  - `ArduinoJson` (версия 6.x)

---

## 🔥 Прошивка M5Stick

### 1. Откройте скетч

Откройте файл `firmware/m5stick/m5stick_firmware.ino` в Arduino IDE.

### 2. Выберите плату

- Tools → Board → ESP32 Arduino → **M5StickC-Plus** (или M5StickC)
- Tools → Port → выберите COM-порт M5Stick

### 3. Настройте (опционально)

В начале файла `m5stick_firmware.ino` можно изменить:
```cpp
#define FIRMWARE_VERSION "1.0.0"  // Версия прошивки
#define IMU_RATE_HZ 30            // Частота обновления IMU
#define BATTERY_CHECK_SEC 30      // Интервал проверки батареи
```

### 4. Загрузите прошивку

Нажмите **Upload** (Ctrl+U). Через 30-60 секунд прошивка будет загружена.

### 5. Проверьте работу

Откройте Serial Monitor (115200 baud) — вы должны увидеть:
```json
{"type":"ready","device":"M5StickCPlus","fw":"1.0.0"}
```

На экране M5Stick появится лицо 😊 (happy).

---

## 🔌 Подключение

### Автоматическое подключение

```python
from actions.m5stick import connect, get_controller

# Авто-обнаружение и подключение
if connect():
    print("M5Stick подключен!")
    ctrl = get_controller()
    ctrl.display_text("Hello!", 10, 40)
else:
    print("M5Stick не найден")
```

### Подключение к конкретному порту

```python
from actions.m5stick import get_controller

ctrl = get_controller()
ctrl.connect("/dev/ttyUSB0")  # Linux
# или
ctrl.connect("COM3")  # Windows
```

### Проверка подключения

```python
from actions.m5stick import is_connected

if is_connected():
    print("M5Stick онлайн")
```

---

## 🗣️ Голосовые команды

### Основные команды

| Команда | Действие |
|---------|----------|
| "статус M5Stick" | Показать информацию об устройстве |
| "батарея M5Stick" | Уровень заряда |
| "температура M5Stick" | Температура процессора |

### Команды экрана

| Команда | Действие |
|---------|----------|
| "покажи привет на M5Stick" | Текст на экране |
| "очисти экран M5Stick" | Очистить дисплей |
| "покажи время" | Текущее время |
| "смайлик счастливый" | Эмодзи-лицо |
| "прогресс 75" | Прогресс-бар |

### LED команды

| Команда | Действие |
|---------|----------|
| "светодиод красный" | Цвет LED |
| "мигай синим" | Мигание LED |
| "пульсируй зеленым" | Пульсация |
| "выключи светодиод" | LED off |

### Системные команды

| Команда | Действие |
|---------|----------|
| "вибрируй" | Вибрация (Plus2) |
| "яркость 50" | Яркость экрана |
| "отключи M5Stick" | Отключение |
| "перезагрузи M5Stick" | Рестарт |
| "сон M5Stick" | Спящий режим |

### Уведомления

| Команда | Действие |
|---------|----------|
| "успех" | Зеленое уведомление |
| "ошибка" | Красное уведомление |
| "предупреждение" | Желтое уведомление |
| "слушаю" | Режим прослушивания |
| "говорю" | Режим воспроизведения |
| "ожидание" | Возврат в idle |

---

## 🐍 API Python

### Базовое использование

```python
from actions.m5stick import get_controller

ctrl = get_controller()
ctrl.connect()

# Текст
ctrl.display_text("Hello World", x=10, y=40, size=2, color="#00FF00")

# Очистка
ctrl.display_clear()

# Заголовок
ctrl.display_title("MARK L", "Connected")

# Эмодзи-лицо
ctrl.display_face("happy")  # happy, sad, angry, surprised, sleepy, cool, love

# Иконка
ctrl.display_icon("mic", x=40, y=20, size=48)

# Прогресс
ctrl.display_progress(0.75, "Loading...")

# Статусные строки
ctrl.display_status([
    "Battery: 85%",
    "Temp: 42.5C",
    "Uptime: 2h 15m"
])
```

### LED управление

```python
# Цвет (red, green, blue, yellow, cyan, magenta, white, off, #FF0000)
ctrl.led_color("cyan", brightness=80)

# Мигание
ctrl.led_blink("red", times=5, interval=200)

# Пульсация
ctrl.led_pulse("blue", duration=3000)
```

### Вибрация (Plus2)

```python
# Одиночная вибрация
ctrl.vibrate(duration=300, strength=150)

# Паттерн [on_ms, off_ms, on_ms, ...]
ctrl.vibrate_pattern([200, 100, 200, 100, 400])
```

### Предустановки уведомлений

```python
ctrl.notify_success("Done!")
ctrl.notify_error("Failed")
ctrl.notify_warning("Warning")
ctrl.notify_listening()   # Синий пульс + иконка микрофона
ctrl.notify_speaking()    # Зеленый LED + иконка динамика
ctrl.notify_idle()        # Счастливое лицо
```

### Системные команды

```python
ctrl.get_battery()
ctrl.get_temperature()
ctrl.set_brightness(200)  # 0-255
ctrl.sleep(10)            # Сон на 10 секунд
ctrl.beep(1000, 100)      # Звуковой сигнал
ctrl.reset()              # Перезагрузка
ctrl.ping()               # Проверка связи
```

### Данные IMU

```python
imu = ctrl.imu

print(f"Акселерометр: {imu.acc_x:.2f}, {imu.acc_y:.2f}, {imu.acc_z:.2f}")
print(f"Гироскоп: {imu.gyro_x:.2f}, {imu.gyro_y:.2f}, {imu.gyro_z:.2f}")
print(f"Наклон X: {imu.tilt_x:.1f}°")
print(f"Наклон Y: {imu.tilt_y:.1f}°")
print(f"Тряска: {imu.is_shaking}")
```

---

## 🎯 События и жесты

### Регистрация обработчиков

```python
from actions.m5stick import get_controller

ctrl = get_controller()

def on_button(data):
    btn = data["btn"]      # "A", "B"
    action = data["action"]  # "press", "release", "long"
    print(f"Кнопка {btn}: {action}")

def on_gesture(gesture):
    print(f"Жест: {gesture}")  # "shake", "flip", "tilt_left", "tilt_right"

def on_imu(imu_data):
    if imu_data.is_shaking:
        print("Тряска!")

def on_battery(level):
    print(f"Батарея: {level}%")

def on_connect(data):
    print(f"Подключено: {data['port']}")

def on_disconnect(data):
    print("Отключено")

# Регистрация
ctrl.on("button", on_button)
ctrl.on("gesture", on_gesture)
ctrl.on("imu", on_imu)
ctrl.on("battery", on_battery)
ctrl.on("connect", on_connect)
ctrl.on("disconnect", on_disconnect)
```

### Встроенные жесты

| Жест | Описание | Как выполнить |
|------|----------|---------------|
| `shake` | Тряска | Быстро потрясти устройство |
| `flip` | Переворот | Перевернуть вверх ногами |
| `tilt_left` | Наклон влево | Наклонить влево >45° |
| `tilt_right` | Наклон вправо | Наклонить вправо >45° |

### Автоматическая обработка жестов

```python
from actions.m5stick_action import setup_m5stick_handlers

def ai_command_handler(command):
    """Вызывается при событиях M5Stick."""
    if command == "listen":
        # Активировать голосовой ввод
        pass
    elif command == "shake":
        # Обработать тряску
        pass

setup_m5stick_handlers(ai_command_handler)
```

---

## 🔧 Решение проблем

### M5Stick не обнаруживается

**Проблема:** `No M5Stick device found on USB`

**Решение:**
1. Проверьте USB-кабель (некоторые кабели только для зарядки)
2. Попробуйте другой USB-порт
3. Установите драйверы:
   - **Windows:** [CP2104 Driver](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers)
   - **Linux:** обычно встроены, проверьте `ls /dev/ttyUSB*`
   - **macOS:** [CH340 Driver](https://www.wch.cn/downloads/CH341SER_MAC_ZIP.html)

### Ошибка Serial

**Проблема:** `SerialException: could not open port`

**Решение:**
```bash
# Linux: добавить пользователя в группу dialout
sudo usermod -a -G dialout $USER
# Перелогиньтесь после этого

# Проверьте, что порт не занят
lsof /dev/ttyUSB0
```

### Нет данных с IMU

**Проблема:** Все значения IMU равны 0

**Решение:**
1. Проверьте прошивку — должна быть v1.0.0+
2. IMU инициализируется при старте, подождите 1-2 секунды
3. Убедитесь, что библиотека M5StickCPlus установлена

### Экран не реагирует

**Проблема:** Команды отправляются, но экран не меняется

**Решение:**
1. Откройте Serial Monitor в Arduino IDE (115200 baud)
2. Отправьте `{"cmd":"ping"}` — должен ответить `{"type":"pong"}`
3. Если нет ответа — перезагрузите прошивку

### Низкая производительность

**Проблема:** Задержки при обновлении экрана

**Решение:**
```python
# Уменьшите частоту IMU в прошивке
#define IMU_RATE_HZ 10  # вместо 30
```

### Батарея быстро разряжается

**Решение:**
```python
# Уменьшите яркость
ctrl.set_brightness(100)  # вместо 255

# Выключайте экран при бездействии
ctrl.sleep(0)  # глубокий сон до нажатия кнопки
```

---

## 📊 Протокол связи

### Формат

- **Baud rate:** 115200
- **Формат:** JSON (одна строка = одна команда)
- **Кодировка:** UTF-8
- **Разделитель:** `\n` (перевод строки)

### Команды PC → M5

```json
{"cmd": "text", "text": "Hello", "x": 0, "y": 0, "size": 2, "color": "#FFFFFF"}
{"cmd": "clear", "color": "#000000"}
{"cmd": "title", "title": "MARK L", "subtitle": "Connected"}
{"cmd": "face", "expression": "happy"}
{"cmd": "led", "color": "cyan", "brightness": 80}
{"cmd": "blink", "color": "red", "times": 3, "interval": 200}
{"cmd": "pulse", "color": "blue", "duration": 2000}
{"cmd": "progress", "value": 0.75, "label": "Loading"}
{"cmd": "icon", "icon": "mic", "x": 40, "y": 20, "size": 48}
{"cmd": "vibrate", "duration": 200, "strength": 100}
{"cmd": "brightness", "level": 128}
{"cmd": "battery"}
{"cmd": "temperature"}
{"cmd": "sleep", "seconds": 10}
{"cmd": "ping"}
{"cmd": "reset"}
```

### События M5 → PC

```json
{"type": "imu", "ax": 0.1, "ay": -0.05, "az": 0.98, "gx": 1.2, "gy": -0.3, "gz": 0.5}
{"type": "button", "btn": "A", "action": "press"}
{"type": "button", "btn": "B", "action": "long"}
{"type": "gesture", "gesture": "shake"}
{"type": "info", "model": "M5StickC Plus", "fw": "1.0.0", "bat": 85, "temp": 42.5}
{"type": "battery", "level": 85, "voltage": 3.95}
{"type": "temperature", "value": 42.5}
{"type": "pong", "device": "M5StickCPlus", "fw": "1.0.0"}
{"type": "ready", "device": "M5StickCPlus", "fw": "1.0.0"}
```

---

## 💡 Примеры проектов

### Часы с уведомлениями

```python
from actions.m5stick import get_controller
from datetime import datetime
import time

ctrl = get_controller()
ctrl.connect()

while True:
    now = datetime.now().strftime("%H:%M:%S")
    ctrl.display_clear()
    ctrl.display_text(now, 30, 50, size=3, color="#00D4FF")
    time.sleep(1)
```

### Индикатор загрузки

```python
ctrl.display_clear()
for i in range(101):
    ctrl.display_progress(i / 100.0, f"Loading {i}%")
    time.sleep(0.05)
ctrl.notify_success("Done!")
```

### Пульт управления

```python
def on_button(data):
    if data["btn"] == "A" and data["action"] == "press":
        # Следующий трек
        import pyautogui
        pyautogui.press("nexttrack")
        ctrl.led_blink("green", 1)
    elif data["btn"] == "B" and data["action"] == "press":
        # Play/Pause
        pyautogui.press("playpause")
        ctrl.led_blink("blue", 1)

ctrl.on("button", on_button)
```

### Датчик наклона

```python
while True:
    imu = ctrl.imu
    angle = imu.tilt_x
    ctrl.display_clear()
    ctrl.display_text(f"Tilt: {angle:.0f}°", 10, 40, size=3)
    
    if abs(angle) > 30:
        ctrl.led_color("red" if angle > 0 else "blue")
    else:
        ctrl.led_color("green")
    
    time.sleep(0.1)
```

---

## 📁 Структура файлов

```
MARK L/
├── actions/
│   ├── m5stick.py              # Основной контроллер USB
│   └── m5stick_action.py       # Голосовые команды
├── firmware/
│   └── m5stick/
│       └── m5stick_firmware.ino  # Прошивка Arduino
├── M5STICK_README.md           # Этот файл
└── requirements.txt            # Зависимости (pyserial)
```

---

**Разработано для MARK L — персонального AI ассистента**
**M5Stick USB Controller v1.0**
