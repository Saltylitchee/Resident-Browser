import sys
import signal
import os
import re
import json
import time
import keyboard
import webbrowser
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
PROFILE_DIR = os.path.join(BASE_DIR, "portal_profile")

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

class ConfigManager:
    def __init__(self, filename="config.json"):
        self.filename = filename
        self.data = self.load_config()

    def create_default_config(self):
        """ファイルがない場合に作成されるデフォルトの構造"""
        default_data = {
            "app_settings": {
                "auto_start": False,
                "last_active_preset_index": 0,
                "global_indicator_scale": 1.0
            },
            "presets": [
                {
                    "name": "デフォルト",
                    "last_url": "https://www.google.com",
                    "indicator_styles": {
                        "shape": "rounded_rect",
                        "text_color": "#FFFFFF",
                        "bg_type": "solid",
                        "bg_color": "#2C3E50",
                        "bg_gradient": ["#2C3E50", "#000000"]
                    },
                    "favorites": [
                        {"title": "Google", "url": "https://www.google.com"}
                    ],
                    "locations": [
                        {"x": 100, "y": 100, "width": 400, "height": 300, "opacity": 0.9}
                    ]
                }
            ]
        }
        # ファイルに保存
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(default_data, f, indent=4, ensure_ascii=False)
        return default_data
    
    def load_config(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return self.create_default_config()

    def save_config(self):
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

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
            
class ResidentWindow(QMainWindow):
    def __init__(self, config_manager):  # 引数を config_manager に変更
        super().__init__()
        self.config_manager = config_manager
        self.current_location_index = 0
        # 実データ（辞書）を取り出しておく
        config_data = self.config_manager.data
        
        # --- 1. まず変数の定義をすべて済ませる ---
        self.audio_indicator = None
        
        # JSON構造に合わせて取得先を変更（app_settingsから取得）
        self._current_preset_idx = config_data["app_settings"].get("last_active_preset_index", 0)
        
        # --- 2. UIのセットアップ ---
        self.setWindowTitle("Resident Browser")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self._setup_browser()
        self._setup_ui()
        
        # --- 3. シグナルの接続 ---
        self.browser.page().recentlyAudibleChanged.connect(self._handle_audio_status)
        self.browser.titleChanged.connect(self._on_title_changed)
        self.browser.loadFinished.connect(self.adjust_zoom)
        self.browser.loadFinished.connect(self.inject_adblock)
        
        # --- 4. データのロードと表示 ---
        # プリセット情報を取得
        preset = config_data["presets"][self._current_preset_idx]
        
        # ジオメトリ（位置・サイズ）の適用
        # locations[0] を初期位置とするなどの処理に書き換えが必要かもしれません
        self.apply_config_geometry() 
        
        if preset.get("last_url"):
            self.browser.setUrl(QUrl(str(preset["last_url"])))
            
        self._is_transitioning = False

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'config_at_start'):
            self.apply_config_geometry(self.config_at_start)
            del self.config_at_start

    def _setup_browser(self):
        if not os.path.exists(PROFILE_DIR): os.makedirs(PROFILE_DIR)
        self.profile = QWebEngineProfile("PortalResidentStorage", self)
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
            console.log("ResidentWindow: Targeted Ad-Cleaning start...");
            
            const applyFix = () => {
                // 1. 強力なCSSによる「空間抹殺」（これを最初に行う）
                const styleId = 'resident-super-shield';
                if (!document.getElementById(styleId)) {
                    const style = document.createElement('style');
                    style.id = styleId;
                    style.innerHTML = `
                        /* 広告が含まれる可能性のある枠自体を消す */
                        ytd-ad-slot-renderer, 
                        ytd-companion-slot-renderer, 
                        #player-ads, 
                        .video-ads, 
                        .ytp-ad-module,
                        #masthead-ad { 
                            display: none !important; 
                        }
                        /* シアターモード時のレイアウト補正（継続） */
                        ytd-watch-flexy[theater] #columns.ytd-watch-flexy { margin: 0 !important; }
                        #primary.ytd-watch-flexy { max-width: 100% !important; padding: 0 !important; }
                    `;
                    document.head.appendChild(style);
                }

                // 2. 「ad」という文字列をIDやクラスに含む要素を全スキャンして消去
                // (これは少し過激ですが、個人開発のデバッグとしては有効です)
                const allElements = document.querySelectorAll('*');
                allElements.forEach(el => {
                    if (el.id && (el.id.includes('ad-') || el.id.includes('-ad'))) {
                    if (!el.closest('#movie_player')) el.remove(); // プレイヤー以外なら消す
                    }
                });

                // 3. 既存のチャット/シアターモード処理（成功しているものは維持）
                document.querySelectorAll('#chat, #secondary, ytd-live-chat-renderer').forEach(el => el.remove());
                
                const btn = document.querySelector('.ytp-size-button');
                const player = document.querySelector('#movie_player');
                if (btn && player && !player.classList.contains('ytp-big-mode')) {
                    btn.click();
                }
            };

            // 実行タイミングの維持
            setTimeout(applyFix, 1500); 
            setTimeout(applyFix, 4000); 
            
            window.addEventListener('scroll', applyFix, {passive: true});
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
            is_url = text.startswith(('http://', 'https://')) or ('.' in text and ' ' not in text)
            
            if is_url:
                target_url = text if text.startswith(('http://', 'https://')) else f"https://{text}"
                
                # --- 【ここが最適な挿入位置】 ---
                # YouTube LiveのURL判定（live/ だけでなく watch?v= 内のライブも含めるなら調整）
                if "youtube.com/live/" in target_url or "youtu.be/live/" in target_url:
                    import webbrowser
                    webbrowser.open(target_url)
                    self.search_container.hide() # 検索バーは閉じる
                    return # 小窓側では遷移させない
                # ------------------------------

                self.browser.setUrl(QUrl(target_url))
            else:
                url = f"https://www.google.com/search?q={text}"
                self.browser.setUrl(QUrl(url))
            
            self.search_container.hide()
            self.browser.setFocus()
            
        else:
            # ページ内検索モード
            modifiers = QApplication.keyboardModifiers()
            is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
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
        # スタイルは維持
        menu.setStyleSheet("""
            QMenu { background-color: white; border: 1px solid #999; }
            QMenu::item { padding: 5px 25px; }
            QMenu::item:selected { background-color: #3a8fb7; color: white; }
        """)
        
        menu.addAction("戻る").triggered.connect(self.browser.back)
        menu.addAction("進む").triggered.connect(self.browser.forward)
        menu.addAction("リロード").triggered.connect(self.browser.reload)
        
        menu.addSeparator()

        # 【新機能】現在のサイズパターンを保存
        save_geo_action = menu.addAction("現在のサイズをプリセットに保存")
        save_geo_action.triggered.connect(self.add_current_geometry_to_preset)

        menu.addSeparator()
        
        close_action = menu.addAction("インジケーター化")
        close_action.triggered.connect(self.close_mini_window)
        
        menu.exec(QCursor.pos())

    def add_current_geometry_to_preset(self):
        """現在の状態を『ロックされた新しいパターン』として追加する"""
        data = self.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        
        geo = self.geometry()
        new_loc = {
            "x": geo.x(),
            "y": geo.y(),
            "width": geo.width(),
            "height": geo.height(),
            "opacity": self.windowOpacity(),
            "is_locked": True # 新しく追加するものは基本「ロック」状態
        }
        
        data["presets"][idx]["locations"].append(new_loc)
        self.config_manager.save_config()
        show_floating_notify("★New Size Pattern Locked & Added!")
        
    def adjust_zoom(self):
        """現在のウィンドウ幅に基づき、プリセットごとの基準幅(base_width)に合わせてズームを調整"""
        new_width = self.width()
        if hasattr(self, '_last_zoom_width') and self._last_zoom_width == new_width:
            return
        # 1. JSONから現在のプリセットの基準幅を取得
        data = self.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        # 将来的に 'base_width' キーが追加されることを見越して get() を使用
        preset = data["presets"][idx]
        base_width = preset.get("base_width", 500)
        zoom_level = new_width / base_width
        zoom_level = max(0.4, min(zoom_level, 2.0))
        self.browser.setZoomFactor(zoom_level)
        self._last_zoom_width = new_width
        
    def resizeEvent(self, event):
        # 親クラスの標準処理をまず実行
        super().resizeEvent(event)
        # サイズ変更に合わせてズームを動的に更新
        self.adjust_zoom()
        
    def cycle_geometry(self):
        """Alt + D: 現在のプリセット内で locations を巡回する"""
        self.setUpdatesEnabled(False)
        try:
            data = self.config_manager.data
            idx = data["app_settings"]["last_active_preset_index"]
            locations = data["presets"][idx].get("locations", [])
            
            if not locations:
                return

            # インデックスを次に進める
            self.current_location_index = (self.current_location_index + 1) % len(locations)
            
            # 新しい位置・サイズを適用
            self.apply_config_geometry()
            
            # 状態通知
            show_floating_notify(f"Size Pattern: {self.current_location_index + 1}/{len(locations)}")
            
        except Exception as e:
            print(f"Error in cycle_geometry: {e}")
        finally:
            self.setUpdatesEnabled(True)
            self.browser.update()

    def apply_config_geometry(self):
        """現在のインデックスに基づき、JSONから座標・サイズ・不透明度を読み込んで適用する"""
        data = self.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        
        try:
            loc = data["presets"][idx]["locations"][self.current_location_index]
            
            # 座標とサイズの適用
            self.setGeometry(loc["x"], loc["y"], loc["width"], loc["height"])
            
            # 不透明度の適用 (キーがなければデフォルト1.0)
            opacity = loc.get("opacity", 1.0)
            self.setWindowOpacity(opacity)
            
        except (IndexError, KeyError):
            print("Failed to apply geometry: Index out of range.")
    
    def save_current_state(self):
        if not hasattr(self, 'config_manager') or self.config_manager is None:
            return
        """現在のウィンドウ位置、サイズ、URLをJSONに保存する"""
        try:
            # 1. 現在のプリセットを取得
            idx = self.config_manager.data["app_settings"]["last_active_preset_index"]
            preset = self.config_manager.data["presets"][idx]

            # 2. 基本情報の更新
            preset["last_url"] = self.browser.url().toString()

            # 3. 現在の「位置とサイズ」の保存
            # ※ Alt+Dで切り替えている最中のインデックス（current_location_indexなど）に合わせる
            loc_idx = getattr(self, 'current_location_index', 0)
            
            # リストの範囲内であることを確認して更新
            if loc_idx < len(preset["locations"]):
                geo = self.geometry()
                preset["locations"][loc_idx] = {
                    "x": geo.x(),
                    "y": geo.y(),
                    "width": geo.width(),
                    "height": geo.height(),
                    "opacity": self.windowOpacity()
                }
            # 4. ファイルへ書き出し
            self.config_manager.save_config()
            
        # ... 以降の保存処理 ...
        except Exception as e:
            print(f"Save failed: {e}")
        
    # Alt+D 実行時のメソッド（例：change_location）を修正
    def change_location(self):
        # 切り替える前に現在の状態を保存
        self.save_current_state()

        # --- 既存の切り替えロジック ---
        self.current_location_index = (self.current_location_index + 1) % len(self.current_locations)
        loc = self.current_locations[self.current_location_index]
        self.setGeometry(loc['x'], loc['y'], loc['width'], loc['height'])
        self.setWindowOpacity(loc.get('opacity', 1.0))
        # ----------------------------

        # 切り替え後のインデックス状態も保存
        self.save_current_state()
        
    def moveEvent(self, event):
        """ウィンドウが移動したときに呼ばれるイベント"""
        super().moveEvent(event)
        self._update_geometry_if_unlocked()

    def resizeEvent(self, event):
        """ウィンドウサイズが変わったときに呼ばれるイベント"""
        super().resizeEvent(event)
        self._update_geometry_if_unlocked()
        
    def closeEvent(self, event):
        self.save_current_state()
        super().closeEvent(event)
        
    def _update_geometry_if_unlocked(self):
        """ロックされていなければ、現在の座標・サイズをJSONに即時反映する"""
        # 準備ができていない場合はスキップ（以前実装したガード節）
        if not hasattr(self, 'config_manager') or self.config_manager is None:
            return

        data = self.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        loc_idx = getattr(self, 'current_location_index', 0)

        try:
            target_location = data["presets"][idx]["locations"][loc_idx]
            
            # 【核心】ロックされている場合は、メモリ上のデータもファイルも更新しない
            if target_location.get("is_locked", True):
                return

            # アンロック状態なら、現在の状態を保存
            geo = self.geometry()
            target_location.update({
                "x": geo.x(),
                "y": geo.y(),
                "width": geo.width(),
                "height": geo.height()
            })
            
            # 物理ファイルに書き出し
            self.config_manager.save_config()
            
        except (IndexError, KeyError):
            pass
        
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
        """ConfigManagerから最新のスタイル（色・背景・スケール）を読み込んで表示"""
        # --- 1. データの取得 ---
        data = self.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        preset = data["presets"][idx]
        styles = preset.get("indicator_styles", {})
        scale = data["app_settings"].get("global_indicator_scale", 1.0)

        # 設定値の抽出
        text_color = styles.get("text_color", "#00FF00")
        bg_color = styles.get("bg_color", "#2C3E50")
        
        # --- 2. インジケーターの生成・レイアウト構築 (初回のみ) ---
        if not self.audio_indicator:
            # ClickableLabel は既存のクラスを使用
            self.audio_indicator = ClickableLabel("", None)
            self.audio_indicator.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | 
                Qt.WindowType.WindowStaysOnTopHint | 
                Qt.WindowType.Tool
            )
            self.audio_indicator.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.audio_indicator.clicked.connect(self._handle_indicator_click)
            
            layout = QHBoxLayout(self.audio_indicator)
            self.icon_label = QLabel()
            self.text_label = QLabel()
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            layout.addWidget(self.icon_label)
            layout.addWidget(self.text_label)

        # --- 3. スタイルの適用 (スケール対応) ---
        scaled_font_size = int(10 * scale)
        container_radius = int(5 * scale)
        icon_width = int(30 * scale)
        
        # コンテナ全体のスタイル（背景色と枠線）
        container_style = f"""
            background-color: {bg_color};
            border: 1px solid {text_color};
            border-radius: {container_radius}px;
        """
        self.audio_indicator.setStyleSheet(container_style)

        # ラベルのスタイル（文字色とフォントサイズ）
        label_style = f"""
            color: {text_color};
            font-weight: bold;
            font-size: {scaled_font_size}pt;
            background: transparent;
            border: none;
        """
        self.icon_label.setStyleSheet(label_style)
        self.text_label.setStyleSheet(label_style)
        
        self.icon_label.setFixedWidth(icon_width)
        self.audio_indicator.layout().setContentsMargins(
            int(6 * scale), int(5 * scale), int(15 * scale), int(5 * scale)
        )

        # --- 4. 状態に応じたコンテンツの更新 ---
        # アイコンの決定
        if state == 'playing':
            icon = "♪"
        elif state == 'paused':
            icon = "||"
        else:
            icon = "❏"
        self.icon_label.setText(icon)

        # タイトルの整形
        raw_title = self.browser.title()
        clean_title = re.sub(r'^\(\d+\)\s*', '', raw_title)
        if not clean_title or clean_title == "about:blank": 
            clean_title = "Resident Browser"

        max_length = 30
        display_title = (clean_title[:max_length] + "...") if len(clean_title) > max_length else clean_title
        self.text_label.setText(display_title)

        # --- 5. 配置と表示 ---
        self.audio_indicator.adjustSize()
        screen_rect = QApplication.primaryScreen().geometry()
        self.audio_indicator.move(
            screen_rect.width() - self.audio_indicator.width() - 10, 
            screen_rect.height() - 80 # タスクバーとの干渉を考慮
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

def get_portal_url():
    """アクティブなブラウザからURLを抽出する (自分自身やフォルダは除外)"""
    try:
        from pywinauto import Desktop
        # 1. ターゲットとなるブラウザのタイトル候補 (必要に応じて追加)
        # 自身のタイトル "Resident Window" を含むものは明示的に除外
        target_windows = [
            w for w in Desktop(backend="uia").windows(visible_only=True) 
            if ("Chrome" in w.window_text() or "Edge" in w.window_text() or "Firefox" in w.window_text() or "Brave" in w.window_text())
            and "Resident Window" not in w.window_text() 
            and w.class_name() != "CabinetWClass"
        ]
        
        if not target_windows:
            return None

        # 2. 最初に見つかったブラウザウィンドウから Edit（アドレスバー）を探す
        all_edits = target_windows[0].descendants(control_type="Edit")
        for edit in all_edits:
            try:
                val = edit.get_value()
                if val and ("http" in val or "." in val):
                    # プロトコル補完
                    return "https://" + val if not val.startswith("http") else val
            except:
                continue
    except:
        return None
    return None

def on_copy_signal():
    """Alt + C: クリップボードを汚さず、直接JSONのlast_urlを更新する"""
    url = get_portal_url()
    if not url:
        return

    if current_window:
        data = current_window.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        
        if 0 <= idx < len(data["presets"]):
            # 直接JSONデータを書き換え
            data["presets"][idx]["last_url"] = url
            # 物理ファイルへ即時保存
            current_window.config_manager.save_config()
            
            # クリップボードへの setText は削除（汚さないため）
            show_floating_notify("★Target URL Saved to JSON!")

def on_paste_signal():
    """Alt + V: クリップボードを優先し、使用後はクリアする。なければJSONから復元"""
    if current_window:
        data = current_window.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        preset = data["presets"][idx]
        
        # 1. 座標適用
        current_window.apply_config_geometry()
        
        # 2. URL決定
        clipboard = QApplication.clipboard()
        clipboard_text = clipboard.text().strip()
        
        is_from_clipboard = False
        if clipboard_text.startswith("http"):
            target_url = clipboard_text
            is_from_clipboard = True
        else:
            target_url = preset.get("last_url", "https://www.google.com")

        current_window.browser.setUrl(QUrl(target_url))
        
        # 3. 使い捨て処理：クリップボードから取得した場合のみクリア
        if is_from_clipboard:
            clipboard.clear()
            show_floating_notify("★URL Loaded & Clipboard Cleared")
        else:
            show_floating_notify("★URL Restored from History")

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

def main():
    app = QApplication(sys.argv)
    # トレイアイコンなどを活用する場合、最後の中を閉じても終了させない
    app.setQuitOnLastWindowClosed(False)

    # 1. ConfigManager のインスタンス化 (load_config() を置き換え)
    config_manager = ConfigManager()

    # 2. ウィンドウを作成 (config_manager を注入)
    main_window = ResidentWindow(config_manager)
    
    # 外部（check_hotkeysなど）から参照が必要な場合はグローバルに保持
    global current_window
    current_window = main_window

    # 3. シグナルの配線 (Bridge は既存のものを使用)
    bridge.copy_requested.connect(on_copy_signal)
    bridge.paste_requested.connect(on_paste_signal)
    bridge.preset_requested.connect(main_window.cycle_geometry)
    bridge.show_requested.connect(main_window.toggle_indicator_mode)
    bridge.minimize_requested.connect(main_window.force_indicator_mode)

    # --- システム・監視系 ---
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    monitor_timer = QTimer()
    # Python の参照保持のため、タイマーを main_window などに紐付けておくと安全
    monitor_timer.setParent(main_window) 
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(50)

    print("Watching Alt+C/V/D/S... (Press Ctrl+C to stop)")
    
    # メインウィンドウを表示
    main_window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()