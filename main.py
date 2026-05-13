import os
import re
import sys
import json
import time
import uuid
import shutil
import keyboard
import requests
from datetime import datetime
from urllib.parse import urlparse
from PyQt6 import sip
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QMainWindow, QLabel, QMessageBox
)
from PyQt6.QtCore import (
    Qt, QUrl, QEvent, QTimer, QObject, pyqtSignal, QPropertyAnimation, QEasingCurve, QByteArray, QRect, QSize
)
from PyQt6.QtGui import QCursor, QPainter, QBrush, QColor, QPen, QShortcut, QKeySequence
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings, QWebEngineScript

from constants import (
    # 1. システム・パス設定
    CONFIG_FILE, PROFILE_DIR, SELECTORS_FILE,

    # 2. アプリケーション基本情報・列挙型
    DEFAULT_APP_NAME, DisplayMode,

    # 3. 表示・レイアウト・数値デフォルト
    DF_LAYOUT_THRESHOLD, DF_ZOOM_DESKTOP, DF_ZOOM_MOBILE,
    DF_MAX_TITLE_LEN, DF_INDICATOR_MARGIN, DF_BASE_WIDTH,
    INDICATOR_FONT_BASE_SIZE, INDICATOR_CIRCLE_BASE_SIZE,
    INDICATOR_ICON_BASE_WIDTH, INDICATOR_MIN_WIDTH_BASE,
    INDICATOR_MARGINS_BASE, INDICATOR_SPACING_BASE,

    # 4. カラー定義
    COLOR_INDICATOR, COLOR_SUB_BUTTON_DEFAULT, COLOR_SUB_BUTTON_FAVORITED,

    # 5. 通知・インジケーター・UI詳細設定
    NOTIF_OPACITY_START, NOTIF_OPACITY_END,
    DEFAULT_NOTIF_SETTINGS, DEFAULT_INDICATOR_SETTINGS,
    DEFAULT_INDICATOR_ICONS, INDICATOR_WINDOW_FLAGS,

    # 6. 検索・ナビゲーション・ブラウザ設定
    SEARCH_ENGINE_URL, HTTPS_PREFIX,
    URL_SCHEMES, URL_BLANK, INVALID_URLS,
    VIEWPORT_DESKTOP, VIEWPORT_MOBILE,
    SEARCH_BAR_HEIGHT, SEARCH_TOGGLE_SIZE,
    SEARCH_SUB_SPACING, FIND_DEFAULT_COUNT,
    ICON_FAVORITE, ICON_FIND_IN_PAGE,
    EXTERNAL_BROWSER_KEYWORDS, TARGET_BROWSER_KEYWORDS,
    EXCLUDE_WINDOW_KEYWORDS, EXPLORER_CLASS_NAME,

    # 7. ショートカット・操作設定
    DEFAULT_MODIFIER, KEY_HIDE, KEY_SHOW, KEY_COPY, KEY_PASTE, KEY_CYCLE,
    SEARCH_MODES, ASCII_PRINTABLE_MIN, ASCII_PRINTABLE_MAX,
    WHEEL_MAX_SPEED_LIMIT, WHEEL_SWIPE_ACCUM_TARGET, SWIPE_THRESHOLD_X,
    HOTKEY_DEBOUNCE_SEC, HOTKEY_MONITOR_INTERVAL_MS,
    MAX_NUM_SHORTCUTS, MAX_FAVORITES, ZOOM_MIN_LIMIT, ZOOM_MAX_LIMIT,

    # 8. タイミング・遅延・しきい値
    INITIAL_LOAD_DELAY_MS, INDICATOR_CLICK_REFRESH_DELAY,
    DELAY_LOAD_FINISHED_DEFAULT, DELAY_SITE_OPTIMIZE_RETRY,
    DELAY_DESKTOP_LAYOUT_RETRY, OPTIMIZE_PROGRESS_THRESHOLD,

    # 9. スタイルシート・JavaScript テンプレート
    STYLE_SEARCH_CONTAINER, STYLE_MODE_BUTTON, STYLE_QMENU, STYLE_INDICATOR_LABEL, STYLE_SUB_BUTTON_BASE,
    JS_SET_VIEWPORT, JS_GET_VIDEO_STATE, JS_TOGGLE_PLAYBACK,

    # 10. メッセージ・テキスト・正規表現
    MENU_TEXT_BACK, MENU_TEXT_FORWARD, MENU_TEXT_RELOAD,
    MENU_TEXT_PRESET_SWITCH, MENU_TEXT_SAVE_GEO, MENU_TEXT_COLLAPSE,
    MSG_NO_LOCATIONS, MSG_SIZE_CYCLE, RE_TITLE_NOTIF,

    # 11. 巨大なデフォルト設定オブジェクト
    DEFAULT_SELECTOR_DATA, DEFAULT_CONFIG
)

class Bridge(QObject):
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()
    show_requested = pyqtSignal()
    hide_completely_requested = pyqtSignal()
    cycle_geometry_requested = pyqtSignal() 
    preset_switch_requested = pyqtSignal(int)

bridge = Bridge()
current_window = None
_notif = None


class ConfigManager:
    def __init__(self, config_path=CONFIG_FILE):
        self.config_path = config_path
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
            return self._deep_merge(DEFAULT_CONFIG.copy(), loaded_data)

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
        self.data = DEFAULT_CONFIG.copy()
        self.save_config()
        return self.data

    def _deep_merge(self, base, update):
        """
        再帰的に辞書をマージし、リストは 'name' キーで同一性を判定。
        型不一致時は可能な限り自動キャストを試みる。
        """
        for key, value in update.items():
            if key not in base:
                # 新機能などでbaseにないキーがupdate（古いファイル）にある場合はそのまま追加
                base[key] = value
                continue

            # --- 1. 辞書同士のマージ ---
            if isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)

            # --- 2. リスト同士のマージ（nameキーによる識別） ---
            elif isinstance(base[key], list) and isinstance(value, list):
                if key == "presets":
                    # 名前をキーにしたマージ用辞書を作成
                    base_presets = {p.get("name"): p for p in base[key] if isinstance(p, dict)}
                    
                    for up_item in value:
                        if not isinstance(up_item, dict): continue
                        name = up_item.get("name")
                        
                        if name in base_presets:
                            # 同じ名前があれば、その中身をさらに再帰マージ
                            self._deep_merge(base_presets[name], up_item)
                        else:
                            # 新しい名前のプリセットならリストに追加
                            base[key].append(up_item)
                else:
                    # presets以外のリストは、型が一致していれば上書き採用
                    base[key] = value

            # --- 3. 値の代入と自動型変換 ---
            else:
                base[key] = self._attempt_cast(value, type(base[key]))

        return base

    def _attempt_cast(self, value, target_type):
        """文字列の数字を数値に変換するなど、可能な限り型を合わせる"""
        if isinstance(value, target_type):
            return value
        
        try:
            # ターゲットが bool の場合は文字列 "true"/"false" 等を考慮
            if target_type is bool and isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            
            # ターゲットが数値（int, float）の場合のキャスト
            if target_type in (int, float):
                return target_type(value)
        except (ValueError, TypeError):
            pass
            
        # 変換不能な場合は安全のためデフォルト（base側の値）を維持（この関数を呼ぶ側で制御）
        return value

    def _backup_corrupted_config(self):
        if os.path.exists(self.config_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.config_path}_{timestamp}.bak"
            shutil.copy(self.config_path, backup_path)
            print(f"[Config] Backup saved to: {backup_path}")
        
class SelectorManager:
    def __init__(self, local_path=SELECTORS_FILE, remote_url=None):
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
        """URLからドメインを抽出し、正確にマッチングさせる。未登録なら空の構造を返す。"""
        if not self.selectors or not url or url == "about:blank":
            return DEFAULT_SELECTOR_DATA.copy()
        try:
            parsed_url = urlparse(url)
            hostname = parsed_url.netloc
        except:
            return DEFAULT_SELECTOR_DATA.copy()
        # ドメインの部分一致判定
        for domain_key, data in self.selectors.items():
            if domain_key in hostname:
                # 見つかった場合も、辞書の欠落を防ぐためデフォルトとマージして返す
                return {**DEFAULT_SELECTOR_DATA.copy(), **data}
        return DEFAULT_SELECTOR_DATA.copy()
    
class FloatingNotification(QWidget):
    def __init__(self, text, 
                 color=DEFAULT_NOTIF_SETTINGS["color"], 
                 bg_color=DEFAULT_NOTIF_SETTINGS["bg_color"], 
                 bg_alpha=DEFAULT_NOTIF_SETTINGS["bg_alpha"], 
                 duration=DEFAULT_NOTIF_SETTINGS["duration"]):
        super().__init__(None)
        
        self.text = text
        self.accent_color = color
        self.bg_color = bg_color
        self.bg_alpha = bg_alpha
        
        self._init_window_attributes()
        self._setup_notification_ui()
        self._setup_animation()
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

    def _setup_notification_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.label = QLabel(self.text)
        c = QColor(self.bg_color)
        rgba_bg = f"rgba({c.red()}, {c.green()}, {c.blue()}, {self.bg_alpha})"
        
        # スタイルシート内の数値を定数参照に置き換え
        self.label.setStyleSheet(f"""
            background-color: {rgba_bg}; 
            color: {self.accent_color}; 
            border: 1px solid {self.accent_color};
            border-radius: {DEFAULT_NOTIF_SETTINGS['border_radius']};
            padding: {DEFAULT_NOTIF_SETTINGS['padding']};
            font-weight: bold;
            font-size: {DEFAULT_NOTIF_SETTINGS['font_size']};
            font-family: {DEFAULT_NOTIF_SETTINGS['font_family']};
        """)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        self.adjustSize()
        screen_geo = QApplication.primaryScreen().availableGeometry()
        x = (screen_geo.width() - self.width()) // 2
        y = DEFAULT_NOTIF_SETTINGS["pos_y"] # 定数を使用
        self.move(x, y)

    def _setup_animation(self):
        self.setWindowOpacity(0.0)
        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(DEFAULT_NOTIF_SETTINGS["fade_duration"]) # 定数を使用
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
        
        # 定数から初期状態を適用
        self._text_color = DEFAULT_INDICATOR_SETTINGS["text_color"]
        self._bg_color = DEFAULT_INDICATOR_SETTINGS["bg_color"]
        self._bg_alpha = DEFAULT_INDICATOR_SETTINGS["bg_alpha"]
        self._shape = DEFAULT_INDICATOR_SETTINGS["shape"]

    def apply_indicator_styles(self, styles: dict):
        # styles辞書に値がない場合のフォールバック先も定数にする
        self._text_color = styles.get("text_color", DEFAULT_INDICATOR_SETTINGS["text_color"])
        self._bg_color = styles.get("bg_color", DEFAULT_INDICATOR_SETTINGS["bg_color"])
        self._shape = styles.get("shape", DEFAULT_INDICATOR_SETTINGS["shape"])
        self._bg_alpha = styles.get("indicator_bg_alpha", DEFAULT_INDICATOR_SETTINGS["bg_alpha"])
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 線の太さを定数化
        pen_width = DEFAULT_INDICATOR_SETTINGS["pen_width"]
        rect = self.rect().adjusted(pen_width, pen_width, -pen_width, -pen_width)

        bg = QColor(self._bg_color)
        bg.setAlpha(self._bg_alpha)
        border = QColor(self._text_color)

        painter.setBrush(QBrush(bg))
        pen = QPen(border)
        pen.setWidth(pen_width)
        painter.setPen(pen)

        # 形状描画
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
        else:
            # 角丸の半径も定数化
            r = DEFAULT_INDICATOR_SETTINGS["corner_radius"]
            painter.drawRoundedRect(rect, r, r)
        
        painter.end()
        super().paintEvent(event)
        
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._shape == "circle":
            self.clicked.emit("icon")
            return

        layout = self.layout()
        if layout and layout.count() > 0:
            margin_left = layout.contentsMargins().left()
            icon_item = layout.itemAt(0).widget()
            
            # アイコン幅のデフォルト値も定数化
            icon_w = icon_item.width() if icon_item else DEFAULT_INDICATOR_SETTINGS["icon_boundary_width"]
            boundary = margin_left + icon_w
            
            if event.pos().x() < boundary:
                self.clicked.emit("icon")
            else:
                self.clicked.emit("title")
        else:
            self.clicked.emit("title")
            
class ResidentMiniPlayer(QMainWindow):
    # クラス定数も外部から取得
    MAX_FAVORITES = MAX_FAVORITES
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
    def layout_threshold(self):
        return self.app_settings.get("layout_threshold", DF_LAYOUT_THRESHOLD)

    @property
    def desktop_zoom_default(self):
        return self.app_settings.get("desktop_zoom_default", DF_ZOOM_DESKTOP)
    
    @property
    def mobile_zoom_default(self):
        return self.app_settings.get("mobile_zoom_default", DF_ZOOM_MOBILE)

    @property
    def indicator_icons(self):
        return self.app_settings.get("indicator_icons", DEFAULT_INDICATOR_ICONS)

    @property
    def indicator_screen_margin(self):
        """画面端からのマージン（設定ファイル > 定数 の順で参照）"""
        # constants.py の DF_INDICATOR_MARGIN を使用するように修正
        return self.app_settings.get("indicator_screen_margin", DF_INDICATOR_MARGIN)

    @property
    def notification_color(self):
        # indicator_stylesの中も、無ければ基本の通知設定から取得
        styles = self.current_preset.get("indicator_styles", {})
        return styles.get("notification_color", DEFAULT_NOTIF_SETTINGS["color"])
    
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
        self._setup_main_ui()
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
        QTimer.singleShot(INITIAL_LOAD_DELAY_MS, start_initial_load)
        # セレクターのロード
        self.load_selectors()
        self.reload_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
        self.reload_shortcut.activated.connect(self.reload_and_apply)
        self.current_mode = DisplayMode.EXPANDED # 明示的に初期化
        self._is_switching_mode = False
        self._mouse_press_pos = None
        self._is_optimized_for_current_url = False
        self.swipe_acc_x = 0
        self.last_swipe_time = 0
        self.last_wheel_time = 0
        self._is_left_button_pressed = False
        
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
                self.clearFocus()
                if self.browser: self.browser.clearFocus()
                
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
                
                # --- 修正: HIDDEN の時は show() を呼ばず、即 hide() する ---
                if target_mode == DisplayMode.COLLAPSED:
                    self.show() # フラグ確定のため
                    self.hide()
                    QTimer.singleShot(50, self._show_indicator)
                else:
                    # 完全非表示時は一瞬の表示もさせない
                    self.hide()
                    if self.collapsed_indicator:
                        self.collapsed_indicator.hide()
                    self._display_preset_notification("★Stealth Mode Activated")
                
                self.current_mode = target_mode

            elif target_mode == DisplayMode.EXPANDED:
                if self.collapsed_indicator:
                    self.collapsed_indicator.hide()
                
                self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                self.show()
                self.activateWindow()
                self.raise_()
                self.current_mode = DisplayMode.EXPANDED

            QApplication.processEvents()

        finally:
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
        # 1. 既存のボタンを削除
        while self.fav_layout.count():
            item = self.fav_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        favorites = self.current_preset.get("favorites", [])

        # 2. ボタンの生成
        for url in favorites[:5]: # MAX_FAVORITES
            domain_part = url.replace("https://", "").replace("http://", "").replace("www.", "")
            display_text = domain_part[0].upper() if domain_part else "?"

            btn = QPushButton(display_text)
            btn.setFixedSize(24, 24)
            btn.setToolTip(url)
            
            # 修正：メソッドから返ってきたスタイルをこのボタンに適用
            style_str = self._update_favorite_button_style(is_favorited=False)
            btn.setStyleSheet(style_str)

            btn.clicked.connect(lambda checked, u=url: self.browser.setUrl(QUrl(u)))
            self.fav_layout.addWidget(btn)

    def _update_favorite_button_style(self, is_favorited=False):
        """
        スタイル文字列を生成して返すだけのメソッドにする。
        (特定のボタンを直接操作しない)
        """
        color = COLOR_SUB_BUTTON_FAVORITED if is_favorited else COLOR_SUB_BUTTON_DEFAULT
        
        style = STYLE_SUB_BUTTON_BASE.format(
            color=color,
            extra_style="font-weight: bold;" if is_favorited else ""
        )
        
        return style # 文字列を返すだけにする
        
    def _setup_sub_groups(self, parent_layout):
        """お気に入りグループとページ内検索グループを個別に構築"""
        
        # --- 1. お気に入りグループ (fav_group) ---
        self.fav_group = QWidget()
        self.fav_layout = QHBoxLayout(self.fav_group)
        self.fav_layout.setContentsMargins(0, 0, 0, 0)
        self.fav_layout.setSpacing(SEARCH_SUB_SPACING)
        # ボタン自体は refresh_favorites_ui で動的に生成されるため、ここでは何もしない
        
        # --- 2. ページ内検索グループ (find_group) ---
        self.find_group = QWidget()
        self.find_layout = QHBoxLayout(self.find_group)
        self.find_layout.setContentsMargins(0, 0, 0, 0)
        self.find_layout.setSpacing(SEARCH_SUB_SPACING)
        
        # ヒット数ラベルの追加（以前のコードより）
        self.hit_label = QLabel("0/0")
        self.hit_label.setFixedWidth(35)
        self.hit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hit_label.setStyleSheet("color: #666; font-size: 10px; font-weight: bold;")
        self.find_layout.addWidget(self.hit_label)

        # 上下ボタンの生成
        self.btn_prev = QPushButton("▲") # または ICON_UP
        self.btn_next = QPushButton("▼") # または ICON_DOWN
        
        style = self._update_favorite_button_style(False) # 汎用スタイルを取得
        
        for b in [self.btn_prev, self.btn_next]:
            b.setFixedSize(24, 24)
            b.setStyleSheet(style)
            self.find_layout.addWidget(b)
        
        # シグナル接続
        self.btn_prev.clicked.connect(lambda: self._find_with_count(backward=True))
        self.btn_next.clicked.connect(lambda: self._find_with_count(backward=False))

        # --- 3. 親レイアウトに追加 ---
        parent_layout.addWidget(self.fav_group)
        parent_layout.addWidget(self.find_group)
        
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
        
        # --- ページ遷移時のチラつき（白い閃光）をゼロにするメソッド(使うときまでコメントアウトでオフにしておく) ---
        # self.setup_flicker_free_script()
        
        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.browser.installEventFilter(self)

        # --- 最適化タイミングの多層化 ---
        self.page.loadFinished.connect(self._on_load_finished)
        self.browser.urlChanged.connect(self._on_url_changed)
        self.browser.loadProgress.connect(self._on_load_progress)

        self.browser.hide()
        
    def apply_site_optimizations(self):
        url = self.browser.url().toString()
        if not url or url == "about:blank":
            return
        
        data = self.selector_manager.get_data_for_url(url)
        if not data:
            QTimer.singleShot(100, self.browser.show)
            return
        
        self._is_optimized_for_current_url = True
        
        if data.get("force_desktop"):
            # 横長（デスクトップ）モードのときだけ実行
            if self.width() > self.layout_threshold:
                self._force_desktop_layout()

        css = data.get("injected_css", "")
        hide_selectors = json.dumps(data.get("hide_elements", [])) # 事前にJSON化

        # JSコードの強化：document.body の存在チェックを追加
        js_code = f"""
        (function() {{
            const runOptimizations = () => {{
                if (!document.body) return false;

                // A. CSS注入（差分があるときだけ更新）
                const styleId = 'resident-optimized-style';
                const newCss = `{css}`;
                let style = document.getElementById(styleId);
                if (!style) {{
                    style = document.createElement('style');
                    style.id = styleId;
                    (document.head || document.documentElement).appendChild(style);
                }}
                // 【改善点】内容が同じならDOMを触らない（再描画コストの削減）
                if (style.textContent !== newCss) {{
                    style.textContent = newCss;
                }}

                // B. 要素隠蔽
                const selectors = {hide_selectors};
                const hideElements = () => {{
                    // 【改善点】ボディがなければ中断（エラー防止）
                    if (!document.body) return;
                    selectors.forEach(s => {{
                        document.querySelectorAll(s).forEach(el => {{
                            if (el.style.display !== 'none') el.style.display = 'none';
                        }});
                    }});
                }};

                // 初回実行
                hideElements();

                // 【改善点】既存の監視役を確実に殺してから新しい監視を始める
                if (window.residentObserver) {{
                    window.residentObserver.disconnect();
                    window.residentObserver = null;
                }}
                
                window.residentObserver = new MutationObserver(hideElements);
                window.residentObserver.observe(document.body, {{ childList: true, subtree: true }});
                return true;
            }};

            // 実行制御
            if (!runOptimizations()) {{
                // 【改善点】待機用のObserverも二重にならないよう名前をつける
                if (window.residentBodyWaiter) window.residentBodyWaiter.disconnect();
                window.residentBodyWaiter = new MutationObserver((mutations, obs) => {{
                    if (document.body) {{
                        runOptimizations();
                        obs.disconnect();
                        window.residentBodyWaiter = null;
                    }}
                }});
                window.residentBodyWaiter.observe(document.documentElement, {{ childList: true }});
            }}
        }})();
        """
        self.browser.page().runJavaScript(js_code)
        # ロード中であれば表示させる
        QTimer.singleShot(150, self.browser.show)
        
    def setup_flicker_free_script(self):
        """設定から背景色を取得し、ページ遷移時のチラつき防止スクリプトを注入"""
        
        # 1. SelectorManager等から現在のドメイン設定を取得
        # ※設定がない場合のデフォルト値を #121212 (ダークグレー) に設定
        current_url = self.browser.url().toString()
        # ここでは仮に self.app_settings から色を引く想定
        # 実際にはURLからドメインを特定して色を選択するロジックをここに挟めます
        bg_color = "#121212" 

        # 2. JavaScriptに色を埋め込む (Pythonの f-string を使用)
        js_code = f"""
        (function() {{
            var css = 'html {{ background-color: {bg_color} !important; }}';
            var style = document.createElement('style');
            style.type = 'text/css';
            style.id = 'anti-flash-script';
            if (style.styleSheet) {{
                style.styleSheet.cssText = css;
            }} else {{
                style.appendChild(document.createTextNode(css));
            }}
            document.documentElement.appendChild(style);
        }})();
        """

        script = QWebEngineScript()
        script.setSourceCode(js_code)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)

        self.browser.page().profile().scripts().insert(script)
        
        # 3. ブラウザ自体の背景色も同期させる
        self.browser.page().setBackgroundColor(QColor(bg_color))
            
    def load_selectors(self):
        """外部設定ファイルを読み込み、メモリ上の変数に格納する"""
        try:
            with open(SELECTORS_FILE, "r", encoding="utf-8") as f:
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
        
        
        

    def _setup_main_ui(self):
        # --- 1. 状態の初期化 ---
        # プロパティ app_settings から初期モードを取得
        initial_mode = self.app_settings.get("search_mode", "google")
        self.search_mode = initial_mode

        # --- 2. UIの構築 ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 検索バーコンテナ
        self.search_container = QWidget()
        self.search_container.setStyleSheet(STYLE_SEARCH_CONTAINER)
        self.search_container.setMaximumHeight(SEARCH_BAR_HEIGHT)
        s_layout = QHBoxLayout(self.search_container)
        s_layout.setContentsMargins(5, 2, 5, 2)
        s_layout.setSpacing(5)

        # モード切替ボタン
        self.mode_toggle = QPushButton()
        self.mode_toggle.setFixedSize(*SEARCH_TOGGLE_SIZE)
        self.mode_toggle.clicked.connect(self.toggle_search_mode)
        s_layout.addWidget(self.mode_toggle)

        # 検索バー本体
        self.search_bar = QLineEdit()
        self.search_bar.installEventFilter(self)
        self.search_bar.returnPressed.connect(self._handle_search_enter)
        s_layout.addWidget(self.search_bar)

        # お気に入り / ページ内検索グループの作成
        self._setup_sub_groups(s_layout)

        layout.addWidget(self.search_container)
        self.search_container.hide()
        layout.addWidget(self.browser)

        # --- 3. 状態の反映 ---
        # 重複を避けるため、現在のモードに基づいてUIを一度更新する
        self._update_search_ui_style()

    def _update_search_ui_style(self):
        """現在の self.search_mode に基づいてUI表示を同期する"""
        settings = SEARCH_MODES.get(self.search_mode, SEARCH_MODES["google"])
        is_google = (self.search_mode == "google")
        
        # 1. ボタンと検索バーの基本スタイル更新
        self.mode_toggle.setText(settings["label"])
        self.mode_toggle.setStyleSheet(
            STYLE_MODE_BUTTON.format(bg=settings["bg_color"], text=settings["accent_color"])
        )
        self.search_bar.setPlaceholderText(settings["placeholder"])

        # 2. グループ要素の表示一括制御
        # fav_group, find_group が setup_sub_groups で正しく生成されている前提
        if hasattr(self, 'fav_group'):
            self.fav_group.setVisible(is_google)
        if hasattr(self, 'find_group'):
            self.find_group.setVisible(not is_google)
        
        # 3. Googleモード固有の処理
        if is_google:
            self.refresh_favorites_ui()

    def toggle_search_mode(self):
        """モードを反転させ、スタイル更新と設定保存を行う"""
        self.search_mode = "find" if self.search_mode == "google" else "google"
        
        self._update_search_ui_style()

        # 設定の保存（プロパティ経由で更新）
        self.app_settings["search_mode"] = self.search_mode
        self.config_manager.save_config()
        
        self.search_bar.clear()
        self.search_bar.setFocus()
        
        
        
        
    def _handle_search_enter(self):
        query = self.search_bar.text().strip()
        if not query:
            return
        # search_mode はプロパティとして app_settings から取得されている想定
        if self.search_mode == "google":
            self._process_web_navigation(query)
        else:
            # ページ内検索のキャッシュ比較
            if query != self._last_search_query:
                self._last_search_query = query
                self.browser.findText("") # 前回のハイライトをクリア
                self._find_with_count(backward=False)
            else:
                self._process_in_page_search()

    def _process_web_navigation(self, text):
        """URLか検索語かを判定し、遷移または外部起動を実行"""
        # URL判定ロジックの整理 「検索したいのにURLだと誤認される」という不便が出た場合は、urllib.parse を使った厳密なバリデーションへ切り替える
        is_explicit_url = text.startswith(URL_SCHEMES)
        is_implicit_url = ('.' in text and ' ' not in text)
        if is_explicit_url or is_implicit_url:
            target_url = text if is_explicit_url else f"https://{text}"
            # 外部ブラウザ転送判定（定数リストを使用）
            if any(keyword in target_url for keyword in EXTERNAL_BROWSER_KEYWORDS):
                import webbrowser
                webbrowser.open(target_url)
                self.search_container.hide()
                return 
            self.browser.setUrl(QUrl(target_url))
        else:
            # 検索クエリの発行（定数からフォーマット）
            url = SEARCH_ENGINE_URL.format(text)
            self.browser.setUrl(QUrl(url))
        self.search_container.hide()
        self.browser.setFocus()

    def _process_in_page_search(self):
        """現在のキー入力状態（Shift）を確認し、ページ内検索を実行"""
        modifiers = QApplication.keyboardModifiers()
        is_backward = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        self._find_with_count(backward=is_backward)

    def _update_hit_count(self, result):
        # hasattr によるチェックは維持しつつ、デフォルト表記を定数化
        if not getattr(self, 'hit_label', None):
            return
        num_matches = result.numberOfMatches()
        active_index = result.activeMatch()
        if num_matches > 0:
            self.hit_label.setText(f"{active_index}/{num_matches}")
        else:
            self.hit_label.setText(FIND_DEFAULT_COUNT)
        
    def _find_with_count(self, backward=False):
        text = self.search_bar.text()
        flags = QWebEnginePage.FindFlag(0)
        if backward:
            flags |= QWebEnginePage.FindFlag.FindBackward
        # 検索実行
        self.browser.findText(text, flags, self._update_hit_count)

    def eventFilter(self, obj, event):
        if sip.isdeleted(obj):
            return False

        # 初期化途中の Attribute Error 回避
        if not hasattr(self, 'search_bar'):
            return super().eventFilter(obj, event)

        # 監視対象リスト（インジケーターが存在すれば追加）
        target_objects = [
            self.browser, 
            self.browser.focusProxy(), 
            self.search_bar
        ]
        if hasattr(self, 'collapsed_indicator') and self.collapsed_indicator:
            target_objects.append(self.collapsed_indicator)

        if obj not in target_objects:
            return super().eventFilter(obj, event)

        if event.type() == QEvent.Type.KeyPress:
            # 入力欄での Enter はスルー（検索実行のため）
            if obj == self.search_bar and event.key() in [Qt.Key.Key_Return, Qt.Key.Key_Enter]:
                return super().eventFilter(obj, event)

            # ショートカット判定
            if self._handle_keypress_event(event):
                return True # 文字入力をブロック
                    
        # その他のイベント（変更なし）
        elif event.type() == QEvent.Type.Wheel:
            if self._handle_wheel_event(event): return True
        elif event.type() in [QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease]:
            if self._handle_mouse_event(event): return True

        return super().eventFilter(obj, event)

    def _handle_keypress_event(self, event):
        """キーボード操作の判定ロジック"""
        key = event.key()
        modifiers = event.modifiers()
        
        # 1. 特定の修飾キーに依存しない共通ショートカット (ESCで検索閉じる)
        if key == Qt.Key.Key_Escape and self.search_container.isVisible():
            self.search_container.hide()
            self.browser.setFocus()
            return True

        # 2. Controlキー固定のショートカット (ブラウザ標準の挙動に合わせる)
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
            elif key == Qt.Key.Key_R:
                self.browser.reload()
                return True

        # 3. 動的な修飾キー (デフォルト Alt) を使用するショートカット
        modifier_mask = getattr(Qt.KeyboardModifier, f"{DEFAULT_MODIFIER.capitalize()}Modifier")
        if bool(modifiers & modifier_mask):
            shortcuts = self.app_settings.get("shortcuts", {})
            key_char = chr(key).lower() if ASCII_PRINTABLE_MIN <= key <= ASCII_PRINTABLE_MAX else ""
            
            # 判定対象を明確化（cycle_size 'd' を追加）
            show_key = shortcuts.get("show_toggle", "s")
            hide_key = shortcuts.get("hide_completely", "w")
            cycle_key = shortcuts.get("cycle_size", "d")

            if key_char == show_key:
                self.handle_show_request()
                return True
            elif key_char == hide_key:
                self.update_display_mode(DisplayMode.HIDDEN)
                return True
            elif key_char == cycle_key:
                self.cycle_geometry() # ここでTrueを返すことで 'd' の入力を防ぐ
                return True
            
            # 方向キーによるナビゲーション
            if key == Qt.Key.Key_Left:
                self.browser.back()
                return True
            elif key == Qt.Key.Key_Right:
                self.browser.forward()
                return True
                    
        return False

    def _handle_wheel_event(self, event):
        current_time = time.time()
    
        delta = event.pixelDelta() if event.pixelDelta() else event.angleDelta()
        dx = delta.x()
        dy = delta.y()

        # --- 1. 状態のリセットロジック ---
        last_t = getattr(self, 'last_wheel_time', 0)
        if current_time - last_t > 0.1:
            self.swipe_start_time = current_time
            self.swipe_acc_x = 0
            self.event_count = 0  # イベントの発生回数をカウント
        self.last_wheel_time = current_time

        # --- 2. 基本ガード ---
        if abs(dy) > abs(dx):
            self.swipe_acc_x = 0
            return False
        
        if (current_time * 1000) - getattr(self, 'last_swipe_time', 0) < 600:
            return False

        # --- 3. 蓄積とカウント ---
        self.swipe_acc_x += dx
        self.event_count = getattr(self, 'event_count', 0) + 1 # 回数をカウント
        
        duration = current_time - getattr(self, 'swipe_start_time', current_time)

        # --- 4. 実行判定（条件を3段構えにする） ---
        # 1. 継続時間（0.1秒以上）
        # 2. 累積距離（WHEEL_SWIPE_ACCUM_TARGET）
        # 3. イベントの密度（例：15回以上のイベントが連続していること）
        if duration > 0.1 and abs(self.swipe_acc_x) > WHEEL_SWIPE_ACCUM_TARGET:
            if getattr(self, 'event_count', 0) > 25: # ★ここがノイズ除去の肝
                if self.swipe_acc_x > 0:
                    self.browser.back()
                else:
                    self.browser.forward()
                
                self.last_swipe_time = current_time * 1000
                self.swipe_acc_x = 0 
                self.swipe_start_time = 0
                self.event_count = 0
                return True
        
        return False

    def _handle_mouse_event(self, event):
        """マウスボタン・ドラッグスワイプの判定ロジック"""
        etype = event.type()
        
        if etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._is_left_button_pressed = True # 選択開始
                self._mouse_press_pos = event.position()
        
        elif etype == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._is_left_button_pressed = False # 選択終了
        
        if etype == QEvent.Type.MouseButtonPress:
            # ゲーミングマウスなどのサイドボタン（進む・戻る）
            if event.button() == Qt.MouseButton.XButton1:
                self.browser.back()
                return True
            elif event.button() == Qt.MouseButton.XButton2:
                self.browser.forward()
                return True
                
            if event.button() == Qt.MouseButton.LeftButton:
                self._mouse_press_pos = event.position()

        elif etype == QEvent.Type.MouseButtonRelease:
            if getattr(self, '_mouse_press_pos', None):
                if event.button() == Qt.MouseButton.LeftButton:
                    delta_x = event.position().x() - self._mouse_press_pos.x()
                    self._mouse_press_pos = None
                    
                    # スワイプ距離の判定を定数化
                    if abs(delta_x) > SWIPE_THRESHOLD_X:
                        if delta_x > 0:
                            self.browser.back()
                        else:
                            self.browser.forward()
                        return True
        return False

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
        menu = self._create_base_menu()
        
        # 1. ブラウザ操作 (定数を使用)
        menu.addAction(MENU_TEXT_BACK).triggered.connect(self.browser.back)
        menu.addAction(MENU_TEXT_FORWARD).triggered.connect(self.browser.forward)
        menu.addAction(MENU_TEXT_RELOAD).triggered.connect(self.browser.reload)
        menu.addSeparator()

        # 2. プリセット切り替え（サブメニュー）
        self._add_preset_switch_menu(menu)
        menu.addSeparator()

        # 3. 設定・保存系
        save_geo_action = menu.addAction(MENU_TEXT_SAVE_GEO)
        save_geo_action.triggered.connect(self.add_current_geometry_to_preset)
        
        menu.addSeparator()
        menu.addAction(MENU_TEXT_COLLAPSE).triggered.connect(
            lambda: self.update_display_mode(DisplayMode.COLLAPSED)
        )
        
        menu.exec(QCursor.pos())

    def _add_preset_switch_menu(self, parent_menu):
        """プリセット切り替え用サブメニューを構築"""
        preset_menu = parent_menu.addMenu(MENU_TEXT_PRESET_SWITCH)
        
        # 整理したプロパティを使用してデータを取得
        presets = self.presets  # @property で定義済み
        current_idx = self._current_preset_idx # @property で定義済み

        for i, preset in enumerate(presets):
            name = preset.get("name", f"Preset {i}")
            action = preset_menu.addAction(name)
            
            action.setCheckable(True)
            if i == current_idx:
                action.setChecked(True)
                
            # index をクロージャで固定して接続
            action.triggered.connect(lambda _, idx=i: self.apply_preset(idx))

    def _create_base_menu(self):
        """スタイルの適用されたQMenuを生成する"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(STYLE_QMENU)
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

        # プロパティ current_preset を経由してリストを更新
        self.current_preset.setdefault("locations", []).append(new_loc)

        self.config_manager.save_config()
        # 通知の文言も将来的に定数化すると多言語対応しやすくなります
        self._display_preset_notification("★New Size Pattern Locked & Added!")
        
    def adjust_zoom(self, force_desktop=False):
        """現在のウィンドウ幅に基づきズームを調整"""
        if not getattr(self, 'config_manager', None):
            return

        # 1. デスクトップ版の固定ズーム
        if force_desktop:
            self.browser.setZoomFactor(self.desktop_zoom_default)
            return

        # 2. モバイル版の動的ズーム計算
        new_width = self.width()
        if getattr(self, '_last_zoom_width', None) == new_width:
            return

        try:
            base_width = self.current_preset.get("base_width", DF_BASE_WIDTH)
            # 定数を使って範囲を制限（クランプ）
            zoom_level = max(ZOOM_MIN_LIMIT, min(new_width / base_width, ZOOM_MAX_LIMIT))
            
            self.browser.setZoomFactor(zoom_level)
            self._last_zoom_width = new_width
        except ZeroDivisionError:
            pass
        
    def cycle_geometry(self):
        """現在のプリセット内で locations を巡回する"""
        self.setUpdatesEnabled(False)
        try:
            self.save_current_state()
            locations = self.current_preset.get("locations", [])
            if not locations:
                self._display_preset_notification(MSG_NO_LOCATIONS)
                return
            # インデックス更新
            self.current_location_index = (self.current_location_index + 1) % len(locations)
            self.apply_config_geometry()
            # 通知メッセージの構築（定数テンプレートを使用）
            msg = MSG_SIZE_CYCLE.format(
                current=self.current_location_index + 1, 
                total=len(locations)
            )
            self._display_preset_notification(msg)
        except Exception as e:
            print(f"Error in cycle_geometry: {e}")
        finally:
            self.setUpdatesEnabled(True)
            self.browser.update()
            self.config_manager.save_config()

    def apply_config_geometry(self):
        """現在選択されているインデックスに基づいて、ウィンドウのサイズと位置を適用する"""
        locations = self.current_preset.get("locations", [])
        if not locations:
            return
        try:
            loc = locations[self.current_location_index]
            self.setGeometry(loc["x"], loc["y"], loc["width"], loc["height"])
            self.setWindowOpacity(loc.get("opacity", 1.0))
            # レイアウト判定（プロパティ化されたしきい値を使用）
            if self.width() > self.layout_threshold:
                self.browser.setZoomFactor(self.desktop_zoom_default)
                self._force_desktop_layout()
            else:
                self.browser.setZoomFactor(self.mobile_zoom_default)
        except (IndexError, KeyError) as e:
            print(f"Failed to apply geometry: {e}")
    
    def save_current_state(self):
        """現在の状態（URL、およびアンロック時のみ座標）を保存する"""
        if not getattr(self, 'config_manager', None):
            return
        try:
            current_url = self.browser.url().toString()
            # 無効なURLリストに含まれていないかチェック
            if current_url and current_url not in INVALID_URLS:
                self.current_preset["last_url"] = current_url
            self._update_geometry_if_unlocked()
            self.config_manager.save_config()
        except Exception as e:
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
        """レイアウトの土台をJSで整える"""
        if mode == "desktop":
            self._force_desktop_layout()
            content = VIEWPORT_DESKTOP
        else:
            content = VIEWPORT_MOBILE
            
        script = JS_SET_VIEWPORT.format(content=content)
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
        # インジケーター生成後のコールバック(_update_indicator_with_state)内で
        # self.collapsed_indicator.installEventFilter(self) を実行してください
        self.browser.page().runJavaScript(JS_GET_VIDEO_STATE, self._update_indicator_with_state)

    def _reset_transition_flag(self):
        self._is_switching_mode = False

    # --- インジケーター更新の司令塔 ---
    def _update_indicator_with_state(self, state):
        """【最新・最適化版司令塔】タイトルが同じなら中身の更新のみを行い、再配置をスキップする"""
        # 1. 状態の正規化
        state = state if state in ('playing', 'paused') else 'stopped'
        
        # 2. スケール反映と存在確認
        scale = self.app_settings.get("global_indicator_scale", 1.0)
        self._ensure_indicator_exists(scale)
        
        # 3. 判定準備
        current_raw_title = self.browser.title()
        is_visible = self.collapsed_indicator.isVisible()
        last_title = getattr(self, '_last_processed_title', None)
        last_state = getattr(self, '_last_processed_state', None)

        # A. 完全一致なら何もせずリターン（最も軽いパス）
        if is_visible and last_title == current_raw_title and last_state == state:
            return

        # B. 【魔法のパス】タイトルは同じだが状態だけが違う場合
        # アイコンの更新（_set_indicator_content）だけを行い、再配置（_finalize_indicator_geometry）をスキップ
        if is_visible and last_title == current_raw_title:
            self._set_indicator_content(state)  # アイコン（中身）だけ書き換え
            self._last_processed_state = state   # 状態を保存
            return # ここで終了！ geometry の再計算をさせない

        # --- C. フル更新（タイトルが変わった場合や初回表示時） ---
        self._last_processed_title = current_raw_title
        self._last_processed_state = state
        
        self.collapsed_indicator.setUpdatesEnabled(False)
        try:
            # 担当メソッドへの自律的委譲
            self._apply_indicator_style()       # 担当1：見た目
            self._set_indicator_content(state)  # 担当2：中身
            self._finalize_indicator_geometry() # 担当3：配置（ここでビクッとする可能性がある）
        finally:
            self.collapsed_indicator.setUpdatesEnabled(True)

        # 4. 表示状態の管理
        if not is_visible:
            self.collapsed_indicator.show()
        self.collapsed_indicator.raise_()

    # --- 担当1：見た目（スタイル） ---
    def _apply_indicator_style(self):
        """プロパティからスタイルとスケールを取得し、ラベルに反映"""
        styles = self.current_preset.get("indicator_styles", {})
        scale = self.app_settings.get("global_indicator_scale", 1.0)
        
        self.collapsed_indicator.apply_indicator_styles(styles)
        
        # スタイルシートの動的構築
        label_style = STYLE_INDICATOR_LABEL.format(
            color=styles.get("text_color", COLOR_INDICATOR),
            size=int(INDICATOR_FONT_BASE_SIZE * scale)
        )
        
        self.icon_label.setStyleSheet(label_style)
        self.text_label.setStyleSheet(label_style)

    # --- 担当2：中身（コンテンツ） ---
    def _set_indicator_content(self, state):
        """再生状態に応じた内容の設定"""
        icon = self.indicator_icons.get(state, self.indicator_icons["stopped"])
        self.icon_label.setText(icon)

        # タイトルの加工（定数化した正規表現を使用）
        raw_title = self.browser.title()
        clean_title = re.sub(RE_TITLE_NOTIF, '', raw_title)
        if not clean_title or clean_title == "about:blank": 
            clean_title = DEFAULT_APP_NAME
        
        max_len = self.current_preset.get("indicator_styles", {}).get("max_title_length", DF_MAX_TITLE_LEN)
        
        display_title = (clean_title[:max_len] + "...") if len(clean_title) > max_len else clean_title
        self.text_label.setText(display_title)

    # --- 担当3：配置（ジオメトリ） ---
    def _finalize_indicator_geometry(self):
        """サイズと位置の最終確定"""
        styles = self.current_preset.get("indicator_styles", {})
        shape = styles.get("shape", "rounded_rect")
        scale = self.app_settings.get("global_indicator_scale", 1.0)

        if shape == "circle":
            self.text_label.hide()
            side = int(INDICATOR_CIRCLE_BASE_SIZE * scale)
            target_size = QSize(side, side)
            self.icon_label.setFixedWidth(side)
        else:
            self.text_label.show()
            # 制約リセットと最小幅の設定
            self.collapsed_indicator.setFixedSize(QSize(-1, -1))
            self.collapsed_indicator.setMinimumSize(int(INDICATOR_MIN_WIDTH_BASE * scale), 0)
            self.icon_label.setFixedWidth(int(INDICATOR_ICON_BASE_WIDTH * scale))
            
            # 余白のスケーリング適用
            m = [int(val * scale) for val in INDICATOR_MARGINS_BASE]
            self.collapsed_indicator.layout().setContentsMargins(*m)
            
            self.collapsed_indicator.layout().activate()
            target_size = self.collapsed_indicator.layout().sizeHint()

        # 座標計算
        screen = QApplication.primaryScreen().availableGeometry()
        margin = self.indicator_screen_margin
        new_x = screen.right() - target_size.width() - margin
        new_y = screen.bottom() - target_size.height() - margin

        self.collapsed_indicator.setGeometry(new_x, new_y, target_size.width(), target_size.height())
        self.collapsed_indicator.setFixedSize(target_size)

    # --- インジケーターの生成補助 ---
    def _ensure_indicator_exists(self, scale):
        """インジケーターの初期化とフラグ設定"""
        if self.collapsed_indicator:
            return

        self.collapsed_indicator = IndicatorWidget("", None)
        
        # constants の文字列リストから Qt フラグを合成
        flags = Qt.WindowType.Widget  # ベース
        for flag_name in INDICATOR_WINDOW_FLAGS:
            flags |= getattr(Qt.WindowType, flag_name)
        
        self.collapsed_indicator.setWindowFlags(flags)
        self.collapsed_indicator.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QHBoxLayout(self.collapsed_indicator)
        layout.setSpacing(int(INDICATOR_SPACING_BASE * scale))
        
        self.icon_label = QLabel() 
        self.text_label = QLabel()
        self.text_label.setWordWrap(False)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        
        self.collapsed_indicator.clicked.connect(self._handle_indicator_click)

    def _handle_indicator_click(self, area):
        """再生制御または復元"""
        # 復元条件の判定
        is_circle = getattr(self, 'is_circle_mode', False)
        if is_circle or area == "title":
            self.show_and_activate()
            return
        
        # アイコンクリック時は再生トグル
        if area == "icon":
            self.browser.page().runJavaScript(JS_TOGGLE_PLAYBACK)
            # 再生状態が変わるのを待ってから表示を更新
            QTimer.singleShot(INDICATOR_CLICK_REFRESH_DELAY, self._show_indicator)
            
    def show_and_activate(self):
        """小窓の復元とインジケーターの破棄"""
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.show()
        self.raise_()
        self.activateWindow()
        if hasattr(self, 'collapsed_indicator') and self.collapsed_indicator:
            self.collapsed_indicator.hide()

    def _on_load_finished(self, ok):
        """ページ読み込み完了時の最終確定プロセス"""
        if not ok: return

        # 1. 通信プロキシ等のインフラ設定
        if hasattr(self, '_install_proxy_filter'):
            self._install_proxy_filter()

        # 2. 即時最適化
        self.apply_site_optimizations()
        if self.width() > self.layout_threshold:
            self._force_desktop_layout()
        
        # 3. 遅延実行（動的コンテンツへのダメ押し）
        # サイト最適化の再実行
        QTimer.singleShot(DELAY_SITE_OPTIMIZE_RETRY, self.apply_site_optimizations)
        
        # デスクトップレイアウトの再適用
        if self.width() > self.layout_threshold:
            QTimer.singleShot(DELAY_DESKTOP_LAYOUT_RETRY, self._force_desktop_layout)

        # 4. 最終表示の保険
        QTimer.singleShot(DELAY_LOAD_FINISHED_DEFAULT, self.browser.show)
                
    def _on_url_changed(self, url):
        """URLが変わったら最適化フラグをリセットする"""
        url_str = url.toString()
        if url_str and url_str != URL_BLANK:
            self._is_optimized_for_current_url = False

    def _on_load_progress(self, progress):
        """読み込み中に先行して最適化をかける"""
        # しきい値を定数化し、フラグで多重実行を防止
        if progress > OPTIMIZE_PROGRESS_THRESHOLD and not self._is_optimized_for_current_url:
            self.apply_site_optimizations()
            self._is_optimized_for_current_url = True

    def show_notification(self, duration):
        """フェードイン開始"""
        self.show()
        # 開始/終了値を定数から取得
        self.fade_animation.setStartValue(NOTIF_OPACITY_START)
        self.fade_animation.setEndValue(NOTIF_OPACITY_END)
        self.fade_animation.setDirection(QPropertyAnimation.Direction.Forward)
        self.fade_animation.start()
        # 指定時間後にフェードアウトを開始
        QTimer.singleShot(duration, self._hide_notification)

    def _hide_notification(self):
        """フェードアウトして削除"""
        # 方向を逆転（Backward）させてフェードアウト
        self.fade_animation.setDirection(QPropertyAnimation.Direction.Backward)
        # finished シグナルは重複接続を避けるため、一度切断してから繋ぐか、
        # もしくは初期化時に一度だけ繋いでおくのが安全
        try:
            self.fade_animation.finished.disconnect()
        except TypeError:
            pass
        self.fade_animation.finished.connect(self.deleteLater)
        self.fade_animation.start()

# ==========================================
# 3. 各種シグナル・ホットキー処理
# ==========================================
def get_portal_url():
    """アクティブなブラウザからURLを抽出する"""
    try:
        from pywinauto import Desktop
        target_windows = [
            w for w in Desktop(backend="uia").windows(visible_only=True) 
            if any(k in w.window_text() for k in TARGET_BROWSER_KEYWORDS)
            and all(e not in w.window_text() for e in EXCLUDE_WINDOW_KEYWORDS)
            and w.class_name() != EXPLORER_CLASS_NAME
        ]
        
        if not target_windows:
            return None

        for edit in target_windows[0].descendants(control_type="Edit"):
            try:
                val = edit.get_value()
                # URL_SCHEMES ('http://', 'https://') のいずれかを含むかチェック
                is_url = any(scheme in val for scheme in URL_SCHEMES) or "." in val
                if val and is_url:
                    # 既に scheme があればそのまま、なければ https:// を付与
                    return val if any(val.startswith(s) for s in URL_SCHEMES) else f"{HTTPS_PREFIX}{val}"
            except:
                continue
    except Exception as e:
        print(f"Portal URL extraction failed: {e}")
    return None

last_action_time = 0
def check_hotkeys():
    global last_action_time
    now = time.time()
    
    if now - last_action_time < HOTKEY_DEBOUNCE_SEC:
        return
    if current_window is None:
        return

    shortcuts = current_window.config_manager.data.get("app_settings", {}).get("shortcuts", {})
    modifier = shortcuts.get("modifier", DEFAULT_MODIFIER).lower()
    
    # 動的修飾キー（Alt等）が押されているかチェック
    if not keyboard.is_pressed(modifier):
        return

    is_shift = keyboard.is_pressed('shift')
    is_ctrl = keyboard.is_pressed('ctrl')

    # Ctrlが混ざっている場合は、eventFilter側の処理（Ctrl+F/R）に任せるため、
    # ここでのグローバル処理（Alt+S等）はスキップする
    if is_ctrl:
        return

    # キーマッピングの構築
    mapping = {
        shortcuts.get("hide_completely", "w"): bridge.hide_completely_requested,
        shortcuts.get("show_toggle", "s"):      bridge.show_requested,
        shortcuts.get("copy", "c"):             bridge.copy_requested,
        shortcuts.get("paste", "v"):           bridge.paste_requested,
        shortcuts.get("cycle_size", "d"):      bridge.cycle_geometry_requested
    }

    for key, signal in mapping.items():
        if keyboard.is_pressed(key):
            # Shift除外ルールの適用
            if key in ["s", "d"] and is_shift:
                continue
            signal.emit()
            last_action_time = now
            return

    # 数字キー（プリセット切替）
    if current_window.app_settings.get("enable_number_shortcuts", True):
        for i in range(1, 10): # 1-9
            if keyboard.is_pressed(str(i)):
                bridge.preset_switch_requested.emit(i - 1)
                last_action_time = now
                return

def show_critical_error(message):
    """起動失敗をユーザーに通知するための共通関数"""
    # まだメインウィンドウがない場合、標準のメッセージボックスを使用
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Icon.Critical)
    msg.setText("Application Startup Failed")
    msg.setInformativeText(message)
    msg.setWindowTitle("Error")
    msg.exec()

def main():
    print(f"[{time.strftime('%H:%M:%S')}] Application startup...")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    try:
        # 1. マネージャー類の初期化（異常系のガード）
        print(f"[{time.strftime('%H:%M:%S')}] Initializing managers...")
        # ConfigManager内でファイル読み込みエラーがあれば、ここで捕捉される
        config_manager = ConfigManager(CONFIG_FILE)
        app_settings = config_manager.data.get("app_settings", {})
        
        selector_manager = SelectorManager(
            local_path=SELECTORS_FILE, 
            remote_url=app_settings.get("selectors_url")
        )

        # 2. メインウィンドウ生成（シングルトンの意識）
        print(f"[{time.strftime('%H:%M:%S')}] Creating ResidentMiniPlayer...")
        global current_window
        
        # 二重生成を防ぐチェック（簡易的なシングルトン実装）
        if 'current_window' in globals() and current_window is not None:
            raise RuntimeError("An instance of ResidentMiniPlayer already exists.")

        main_window = ResidentMiniPlayer(config_manager, selector_manager)
        current_window = main_window

        # 3. シグナル接続
        print(f"[{time.strftime('%H:%M:%S')}] Connecting signals...")
        connect_app_signals(main_window) # 共通化のために別関数へ切り出し推奨

        # 4. 監視タイマー開始
        setup_hotkey_monitor(main_window)

        # 5. アプリケーション表示
        print(f"[{time.strftime('%H:%M:%S')}] Showing main window...")
        main_window.show()
        QApplication.processEvents() 
        
        print(f"[{time.strftime('%H:%M:%S')}] Entering event loop.")
        main_window.update_display_mode(DisplayMode.EXPANDED)
        
        # イベントループ開始
        exit_code = app.exec()
        sys.exit(exit_code)

    except FileNotFoundError as e:
        # 必須ファイルがない場合
        show_critical_error(f"Required file missing: {os.path.basename(e.filename)}")
    except Exception as e:
        # 予期せぬ致命的なエラー
        show_critical_error(f"Unexpected error: {str(e)}")
        print(f"CRITICAL ERROR: {e}")
    finally:
        # 終了時のクリーンアップ処理が必要ならここに記述
        print(f"[{time.strftime('%H:%M:%S')}] Application shutting down.")

def connect_app_signals(window):
    """シグナル接続を一元管理（メンテナンス性向上）"""
    bridge.show_requested.connect(window.handle_show_request)
    bridge.hide_completely_requested.connect(
        lambda: window.update_display_mode(DisplayMode.HIDDEN)
    )
    bridge.copy_requested.connect(window.capture_current_url)
    bridge.paste_requested.connect(window.apply_url_from_dispatch)
    bridge.cycle_geometry_requested.connect(window.cycle_geometry)
    bridge.preset_switch_requested.connect(window.apply_preset)

def setup_hotkey_monitor(window):
    """監視タイマーの初期化（役割の分離）"""
    monitor_timer = QTimer(window)
    monitor_timer.timeout.connect(check_hotkeys)
    monitor_timer.start(HOTKEY_MONITOR_INTERVAL_MS)

if __name__ == "__main__":
    main()