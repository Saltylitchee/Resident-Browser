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
    Qt, QUrl, QEvent, QTimer, QMetaObject, Q_ARG, QObject, pyqtSignal, QPoint, QCoreApplication
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
        
        self.browser.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        
    def apply_config_geometry(self, config):
        """JSONの設定を窓に反映させる"""
        self.setGeometry(
            config.get("x", 990), 
            config.get("y", 28), 
            config.get("width", 450), 
            config.get("height", 830)
        )

    def _setup_browser(self):
        # プロファイルの指定を一切せず、デフォルトの「一時的なメモリ内プロファイル」を使う
        # これにより、過去のキャッシュや設定の影響を完全に排除できます。
        self.page = QWebEnginePage(self) 
        self.browser = QWebEngineView()
        self.browser.setPage(self.page)
        
        # イベントフィルタの設定はそのまま
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
        
        # --- 検索バー（ボタンと件数表示を追加） ---
        self.search_container = QWidget()
        self.search_container.setStyleSheet("background: #f0f0f0; border-bottom: 1px solid #ccc;")
        s_layout = QHBoxLayout(self.search_container)
        s_layout.setContentsMargins(5, 2, 5, 2)
        
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search...")
        
        # 上下ボタン
        self.btn_prev = QPushButton("▲")
        self.btn_next = QPushButton("▼")
        self.btn_prev.setFixedSize(24, 24)
        self.btn_next.setFixedSize(24, 24)
        
        # 件数ラベル
        self.search_status_label = QLabel("0/0")
        self.search_status_label.setStyleSheet("color: #666; font-size: 10px; margin-right: 5px;")

        s_layout.addWidget(self.search_bar)
        s_layout.addWidget(self.search_status_label)
        s_layout.addWidget(self.btn_prev)
        s_layout.addWidget(self.btn_next)
        
        layout.addWidget(self.search_container)
        self.search_container.hide()

        # --- シグナル接続 ---
        self.search_bar.textChanged.connect(lambda: self._do_search(forward=True))
        self.search_bar.returnPressed.connect(self._handle_search_enter)
        self.btn_next.clicked.connect(lambda: self._do_search(forward=True))
        self.btn_prev.clicked.connect(lambda: self._do_search(forward=False))

    def _handle_search_enter(self):
        """Enterで次へ、Shift+Enterで前へ"""
        modifiers = QGuiApplication.queryKeyboardModifiers()
        forward = not (modifiers & Qt.KeyboardModifier.ShiftModifier)
        self._do_search(forward=forward)
        
    def _do_search(self, forward=True):
        """検索実行と結果の更新"""
        text = self.search_bar.text()
        if not text:
            self.browser.findText("") # 検索ハイライト消去
            self.search_status_label.setText("0/0")
            return

        options = QWebEnginePage.FindFlag(0)
        if not forward:
            options |= QWebEnginePage.FindFlag.FindBackward
        
        # 検索実行。第3引数にコールバック関数を渡して結果を受け取る
        self.browser.findText(text, options, self._update_search_count)

    def _update_search_count(self, result):
        """ヒット件数（現在/合計）を更新"""
        # result は QWebEngineFindTextResult オブジェクト
        count = result.numberOfMatches()
        current = result.activeMatch() # 現在選択されているのは何件目か
        self.search_status_label.setText(f"{current}/{count}")
        
        if count == 0 and self.search_bar.text():
            self.search_status_label.setStyleSheet("color: red; font-size: 10px;")
        else:
            self.search_status_label.setStyleSheet("color: #666; font-size: 10px;")

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
        config = {"x": 960, "y": 0, "width": 480, "height": 800}

    config["url"] = url
    
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)
    
    show_floating_notify("★URL Updated!")

def show_floating_notify(text):
    global _notif
    _notif = FloatingNotification(text)
    _notif.show()

def on_paste_signal():
    global current_window
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except: return

    # 1. 古いウィンドウの完全な破棄
    if current_window:
        current_window.close()
        current_window.deleteLater()
        current_window = None

    # 2. 新しいウィンドウの生成
    current_window = MiniWindow(config)
    
    # [デバッグ用] 枠を表示してOSに窓を認識させる（安定したら Frameless に戻せます）
    current_window.setWindowFlags(
        Qt.WindowType.Window | 
        Qt.WindowType.WindowStaysOnTopHint
    )
    
    # 3. 窓を先に表示する
    current_window.show()
    current_window.raise_()
    current_window.activateWindow()

    # 4. 窓が表示された「後」に、一度だけURLをセットする
    # 即座に呼ぶのではなく、100ms待つことでエンジンの初期化を確実に完了させます
    target_url = config.get("url")
    if target_url:
        # 重複呼び出しを防ぐため、ここ以外の setUrl はすべて削除またはコメントアウトしてください
        QTimer.singleShot(100, lambda: current_window.browser.setUrl(QUrl(target_url)))

    # 5. 描画が始まらない場合のバックアップ（一押し）
    def force_refresh():
        if current_window and current_window.browser:
            current_window.browser.update()
            print(f"Rendering triggered for: {target_url}")

    QTimer.singleShot(500, force_refresh)

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
    # OSレベルで全てのGPUパスを封鎖する（最優先）
    os.environ["QT_OPENGL"] = "software"
    os.environ["QTWEBENGINE_DISABLE_GPU"] = "1"
    
    # 既存のフラグをさらに強化
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--disable-gpu "
        "--disable-gpu-compositing "
        "--disable-software-rasterizer "
        "--disable-gpu-sandbox "
        "--in-process-gpu "  # GPUを別プロセスではなくメインプロセス内で動かそうとする（失敗しやすくなるので回避用）
        "--no-sandbox"
    )

    import sys

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QCoreApplication, Qt

    # [最重要] アプリ生成前に、描画エンジンをソフトウェアモード（CPU）に固定
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
    
    app = QApplication(sys.argv)
    
    # 既存の bridge 等の設定...
    sys.coinit_flags = 2 
    app.setQuitOnLastWindowClosed(False)
    bridge.copy_requested.connect(on_copy_signal)
    bridge.paste_requested.connect(on_paste_signal)
    
    monitor_timer = QTimer()
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(50)
    
    print("Running in Software Rendering Mode (CPU)...")
    sys.exit(app.exec())