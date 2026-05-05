import sys
import signal
import os
import json
import time
import keyboard
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QStatusBar, QMainWindow, QLabel
)
from PyQt6.QtCore import (
    Qt, QUrl, QEvent, QTimer, QObject, pyqtSignal
)
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage

# ==========================================
# 1. 定数・デフォルト設定の集約
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PROFILE_DIR = os.path.join(BASE_DIR, "brave_profile")

# 初期値やセーフティ情報をここに集約
DEFAULT_CONFIG = {
    "x": 990,
    "y": 25,
    "width": 450,
    "height": 830,
    "url": "https://www.google.com"
}

JS_COPY_SCRIPT = "window.getSelection().toString();"

# ==========================================
# 2. クラス定義
# ==========================================
class GlobalBridge(QObject):
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()

bridge = GlobalBridge()
current_window = None
_notif = None

class FloatingNotification(QWidget):
    def __init__(self, text):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        
        layout = QVBoxLayout(self)
        self.label = QLabel(text)
        self.label.setStyleSheet("background-color: rgba(0, 0, 0, 180); color: #00FF00; font-weight: bold; padding: 8px 15px; border-radius: 5px;")
        self.label.setFont(QFont("Arial", 12))
        layout.addWidget(self.label)
        
        pos = QCursor.pos()
        self.move(pos.x() + 20, pos.y() - 20)
        QTimer.singleShot(1500, self.close)

class MiniWindow(QMainWindow):
    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("Resident Mini")
        self._setup_browser()
        self._setup_ui()
        
        # 設定を反映
        self.apply_config_geometry(config)
        if config.get("url"):
            self.browser.setUrl(QUrl(str(config["url"])))
        
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

    def apply_config_geometry(self, config):
        self.setGeometry(
            config.get("x", DEFAULT_CONFIG["x"]), 
            config.get("y", DEFAULT_CONFIG["y"]), 
            config.get("width", DEFAULT_CONFIG["width"]), 
            config.get("height", DEFAULT_CONFIG["height"])
        )

    def _setup_browser(self):
        if not os.path.exists(PROFILE_DIR): os.makedirs(PROFILE_DIR)
        self.profile = QWebEngineProfile("BraveResidentStorage", self)
        self.profile.setPersistentStoragePath(PROFILE_DIR)
        self.page = QWebEnginePage(self.profile, self)
        self.browser = QWebEngineView()
        self.browser.setPage(self.page)
        self.browser.installEventFilter(self)
        self.page.loadFinished.connect(self._install_proxy_filter)

    def _install_proxy_filter(self):
        if self.browser.focusProxy():
            self.browser.focusProxy().installEventFilter(self)

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.search_container = QWidget()
        self.search_container.setStyleSheet("background: #f0f0f0; border-bottom: 1px solid #ccc;")
        s_layout = QHBoxLayout(self.search_container)
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search in page...")
        self.search_bar.returnPressed.connect(self._handle_search_enter)
        s_layout.addWidget(self.search_bar)
        layout.addWidget(self.search_container)
        self.search_container.hide()
        
        layout.addWidget(self.browser)
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

    def _handle_search_enter(self):
        self.browser.findText(self.search_bar.text())

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_F:
                if self.search_container.isVisible():
                    self.search_container.hide()
                    self.browser.setFocus()
                else:
                    self.search_container.show()
                    self.search_bar.setFocus()
                    self.search_bar.selectAll()
                return True
        return super().eventFilter(obj, event)

# ==========================================
# 3. 各種シグナル・ホットキー処理
# ==========================================
def load_config():
    """JSONから設定を読み込む。失敗した場合はデフォルトを返す"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"Config load error: {e}")
    return DEFAULT_CONFIG.copy()

def get_brave_url():
    try:
        from pywinauto import Desktop
        bw = [w for w in Desktop(backend="uia").windows(title_re=".*Brave.*", visible_only=True) 
              if "Brave Resident Mini" not in w.window_text() 
              and w.class_name() != "CabinetWClass"]
        
        if not bw: return None
        all_edits = bw[0].descendants(control_type="Edit")
        for edit in all_edits:
            try:
                val = edit.get_value()
                if val and ("http" in val or "." in val):
                    return "https://" + val if not val.startswith("http") else val
            except: continue
    except: return None
    return None

def on_copy_signal():
    url = get_brave_url()
    if not url: return

    config = load_config()
    config["url"] = url
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    show_floating_notify("★URL Updated!")

def on_paste_signal():
    global current_window
    config = load_config()

    if not current_window:
        current_window = MiniWindow(config)

    # 座標とURLを更新
    current_window.apply_config_geometry(config)
    current_window.browser.setUrl(QUrl(config.get("url", DEFAULT_CONFIG["url"])))
    
    # ウィンドウフラグの再設定（仮想デスクトップ対策）
    current_window.setWindowFlags(
        current_window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
    )
    
    current_window.show()
    current_window.raise_()
    current_window.activateWindow()

def show_floating_notify(text):
    global _notif
    _notif = FloatingNotification(text)
    _notif.show()

last_action_time = 0
def check_hotkeys():
    global last_action_time
    now = time.time()
    if now - last_action_time < 0.3: return
    
    if keyboard.is_pressed('alt'):
        if keyboard.is_pressed('c'):
            bridge.copy_requested.emit()
            last_action_time = now
        elif keyboard.is_pressed('v'):
            bridge.paste_requested.emit()
            last_action_time = now

# ==========================================
# 4. メイン実行
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    bridge.copy_requested.connect(on_copy_signal)
    bridge.paste_requested.connect(on_paste_signal)
    
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    monitor_timer = QTimer()
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(50)
    
    print("Watching for Alt+C / Alt+V... (Press Ctrl+C in console to stop)")
    sys.exit(app.exec())