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
    QGraphicsPixmapItem, QMenu, QDialog, QSystemTrayIcon,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QCheckBox,
    QLineEdit, QTextEdit, QComboBox, QScrollArea, QFrame,
    QMessageBox, QFileDialog, QStackedWidget
)
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QSize, QEvent, QPointF
)
from PySide6.QtGui import (
    QPixmap, QImage, QIcon, QAction, QPainter,
    QFont, QColor, QBrush, QPen, QPolygonF,
    QPainterPath
)
from PIL import Image

from pet_db import PetDatabase, INITIAL_MESSAGES


def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _get_search_dirs():
    dirs = []
    if getattr(sys, 'frozen', False):
        dirs.append(sys._MEIPASS)
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir != sys._MEIPASS:
            dirs.append(exe_dir)
    else:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    return dirs


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
    seen = set()
    result = []
    for base in _get_search_dirs():
        pets_dir = os.path.join(base, "pets")
        if not os.path.exists(pets_dir) or pets_dir in seen:
            continue
        seen.add(pets_dir)
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
        raw = self._text
        wrapped = []
        for paragraph in raw.split("\n"):
            chars = list(paragraph)
            i = 0
            while i < len(chars):
                j = i + 1
                while j <= len(chars):
                    if fm.horizontalAdvance("".join(chars[i:j])) > max_allowed:
                        break
                    j += 1
                wrapped.append("".join(chars[i:j-1]) if j > i + 1 else chars[i])
                i = j - 1
        wrapped = wrapped[-5:]

        line_h = fm.height() + 4
        max_line_w = max(fm.horizontalAdvance(line) for line in wrapped) if wrapped else 0
        bw = min(max(max_line_w + pad_x * 2, 60), max_allowed + pad_x * 2)
        bh = len(wrapped) * line_h + pad_y * 2 + 4
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

        path = QPainterPath()
        path.addRoundedRect(bx1, bubble_top, bw, bh, r, r)

        tx, ty = cx, bubble_top + bh
        tri = QPolygonF()
        tri.append(QPointF(tx - 6, ty))
        tri.append(QPointF(tx + 6, ty))
        tri.append(QPointF(tx, ty + 8))
        path.addPolygon(tri)

        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.drawPath(path)

        p.setPen(QColor("#dddddd"))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        p.setPen(QColor("#333333"))
        text_x = bx1 + pad_x
        text_y = bubble_top + pad_y + fm.ascent()
        for line in wrapped:
            p.drawText(text_x, text_y, line)
            text_y += line_h
        p.end()

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
            QLineEdit:focus { border-color: #4A7AFF; }
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
                    spacer = QWidget()
                    spacer.setStyleSheet("background: transparent;")
                    row.addWidget(spacer, 1)
                else:
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
        self.setFixedSize(520, 520)
        self.setStyleSheet("""
            MessageEditor {
                background: #f5f6fa;
                border-radius: 12px;
            }
            QComboBox {
                background: white;
                border: 2px solid #e0e3eb;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                color: #2d3436;
                min-width: 100px;
                max-height: 34px;
            }
            QComboBox:hover {
                border-color: #6c5ce7;
            }
            QComboBox:focus {
                border-color: #6c5ce7;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #888;
                margin-right: 4px;
            }
            QComboBox QAbstractItemView {
                background: white;
                border: 1px solid #d0d4e0;
                color: #2d3436;
                selection-background-color: #6c5ce7;
                selection-color: white;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                padding: 4px 10px;
                min-height: 22px;
                color: #2d3436;
            }
            QComboBox QAbstractItemView::item:hover {
                background: #f0edff;
            }
            QComboBox QAbstractItemView QScrollBar:vertical {
                width: 5px;
                background: transparent;
            }
            QComboBox QAbstractItemView QScrollBar::handle:vertical {
                background: #c0c4d0;
                border-radius: 3px;
                min-height: 20px;
            }
            QComboBox QAbstractItemView QScrollBar::handle:vertical:hover {
                background: #a0a5b5;
            }
            QComboBox QAbstractItemView QScrollBar::add-line:vertical,
            QComboBox QAbstractItemView QScrollBar::sub-line:vertical {
                height: 0;
            }
            QTextEdit {
                background: white;
                border: 2px solid #e0e3eb;
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
                color: #2d3436;
            }
            QTextEdit:hover {
                border-color: #6c5ce7;
            }
            QTextEdit:focus {
                border-color: #6c5ce7;
            }
            QScrollArea {
                border: 2px solid #e0e3eb;
                border-radius: 8px;
                background: white;
            }
            QScrollBar:vertical {
                width: 6px;
                background: transparent;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #d1d5e0;
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #b0b5c5;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QLabel("📝 管理对话内容")
        header.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
            color: white;
            background: #6c5ce7;
            padding: 18px 24px;
            border-radius: 0;
        """)
        header.setAlignment(Qt.AlignCenter)
        root.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(24, 20, 24, 20)
        body.setSpacing(14)

        form_card = QFrame()
        form_card.setStyleSheet("""
            QFrame {
                background: white;
                border: 1px solid #e8eaf0;
                border-radius: 12px;
            }
        """)
        form = QVBoxLayout(form_card)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)

        row1 = QHBoxLayout()
        row1.setSpacing(16)
        topic_group = QVBoxLayout()
        topic_group.setSpacing(6)
        topic_lbl = QLabel("选择分类")
        topic_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #6c5ce7; letter-spacing: 0.5px; background: transparent;")
        topic_group.addWidget(topic_lbl)
        self.topic_combo = QComboBox()
        topics = self.db.get_topics()
        for t in topics:
            self.topic_combo.addItem(t["display_name"], t["id"])
        topic_group.addWidget(self.topic_combo)
        row1.addLayout(topic_group)

        period_group = QVBoxLayout()
        period_group.setSpacing(6)
        period_lbl = QLabel("时段")
        period_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #6c5ce7; letter-spacing: 0.5px; background: transparent;")
        period_group.addWidget(period_lbl)
        self.period_combo = QComboBox()
        period_labels = {
            "general": "通用", "morning": "早晨", "forenoon": "上午",
            "pre_lunch": "午餐前", "lunch": "午餐", "afternoon": "下午",
            "evening": "傍晚", "night": "夜间"
        }
        for k, v in period_labels.items():
            self.period_combo.addItem(v, k)
        period_group.addWidget(self.period_combo)
        row1.addLayout(period_group)
        form.addLayout(row1)

        content_lbl = QLabel("对话内容")
        content_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #6c5ce7; letter-spacing: 0.5px; background: transparent;")
        form.addWidget(content_lbl)
        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("输入新的对话内容...")
        self.text_edit.setMaximumHeight(90)
        form.addWidget(self.text_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        self.add_btn = QPushButton("✚ 添加")
        self.add_btn.setStyleSheet("""
            QPushButton {
                background: #6c5ce7;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 28px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #5a4bd1;
            }
            QPushButton:pressed {
                background: #4a3db8;
            }
        """)
        self.add_btn.clicked.connect(self._add)
        btn_row.addWidget(self.add_btn)
        form.addLayout(btn_row)

        body.addWidget(form_card)

        saved_label = QLabel("💾 已保存的对话")
        saved_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #2d3436; margin-top: 4px; background: transparent;")
        body.addWidget(saved_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(120)
        self.list_widget = QWidget()
        self.list_widget.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(8, 8, 8, 8)
        self.list_layout.setSpacing(6)
        scroll.setWidget(self.list_widget)
        body.addWidget(scroll, 1)

        root.addLayout(body)

        self._refresh()

    def _refresh(self):
        for i in reversed(range(self.list_layout.count())):
            item = self.list_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        msgs = self.db.conn.execute(
            "SELECT content, source FROM messages WHERE source='user' ORDER BY id DESC LIMIT 20"
        ).fetchall()
        for row in msgs:
            text = row["content"][:50] + ("..." if len(row["content"]) > 50 else "")
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #f8f9fd;
                    border: 1px solid #eef0f5;
                    border-radius: 8px;
                }
                QFrame:hover {
                    background: #f0edff;
                    border-color: #d5cef0;
                }
            """)
            cl = QHBoxLayout(card)
            cl.setContentsMargins(12, 8, 12, 8)
            icon_label = QLabel("💬")
            icon_label.setStyleSheet("font-size: 14px; background: transparent;")
            cl.addWidget(icon_label)
            text_label = QLabel(text)
            text_label.setStyleSheet("""
                color: #555;
                font-size: 12px;
                background: transparent;
            """)
            text_label.setWordWrap(True)
            cl.addWidget(text_label, 1)
            self.list_layout.addWidget(card)
        if not msgs:
            empty = QLabel("还没有自定义对话，在上方添加一条吧 ✨")
            empty.setStyleSheet("color: #aaa; font-size: 12px; padding: 20px; background: transparent;")
            empty.setAlignment(Qt.AlignCenter)
            self.list_layout.addWidget(empty)
        self.list_layout.addStretch()

    def _add(self):
        content = self.text_edit.toPlainText().strip()
        if not content:
            return
        topic_id = self.topic_combo.currentData()
        period = self.period_combo.currentData()
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

        self._tray_setup()

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
        saved_id = self.db.get_setting("current_pet", "")
        if saved_id and self.pets:
            for i, p in enumerate(self.pets):
                if p.id == saved_id:
                    self.current_pet_index = i
                    break
        self.assets = None
        self._drag_pos = QPoint()
        self._was_dragging = False
        self._click_count = 0
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._single_click)

        if self.pets:
            self._load_pet(self.current_pet_index)

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
            WS_EX_APPWINDOW = 0x00040000
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex_style |= WS_EX_LAYERED
            ex_style &= ~(WS_EX_TOOLWINDOW | WS_EX_APPWINDOW)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)

            margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))

            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0004 | 0x0020 | 0x0001)
        except Exception:
            pass

    def _tray_setup(self):
        icon_path = os.path.join(_get_base_dir(), "CodexPets.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QApplication.instance().windowIcon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("CodexPets - 桌面宠物")

        tray_menu = QMenu()
        tray_menu.addAction("🐾 显示桌宠", self.show)
        tray_menu.addSeparator()
        tray_menu.addAction("❌ 退出", self._quit_app)
        self._tray.setContextMenu(tray_menu)

        self._tray.activated.connect(self._on_tray_activate)

    def _on_tray_activate(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show()

    def closeEvent(self, event):
        setting = self.db.get_setting("close_behavior", "ask")
        if setting == "tray":
            event.ignore()
            self.hide()
            self._tray.show()
            return
        elif setting == "exit":
            self._tray.hide()
            event.accept()
            return

        dialog = QMessageBox()
        dialog.setWindowTitle("🐾 桌面宠物")
        dialog.setText("关闭桌宠后要做什么？")
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)

        tray_btn = dialog.addButton("📦 最小化到托盘", QMessageBox.ActionRole)
        exit_btn = dialog.addButton("❌ 关闭程序", QMessageBox.AcceptRole)
        dialog.setDefaultButton(tray_btn)

        dont_ask = QCheckBox("不再询问，记住我的选择")
        dialog.setCheckBox(dont_ask)

        dialog.exec()

        remembered = None
        if dialog.clickedButton() == tray_btn:
            if dont_ask.isChecked():
                remembered = "tray"
            self._tray.show()
            event.ignore()
            self.hide()
        else:
            if dont_ask.isChecked():
                remembered = "exit"
            self._tray.hide()
            event.accept()

        if remembered:
            self.db.set_setting("close_behavior", remembered)

    def _quit_app(self):
        self._tray.hide()
        QApplication.instance().quit()

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
        menu.addAction("❌ 退出", self._quit_app)
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
        if self.assets:
            self.db.set_setting("current_pet", self.assets.id)

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

    def _refresh_pets(self):
        self.pets = discover_pets()
        if self.current_pet_index >= len(self.pets):
            self.current_pet_index = 0
        if self.pets:
            current_folder = self.assets.folder if self.assets else None
            found = False
            for i, p in enumerate(self.pets):
                if p.folder == current_folder:
                    self.current_pet_index = i
                    found = True
                    break
            if not found:
                self.current_pet_index = 0
            self._load_pet(self.current_pet_index)

    def _open_store(self):
        dialog = StoreDialog(self.db, self)
        dialog.exec()
        self._refresh_pets()

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
        self.setFixedSize(780, 620)
        self.setStyleSheet("""
            StoreDialog { background: #f0f2f5; }
        """)
        self._page = 1
        self._total_pages = 1
        self._all_pets_data = []
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QLabel("🧩 桌宠拓展")
        header.setStyleSheet("""
            font-size: 18px; font-weight: bold; color: white;
            background: #6c5ce7; padding: 16px 24px;
        """)
        header.setAlignment(Qt.AlignCenter)
        root.addWidget(header)

        tab_bar = QFrame()
        tab_bar.setStyleSheet("QFrame { background: white; border-bottom: 1px solid #e0e3eb; }")
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        self._tabs = {}
        for key, label in [("installed", "📦 已安装"), ("official", "🏪 Codex-Pets 社区"), ("petdex", "🌐 Petdex 社区")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(40)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; border: none;
                    padding: 0 28px; font-size: 14px; color: #888;
                    border-bottom: 3px solid transparent;
                }
                QPushButton:checked {
                    color: #6c5ce7; font-weight: 600;
                    border-bottom: 3px solid #6c5ce7;
                }
                QPushButton:hover { color: #6c5ce7; }
            """)
            btn.clicked.connect(lambda checked, k=key: self._switch_tab(k))
            tab_layout.addWidget(btn)
            self._tabs[key] = btn
        tab_layout.addStretch()
        root.addWidget(tab_bar)

        self._tab_content = QStackedWidget()
        root.addWidget(self._tab_content, 1)

        self._init_installed_tab()
        self._init_official_tab()
        self._init_petdex_tab()
        self._tabs["installed"].setChecked(True)

    def _switch_tab(self, key):
        for k, btn in self._tabs.items():
            btn.setChecked(k == key)
        idx = list(self._tabs.keys()).index(key)
        self._tab_content.setCurrentIndex(idx)
        if key == "installed":
            self._refresh_installed()
        elif key == "official" and not self._all_pets_data:
            self._load_page(0)
        elif key == "petdex":
            self._load_petdex_source()

    # ────────────── Installed Tab ──────────────

    def _init_installed_tab(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        info = QLabel("已安装到本地的桌宠，可在此管理和删除")
        info.setStyleSheet("color: #666; font-size: 12px; padding: 2px 0;")
        layout.addWidget(info)

        self.inst_status = QLabel("")
        self.inst_status.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.inst_status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 6px; background: #eee; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #ccc; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #aaa; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self.inst_grid = QWidget()
        self.inst_grid.setStyleSheet("background: transparent;")
        self.inst_layout = QGridLayout(self.inst_grid)
        self.inst_layout.setSpacing(8)
        self.inst_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.inst_grid)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setStyleSheet("""
            QPushButton { background: #6c5ce7; color: white; border: none;
                        padding: 6px 18px; border-radius: 6px; font-size: 12px; }
            QPushButton:hover { background: #5a4bd1; }
        """)
        refresh_btn.clicked.connect(self._refresh_installed)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

        self._tab_content.addWidget(container)

    def _refresh_installed(self):
        for i in reversed(range(self.inst_layout.count())):
            item = self.inst_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()

        base = os.path.join(_get_base_dir(), "pets")
        if not os.path.exists(base):
            lbl = QLabel("未找到 pets 目录")
            lbl.setStyleSheet("color: #aaa; font-size: 13px; padding: 40px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.inst_layout.addWidget(lbl, 0, 0, 1, 4)
            self.inst_status.setText("未安装任何桌宠")
            return

        entries = []
        for name in sorted(os.listdir(base)):
            folder = os.path.join(base, name)
            json_path = os.path.join(folder, "pet.json")
            webp_path = os.path.join(folder, "spritesheet.webp")
            if os.path.isdir(folder) and os.path.exists(json_path) and os.path.exists(webp_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    entries.append({
                        "id": meta.get("id", name),
                        "displayName": meta.get("displayName") or meta.get("name", name),
                        "description": meta.get("description", ""),
                        "folder": folder,
                        "json_path": json_path,
                        "webp_path": webp_path
                    })
                except Exception:
                    entries.append({
                        "id": name,
                        "displayName": name,
                        "description": "",
                        "folder": folder,
                        "json_path": json_path,
                        "webp_path": webp_path
                    })

        if not entries:
            lbl = QLabel("还没有安装任何桌宠，去商店看看吧 🎉")
            lbl.setStyleSheet("color: #aaa; font-size: 13px; padding: 40px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.inst_layout.addWidget(lbl, 0, 0, 1, 4)
            self.inst_status.setText("未安装任何桌宠")
            return

        self.inst_status.setText(f"共 {len(entries)} 个桌宠")
        base_dir = os.path.join(_get_base_dir(), "pets")
        for i, pet in enumerate(entries):
            card = self._make_installed_card(pet, base_dir)
            self.inst_layout.addWidget(card, i // 4, i % 4)

    def _make_installed_card(self, pet, base_dir):
        pid = pet["id"]
        name = pet["displayName"]
        desc = pet.get("description", "")

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

        if desc:
            d = desc if len(desc) <= 30 else desc[:28] + "..."
            desc_lbl = QLabel(d)
            desc_lbl.setStyleSheet("color: #999; font-size: 10px;")
            desc_lbl.setAlignment(Qt.AlignCenter)
            cl.addWidget(desc_lbl)

        cl.addStretch()

        del_btn = QPushButton("🗑 删除")
        del_btn.setStyleSheet("""
            QPushButton { background: #ff6b6b; color: white; border: none;
                        padding: 5px; border-radius: 5px; font-size: 11px; }
            QPushButton:hover { background: #e05555; }
        """)
        del_btn.clicked.connect(lambda checked, p=pid, n=name: self._delete_installed(p, n))
        cl.addWidget(del_btn)

        try:
            sheet = Image.open(pet["webp_path"]).convert("RGBA")
            cw, ch = sheet.width // 8, sheet.height // 9
            frame = sheet.crop((0, 0, cw, ch)).resize((110, 120), Image.NEAREST)
            qimg = QImage(frame.tobytes(), 110, 120, QImage.Format_RGBA8888)
            pm = QPixmap.fromImage(qimg)
            preview.setPixmap(pm)
            preview.setFixedSize(110, 120)
        except Exception:
            pass

        return card

    def _delete_installed(self, pid, name):
        msg = QMessageBox(self)
        msg.setWindowTitle("确认删除")
        msg.setText(f"确定要删除桌宠「{name}」吗？\n删除后需要重新下载才能使用。")
        msg.setIcon(QMessageBox.Question)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.setStyleSheet("""
            QMessageBox { background: white; }
            QLabel { color: #333; font-size: 13px; }
            QPushButton { padding: 6px 20px; border-radius: 4px; }
        """)
        reply = msg.exec()
        if reply != QMessageBox.Yes:
            return

        pet_folder = os.path.join(_get_base_dir(), "pets", pid)
        try:
            import shutil
            shutil.rmtree(pet_folder)
            self._refresh_installed()
            self._pd_filter()
            if isinstance(self.parent(), PetWindow):
                self.parent()._refresh_pets()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", f"删除「{name}」失败：{str(e)}")

    # ────────────── Official Tab (existing) ──────────────

    def _init_official_tab(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

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
        layout.addLayout(search_row)

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
        layout.addWidget(scroll, 1)

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
        layout.addLayout(nav)

        self._tab_content.addWidget(container)

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
            card = self._make_card(pet, base, "official")
            self.grid_layout.addWidget(card, i // 4, i % 4)

    # ────────────── Community Tab (Petdex) ──────────────

    def _init_petdex_tab(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel("💡 前往 petdex.crafter.run 找到喜欢的桌宠，记住名称后来此搜索下载")
        hint.setStyleSheet("color: #6c5ce7; font-size: 12px; padding: 4px 8px; "
                          "background: #f0edff; border-radius: 6px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.pd_refresh_btn = QPushButton("🔄 加载 Petdex 图库")
        self.pd_refresh_btn.setStyleSheet("""
            QPushButton { background: #6c5ce7; color: white; border: none;
                        padding: 6px 18px; border-radius: 6px; font-size: 13px; }
            QPushButton:hover { background: #5a4bd1; }
            QPushButton:disabled { background: #ccc; }
        """)
        self.pd_refresh_btn.clicked.connect(self._load_petdex_source)
        layout.addWidget(self.pd_refresh_btn, alignment=Qt.AlignCenter)

        search_row = QHBoxLayout()
        self.pd_search = QLineEdit()
        self.pd_search.setPlaceholderText("🔍 输入宠物名称或 ID 搜索...")
        self.pd_search.setStyleSheet("""
            QLineEdit { border: 2px solid #ddd; border-radius: 8px;
                       padding: 7px 12px; font-size: 13px; background: white; }
            QLineEdit:focus { border-color: #6c5ce7; }
        """)
        self.pd_search.textChanged.connect(self._pd_filter)
        search_row.addWidget(self.pd_search)

        self.pd_status = QLabel("")
        self.pd_status.setStyleSheet("color: #888; font-size: 12px;")
        self.pd_status.setFixedWidth(200)
        search_row.addWidget(self.pd_status)
        layout.addLayout(search_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { width: 6px; background: #eee; border-radius: 3px; }
            QScrollBar::handle:vertical { background: #ccc; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #aaa; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self.pd_grid = QWidget()
        self.pd_grid.setStyleSheet("background: transparent;")
        self.pd_layout = QGridLayout(self.pd_grid)
        self.pd_layout.setSpacing(8)
        self.pd_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.pd_grid)
        layout.addWidget(scroll, 1)

        nav_row = QHBoxLayout()
        self.pd_info = QLabel("")
        self.pd_info.setStyleSheet("color: #888; font-size: 12px;")
        nav_row.addWidget(self.pd_info)
        nav_row.addStretch()
        layout.addLayout(nav_row)

        self._tab_content.addWidget(container)
        self._pd_all = []
        self._pd_filtered = []

    def _load_petdex_source(self):
        self.pd_status.setText("正在从 Petdex 加载图库...")
        self.pd_refresh_btn.setEnabled(False)
        self._pd_all = []
        self._pd_filter()

        def fetch():
            import re as _re
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                req = urllib.request.Request(
                    "https://petdex.crafter.run/zh",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                resp = urllib.request.urlopen(req, context=ctx, timeout=10)
                html = resp.read().decode("utf-8")

                slugs = set()
                for pattern in [r'href="/zh/pets/([^/"\?]+)"', r'href="/pets/([^/"\?]+)"']:
                    for m in _re.finditer(pattern, html):
                        slugs.add(m.group(1))

                entries = []
                for slug in sorted(slugs):
                    name = slug
                    desc = ""
                    try:
                        jurl = f"https://pub-94495283df974cfea5e98d6a9e3fa462.r2.dev/curated/{slug}/pet.json"
                        jreq = urllib.request.Request(jurl, headers={"User-Agent": "Mozilla/5.0"})
                        jresp = urllib.request.urlopen(jreq, context=ctx, timeout=3)
                        meta = json.loads(jresp.read())
                        name = meta.get("displayName", slug)
                        desc = meta.get("description", "")
                    except Exception:
                        pass
                    entries.append({
                        "id": slug,
                        "displayName": name,
                        "description": desc or "Petdex 社区宠物",
                        "owner": "",
                        "spritesheetUrl": f"https://pub-94495283df974cfea5e98d6a9e3fa462.r2.dev/curated/{slug}/spritesheet.webp",
                    })
            except Exception as ex:
                err_msg = str(ex)
                def error():
                    self.pd_status.setText(f"❌ 加载失败: {err_msg[:50]}")
                    self.pd_refresh_btn.setEnabled(True)
                QApplication.instance().postEvent(self, _UIEvent(error))
                return

            def update():
                self.pd_refresh_btn.setEnabled(True)
                if not entries:
                    self.pd_status.setText("⚠️ 未找到桌宠，请确认网络或稍后重试")
                    self._pd_filter()
                    return
                self._pd_all = entries
                self.pd_status.setText(f"共 {len(entries)} 个桌宠")
                self._pd_filter()
            QApplication.instance().postEvent(self, _UIEvent(update))

        threading.Thread(target=fetch, daemon=True).start()

    def _pd_filter(self):
        query = self.pd_search.text().strip().lower()
        for i in reversed(range(self.pd_layout.count())):
            item = self.pd_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()

        if not self._pd_all:
            lbl = QLabel("点击「加载图库」浏览社区宠物，或在搜索框输入 ID 直接下载")
            lbl.setStyleSheet("color: #aaa; font-size: 13px; padding: 40px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.pd_layout.addWidget(lbl, 0, 0, 1, 4)
            return

        if query:
            self._pd_filtered = [p for p in self._pd_all if query in p["displayName"].lower() or query in p["id"].lower()]
        else:
            self._pd_filtered = list(self._pd_all)

        if not self._pd_filtered:
            lbl = QLabel("没有找到匹配的桌宠，试试直接输入 ID")
            lbl.setStyleSheet("color: #aaa; font-size: 13px; padding: 40px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.pd_layout.addWidget(lbl, 0, 0, 1, 4)
            self.pd_info.setText("")
            return

        self.pd_info.setText(f"显示 {len(self._pd_filtered)} 个")
        base = os.path.join(_get_base_dir(), "pets")
        for i, pet in enumerate(self._pd_filtered):
            card = self._make_community_card(pet, base)
            self.pd_layout.addWidget(card, i // 4, i % 4)

    def _make_community_card(self, pet, base_dir):
        pid = pet["id"]
        name = pet["displayName"]
        owner = pet.get("owner", "")
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
            row = QHBoxLayout()
            row.setSpacing(4)
            lbl = QLabel("✅ 已安装")
            lbl.setStyleSheet("color: #28a745; font-size: 11px; padding: 2px;")
            lbl.setAlignment(Qt.AlignCenter)
            row.addWidget(lbl)
            del_btn = QPushButton("🗑")
            del_btn.setFixedSize(28, 24)
            del_btn.setStyleSheet("""
                QPushButton { background: #ff6b6b; color: white; border: none;
                            border-radius: 4px; font-size: 11px; }
                QPushButton:hover { background: #e05555; }
            """)
            del_btn.clicked.connect(lambda checked, p=pid, n=name: self._delete_community_pet(p, n))
            row.addWidget(del_btn)
            cl.addLayout(row)
        else:
            btn = QPushButton("⬇ 下载")
            btn.setStyleSheet("""
                QPushButton { background: #4A7AFF; color: white; border: none;
                            padding: 5px; border-radius: 5px; font-size: 11px; }
                QPushButton:hover { background: #3B6BE8; }
                QPushButton:disabled { background: #ccc; }
            """)
            btn.clicked.connect(lambda checked, p=pid, n=name, b=btn: self._download_community(p, n, base_dir, b))
            cl.addWidget(btn)

        preview_url = pet.get("spritesheetUrl", "")
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
        else:
            def load_preview():
                try:
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    surl = f"https://pub-94495283df974cfea5e98d6a9e3fa462.r2.dev/curated/{pid}/spritesheet.webp"
                    req = urllib.request.Request(surl, headers={"User-Agent": "CodexPets/1.0"})
                    resp = urllib.request.urlopen(req, context=ctx, timeout=8)
                    data = resp.read()
                    sheet = Image.open(io_module.BytesIO(data)).convert("RGBA")
                    cw, ch = sheet.width // 8, sheet.height // 9
                    frame = sheet.crop((0, 0, cw, ch)).resize((110, 120), Image.NEAREST)
                    qimg = QImage(frame.tobytes(), 110, 120, QImage.Format_RGBA8888)
                    pm = QPixmap.fromImage(qimg)
                    def set_pm():
                        preview.setPixmap(pm)
                        preview.setFixedSize(110, 120)
                    QApplication.instance().postEvent(self, _UIEvent(set_pm))
                except Exception:
                    pass
            threading.Thread(target=load_preview, daemon=True).start()

        return card

    def _download_community(self, pid, name, base_dir, btn):
        btn.setText("⏳...")
        btn.setEnabled(False)
        parent_layout = btn.parentWidget().layout() if btn.parentWidget() else None

        def do():
            ok = self._do_download_petdex(pid, base_dir)

            def done():
                if ok:
                    btn.deleteLater()
                    lbl = QLabel("✅ 已安装")
                    lbl.setStyleSheet("color: #28a745; font-size: 11px; padding: 2px;")
                    lbl.setAlignment(Qt.AlignCenter)
                    if parent_layout:
                        parent_layout.addWidget(lbl)
                    QMessageBox.information(self, "下载完成", f"「{name}」已下载！\n可在菜单中切换使用。")
                else:
                    btn.setText("⬇ 重试")
                    btn.setEnabled(True)
            QApplication.instance().postEvent(self, _UIEvent(done))

        threading.Thread(target=do, daemon=True).start()

    def _delete_community_pet(self, pid, name):
        base_dir = os.path.join(_get_base_dir(), "pets")
        pet_folder = os.path.join(base_dir, pid)
        msg = QMessageBox(self)
        msg.setWindowTitle("确认删除")
        msg.setText(f"确定要删除桌宠「{name}」吗？\n删除后需要重新下载才能使用。")
        msg.setIcon(QMessageBox.Question)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.setStyleSheet("""
            QMessageBox { background: white; }
            QLabel { color: #333; font-size: 13px; }
            QPushButton { padding: 6px 20px; border-radius: 4px; }
        """)
        reply = msg.exec()
        if reply != QMessageBox.Yes:
            return
        try:
            import shutil
            shutil.rmtree(pet_folder)
            self._pd_filter()
            if isinstance(self.parent(), PetWindow):
                self.parent()._refresh_pets()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", f"删除「{name}」失败：{str(e)}")

    def _do_download_petdex(self, pid, dest_dir):
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            folder = os.path.join(dest_dir, pid)
            os.makedirs(folder, exist_ok=True)

            r2_base = f"https://pub-94495283df974cfea5e98d6a9e3fa462.r2.dev/curated/{pid}"

            for url, fname in [(f"{r2_base}/pet.json", "pet.json"), (f"{r2_base}/spritesheet.webp", "spritesheet.webp")]:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "CodexPets/1.0"})
                    resp = urllib.request.urlopen(req, context=ctx, timeout=15)
                    with open(os.path.join(folder, fname), "wb") as f:
                        f.write(resp.read())
                except Exception:
                    gh_url = f"https://raw.githubusercontent.com/crafter-station/petdex/main/public/pets/{pid}/{fname}"
                    req = urllib.request.Request(gh_url, headers={"User-Agent": "CodexPets/1.0"})
                    resp = urllib.request.urlopen(req, context=ctx, timeout=15)
                    with open(os.path.join(folder, fname), "wb") as f:
                        f.write(resp.read())
            return True
        except Exception:
            return False

    # ────────────── Shared methods ──────────────

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

    def _make_card(self, pet, base_dir, source="official"):
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
            row = QHBoxLayout()
            row.setSpacing(4)
            lbl = QLabel("✅ 已安装")
            lbl.setStyleSheet("color: #28a745; font-size: 11px; padding: 2px;")
            lbl.setAlignment(Qt.AlignCenter)
            row.addWidget(lbl)
            del_btn = QPushButton("🗑")
            del_btn.setFixedSize(28, 24)
            del_btn.setStyleSheet("""
                QPushButton { background: #ff6b6b; color: white; border: none;
                            border-radius: 4px; font-size: 11px; }
                QPushButton:hover { background: #e05555; }
            """)
            del_btn.clicked.connect(lambda checked, p=pid, n=name, b=del_btn: self._delete_pet(p, n, base_dir, b))
            row.addWidget(del_btn)
            cl.addLayout(row)
        else:
            btn = QPushButton("⬇ 下载")
            btn.setStyleSheet("""
                QPushButton { background: #4A7AFF; color: white; border: none;
                            padding: 5px; border-radius: 5px; font-size: 11px; }
                QPushButton:hover { background: #3B6BE8; }
                QPushButton:disabled { background: #ccc; }
            """)
            if source == "official":
                btn.clicked.connect(lambda checked, p=pid, n=name, b=btn: self._download(p, n, base_dir, b))
            else:
                btn.clicked.connect(lambda checked, p=pid, n=name, b=btn: self._download_petdex(p, n, base_dir, b))
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
                    QMessageBox.information(self, "下载完成", f"「{name}」已下载！\n可在菜单中切换使用。")
                else:
                    btn.setText("⬇ 重试")
                    btn.setEnabled(True)

            QApplication.instance().postEvent(self, _UIEvent(done))

        threading.Thread(target=do, daemon=True).start()

    def _delete_pet(self, pid, name, base_dir, btn):
        msg = QMessageBox(self)
        msg.setWindowTitle("确认删除")
        msg.setText(f"确定要删除桌宠「{name}」吗？\n删除后需要重新下载才能使用。")
        msg.setIcon(QMessageBox.Question)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        msg.setStyleSheet("""
            QMessageBox { background: white; }
            QLabel { color: #333; font-size: 13px; }
            QPushButton { padding: 6px 20px; border-radius: 4px; }
        """)
        reply = msg.exec()
        if reply != QMessageBox.Yes:
            return

        pet_folder = os.path.join(base_dir, pid)
        try:
            import shutil
            shutil.rmtree(pet_folder)
            self._filter_display()
            self._pd_filter()
            if isinstance(self.parent(), PetWindow):
                self.parent()._refresh_pets()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", f"删除「{name}」失败：{str(e)}")

    def customEvent(self, event):
        if isinstance(event, _UIEvent):
            event.fn()


class _UIEvent(QEvent):
    def __init__(self, fn):
        super().__init__(QEvent.Type(QEvent.User + 1))
        self.fn = fn


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CodexPets")
    app.setQuitOnLastWindowClosed(False)

    icon_path = os.path.join(_get_base_dir(), "CodexPets.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    db = PetDatabase()
    first = db.is_first_launch()

    if first:
        wiz = SetupWizard(db)
        if wiz.exec() == QDialog.Accepted:
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
