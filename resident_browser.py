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
    Qt, QUrl, QEvent, QTimer, QObject, pyqtSignal, QPropertyAnimation, QEasingCurve, QByteArray
)
from PyQt6.QtGui import QCursor, QFont, QPainter, QBrush, QColor, QPen, QShortcut, QKeySequence
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
    # --- 1. スキーマの定義（唯一の正解とする） ---
    DEFAULT_CONFIG = {
        "app_settings": {
            "auto_start": False,
            "last_active_preset_index": 0,
            "global_indicator_scale": 1.0,
            "selectors_url": None,  # セレクタ管理との連携用
            "search_mode": "google",
            "developer_notes": {
                "reference_url": "https://gemini.google.com/app",
                "last_modified": "2026-05-08" # メタデータとして役立ちます
            }
        },
        "presets": [
            {
                "name": "デフォルト",
                "last_url": "https://www.google.com",
                "favorites": [
                    "https://www.youtube.com",
                    "https://gemini.google.com/app"
                ],
                "base_width": 500,
                "indicator_styles": {
                    "shape": "rounded_rect",
                    "text_color": "#00FF00",
                    "bg_type": "solid",
                    "bg_color": "#2C3E50",
                    "bg_gradient": ["#2C3E50", "#000000"]
                },
                "locations": [
                    { "x": 100, "y": 100, "width": 400, "height": 300, "opacity": 1.0, "is_locked": True }
                ]
            }
        ]
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
        self.setWindowTitle("Resident Browser")
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
                self.browser.setUrl(QUrl(str(preset["last_url"])))
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
            self.show_floating_notify("No URL to show. Press Alt+C.")
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
                self.show_floating_notify("★Stealth Mode Activated") # 潜伏したことを通知

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
            self.show_floating_notify("★Target URL Saved!")

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
            self.show_floating_notify("★URL Loaded from Clipboard")
        else:
            target_url = preset.get("last_url", "https://www.google.com")
            self.show_floating_notify("★URL Restored from History")

        self.browser.setUrl(QUrl(target_url))
        self.update_display_mode(DisplayMode.EXPANDED) # 貼り付けたら即・小窓へ
        
    def show_floating_notify(self, text):
        """
        現在のプリセット色を取得して通知を表示する。
        以前のグローバル関数を置き換える。
        """
        # 1. 現在のプリセットからテーマ色を取得
        try:
            data = self.config_manager.data
            idx = data["app_settings"].get("last_active_preset_index", 0)
            color = data["presets"][idx]["indicator_styles"].get("text_color", "#00FF00")
        except (KeyError, IndexError):
            color = "#00FF00"

        # 2. 通知インスタンスの生成 (参照を保持しなくても deleteLater で消える)
        self._last_notification = FloatingNotification(text, color=color)
        
        
        
        
    def apply_preset(self, index):
        """
        指定されたインデックスのプリセットをアプリ全体に適用する
        """
        data = self.config_manager.data
        # ガード：存在しないインデックスが指定された場合
        if index >= len(data["presets"]):
            self.show_floating_notify(f"Preset {index + 1} not defined.")
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
        self.show_floating_notify(f"Switch -> {target_preset['name']}")
        
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

            // 2. Cookieの再セット
            document.cookie = "PREF=f6=40000; domain=.youtube.com; path=/";
            
            // 3. YouTubeの内部フラグ書き換え
            if (window.yt && window.yt.config_) {
                window.yt.config_.EXPERIMENT_FLAGS.kevlar_is_mweb_modern_f_and_e_interaction = false;
            }
            
            // 4. ViewportをPCサイズで固定（再定義）
            var meta = document.querySelector('meta[name="viewport"]');
            if (meta) { 
                meta.setAttribute('content', 'width=1280, initial-scale=1.0');
            } else {
                var newMeta = document.createElement('meta');
                newMeta.name = "viewport";
                newMeta.content = "width=1280";
                document.getElementsByTagName('head')[0].appendChild(newMeta);
            }

            // 5. YouTubeに「画面が変わったぞ」と叫ぶ
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
        from PyQt6.QtGui import QCursor
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
            self.show_floating_notify("Error: Preset not found.")
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
        self.show_floating_notify("★New Size Pattern Locked & Added!")
        
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
                self.show_floating_notify("No size patterns found.")
                return
            # 2. インデックスを次に進める（ループ処理）
            self.current_location_index = (self.current_location_index + 1) % len(locations)
            # 3. 新しい位置・サイズを適用
            self.apply_config_geometry()
            # 4. 通知を表示
            self.show_floating_notify(f"Size: {self.current_location_index + 1}/{len(locations)}")
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
        
        
        
        
    def _handle_audio_status(self, audible):
        """音が止まってもインジケーターは消さず、状態（アイコン）だけ更新する"""
        if not self.isVisible():
            # 音が止まった（一時停止した）瞬間に、アイコンを ♪ から || に変えるために再描画
            self._show_indicator()

    def _on_title_changed(self, title):
        # hasattr を使うことで、変数が存在しない場合のクラッシュを防ぐ
        if not hasattr(self, 'collapsed_indicator') or self.collapsed_indicator is None:
            return
        """動画が切り替わるなどしてタイトルが変わった時の処理"""
        # インジケーターが表示されている（格納状態である）時だけ更新をかける
        if self.collapsed_indicator and self.collapsed_indicator.isVisible():
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
        if not self.collapsed_indicator:
            # ClickableLabel は既存のクラスを使用
            self.collapsed_indicator = ClickableLabel("", None)
            self.collapsed_indicator.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | 
                Qt.WindowType.WindowStaysOnTopHint | 
                Qt.WindowType.Tool
            )
            self.collapsed_indicator.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.collapsed_indicator.clicked.connect(self._handle_indicator_click)
            
            layout = QHBoxLayout(self.collapsed_indicator)
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
        self.collapsed_indicator.setStyleSheet(container_style)

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
        self.collapsed_indicator.layout().setContentsMargins(
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
        self.collapsed_indicator.adjustSize()
        screen_rect = QApplication.primaryScreen().geometry()
        self.collapsed_indicator.move(
            screen_rect.width() - self.collapsed_indicator.width() - 10, 
            screen_rect.height() - 80 # タスクバーとの干渉を考慮
        )
        self.collapsed_indicator.show()

    def _handle_indicator_click(self, area):
        """インジケーターがクリックされた時の処理"""
        if area == "title":
            # タイトルクリックで再表示
            self.show()
            self.raise_()
            self.activateWindow()
            self._hide_collapsed_indicator()
        
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
        if self.collapsed_indicator:
            self.collapsed_indicator.hide()
            
    def toggle_indicator_mode(self):
        if self._is_switching_mode: return # 処理中なら無視
        self._is_switching_mode = True
        
        try:
            # 既存のトグル処理
            if self.collapsed_indicator and self.collapsed_indicator.isVisible():
                self.collapsed_indicator.close()
                # ここも QTimer で少し余裕を持たせる
                QTimer.singleShot(100, self.show_and_activate)
            else:
                self.hide()
                self._show_indicator()
        finally:
            # 0.5秒後にフラグを下ろす（重いサイト対策）
            QTimer.singleShot(500, self._reset_transition_flag)

    def _reset_transition_flag(self):
        self._is_switching_mode = False
            
    def force_indicator_mode(self):
        """Alt+W用：既にインジケーターなら何もしない、小窓なら確実にインジケーター化する"""
        if self._is_switching_mode: return
        
        # すでにインジケーターが表示中なら、重複処理を避けるために何もしない
        if self.collapsed_indicator and self.collapsed_indicator.isVisible():
            return

        self._is_switching_mode = True
        try:
            # 小窓を隠してインジケーターを表示
            self.hide()
            self._show_indicator()
        finally:
            # 0.5秒後にフラグをリセット（連打防止）
            QTimer.singleShot(500, self._reset_transition_flag)

    def _hide_collapsed_indicator(self):
        if self.collapsed_indicator:
            self.collapsed_indicator.hide()

    def collapse_to_indicator(self):
        """小窓を閉じたら、必ずインジケーターとして格納する"""
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
            
class FloatingNotification(QWidget):
    def __init__(self, text, color="#00FF00", duration=2500, parent=None):
        super().__init__(parent)
        
        # 1. ウィンドウ属性の設定
        # Tool: タスクバーに表示しない / Frameless: 枠なし / StaysOnTop: 最前面 / TransparentForInput: クリック透過
        self.setWindowFlags(
            Qt.WindowType.Tool | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(0.0)  # 最初は透明

        # 2. UIレイアウト
        layout = QVBoxLayout(self)
        self.label = QLabel(text)
        self.label.setStyleSheet(f"""
            background-color: rgba(30, 30, 30, 200); 
            color: {color}; 
            border: 1px solid {color};
            border-radius: 10px;
            padding: 12px 20px;
            font-weight: bold;
            font-size: 14px;
        """)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        # 3. 表示位置の計算（メイン画面の中央上部）
        self.adjustSize()
        screen_geometry = QApplication.primaryScreen().geometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = 100  # 画面上部から100pxの位置
        self.move(x, y)

        # 4. アニメーションの設定
        self.fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self.fade_animation.setDuration(400)
        self.fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        # 5. ライフサイクルの開始
        self.show_notification(duration)

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