"""Offline parts catalog and safety-first diagnostics for Holo Lab.

The catalog is intentionally a practical starter BOM rather than a promise that
it contains every part sold in the world. It keeps the lab useful without
silently ordering hardware or energising a circuit. Projects can add their own
part names and the diagnostics always call out what still needs to be verified
on the bench.
"""

from __future__ import annotations

import re
from typing import Iterable


# Common parts for optical displays, cameras, embedded computers, power,
# mechanics and safe bench testing. ``source`` tells the user whether this is
# normally bought or can be fabricated/printed locally.
PART_CATALOG: tuple[dict, ...] = (
    # Computing / control
    {"id": "esp32_s3", "name": "ESP32-S3 development board", "category": "CONTROL", "source": "BUY", "supplier": "local electronics shop / DigiKey / Mouser", "price": 8, "spec": "Wi-Fi/BLE, low-power controller"},
    {"id": "arduino_nano_esp32", "name": "Arduino Nano ESP32", "category": "CONTROL", "source": "BUY", "supplier": "Arduino / local electronics shop", "price": 25, "spec": "easy prototyping controller"},
    {"id": "raspberry_pi_zero2", "name": "Raspberry Pi Zero 2 W", "category": "COMPUTE", "source": "BUY", "supplier": "Raspberry Pi reseller / local shop", "price": 25, "spec": "Linux, camera and network gateway"},
    {"id": "raspberry_pi5", "name": "Raspberry Pi 5", "category": "COMPUTE", "source": "BUY", "supplier": "Raspberry Pi reseller / local shop", "price": 60, "spec": "higher-performance Linux computer"},
    {"id": "jetson_orin_nano", "name": "Jetson Orin Nano developer kit", "category": "COMPUTE", "source": "BUY", "supplier": "NVIDIA partner / specialist shop", "price": 250, "spec": "GPU vision prototype; needs cooling"},
    {"id": "usb_logic_analyzer", "name": "8-channel USB logic analyzer", "category": "TEST", "source": "BUY", "supplier": "electronics shop / AliExpress", "price": 12, "spec": "digital bus debugging"},

    # Vision / sensors
    {"id": "ov2640_camera", "name": "OV2640 camera module", "category": "VISION", "source": "BUY", "supplier": "electronics shop", "price": 8, "spec": "compact camera for ESP32"},
    {"id": "pi_camera3", "name": "Raspberry Pi Camera Module 3", "category": "VISION", "source": "BUY", "supplier": "Raspberry Pi reseller", "price": 25, "spec": "autofocus camera"},
    {"id": "usb_webcam", "name": "USB UVC webcam", "category": "VISION", "source": "BUY", "supplier": "local computer shop", "price": 20, "spec": "fastest PC proof-of-concept camera"},
    {"id": "esp32_cam", "name": "ESP32-CAM board", "category": "VISION", "source": "BUY", "supplier": "electronics shop", "price": 10, "spec": "camera + Wi-Fi board"},
    {"id": "imu_bmi270", "name": "BMI270 / MPU-6050 IMU", "category": "SENSORS", "source": "BUY", "supplier": "electronics shop", "price": 6, "spec": "accelerometer + gyroscope"},
    {"id": "tof_vl53l0x", "name": "VL53L0X time-of-flight sensor", "category": "SENSORS", "source": "BUY", "supplier": "electronics shop", "price": 8, "spec": "short-range distance"},
    {"id": "bme280", "name": "BME280 environmental sensor", "category": "SENSORS", "source": "BUY", "supplier": "electronics shop", "price": 5, "spec": "temperature / humidity / pressure"},
    {"id": "photoresistor", "name": "Ambient light sensor / photoresistor", "category": "SENSORS", "source": "BUY", "supplier": "electronics shop", "price": 1, "spec": "automatic display brightness"},
    {"id": "mic_inmp441", "name": "INMP441 I2S microphone", "category": "AUDIO", "source": "BUY", "supplier": "electronics shop", "price": 5, "spec": "digital microphone"},

    # Displays / optics
    {"id": "oled_ssd1306", "name": "0.96-inch SSD1306 OLED", "category": "DISPLAY", "source": "BUY", "supplier": "electronics shop", "price": 5, "spec": "simple status display"},
    {"id": "oled_amoled", "name": "Small AMOLED / micro-OLED display", "category": "DISPLAY", "source": "BUY", "supplier": "specialist display supplier", "price": 80, "spec": "near-eye display prototype"},
    {"id": "hdmi_microdisplay", "name": "HDMI micro-display module", "category": "DISPLAY", "source": "BUY", "supplier": "specialist display supplier", "price": 100, "spec": "video input; verify latency and optics"},
    {"id": "combiner_acrylic", "name": "Optical acrylic combiner", "category": "OPTICS", "source": "BUY/MAKE", "supplier": "optics supplier or laser-cut locally", "price": 15, "spec": "transparent HUD combiner experiment"},
    {"id": "fresnel_lens", "name": "Fresnel / magnifier lens", "category": "OPTICS", "source": "BUY", "supplier": "optics shop", "price": 5, "spec": "display magnification experiment"},
    {"id": "beam_splitter", "name": "Non-coated beam-splitter sample", "category": "OPTICS", "source": "BUY", "supplier": "optics supplier", "price": 35, "spec": "test only; never point at eyes/lasers"},
    {"id": "led_ring", "name": "WS2812B addressable LED ring", "category": "LIGHTING", "source": "BUY", "supplier": "electronics shop", "price": 5, "spec": "safe status lighting; not a projector"},

    # Power / protection
    {"id": "lipo_1000mah", "name": "Protected 3.7 V Li-Po 1000 mAh", "category": "POWER", "source": "BUY", "supplier": "reputable battery supplier", "price": 12, "spec": "single-cell portable power"},
    {"id": "lipo_charger", "name": "Li-Po charger with protection", "category": "POWER", "source": "BUY", "supplier": "reputable electronics supplier", "price": 5, "spec": "use a protected charging board"},
    {"id": "buck_5v", "name": "Buck converter / regulator", "category": "POWER", "source": "BUY", "supplier": "electronics shop", "price": 4, "spec": "stable regulated rail"},
    {"id": "boost_5v", "name": "5 V boost converter", "category": "POWER", "source": "BUY", "supplier": "electronics shop", "price": 4, "spec": "raise single-cell voltage"},
    {"id": "polyfuse", "name": "Resettable fuse / polyfuse", "category": "SAFETY", "source": "BUY", "supplier": "electronics shop", "price": 1, "spec": "over-current protection"},
    {"id": "power_switch", "name": "Latching power switch", "category": "POWER", "source": "BUY", "supplier": "electronics shop", "price": 2, "spec": "hard power disconnect"},
    {"id": "usb_c_breakout", "name": "USB-C power breakout with ESD protection", "category": "POWER", "source": "BUY", "supplier": "electronics shop", "price": 4, "spec": "safe bench input"},
    {"id": "battery_holder", "name": "Protected battery holder / enclosure", "category": "SAFETY", "source": "BUY/MAKE", "supplier": "local shop or 3-D print", "price": 5, "spec": "mechanical battery isolation"},
    {"id": "lipo_safety_bag", "name": "Li-Po fire-resistant safety bag", "category": "SAFETY", "source": "BUY", "supplier": "model/RC shop", "price": 12, "spec": "charge and store Li-Po safely"},

    # Audio / interaction
    {"id": "max98357_amp", "name": "MAX98357 I2S amplifier", "category": "AUDIO", "source": "BUY", "supplier": "electronics shop", "price": 6, "spec": "small mono speaker amplifier"},
    {"id": "mini_speaker", "name": "4–8 ohm miniature speaker", "category": "AUDIO", "source": "BUY", "supplier": "electronics shop", "price": 4, "spec": "voice feedback"},
    {"id": "push_button", "name": "Momentary push button", "category": "INPUT", "source": "BUY", "supplier": "electronics shop", "price": 1, "spec": "manual trigger / reset"},
    {"id": "rotary_encoder", "name": "Rotary encoder with push switch", "category": "INPUT", "source": "BUY", "supplier": "electronics shop", "price": 3, "spec": "menu/brightness control"},
    {"id": "haptic_motor", "name": "Coin vibration motor", "category": "FEEDBACK", "source": "BUY", "supplier": "electronics shop", "price": 2, "spec": "low-power haptic alert"},

    # Prototyping / fabrication
    {"id": "breadboard", "name": "Solderless breadboard + jumper wires", "category": "PROTO", "source": "BUY", "supplier": "electronics shop", "price": 8, "spec": "temporary low-voltage prototype"},
    {"id": "perfboard", "name": "Perfboard / prototype PCB", "category": "PROTO", "source": "BUY/MAKE", "supplier": "electronics shop or PCB house", "price": 3, "spec": "semi-permanent low-voltage build"},
    {"id": "custom_pcb", "name": "Custom PCB", "category": "PROTO", "source": "MAKE", "supplier": "PCB fabrication service", "price": 20, "spec": "design, review and order after bench test"},
    {"id": "pla_petg", "name": "PLA/PETG 3-D printer filament", "category": "MECHANICAL", "source": "BUY", "supplier": "3-D printing supplier", "price": 25, "spec": "prototype enclosure"},
    {"id": "printed_enclosure", "name": "3-D printed enclosure / bracket", "category": "MECHANICAL", "source": "MAKE", "supplier": "make locally or makerspace", "price": 5, "spec": "fit and mounting prototype"},
    {"id": "acrylic_sheet", "name": "Laser-cut acrylic sheet", "category": "MECHANICAL", "source": "BUY/MAKE", "supplier": "makerspace / laser-cut service", "price": 10, "spec": "non-structural mock-up"},
    {"id": "m2_m3_fasteners", "name": "M2/M3 screws, standoffs and nuts", "category": "MECHANICAL", "source": "BUY", "supplier": "hardware shop", "price": 8, "spec": "serviceable assembly"},
    {"id": "velcro_strap", "name": "Hook-and-loop strap / cable tie kit", "category": "MECHANICAL", "source": "BUY", "supplier": "hardware shop", "price": 5, "spec": "temporary wearable mounting"},
    {"id": "heat_shrink", "name": "Heat-shrink tubing and strain relief", "category": "SAFETY", "source": "BUY", "supplier": "electronics shop", "price": 4, "spec": "insulate exposed conductors"},

    # Measurement / safety
    {"id": "multimeter", "name": "Digital multimeter", "category": "TEST", "source": "BUY", "supplier": "hardware/electronics shop", "price": 25, "spec": "measure voltage, current and continuity"},
    {"id": "bench_psu", "name": "Current-limited bench power supply", "category": "TEST", "source": "BUY", "supplier": "electronics test supplier", "price": 80, "spec": "first power-up without a battery"},
    {"id": "usb_power_meter", "name": "USB voltage/current power meter", "category": "TEST", "source": "BUY", "supplier": "electronics shop", "price": 8, "spec": "quick power budget check"},
    {"id": "thermocouple", "name": "Thermocouple / IR thermometer", "category": "TEST", "source": "BUY", "supplier": "hardware shop", "price": 20, "spec": "thermal monitoring"},
    {"id": "safety_glasses", "name": "Safety glasses", "category": "SAFETY", "source": "BUY", "supplier": "hardware shop", "price": 8, "spec": "bench protection"},
    {"id": "soldering_station", "name": "Temperature-controlled soldering station", "category": "TEST", "source": "BUY", "supplier": "electronics tool supplier", "price": 45, "spec": "assembly and rework"},
)

PARTS_BY_ID = {part["id"]: part for part in PART_CATALOG}


def catalog_text() -> str:
    """Compact catalog text for a UI or assistant response."""
    lines = []
    for part in PART_CATALOG:
        lines.append(
            f"{part['id']} | {part['category']} | {part['source']} | "
            f"${part['price']} | {part['name']} — {part['spec']}"
        )
    return "\n".join(lines)


def normalize_part_ids(values: Iterable[str] | None) -> list[str]:
    """Keep only known catalog IDs, preserving order and removing duplicates."""
    result = []
    seen = set()
    for value in values or ():
        part_id = str(value or "").strip()
        if part_id in PARTS_BY_ID and part_id not in seen:
            seen.add(part_id)
            result.append(part_id)
    return result


def parts_for_ids(values: Iterable[str] | None) -> list[dict]:
    return [PARTS_BY_ID[part_id] for part_id in normalize_part_ids(values)]


def suggested_part_ids(project: dict | None) -> list[str]:
    """Suggest a safe starter BOM without claiming it is build-ready."""
    project = project or {}
    model = str(project.get("model") or "custom").lower()
    text = " ".join(str(project.get(key) or "") for key in ("subject", "name", "notes", "blueprint")).lower()
    ids = ["esp32_s3", "bench_psu", "multimeter", "breadboard", "heat_shrink", "safety_glasses"]
    if model in ("glasses",) or any(word in text for word in ("camera", "vision", "optics", "очки", "глаз")):
        ids += ["pi_camera3", "imu_bmi270", "photoresistor", "combiner_acrylic", "oled_amoled"]
    if model == "glove" or any(word in text for word in ("glove", "перчат", "gesture", "жест")):
        ids += ["imu_bmi270", "push_button", "haptic_motor", "printed_enclosure", "velcro_strap"]
    if model == "suit" or any(word in text for word in ("suit", "костюм", "wearable", "robot", "робот")):
        ids += ["imu_bmi270", "bme280", "printed_enclosure", "velcro_strap", "haptic_motor"]
    if any(word in text for word in ("display", "hud", "hologram", "голограм", "screen", "экран")):
        ids += ["oled_amoled", "combiner_acrylic", "fresnel_lens"]
    if any(word in text for word in ("battery", "portable", "wireless", "батар", "портатив")):
        ids += ["lipo_1000mah", "lipo_charger", "buck_5v", "polyfuse", "lipo_safety_bag"]
    return normalize_part_ids(ids)


def diagnose_project(project: dict | None, selected_ids: Iterable[str] | None = None,
                     symptom: str = "") -> list[dict]:
    """Return actionable bench checks; never says that hardware is safe without tests."""
    project = project or {}
    ids = set(normalize_part_ids(selected_ids))
    geometry = [item for item in (project.get("geometry") or []) if isinstance(item, dict)]
    ids.update(normalize_part_ids(item.get("part_id") for item in geometry))
    parts = parts_for_ids(ids)
    categories = {part["category"] for part in parts}
    text = " ".join(str(project.get(key) or "") for key in ("model", "subject", "name", "notes", "blueprint")).lower()
    issues: list[dict] = []

    def add(severity: str, problem: str, fix: str, test: str):
        issues.append({"severity": severity, "problem": problem, "fix": fix, "test": test})

    if not geometry:
        add("WARN", "No physical part is placed in the assembly scene.", "Open ASSEMBLY EDITOR, add selected BOM parts to the scene, then drag them into their intended locations.", "Check mounting, cable routes, service access and clearance before fabrication.")
    else:
        placed_ids = {str(item.get("part_id")) for item in geometry if item.get("part_id")}
        for part_id in sorted(ids - placed_ids):
            add("INFO", f"BOM part {part_id} is selected but not placed in the scene.", "Add it to the scene or mark it as an off-board/service part in the build notes.", "Confirm the final enclosure still has room for it.")
        # A rough 2-D clearance check catches obvious overlaps. It is a review
        # aid, not a substitute for a dimensioned CAD model or datasheets.
        def num(item, key, fallback=0.0):
            try:
                return float(item.get(key, fallback))
            except (TypeError, ValueError):
                return fallback
        collision_reported = False
        for left_index, left in enumerate(geometry):
            if str(left.get("type")) in ("line", "point", "ring"):
                continue
            for right in geometry[left_index + 1:]:
                if str(right.get("type")) in ("line", "point", "ring"):
                    continue
                x_overlap = abs(num(left, "x", 500) - num(right, "x", 500)) < (num(left, "w", 100) + num(right, "w", 100)) * .42
                y_overlap = abs(num(left, "y", 500) - num(right, "y", 500)) < (num(left, "h", 100) + num(right, "h", 100)) * .34
                z_overlap = abs(num(left, "z", 0) - num(right, "z", 0)) < (num(left, "d", 50) + num(right, "d", 50)) * .5 + 5
                if x_overlap and y_overlap and z_overlap:
                    a = left.get("label") or left.get("part_id") or "part A"
                    b = right.get("label") or right.get("part_id") or "part B"
                    add("WARN", f"Possible assembly overlap: {a} / {b}.", "Move one part, add a bracket/spacer, or verify that the overlap is intentional and serviceable.", "Measure the real footprints and leave room for connectors, screws and airflow.")
                    collision_reported = True
                    break
            if collision_reported:
                break

    if not ids:
        add("WARN", "No physical parts are selected.", "Open PARTS CATALOG and select a controller, power path, protection, sensors and the requested output parts.", "Start with a current-limited bench supply, not a Li-Po.")
    if not (ids & {"esp32_s3", "arduino_nano_esp32", "raspberry_pi_zero2", "raspberry_pi5", "jetson_orin_nano", "esp32_cam"}):
        add("ERROR", "No controller or computer is selected.", "Add ESP32-S3 for a low-power controller or Raspberry Pi for camera/Linux work.", "Check the board boots and measure its idle current first.")
    if "POWER" not in categories:
        add("WARN", "No power/regulator part is selected.", "Add a current-limited bench PSU for first tests; add a regulator before portable power.", "Verify voltage and current at the board with a multimeter.")
    if "SAFETY" not in categories and ("POWER" in categories or "battery" in text or "portable" in text):
        add("ERROR", "Battery/power path has no explicit protection part.", "Add a polyfuse, protected enclosure, strain relief and a Li-Po safety bag where applicable.", "Power off, check polarity and continuity, then use current limiting.")
    if "battery" in text or "portable" in text or "lipo_1000mah" in ids:
        if "lipo_charger" not in ids:
            add("ERROR", "A Li-Po is mentioned but no protected charger is selected.", "Never charge a Li-Po with a random 5 V source; use a charger with protection matched to the cell.", "Check the charger output and cell polarity with no load.")
        if "polyfuse" not in ids:
            add("WARN", "Portable power has no resettable over-current protection.", "Place a correctly rated fuse close to the battery positive lead.", "Test current limit before connecting the rest of the circuit.")
    if any(word in text for word in ("camera", "vision", "optics", "очки", "глаз")) and not (ids & {"ov2640_camera", "pi_camera3", "usb_webcam", "esp32_cam"}):
        add("WARN", "The design asks for vision but no camera is selected.", "Add a camera module and verify its voltage, connector and field of view.", "Test the camera independently and confirm frames arrive before mounting it.")
    if any(word in text for word in ("display", "hud", "hologram", "голограм", "screen", "экран")) and not (ids & {"oled_ssd1306", "oled_amoled", "hdmi_microdisplay", "combiner_acrylic"}):
        add("WARN", "The design asks for a display/HUD but no display or optical part is selected.", "Add a display and, for near-eye work, an optical combiner; validate brightness and fit.", "Test brightness at low power and do not place an untested optic over the eye.")
    if ids & {"raspberry_pi5", "jetson_orin_nano"} and "thermocouple" not in ids:
        add("WARN", "A high-performance computer is selected without a thermal measurement plan.", "Add a heatsink/fan in the mechanical design and a thermometer for the first run.", "Stop if the enclosure becomes hot; measure temperature under the intended load.")
    if "breadboard" not in ids and "perfboard" not in ids and "custom_pcb" not in ids:
        add("INFO", "There is no temporary prototyping surface in the BOM.", "Use a breadboard first; only order a custom PCB after the wiring is tested.", "Document the pinout and photograph the working prototype.")
    if "multimeter" not in ids:
        add("WARN", "A multimeter is not selected.", "Add one before powering a custom circuit.", "Check continuity, polarity, rail voltage and current draw.")

    symptom = str(symptom or "").lower()
    if symptom:
        symptom_rules = (
            (("black", "blank", "no image", "черн", "нет картинки"), "No display/image is reported.", "Check power rail, ground, display cable/protocol and brightness; test the display alone."),
            (("camera", "vision", "камера", "не видит"), "The camera/vision path may be failing.", "Check camera permissions, connector orientation, voltage and whether a standalone frame arrives."),
            (("hot", "heat", "перегрев", "гре"), "A thermal problem is reported.", "Power down, remove the battery, improve airflow and measure temperature before another test."),
            (("battery", "charge", "батар", "заряд"), "A battery/charging problem is reported.", "Do not bypass protection; check charger, polarity, cell damage and current limit with a multimeter."),
            (("wifi", "bluetooth", "сеть", "интернет"), "A wireless link problem is reported.", "Test the controller alone, confirm firmware credentials and check antenna placement."),
            (("reset", "restart", "перезагруз", "сброс"), "The controller is resetting or brown-outing.", "Measure the rail during the load spike and use a regulator with enough current headroom."),
        )
        for words, problem, fix in symptom_rules:
            if any(word in symptom for word in words):
                add("ERROR", problem, fix, "Repeat the test with one subsystem at a time and record voltage/current.")
                break
        else:
            add("INFO", f"Symptom recorded: {symptom[:140]}", "Disconnect power, reproduce it with the smallest subsystem and attach measurements/logs.", "Do not keep retrying a hot, damaged or swollen battery.")

    if not issues:
        add("INFO", "No obvious catalog conflict was found.", "Proceed in small bench-tested subsystems; this is not certification or a finished build review.", "Record voltage, current, temperature and a pass/fail result for each test.")
    return issues


def format_diagnostics(issues: Iterable[dict]) -> str:
    lines = ["HOLO LAB DIAGNOSTICS", "=" * 24]
    for i, issue in enumerate(issues, 1):
        lines += [
            f"{i:02d}. [{issue.get('severity', 'INFO')}] {issue.get('problem', '')}",
            f"    FIX:  {issue.get('fix', '')}",
            f"    TEST: {issue.get('test', '')}",
        ]
    return "\n".join(lines)
