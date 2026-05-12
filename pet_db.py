import sqlite3
import os
import json
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet_data.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    time_period TEXT,
    content TEXT NOT NULL,
    source TEXT DEFAULT 'system',
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);
"""

DEFAULT_TOPICS = [
    ("greeting", "问候", 1),
    ("daily", "日常", 1),
    ("poem", "古诗", 1),
    ("knowledge", "知识", 0),
    ("philosophy", "哲学", 0),
    ("life", "生活", 1),
    ("work", "工作", 1),
    ("games", "游戏", 0),
    ("food", "美食", 1),
    ("cheer", "鼓励", 1),
    ("pet_talk", "宠物闲聊", 1),
]

INITIAL_MESSAGES = {
    "greeting": {
        "morning": ["早上好呀~今天也要加油哦！", "新的一天开始了！", "早安！吃早餐了吗？"],
        "forenoon": ["上午好~状态怎么样？", "已经开始一小时了，加油！"],
        "pre_lunch": ["还有一小时到午饭时间~", "肚子有点饿了…"],
        "lunch": ["吃饭啦~！", "午饭时间到！"],
        "afternoon": ["下午也要加油哦！", "来杯咖啡提提神吧！"],
        "evening": ["快下班了，再坚持一下！", "夕阳无限好~"],
        "night": ["还在加班呢？辛苦了~", "早点休息哦！", "晚安！"],
        "general": ["你好呀~", "今天怎么样？", "过得开心吗？"],
    },
    "daily": {
        "general": [
            "今天天气真不错呢！",
            "你吃了吗？",
            "周末有什么计划吗？",
            "今天工作忙不忙？",
            "要记得多喝水哦！",
            "坐久了站起来活动一下吧~",
        ]
    },
    "poem": {
        "general": [
            "长风破浪会有时，直挂云帆济沧海。",
            "山重水复疑无路，柳暗花明又一村。",
            "不畏浮云遮望眼，自缘身在最高层。",
            "沉舟侧畔千帆过，病树前头万木春。",
            "会当凌绝顶，一览众山小。",
            "问渠那得清如许，为有源头活水来。",
            "纸上得来终觉浅，绝知此事要躬行。",
        ]
    },
    "knowledge": {
        "general": [
            "你知道吗？地球每天有大约4.3万公斤的尘埃降落到地面。",
            "海豚在睡觉时只有一半大脑在休息哦。",
            "蜜蜂需要采集约200万朵花才能酿造1公斤蜂蜜。",
            "人的胃酸可以溶解金属，但胃壁每三天会更新一次。",
        ]
    },
    "philosophy": {
        "general": [
            "知之为知之，不知为不知，是知也。",
            "已所不欲，勿施于人。",
            "学而不思则罔，思而不学则殆。",
            "人生到处知何似，应似飞鸿踏雪泥。",
        ]
    },
    "life": {
        "general": [
            "生活就像一盒巧克力，你永远不知道下一颗是什么味道。",
            "今天的努力是明天幸运的伏笔。",
            "享受当下的每一刻~",
            "简单的生活也可以很幸福。",
        ]
    },
    "work": {
        "general": [
            "加油！代码总会跑通的！",
            "遇到bug不要慌，先喝口水~",
            "重构是一种修行……",
            "再难的题，拆开来一步一步做就简单了。",
            "提交前记得review一下哦！",
        ]
    },
    "games": {
        "general": [
            "今天打游戏了吗？",
            "适度游戏益脑，沉迷游戏伤身~",
            "又出新版本了，更新了吗？",
            "游戏输了不要紧，开心最重要！",
        ]
    },
    "food": {
        "general": [
            "今天吃什么好吃的了？",
            "好想吃火锅啊……",
            "甜食可以让人心情变好哦！",
            "你最喜欢的菜是什么？",
        ]
    },
    "cheer": {
        "general": [
            "你超棒的！",
            "今天也要开心哦！",
            "加油加油！",
            "世界因你而美好~",
            "每一步都在进步呢！",
            "相信自己，你可以的！",
        ]
    },
    "pet_talk": {
        "general": [
            "嘿，想我了吗？",
            "一直盯着我看干嘛~",
            "要不要摸摸我的头？",
            "你认真工作的样子真好看~",
            "好无聊啊，陪我玩会儿？",
            "呼……一直在看着你呢。",
        ]
    },
}


class PetDatabase:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self._init_data()

    def _init_schema(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def _init_data(self):
        existing = self.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        if existing > 0:
            return
        for name_key, display_name, enabled in DEFAULT_TOPICS:
            self.conn.execute(
                "INSERT INTO topics (name, display_name, enabled) VALUES (?, ?, ?)",
                (name_key, display_name, enabled)
            )
        name_to_id = {}
        for row in self.conn.execute("SELECT id, name FROM topics").fetchall():
            name_to_id[row["name"]] = row["id"]

        for topic_name, periods in INITIAL_MESSAGES.items():
            tid = name_to_id.get(topic_name)
            if tid is None:
                continue
            for period, msgs in periods.items():
                for msg in msgs:
                    self.conn.execute(
                        "INSERT INTO messages (topic_id, time_period, content, source) VALUES (?, ?, ?, 'system')",
                        (tid, period, msg)
                    )
        self.conn.commit()

    def get_setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, value, value)
        )
        self.conn.commit()

    def is_first_launch(self):
        count = self.get_setting("launch_count", "0")
        return int(count) == 0

    def increment_launch(self):
        count = int(self.get_setting("launch_count", "0"))
        self.set_setting("launch_count", str(count + 1))

    def get_user_name(self):
        return self.get_setting("user_name", "主人")

    def set_user_name(self, name):
        self.set_setting("user_name", name)

    def get_enabled_topics(self):
        rows = self.conn.execute(
            "SELECT id, name, display_name FROM topics WHERE enabled=1"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_topic_enabled(self, topic_name, enabled):
        self.conn.execute("UPDATE topics SET enabled=? WHERE name=?", (1 if enabled else 0, topic_name))
        self.conn.commit()

    def set_topics_enabled(self, topic_names):
        self.conn.execute("UPDATE topics SET enabled=0")
        for name in topic_names:
            self.conn.execute("UPDATE topics SET enabled=1 WHERE name=?", (name,))
        self.conn.commit()

    def get_messages_for_period(self, period):
        enabled = self.conn.execute("SELECT id FROM topics WHERE enabled=1").fetchall()
        if not enabled:
            return []
        tids = [r["id"] for r in enabled]
        placeholders = ",".join("?" for _ in tids)
        rows = self.conn.execute(
            f"SELECT content, topic_id FROM messages WHERE topic_id IN ({placeholders}) AND (time_period=? OR time_period='general')",
            (*tids, period)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_topics(self):
        rows = self.conn.execute("SELECT id, name, display_name, enabled FROM topics ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def add_message(self, topic_id, content, time_period="general"):
        self.conn.execute(
            "INSERT INTO messages (topic_id, time_period, content, source) VALUES (?, ?, ?, 'user')",
            (topic_id, time_period, content)
        )
        self.conn.commit()
