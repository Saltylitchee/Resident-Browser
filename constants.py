import os
from enum import Enum, auto

# ==============================================================================
# 1. システム・パス設定
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PROFILE_DIR = os.path.join(BASE_DIR, "portal_profile")
SELECTORS_FILE = os.path.join(BASE_DIR, "selectors.json")

# ==============================================================================
# 2. アプリケーション基本情報・列挙型
# ==============================================================================
DEFAULT_APP_NAME = "Doppel"

class DisplayMode(Enum):
    EXPANDED = auto()   # 小窓
    COLLAPSED = auto()  # インジケーター
    HIDDEN = auto()     # 潜伏

# ==============================================================================
# 3. 表示・レイアウト・数値デフォルト (DF_...)
# ==============================================================================
DF_LAYOUT_THRESHOLD = 600
DF_ZOOM_DESKTOP = 0.8
DF_ZOOM_MOBILE = 1.0
DF_MAX_TITLE_LEN = 25
DF_INDICATOR_MARGIN = 15
DF_BASE_WIDTH = 400
DF_ALPHA = 220

# インジケーター計算用マジックナンバー
INDICATOR_FONT_BASE_SIZE = 10
INDICATOR_CIRCLE_BASE_SIZE = 50
INDICATOR_ICON_BASE_WIDTH = 30
INDICATOR_MIN_WIDTH_BASE = 80
INDICATOR_MARGINS_BASE = (12, 5, 15, 5) # (Left, Top, Right, Bottom)
INDICATOR_SPACING_BASE = 8

# ==============================================================================
# 4. カラー定義
# ==============================================================================
COLOR_ACCENT = "#00FF7F"    # 通知用
COLOR_INDICATOR = "#00FF00" # インジケーター用
COLOR_BG_DARK = "#000000"
COLOR_BG_NOTIF = "#3A01C1"
COLOR_SUB_BUTTON_DEFAULT = "#333333"
COLOR_SUB_BUTTON_FAVORITED = "#4285f4"

# ==============================================================================
# 5. 通知・インジケーター・UI詳細設定
# ==============================================================================
NOTIF_OPACITY_START = 0.0
NOTIF_OPACITY_END = 1.0

DEFAULT_NOTIF_SETTINGS = {
    "color": COLOR_ACCENT,
    "bg_color": COLOR_BG_NOTIF,
    "bg_alpha": DF_ALPHA,
    "duration": 2000,
    "fade_duration": 300,
    "font_size": "13px",
    "font_family": "'Segoe UI', Arial",
    "border_radius": "8px",
    "padding": "10px 20px",
    "pos_y": 80
}

DEFAULT_INDICATOR_SETTINGS = {
    "text_color": COLOR_INDICATOR,
    "bg_color": COLOR_BG_DARK,
    "bg_alpha": DF_ALPHA,
    "shape": "rounded_rect",
    "pen_width": 2,
    "corner_radius": 15,
    "icon_boundary_width": 30
}

DEFAULT_INDICATOR_ICONS = {
    "playing": "♪",
    "paused": "||",
    "stopped": "❏"
}

INDICATOR_WINDOW_FLAGS = ["FramelessWindowHint", "WindowStaysOnTopHint", "Tool"]

# ==============================================================================
# 6. 検索・ナビゲーション・ブラウザ設定
# ==============================================================================
DEFAULT_UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SEARCH_ENGINE_URL = "https://www.google.com/search?q={}"
HTTPS_PREFIX = "https://"
URL_SCHEMES = ('http://', 'https://')
URL_BLANK = "about:blank"
INVALID_URLS = (URL_BLANK, "", "None")

VIEWPORT_DESKTOP = "width=1280, initial-scale=1.0"
VIEWPORT_MOBILE = "width=device-width, initial-scale=1.0"

SEARCH_BAR_HEIGHT = 40
SEARCH_TOGGLE_SIZE = (30, 24)
SEARCH_BTN_SIZE = (24, 24)
SEARCH_SUB_SPACING = 2
SEARCH_SUB_BUTTON_SIZE = (24, 24)
FIND_DEFAULT_COUNT = "0/0"

SEARCH_MODES = {
    "google": {
        "label": "[G]",
        "bg_color": "#4285f4",
        "placeholder": "Google Search...",
        "accent_color": "white"
    },
    "find": {
        "label": "[F]",
        "bg_color": "#ff9800",
        "placeholder": "Find in page...",
        "accent_color": "white"
    }
}

EXTERNAL_BROWSER_KEYWORDS = ["youtube.com/live/", "youtu.be/live/"]
TARGET_BROWSER_KEYWORDS = ("Chrome", "Edge", "Firefox", "Brave")
EXCLUDE_WINDOW_KEYWORDS = (DEFAULT_APP_NAME,)
EXPLORER_CLASS_NAME = "CabinetWClass"

# ==============================================================================
# 7. ショートカット・操作設定
# ==============================================================================
DEFAULT_MODIFIER = "alt"
KEY_HIDE = "w"
KEY_SHOW = "s"
KEY_CYCLE = "d"
KEY_COPY = "c"
KEY_PASTE = "v"

ACTION_HIDE = "action_hide"
ACTION_TOGGLE = "action_toggle_visibility"
ACTION_CYCLE = "action_cycle_geometry"
ACTION_COPY = "action_copy"
ACTION_PASTE = "action_paste"

DEFAULT_KEYS = {
    ACTION_HIDE: KEY_HIDE,
    ACTION_TOGGLE: KEY_SHOW,
    ACTION_CYCLE: KEY_CYCLE,
    ACTION_COPY: KEY_COPY,
    ACTION_PASTE: KEY_PASTE
}

ASCII_PRINTABLE_MIN = 32
ASCII_PRINTABLE_MAX = 126

# --- トラックパッド・スワイプ詳細設定 ---
WHEEL_RELEASE_COOLDOWN = 0.15      # 指を離した直後の不感帯（秒）
WHEEL_MAX_SPEED_LIMIT = 100        # スパイクノイズ除去用の上限
WHEEL_RESET_THRESHOLD = 0.12       # 蓄積をリセットする無操作時間（秒）
WHEEL_MIN_DX_GUARD = 3             # 遊び（これ以下の動きは無視）
WHEEL_AFTER_SWIPE_COOLDOWN = 800   # 連続発火を防ぐクールダウン（ミリ秒）

# --- 実行判定しきい値 ---
WHEEL_SWIPE_MIN_DURATION = 0.15    # 最低スワイプ継続時間（秒）
WHEEL_SWIPE_ACCUM_TARGET = 100     # 必要蓄積距離
WHEEL_MIN_EVENT_COUNT = 45         # 必要イベント数
WHEEL_VELOCITY_THRESHOLD = 1000    # 必要最低速度    # これ以下の微小な動きは「遊び」として捨てる

SWIPE_THRESHOLD_X = 100         # マウスクリック＋ドラッグ時の発火閾値
HOTKEY_DEBOUNCE_SEC = 0.25
HOTKEY_MONITOR_INTERVAL_MS = 100
MAX_NUM_SHORTCUTS = 10
MAX_FAVORITES = 5
ZOOM_MIN_LIMIT = 0.4
ZOOM_MAX_LIMIT = 2.0

# ==============================================================================
# 8. タイミング・遅延・しきい値
# ==============================================================================
INITIAL_LOAD_DELAY_MS = 150
INDICATOR_CLICK_REFRESH_DELAY = 200
DELAY_LOAD_FINISHED_DEFAULT = 200
DELAY_SITE_OPTIMIZE_RETRY = 1000
DELAY_DESKTOP_LAYOUT_RETRY = 1500
OPTIMIZE_PROGRESS_THRESHOLD = 80

# ==============================================================================
# 9. スタイルシート・JavaScript テンプレート
# ==============================================================================
STYLE_SEARCH_CONTAINER = "background: #f0f0f0; border-bottom: 1px solid #ccc;"
STYLE_MODE_BUTTON = "background-color: {bg}; color: {text}; font-weight: bold; border: none; border-radius: 3px;"
STYLE_FIND_BUTTON = "background-color: white; border: 1px solid #ccc; border-radius: 3px;"
STYLE_QMENU = "QMenu { background-color: white; border: 1px solid #999; } QMenu::item { padding: 5px 25px; } QMenu::item:selected { background-color: #3a8fb7; color: white; }"

STYLE_INDICATOR_LABEL = "color: {color}; font-weight: bold; font-size: {size}pt; background: transparent; border: none;"
STYLE_SUB_BUTTON_BASE = """
    QPushButton {{ border: none; background: transparent; color: {color}; font-size: 12pt; border-radius: 4px; {extra_style} }}
    QPushButton:hover {{ color: #FFFFFF; background: rgba(255, 255, 255, 0.1); }}
"""

JS_COPY_SELECTION = "window.getSelection().toString();"
JS_SET_VIEWPORT = "(function() {{ var meta = document.querySelector('meta[name=\"viewport\"]'); if (!meta) {{ meta = document.createElement('meta'); meta.name = \"viewport\"; document.getElementsByTagName('head')[0].appendChild(meta); }} meta.setAttribute('content', '{content}'); window.dispatchEvent(new Event('resize')); }})();"
JS_GET_VIDEO_STATE = "(function() { var videos = Array.from(document.querySelectorAll('video')); var activeVideo = videos.find(v => v.offsetWidth > 0 && v.offsetHeight > 0); if (!activeVideo) return 'none'; return activeVideo.paused ? 'paused' : 'playing'; })();"
JS_TOGGLE_PLAYBACK = "(function() { var videos = Array.from(document.querySelectorAll('video')); var target = videos.sort((a, b) => b.offsetHeight - a.offsetHeight)[0]; if (target) { if (target.paused) target.play(); else target.pause(); return true; } return false; })();"

# ==============================================================================
# 10. メッセージ・テキスト・正規表現
# ==============================================================================
MENU_TEXT_BACK = "戻る"
MENU_TEXT_FORWARD = "進む"
MENU_TEXT_RELOAD = "リロード"
MENU_TEXT_PRESET_SWITCH = "プリセット切替"
MENU_TEXT_SAVE_GEO = "現在のサイズをプリセットに保存"
MENU_TEXT_COLLAPSE = "インジケーター化"

MSG_NO_LOCATIONS = "No size patterns found."
MSG_SIZE_CYCLE = "Size: {current}/{total}"
MSG_GEO_LOCKED_ADDED = "★New Size Pattern Locked & Added!"

RE_TITLE_NOTIF = r'^\(\d+\)\s*'

# ==============================================================================
# 11. 巨大なデフォルト設定オブジェクト (最後に配置して可読性を確保)
# ==============================================================================
DEFAULT_SELECTOR_DATA = {
    "force_desktop": False, "hide_elements": [], "injected_css": "", "action_selectors": {}
}

DEFAULT_CONFIG = {
    "app_settings": {
        "auto_start": False,
        "show_notifications": True,
        "last_active_preset_index": 0,
        "global_indicator_scale": 1.0,
        "indicator_screen_margin": DF_INDICATOR_MARGIN,
        "layout_threshold": DF_LAYOUT_THRESHOLD,
        "desktop_zoom_default": DF_ZOOM_DESKTOP,
        "mobile_zoom_default": DF_ZOOM_MOBILE,
        "search_mode": "google",
        "selectors_url": None,
        "developer_notes": { # 復元
            "reference_url": "https://gemini.google.com/app",
            "last_modified": "2026-05-10"
        },
        "shortcuts": {
            "modifier": DEFAULT_MODIFIER,
            **DEFAULT_KEYS
        }
    },
    "presets": [
        {
            "name": "デフォルト",
            "last_url": "https://youtube.com",
            "favorites": ["https://www.youtube.com/", "https://gemini.google.com/app"],
            "base_width": DF_BASE_WIDTH,
            "indicator_styles": {
                "shape": DEFAULT_INDICATOR_SETTINGS["shape"],
                "max_title_length": DF_MAX_TITLE_LEN,
                "text_color": DEFAULT_INDICATOR_SETTINGS["text_color"],
                "bg_color": DEFAULT_INDICATOR_SETTINGS["bg_color"],
                "indicator_bg_alpha": DEFAULT_INDICATOR_SETTINGS["bg_alpha"],
                "notification_color": DEFAULT_NOTIF_SETTINGS["color"],
                "notif_bg_color": DEFAULT_NOTIF_SETTINGS["bg_color"],
                "notif_bg_alpha": DEFAULT_NOTIF_SETTINGS["bg_alpha"]
            },
            "locations": [{ "x": 100, "y": 100, "width": 400, "height": 300, "opacity": 1.0, "is_locked": True }],
            "last_location_index": 0
        }
    ],
}