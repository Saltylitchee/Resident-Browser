import sys
import signal
import os
import re
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
from PyQt6.QtGui import QCursor, QFont, QPainter, QBrush, QColor, QPen
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
        self.label.setStyleSheet("background-color: rgba(0, 0, 0, 180); color: #00ff7f; font-weight: bold; padding: 8px 15px; border-radius: 5px;")
        self.label.setFont(QFont("Arial", 12))
        layout.addWidget(self.label)
        
        pos = QCursor.pos()
        self.move(pos.x() + 20, pos.y() - 20)
        QTimer.singleShot(1500, self.close)

class ClickableLabel(QLabel):
    clicked = pyqtSignal(str)

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._current_color = "#00FF00" # デフォルト

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 背景色と枠線の色
        bg_color = QColor(60, 60, 60, 230)
        border_color = QColor(self._current_color)

        # 1. 描画エリアの確定（少し内側にマージンを取る）
        rect = self.rect().adjusted(1, 1, -1, -1)

        # 2. 背景の描画
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 15, 15)

        # 3. 枠線の描画
        pen = QPen(border_color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 15, 15)
        
        painter.end()

        # 4. 文字だけを上に描画させる（背景を描画させないために重要）
        # super().paintEvent(event) を呼ぶ前に背景を自前で塗りつぶしているので
        # ラベルのデフォルト描画が干渉しないよう制御します
        super().paintEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # アイコン付近かタイトル付近かの判定
            if event.pos().x() < 45:
                self.clicked.emit("icon")
            else:
                self.clicked.emit("title")
            
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
        self.browser.page().recentlyAudibleChanged.connect(self._handle_audio_status)
        self.browser.titleChanged.connect(self._on_title_changed)

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
            if modifiers & Qt.KeyboardModifier.AltModifier:
                if key == Qt.Key.Key_Left:
                    self.browser.back()
                    return True
                elif key == Qt.Key.Key_Right:
                    self.browser.forward()
                    return True
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
        """音が止まってもインジケーターは消さず、状態（アイコン）だけ更新する"""
        if not self.isVisible():
            # 音が止まった（一時停止した）瞬間に、アイコンを ♪ から || に変えるために再描画
            self._show_indicator()

    def _on_title_changed(self, title):
        """動画が切り替わるなどしてタイトルが変わった時の処理"""
        # インジケーターが表示されている（格納状態である）時だけ更新をかける
        if self.audio_indicator and self.audio_indicator.isVisible():
            self._show_indicator()

    def _show_indicator(self):
        """JavaScriptでページの状態を確認してからインジケーターを表示する"""
        # ページ内のビデオ状態をチェックするJS
        js_code = """
        (function() {
            var v = document.querySelector('video');
            if (!v) return 'none';
            return v.paused ? 'paused' : 'playing';
        })();
        """
        # JSを実行し、結果を _update_indicator_with_state に渡す
        self.browser.page().runJavaScript(js_code, self._update_indicator_with_state)

    def _update_indicator_with_state(self, state):
        """JSの結果を受けて、アイコン幅固定のレイアウトで表示を確定させる"""
        config = load_config()
        color = config.get("theme_color", "#00FF00")

        if not self.audio_indicator:
            self.audio_indicator = ClickableLabel("", None)
            self.audio_indicator.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | 
                Qt.WindowType.WindowStaysOnTopHint | 
                Qt.WindowType.Tool
            )
            self.audio_indicator.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.audio_indicator.clicked.connect(self._handle_indicator_click)
            
            # --- レイアウトの構築 (初回のみ) ---
            layout = QHBoxLayout(self.audio_indicator)
            layout.setContentsMargins(10, 5, 10, 5) # インジケーターの「外枠」と「中身（アイコンや文字）」の間の隙間(左, 上, 右, 下)
            layout.setSpacing(4) # 「アイコン」と「タイトル文字」の間の "距離"
            
            self.icon_label = QLabel()
            self.icon_label.setFixedWidth(30) # アイコンが入る「透明な箱」の幅
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            self.text_label = QLabel()
            
            layout.addWidget(self.icon_label)
            layout.addWidget(self.text_label)
        
        self.audio_indicator._current_color = color

        # 状態に応じたアイコン設定
        if state == 'playing':
            icon = "♪" # 再生中のアイコン
        elif state == 'paused':
            icon = "||" # 一時停止中のアイコン
        else:
            icon = "❏" # 動画サイト以外のアイコン

        # タイトル整形
        raw_title = self.browser.title()
        clean_title = re.sub(r'^\(\d+\)\s*', '', raw_title)
        if not clean_title or clean_title == "about:blank": 
            clean_title = "Resident Mini"

        # ラベルに値をセット
        self.icon_label.setText(icon)
        self.text_label.setText(f"{clean_title[:30]}...")
        
        # スタイル適用（背景を透明にしてpaintEventの描画を活かす）
        label_style = f"color: {color}; font-weight: bold; border: none; background: transparent;"
        self.icon_label.setStyleSheet(label_style)
        self.text_label.setStyleSheet(label_style)

        # 全体のサイズを内容に合わせる
        self.audio_indicator.adjustSize()

        # 配置（右下）
        screen_rect = QApplication.primaryScreen().geometry()
        self.audio_indicator.move(
            screen_rect.width() - self.audio_indicator.width() - 10, 
            screen_rect.height() - 75
        )
        self.audio_indicator.show()

    def _handle_indicator_click(self, area):
        """4. クリックエリアに応じた処理の分岐"""
        if area == "title":
            # タイトルクリックで再表示（Alt+Sと同じ）
            self.show()
            self.raise_()
            self.activateWindow()
            self._hide_audio_indicator()
        
        elif area == "icon":
            # YouTubeの内部APIまたはvideo要素に直接干渉する強力なJS
            js_toggle = """
            (function() {
                // 1. YouTubeの内部API(playerApi)を利用したトグル
                var moviePlayer = document.querySelector('#movie_player');
                if (moviePlayer && moviePlayer.getPlayerState) {
                    var state = moviePlayer.getPlayerState();
                    if (state === 1) { // 1: 再生中
                        moviePlayer.pauseVideo();
                    } else {
                        moviePlayer.playVideo();
                    }
                    return;
                }

                // 2. ショート動画用のオーバーレイ要素を直接探してクリック
                var shortsPlayer = document.querySelector('video.video-stream');
                if (shortsPlayer) {
                    if (shortsPlayer.paused) { shortsPlayer.play(); }
                    else { shortsPlayer.pause(); }
                    return;
                }

                // 3. 最終手段：スペースキー送信
                window.dispatchEvent(new KeyboardEvent('keydown', { keyCode: 32 }));
            })();
            """
            self.browser.page().runJavaScript(js_toggle)
            # クリック直後に状態を再確認してインジケーターを更新
            QTimer.singleShot(200, self._show_indicator)
            
            # アイコンの見た目を即座に切り替える（簡易的なフィードバック）
            current_text = self.audio_indicator.text()
            if "♪" in current_text:
                self.audio_indicator.setText(current_text.replace("♪", "||"))
            else:
                self.audio_indicator.setText(current_text.replace("||", "♪"))

    def _hide_audio_indicator(self):
        if self.audio_indicator:
            self.audio_indicator.hide()

    def close_mini_window(self):
        """小窓を閉じたら、必ずインジケーターとして格納する"""
        self.hide()
        self._show_indicator()

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
    config = load_config()
    # "indicator_color" を "theme_color" に置換済みの前提
    theme_color = config.get("theme_color", "#00FF00")
    
    _notif = FloatingNotification(text)
    # 通知ラベルのスタイルをJSONの色に同期
    _notif.label.setStyleSheet(f"""
        color: {theme_color}; 
        font-weight: bold; 
        font-size: 14px;
        background: transparent;
    """)
    _notif.show()

last_action_time = 0
def check_hotkeys():
    global last_action_time
    now = time.time()
    if now - last_action_time < 0.3: return
    
    if keyboard.is_pressed('alt'):
        # Shiftが押されているか確認
        is_shift = keyboard.is_pressed('shift')
        
        if keyboard.is_pressed('c'):
            bridge.copy_requested.emit()
            last_action_time = now
        elif keyboard.is_pressed('v'):
            bridge.paste_requested.emit()
            last_action_time = now
        elif keyboard.is_pressed('s') and not is_shift: # ★Shiftが押されていない時だけ実行
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