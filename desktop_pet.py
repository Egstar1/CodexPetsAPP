import sys
import os
import json
import random
import threading
import time
import urllib.request
import ssl
import zipfile
import io as io_module

from PySide6.QtWidgets import (
    QApplication, QWidget, QGraphicsView, QGraphicsScene,
    QGraphicsPixmapItem, QMenu, QDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QCheckBox,
    QLineEdit, QTextEdit, QComboBox, QScrollArea, QFrame,
    QMessageBox, QFileDialog
)
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QSize, QEvent, QPointF
)
from PySide6.QtGui import (
    QPixmap, QImage, QIcon, QAction, QPainter,
    QFont, QColor, QBrush, QPen, QPolygonF
)
from PIL import Image

from pet_db import PetDatabase, INITIAL_MESSAGES


def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CELL_W = 192
CELL_H = 208
COLS = 8
ROWS = 9
DEFAULT_SCALE = 0.5
TICK_MS = 16
CANVAS_W = 260
CANVAS_H = 280

STORE_API = "https://codex-pets.net/api/pets"

STATE_ROW_MAP = {
    "idle": 0, "running-right": 1, "running-left": 2,
    "waving": 3, "jumping": 4, "failed": 5,
    "waiting": 6, "running": 7, "review": 8,
}

ANIM_CONFIG = {
    "idle":           {"fps": 4,  "loop": True,  "frames": 8},
    "running-right":  {"fps": 8,  "loop": True,  "frames": 8},
    "running-left":   {"fps": 8,  "loop": True,  "frames": 8},
    "waving":         {"fps": 10, "loop": False, "frames": 8, "return_to": "idle"},
    "jumping":        {"fps": 12, "loop": False, "frames": 8, "return_to": "idle"},
    "failed":         {"fps": 6,  "loop": False, "frames": 8, "return_to": "idle"},
    "waiting":        {"fps": 3,  "loop": True,  "frames": 8},
    "running":        {"fps": 8,  "loop": True,  "frames": 8},
    "review":         {"fps": 6,  "loop": False, "frames": 8, "return_to": "idle"},
}

RANDOM_ACTIONS = ["waving", "jumping", "review"]

BUBBLE_MAX_W = 170


class PetAssets:
    def __init__(self, folder_path):
        self.folder = folder_path
        self.pet_json = None
        self.spritesheet = None
        self.frames = {}
        self.row_frame_counts = {}
        self._load()

    def _load(self):
        json_path = os.path.join(self.folder, "pet.json")
        webp_path = os.path.join(self.folder, "spritesheet.webp")
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"pet.json not found in {self.folder}")
        if not os.path.exists(webp_path):
            raise FileNotFoundError(f"spritesheet.webp not found in {self.folder}")

        with open(json_path, "r", encoding="utf-8") as f:
            self.pet_json = json.load(f)

        sheet = Image.open(webp_path).convert("RGBA")
        self.spritesheet = sheet
        sw, sh = sheet.size
        cw, ch = sw // COLS, sh // ROWS

        self.frames = {}
        for row in range(ROWS):
            real_count = 0
            for col in range(COLS):
                x, y = col * cw, row * ch
                frame = sheet.crop((x, y, x + cw, y + ch))
                self.frames[(row, col)] = frame
                px = list(frame.getdata())
                non_transparent = sum(1 for p in px if p[3] > 10)
                if non_transparent >= 50:
                    real_count = col + 1
            self.row_frame_counts[row] = max(real_count, 1)

    def get_frame(self, row, col):
        return self.frames.get((row, col))

    @property
    def name(self):
        return self.pet_json.get("displayName") or self.pet_json.get("name", os.path.basename(self.folder))

    @property
    def id(self):
        return self.pet_json.get("id", os.path.basename(self.folder))


def discover_pets():
    base = _get_base_dir()
    pets_dir = os.path.join(base, "pets")
    if not os.path.exists(pets_dir):
        return []
    result = []
    for name in sorted(os.listdir(pets_dir)):
        folder = os.path.join(pets_dir, name)
        if os.path.isdir(folder):
            if os.path.exists(os.path.join(folder, "pet.json")) and \
               os.path.exists(os.path.join(folder, "spritesheet.webp")):
                try:
                    result.append(PetAssets(folder))
                except Exception:
                    pass
    return result


def fetch_gallery(page=1, page_size=30):
    url = f"{STORE_API}?page={page}&pageSize={page_size}"
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, context=ctx, timeout=8)
        data = json.loads(resp.read())
        return data.get("pets", []), data.get("totalPages", 1), data.get("total", 0), None
    except Exception as e:
        return [], 0, 0, str(e)


def download_pet_from_store(pet_id, dest_dir):
    url = f"https://codex-pets.net/api/pets/{pet_id}/download?v={int(time.time())}"
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        data = resp.read()
        zf = zipfile.ZipFile(io_module.BytesIO(data))
        folder = os.path.join(dest_dir, pet_id)
        os.makedirs(folder, exist_ok=True)
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            basename = os.path.basename(name)
            if basename in ("pet.json", "spritesheet.webp"):
                with open(os.path.join(folder, basename), "wb") as f:
                    f.write(zf.read(name))
        zf.close()
        return True
    except Exception:
        return False


class AnimController:
    def __init__(self, speed_multiplier=1.0, assets=None):
        self.state = "idle"
        self.tick_count = 0
        self.speed = speed_multiplier
        self.paused = False
        self.assets = assets

    def set_assets(self, assets):
        self.assets = assets

    def get_config(self, state=None):
        base = ANIM_CONFIG.get(state or self.state, ANIM_CONFIG["idle"])
        cfg = dict(base)
        if self.assets:
            s = state or self.state
            row = STATE_ROW_MAP.get(s, 0)
            real_frames = self.assets.row_frame_counts.get(row)
            if real_frames:
                cfg["frames"] = real_frames
        return cfg

    def set_speed(self, multiplier):
        self.speed = multiplier

    def set_state(self, new_state):
        if new_state == self.state:
            return False
        self.state = new_state
        self.tick_count = 0
        self._last_abs = 0
        return True

    def update(self):
        if self.paused:
            return False
        cfg = self.get_config()
        self.tick_count += 1
        total = cfg["frames"]
        efps = cfg["fps"] * self.speed
        tpf = max(1, round(1000.0 / efps / TICK_MS))
        af = self.tick_count // tpf
        prev = getattr(self, "_last_abs", -1)
        if af == prev:
            return False
        self._last_abs = af
        if not cfg["loop"] and af >= total:
            self.set_state(cfg.get("return_to", "idle"))
            return True
        return True

    def get_display_frame(self):
        cfg = self.get_config()
        total = cfg["frames"]
        af = getattr(self, "_last_abs", 0)
        return af % total if cfg["loop"] else min(af, total - 1)

    def is_idle(self):
        cfg = self.get_config()
        return cfg["loop"] and self.state == "idle"


class BubbleWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self._text = ""
        self._timer = None
        self._visible = False
        self.setFixedSize(CANVAS_W, 160)
        self.hide()

    def show_bubble(self, text, duration=8000):
        self._text = text
        self._visible = True
        self.show()
        self.raise_()
        self.update()
        if self._timer:
            self._timer.stop()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_bubble)
        self._timer.start(duration)

    def hide_bubble(self):
        self._text = ""
        self._visible = False
        self.hide()
        self.update()

    def paintEvent(self, event):
        if not self._text or not self._visible:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        font = QFont("Microsoft YaHei", 9)
        p.setFont(font)
        fm = p.fontMetrics()

        pad_x, pad_y = 14, 8
        max_allowed = min(BUBBLE_MAX_W, self.width() - pad_x * 2 - 10)
        words = self._text.replace("\n", " \n ").split(" ")
        wrapped = []
        current = ""
        for w in words:
            if w == "\n":
                if current:
                    wrapped.append(current.strip())
                    current = ""
                continue
            test = (current + " " + w).strip()
            if fm.horizontalAdvance(test) > max_allowed:
                if current:
                    wrapped.append(current.strip())
                current = w
            else:
                current = test
        if current.strip():
            wrapped.append(current.strip())
        wrapped = wrapped[-5:]

        line_h = fm.height() + 4
        max_line_w = max(fm.horizontalAdvance(line) for line in wrapped) if wrapped else 0
        bw = min(max(max_line_w + pad_x * 2, 60), max_allowed + pad_x * 2)
        bh = len(wrapped) * line_h + pad_y * 2
        cx = self.width() // 2

        bubble_top = 65 - bh
        if bubble_top < 2:
            bubble_top = 2
        bx1 = cx - bw // 2
        r = 8

        if bx1 < 5:
            bx1 = 5
        if bx1 + bw > self.width() - 5:
            bx1 = self.width() - 5 - bw

        p.setPen(QPen(QColor("#888888"), 1))
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawRoundedRect(bx1, bubble_top, bw, bh, r, r)

        tail = [cx - 6, bubble_top + bh, cx + 6, bubble_top + bh, cx, bubble_top + bh + 8]
        path = self._create_triangle(tail)
        p.drawPolygon(path)

        p.setPen(QColor("#333333"))
        text_x = bx1 + pad_x
        text_y = bubble_top + pad_y + fm.ascent()
        for line in wrapped:
            p.drawText(text_x, text_y, line)
            text_y += line_h
        p.end()

    def _create_triangle(self, pts):
        poly = QPolygonF()
        for i in range(0, len(pts), 2):
            poly.append(QPointF(pts[i], pts[i + 1]))
        return poly


class SetupWizard(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._hue = 0
        self._drag_pos = QPoint()
        self.setWindowTitle("")
        self.setFixedSize(540, 480)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self._setup_ui()
        self._start_bg_anim()

    def _start_bg_anim(self):
        self._bg_timer = QTimer(self)
        self._bg_timer.timeout.connect(self._tick_bg)
        self._bg_timer.start(50)

    def _tick_bg(self):
        self._hue = (self._hue + 0.3) % 360

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        r, g, b = self._hsl_to_rgb(self._hue, 65, 88)
        base = QColor(int(r), int(g), int(b))

        p.fillRect(self.rect(), base)

        s = 40
        p.setPen(QPen(QColor(255, 255, 255, 8), 1))
        for y in range(-s, self.height() + s, s):
            for x in range(-s, self.width() + s, s):
                path = QPolygonF()
                path.append(QPointF(x, y + s // 2))
                path.append(QPointF(x + s // 2, y))
                path.append(QPointF(x + s, y + s // 2))
                path.append(QPointF(x + s // 2, y + s))
                path.append(QPointF(x, y + s // 2))
                p.drawPolygon(path)

        p.fillRect(self.rect(), QColor(255, 255, 255, 180))
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _hsl_to_rgb(self, h, s, l):
        s /= 100.0; l /= 100.0
        c = (1 - abs(2 * l - 1)) * s
        x = c * (1 - abs((h / 60) % 2 - 1))
        m = l - c / 2
        if h < 60: r, g, b = c, x, 0
        elif h < 120: r, g, b = x, c, 0
        elif h < 180: r, g, b = 0, c, x
        elif h < 240: r, g, b = 0, x, c
        elif h < 300: r, g, b = x, 0, c
        else: r, g, b = c, 0, x
        return (r + m) * 255, (g + m) * 255, (b + m) * 255

    def showEvent(self, event):
        super().showEvent(event)
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            self.move((sg.width() - self.width()) // 2 + sg.x(),
                      (sg.height() - self.height()) // 2 + sg.y())

    def _setup_ui(self):
        card = QFrame(self)
        card.setObjectName("wizardCard")
        card.setStyleSheet("""
            QFrame#wizardCard {
                background: rgba(255,255,255,0.85);
                border-radius: 18px;
            }
            QLabel { color: #444; }
            QLineEdit {
                border: 2px solid #ddd; border-radius: 8px;
                padding: 8px 12px; font-size: 13px; background: white;
            }
            QLineEdit:focus {
                border-color: #4A7AFF;
            }
            QLineEdit::placeholder { color: #bbb; }
            QCheckBox { spacing: 6px; font-size: 12px; color: #555; padding: 3px 2px; }
            QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px;
                                  border: 2px solid #ccc; background: white; }
            QCheckBox::indicator:hover { border-color: #4A7AFF; }
            QCheckBox::indicator:checked { background: #4A7AFF; border-color: #4A7AFF; }
            QPushButton {
                background: #4A7AFF; color: white; border: none;
                border-radius: 8px; padding: 10px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background: #3B6BE8; }
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(24, 6, 24, 20)
        cl.setSpacing(8)

        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(0, 0, 0, 0)
        title_bar.addStretch()
        for text, action in [("─", self.showMinimized), ("✕", self.close)]:
            btn = QPushButton(text)
            btn.setFixedSize(28, 24)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: #aaa;
                             border: none; border-radius: 4px; font-size: 14px; padding: 0; }}
                QPushButton:hover {{ background: {"#E53935" if text == "✕" else "#eee"};
                                    color: {"white" if text == "✕" else "#333"}; }}
            """)
            btn.clicked.connect(action)
            title_bar.addWidget(btn)
        cl.addLayout(title_bar)

        title = QLabel("🐾  初次见面~")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #333; background: transparent;")
        title.setAlignment(Qt.AlignCenter)
        cl.addWidget(title)

        sub = QLabel("我是你的桌面小伙伴，先认识一下吧！")
        sub.setStyleSheet("font-size: 12px; color: #999; background: transparent;")
        sub.setAlignment(Qt.AlignCenter)
        cl.addWidget(sub)

        name_label = QLabel("💬  你希望我怎么称呼你？")
        name_label.setStyleSheet("font-size: 12px; color: #888; margin-top: 4px; background: transparent;")
        cl.addWidget(name_label)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("输入你的名字或昵称...")
        self.name_input.setMaxLength(20)
        cl.addWidget(self.name_input)

        topic_label = QLabel("🎯  选择感兴趣的话题：")
        topic_label.setStyleSheet("font-size: 12px; color: #888; margin-top: 4px; background: transparent;")
        cl.addWidget(topic_label)

        topic_frame = QFrame()
        topic_frame.setStyleSheet("background: rgba(245,245,245,0.6); border-radius: 10px;")
        tf_layout = QVBoxLayout(topic_frame)
        tf_layout.setContentsMargins(12, 10, 12, 10)
        tf_layout.setSpacing(4)

        self.topic_checks = {}
        all_topics = self.db.get_topics()
        for i in range(0, len(all_topics), 3):
            row = QHBoxLayout()
            row.setSpacing(4)
            for j in range(3):
                idx = i + j
                if idx >= len(all_topics):
                    row.addStretch()
                    break
                t = all_topics[idx]
                cb = QCheckBox(t["display_name"])
                cb.setChecked(bool(t["enabled"]))
                cb.setCursor(Qt.PointingHandCursor)
                self.topic_checks[t["name"]] = cb
                row.addWidget(cb, 1)
            tf_layout.addLayout(row)
        cl.addWidget(topic_frame)

        self.finish_btn = QPushButton("✨  开始！")
        self.finish_btn.setCursor(Qt.PointingHandCursor)
        self.finish_btn.clicked.connect(self._finish)
        self.finish_btn.setStyleSheet("""
            QPushButton { background: #4A7AFF; color: white; border: none;
                         border-radius: 8px; padding: 12px;
                         font-size: 15px; font-weight: bold; }
            QPushButton:hover { background: #3B6BE8; }
        """)
        cl.addWidget(self.finish_btn)

        main = QVBoxLayout(self)
        main.setContentsMargins(25, 25, 25, 25)
        main.addWidget(card)

    def _finish(self):
        name = self.name_input.text().strip()
        if not name:
            self.name_input.setStyleSheet("""
                border: 2px solid #E53935;
                border-radius: 8px; padding: 8px 12px;
                font-size: 13px; background: #fff5f5;
            """)
            self.name_input.setPlaceholderText("请告诉我你的名字~")
            return
        self.db.set_user_name(name)
        enabled = [n for n, cb in self.topic_checks.items() if cb.isChecked()]
        self.db.set_topics_enabled(enabled)
        self.accept()


class MessageEditor(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("📝 管理对话内容")
        self.setFixedSize(500, 400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.topic_combo = QComboBox()
        topics = self.db.get_topics()
        for t in topics:
            self.topic_combo.addItem(t["display_name"], t["id"])
        layout.addWidget(QLabel("选择分类："))
        layout.addWidget(self.topic_combo)

        self.period_combo = QComboBox()
        for p in ["general", "morning", "forenoon", "pre_lunch", "lunch", "afternoon", "evening", "night"]:
            self.period_combo.addItem(p)
        layout.addWidget(QLabel("时段："))
        layout.addWidget(self.period_combo)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("输入新的对话内容...")
        self.text_edit.setMaximumHeight(100)
        layout.addWidget(QLabel("对话内容："))
        layout.addWidget(self.text_edit)

        btn = QPushButton("➕ 添加")
        btn.clicked.connect(self._add)
        layout.addWidget(btn)

        layout.addWidget(QLabel("已保存的对话："))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        scroll.setWidget(self.list_widget)
        layout.addWidget(scroll)

        self._refresh()

    def _refresh(self):
        for i in reversed(range(self.list_layout.count())):
            self.list_layout.itemAt(i).widget().deleteLater()
        msgs = self.db.conn.execute(
            "SELECT content, source FROM messages WHERE source='user' ORDER BY id DESC LIMIT 20"
        ).fetchall()
        for row in msgs:
            lbl = QLabel(f"💬 {row['content'][:50]}")
            lbl.setStyleSheet("color: #666; padding: 4px;")
            self.list_layout.addWidget(lbl)
        self.list_layout.addStretch()

    def _add(self):
        content = self.text_edit.toPlainText().strip()
        if not content:
            return
        topic_id = self.topic_combo.currentData()
        period = self.period_combo.currentText()
        self.db.add_message(topic_id, content, period)
        self.text_edit.clear()
        self._refresh()


class PetWindow(QWidget):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self._chrome_done = False
        self._pos_set = False
        self.setWindowTitle("")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self.setFixedSize(CANVAS_W, CANVAS_H)

        self.scene = QGraphicsScene(self)
        self.scene.setBackgroundBrush(QBrush(QColor(0, 0, 0, 0)))
        self.view = QGraphicsView(self.scene, self)
        self.view.setGeometry(0, 0, CANVAS_W, CANVAS_H)
        self.view.setStyleSheet("background: transparent; border: none;")
        self.view.setAttribute(Qt.WA_TranslucentBackground)
        self.view.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.pet_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pet_item)

        self.bubble = BubbleWidget(self)
        self.bubble.setGeometry(0, 0, CANVAS_W, 160)

        self.scale = DEFAULT_SCALE
        self.anim = AnimController()
        self.photo_cache = {}
        self.pets = discover_pets()
        self.current_pet_index = 0
        self.assets = None
        self._drag_pos = QPoint()
        self._was_dragging = False
        self._click_count = 0
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._single_click)

        if self.pets:
            self._load_pet(0)

        self._random_timer = QTimer(self)
        self._random_timer.timeout.connect(self._do_random)
        self._schedule_random()

        self._msg_timer = QTimer(self)
        self._msg_timer.timeout.connect(self._show_message)
        self._schedule_message()

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(TICK_MS)

    def _remove_window_chrome(self):
        try:
            import ctypes
            hwnd = int(self.winId())
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_DONOTROUND = 1
            DWMWA_TRANSITIONS_FORCEDISABLED = 3
            dwm = ctypes.windll.dwmapi

            val = ctypes.c_int(DWMWCP_DONOTROUND)
            dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(val), ctypes.sizeof(val)
            )

            disabled = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(
                hwnd, DWMWA_TRANSITIONS_FORCEDISABLED,
                ctypes.byref(disabled), ctypes.sizeof(disabled)
            )

            GWL_STYLE = -16
            WS_POPUP = 0x80000000
            WS_VISIBLE = 0x10000000
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            style = style | WS_POPUP | WS_VISIBLE
            style = style & ~0x00CF0000
            user32.SetWindowLongW(hwnd, GWL_STYLE, style)

            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TOOLWINDOW = 0x00000080
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex_style |= WS_EX_LAYERED
            ex_style &= ~WS_EX_TOOLWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)

            margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))

            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0004 | 0x0020 | 0x0001)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        if not self._chrome_done:
            self._chrome_done = True
            self._remove_window_chrome()
        if not getattr(self, '_pos_set', False):
            self._pos_set = True
            self._move_to_bottom_right()

    def _move_to_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            margin_x = 100
            margin_y = 50
            fw = int(CANVAS_W * self.scale)
            fh = int(CANVAS_H * self.scale)
            self.move(sg.right() - fw - margin_x, sg.bottom() - fh - margin_y)

    def _load_pet(self, index):
        if not self.pets or index < 0 or index >= len(self.pets):
            return
        self.current_pet_index = index
        self.assets = self.pets[index]
        self.anim.set_assets(self.assets)
        self.photo_cache = {}
        self.anim.set_state("idle")
        threading.Thread(target=self._pre_cache, daemon=True).start()

    def _pre_cache(self):
        if not self.assets:
            return
        for state, row in STATE_ROW_MAP.items():
            ac = ANIM_CONFIG.get(state, ANIM_CONFIG["idle"])
            mf = min(ac["frames"], self.assets.row_frame_counts.get(row, ac["frames"]))
            for col in range(mf):
                self._get_pixmap(row, col)

    def _get_pixmap(self, row, col):
        if not self.assets:
            return None
        key = (row, col, self.scale)
        if key in self.photo_cache:
            return self.photo_cache[key]
        frame = self.assets.get_frame(row, col)
        if frame is None:
            return None
        nw = int(CELL_W * self.scale)
        nh = int(CELL_H * self.scale)
        if self.scale != 1.0:
            frame = frame.resize((nw, nh), Image.NEAREST)
        data = frame.tobytes()
        qimg = QImage(data, nw, nh, QImage.Format_RGBA8888)
        pm = QPixmap.fromImage(qimg)
        self.photo_cache[key] = pm
        return pm

    def _render(self):
        if self.assets is None:
            return
        state = self.anim.state
        cfg = self.anim.get_config(state)
        row = STATE_ROW_MAP.get(state, 0)
        col = self.anim.get_display_frame()
        col = min(col, cfg["frames"] - 1)

        pm = self._get_pixmap(row, col)
        if pm is None:
            pm = self._get_pixmap(row, 0)
        if pm is None:
            pm = self._get_pixmap(STATE_ROW_MAP["idle"], 0)
        if pm is None:
            return

        fw = int(CELL_W * self.scale)
        fh = int(CELL_H * self.scale)
        x = (CANVAS_W - fw) // 2
        y = (CANVAS_H - fh) // 2 + 10
        self.pet_item.setPixmap(pm)
        self.pet_item.setPos(x, y)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._was_dragging = False
            self._click_count += 1
            if self._click_count == 1:
                self._click_timer.start(300)
            else:
                self._click_timer.stop()
                self._click_count = 0
                self._double_click()
        elif event.button() == Qt.RightButton:
            self._context_menu(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            dx = new_pos.x() - self.x()
            if abs(dx) > 3 or abs(new_pos.y() - self.y()) > 3:
                self._was_dragging = True
            self.move(new_pos)

    def _single_click(self):
        self._click_count = 0
        if not self._was_dragging and self.anim.is_idle():
            self.anim.set_state(random.choice(RANDOM_ACTIONS))
            self._schedule_random()

    def _double_click(self):
        if not self._was_dragging:
            self.anim.paused = not self.anim.paused

    def _context_menu(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: white; border: 1px solid #ddd; border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background: #E8E8E8; }
        """)

        states_sub = menu.addMenu("🎬 切换动作")
        for sid, label in [
            ("idle", "😴 待机"), ("waving", "👋 挥手"), ("jumping", "⬆️ 跳跃"),
            ("running-right", "🏃 向右跑"), ("running-left", "🏃 向左跑"),
            ("running", "💨 奔跑"), ("waiting", "⏳ 等待"),
            ("review", "🔍 审查"), ("failed", "❌ 失败"),
        ]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked, s=sid: self._play(s))
            states_sub.addAction(act)

        if len(self.pets) > 1:
            pet_sub = menu.addMenu("🐾 切换宠物")
            for i, pet in enumerate(self.pets):
                act = QAction(pet.name, self)
                if i == self.current_pet_index:
                    act.setIcon(QIcon())
                act.triggered.connect(lambda checked, idx=i: self._switch_pet(idx))
                pet_sub.addAction(act)

        scale_sub = menu.addMenu("🔍 缩放")
        for s in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
            act = QAction(f"{'✓ ' if abs(self.scale - s) < 0.01 else '  '}{s:.0%}", self)
            act.triggered.connect(lambda checked, sc=s: self._set_scale(sc))
            scale_sub.addAction(act)

        speed_sub = menu.addMenu("⚡ 速度")
        labels = {0.25: "极慢", 0.5: "慢速", 1.0: "正常", 1.5: "稍快", 2.0: "快速", 3.0: "极速"}
        for sp in [0.25, 0.5, 1.0, 1.5, 2.0, 3.0]:
            act = QAction(f"{'✓ ' if abs(self.anim.speed - sp) < 0.01 else '  '}{labels[sp]} {sp}×", self)
            act.triggered.connect(lambda checked, s=sp: self.anim.set_speed(s))
            speed_sub.addAction(act)

        menu.addAction("🧩 桌宠拓展", self._open_store)
        menu.addAction("📝 管理对话", self._open_message_editor)
        menu.addSeparator()
        menu.addAction("📋 选择Log文件", self._select_log)
        menu.addAction("🔄 重置位置", self._reset_pos)
        pause_text = "▶️ 恢复" if self.anim.paused else "⏸️ 暂停"
        menu.addAction(pause_text, self._toggle_pause)
        menu.addSeparator()
        menu.addAction("❌ 退出", QApplication.instance().quit)
        menu.exec(event.globalPosition().toPoint())

    def _play(self, state):
        if self._random_timer.isActive():
            self._random_timer.stop()
        self.anim.set_state(state)
        self._schedule_random()

    def _switch_pet(self, index):
        if index == self.current_pet_index:
            return
        if self._random_timer.isActive():
            self._random_timer.stop()
        self._load_pet(index)
        self._schedule_random()

    def _set_scale(self, sc):
        if abs(self.scale - sc) < 0.01:
            return
        self.scale = sc
        self.photo_cache = {}

    def _toggle_pause(self):
        self.anim.paused = not self.anim.paused

    def _reset_pos(self):
        self._move_to_bottom_right()

    def _select_log(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择Log文件", "", "Log文件 (*.log *.txt *.out);;所有文件 (*.*)")
        if path:
            self._log_path = path
            self._last_log_size = os.path.getsize(path) if os.path.exists(path) else 0
            self._last_log_pos = self._last_log_size
            threading.Thread(target=self._log_loop, daemon=True).start()

    def _log_loop(self):
        while True:
            if hasattr(self, "_log_path") and self._log_path and os.path.exists(self._log_path):
                try:
                    sz = os.path.getsize(self._log_path)
                    if sz > self._last_log_size:
                        with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(self._last_log_pos)
                            nc = f.read()
                            self._last_log_pos = f.tell()
                            self._last_log_size = sz
                            if nc.strip():
                                self._show_bubble_text(nc.strip())
                    elif sz < self._last_log_size:
                        self._last_log_size = 0
                        self._last_log_pos = 0
                except (OSError, PermissionError):
                    pass
            time.sleep(1)

    def _show_bubble_text(self, text):
        lines = text.split("\n")
        dl = []
        for line in lines:
            line = line.strip()
            if line:
                while len(line) > 30:
                    dl.append(line[:30])
                    line = line[30:]
                if line:
                    dl.append(line)
        dt = "\n".join(dl[-5:])
        if len(dt) > 150:
            dt = dt[:147] + "..."
        self.bubble.show_bubble(dt)

    def _schedule_random(self):
        delay = random.randint(15000, 45000)
        self._random_timer.start(delay)

    def _do_random(self):
        if self.anim.is_idle():
            self.anim.set_state(random.choice(RANDOM_ACTIONS))
        self._schedule_random()

    def _schedule_message(self):
        delay = random.randint(10000, 180000)
        self._msg_timer.start(delay)

    def _show_message(self):
        msgs = self.db.get_messages_for_period(self._get_period())
        if msgs:
            msg = random.choice(msgs)["content"]
            self.bubble.show_bubble(msg)
        self._schedule_message()

    def _get_period(self):
        h = time.localtime().tm_hour
        if 5 <= h < 8: return "morning"
        if 8 <= h < 10: return "forenoon"
        if 10 <= h < 12: return "pre_lunch"
        if 12 <= h < 13: return "lunch"
        if 13 <= h < 17: return "afternoon"
        if 17 <= h < 19: return "evening"
        return "night"

    def _open_store(self):
        dialog = StoreDialog(self.db, self)
        dialog.exec()

    def _open_message_editor(self):
        dialog = MessageEditor(self.db, self)
        dialog.exec()

    def _tick(self):
        self.anim.update()
        self._render()


class StoreDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("🧩 桌宠拓展")
        self.setFixedSize(750, 600)
        self._page = 1
        self._total_pages = 1
        self._all_pets_data = []
        self.setStyleSheet("""
            QDialog { background: #f0f2f5; }
        """)
        self._setup_ui()

    def _setup_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(12, 12, 12, 12)
        main.setSpacing(8)

        header = QLabel("🧩 桌宠拓展")
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: #333; padding: 4px;")
        header.setAlignment(Qt.AlignCenter)
        main.addWidget(header)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索宠物名称...")
        self.search_input.setStyleSheet("""
            QLineEdit { border: 2px solid #ddd; border-radius: 8px;
                       padding: 8px 12px; font-size: 13px; background: white; }
            QLineEdit:focus { border-color: #4A7AFF; }
        """)
        self.search_input.textChanged.connect(self._on_search)
        search_row.addWidget(self.search_input)

        self.status = QLabel("正在加载...")
        self.status.setStyleSheet("color: #888; font-size: 12px;")
        self.status.setFixedWidth(200)
        search_row.addWidget(self.status)
        main.addLayout(search_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 6px; background: #eee; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #ccc; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #aaa; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self.grid_widget = QWidget()
        self.grid_widget.setStyleSheet("background: transparent;")
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(8)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.grid_widget)
        main.addWidget(scroll, 1)

        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.page_label = QLabel("")
        self.page_label.setStyleSheet("color: #888; font-size: 12px;")
        nav.addWidget(self.page_label)
        nav.addStretch()
        for text, delta in [("◀ 上一页", -1), ("下一页 ▶", 1)]:
            btn = QPushButton(text)
            btn.setStyleSheet("""
                QPushButton { background: #4A7AFF; color: white; border: none;
                            padding: 6px 18px; border-radius: 6px; font-size: 12px; }
                QPushButton:hover { background: #3B6BE8; }
                QPushButton:disabled { background: #ccc; }
            """)
            btn.clicked.connect(lambda checked, d=delta: self._load_page(d))
            nav.addWidget(btn)
            setattr(self, "_nav_" + ("prev" if delta < 0 else "next"), btn)
        main.addLayout(nav)

        self._load_page(0)

    def _on_search(self, text):
        self._filter_display()

    def _filter_display(self):
        query = self.search_input.text().strip().lower()
        for i in reversed(range(self.grid_layout.count())):
            item = self.grid_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()

        filtered = []
        if query:
            for pet in self._all_pets_data:
                name = (pet.get("displayName") or pet.get("id", "")).lower()
                if query in name:
                    filtered.append(pet)
        else:
            filtered = self._all_pets_data

        if not filtered:
            lbl = QLabel("没有找到匹配的桌宠" if query else "暂无数据")
            lbl.setStyleSheet("color: #aaa; font-size: 13px; padding: 40px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.grid_layout.addWidget(lbl, 0, 0, 1, 4)
            return

        base = os.path.join(_get_base_dir(), "pets")
        for i, pet in enumerate(filtered):
            card = self._make_card(pet, base)
            self.grid_layout.addWidget(card, i // 4, i % 4)

    def _make_card(self, pet, base_dir):
        pid = pet.get("id", "")
        name = pet.get("displayName") or pid
        owner = pet.get("ownerName") or pet.get("ownerHandle") or pet.get("ownerId", "")
        installed = os.path.exists(os.path.join(base_dir, pid, "pet.json"))

        card = QFrame()
        card.setStyleSheet("""
            QFrame { background: white; border-radius: 10px; padding: 8px; }
            QFrame:hover { background: #f8f9ff; }
        """)
        card.setFixedSize(170, 210)

        cl = QVBoxLayout(card)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(3)

        preview = QLabel()
        preview.setFixedSize(110, 120)
        preview.setAlignment(Qt.AlignCenter)
        preview.setStyleSheet("background: #f5f5f5; border-radius: 6px;")
        cl.addWidget(preview, alignment=Qt.AlignCenter)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("font-weight: bold; font-size: 12px; color: #333;")
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setWordWrap(True)
        cl.addWidget(name_lbl)

        if owner:
            owner_lbl = QLabel(f"👤 {owner}")
            owner_lbl.setStyleSheet("color: #888; font-size: 10px;")
            owner_lbl.setAlignment(Qt.AlignCenter)
            owner_lbl.setWordWrap(True)
            cl.addWidget(owner_lbl)

        if installed:
            btn = QLabel("✅ 已安装")
            btn.setStyleSheet("color: #28a745; font-size: 11px; padding: 2px;")
            btn.setAlignment(Qt.AlignCenter)
            cl.addWidget(btn)
        else:
            btn = QPushButton("⬇ 下载")
            btn.setStyleSheet("""
                QPushButton { background: #4A7AFF; color: white; border: none;
                            padding: 5px; border-radius: 5px; font-size: 11px; }
                QPushButton:hover { background: #3B6BE8; }
                QPushButton:disabled { background: #ccc; }
            """)
            btn.clicked.connect(lambda checked, p=pid, n=name, b=btn: self._download(p, n, base_dir, b))
            cl.addWidget(btn)

        preview_url = pet.get("spritesheetUrl") or pet.get("previewUrl") or pet.get("posterUrl", "")
        if preview_url:
            def load(url=preview_url, lbl=preview):
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    resp = urllib.request.urlopen(req, context=ctx, timeout=5)
                    data = resp.read()
                    img = Image.open(io_module.BytesIO(data)).convert("RGBA")
                    if img.width > 500:
                        cw = min(192, img.width)
                        ch = min(208, img.height)
                        frame = img.crop((0, 0, cw, ch))
                    else:
                        frame = img
                    frame = frame.resize((110, 120), Image.NEAREST)
                    qimg = QImage(frame.tobytes(), 110, 120, QImage.Format_RGBA8888)
                    pm = QPixmap.fromImage(qimg)
                    lbl.setPixmap(pm)
                    lbl.setFixedSize(110, 120)
                except Exception:
                    pass
            threading.Thread(target=load, daemon=True).start()

        return card

    def _load_page(self, delta):
        self._page = max(1, min(self._page + delta, self._total_pages))
        self.status.setText(f"加载第 {self._page} 页...")
        for attr in ["_nav_prev", "_nav_next"]:
            btn = getattr(self, attr, None)
            if btn:
                btn.setEnabled(False)

        def fetch():
            data, total_pages, total, err = fetch_gallery(self._page, 40)
            self._total_pages = total_pages

            def update():
                for attr in ["_nav_prev", "_nav_next"]:
                    btn = getattr(self, attr, None)
                    if btn:
                        btn.setEnabled(True)
                if err:
                    self.status.setText(f"❌ 加载失败: {err[:40]}")
                    return
                self._all_pets_data = data
                self.status.setText(f"共 {total} 个 | 第 {self._page}/{total_pages} 页")
                self.page_label.setText(f"第 {self._page}/{total_pages} 页")
                self._filter_display()

            QApplication.instance().postEvent(self, _UIEvent(update))

        threading.Thread(target=fetch, daemon=True).start()

    def _download(self, pid, name, base_dir, btn):
        btn.setText("⏳...")
        btn.setEnabled(False)
        parent_layout = btn.parentWidget().layout() if btn.parentWidget() else None

        def do():
            ok = download_pet_from_store(pid, base_dir)

            def done():
                if ok:
                    btn.deleteLater()
                    lbl = QLabel("✅ 已安装")
                    lbl.setStyleSheet("color: #28a745; font-size: 11px; padding: 2px;")
                    lbl.setAlignment(Qt.AlignCenter)
                    if parent_layout:
                        parent_layout.addWidget(lbl)
                    QMessageBox.information(self, "下载完成", f"「{name}」已下载！\n重启程序后可用。")
                else:
                    btn.setText("⬇ 重试")
                    btn.setEnabled(True)

            QApplication.instance().postEvent(self, _UIEvent(done))

        threading.Thread(target=do, daemon=True).start()

    def customEvent(self, event):
        if isinstance(event, _UIEvent):
            event.fn()


class _UIEvent(QEvent):
    def __init__(self, fn):
        super().__init__(QEvent.Type(QEvent.User + 1))
        self.fn = fn


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("桌面宠物")

    db = PetDatabase()
    first = db.is_first_launch()

    if first:
        wizard = SetupWizard(db)
        if wizard.exec() == QDialog.Accepted:
            db.set_setting("setup_complete", "1")
            db.increment_launch()
            name = db.get_user_name()
            msg = f"{name}你好~，很高兴见到你！"
            pet = PetWindow(db)
            pet.show()
            pet.bubble.show_bubble(msg, 5000)
        else:
            sys.exit(0)
    else:
        db.increment_launch()
        pet = PetWindow(db)
        pet.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
