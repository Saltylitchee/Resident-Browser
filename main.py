import sys
import signal
import os
import re
import json
import time
import keyboard
import traceback
import shutil
import requests
from datetime import datetime
from urllib.parse import urlparse
from enum import Enum, auto
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QStatusBar, QMainWindow, QLabel
)
from PyQt6.QtCore import (
    Qt, QUrl, QEvent, QTimer, QObject, pyqtSignal, QPropertyAnimation, QEasingCurve, QByteArray, QRect, QSize
)
from PyQt6.QtGui import QCursor, QPainter, QBrush, QColor, QPen, QShortcut, QKeySequence
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings

# ==========================================
# 1. 定数・デフォルト設定の集約
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PROFILE_DIR = os.path.join(BASE_DIR, "portal_profile")

JS_COPY_SCRIPT = "window.getSelection().toString();"

# ==========================================
# 2. クラス定義
# ==========================================
class Bridge(QObject):
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()
    show_requested = pyqtSignal()
    hide_completely_requested = pyqtSignal()
    cycle_geometry_requested = pyqtSignal() 
    # Alt + 1~9 用（プリセット自体の切り替え）
    # int を引数に取ることで、どの番号が押されたかを受け渡せます
    preset_switch_requested = pyqtSignal(int)

bridge = Bridge()
current_window = None
_notif = None

class DisplayMode(Enum):
    EXPANDED = auto()   # 小窓
    COLLAPSED = auto()  # インジケーター
    HIDDEN = auto()     # 潜伏

class ConfigManager:
    DEFAULT_CONFIG = {
        "app_settings": {
            "auto_start": False,
            "show_notifications": True,
            "last_active_preset_index": 0,
            "global_indicator_scale": 1.0,
            "selectors_url": None,
            "search_mode": "google",
            "developer_notes": {
                "reference_url": "https://gemini.google.com/app",
                "last_modified": "2026-05-10"
            },
            "shortcuts": { # ここもファイルに合わせて追加
                "modifier": "alt",
                "hide_completely": "w",
                "show_toggle": "s",
                "copy": "c",
                "paste": "v",
                "cycle_size": "d"
            }
        },
        "presets": [
            {
                "name": "デフォルト",
                "last_url": "https://www.google.com",
                "base_width": 500,
                "indicator_styles": {
                    "shape": "rounded_rect",
                    "max_title_length": 25,
                    "text_color": "#00FF00",
                    "bg_color": "#000000",
                    "indicator_bg_alpha": 220,
                    "notification_color": "#00FF00",
                    "notif_bg_color": "#3A01C1",
                    "notif_bg_alpha": 220
                },
                "locations": [
                    { "x": 100, "y": 100, "width": 400, "height": 300, "opacity": 1.0, "is_locked": True }
                ],
                "last_location_index": 0
            }
        ],
        "search_mode": "google"
    }

    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        # 初期化時に読み込みと整合性チェックを完結させる
        self.data = self.load_config()
        print(f"DEBUG: Config saved to >>> {os.path.abspath(self.config_path)}")

    # --- 2. 読み込みロジックの改善 ---
    def load_config(self):
        if not os.path.exists(self.config_path):
            return self.save_default_config()

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
            
            # デフォルト構造をベースに、ロードしたデータを上書き
            # これにより、プログラム更新で新しい設定項目が増えても、既存ファイルが壊れません
            return self._deep_merge(self.DEFAULT_CONFIG.copy(), loaded_data)

        except (json.JSONDecodeError, Exception) as e:
            print(f"[Config] Corruption detected: {e}")
            self._backup_corrupted_config()
            return self.save_default_config()
        
    def save_config(self):
        try:
            # 保存前にディレクトリチェック（Windows環境での安定性のため）
            os.makedirs(os.path.dirname(os.path.abspath(self.config_path)), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[Config] Save failed: {e}")
        print(f"DEBUG: Config saved to >>> {os.path.abspath(self.config_path)}")

    def save_default_config(self):
        self.data = self.DEFAULT_CONFIG.copy()
        self.save_config()
        return self.data

    def _deep_merge(self, base, update):
        """再帰的に辞書をマージし、古い設定ファイルに新機能のキーを補完する"""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                # リスト（presetsなど）の場合は、単純上書きでも良いですが、
                # 将来的に「リスト内の要素数」が変わる場合はここを拡張します。
                base[key] = value
        return base

    def _backup_corrupted_config(self):
        if os.path.exists(self.config_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.config_path}_{timestamp}.bak"
            shutil.copy(self.config_path, backup_path)
            print(f"[Config] Backup saved to: {backup_path}")
        
class SelectorManager:
    def __init__(self, local_path="selectors.json", remote_url=None):
        self.local_path = local_path
        self.remote_url = remote_url
        self.selectors = self._load_selectors()

    def _load_selectors(self):
        """起動時にセレクタを読み込む（リモート優先 ⇄ ローカルフォールバック）"""
        if self.remote_url:
            try:
                # タイムアウトを短めに設定し、起動を妨げないようにする
                response = requests.get(self.remote_url, timeout=3)
                if response.status_code == 200:
                    new_data = response.json()
                    self._save_local(new_data)
                    return new_data
            except Exception as e:
                print(f"[SelectorManager] Remote sync failed: {e}. Using local cache.")
        
        return self._load_local_file()

    def _load_local_file(self):
        try:
            if os.path.exists(self.local_path):
                with open(self.local_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"[SelectorManager] Failed to load local file: {e}")
        return {}

    def _save_local(self, data):
        try:
            with open(self.local_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[SelectorManager] Failed to save cache: {e}")

    def get_data_for_url(self, url):
        """URLからドメインを抽出し、正確にマッチングさせる"""
        if not self.selectors:
            return None
        # URLから純粋なホスト名（例: www.youtube.com）を取得
        try:
            parsed_url = urlparse(url)
            hostname = parsed_url.netloc
        except:
            return None
        # ドメインの部分一致判定を少し厳格にする
        for domain_key, data in self.selectors.items():
            if domain_key in hostname:
                return data
        return None
    
    
    
class FloatingNotification(QWidget):
    def __init__(self, text, color="#00FF7F", bg_color="#2C3E50", bg_alpha=220, duration=2000):
        super().__init__(None)
        
        # 属性の保持
        self.text = text
        self.accent_color = color
        self.bg_color = bg_color
        self.bg_alpha = bg_alpha # 0-255 で指定
        
        self._init_window_attributes()
        self._setup_ui()
        self._setup_animation()
        
        # 実行
        self.start_show(duration)

    def _init_window_attributes(self):
        """ウィンドウの振る舞いを設定"""
        self.setWindowFlags(
            Qt.WindowType.Tool | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def _setup_ui(self):
        """UIコンポーネントの構築と色設定"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.label = QLabel(self.text)
        
        # 背景色のRGBA化（alphaをconfigから反映）
        c = QColor(self.bg_color)
        rgba_bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, {self.bg_alpha})"
        
        self.label.setStyleSheet(f"""
            background-color: {rgba_bg}; 
            color: {self.accent_color}; 
            border: 1px solid {self.accent_color};
            border-radius: 8px;
            padding: 10px 20px;
            font-weight: bold;
            font-size: 13px;
            font-family: 'Segoe UI', Arial;
        """)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        # サイズと位置の確定
        self.adjustSize()
        screen_geo = QApplication.primaryScreen().availableGeometry()
        x = (screen_geo.width() - self.width()) // 2
        y = 80
        self.move(x, y)

    def _setup_animation(self):
        """フェードアニメーション設定"""
        self.setWindowOpacity(0.0)
        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    # paintEvent は setStyleSheet で代用できるため削除しました

    def start_show(self, duration):
        """フェードイン -> 待機 -> フェードアウト"""
        self.show()
        self.animation.stop() # 念のため停止
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDirection(QPropertyAnimation.Direction.Forward)
        self.animation.start()
        
        # 指定時間後にフェードアウトを開始
        QTimer.singleShot(duration, self.start_fade_out)

    def start_fade_out(self):
        """フェードアウトして閉じる"""
        # アニメーションが実行中なら一旦停止
        if self.animation.state() == QPropertyAnimation.State.Running:
            self.animation.stop()
            
        self.animation.setDirection(QPropertyAnimation.Direction.Backward)
        # 以前の接続があれば解除（多重接続防止）
        try: self.animation.finished.disconnect()
        except: pass
        
        self.animation.finished.connect(self.close)
        self.animation.start()

class ClickableLabel(QLabel):
    clicked = pyqtSignal(str)

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # 内部状態の初期値（config読み込みまでの安全なデフォルト）
        self._text_color = "#00FF00"
        self._bg_color = "#2C3E50"
        self._bg_alpha = 220  # 0 (透明) - 255 (不透明)
        self._shape = "rounded_rect"

    def set_custom_style(self, text_color, bg_color, shape, bg_alpha):
        """
        config.json から取得したスタイルを適用する。
        bg_alpha: 0-255 の整数
        """
        self._text_color = text_color
        self._bg_color = bg_color
        self._shape = shape
        self._bg_alpha = max(0, min(255, int(bg_alpha))) # 範囲外の値をガード
        self.update() # paintEventをトリガー

    def paintEvent(self, event):
        """背景・枠線・形状のカスタム描画"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 境界線の太さ（2px）を考慮して、描画範囲を少し内側に絞る
        pen_width = 2
        rect = self.rect().adjusted(pen_width, pen_width, -pen_width, -pen_width)

        # 色オブジェクトの生成（config値を適用）
        bg = QColor(self._bg_color)
        bg.setAlpha(self._bg_alpha) # ここでconfigの透明度を反映
        
        border = QColor(self._text_color)

        # ブラシ（塗りつぶし）とペン（枠線）の設定
        painter.setBrush(QBrush(bg))
        pen = QPen(border)
        pen.setWidth(pen_width)
        painter.setPen(pen)

        # 形状に基づく描画ロジック
        if self._shape == "circle":
            side = min(rect.width(), rect.height())
            circle_rect = QRect(
                rect.left() + (rect.width() - side) // 2,
                rect.top() + (rect.height() - side) // 2,
                side, side
            )
            painter.drawEllipse(circle_rect)
        elif self._shape == "rect":
            painter.drawRect(rect)
        elif self._shape == "capsule":
            radius = rect.height() // 2
            painter.drawRoundedRect(rect, radius, radius)
        else:  # rounded_rect (デフォルト)
            painter.drawRoundedRect(rect, 15, 15)
        
        painter.end()
        
        # 最後に親の paintEvent を呼び、上にテキストを描画させる
        super().paintEvent(event)
        
    def mousePressEvent(self, event):
        """
        クリックされた座標に基づき、'icon' 領域か 'title' 領域かを判定して発火。
        """
        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Circleモードの場合は、形状全体をひとつのボタン（icon扱い）として処理
        if self._shape == "circle":
            self.clicked.emit("icon")
            return

        # 動的な境界判定：最初のウィジェット（アイコンラベル）の右端を境界とする
        layout = self.layout()
        if layout and layout.count() > 0:
            margin_left = layout.contentsMargins().left()
            icon_item = layout.itemAt(0).widget()
            
            # アイコンウィジェットが存在すればその幅を、無ければデフォルト30を使用
            icon_w = icon_item.width() if icon_item else 30
            boundary = margin_left + icon_w
            
            if event.pos().x() < boundary:
                self.clicked.emit("icon")
            else:
                self.clicked.emit("title")
        else:
            # レイアウトが未構築の場合は安全のため title 扱いにする
            self.clicked.emit("title")
            
class ResidentMiniPlayer(QMainWindow):
    def __init__(self, config_manager, selector_manager):
        super().__init__()
        # --- 1. すべてのインスタンス変数を「最初」に初期化する ---
        self.config_manager = config_manager
        self.selector_manager = selector_manager
        self.current_mode = DisplayMode.EXPANDED
        self._is_switching_mode = False  # ここに移動！
        self.collapsed_indicator = None
        self.all_selectors = {}
        self.current_location_index = 0
        # データの準備
        config_data = self.config_manager.data
        self._current_preset_idx = config_data["app_settings"].get("last_active_preset_index", 0)
        self.current_location_index = config_data["presets"][self._current_preset_idx].get("last_location_index", 0)
        # --- 2. UIとブラウザのセットアップ ---
        self.setWindowTitle("Doppel")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self._setup_browser()
        self._setup_ui()
        # --- 3. ジオメトリの適用 ---
        self.apply_config_geometry() 
        # --- 4. シグナルの接続 ---
        self.browser.page().recentlyAudibleChanged.connect(self._handle_audio_status)
        self.browser.titleChanged.connect(self._on_title_changed)
        self.browser.loadFinished.connect(self.inject_adblock)
        self.browser.urlChanged.connect(lambda: self.inject_adblock()) 
        self.browser.loadFinished.connect(self.adjust_zoom)
        self.browser.loadFinished.connect(self._on_load_finished)
        # --- 5. URLのロード ---
        def start_initial_load():
            preset = config_data["presets"][self._current_preset_idx]
            if self.width() > 800:
                self._set_desktop_cookie_directly()
                self.browser.setZoomFactor(0.8)
                self.profile.setHttpUserAgent(self.ua_desktop)
            if preset.get("last_url"):
                url_str = str(preset["last_url"])
                # URLに字幕オフのパラメータを追加
                # cc_load_policy=0: 字幕をデフォルトで非表示にする
                if "?" in url_str:
                    url_str += "&cc_load_policy=0"
                else:
                    url_str += "?cc_load_policy=0"
                self.browser.setUrl(QUrl(url_str))
        # 直接呼び出すのではなく、タイマーで一瞬だけ遅らせる
        # これにより、Geometry（サイズ）が確実にOS側で適用された後にリクエストが飛ぶ
        QTimer.singleShot(50, start_initial_load)
        # セレクターのロード
        self.load_selectors()
        self.reload_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.reload_shortcut.activated.connect(self.reload_and_apply)
        
    def handle_show_request(self):
        """
        Alt+S（表示リクエスト）に対する意思決定。
        何を表示するか、どのモードにするかはクラス自身が判断する。
        """
        if not self.has_valid_content():
            self._display_preset_notification("No URL to show. Press Alt+C.")
            return

        # モードのトグル
        target = (DisplayMode.EXPANDED 
                if self.current_mode != DisplayMode.EXPANDED 
                else DisplayMode.COLLAPSED)
        self.update_display_mode(target)
        
    def update_display_mode(self, target_mode: DisplayMode):
        if self._is_switching_mode: return
        self._is_switching_mode = True

        try:
            # 1. 既存表示のクリーンアップ
            if self.collapsed_indicator:
                self.collapsed_indicator.hide()

            # 2. ターゲットモードへの遷移
            if target_mode == DisplayMode.EXPANDED:
                self.show_and_activate()
                self.current_mode = DisplayMode.EXPANDED

            elif target_mode == DisplayMode.COLLAPSED:
                self.hide()
                self._show_indicator()
                self.current_mode = DisplayMode.COLLAPSED

            elif target_mode == DisplayMode.HIDDEN:
                self.hide()
                self.current_mode = DisplayMode.HIDDEN
                self._display_preset_notification("★Stealth Mode Activated") # 潜伏したことを通知

        finally:
            QTimer.singleShot(500, self._reset_transition_flag)
            
    def has_valid_content(self):
        """
        ブラウザに有効なURLが読み込まれているかチェックする
        """
        url = self.browser.url().toString()
        # 空、about:blank、または初期状態でないことを確認
        return bool(url) and url != "about:blank" and url != ""
    
    def _set_desktop_cookie_directly(self):
        """サーバーへの最初のリクエストに間に合うようにクッキーをセットする"""
        # QByteArray を使う場合も、文字列をそのまま入れる形式に変更します
        cookie = QNetworkCookie(QByteArray(b"PREF"), QByteArray(b"f6=40000"))
        # ここを bytes(b".youtube.com") ではなく str(".youtube.com") に修正
        cookie.setDomain(".youtube.com")
        cookie.setPath("/")
        # クッキーをストアに登録
        self.browser.page().profile().cookieStore().setCookie(cookie)
        
    def capture_current_url(self):
        """Alt + C相当：現在のURLをJSONに保存"""
        url = get_portal_url() # これは外部関数として維持でOK
        if not url: return

        # セルフ（自分自身）のコンフィグを更新
        data = self.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        
        if 0 <= idx < len(data["presets"]):
            data["presets"][idx]["last_url"] = url
            self.config_manager.save_config()
            self._display_preset_notification("★Target URL Saved!")

    def apply_url_from_dispatch(self):
        """Alt + V相当：クリップボードまたは履歴からURLを展開"""
        data = self.config_manager.data
        idx = data["app_settings"]["last_active_preset_index"]
        preset = data["presets"][idx]
        
        # 座標適用
        self.apply_config_geometry()
        
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        
        if text.startswith("http"):
            target_url = text
            clipboard.clear()
            self._display_preset_notification("★URL Loaded from Clipboard")
        else:
            target_url = preset.get("last_url", "https://www.google.com")
            self._display_preset_notification("★URL Restored from History")

        self.browser.setUrl(QUrl(target_url))
        self.update_display_mode(DisplayMode.EXPANDED) # 貼り付けたら即・小窓へ
        
    def _display_preset_notification(self, text):
        """現在のプリセット設定に基づいた通知を表示する"""
        # 1. 表示設定のチェック
        settings = self.config_manager.data.get("app_settings", {})
        if not settings.get("show_notifications", True):
            return
        # 2. スタイルデータの抽出
        try:
            data = self.config_manager.data
            idx = settings.get("last_active_preset_index", 0)
            # プリセットリストが空の場合の安全策
            preset = data.get("presets", [{}])[idx]
            styles = preset.get("indicator_styles", {})
        except (IndexError, TypeError):
            styles = {}
        # 3. 色の決定（個別設定 > インジケーター設定 > デフォルト値 の優先順位）
        # notification_color が無ければ text_color を、それも無ければ SpringGreen を使う
        text_color = styles.get("notification_color") or styles.get("text_color") or "#00FF7F"
        # notif_bg_color が無ければ 濃いグレー を使う
        bg_color = styles.get("notif_bg_color", "#2C3E50")
        # 4. 通知インスタンスの生成と表示
        bg_alpha = styles.get("notif_bg_alpha", 220)
        self._last_notification = FloatingNotification(
            text, 
            color=text_color, 
            bg_color=bg_color,
            bg_alpha=bg_alpha
        )
        
        
        
    def apply_preset(self, index):
        """
        指定されたインデックスのプリセットをアプリ全体に適用する
        """
        data = self.config_manager.data
        # ガード：存在しないインデックスが指定された場合
        if index >= len(data["presets"]):
            self._display_preset_notification(f"Preset {index + 1} not defined.")
            return
        # 1. 切り替える前に、現在の（古い）プリセットの状態を保存
        self.save_current_state()
        # 2. 設定データの更新
        data["app_settings"]["last_active_preset_index"] = index
        # プリセットを跨ぐ際、サイズパターンのインデックスは 0（メインサイズ）に戻す
        self.current_location_index = 0 
        # 3. UIと外観の同期
        self.refresh_favorites_ui()       # お気に入りボタン更新
        self.apply_config_geometry()      # 位置・サイズ更新
        self.update_indicator_style()     # インジケーターの色更新
        # 4. コンテンツのロード
        # プリセットに記録されている最後のURLを復元
        target_preset = data["presets"][index]
        last_url = target_preset.get("last_url", "https://www.google.com")
        self.browser.setUrl(QUrl(last_url))
        # 5. 設定の永続化
        self.config_manager.save_config()
        self._display_preset_notification(f"Switch -> {target_preset['name']}")
        
    def update_indicator_style(self):
        """
        現在のプリセットに応じたインジケーターのスタイル（色など）を適用
        """
        data = self.config_manager.data
        idx = data["app_settings"].get("last_active_preset_index", 0)
        
        # プリセットごとにテーマカラーを持たせる拡張を見越した実装
        # （現在は暫定で固定、またはプリセット名に応じて色を変える等）
        preset_name = data["presets"][idx].get("name", "")
        
        # 例：特定のワードが含まれていたら色を変える、といった遊び心も可能
        color = "#3a8fb7" # デフォルト
        if "YouTube" in preset_name: color = "#ff0000"
        elif "Work" in preset_name: color = "#2ecc71"

        self.indicator.setStyleSheet(f"""
            background-color: {color};
            border-radius: 5px;
        """)
        
        
        
        
    def refresh_favorites_ui(self):
        """
        現在のプリセットに基づいてお気に入りボタンを再生成する
        """
        # 1. 既存のボタンを安全に削除
        # レイアウト内のアイテムを後ろから順番に削除していくのがQtの定石です
        while self.fav_layout.count():
            item = self.fav_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater() # メモリ解放

        # 2. 現在のプリセット情報を取得
        config_data = self.config_manager.data
        idx = config_data["app_settings"].get("last_active_preset_index", 0)
        
        try:
            current_preset = config_data["presets"][idx]
            favs = current_preset.get("favorites", [])
            
            # 3. 新しいボタンを生成して追加
            for url in favs[:2]:
                display_text = url.replace("https://", "").replace("www.", "")[0].upper()
                btn = QPushButton(display_text) 
                btn.setFixedSize(24, 24)
                btn.setToolTip(url)
                btn.setStyleSheet(self._get_fav_btn_style()) # スタイルも共通化
                btn.clicked.connect(lambda checked, u=url: self.browser.setUrl(QUrl(u)))
                self.fav_layout.addWidget(btn)
                
        except (IndexError, KeyError) as e:
            print(f"Error refreshing favorites: {e}")

    def _get_fav_btn_style(self):
        """お気に入りボタンのスタイルを返す（保守性のため分離）"""
        return """
            QPushButton {
                background-color: white; 
                border: 1px solid #ccc; 
                border-radius: 3px; 
                font-weight: bold;
            }
            QPushButton:hover { background-color: #e0e0e0; }
        """
        
        
        

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'config_at_start'):
            self.apply_config_geometry(self.config_at_start)
            del self.config_at_start

    def _setup_browser(self):
        if not os.path.exists(PROFILE_DIR): os.makedirs(PROFILE_DIR)
        self.profile = QWebEngineProfile("PortalResidentStorage", self)
        self.profile.setPersistentStoragePath(PROFILE_DIR)

        self.ua_desktop = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        self.profile.setHttpUserAgent(self.ua_desktop)
        
        # --- セキュリティ設定の緩和 ---
        s = self.profile.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, True)

        self.page = QWebEnginePage(self.profile, self)
        self.browser = QWebEngineView()
        self.browser.setPage(self.page)
        
        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.browser.installEventFilter(self)
        self.page.loadFinished.connect(self._install_proxy_filter)
        
    def inject_adblock(self):
        current_url = self.browser.url().host()
        
        # --- 1. 設定ファイルの読み込みと検証 ---
        try:
            # 実行ファイルからの絶対パスを取得（Windows環境での安定性向上）
            base_path = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(base_path, "selectors.json")

            if not os.path.exists(json_path):
                # ファイルがない場合はログを出して早期リターン
                print(f"WARNING: '{json_path}' not found. Skipping injection.")
                return

            with open(json_path, "r", encoding="utf-8") as f:
                all_selectors = json.load(f)
                
        except json.JSONDecodeError as e:
            # JSONの書き方が間違っている場合
            print(f"ERROR: Failed to parse 'selectors.json'. Line {e.lineno}, Col {e.colno}: {e.msg}")
            return
        except Exception:
            # その他の予期せぬエラー（権限エラーなど）
            print("ERROR: Unexpected error while loading selectors.json")
            print(traceback.format_exc()) # エラーの詳細（スタックトレース）を出力
            return

        # --- 2. ドメイン設定の取得 ---
        # ドメインの部分一致（youtube.comなど）を確認
        site_config = next((config for domain, config in all_selectors.items() if domain in current_url), None)
        
        if not site_config:
            # 設定がないドメインはエラーではなく、単に何もしないのが「汎用型」として正しい挙動
            return

        # --- 3. JS注入（置換処理） ---
        try:
            js_template = """
            (function() {
                const run = () => {
                    const targets = __REMOVE_LIST__;
                    targets.forEach(s => {
                        const el = document.querySelector(s);
                        if (el) el.style.display = 'none';
                    });

                    const styleId = 'resident-shield-v4';
                    let styleTag = document.getElementById(styleId);
                    if (!styleTag) {
                        styleTag = document.createElement('style');
                        styleTag.id = styleId;
                        (document.head || document.documentElement).appendChild(styleTag);
                    }
                    styleTag.textContent = "__CSS_CONTENT__";
                };
                run();
                setTimeout(run, 1000);
                setTimeout(run, 3000);
            })();
            """

            # 必要な値が site_config に含まれているかチェック
            remove_list = json.dumps(site_config.get("remove", []))
            css_content = site_config.get("css", "")

            final_js = js_template.replace("__REMOVE_LIST__", remove_list)
            final_js = final_js.replace("__CSS_CONTENT__", css_content)

            self.browser.page().runJavaScript(final_js)
            
        except Exception as e:
            print(f"ERROR: Script injection failed for {current_url}: {e}")
            
    def load_selectors(self):
        """外部設定ファイルを読み込み、メモリ上の変数に格納する"""
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(base_path, "selectors.json")
            
            with open(json_path, "r", encoding="utf-8") as f:
                # クラス変数（self.all_selectors）に保存することで、どこからでも参照可能にする
                self.all_selectors = json.load(f)
            
            print("SUCCESS: selectors.json reloaded.")
            return True
        except Exception as e:
            print(f"ERROR: Failed to reload selectors.json: {e}")
            return False
        
    def reload_and_apply(self):
        """設定を読み直して、現在のページに即適用する"""
        if self.load_selectors():
            # 読み込みに成功したら、現在のページに最新設定を注入
            self.inject_adblock()
            # ユーザーに知らせる（ステータスバーがある場合）
            self.statusBar().showMessage("Settings reloaded and applied!", 3000)
            
            
            

    def _install_proxy_filter(self):
        """読み込み完了時に呼ばれる。イベントフィルタの設置とデスクトップ表示の強制を行う"""
        if self.browser.focusProxy():
            self.browser.focusProxy().installEventFilter(self)
        # ページ読み込み完了時にデスクトップ化を実行
        self._force_desktop_layout()

    def _force_desktop_layout(self):
        if not hasattr(self, 'page') or self.page is None or self.width() <= 800:
            return

        script = """
        (function() {
            // 1. YouTubeのモバイル専用要素を隠す
            var m_web = document.getElementsByTagName('ytm-app')[0];
            if (m_web) { m_web.style.display = 'none'; }

            // 2. Cookieの再セット（デスクトップ設定の維持）
            try {
                document.cookie = "PREF=f6=40000; domain=.youtube.com; path=/";
            } catch (e) {
                console.warn("Cookie injection blocked by browser security.");
            }
            
            // 3. YouTubeの内部フラグ書き換え
            if (window.yt && window.yt.config_) {
                window.yt.config_.EXPERIMENT_FLAGS.kevlar_is_mweb_modern_f_and_e_interaction = false;
            }
            
            var target = document.getElementsByTagName('head')[0] || document.documentElement;
            if (target) {
                target.appendChild(newMeta);
            }
            
            // 4. ViewportをPCサイズで固定
            var meta = document.querySelector('meta[name="viewport"]');
            if (meta) { 
                meta.setAttribute('content', 'width=1280, initial-scale=1.0');
            } else {
                var newMeta = document.createElement('meta');
                newMeta.name = "viewport";
                newMeta.content = "width=1280";
                document.getElementsByTagName('head')[0].appendChild(newMeta);
            }

            // 5. 字幕（CC）を強制オフにするロジック
            var disableSubtitles = function() {
                // YouTubeプレーヤーの字幕ボタンを取得
                var ccButton = document.querySelector('.ytp-subtitles-button');
                // ボタンが存在し、かつ「押されている（オン）」状態ならクリックしてオフにする
                if (ccButton && ccButton.getAttribute('aria-pressed') === 'true') {
                    ccButton.click();
                    console.log("Subtitles disabled by ResidentBrowser");
                }
            };

            // 即時実行と、要素のレンダリング待ちを考慮した遅延実行（1秒後）
            disableSubtitles();
            setTimeout(disableSubtitles, 1000);

            // 6. YouTubeに「画面サイズが変わったぞ」と叫ぶ
            window.dispatchEvent(new Event('resize'));
        })();
        """
        self.page.runJavaScript(script)

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # --- 検索バーコンテナ ---
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
        self.search_bar.installEventFilter(self)
        self.search_bar.setPlaceholderText("Google Search...")
        self.search_bar.returnPressed.connect(self._handle_search_enter)
        s_layout.addWidget(self.search_bar)

        # お気に入りグループ
        self.fav_group = QWidget()
        fav_layout = QHBoxLayout(self.fav_group)
        fav_layout.setContentsMargins(0,0,0,0)
        fav_layout.setSpacing(5)

        # ConfigManagerから現在のプリセットを取得
        config_data = self.config_manager.data
        idx = config_data["app_settings"].get("last_active_preset_index", 0)
        
        # 範囲チェックを行い安全にプリセットを取得
        if 0 <= idx < len(config_data["presets"]):
            current_preset = config_data["presets"][idx]
            favs = current_preset.get("favorites", [])
            
            for url in favs[:2]:  # 上位2つを表示
                # ドメインの頭文字を取得
                display_text = url.replace("https://", "").replace("www.", "")[0].upper()
                btn = QPushButton(display_text) 
                btn.setFixedSize(24, 24)
                btn.setToolTip(url)
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: white; 
                        border: 1px solid #ccc; 
                        border-radius: 3px; 
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background-color: #e0e0e0;
                    }
                """)
                # クロージャの問題を避けるため、デフォルト引数 u=url を使用
                btn.clicked.connect(lambda checked, u=url: self.browser.setUrl(QUrl(u)))
                self.fav_layout.addWidget(btn)
        
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
        
        # 初期状態の検索モード復元
        saved_mode = config_data["app_settings"].get("search_mode", "google")
        if saved_mode == "find":
            self.toggle_search_mode()
        else:
            self.search_bar.setPlaceholderText("Google Search...")

        layout.addWidget(self.search_container)
        layout.addWidget(self.browser)
        
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
        
        # 修正：ConfigManagerを使用して保存
        self.config_manager.data["app_settings"]["search_mode"] = self.search_mode
        self.config_manager.save_config()
        
        self.search_bar.setFocus()

    def _handle_search_enter(self):
        """Enterキーで検索実行 / URL判定"""
        text = self.search_bar.text().strip()
        if not text: return

        if self.search_mode == "google":
            # URLかどうかの判定
            is_url = text.startswith(('http://', 'https://')) or ('.' in text and ' ' not in text)
            
            if is_url:
                target_url = text if text.startswith(('http://', 'https://')) else f"https://{text}"
                
                # YouTube Live等の外部ブラウザ転送判定
                if any(x in target_url for x in ["youtube.com/live/", "youtu.be/live/"]):
                    import webbrowser
                    webbrowser.open(target_url)
                    self.search_container.hide()
                    return 
                
                self.browser.setUrl(QUrl(target_url))
            else:
                url = f"https://www.google.com/search?q={text}"
                self.browser.setUrl(QUrl(url))
            
            self.search_container.hide()
            self.browser.setFocus()
            
        else:
            # ページ内検索モード（Shift押下で逆方向）
            modifiers = QApplication.keyboardModifiers()
            is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
            self._find_with_count(backward=bool(is_shift))
        
    def _find_with_count(self, backward=False):
        text = self.search_bar.text()
        flags = QWebEnginePage.FindFlag(0)
        if backward:
            flags |= QWebEnginePage.FindFlag.FindBackward
        
        # 検索実行
        self.browser.findText(text, flags, self._update_hit_count)

    def _update_hit_count(self, result):
        """検索結果（件数情報）をラベルに反映"""
        num_matches = result.numberOfMatches() 
        active_index = result.activeMatch() 

        if num_matches > 0:
            # インデックスは0開始のため、表示は +1 する
            self.hit_label.setText(f"{active_index + 1}/{num_matches}")
        else:
            self.hit_label.setText("0/0")

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            
            # --- 1. Control キーとの組み合わせ ---
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                # 検索バーのトグル（Ctrl+F）
                if key == Qt.Key.Key_F:
                    self.toggle_search_container() # メソッド化して共通利用
                    return True
                # リロード（Ctrl+R）
                elif key == Qt.Key.Key_R:
                    self.browser.reload()
                    return True

            # --- 2. Alt キーとの組み合わせ ---
            if modifiers & Qt.KeyboardModifier.AltModifier:
                # ブラウザバック（Alt+←）
                if key == Qt.Key.Key_Left:
                    self.browser.back()
                    return True
                # ブラウザ進む（Alt+→）
                elif key == Qt.Key.Key_Right:
                    self.browser.forward()
                    return True

            # --- 3. 特定ウィジェットに対する個別処理 ---
            if obj == self.search_bar:
                # エンターキーで検索実行
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._handle_search_enter()
                    return True
                # Escapeで検索バーを閉じる
                elif key == Qt.Key.Key_Escape:
                    if self.search_container.isVisible():
                        self.toggle_search_container()
                        return True

        return super().eventFilter(obj, event)

    def toggle_search_container(self):
        """検索バーの表示・非表示を切り替え、適切にフォーカスを制御する"""
        if self.search_container.isVisible():
            self.search_container.hide()
            self.browser.setFocus()
        else:
            self.search_container.show()
            self.search_bar.setFocus()
            self.search_bar.selectAll()
    
    def contextMenuEvent(self, event):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: white; border: 1px solid #999; }
            QMenu::item { padding: 5px 25px; }
            QMenu::item:selected { background-color: #3a8fb7; color: white; }
        """)
        # 基本操作
        menu.addAction("戻る").triggered.connect(self.browser.back)
        menu.addAction("進む").triggered.connect(self.browser.forward)
        menu.addAction("リロード").triggered.connect(self.browser.reload)
        menu.addSeparator()
        # --- 【新機能】プリセット切り替えサブメニュー ---
        preset_menu = menu.addMenu("プリセット切替")
        config_data = self.config_manager.data
        presets = config_data.get("presets", [])
        current_idx = config_data["app_settings"].get("last_active_preset_index", 0)
        for i, preset in enumerate(presets):
            name = preset.get("name", f"Preset {i}")
            # 現在選択中のプリセットにはチェックマークをつける（視認性向上）
            prefix = "● " if i == current_idx else "   "
            action = preset_menu.addAction(f"{prefix}{name}")
            # ラムダの引数に現在の i を固定して渡す
            action.triggered.connect(lambda checked, idx=i: self.apply_preset(idx))
        menu.addSeparator()
        # 設定・保存系
        save_geo_action = menu.addAction("現在のサイズをプリセットに保存")
        save_geo_action.triggered.connect(self.add_current_geometry_to_preset)
        menu.addSeparator()
        close_action = menu.addAction("インジケーター化")
        close_action.triggered.connect(self.collapse_to_indicator)
        menu.exec(QCursor.pos())

    def add_current_geometry_to_preset(self):
        """現在の状態を『ロックされた新しいパターン』として追加する"""
        data = self.config_manager.data
        # 1. 安全にインデックスを取得
        try:
            idx = data["app_settings"].get("last_active_preset_index", 0)
            target_preset = data["presets"][idx]
        except (IndexError, KeyError):
            self._display_preset_notification("Error: Preset not found.")
            return
        # 2. 現在のウィンドウ情報を取得
        geo = self.geometry()
        new_loc = {
            "x": geo.x(),
            "y": geo.y(),
            "width": geo.width(),
            "height": geo.height(),
            "opacity": self.windowOpacity(),
            "is_locked": True  # 新しく追加するものは「ロック」状態
        }
        # 3. locations リストに追加 (append)
        if "locations" not in target_preset:
            target_preset["locations"] = []
        target_preset["locations"].append(new_loc)
        # 4. 保存と通知
        self.config_manager.save_config()
        self._display_preset_notification("★New Size Pattern Locked & Added!")
        
    def adjust_zoom(self):
        """現在のウィンドウ幅に基づき、プリセットごとの基準幅(base_width)に合わせてズームを調整"""
        # 初期化前や設定マネージャーがない場合はスキップ
        if not hasattr(self, 'config_manager') or self.config_manager is None:
            return
        new_width = self.width()
        # 前回の幅と同じなら処理しない（無駄な計算を回避）
        if hasattr(self, '_last_zoom_width') and self._last_zoom_width == new_width:
            return
        try:
            # 1. JSONから現在のプリセット情報を取得
            data = self.config_manager.data
            idx = data["app_settings"].get("last_active_preset_index", 0)
            preset = data["presets"][idx]
            # 2. 基準幅(base_width)を取得（未設定なら500pxをデフォルトに）
            base_width = preset.get("base_width", 500)
            # 3. ズーム倍率を計算
            zoom_level = new_width / base_width
            # 極端な数値にならないよう制限（0.4倍〜2.0倍）
            zoom_level = max(0.4, min(zoom_level, 2.0))
            # 4. ブラウザに適用
            self.browser.setZoomFactor(zoom_level)
            self._last_zoom_width = new_width
        except (IndexError, KeyError, ZeroDivisionError) as e:
            # 基準幅が0だった場合などのエラー回避
            print(f"Zoom adjustment failed: {e}")
        
    def cycle_geometry(self):
        """Alt + D: 現在のプリセット内で locations を巡回する"""
        # 描画のチラつきを抑える
        self.setUpdatesEnabled(False)
        try:
            # 1. 切り替える前に現在のURLなどを保存
            self.save_current_state()
            data = self.config_manager.data
            idx = data["app_settings"].get("last_active_preset_index", 0)
            locations = data["presets"][idx].get("locations", [])
            if not locations:
                self._display_preset_notification("No size patterns found.")
                return
            # 2. インデックスを次に進める（ループ処理）
            self.current_location_index = (self.current_location_index + 1) % len(locations)
            # 3. 新しい位置・サイズを適用
            self.apply_config_geometry()
            # 4. 通知を表示
            self._display_preset_notification(f"Size: {self.current_location_index + 1}/{len(locations)}")
        except Exception as e:
            print(f"Error in cycle_geometry: {e}")
        finally:
            self.setUpdatesEnabled(True)
            self.browser.update()
            preset_idx = self.config_manager.data["app_settings"]["last_active_preset_index"]
            # 現在のサイズインデックスを保存
            self.config_manager.data["presets"][preset_idx]["last_location_index"] = self.current_location_index
            self.config_manager.save_config() # JSONへ書き出し

    def apply_config_geometry(self):
        try:
            data = self.config_manager.data
            p_idx = data["app_settings"].get("last_active_preset_index", 0)
            preset = data["presets"][p_idx]
            
            loc = preset["locations"][self.current_location_index]
            self.setGeometry(loc["x"], loc["y"], loc["width"], loc["height"])
            self.setWindowOpacity(loc.get("opacity", 1.0))
            
            # --- ここがポイント：リロードせず、表示を最適化する ---
            if self.width() > 800:
                # 横長の場合：ズームを少し下げて「広大なデスクトップ」に見せかける
                # 1.0(標準)だと427px判定されるため、0.8～0.9程度に設定
                self.browser.setZoomFactor(0.8)
                self._force_desktop_layout()
            else:
                # 縦長（小窓）の場合：標準のズームに戻す
                self.browser.setZoomFactor(1.0)
                
        except (IndexError, KeyError) as e:
            print(f"Failed to apply geometry: {e}")
    
    def save_current_state(self):
        """現在の状態（URL、およびアンロック時のみ座標）を保存する"""
        if not hasattr(self, 'config_manager') or self.config_manager is None:
            return
        try:
            data = self.config_manager.data
            idx = data["app_settings"].get("last_active_preset_index", 0)
            preset = data["presets"][idx]
            # 1. URLの保存（これはロックに関係なく最新を保持）
            current_url = self.browser.url().toString()
            if current_url and current_url != "about:blank":
                preset["last_url"] = current_url
            # 2. 座標情報の保存（アンロック状態の時のみ）
            self._update_geometry_if_unlocked()
            # 3. ファイルへ書き出し
            self.config_manager.save_config()
        except Exception as e:
            print(f"Save failed: {e}")
            
    def _update_geometry_if_unlocked(self):
        """現在のスロットがロックされていなければ、メモリ上のデータを更新する"""
        if not hasattr(self, 'config_manager') or self.config_manager is None:
            return
        data = self.config_manager.data
        idx = data["app_settings"].get("last_active_preset_index", 0)
        loc_idx = getattr(self, 'current_location_index', 0)
        try:
            target_location = data["presets"][idx]["locations"][loc_idx]
            # ロックされている場合は座標を更新しない
            if target_location.get("is_locked", False):
                return
            # アンロック状態なら、現在のウィンドウ座標をメモリに反映
            geo = self.geometry()
            target_location.update({
                "x": geo.x(),
                "y": geo.y(),
                "width": geo.width(),
                "height": geo.height(),
                "opacity": self.windowOpacity()
            })
            # ※ save_config は呼び出し元の save_current_state 等で行うためここでは不要
        except (IndexError, KeyError):
            pass
        
    def moveEvent(self, event):
        super().moveEvent(event)
        # 移動中、メモリ上の座標データのみ更新（ロックされていなければ）
        self._update_geometry_if_unlocked()

    def resizeEvent(self, event):
        """ウィンドウサイズが変わったときに呼ばれるイベント（統合版）"""
        super().resizeEvent(event)
        # 1. ロックされていなければ、現在のサイズをメモリに反映
        self._update_geometry_if_unlocked()
        # 2. ウィンドウ幅に合わせてコンテンツのズームを調整
        self.adjust_zoom()
        
    def closeEvent(self, event):
        # 終了時にURLと最新座標をまとめてファイル保存
        self.save_current_state()
        super().closeEvent(event)
        
    def show_floating_notification(self, text):
        # 1. 全体設定で通知がオフなら何もしない
        if not self.config_manager.data["app_settings"].get("show_notifications", True):
            return
        # 2. 現在のプリセットから通知色を取得
        preset = self.config_manager.data["presets"][self._current_preset_idx]
        n_color = preset.get("indicator_styles", {}).get("notification_color", "#00FF7F")
        # 3. 通知を表示
        self.notif = FloatingNotification(text, color=n_color)
        
    def _handle_audio_status(self, audible):
        """音が止まっても消さず、アイコン状態のみ更新"""
        if not self.isVisible() and self.collapsed_indicator and self.collapsed_indicator.isVisible():
            self._show_indicator()

    def _on_title_changed(self, title):
        """タイトル変更時、インジケーター表示中なら更新"""
        if not hasattr(self, 'collapsed_indicator') or self.collapsed_indicator is None:
            return
        if self.collapsed_indicator.isVisible():
            self._show_indicator()

    def _show_indicator(self):
        """JSで再生状態を取得し、更新メソッドへ渡す"""
        js_code = """
        (function() {
            var videos = Array.from(document.querySelectorAll('video'));
            var activeVideo = videos.find(v => v.offsetWidth > 0 && v.offsetHeight > 0);
            if (!activeVideo) return 'none';
            return activeVideo.paused ? 'paused' : 'playing';
        })();
        """
        self.browser.page().runJavaScript(js_code, self._update_indicator_with_state)

    # --- エラー修正用：フラグリセットメソッド ---
    def _reset_transition_flag(self):
        """トグル処理中のロックを解除する"""
        self._is_switching_mode = False

    # --- インジケーター更新の司令塔 ---
    def _update_indicator_with_state(self, state):
        """【司令塔】データの準備を行い、各担当メソッドを順に実行する"""
        # 1. データの準備
        state = state if state in ['playing', 'paused'] else 'stopped'
        data = self.config_manager.data
        try:
            idx = data["app_settings"].get("last_active_preset_index", 0)
            preset = data["presets"][idx]
        except (IndexError, KeyError):
            preset = {}
        
        styles = preset.get("indicator_styles", {})
        scale = data["app_settings"].get("global_indicator_scale", 1.0)
        shape = styles.get("shape", "rounded_rect")

        # 2. インジケーターの生成（未作成時のみ）
        self._ensure_indicator_exists(scale)

        # 3. 描画更新を一時停止
        self.collapsed_indicator.setUpdatesEnabled(False)

        # 4. 役割ごとにメソッドを呼び出す
        self._apply_indicator_style(styles, scale)        # 見た目担当
        self._set_indicator_content(state, styles)       # 中身担当
        self._finalize_indicator_geometry(shape, scale)  # 配置担当

        # 5. 描画再開と表示
        self.collapsed_indicator.setUpdatesEnabled(True)
        if not self.collapsed_indicator.isVisible():
            self.collapsed_indicator.show()
        self.collapsed_indicator.raise_()

    # --- 担当1：見た目（スタイル） ---
    def _apply_indicator_style(self, styles, scale):
        text_color = styles.get("text_color", "#00FF00")
        bg_color = styles.get("bg_color", "#2C3E50")
        shape = styles.get("shape", "rounded_rect")
        alpha = styles.get("indicator_bg_alpha", 220)
        font_size = int(10 * scale)

        # カスタムペイント（背景）の更新
        self.collapsed_indicator.set_custom_style(text_color, bg_color, shape, alpha)
        
        # ラベルの文字装飾
        label_style = f"color: {text_color}; font-weight: bold; font-size: {font_size}pt; background: transparent; border: none;"
        self.icon_label.setStyleSheet(label_style)
        self.text_label.setStyleSheet(label_style)

    # --- 担当2：中身（コンテンツ） ---
    def _set_indicator_content(self, state, styles):
        # アイコンの決定
        icon = {"playing": "♪", "paused": "||"}.get(state, "❏")
        self.icon_label.setText(icon)

        # タイトルの加工
        raw_title = self.browser.title()
        clean_title = re.sub(r'^\(\d+\)\s*', '', raw_title)
        if not clean_title or clean_title == "about:blank": clean_title = "Doppel"
        
        # プリセットごとの最大長さを取得
        max_len = styles.get("max_title_length", 25)
        
        if len(clean_title) > max_len:
            display_title = clean_title[:max_len] + "..."
        else:
            display_title = clean_title
        self.text_label.setText(display_title)

    # --- 担当3：配置（ジオメトリ） ---
    def _finalize_indicator_geometry(self, shape, scale):
        if shape == "circle":
            self.text_label.hide()
            size = int(50 * scale)
            target_size = QSize(size, size)
            self.icon_label.setFixedWidth(size)
        else:
            self.text_label.show()
            # リサイズのために制約を一旦リセット
            self.collapsed_indicator.setFixedSize(QSize(-1, -1))
            self.collapsed_indicator.setMinimumSize(int(80 * scale), 0)
            self.icon_label.setFixedWidth(int(30 * scale))
            
            # 余白設定
            m = (int(12*scale), int(5*scale), int(15*scale), int(5*scale))
            self.collapsed_indicator.layout().setContentsMargins(*m)
            
            # 【重要】内容に基づいてサイズを計算
            self.collapsed_indicator.layout().activate()
            target_size = self.collapsed_indicator.layout().sizeHint()

        # 右下座標の計算
        screen = QApplication.primaryScreen().availableGeometry()
        new_x = screen.right() - target_size.width() - 15
        new_y = screen.bottom() - target_size.height() - 15

        # 位置とサイズを同時に確定（震え防止）
        self.collapsed_indicator.setGeometry(new_x, new_y, target_size.width(), target_size.height())
        self.collapsed_indicator.setFixedSize(target_size)

    # --- インジケーターの生成補助 ---
    def _ensure_indicator_exists(self, scale):
        if not self.collapsed_indicator:
            self.collapsed_indicator = ClickableLabel("", None)
            self.collapsed_indicator.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
            )
            self.collapsed_indicator.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            layout = QHBoxLayout(self.collapsed_indicator)
            layout.setSpacing(int(8 * scale))
            self.icon_label = QLabel() 
            self.text_label = QLabel()
            self.text_label.setWordWrap(False)
            self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.icon_label)
            layout.addWidget(self.text_label)
            self.collapsed_indicator.clicked.connect(self._handle_indicator_click)

    def _handle_indicator_click(self, area):
        """クリック時の再生制御または復元"""
        def restore_window():
            self.show_and_activate()

        if getattr(self, 'is_circle_mode', False) or area == "title":
            restore_window()
            return
        
        if area == "icon":
            js_toggle = """
            (function() {
                var videos = Array.from(document.querySelectorAll('video'));
                var target = videos.sort((a, b) => b.offsetHeight - a.offsetHeight)[0];
                if (target) {
                    if (target.paused) target.play(); else target.pause();
                    return true;
                }
                return false;
            })();
            """
            self.browser.page().runJavaScript(js_toggle)
            # クリック直後の状態を反映
            QTimer.singleShot(200, self._show_indicator)
            
    def show_and_activate(self):
        """小窓の復元とインジケーターの破棄"""
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.show()
        self.raise_()
        self.activateWindow()
        if hasattr(self, 'collapsed_indicator') and self.collapsed_indicator:
            self.collapsed_indicator.hide()

    def toggle_indicator_mode(self):
        """手動トグル（Alt+S等）"""
        if getattr(self, '_is_switching_mode', False): return
        self._is_switching_mode = True
        
        if self.collapsed_indicator and self.collapsed_indicator.isVisible():
            self.show_and_activate()
        else:
            self.hide()
            self._show_indicator()
            
        QTimer.singleShot(500, lambda: setattr(self, '_is_switching_mode', False))

    def force_indicator_mode(self):
        """Alt+W用：確実にインジケーター化"""
        if getattr(self, '_is_switching_mode', False): return
        if not self.isVisible() and self.collapsed_indicator and self.collapsed_indicator.isVisible():
            return

        self._is_switching_mode = True
        self.hide()
        self._show_indicator()
        QTimer.singleShot(500, lambda: setattr(self, '_is_switching_mode', False))

    def collapse_to_indicator(self):
        """小窓閉鎖時の自動格納"""
        self.hide()
        self._show_indicator()
        
    def _apply_site_customizations(self):
        """CSS注入と監視のみを実行（ボタン操作なし）"""
        url = self.browser.url().toString()
        data = self.selector_manager.get_data_for_url(url)
        if not data:
            return

        css = data.get("injected_css", "")
        hide_selectors = data.get("hide_elements", [])

        js_code = f"""
        (function() {{
            // 1. CSS注入（一度だけ実行）
            const styleId = 'resident-shield';
            let style = document.getElementById(styleId);
            if (!style) {{
                style = document.createElement('style');
                style.id = styleId;
                (document.head || document.documentElement).appendChild(style);
            }}
            style.textContent = `{css}`;

            // 2. 要素隠蔽の関数
            const hideElements = () => {{
                const selectors = {json.dumps(hide_selectors)};
                selectors.forEach(s => {{
                    document.querySelectorAll(s).forEach(el => {{
                        if (el.style.display !== 'none') el.style.display = 'none';
                    }});
                }});
            }};

            // 初回実行
            hideElements();

            // 3. 監視開始（広告やサイドバーが復活したら消す）
            if (window.residentObserver) window.residentObserver.disconnect();
            window.residentObserver = new MutationObserver(hideElements);
            window.residentObserver.observe(document.body, {{ childList: true, subtree: true }});
        }})();
        """
        self.browser.page().runJavaScript(js_code)
        
    def apply_site_settings(self, current_url):
        # SelectorManager に丸投げ！
        site_data = self.selector_manager.get_data_for_url(current_url)

        if site_data:
            css = site_data.get("css", "")
            js = site_data.get("js", "")
            # ここでCSSとJSを適用する処理へ...
        else:
            print("No specific settings for this domain.")

    def _on_load_finished(self, ok):
        """ページ読み込み完了時に実行"""
        if ok:
            # 1. サイト固有のカスタマイズ（広告ブロック等）を適用
            self._apply_site_customizations()
            
            # 2. 横長の場合、再度デスクトップ化を強制
            if self.width() > 800:
                self._force_desktop_layout()
                
            # 3. 1秒後にもう一度ダメ押し（動的な要素の読み込み待ち）
            QTimer.singleShot(1000, self._apply_site_customizations)
            if self.width() > 800:
                QTimer.singleShot(1500, self._force_desktop_layout)

    def show_notification(self, duration):
        """フェードイン開始"""
        self.show()
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.start()

        # 指定時間後にフェードアウトを開始するタイマー
        QTimer.singleShot(duration, self._hide_notification)

    def _hide_notification(self):
        """フェードアウトして削除"""
        self.fade_animation.setDirection(QPropertyAnimation.Direction.Backward)
        self.fade_animation.finished.connect(self.deleteLater) # 終わったら自分を消去
        self.fade_animation.start()

# ==========================================
# 3. 各種シグナル・ホットキー処理
# ==========================================
def get_portal_url():
    """アクティブなブラウザからURLを抽出する (自分自身やフォルダは除外)"""
    try:
        from pywinauto import Desktop
        target_windows = [
            w for w in Desktop(backend="uia").windows(visible_only=True) 
            if ("Chrome" in w.window_text() or "Edge" in w.window_text() or "Firefox" in w.window_text() or "Brave" in w.window_text())
            and "Doppel" not in w.window_text() 
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

last_action_time = 0
def check_hotkeys():
    global last_action_time
    now = time.time()
    if now - last_action_time < 0.3:
        return
    # 1. 設定からショートカットキー設定を読み込む（存在しなければデフォルト値を設定）
    # main_window (ResidentMiniPlayer) を経由して config_manager にアクセス
    shortcuts = current_window.config_manager.data["app_settings"].get("shortcuts", {})
    modifier = shortcuts.get("modifier", "alt")
    # モディファイアキー（Altなど）が押されていなければ終了
    if not keyboard.is_pressed(modifier):
        return
    is_shift = keyboard.is_pressed('shift')
    # 2. 機能キーとのマッピング表
    # キー名 : 発火させるシグナル
    mapping = {
        shortcuts.get("hide_completely", "w"): bridge.hide_completely_requested,
        shortcuts.get("show_toggle", "s"):      bridge.show_requested,
        shortcuts.get("copy", "c"):             bridge.copy_requested,
        shortcuts.get("paste", "v"):            bridge.paste_requested,
        shortcuts.get("cycle_size", "d"):       bridge.cycle_geometry_requested # 名称変更を反映
    }
    # 3. マッピングに基づいた判定
    for key, signal in mapping.items():
        if keyboard.is_pressed(key):
            # Shiftが必要なキーとそうでないキーの干渉を防ぐガード
            if key in ['s', 'd'] and is_shift:
                continue
            signal.emit()
            last_action_time = now
            return # 1つのキーが判定されたら終了
    # 4. 数字キー 1~9 の動的スキャン（プリセット切り替え）
    for i in range(1, 10):
        if keyboard.is_pressed(str(i)):
            # Bridgeに新設した preset_switch_requested を使用 (0始まりにするため i-1)
            bridge.preset_switch_requested.emit(i - 1)
            last_action_time = now
            return

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # 1. ConfigManagerのインスタンス化
    config_manager = ConfigManager("config.json") 
    # 2. 【ここを修正】 self.data にアクセスする
    config_dict = config_manager.data 
    # 3. 階層を辿って URL を取得
    # config.json の app_settings -> selectors_url を参照
    app_settings = config_dict.get("app_settings", {})
    remote_url = app_settings.get("selectors_url")
    # 4. SelectorManager の初期化
    selector_manager = SelectorManager(
        local_path="selectors.json", 
        remote_url=remote_url
    )
    # --- 2. Windowを作成 ---
    # インスタンス化したマネージャーたちを渡す
    main_window = ResidentMiniPlayer(config_manager, selector_manager)
    global current_window
    current_window = main_window
    # --- 3. シグナルの配線 ---
    bridge.copy_requested.connect(main_window.capture_current_url)
    bridge.paste_requested.connect(main_window.apply_url_from_dispatch)
    bridge.cycle_geometry_requested.connect(main_window.cycle_geometry)
    bridge.preset_switch_requested.connect(main_window.apply_preset)
    bridge.show_requested.connect(main_window.handle_show_request)
    bridge.hide_completely_requested.connect(
        lambda: main_window.update_display_mode(DisplayMode.HIDDEN)
    )
    # --- 4. システム・監視系 ---
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    monitor_timer = QTimer()
    monitor_timer.setParent(main_window) 
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(50)
    print("Watching Alt+C/V/D/S... (Press Ctrl+C to stop)")
    main_window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()