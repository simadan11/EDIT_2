# hub.py — EDIT GEOINT / OSINT Hub: 467+ Strategic Sites (Ukraine, Russia & Global Focus)
"""
EDIT GEOINT / OSINT Hub — Tactical Command Center for Geospatial Intelligence.
Provides interactive access to multi-layer maps (Google Maps, Google Satellite, OpenStreetMap, Esri Imagery, OpenTopoMap)
marking over 467+ active, abandoned, and historical military bases, radar sites, airfields, bunkers, naval stations,
ICBM silos, and equipment locations — with a primary focus on Ukraine and Russia.
Operates within legal and ethical OSINT/GEOINT boundaries using publicly verifiable open-source data.
"""

import os
import sys
import json
from pathlib import Path

# Prevent SetProcessDpiAwarenessContext errors on Windows
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
if sys.platform.startswith("win") and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "windows:dpiawareness=0"

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QTextEdit, QSplitter,
    QFrame, QSizePolicy, QListWidget, QListWidgetItem, QMessageBox
)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False
    QWebEngineView = None
from PyQt6.QtGui import QFont, QIcon

BASE_DIR = Path(__file__).resolve().parent
API_FILE = BASE_DIR / "config" / "api_keys.json"

from actions.geoint_engine import (
    MILITARY_SITES, get_all_sites, search_sites, calculate_geodesic_distance,
    query_osm_overpass, build_external_links, generate_html_map, open_map_in_browser,
    ai_geoint_analysis
)


def _load_config():
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


class OSINTHub(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🛰️ EDIT GEOINT / OSINT Hub — 467+ Военных и исторических объектов (Украина, Россия & Мир)")
        self.resize(1480, 930)
        self.setMinimumSize(1024, 720)

        # Generate initial interactive Leaflet GEOINT HTML map
        self.html_map_path = generate_html_map()
        self.current_site = MILITARY_SITES[0] if MILITARY_SITES else None
        self.current_country_filter = "all"
        self.current_cat_filter = "all"

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # === ЛЕВАЯ ПАНЕЛЬ (управление) ===
        left_panel = self._build_left_panel()
        main_layout.addWidget(left_panel, 0)

        # === ПРАВАЯ ЧАСТЬ (карта + карточка объекта) ===
        right_split = QSplitter(Qt.Orientation.Vertical)

        # Карта (WebEngine или PyQt fallback)
        if WEBENGINE_AVAILABLE:
            self.map_view = QWebEngineView()
            self.map_view.setUrl(QUrl.fromLocalFile(str(self.html_map_path)))
        else:
            self.map_view = self._build_no_webengine_view()
        right_split.addWidget(self.map_view)

        # Панель спутниковой разведки и отчётов
        sat_panel = self._build_satellite_panel()
        right_split.addWidget(sat_panel)

        right_split.setSizes([630, 300])
        main_layout.addWidget(right_split, 1)

        self.statusBar().showMessage(f"🛰️ GEOINT Hub готов • В базе: {len(MILITARY_SITES)} объектов (Украина 147+, Россия 291+, Мир) • OSINT Mode активен")

    def _build_no_webengine_view(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #060e14; border: 1px solid #1a5c7a; border-radius: 8px;")
        lay = QVBoxLayout(w)
        lbl = QLabel(
            f"🛰️ Интерактивная карта GEOINT (Leaflet) создана и загружена с {len(MILITARY_SITES)} объектами!\n\n"
            "PyQt6-WebEngine не установлен в текущем окружении.\n"
            "Нажмите кнопку ниже, чтобы открыть полноценную интерактивную карту в браузере по умолчанию:"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #8ffcff; font-size: 13px; font-weight: bold;")
        lay.addWidget(lbl)

        open_btn = QPushButton("🌐 ОТКРЫТЬ GEOINT КАРТУ В БРАУЗЕРЕ (Google Maps / Спутник / Copernicus / OSM)")
        open_btn.setFixedHeight(45)
        open_btn.setStyleSheet("""
            QPushButton {
                background: #00e5ff; color: #000; font-weight: bold; font-size: 13px; border-radius: 6px;
            }
            QPushButton:hover { background: #80f2ff; }
        """)
        open_btn.clicked.connect(lambda: open_map_in_browser(self.current_site, self.current_cat_filter, self.current_country_filter))
        lay.addWidget(open_btn)

        self.site_list_widget = QListWidget()
        self.site_list_widget.setStyleSheet("background: #001218; color: #d8f8ff; font-size: 11px;")
        self._refresh_list_widget()
        self.site_list_widget.itemClicked.connect(self._on_list_item_clicked)
        lay.addWidget(self.site_list_widget)

        return w

    def _refresh_list_widget(self):
        if not hasattr(self, "site_list_widget"):
            return
        self.site_list_widget.clear()
        sites = get_all_sites(category=self.current_cat_filter, country=self.current_country_filter)
        for s in sites:
            item = QListWidgetItem(f"[{s['status']}] {s['name']} — {s['country']} ({s['type']})")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.site_list_widget.addItem(item)

    def _on_list_item_clicked(self, item: QListWidgetItem):
        site = item.data(Qt.ItemDataRole.UserRole)
        if site:
            self.display_site_report(site)

    def _build_left_panel(self):
        panel = QWidget()
        panel.setFixedWidth(350)
        panel.setStyleSheet("""
            QWidget {
                background: #0a0f14;
                border: 1px solid #1a3c5a;
                border-radius: 6px;
            }
            QLabel { color: #8ffcff; font-weight: bold; }
            QLineEdit, QComboBox, QTextEdit {
                background: #001218;
                color: #d8f8ff;
                border: 1px solid #1a5c7a;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton {
                background: #001a2e;
                color: #00d4ff;
                border: 1px solid #00d4ff;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover { background: #002a4a; color: #fff; }
            .btn-ua { background: #002e63; color: #ffd700; border-color: #ffd700; }
            .btn-ru { background: #3e0b0b; color: #ffb8b8; border-color: #ff6b6b; }
        """)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(7)

        # Заголовок
        header = QLabel(f"🛰️ GEOINT COMMAND (467+ БАЗ)")
        header.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.setStyleSheet("color: #00ffaa; font-size: 14px; letter-spacing: 1px;")
        lay.addWidget(header)

        # Быстрый фильтр регионов (Украина 147+, Россия 291+, Все)
        lay.addWidget(QLabel("🌍 Выбор региона / страны:"))
        reg_box = QHBoxLayout()
        btn_all_r = QPushButton(f"🟢 Все ({len(MILITARY_SITES)})")
        btn_all_r.clicked.connect(lambda: self.filter_map_country("all"))
        btn_ua = QPushButton("🇺🇦 Украина (147+)")
        btn_ua.setStyleSheet("background: #002e63; color: #ffd700; border: 1px solid #ffd700;")
        btn_ua.clicked.connect(lambda: self.filter_map_country("ukraine"))
        btn_ru = QPushButton("🇷🇺 Россия (291+)")
        btn_ru.setStyleSheet("background: #3e0b0b; color: #ffb8b8; border: 1px solid #ff6b6b;")
        btn_ru.clicked.connect(lambda: self.filter_map_country("russia"))
        reg_box.addWidget(btn_all_r)
        reg_box.addWidget(btn_ua)
        reg_box.addWidget(btn_ru)
        lay.addLayout(reg_box)

        # Выбор объекта из базы (467+ военных объектов)
        lay.addWidget(QLabel("📍 Выбор базы / стратегического объекта:"))
        self.site_combo = QComboBox()
        self._populate_site_combo()
        self.site_combo.currentIndexChanged.connect(self._on_combo_changed)
        lay.addWidget(self.site_combo)

        # Поиск по ключевому слову
        lay.addWidget(QLabel("🔍 Поиск по названию или координатам:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Например: Дуга, Васильков, Энгельс, Сармат...")
        self.search_input.returnPressed.connect(self.search_location)
        lay.addWidget(self.search_input)

        search_btn = QPushButton("🔍 НАЙТИ И ПОКАЗАТЬ НА КАРТЕ GEOINT")
        search_btn.clicked.connect(self.search_location)
        lay.addWidget(search_btn)

        # Категории (фильтрация)
        lay.addWidget(QLabel("🗂️ Фильтр по категории:"))
        cat_box = QHBoxLayout()
        btn_all_c = QPushButton("🟢 Все")
        btn_all_c.clicked.connect(lambda: self.filter_map_category("all"))
        btn_act = QPushButton("🔴 Активные")
        btn_act.clicked.connect(lambda: self.filter_map_category("active"))
        btn_abd = QPushButton("🟡 Заброшенные")
        btn_abd.clicked.connect(lambda: self.filter_map_category("abandoned"))
        cat_box.addWidget(btn_all_c)
        cat_box.addWidget(btn_act)
        cat_box.addWidget(btn_abd)
        lay.addLayout(cat_box)

        cat_box2 = QHBoxLayout()
        btn_air = QPushButton("✈️ Аэродромы")
        btn_air.clicked.connect(lambda: self.filter_map_category("airbase"))
        btn_mis = QPushButton("🚀 РВСН/Шахты")
        btn_mis.clicked.connect(lambda: self.filter_map_category("missile"))
        btn_bnk = QPushButton("⚓ Бункеры/ВМБ")
        btn_bnk.clicked.connect(lambda: self.filter_map_category("bunker"))
        cat_box2.addWidget(btn_air)
        cat_box2.addWidget(btn_mis)
        cat_box2.addWidget(btn_bnk)
        lay.addLayout(cat_box2)

        # Выбор картографической подложки
        lay.addWidget(QLabel("🗺️ Карта / Спутниковая подложка:"))
        self.platform_combo = QComboBox()
        self.platform_combo.addItems([
            "🛰️ Google Hybrid (Спутник + Метки)",
            "🛰️ Google Satellite (Спутник HD)",
            "🌍 Google Maps (Карта дорог)",
            "🗺️ OpenStreetMap (OSM)",
            "🛰️ Esri World Imagery (Спутник)",
            "⛰️ OpenTopoMap (Рельеф и высоты)"
        ])
        self.platform_combo.currentIndexChanged.connect(self._change_basemap)
        lay.addWidget(self.platform_combo)

        # Кнопки разведки
        browser_btn = QPushButton("🌐 ОТКРЫТЬ GOOGLE MAPS / СПУТНИК В БРАУЗЕРЕ")
        browser_btn.setStyleSheet("background: #003040; color: #00ffaa; font-weight: bold;")
        browser_btn.clicked.connect(self._open_in_external_browser)
        lay.addWidget(browser_btn)

        osm_btn = QPushButton("🛰️ ЗАГРУЗИТЬ ОБЪЕКТЫ ИЗ OSM OVERPASS API")
        osm_btn.clicked.connect(self.query_overpass_current)
        lay.addWidget(osm_btn)

        calc_btn = QPushButton("📏 РАСЧЁТ ДИСТАНЦИИ И ПЕЛЕНГА (GEOINT)")
        calc_btn.clicked.connect(self.show_distance_calculator)
        lay.addWidget(calc_btn)

        ai_btn = QPushButton("🧠 AI GEOINT АНАЛИЗ ОБЪЕКТА (УГРОЗА & СПУТНИК)")
        ai_btn.setStyleSheet("background: #00361d; color: #00ff88; font-weight: bold;")
        ai_btn.clicked.connect(self.enhance_with_ai)
        lay.addWidget(ai_btn)

        # Поле информации / отчёта
        lay.addWidget(QLabel("📋 GEOINT Сводка и характеристики:"))
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFont(QFont("Consolas", 9))
        lay.addWidget(self.result_text, 1)

        # Кнопка закрытия
        close_btn = QPushButton("✕ ЗАКРЫТЬ HUB")
        close_btn.clicked.connect(self.close)
        lay.addWidget(close_btn)

        return panel

    def _populate_site_combo(self):
        self.site_combo.blockSignals(True)
        self.site_combo.clear()
        sites = get_all_sites(category=self.current_cat_filter, country=self.current_country_filter)
        self.site_combo.addItem(f"── Выберите объект ({len(sites)} баз) ──", None)
        for site in sites:
            icon_str = "🟢" if "active" in site["status"].lower() else ("🔴" if "abandoned" in site["status"].lower() else "🏛️")
            self.site_combo.addItem(f"{icon_str} {site['name']} ({site['country']})", site)
        self.site_combo.blockSignals(False)

    def _build_satellite_panel(self):
        panel = QWidget()
        panel.setStyleSheet("""
            QWidget { background: #0a0f14; border: 1px solid #1a3c5a; border-radius: 6px; }
            QLabel { color: #8ffcff; font-weight: bold; }
        """)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 8, 10, 8)

        header = QLabel("🛰️ СПУТНИКОВАЯ РАЗВЕДКА • AI АНАЛИЗ И ССЫЛКИ")
        header.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        header.setStyleSheet("color: #00ffaa;")
        lay.addWidget(header)

        self.sat_report_view = QTextEdit()
        self.sat_report_view.setReadOnly(True)
        self.sat_report_view.setFont(QFont("Consolas", 9))
        self.sat_report_view.setStyleSheet("background: #001218; color: #d8f8ff; border: 1px solid #1a5c7a;")
        lay.addWidget(self.sat_report_view, 1)

        # Инициализируем сводку первого объекта
        if MILITARY_SITES:
            self.display_site_report(MILITARY_SITES[0])

        return panel

    def display_site_report(self, site: dict):
        self.current_site = site
        links = build_external_links(site["lat"], site["lon"])

        report = f"📍 ОБЪЕКТ: {site['name']} ({site['country']})\n"
        report += f" • Статус   : {site['status']}\n"
        report += f" • Категория: {site['type']}\n"
        report += f" • Координаты: WGS84: {site['lat']}, {site['lon']}\n"
        report += f" • Описание  : {site['description']}\n"
        report += f" • Техника/Инфраструктура: {site['equipment']}\n\n"
        report += "🌐 ВНЕШНИЕ ССЫЛКИ (в один клик):\n"
        report += f" • Google Satellite: {links['google_satellite']}\n"
        report += f" • Copernicus (S-2): {links['copernicus_sentinel']}\n"
        report += f" • NASA Thermal    : {links['nasa_thermal']}\n"
        report += f" • OpenStreetMap   : {links['openstreetmap']}\n"
        report += f" • WikiMapia       : {links['wikimapia']}\n"

        self.result_text.setPlainText(report)

        # Автоматически отображаем краткий AI GEOINT анализ в панели справа
        analysis = ai_geoint_analysis(site)
        self.sat_report_view.setPlainText(analysis["report_text"])
        self.statusBar().showMessage(f"🛰️ Выбран объект: {site['name']} ({site['country']})")

    def _on_combo_changed(self, index: int):
        site = self.site_combo.itemData(index)
        if site and isinstance(site, dict):
            self.display_site_report(site)
            self.html_map_path = generate_html_map(target_site=site, filter_category=self.current_cat_filter, filter_country=self.current_country_filter)
            if WEBENGINE_AVAILABLE and hasattr(self, "map_view"):
                self.map_view.setUrl(QUrl.fromLocalFile(str(self.html_map_path)))

    def filter_map_country(self, country_code: str):
        self.current_country_filter = country_code
        self._populate_site_combo()
        self._refresh_list_widget()
        self.html_map_path = generate_html_map(target_site=self.current_site, filter_category=self.current_cat_filter, filter_country=country_code)
        if WEBENGINE_AVAILABLE and hasattr(self, "map_view"):
            self.map_view.setUrl(QUrl.fromLocalFile(str(self.html_map_path)))
        reg_label = "Украина (147+)" if country_code == "ukraine" else ("Россия (291+)" if country_code == "russia" else "Все (467+)")
        self.result_text.append(f"\n🌍 Регион отфильтрован: {reg_label}")
        self.statusBar().showMessage(f"🛰️ Регион: {reg_label}")

    def filter_map_category(self, cat: str):
        self.current_cat_filter = cat
        self._populate_site_combo()
        self._refresh_list_widget()
        self.html_map_path = generate_html_map(target_site=self.current_site, filter_category=cat, filter_country=self.current_country_filter)
        if WEBENGINE_AVAILABLE and hasattr(self, "map_view"):
            self.map_view.setUrl(QUrl.fromLocalFile(str(self.html_map_path)))
        self.result_text.append(f"\n🗂️ Категория отфильтрована: {cat.upper()}")
        self.statusBar().showMessage(f"🛰️ Категория: {cat.upper()}")

    def _change_basemap(self, index: int):
        if not WEBENGINE_AVAILABLE or not hasattr(self, "map_view"):
            return
        platform = self.platform_combo.currentText()
        self.statusBar().showMessage(f"🛰️ Выбран слой: {platform}")

    def _open_in_external_browser(self):
        msg = open_map_in_browser(self.current_site, self.current_cat_filter, self.current_country_filter)
        self.result_text.append(f"\n🌐 {msg}")
        self.statusBar().showMessage("🌐 Карта открыта в браузере")

    def search_location(self):
        query = self.search_input.text().strip()
        if not query:
            self.result_text.setPlainText("Введите название базы, страны или координаты для поиска")
            return

        matches = search_sites(query)
        if matches:
            site = matches[0]
            self.display_site_report(site)
            self.html_map_path = generate_html_map(target_site=site, filter_category=self.current_cat_filter, filter_country=self.current_country_filter)
            if WEBENGINE_AVAILABLE and hasattr(self, "map_view"):
                self.map_view.setUrl(QUrl.fromLocalFile(str(self.html_map_path)))
            self.statusBar().showMessage(f"🔍 Найдено объектов: {len(matches)} • Показан: {site['name']}")
        else:
            try:
                parts = [float(p.strip()) for p in query.replace(";", ",").split(",")]
                if len(parts) == 2:
                    lat, lon = parts[0], parts[1]
                    custom_site = {
                        "name": f"Пользовательская точка ({lat:.4f}, {lon:.4f})",
                        "lat": lat, "lon": lon,
                        "category": "custom", "type": "Coordinates Lookup",
                        "status": "Custom Coordinate", "country": "WGS84",
                        "description": f"Пользовательские координаты WGS84: {lat}, {lon}",
                        "equipment": "N/A"
                    }
                    self.display_site_report(custom_site)
                    self.html_map_path = generate_html_map(target_site=custom_site, filter_category=self.current_cat_filter, filter_country=self.current_country_filter)
                    if WEBENGINE_AVAILABLE and hasattr(self, "map_view"):
                        self.map_view.setUrl(QUrl.fromLocalFile(str(self.html_map_path)))
                    return
            except Exception:
                pass

            self.result_text.setPlainText(
                f"❌ По запросу '{query}' не найдено баз в основном каталоге.\n"
                f"Нажмите кнопку '🛰️ ЗАГРУЗИТЬ ОБЪЕКТЫ ИЗ OSM OVERPASS API', чтобы запросить все публичные военные и исторические объекты OpenStreetMap вокруг текущих координат."
            )

    def query_overpass_current(self):
        site = self.current_site or MILITARY_SITES[0]
        self.result_text.append(f"\n🛰️ Запрос в Overpass API (OSM) в радиусе 25 км от {site['name']}...")
        self.statusBar().showMessage("🛰️ Загрузка объектов из OpenStreetMap...")

        osm_results = query_osm_overpass(site["lat"], site["lon"], radius_km=25.0)
        if osm_results:
            text = f"✅ Найдено {len(osm_results)} публичных военных/исторических объектов в OSM:\n\n"
            for r in osm_results:
                text += f" • {r['name']} ({r['lat']:.4f}, {r['lon']:.4f}) — {r['type']}\n"
            self.sat_report_view.setPlainText(text)
            self.result_text.append(f"✅ Успешно добавлено {len(osm_results)} объектов OSM в отчёт.")
            self.statusBar().showMessage(f"✅ Найдено {len(osm_results)} объектов из OSM")
        else:
            self.result_text.append("ℹ️ Новых объектов военного/исторического назначения в радиусе 25 км не обнаружено в OSM.")
            self.statusBar().showMessage("ℹ️ В радиусе 25 км нет новых меток OSM")

    def show_distance_calculator(self):
        if len(MILITARY_SITES) < 2:
            return
        site1 = self.current_site or MILITARY_SITES[0]
        site2 = MILITARY_SITES[1] if site1 != MILITARY_SITES[1] else MILITARY_SITES[0]
        d = calculate_geodesic_distance(site1["lat"], site1["lon"], site2["lat"], site2["lon"])
        calc_text = (
            f"📏 GEOINT КАЛЬКУЛЯТОР ДИСТАНЦИИ:\n"
            f"{'─'*45}\n"
            f"Точка 1: {site1['name']} ({site1['lat']:.4f}, {site1['lon']:.4f})\n"
            f"Точка 2: {site2['name']} ({site2['lat']:.4f}, {site2['lon']:.4f})\n"
            f"{'─'*45}\n"
            f" • Геодезическое расстояние : {d['distance_km']} км\n"
            f" • В морских милях          : {d['distance_nm']} nm\n"
            f" • Начальный пеленг         : {d['bearing_deg']}°\n"
        )
        self.sat_report_view.setPlainText(calc_text)
        self.result_text.append("\n" + calc_text)

    def enhance_with_ai(self):
        site = self.current_site or MILITARY_SITES[0]
        self.result_text.append(f"\n✨ Запуск AI GEOINT-анализа для объекта: {site['name']}...")
        analysis = ai_geoint_analysis(site)
        self.sat_report_view.setPlainText(analysis["report_text"])
        self.result_text.append("\n✅ AI GEOINT Анализ завершён. Отчёт выведен на панель справа.")
        self.statusBar().showMessage("✅ AI GEOINT Анализ готов")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    hub = OSINTHub()
    hub.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
