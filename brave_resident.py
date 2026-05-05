import sys
import signal
import os
import json
import threading
import ctypes
import time
import pyperclip
import keyboard  # [重要] 不足していたインポート
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QPushButton, QStatusBar, QMainWindow, QLabel
)
from PyQt6.QtCore import (
    Qt, QUrl, QEvent, QTimer, QMetaObject, Q_ARG, QObject, pyqtSignal, QPoint
)
from PyQt6.QtGui import QCursor, QGuiApplication, QKeyEvent, QFont
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage

# --- 設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PROFILE_DIR = os.path.join(BASE_DIR, "brave_profile")

# JSコピー用スクリプト（MiniWindow内で使用）
JS_COPY_SCRIPT = "window.getSelection().toString();"

# --- 通知系クラス ---
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

# --- 通信ブリッジ ---
class GlobalBridge(QObject):
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()

bridge = GlobalBridge()
current_window = None
_notif = None

# --- クリップボード・URL取得関数 ---
def get_brave_url():
    try:
        from pywinauto import Desktop
        # 自分自身(Mini Window)を完全に除外するためのフィルタリングを強化
        bw = [w for w in Desktop(backend="uia").windows(title_re=".*Brave.*", visible_only=True) 
              if "Brave Resident Mini" not in w.window_text() 
              and "Mini Window" not in w.window_text()
              and w.class_name() != "CabinetWClass"]
        
        if not bw: return None
        # 0番目（一番手前にあるBrave本体）からEditを探す
        all_edits = bw[0].descendants(control_type="Edit")
        for edit in all_edits:
            try:
                val = edit.get_value()
                if val and ("http" in val or "." in val):
                    return "https://" + val if not val.startswith("http") else val
            except: continue
    except: return None
    return None

# --- メインウィンドウ ---
class MiniWindow(QMainWindow):
    def __init__(self, config):
        super().__init__()
        # タイトルを厳密に設定（get_brave_urlでの除外用）
        self.setWindowTitle("Brave Resident Mini")
        self._setup_browser()
        self._setup_ui()
        
        # JSONから初期位置・サイズを適用
        self.apply_config_geometry(config)
        
        if config.get("url"):
            self.browser.setUrl(QUrl(str(config["url"])))
        
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

    def apply_config_geometry(self, config):
        """JSONの設定を窓に反映させる"""
        self.setGeometry(
            config.get("x", 960), 
            config.get("y", 0), 
            config.get("width", 480), 
            config.get("height", 800)
        )

    def _setup_browser(self):
        if not os.path.exists(PROFILE_DIR): os.makedirs(PROFILE_DIR)
        self.profile = QWebEngineProfile("BraveResidentStorage", self)
        self.profile.setPersistentStoragePath(PROFILE_DIR)
        self.page = QWebEnginePage(self.profile, self)
        self.browser = QWebEngineView()
        self.browser.setPage(self.page)
        
        # --- ここが重要 ---
        # ブラウザ本体だけでなく、実際にキー入力を受け取る「中の人（focusProxy）」にも
        # イベントフィルタをインストールして、Ctrl+Fを横取りできるようにします。
        self.browser.installEventFilter(self)
        self.page.loadFinished.connect(self._install_proxy_filter)

    def _install_proxy_filter(self):
        """ブラウザの内部パーツにフィルタをかける"""
        if self.browser.focusProxy():
            self.browser.focusProxy().installEventFilter(self)

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # --- 検索バー（復活） ---
        self.search_container = QWidget()
        self.search_container.setStyleSheet("background: #f0f0f0; border-bottom: 1px solid #ccc;")
        s_layout = QHBoxLayout(self.search_container)
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search in page...")
        self.search_bar.returnPressed.connect(self._handle_search_enter)
        s_layout.addWidget(self.search_bar)
        layout.addWidget(self.search_container)
        self.search_container.hide() # 最初は隠す
        
        layout.addWidget(self.browser)
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

    def _handle_search_enter(self):
        """Enterで検索実行"""
        self.browser.findText(self.search_bar.text())

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            # Ctrl + F を最優先で捕まえる
            if modifiers & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_F:
                if self.search_container.isVisible():
                    self.search_container.hide()
                    self.browser.setFocus()
                else:
                    self.search_container.show()
                    self.search_bar.setFocus()
                    self.search_bar.selectAll() # 入力しやすく
                return True # イベントをここで消費（ブラウザに渡さない）

            # 検索バーにフォーカスがある時の Enter
            if self.search_bar.hasFocus() and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._handle_search_enter()
                return True
            
            # 検索バーが表示されている時に ESC で閉じる（おまけの利便性）
            if key == Qt.Key.Key_Escape and self.search_container.isVisible():
                self.search_container.hide()
                self.browser.setFocus()
                return True

        return super().eventFilter(obj, event)

# --- 各種シグナル処理 ---
def on_copy_signal():
    """Alt+C: URLを取得してJSONを更新"""
    url = get_brave_url()
    if not url: return

    # 現在のJSONを一度読み込んで、座標を保持したままURLだけ書き換える
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except:
        config = {"x": 990, "y": 28, "width": 450, "height": 830}

    config["url"] = url
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    show_floating_notify("★URL Updated!")

def show_floating_notify(text):
    global _notif
    _notif = FloatingNotification(text)
    _notif.show()

def on_paste_signal():
    """Alt+V: JSONの設定に従ってワープ"""
    global current_window
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except: return

    if not current_window:
        current_window = MiniWindow(config)

    # JSONの座標とサイズを適用
    current_window.hide()
    # 仮想デスクトップを跨ぐための再フラグ立て
    current_window.setWindowFlags(
        current_window.windowFlags() | 
        Qt.WindowType.WindowStaysOnTopHint | 
        Qt.WindowType.Tool
    )
    current_window.apply_config_geometry(config) # ここでJSONの値を反映
    current_window.browser.setUrl(QUrl(config["url"]))
    
    current_window.show()
    current_window.raise_()
    current_window.activateWindow()

# --- ホットキー監視 ---
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

# --- メイン実行 ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    bridge.copy_requested.connect(on_copy_signal)
    bridge.paste_requested.connect(on_paste_signal)
    
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    monitor_timer = QTimer()
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(50) # 感度を少し上げる
    
    print("Watching for Alt+C / Alt+V... (Press Ctrl+C in console to stop)")
    sys.exit(app.exec())