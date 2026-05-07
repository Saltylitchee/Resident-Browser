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
    preset_requested = pyqtSignal()
    minimize_requested = pyqtSignal()

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
        # --- 1. まず変数の定義をすべて済ませる（超重要） ---
        self.audio_indicator = None
        self._current_preset_idx = config.get("current_preset_index", 0)
        
        # --- 2. UIのセットアップ ---
        self.setWindowTitle("Resident Mini")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self._setup_browser()
        self._setup_ui()
        
        # --- 3. シグナルの接続（変数が準備できてから！） ---
        self.browser.page().recentlyAudibleChanged.connect(self._handle_audio_status)
        self.browser.titleChanged.connect(self._on_title_changed)
        self.browser.loadFinished.connect(self.adjust_zoom)
        self.browser.loadFinished.connect(self.inject_adblock)
        # --- 4. データのロードと表示 ---
        self.apply_config_geometry(config)
        if config.get("url"):
            self.browser.setUrl(QUrl(str(config["url"])))
        self._is_transitioning = False

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'config_at_start'):
            self.apply_config_geometry(self.config_at_start)
            del self.config_at_start

    def apply_config_geometry(self, config):
        # 1. プリセットリストと現在のインデックスを取得
        presets = config.get("presets", [])
        idx = config.get("current_preset_index", 0)

        # 2. 指定されたインデックスのプリセットが存在するかチェック
        if presets and 0 <= idx < len(presets):
            p = presets[idx]
            # プリセットから座標を適用
            self.setGeometry(
                p.get("x", DEFAULT_CONFIG["x"]),
                p.get("y", DEFAULT_CONFIG["y"]),
                p.get("width", DEFAULT_CONFIG["width"]),
                p.get("height", DEFAULT_CONFIG["height"])
            )
            print(f"Applied geometry from preset index: {idx}")
        else:
            # 3. プリセットがない場合のフォールバック（従来の挙動）
            self.setGeometry(
                config.get("x", DEFAULT_CONFIG["x"]), 
                config.get("y", DEFAULT_CONFIG["y"]), 
                config.get("width", DEFAULT_CONFIG["width"]), 
                config.get("height", DEFAULT_CONFIG["height"])
            )
            print("No valid preset found. Applied root/default geometry.")
        self.adjust_zoom()

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
        
    def inject_adblock(self):
        js_code = """
        (function() {
            // 1. 広告消去（最小限のセレクタ）
            const hideAds = () => {
                const selectors = ['.ad-container', '.ytd-ad-slot-renderer', '#masthead-ad'];
                selectors.forEach(s => {
                    const el = document.querySelector(s);
                    if (el) el.remove(); // 非表示ではなく削除することで負荷を減らす
                });
            };

            // 2. シアターモード（無限ループ防止策付き）
            let theaterDone = false;
            const forceTheater = () => {
                if (theaterDone) return; // 一度成功したら何もしない
                const player = document.querySelector('#movie_player');
                const btn = document.querySelector('.ytp-size-button');
                
                if (player && btn) {
                    if (!player.classList.contains('ytp-big-mode')) {
                        btn.click();
                    }
                    theaterDone = true; // 実行済みフラグを立てる
                }
            };

            // 3. 実行制御（監視の頻度を劇的に下げる）
            hideAds();
            setTimeout(forceTheater, 2000);

            // 監視は「動画が切り替わった時」などの大きな変化だけに絞る
            let lastUrl = location.href;
            const observer = new MutationObserver(() => {
                if (location.href !== lastUrl) {
                    lastUrl = location.href;
                    theaterDone = false; // URLが変わったらリセット
                    setTimeout(forceTheater, 2000);
                }
                hideAds();
            });
            
            // 監視対象を限定して負荷を抑える
            observer.observe(document.body, { childList: true });
        })();
        """
        self.browser.page().runJavaScript(js_code)

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
        """Enterキーで検索実行（Shift併用で逆方向）/ Googleモード時はURL判定"""
        text = self.search_bar.text().strip()
        if not text: return

        if self.search_mode == "google":
            # --- URLかどうかの判定ロジックを追加 ---
            is_url = text.startswith(('http://', 'https://')) or ('.' in text and ' ' not in text)
            
            if is_url:
                # URLなら直接移動（プロトコル補完）
                target_url = text if text.startswith(('http://', 'https://')) else f"https://{text}"
                self.browser.setUrl(QUrl(target_url))
            else:
                # URLでなければ通常のGoogle検索
                url = f"https://www.google.com/search?q={text}"
                self.browser.setUrl(QUrl(url))
            
            # 検索完了後はバーを隠してブラウザにフォーカスを戻す
            self.search_container.hide()
            self.browser.setFocus()
            
        else:
            # ページ内検索モード（既存のロジックを維持）
            modifiers = QApplication.keyboardModifiers()
            is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
            
            # Shiftがあれば逆方向
            self._find_with_count(backward=bool(is_shift))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            
            # --- Alt キーとの組み合わせ ---
            if modifiers & Qt.KeyboardModifier.AltModifier:
                if key == Qt.Key.Key_Left:
                    self.browser.back()
                    return True
                elif key == Qt.Key.Key_Right:
                    self.browser.forward()
                    return True
                # 【新規追加】Alt + W でインジケーター化（または終了）
                # elif key == Qt.Key.Key_W:
                #     self._show_indicator() # または既存の close_mini_window()
                #     return True
            
            # エンターキーの処理
            if obj == self.search_bar and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._handle_search_enter()
                return True 

            # --- Control キーとの組み合わせ ---
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
                elif key == Qt.Key.Key_R:  # 【新規追加】
                    self.browser.reload()
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
        close_action = menu.addAction("インジケーター化")
        close_action.triggered.connect(self.close_mini_window)
        menu.exec(QCursor.pos())
        
    def adjust_zoom(self):
        new_width = self.width()
        # 前回の幅と同じなら何もしない（無駄な計算を省く）
        if hasattr(self, '_last_zoom_width') and self._last_zoom_width == new_width:
            return
        # base_widthが大きいほどズームアウトして、中身を無理やり収める
        base_width = 480
        zoom_level = new_width / base_width
        zoom_level = max(0.6, min(zoom_level, 1.2))
        self.browser.setZoomFactor(zoom_level)
        self._last_zoom_width = new_width # 今回の幅を保存
        
    def resizeEvent(self, event):
        # 親クラスの標準処理をまず実行
        super().resizeEvent(event)
        # サイズ変更に合わせてズームを動的に更新
        self.adjust_zoom()
        
    def toggle_window_preset(self):
        # 1. 一時的にGUIの描画更新をオフにして、計算中の「カクつき」を見せない
        self.setUpdatesEnabled(False)
        
        try:
            # --- 既存のプリセット切り替えロジック ---
            config = load_config()
            presets = config.get("presets", [])
            self._current_preset_idx = (self._current_preset_idx + 1) % len(presets)
            
            p = presets[self._current_preset_idx]
            self.setGeometry(p["x"], p["y"], p["width"], p["height"])
            
            # 保存処理など
            config["current_preset_index"] = self._current_preset_idx
            save_config(config)
            
        finally:
            # 2. 最後に描画をオンに戻して、一気に最新状態を表示
            self.setUpdatesEnabled(True)
            self.browser.update() # 強制再描画
        
    def _handle_audio_status(self, audible):
        """音が止まってもインジケーターは消さず、状態（アイコン）だけ更新する"""
        if not self.isVisible():
            # 音が止まった（一時停止した）瞬間に、アイコンを ♪ から || に変えるために再描画
            self._show_indicator()

    def _on_title_changed(self, title):
        # hasattr を使うことで、変数が存在しない場合のクラッシュを防ぐ
        if not hasattr(self, 'audio_indicator') or self.audio_indicator is None:
            return
        """動画が切り替わるなどしてタイトルが変わった時の処理"""
        # インジケーターが表示されている（格納状態である）時だけ更新をかける
        if self.audio_indicator and self.audio_indicator.isVisible():
            self._show_indicator()

    def _show_indicator(self):
        js_code = """
        (function() {
            // 画面上に実際に見えている（表示サイズがある）ビデオ要素を探す
            var videos = Array.from(document.querySelectorAll('video'));
            var activeVideo = videos.find(v => v.offsetWidth > 0 && v.offsetHeight > 0);
            
            if (!activeVideo) return 'none';
            return activeVideo.paused ? 'paused' : 'playing';
        })();
        """
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
            layout.setContentsMargins(6, 5, 15, 5) # インジケーターの「外枠」と「中身（アイコンや文字）」の間の隙間(左, 上, 右, 下)
            layout.setSpacing(1) # 「アイコン」と「タイトル文字」の間の "距離"
            
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
            clean_title = "Resident Mini Window"

        # ラベルに値をセット
        self.icon_label.setText(icon)
        max_length = 30
        if len(clean_title) > max_length:
            display_title = clean_title[:max_length] + "..."
        else:
            display_title = clean_title
        self.text_label.setText(display_title)
        
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
        """インジケーターがクリックされた時の処理"""
        if area == "title":
            # タイトルクリックで再表示
            self.show()
            self.raise_()
            self.activateWindow()
            self._hide_audio_indicator()
        
        elif area == "icon":
            js_toggle = """
            (function() {
                var videos = document.querySelectorAll('video');
                var targetVideo = null;
                var maxH = 0;
                for (var i = 0; i < videos.length; i++) {
                    var rect = videos[i].getBoundingClientRect();
                    if (rect.height > maxH) {
                        maxH = rect.height;
                        targetVideo = videos[i];
                    }
                }
                if (targetVideo) {
                    if (targetVideo.paused) { targetVideo.play(); }
                    else { targetVideo.pause(); }
                    return true;
                }
                return false;
            })();
            """
            self.browser.page().runJavaScript(js_toggle)
            
            # 【重要】JS実行の「200ms後」に、最新の状態を反映させる
            # これにより、再生/停止が完了した後の「真の状態」をアイコンに反映できます
            QTimer.singleShot(200, self._show_indicator)
            
    def show_and_activate(self):
        # 表示前にフラグを再セットすることで、OSに「現在のコンテキスト」を再認識させる
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.show()
        self.raise_()
        self.activateWindow()
        # インジケーターを消す処理などがあればここに追加
        if self.audio_indicator:
            self.audio_indicator.hide()
            
    def toggle_indicator_mode(self):
        if self._is_transitioning: return # 処理中なら無視
        self._is_transitioning = True
        
        try:
            # 既存のトグル処理
            if self.audio_indicator and self.audio_indicator.isVisible():
                self.audio_indicator.close()
                # ここも QTimer で少し余裕を持たせる
                QTimer.singleShot(100, self.show_and_activate)
            else:
                self.hide()
                self._show_indicator()
        finally:
            # 0.5秒後にフラグを下ろす（重いサイト対策）
            QTimer.singleShot(500, self._reset_transition_flag)

    def _reset_transition_flag(self):
        self._is_transitioning = False
            
    def force_indicator_mode(self):
        """Alt+W用：既にインジケーターなら何もしない、小窓なら確実にインジケーター化する"""
        if self._is_transitioning: return
        
        # すでにインジケーターが表示中なら、重複処理を避けるために何もしない
        if self.audio_indicator and self.audio_indicator.isVisible():
            return

        self._is_transitioning = True
        try:
            # 小窓を隠してインジケーターを表示
            self.hide()
            self._show_indicator()
        finally:
            # 0.5秒後にフラグをリセット（連打防止）
            QTimer.singleShot(500, self._reset_transition_flag)

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

def save_config(config):
    """設定をJSONファイルに保存する"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"設定の保存に失敗しました: {e}")

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
    # 既存の座標データを消さないよう、URLだけを更新
    config["url"] = url
    save_config(config) # 10個目の save_config 関数を使う
    show_floating_notify("★URL Updated!")

def on_paste_signal():
    global current_window
    if not current_window: return

    config = load_config()
    
    # 1. ジオメトリの適用
    presets = config.get("presets", [])
    idx = config.get("current_preset_index", 0)

    if presets and 0 <= idx < len(presets):
        p = presets[idx]
        current_window.setGeometry(p["x"], p["y"], p["width"], p["height"])
    else:
        current_window.apply_config_geometry(config)

    # 2. URLロード
    target_url = config.get("url", DEFAULT_CONFIG["url"])
    current_window.browser.setUrl(QUrl(target_url))
    
    # 【追加ポイント】インジケーター状態なら解除して小窓を優先する
    if current_window.audio_indicator and current_window.audio_indicator.isVisible():
        current_window.audio_indicator.close()

    # 3. 小窓を表示してアクティブ化
    QTimer.singleShot(100, current_window.show_and_activate)
    
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
        is_shift = keyboard.is_pressed('shift')
        
        # 【新規追加】Alt + W
        if keyboard.is_pressed('w'):
            bridge.minimize_requested.emit() # 信号を送る
            last_action_time = now
        # Alt + S (トグル切り替え)
        elif keyboard.is_pressed('s') and not is_shift:
            bridge.show_requested.emit() # 信号を送る
            last_action_time = now
        elif keyboard.is_pressed('c'):
            bridge.copy_requested.emit()
            last_action_time = now
        elif keyboard.is_pressed('v'):
            bridge.paste_requested.emit()
            last_action_time = now
        elif keyboard.is_pressed('d') and not is_shift:
            bridge.preset_requested.emit()
            last_action_time = now

# ==========================================
# 4. メイン実行
# ==========================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # 1. まず設定を読み込む
    config = load_config()
    # 2. ウィンドウを作成
    main_window = MiniWindow(config)
    # [修正ポイント] ここでの global 宣言を削除！
    # トップレベル（関数の外）なので、代入するだけでグローバル変数になります。
    current_window = main_window 
    # 3. シグナルの配線
    bridge.copy_requested.connect(on_copy_signal)
    bridge.paste_requested.connect(on_paste_signal)
    bridge.preset_requested.connect(main_window.toggle_window_preset)
    bridge.show_requested.connect(main_window.toggle_indicator_mode)
    bridge.minimize_requested.connect(main_window.force_indicator_mode)
    # --- システム系 ---
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    monitor_timer = QTimer()
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(50)
    print("Watching Alt+C/V/D/S... (Press Ctrl+C to stop)")
    sys.exit(app.exec())