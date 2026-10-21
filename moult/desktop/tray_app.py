from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from typing import Optional

from moult.engine.sync_service import SyncService
from moult.models import DeviceRole, SyncMode
from moult.platform.windows_integration import open_in_file_manager, set_windows_autostart

SYNC_MODE_LABELS = {
    SyncMode.SEND_RECEIVE.value: "↔ Двусторонняя (Send & Receive)",
    SyncMode.SEND_ONLY.value: "→ Только отправка / Слив файлов (Send Only)",
    SyncMode.RECEIVE_ONLY.value: "← Только приём / Мастер-копия (Receive Only)",
    SyncMode.RECEIVE_ENCRYPTED.value: "🔒 Зашифрованное хранение (Encrypted)",
}


def run_desktop_tray_app(service: Optional[SyncService] = None, web_port: int = 8765) -> int:
    """
    Запускает нативное Windows-приложение Moult Диск с иконкой в системном трее (PySide6 / Qt6).
    Если PySide6 не установлен в окружении, открывает локальную панель управления в браузере.
    """
    svc = service or SyncService()

    try:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
        from PySide6.QtWidgets import (
            QApplication,
            QComboBox,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMenu,
            QMessageBox,
            QPushButton,
            QSystemTrayIcon,
            QTableWidget,
            QTableWidgetItem,
            QTabWidget,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        url = f"http://127.0.0.1:{web_port}"
        print(
            f"[Moult Диск] PySide6 не найден. Открываем панель управления в браузере: {url}\n"
            "Для запуска нативного трея на Windows установите: pip install -r requirements-windows.txt"
        )
        webbrowser.open(url)
        return 0

    def create_tray_icon(color_hex: str = "#f59e0b") -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color_hex))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
        painter.setPen(QColor("#0f172a"))
        font = QFont("Segoe UI", 22, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "M")
        painter.end()
        return QIcon(pixmap)

    class MoultMainWindow(QMainWindow):
        def __init__(self, sync_service: SyncService) -> None:
            super().__init__()
            self.svc = sync_service
            self.setWindowTitle(f"Moult Диск — {self.svc.cm.config.device_name}")
            self.resize(980, 640)
            self.setWindowIcon(create_tray_icon("#f59e0b"))

            central = QWidget()
            self.setCentralWidget(central)
            main_layout = QVBoxLayout(central)

            # Верхняя панель статуса (в стиле Яндекс.Диска)
            header_box = QGroupBox("Статус Moult Диск и профиль безопасности")
            header_layout = QHBoxLayout(header_box)

            self.status_label = QLabel("Статус: Защищённый канал TLS 1.3 активен")
            self.status_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #10b981;")

            self.role_combo = QComboBox()
            self.role_combo.addItem("🏠 Главный ПК (Домашний — центр хранения)", DeviceRole.HOME_MASTER.value)
            self.role_combo.addItem("💼 Рабочий ПК (Офис — слив файлов домой)", DeviceRole.WORK_CLIENT.value)
            idx = 0 if self.svc.cm.config.device_role == DeviceRole.HOME_MASTER else 1
            self.role_combo.setCurrentIndex(idx)
            self.role_combo.currentIndexChanged.connect(self.on_role_changed)

            btn_sync_all = QPushButton("🔄 Синхронизировать все папки")
            btn_sync_all.clicked.connect(self.on_sync_all)

            btn_open_root = QPushButton("📂 Открыть в Проводнике Windows")
            btn_open_root.clicked.connect(
                lambda: open_in_file_manager(self.svc.cm.config.root_disk_path)
            )

            btn_open_web = QPushButton("🌐 Расширенная панель (QR / Крипто-сейф)")
            btn_open_web.clicked.connect(lambda: webbrowser.open(f"http://127.0.0.1:{web_port}"))

            header_layout.addWidget(self.status_label, 2)
            header_layout.addWidget(QLabel("Роль этого ПК:"))
            header_layout.addWidget(self.role_combo, 2)
            header_layout.addWidget(btn_sync_all)
            header_layout.addWidget(btn_open_root)
            header_layout.addWidget(btn_open_web)
            main_layout.addWidget(header_box)

            # Вкладки
            self.tabs = QTabWidget()
            main_layout.addWidget(self.tabs)

            # Вкладка 1: Папки синхронизации (Dropsync + Яндекс.Диск)
            folders_tab = QWidget()
            folders_layout = QVBoxLayout(folders_tab)
            self.folders_table = QTableWidget(0, 6)
            self.folders_table.setHorizontalHeaderLabels(
                [
                    "Папка",
                    "Режим синхронизации",
                    "Файлов / Объём",
                    "Защита",
                    "Статус",
                    "Действия",
                ]
            )
            self.folders_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
            folders_layout.addWidget(self.folders_table)
            self.tabs.addTab(folders_tab, "📁 Папки синхронизации")

            # Вкладка 2: Безопасность и Device ID
            sec_tab = QWidget()
            sec_layout = QFormLayout(sec_tab)
            self.device_id_input = QLineEdit(self.svc.cm.config.device_id)
            self.device_id_input.setReadOnly(True)
            sec_layout.addRow("Криптографический Device ID (SHA-256 + Luhn):", self.device_id_input)
            sec_layout.addRow(
                "Протокол передачи:",
                QLabel("Block Exchange Protocol (BEP) поверх взаимного TLS 1.3 (mTLS)"),
            )
            sec_layout.addRow(
                "Шифрование файлов и сейфа:",
                QLabel("AES-256-GCM + PBKDF2-HMAC-SHA256 (600 000 итераций) + контроль SHA-256"),
            )

            btn_autostart = QPushButton("Включить автозапуск при старте Windows")
            btn_autostart.clicked.connect(self.on_enable_autostart)
            sec_layout.addRow("Интеграция с Windows:", btn_autostart)

            self.xml_preview = QTextEdit()
            self.xml_preview.setReadOnly(True)
            sec_layout.addRow("Сгенерированный config.xml (Syncthing):", self.xml_preview)
            self.tabs.addTab(sec_tab, "🛡 Безопасность данных (TLS 1.3 / AES-256)")

            # Вкладка 3: История активности (как в Яндекс.Диске)
            events_tab = QWidget()
            events_layout = QVBoxLayout(events_tab)
            self.events_table = QTableWidget(0, 5)
            self.events_table.setHorizontalHeaderLabels(
                ["Время", "Папка", "Направление", "Файл", "Подробности / SHA-256"]
            )
            self.events_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
            events_layout.addWidget(self.events_table)
            self.tabs.addTab(events_tab, "🕓 Последние файлы и журнал")

            self.refresh_ui()

        def refresh_ui(self) -> None:
            overview = self.svc.get_dashboard_overview()
            folders = overview["folders"]
            self.folders_table.setRowCount(len(folders))
            for row, f in enumerate(folders):
                self.folders_table.setItem(row, 0, QTableWidgetItem(f["label"]))

                mode_combo = QComboBox()
                for m_val, m_label in SYNC_MODE_LABELS.items():
                    mode_combo.addItem(m_label, m_val)
                curr_idx = list(SYNC_MODE_LABELS.keys()).index(f["sync_mode"])
                mode_combo.setCurrentIndex(curr_idx)
                folder_id = f["id"]
                mode_combo.currentIndexChanged.connect(
                    lambda _idx, fid=folder_id, cb=mode_combo: self.on_folder_mode_changed(
                        fid, cb.currentData()
                    )
                )
                self.folders_table.setCellWidget(row, 1, mode_combo)

                kb = round(f["total_bytes"] / 1024, 1)
                self.folders_table.setItem(
                    row, 2, QTableWidgetItem(f"{f['files_count']} файл(ов) ({kb} КБ)")
                )
                sec_badge = "🔒 AES-256 + TLS 1.3" if f["encryption_enabled"] else "🛡 TLS 1.3 + SHA-256"
                self.folders_table.setItem(row, 3, QTableWidgetItem(sec_badge))
                self.folders_table.setItem(row, 4, QTableWidgetItem(f["last_status"]))

                btn_widget = QWidget()
                btn_layout = QHBoxLayout(btn_widget)
                btn_layout.setContentsMargins(2, 2, 2, 2)
                b_sync = QPushButton("Синхр.")
                b_sync.clicked.connect(lambda _checked=False, fid=folder_id: self.on_sync_one(fid))
                b_open = QPushButton("Папка")
                f_path = f["path"]
                b_open.clicked.connect(
                    lambda _checked=False, p=f_path: open_in_file_manager(p)
                )
                btn_layout.addWidget(b_sync)
                btn_layout.addWidget(b_open)
                self.folders_table.setCellWidget(row, 5, btn_widget)

            events = overview["recent_events"]
            self.events_table.setRowCount(len(events))
            for r, ev in enumerate(events):
                self.events_table.setItem(r, 0, QTableWidgetItem(ev["timestamp"]))
                self.events_table.setItem(r, 1, QTableWidgetItem(ev["folder_label"]))
                self.events_table.setItem(r, 2, QTableWidgetItem(ev["direction"]))
                self.events_table.setItem(r, 3, QTableWidgetItem(ev["file_name"]))
                self.events_table.setItem(
                    r,
                    4,
                    QTableWidgetItem(f"{ev['details']} [SHA256: {ev.get('sha256_short') or '-'}]"),
                )

            self.xml_preview.setPlainText(overview["syncthing_xml_preview"])

        def on_role_changed(self) -> None:
            role_val = self.role_combo.currentData()
            self.svc.cm.apply_role_preset(DeviceRole(role_val))
            self.setWindowTitle(f"Moult Диск — {self.svc.cm.config.device_name}")
            self.refresh_ui()

        def on_folder_mode_changed(self, folder_id: str, new_mode: str) -> None:
            self.svc.add_or_update_folder({"id": folder_id, "sync_mode": new_mode})
            self.refresh_ui()

        def on_sync_one(self, folder_id: str) -> None:
            res = self.svc.sync_folder(folder_id)
            self.status_label.setText(
                f"Папка '{res['folder_label']}' синхронизирована (↑{res['uploaded']} ↓{res['downloaded']})"
            )
            self.refresh_ui()

        def on_sync_all(self) -> None:
            res = self.svc.sync_all_folders()
            self.status_label.setText(
                f"Все папки синхронизированы: отправлено ↑{res['total_uploaded']}, принято ↓{res['total_downloaded']}"
            )
            self.refresh_ui()

        def on_enable_autostart(self) -> None:
            res = set_windows_autostart(True)
            QMessageBox.information(
                self,
                "Автозапуск Windows",
                f"Статус регистрации в реестре Windows: {res}",
            )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = MoultMainWindow(svc)
    window.show()

    tray = QSystemTrayIcon(create_tray_icon("#10b981"), app)
    tray.setToolTip("Moult Диск — Защищённая синхронизация (TLS 1.3)")

    menu = QMenu()
    act_open = QAction("Открыть окно Moult Диск", menu)
    act_open.triggered.connect(window.showNormal)
    menu.addAction(act_open)

    act_explorer = QAction("Открыть папку в Проводнике Windows", menu)
    act_explorer.triggered.connect(
        lambda: open_in_file_manager(svc.cm.config.root_disk_path)
    )
    menu.addAction(act_explorer)

    act_sync = QAction("Синхронизировать все папки сейчас", menu)
    act_sync.triggered.connect(window.on_sync_all)
    menu.addAction(act_sync)

    menu.addSeparator()
    act_quit = QAction("Выход", menu)
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: window.showNormal()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()

    # Фоновый таймер автоматической синхронизации каждые 30 секунд
    timer = QTimer()
    timer.timeout.connect(window.on_sync_all)
    timer.start(30_000)

    return app.exec()
