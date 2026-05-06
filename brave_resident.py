import sys
import signal
import os
import json
import time
import keyboard
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QPushButton, QStatusBar, QMainWindow, QLabel
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
    "url": "https://www.google.com",
    "favorites": ["https://www.youtube.com", "https://gemini.google.com/app"]
}

JS_COPY_SCRIPT = "window.getSelection().toString();"

# ==========================================
# 2. クラス定義
# ==========================================
class GlobalBridge(QObject):
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()
    show_requested = pyqtSignal()

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

class ClickableLabel(QLabel):
    clicked = pyqtSignal()
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            
class MiniWindow(QMainWindow):
    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("Resident Mini")
        self.config_at_start = config
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self._setup_browser()
        self._setup_ui()
        if config.get("url"):
            self.browser.setUrl(QUrl(str(config["url"])))
        self.audio_indicator = None
        # 音声状態が変わった時に通知を受け取る設定
        self.browser.page().recentlyAudibleChanged.connect(self._handle_audio_status)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'config_at_start'):
            self.apply_config_geometry(self.config_at_start)
            del self.config_at_start

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
        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
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
        self.search_container.setMaximumHeight(40)
        s_layout = QHBoxLayout(self.search_container)
        s_layout.setContentsMargins(5, 2, 5, 2)
        s_layout.setSpacing(5)
        s_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter) 

        self.mode_toggle = QPushButton("[G]")
        self.mode_toggle.setFixedSize(30, 24)
        self.mode_toggle.setStyleSheet("background-color: #4285f4; color: white; font-weight: bold; border: none; border-radius: 3px;")
        self.mode_toggle.clicked.connect(self.toggle_search_mode)
        s_layout.addWidget(self.mode_toggle)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Google Search...")
        self.search_bar.returnPressed.connect(self._handle_search_enter)
        s_layout.addWidget(self.search_bar)

        # お気に入りグループ
        self.fav_group = QWidget()
        fav_layout = QHBoxLayout(self.fav_group)
        fav_layout.setContentsMargins(0,0,0,0)
        fav_layout.setSpacing(5)

        config = load_config()
        favs = config.get("favorites", DEFAULT_CONFIG.get("favorites", []))
        
        # アイコンURLの直接指定は不可なため、頭文字1文字をボタンにする
        for url in favs[:2]:
            display_text = url.replace("https://", "").replace("www.", "")[0].upper() # 頭文字
            btn = QPushButton(display_text) 
            btn.setFixedSize(24, 24)
            btn.setToolTip(url) # ホバーでURLを表示
            btn.setStyleSheet("background-color: white; border: 1px solid #ccc; border-radius: 3px; font-weight: bold;")
            btn.clicked.connect(lambda checked, u=url: self.browser.setUrl(QUrl(u)))
            fav_layout.addWidget(btn)
        
        s_layout.addWidget(self.fav_group)

        # ページ内検索グループ
        self.find_group = QWidget()
        find_layout = QHBoxLayout(self.find_group)
        find_layout.setContentsMargins(0,0,0,0)
        find_layout.setSpacing(5)

        self.hit_label = QLabel("0/0")
        self.hit_label.setFixedWidth(40) # 幅を固定してガタつき防止
        self.hit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hit_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold;")
        find_layout.addWidget(self.hit_label)

        btn_prev = QPushButton("↑")
        btn_next = QPushButton("↓")
        for b in [btn_prev, btn_next]: 
            b.setFixedSize(24, 24)
            b.setStyleSheet("background-color: white; border: 1px solid #ccc; border-radius: 3px;")
        
        btn_prev.clicked.connect(lambda: self._find_with_count(backward=True))
        btn_next.clicked.connect(lambda: self._find_with_count(backward=False))
        
        find_layout.addWidget(btn_prev)
        find_layout.addWidget(btn_next)
        s_layout.addWidget(self.find_group)
        self.find_group.hide()

        layout.addWidget(self.search_container)
        self.search_container.hide()
        
        layout.addWidget(self.browser)
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.search_mode = "google"
        
        config = load_config()
        saved_mode = config.get("search_mode", "google")
        
        # 初期状態を設定
        self.search_mode = "google" # 一旦デフォルトに
        if saved_mode == "find":
            # findモードへの切り替え処理を実行
            self.toggle_search_mode() 
        else:
            # googleモードの初期状態を確定（色は既にセットされているので検索バーの表示等）
            self.search_bar.setPlaceholderText("Google Search...")
        
    def toggle_search_mode(self):
        """トグルボタンでモードを切り替える"""
        if self.search_mode == "google":
            self.search_mode = "find"
            self.mode_toggle.setText("[F]")
            self.mode_toggle.setStyleSheet("background-color: #ff9800; color: white; font-weight: bold; border-radius: 3px;")
            self.search_bar.setPlaceholderText("Find in page...")
            self.fav_group.hide()
            self.find_group.show()
        else:
            self.search_mode = "google"
            self.mode_toggle.setText("[G]")
            self.mode_toggle.setStyleSheet("background-color: #4285f4; color: white; font-weight: bold; border-radius: 3px;")
            self.search_bar.setPlaceholderText("Google Search...")
            self.find_group.hide()
            self.fav_group.show()
        self.save_current_mode()
        self.search_bar.setFocus()
        
    def save_current_mode(self):
        """現在の検索モードをJSONに書き込む"""
        config = load_config()
        config["search_mode"] = self.search_mode
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        
    # --- 新設：ヒット件数を取得しながら検索する ---
    def _find_with_count(self, backward=False):
        text = self.search_bar.text()
        flags = QWebEnginePage.FindFlag(0)
        if backward:
            flags |= QWebEnginePage.FindFlag.FindBackward
        
        # 検索実行時にコールバックを登録して件数を受け取る
        self.browser.findText(text, flags, self._update_hit_count)

    def _update_hit_count(self, result):
        """検索結果（件数情報）をラベルに反映"""
        # [修正] 環境に合わせて activeMatch() を使用
        num_matches = result.numberOfMatches() 
        active_index = result.activeMatch() # activeMatchIndex から activeMatch へ変更

        if num_matches > 0:
            current = active_index
            self.hit_label.setText(f"{current}/{num_matches}")
        else:
            self.hit_label.setText("0/0")

    def _handle_search_enter(self):
        """Enterキーで検索実行（Shift併用で逆方向）"""
        text = self.search_bar.text()
        if not text: return

        if self.search_mode == "google":
            url = f"https://www.google.com/search?q={text}"
            self.browser.setUrl(QUrl(url))
        else:
            # Shiftキーが押されているか判定
            modifiers = QApplication.keyboardModifiers()
            is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
            
            # Shiftがあれば逆方向（backward=True）
            self._find_with_count(backward=bool(is_shift))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if obj == self.search_bar and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._handle_search_enter()
                return True # イベントをここで消費
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_F:
                    if self.search_container.isVisible():
                        self.search_container.hide()
                        self.browser.setFocus()
                    else:
                        self.search_container.show()
                        self.search_bar.setFocus()
                        self.search_bar.selectAll()
                    return True
                elif key == Qt.Key.Key_W:
                    self.close_mini_window()
                    return True
            if key == Qt.Key.Key_Escape and self.search_container.isVisible():
                self.search_container.hide()
                self.browser.setFocus()
                return True
        return super().eventFilter(obj, event)
    
    def contextMenuEvent(self, event):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: white; border: 1px solid #999; }
            QMenu::item { padding: 5px 25px; }
            QMenu::item:selected { background-color: #3a8fb7; color: white; }
        """)
        menu.addAction("戻る").triggered.connect(self.browser.back)
        menu.addAction("進む").triggered.connect(self.browser.forward) # 追加
        menu.addAction("リロード").triggered.connect(self.browser.reload)
        menu.addSeparator()
        close_action = menu.addAction("隠す (Ctrl+W)")
        close_action.triggered.connect(self.close_mini_window)
        menu.exec(QCursor.pos())
        
    def _handle_audio_status(self, audible):
        """音が鳴り始めた/止まった時の処理"""
        if audible and not self.isVisible():
            self._show_audio_indicator()
        else:
            self._hide_audio_indicator()

    def _show_audio_indicator(self):
        """画面端に動画タイトル付きのインジケーターを出す"""
        if not self.audio_indicator:
            # ① カスタムラベルを使用
            self.audio_indicator = ClickableLabel("", None)
            self.audio_indicator.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | 
                Qt.WindowType.WindowStaysOnTopHint | 
                Qt.WindowType.Tool
            )
            # クリックされたらAlt+Sと同じ挙動（自分を表示）を呼び出す
            self.audio_indicator.clicked.connect(self._handle_indicator_click)
            
            self.audio_indicator.setStyleSheet("""
                background: rgba(0, 0, 0, 180); color: #00FF00; 
                padding: 8px; border-radius: 5px; font-weight: bold;
                border: 1px solid #00FF00;
            """)

        # ② タイトルを取得して反映 (♪ + 動画タイトル)
        video_title = self.browser.title()
        if not video_title: video_title = "Audio Playing"
        # 文字が長すぎると画面を覆うので、適度に省略
        display_text = f"♪ {video_title[:30]}..." if len(video_title) > 30 else f"♪ {video_title}"
        
        self.audio_indicator.setText(display_text)
        self.audio_indicator.adjustSize() # 文字数に合わせてサイズ調整

        screen_rect = QApplication.primaryScreen().geometry()
        # サイズが変わるので右下配置を再計算
        self.audio_indicator.move(
            screen_rect.width() - self.audio_indicator.width() - 5, 
            screen_rect.height() - 80
        )
        self.audio_indicator.show()

    def _handle_indicator_click(self):
        """インジケーターがクリックされた時の処理"""
        self.show()
        self.raise_()
        self.activateWindow()
        self._hide_audio_indicator()

    def _hide_audio_indicator(self):
        if self.audio_indicator:
            self.audio_indicator.hide()

    def close_mini_window(self):
        """[修正] 隠す際、音が鳴っていたらインジケーターを出す"""
        self.hide()
        if self.browser.page().recentlyAudible():
            self._show_audio_indicator()

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
    
def on_show_signal():
    """Alt+S：現在のウィンドウをそのまま再表示する"""
    global current_window
    if current_window:
        current_window.show()
        current_window.raise_()
        current_window.activateWindow()
        
        # もし音声インジケーターが表示されていたら隠す
        if hasattr(current_window, '_hide_audio_indicator'):
            current_window._hide_audio_indicator()
    else:
        # ウィンドウがまだ一度も作られていない場合は「コピーからしてね」と通知
        show_floating_notify("No window to show. Press Alt+C first.")

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
        elif keyboard.is_pressed('s'):  # ← 追加
            bridge.show_requested.emit()
            last_action_time = now

# ==========================================
# 4. メイン実行
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    
    # --- ここが「配線」の完成形 ---
    bridge.copy_requested.connect(on_copy_signal)
    bridge.paste_requested.connect(on_paste_signal)
    bridge.show_requested.connect(on_show_signal)  # ← これを追記！
    
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    monitor_timer = QTimer()
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(50)
    
    print("Watching Alt+C/V/S... (Press Ctrl+C to stop)")
    sys.exit(app.exec())