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
    QLineEdit, QPushButton, QMainWindow, QLabel
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
            "layout_threshold": 600,
            "desktop_zoom_default": 0.8,
            "mobile_zoom_default": 1.0,
            "search_mode": "google",
            "selectors_url": None,
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
                "last_url": "https://youtube.com",
                "favorites": [
                    "https://www.youtube.com/",
                    "https://gemini.google.com/app"
                ],
                "base_width": 400,
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

class IndicatorWidget(QLabel):
    clicked = pyqtSignal(str)

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # 内部状態の初期値（config読み込みまでの安全なデフォルト）
        self._text_color = "#00FF00"
        self._bg_color = "#2C3E50"
        self._bg_alpha = 220  # 0 (透明) - 255 (不透明)
        self._shape = "rounded_rect"

    def apply_indicator_styles(self, styles: dict):
        # デフォルト値の管理をこちらに集約
        self._text_color = styles.get("text_color", "#00FF00")
        self._bg_color = styles.get("bg_color", "#2C3E50")
        self._shape = styles.get("shape", "rounded_rect")
        self._bg_alpha = styles.get("indicator_bg_alpha", 220)
        self.update()

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
    MAX_FAVORITES = 5
    # --- 設定値へのアクセスをプロパティ化 ---
    @property
    def app_settings(self):
        """アプリ全体の共通設定を取得"""
        return self.config_manager.data.get("app_settings", {})
    
    @property
    def reserved_shortcut_keys(self):
        """キャッシュされたキーセットを返す（_is_reserved_shortcut は削除してOK）"""
        if hasattr(self, "_shortcut_cache"):
            return self._shortcut_cache

        shortcuts = self.app_settings.get("shortcuts", {})
        self._shortcut_cache = set()
        for action_name, key_char in shortcuts.items():
            if action_name == "modifier": continue
            target_key = getattr(Qt.Key, f"Key_{key_char.upper()}", None)
            if target_key:
                self._shortcut_cache.add(target_key)
        return self._shortcut_cache
    
    @property
    def presets(self):
        """全プリセットのリストを返すプロパティ"""
        return self.config_manager.data.get("presets", [])
    
    @property
    def _current_preset_idx(self):
        """現在アクティブなプリセットのインデックスを取得"""
        return self.app_settings.get("last_active_preset_index", 0)

    @_current_preset_idx.setter
    def _current_preset_idx(self, value):
        """インデックスを更新し、app_settingsを同期する"""
        self.app_settings["last_active_preset_index"] = value

    @property
    def current_preset(self):
        """現在アクティブなプリセットデータを直接返す"""
        try:
            data = self.config_manager.data
            idx = self.app_settings.get("last_active_preset_index", 0)
            return data["presets"][idx]
        except (IndexError, KeyError):
            return {}
        
    @property
    def current_location_index(self):
        """現在のアクティブなプリセットにおける、ロケーション（サイズ）のインデックスを返す"""
        return self.current_preset.get("last_location_index", 0)

    @current_location_index.setter
    def current_location_index(self, value):
        """インデックスを更新し、同時にプリセットデータ側も同期する"""
        self.current_preset["last_location_index"] = value
        
    @property
    def notification_color(self):
        """現在のプリセットに設定された通知色を返す。未設定ならデフォルトの黄緑色を返す。"""
        styles = self.current_preset.get("indicator_styles", {})
        return styles.get("notification_color", "#00FF7F")
        
    @property
    def layout_threshold(self):
        """デスクトップ/モバイルを判定する閾値"""
        return self.app_settings.get("layout_threshold", 600)

    @property
    def desktop_zoom_default(self):
        """デスクトップモード時の固定ズーム率"""
        return self.app_settings.get("desktop_zoom_default", 0.8)
    
    @property
    def mobile_zoom_default(self):
        """モバイルモード時の固定ズーム率"""
        return self.app_settings.get("mobile_zoom_default", 1.0)
    
    @property
    def indicator_icons(self):
        """再生状態に応じたアイコンの辞書を返す"""
        # 将来的には設定ファイルから {"playing": "▶", ...} のように上書き可能にする
        default_icons = {"playing": "♪", "paused": "||", "stopped": "❏"}
        return self.app_settings.get("indicator_icons", default_icons)

    @property
    def indicator_screen_margin(self):
        """画面端からのマージン（ピクセル）"""
        return self.app_settings.get("indicator_screen_margin", 15)
    
    def __init__(self, config_manager, selector_manager):
        super().__init__()
        self.config_manager = config_manager
        self.selector_manager = selector_manager
        self.current_mode = DisplayMode.EXPANDED
        self._is_switching_mode = False
        self.collapsed_indicator = None
        self.all_selectors = {}
        self._last_processed_title = ""
        self._last_search_query = ""
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
        self.browser.loadFinished.connect(self.apply_site_optimizations)
        self.browser.urlChanged.connect(lambda: self.apply_site_optimizations())
        self.browser.loadFinished.connect(self.adjust_zoom)
        self.browser.loadFinished.connect(self._on_load_finished)
        # --- 5. URLのロード ---
        def start_initial_load():
            # 1. 広告のチラ見えを防ぐ（先に真っ白にしておく）
            self.browser.setHtml("<html><body style='background:white;'></body></html>")
            # 2. 強制的にフォーカスを「ブラウザ以外」に一度移す
            # これにより eventFilter が最初から有効になるように促す
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            # 3. UA・ズーム判定（既存ロジック）
            if self.width() > self.layout_threshold:
                self._set_desktop_cookie_directly()
                self.browser.setZoomFactor(self.desktop_zoom_default)
            else:
                self.browser.setZoomFactor(self.mobile_zoom_default)
            # 4. URLロード
            last_url = self.current_preset.get("last_url")
            self.browser.setUrl(self._get_clean_url(last_url))
        QTimer.singleShot(150, start_initial_load) # 余裕を持って150ms
        # セレクターのロード
        self.load_selectors()
        self.reload_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.reload_shortcut.activated.connect(self.reload_and_apply)
        self.current_mode = DisplayMode.EXPANDED # 明示的に初期化
        self._is_switching_mode = False
        
    def handle_show_request(self):
        """
        Alt+S：OSの状態（isHidden）を信じず、
        『インジケーターが出ているか、あるいは窓が変数上で展開中以外か』で判定する
        """
        if not self.has_valid_content(): return

        # インジケーターが表示されているか、あるいは現在のモードが EXPANDED 以外なら「展開」
        indicator_visible = self.collapsed_indicator and self.collapsed_indicator.isVisible()
        
        if indicator_visible or self.current_mode != DisplayMode.EXPANDED:
            self.update_display_mode(DisplayMode.EXPANDED)
        else:
            # それ以外（窓が展開されているはず）なら「格納」
            self.update_display_mode(DisplayMode.COLLAPSED)
        
    def update_display_mode(self, target_mode: DisplayMode):
        if getattr(self, '_is_switching_mode', False): return
        self._is_switching_mode = True

        try:
            if target_mode in [DisplayMode.COLLAPSED, DisplayMode.HIDDEN]:
                # --- [解決] 1回目の空振りを防ぐための「フォーカス・リリース」 ---
                # 自分のフォーカスを外してから隠すことで、OSの拒絶を回避する
                self.clearFocus()
                if self.browser:
                    self.browser.clearFocus()
                
                # 最前面フラグを剥がす
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
                
                # [重要] show()を呼んでフラグ変更を確定させてから即hide()
                self.show() 
                self.hide()
                
                self.current_mode = target_mode
                
                if target_mode == DisplayMode.COLLAPSED:
                    # 前回の修正通り、少し遅延させてインジケーターを出す
                    QTimer.singleShot(50, self._show_indicator)
                else:
                    self._display_preset_notification("★Stealth Mode Activated")

            elif target_mode == DisplayMode.EXPANDED:
                if self.collapsed_indicator:
                    self.collapsed_indicator.hide()
                
                # 展開時はフラグを戻して表示
                self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                self.show()
                self.activateWindow()
                self.raise_()
                self.current_mode = DisplayMode.EXPANDED

            QApplication.processEvents()

        finally:
            # フラグのリセット（間隔を少し短くしてレスポンス向上）
            QTimer.singleShot(150, self._reset_transition_flag)
            
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
        
    def _get_clean_url(self, raw_url):
        """
        URL文字列を加工し、必要なパラメータ（字幕オフ等）を付加してQUrlを返す。
        """
        if not raw_url:
            return QUrl("https://www.google.com")
        url_str = str(raw_url)
        # YouTubeの字幕をデフォルトで非表示にするパラメータ
        # cc_load_policy=0
        target_param = "cc_load_policy=0"
        if target_param not in url_str:
            sep = "&" if "?" in url_str else "?"
            url_str = f"{url_str}{sep}{target_param}"
            
        return QUrl(url_str)
        
    def capture_current_url(self):
        """Alt + C相当：現在のURLをJSONに保存"""
        url = get_portal_url()
        if not url: return
        # --- 1. プロパティを使って「現在のプリセット」に直接アクセス ---
        # self.current_preset は内部で config_manager.data["presets"][idx] を参照している
        preset = self.current_preset
        if preset: # プリセットが存在すれば
            # --- 2. 辞書の内容を書き換える ---
            # 辞書(dict)は参照渡しなので、presetを書き換えれば大元のdataも書き換わります
            preset["last_url"] = url
            # --- 3. 保存を実行 ---
            self.config_manager.save_config()
            self._display_preset_notification("★Target URL Saved!")

    def apply_url_from_dispatch(self):
        """Alt + V相当：クリップボードまたは履歴からURLを展開"""
        # これだけで「現在アクティブなプリセットの辞書」が手に入ります！
        # idx を取得する工程すら不要になります。
        preset = self.current_preset
        # 座標適用
        self.apply_config_geometry()
        clipboard = QApplication.clipboard()
        text = clipboard.text().strip()
        if text.startswith("http"):
            target_url = text
            clipboard.clear()
            self._display_preset_notification("★URL Loaded from Clipboard")
        else:
            # preset はすでに「今のプリセット」を指しているのでそのまま使えます
            target_url = preset.get("last_url", "https://www.google.com")
            self._display_preset_notification("★URL Restored from History")
        self.browser.setUrl(QUrl(target_url))
        self.update_display_mode(DisplayMode.EXPANDED)
        
    def _display_preset_notification(self, text):
        """現在のプリセット設定に基づいた通知を表示する"""
        # 1. 表示設定のチェック
        # self.app_settings プロパティを使用
        if not self.app_settings.get("show_notifications", True):
            return
        # 2. スタイルデータの抽出
        # self.current_preset プロパティを使用
        styles = self.current_preset.get("indicator_styles", {})
        # 3. 色の決定
        text_color = styles.get("notification_color") or styles.get("text_color") or "#00FF7F"
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
        """指定されたインデックスのプリセットに切り替え、UIとブラウザを更新する"""
        # 1. ガード
        if not (0 <= index < len(self.presets)):
            return

        # 2. 状態の保存（現在のURLや座標を今のプリセットに書き込む）
        self.save_current_state()

        # 3. インデックスの更新
        # プロパティの Setter を使うことで、内部の app_settings も自動更新される
        self._current_preset_idx = index 

        # 4. 同期とロード
        # この時点で self.current_preset は「新しいプリセット」を指している
        self.refresh_favorites_ui()
        
        # プリセット切り替え時、そのプリセットが持っていた「最後の場所」を復元する
        # self.current_location_index プロパティが内部で current_preset を参照していれば、
        # apply_config_geometry は自動的に正しい位置を再現します。
        self.apply_config_geometry()
        
        self._update_indicator_with_state("stopped")
        
        # URLのロード
        last_url = self.current_preset.get("last_url", "https://www.google.com")
        self.browser.setUrl(self._get_clean_url(last_url))

        # 5. 永続化と通知
        self.config_manager.save_config()
        self._display_preset_notification(f"Switch -> {self.current_preset.get('name', 'Untitled')}")
        
    def refresh_favorites_ui(self):
        """現在のプリセットに基づいてお気に入りボタンを再生成する"""
        # 1. 既存のボタンを削除（ここはそのまま）
        while self.fav_layout.count():
            item = self.fav_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # 2. プロパティを使用して「今のお気に入り」を直接取得
        favorites = self.current_preset.get("favorites", [])

        # 3. リスト内の全URLに対してボタンを生成（最大5件）
        for url in favorites[:self.MAX_FAVORITES]:
            # 修正ポイント：ロジックが複雑に見える場合は、一時変数で意味を明確にする
            domain_part = url.replace("https://", "").replace("http://", "").replace("www.", "")
            display_text = domain_part[0].upper() if domain_part else "?"

            btn = QPushButton(display_text)
            btn.setFixedSize(24, 24)
            btn.setToolTip(url)
            btn.setStyleSheet(self._get_fav_btn_style())

            # クロージャ対策：u=url は非常に重要なテクニックです！
            btn.clicked.connect(lambda checked, u=url: self.browser.setUrl(QUrl(u)))
            self.fav_layout.addWidget(btn)

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
        self.browser.hide()
        
    def apply_site_optimizations(self):
        """
        SelectorManagerを使用して、サイト固有のCSS注入・要素隠蔽・レイアウト調整を
        一括で実行する。
        """
        url = self.browser.url().toString()
        if not url or url == "about:blank":
            return
        
        # 1. データの取得（SelectorManagerに一任）
        data = self.selector_manager.get_data_for_url(url)
        if not data:
            return
        css = data.get("injected_css", "")
        hide_selectors = data.get("hide_elements", [])
        # 2. JSコードの構築
        # f-string内での波括弧は二重 {{ }} でエスケープ
        js_code = f"""
        (function() {{
            // A. CSSの注入
            const styleId = 'resident-optimized-style';
            let style = document.getElementById(styleId);
            if (!style) {{
                style = document.createElement('style');
                style.id = styleId;
                (document.head || document.documentElement).appendChild(style);
            }}
            style.textContent = `{css}`;

            // B. 要素隠蔽（動的監視）
            const hideElements = () => {{
                const selectors = {json.dumps(hide_selectors)};
                selectors.forEach(s => {{
                    document.querySelectorAll(s).forEach(el => {{
                        if (el.style.display !== 'none') {{
                            el.style.display = 'none';
                        }}
                    }});
                }});
            }};

            // 初回実行と監視の開始
            hideElements();
            if (window.residentObserver) window.residentObserver.disconnect();
            window.residentObserver = new MutationObserver(hideElements);
            window.residentObserver.observe(document.body, {{ childList: true, subtree: true }});
        }})();
        """
        # 3. 実行
        self.browser.page().runJavaScript(js_code)
        # 4. 特定ドメインの追加調整（デスクトップレイアウト強制など）
        if "youtube.com" in url:
            self._force_desktop_layout()
        QTimer.singleShot(150, self.browser.show)
            
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
            self.apply_site_optimizations()
            # ユーザーに知らせる（ステータスバーがある場合）
            self.statusBar().showMessage("Settings reloaded and applied!", 3000)

    def _install_proxy_filter(self):
        """読み込み完了時に呼ばれる。イベントフィルタの設置とデスクトップ表示の強制を行う"""
        if self.browser.focusProxy():
            self.browser.focusProxy().installEventFilter(self)
        # ページ読み込み完了時にデスクトップ化を実行
        self._force_desktop_layout()

    def _force_desktop_layout(self):
        if not hasattr(self, 'page') or self.page is None:
            return

        script = """
        (function() {
            // 1. YouTubeのモバイル専用UI要素を隠蔽
            const mWeb = document.getElementsByTagName('ytm-app')[0];
            if (mWeb) { mWeb.style.display = 'none'; }

            // 2. デスクトップ版設定を維持するためのCookie
            try {
                document.cookie = "PREF=f6=40000; domain=.youtube.com; path=/";
            } catch (e) {
                console.warn("Cookie injection failed:", e);
            }
            
            // 3. YouTubeの内部フラグ（モバイル版挙動）を無効化
            if (window.yt && window.yt.config_) {
                if (window.yt.config_.EXPERIMENT_FLAGS) {
                    window.yt.config_.EXPERIMENT_FLAGS.kevlar_is_mweb_modern_f_and_e_interaction = false;
                }
            }
            
            // 4. Viewportの強制上書き（PC版の解像度に見せかける）
            let meta = document.querySelector('meta[name="viewport"]');
            if (!meta) {
                meta = document.createElement('meta');
                meta.name = "viewport";
                (document.head || document.documentElement).appendChild(meta);
            }
            meta.setAttribute('content', 'width=1280, initial-scale=1.0');

            // 5. 字幕（CC）の自動オフ
            const disableSubtitles = () => {
                const ccButton = document.querySelector('.ytp-subtitles-button');
                if (ccButton && ccButton.getAttribute('aria-pressed') === 'true') {
                    ccButton.click();
                }
            };
            disableSubtitles();
            setTimeout(disableSubtitles, 1500); // 描画遅延を考慮

            // 6. レイアウト再計算のトリガー
            window.dispatchEvent(new Event('resize'));
        })();
        """
        self.browser.page().runJavaScript(script)

    def _setup_ui(self):
        # --- 1. 状態の初期化 ---
        self.search_mode = "google"
        config_data = self.config_manager.data
        saved_mode = config_data["app_settings"].get("search_mode", "google")
        
        # --- 2. UIの構築 ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 検索バーコンテナ
        self.search_container = QWidget()
        self.search_container.setStyleSheet("background: #f0f0f0; border-bottom: 1px solid #ccc;")
        self.search_container.setMaximumHeight(40)
        s_layout = QHBoxLayout(self.search_container)
        s_layout.setContentsMargins(5, 2, 5, 2)
        s_layout.setSpacing(5)

        # モード切替ボタン
        self.mode_toggle = QPushButton("[G]")
        self.mode_toggle.setFixedSize(30, 24)
        self.mode_toggle.clicked.connect(self.toggle_search_mode)
        s_layout.addWidget(self.mode_toggle)

        # 検索バー本体
        self.search_bar = QLineEdit()
        self.search_bar.installEventFilter(self)
        self.search_bar.returnPressed.connect(self._handle_search_enter)
        s_layout.addWidget(self.search_bar)

        # お気に入りグループ
        self.fav_group = QWidget()
        self.fav_layout = QHBoxLayout(self.fav_group)
        self.fav_layout.setContentsMargins(0, 0, 0, 0)
        self.fav_layout.setSpacing(5)
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
        self.search_container.hide() # 初期状態は隠す

        # 【超重要】ブラウザをレイアウトに追加
        # これがないと画面がグレーのままになります
        layout.addWidget(self.browser)

        # --- 3. 状態の反映 ---
        if saved_mode == "find":
            self.search_mode = "google" 
            self.toggle_search_mode()
        else:
            self.refresh_favorites_ui()
            # [G] モードのスタイルを適用
            self.mode_toggle.setText("[G]")
            self.mode_toggle.setStyleSheet("background-color: #4285f4; color: white; font-weight: bold; border: none; border-radius: 3px;")
        
    def toggle_search_mode(self):
        """Google検索モード[G]とページ内検索モード[F]を切り替える"""
        if self.search_mode == "google":
            # ページ内検索モード [F] へ
            self.search_mode = "find"
            self.mode_toggle.setText("[F]")
            self.mode_toggle.setStyleSheet("background-color: #ff9800; color: white; font-weight: bold; border: none; border-radius: 3px;")
            self.search_bar.setPlaceholderText("Find in page...")
            self.fav_group.hide()
            self.find_group.show()
        else:
            # Google検索モード [G] へ
            self.search_mode = "google"
            self.mode_toggle.setText("[G]")
            self.mode_toggle.setStyleSheet("background-color: #4285f4; color: white; font-weight: bold; border: none; border-radius: 3px;")
            self.search_bar.setPlaceholderText("Google Search...")
            self.fav_group.show()
            self.find_group.hide()
            self.refresh_favorites_ui()

        # 設定を保存
        self.config_manager.data["app_settings"]["search_mode"] = self.search_mode
        self.config_manager.save_config()
        self.search_bar.clear()
        self.search_bar.setFocus()
        
    def _handle_search_enter(self):
        """
        1. 入力値のバリデーション
        2. 検索モードによる分岐
        A. Googleモード: URL判定 or 検索クエリ発行
        B. ページ内検索モード: 検索実行
        """
        query = self.search_bar.text().strip()
        if not query:
            return
        if self.search_mode == "google":
            self._process_web_navigation(query)
        else:
            # 1. 検索ワードが「前回と違う」場合
            if query != self._last_search_query:
                self._last_search_query = query
                self.browser.findText("") 
                self._find_with_count(backward=False)
            # 2. 検索ワードが「前回と同じ」場合（「次へ」の挙動）
            else:
                self._process_in_page_search()

    def _process_web_navigation(self, text):
        """URLか検索語かを判定し、適切なアクション（遷移/外部起動）を実行"""
        # URL判定ロジックをここに集約
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

    def _process_in_page_search(self):
        """現在のキー入力状態（Shift）を確認し、ページ内検索を実行"""
        modifiers = QApplication.keyboardModifiers()
        is_backward = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        self._find_with_count(backward=is_backward)

    def _update_hit_count(self, result):
        """
        UIパーツの存在確認（防御的プログラミング）を行い、表示を更新。
        採用担当者の視点：パーツの有無をチェックすることで、UI変更時のクラッシュを防ぐ。
        """
        # 存在確認のロジック
        if not hasattr(self, 'hit_label') or self.hit_label is None:
            print("Warning: hit_label is not initialized.")
            return
        num_matches = result.numberOfMatches()
        active_index = result.activeMatch()
        # 表示ロジック
        text = f"{active_index}/{num_matches}" if num_matches > 0 else "0/0"
        self.hit_label.setText(text)
        
    def _find_with_count(self, backward=False):
        text = self.search_bar.text()
        flags = QWebEnginePage.FindFlag(0)
        if backward:
            flags |= QWebEnginePage.FindFlag.FindBackward
        # 検索実行
        self.browser.findText(text, flags, self._update_hit_count)

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        key = event.key()
        modifiers = event.modifiers()
        is_alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)

        # [解決] Alt+Wが効かない原因：ブロックするだけで何もしていなかった
        # ここで直接メソッドを呼ぶことで、フォーカスがあっても即座に反応させる
        if is_alt:
            shortcuts = self.app_settings.get("shortcuts", {})
            key_char = chr(key).lower() if 32 <= key <= 126 else ""

            if key_char == shortcuts.get("show_toggle", "s"):
                self.handle_show_request()
                return True
            if key_char == shortcuts.get("hide_completely", "w"):
                self.update_display_mode(DisplayMode.HIDDEN)
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
        """右クリックメニューを表示する"""
        # QMenu生成とスタイル適用
        menu = self._create_base_menu()
        
        # 1. ブラウザ操作
        menu.addAction("戻る").triggered.connect(self.browser.back)
        menu.addAction("進む").triggered.connect(self.browser.forward)
        menu.addAction("リロード").triggered.connect(self.browser.reload)
        menu.addSeparator()

        # 2. プリセット切り替え（サブメニュー）
        self._add_preset_switch_menu(menu)
        menu.addSeparator()

        # 3. 設定・保存系
        save_geo_action = menu.addAction("現在のサイズをプリセットに保存")
        save_geo_action.triggered.connect(self.add_current_geometry_to_preset)
        
        # --- ここに将来、新しいメニュー（例：設定画面を開くなど）を追加しやすくなる ---
        
        menu.addSeparator()
        menu.addAction("インジケーター化").triggered.connect(self.collapse_to_indicator)
        
        menu.exec(QCursor.pos())

    def _add_preset_switch_menu(self, parent_menu):
        """プリセット切り替え用サブメニューを構築"""
        preset_menu = parent_menu.addMenu("プリセット切替")
        
        # プロパティを使用してデータを取得
        presets = self.config_manager.data.get("presets", [])
        current_idx = self.app_settings.get("last_active_preset_index", 0)

        for i, preset in enumerate(presets):
            name = preset.get("name", f"Preset {i}")
            action = preset_menu.addAction(name)
            
            # Qt標準のチェックマーク機能を使用
            action.setCheckable(True)
            if i == current_idx:
                action.setChecked(True)
                
            action.triggered.connect(lambda _, idx=i: self.apply_preset(idx))

    def _create_base_menu(self):
        """スタイルの適用されたQMenuを生成する"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: white; border: 1px solid #999; }
            QMenu::item { padding: 5px 25px; }
            QMenu::item:selected { background-color: #3a8fb7; color: white; }
        """)
        return menu

    def add_current_geometry_to_preset(self):
        """現在の状態を『ロックされた新しいパターン』として追加する"""
        geo = self.geometry()
        new_loc = {
            "x": geo.x(), "y": geo.y(),
            "width": geo.width(), "height": geo.height(),
            "opacity": self.windowOpacity(),
            "is_locked": True
        }

        # 安全策：dict.setdefault を使うと「なければ作る、あればそれを使う」を1行で書ける
        self.current_preset.setdefault("locations", []).append(new_loc)

        self.config_manager.save_config()
        self._display_preset_notification("★New Size Pattern Locked & Added!")
        
    def adjust_zoom(self, force_desktop=False):
        """現在のウィンドウ幅に基づきズームを調整"""
        if not hasattr(self, 'config_manager') or self.config_manager is None:
            return
        # デスクトップ版：設定値から固定ズームを適用
        if force_desktop:
            self.browser.setZoomFactor(self.desktop_zoom_default)
            return
        # モバイル版：base_widthに基づき動的に計算
        new_width = self.width()
        if hasattr(self, '_last_zoom_width') and self._last_zoom_width == new_width:
            return
        try:
            # プロパティ current_preset を使ってスッキリ記述
            base_width = self.current_preset.get("base_width", 400)
            zoom_level = new_width / base_width
            zoom_level = max(0.4, min(zoom_level, 2.0))
            self.browser.setZoomFactor(zoom_level)
            self._last_zoom_width = new_width
        except ZeroDivisionError:
            pass
        
    def cycle_geometry(self):
        """Alt + D: 現在のプリセット内で locations を巡回する"""
        self.setUpdatesEnabled(False)
        try:
            # 1. 状態の保存とデータの取得
            self.save_current_state()
            locations = self.current_preset.get("locations", [])
            if not locations:
                self._display_preset_notification("No size patterns found.")
                return
            # 2. インデックスの更新（剰余演算によるループ）
            self.current_location_index = (self.current_location_index + 1) % len(locations)
            # 3. 適用と通知
            self.apply_config_geometry()
            self._display_preset_notification(f"Size: {self.current_location_index + 1}/{len(locations)}")
        except Exception as e:
            print(f"Error in cycle_geometry: {e}")
        finally:
            self.setUpdatesEnabled(True)
            self.browser.update()
            # 代入するだけで内部的に self.current_preset も更新されている
            self.config_manager.save_config()

    def apply_config_geometry(self):
        """現在選択されているインデックスに基づいて、ウィンドウのサイズと位置を適用する"""
        try:
            locations = self.current_preset.get("locations", [])
            if not locations:
                return
            loc = locations[self.current_location_index]
            self.setGeometry(loc["x"], loc["y"], loc["width"], loc["height"])
            self.setWindowOpacity(loc.get("opacity", 1.0))
            # --- 表示の最適化：マジックナンバーをプロパティへ置き換え ---
            if self.width() > self.layout_threshold:
                # プロパティを使用（デフォルト0.8）
                self.browser.setZoomFactor(self.desktop_zoom_default)
                self._force_desktop_layout()
            else:
                # プロパティを使用（デフォルト1.0）
                self.browser.setZoomFactor(self.mobile_zoom_default)
        except (IndexError, KeyError) as e:
            print(f"Failed to apply geometry: {e}")
    
    def save_current_state(self):
        """現在の状態（URL、およびアンロック時のみ座標）を保存する"""
        # 1. ガード：依存オブジェクトの存在確認
        if not getattr(self, 'config_manager', None):
            return
        try:
            # 2. URLの保存（有効なURLのみ）
            current_url = self.browser.url().toString()
            if current_url and current_url not in ("about:blank", ""):
                # プロパティ経由で現在のプリセットを直接更新
                self.current_preset["last_url"] = current_url
            # 3. 座標情報の保存（アンロック状態の時のみ）
            # ※このメソッド内でも self.current_preset プロパティを活用するように修正されている前提
            self._update_geometry_if_unlocked()
            # 4. データの永続化
            self.config_manager.save_config()
        except Exception as e:
            # 実際の運用では print だけでなくログ出力が望ましい
            print(f"Failed to save current state: {e}")
            
    def _update_geometry_if_unlocked(self):
        """現在のスロットがロックされていなければ、メモリ上のデータを更新する"""
        if not getattr(self, 'config_manager', None):
            return
        try:
            # 修正ポイント：current_preset の中の 'locations' リストを参照する
            locations = self.current_preset.get("locations", [])
            if not locations:
                return
            target_location = locations[self.current_location_index]
            # ロック状態の判定（設計通り：Trueなら保存しない）
            if target_location.get("is_locked", False):
                return
            # アンロック状態なら、現在のウィンドウ情報を辞書に反映
            geo = self.geometry()
            target_location.update({
                "x": geo.x(),
                "y": geo.y(),
                "width": geo.width(),
                "height": geo.height(),
                "opacity": self.windowOpacity()
            })
        except (IndexError, KeyError) as e:
            print(f"Failed to update geometry memory: {e}")
        
    def moveEvent(self, event):
        super().moveEvent(event)
        # 移動中、メモリ上の座標データのみ更新（ロックされていなければ）
        self._update_geometry_if_unlocked()

    def resizeEvent(self, event):
        """ウィンドウサイズ変更時の統合ハンドラ"""
        super().resizeEvent(event)
        # 1. 座標の更新
        self._update_geometry_if_unlocked()
        # 2. 表示モードの判定（プロパティを使用）
        is_desktop = self.width() > self.layout_threshold
        target_mode = "desktop" if is_desktop else "mobile"
        # 3. Viewport/YouTubeフラグの適用
        self.set_view_mode(target_mode)
        # 4. ズームの最終調整
        self.adjust_zoom(force_desktop=is_desktop)
        
    def closeEvent(self, event):
        """ウィンドウが閉じられるとき（Ctrl+C含む）のクリーンアップ"""
        # keyboardのフックを外す（これが終了を遅らせる主犯です）
        try:
            import keyboard
            keyboard.unhook_all()
        except:
            pass
        super().closeEvent(event)
        
    def set_view_mode(self, mode="desktop"):
        """レイアウトの土台をJSで整える（ズーム操作はadjust_zoomに任せる）"""
        # YouTube等の個別調整
        if mode == "desktop":
            self._force_desktop_layout()
        # Viewportの書き換え（Gemini等にPC版だと思い込ませる）
        viewport_content = "width=1280, initial-scale=1.0" if mode == "desktop" else "width=device-width, initial-scale=1.0"
        script = f"""
        (function() {{
            var meta = document.querySelector('meta[name="viewport"]');
            if (!meta) {{
                meta = document.createElement('meta');
                meta.name = "viewport";
                document.getElementsByTagName('head')[0].appendChild(meta);
            }}
            meta.setAttribute('content', '{viewport_content}');
            window.dispatchEvent(new Event('resize'));
        }})();
        """
        self.page.runJavaScript(script)
        
    def show_floating_notification(self, text):
        # 1. 全体設定を確認
        if not self.app_settings.get("show_notifications", True):
            return
        # 2. 通知を表示（プロパティから色を取得）
        self.notif = FloatingNotification(text, color=self.notification_color)
        
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

    def _reset_transition_flag(self):
        self._is_switching_mode = False

    # --- インジケーター更新の司令塔 ---
    def _update_indicator_with_state(self, state):
        """【最新版司令塔】状態を正規化し、自律的な各担当メソッドへ処理を委譲する"""
        # 1. 状態の正規化（playing / paused / stopped）
        state = state if state in ('playing', 'paused') else 'stopped'
        # 2. インジケーターの存在確認とスケール反映（ここは初期化として維持）
        scale = self.app_settings.get("global_indicator_scale", 1.0)
        self._ensure_indicator_exists(scale)
        # 3. 最適化：タイトルと状態が前回と同じなら、重い更新をスキップ
        current_raw_title = self.browser.title()
        is_visible = self.collapsed_indicator.isVisible()
        last_title = getattr(self, '_last_processed_title', None)
        last_state = getattr(self, '_last_processed_state', None)
        if is_visible and last_title == current_raw_title and last_state == state:
            return
        # --- フル更新の開始 ---
        self._last_processed_title = current_raw_title
        self._last_processed_state = state
        # 描画のチラつきを抑える
        self.collapsed_indicator.setUpdatesEnabled(False)
        try:
            # 【ここを修正】引数を渡さず、メソッド自身の自律性に任せる
            self._apply_indicator_style()       # 担当1：見た目
            self._set_indicator_content(state)  # 担当2：中身（状態だけは渡す）
            self._finalize_indicator_geometry() # 担当3：配置
        finally:
            self.collapsed_indicator.setUpdatesEnabled(True)
        # 4. 表示状態の管理
        if not is_visible:
            self.collapsed_indicator.show()
        self.collapsed_indicator.raise_()

    # --- 担当1：見た目（スタイル） ---
    def _apply_indicator_style(self):
        """
        プロパティから最新のスタイルとスケールを取得し、ラベルに反映する
        引数をなくすことで、外部からの「お膳立て」を不要にする
        """
        # 1. 必要なデータは自分（プロパティ）で取得する
        styles = self.current_preset.get("indicator_styles", {})
        scale = self.app_settings.get("global_indicator_scale", 1.0)
        
        # 2. 描画クラスへ丸ごと投げる
        self.collapsed_indicator.apply_indicator_styles(styles)
        
        # 3. フォント周りの計算と反映
        text_color = styles.get("text_color", "#00FF00")
        font_size = int(10 * scale)
        
        label_style = (
            f"color: {text_color}; "
            f"font-weight: bold; "
            f"font-size: {font_size}pt; "
            f"background: transparent; "
            f"border: none;"
        )
        
        self.icon_label.setStyleSheet(label_style)
        self.text_label.setStyleSheet(label_style)

    # --- 担当2：中身（コンテンツ） ---
    def _set_indicator_content(self, state):
        """
        再生状態に応じたアイコンと、加工したタイトルをラベルに設定する。
        スタイル（最大文字数など）はプロパティから取得する。
        """
        # プロパティからアイコン辞書を取得して適用
        icon = self.indicator_icons.get(state, self.indicator_icons["stopped"])
        self.icon_label.setText(icon)

        # 2. タイトルの加工（正規表現で通知バッジなどを除去）
        raw_title = self.browser.title()
        clean_title = re.sub(r'^\(\d+\)\s*', '', raw_title)
        if not clean_title or clean_title == "about:blank": 
            clean_title = "Doppel"
        
        # 3. プリセットごとの最大長さをプロパティから取得
        styles = self.current_preset.get("indicator_styles", {})
        max_len = styles.get("max_title_length", 25)
        
        # 4. 文字数制限の適用
        if len(clean_title) > max_len:
            display_title = clean_title[:max_len] + "..."
        else:
            display_title = clean_title
            
        self.text_label.setText(display_title)

    # --- 担当3：配置（ジオメトリ） ---
    def _finalize_indicator_geometry(self):
        """
        プロパティから形状とスケールを取得し、インジケーターの最終的なサイズと位置を確定させる。
        """
        # 1. 必要な情報をプロパティから取得
        styles = self.current_preset.get("indicator_styles", {})
        shape = styles.get("shape", "rounded_rect")
        scale = self.app_settings.get("global_indicator_scale", 1.0)

        # 2. 形状に応じたサイズ計算
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
            
            # 余白設定（スケーリング対応）
            m = (int(12*scale), int(5*scale), int(15*scale), int(5*scale))
            self.collapsed_indicator.layout().setContentsMargins(*m)
            
            # 内容に基づいて最適なサイズを再計算
            self.collapsed_indicator.layout().activate()
            target_size = self.collapsed_indicator.layout().sizeHint()

        # 3. 画面端（右下）の座標計算
        screen = QApplication.primaryScreen().availableGeometry()
        margin = self.indicator_screen_margin # プロパティを使用
        new_x = screen.right() - target_size.width() - margin
        new_y = screen.bottom() - target_size.height() - margin

        # 4. 位置とサイズを同時に確定（setGeometryを使うことで一回の描画更新で済ませる）
        self.collapsed_indicator.setGeometry(new_x, new_y, target_size.width(), target_size.height())
        self.collapsed_indicator.setFixedSize(target_size)

    # --- インジケーターの生成補助 ---
    def _ensure_indicator_exists(self, scale):
        if not self.collapsed_indicator:
            self.collapsed_indicator = IndicatorWidget("", None)
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

    def collapse_to_indicator(self):
        """小窓閉鎖時の自動格納"""
        self.hide()
        self._show_indicator()

    def _on_load_finished(self, ok):
        """ページ読み込み完了時に実行"""
        if ok:
            # 1. サイト固有のカスタマイズ（広告ブロック等）を適用
            self.apply_site_optimizations()
            
            # 2. 横長の場合、再度デスクトップ化を強制
            if self.width() > self.layout_threshold:
                self._force_desktop_layout()
                
            # 3. 1秒後にもう一度ダメ押し（動的な要素の読み込み待ち）
            QTimer.singleShot(1000, self.apply_site_optimizations)
            if self.width() > self.layout_threshold:
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
    if now - last_action_time < 0.25: # チャタリング防止
        return

    if current_window is None:
        return

    shortcuts = current_window.config_manager.data["app_settings"].get("shortcuts", {})
    modifier = shortcuts.get("modifier", "alt")
    
    if not keyboard.is_pressed(modifier):
        return

    is_shift = keyboard.is_pressed('shift')

    mapping = {
        shortcuts.get("hide_completely", "w"): bridge.hide_completely_requested,
        shortcuts.get("show_toggle", "s"):      bridge.show_requested,
        shortcuts.get("copy", "c"):             bridge.copy_requested,
        shortcuts.get("paste", "v"):            bridge.paste_requested,
        shortcuts.get("cycle_size", "d"):       bridge.cycle_geometry_requested
    }

    # 1. 機能キー
    for key, signal in mapping.items():
        if keyboard.is_pressed(key):
            if key in ['s', 'd'] and is_shift: continue
            signal.emit()
            last_action_time = now
            return

    # 2. 数字キー（current_window を経由）
    if current_window.app_settings.get("enable_number_shortcuts", True):
        for i in range(1, 10):
            if keyboard.is_pressed(str(i)):
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
    # 1. current_windowを先に確定させる（check_hotkeysエラー防止）
    global current_window
    current_window = main_window

    # 2. シグナル接続を一本化
    bridge.show_requested.connect(main_window.handle_show_request)
    # [解決] メソッドを直接呼ぶようにしてHIDDENを処理させる
    bridge.hide_completely_requested.connect(
        lambda: main_window.update_display_mode(DisplayMode.HIDDEN)
    )
    
    # 他の接続...
    bridge.copy_requested.connect(main_window.capture_current_url)
    bridge.paste_requested.connect(main_window.apply_url_from_dispatch)
    bridge.cycle_geometry_requested.connect(main_window.cycle_geometry)
    bridge.preset_switch_requested.connect(main_window.apply_preset)

    # 3. タイマー開始
    monitor_timer = QTimer(main_window)
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(100)

    # 4. 最後に表示
    main_window.show()
    # 起動時のモードを明示的にセットし、1回目のAlt+Sが「隠す」から始まるようにする
    main_window.current_mode = DisplayMode.EXPANDED 
    QApplication.processEvents() 
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()