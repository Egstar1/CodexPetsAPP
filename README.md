# 🐾 Desktop Pet

[English](#english) | [中文](#chinese)

---

## Chinese

一个基于 PySide6 的桌面宠物程序，兼容 [Codex Pets](https://codex-pets.net/) 精灵图生态。可自定义对话内容、在线下载宠物素材。

### ✨ 功能特色

| 功能 | 说明 |
|------|------|
| 🎨 **精灵图渲染** | 加载 Codex 标准 8×9 spritesheet.webp，支持 9 种动画状态 |
| 🐱 **海量宠物** | 内置商店对接 codex-pets.net，**1600+** 免费宠物一键下载 |
| 💬 **智能对话** | 按时段（早/午/晚）自动问候，支持古诗、鼓励、闲聊等多主题 |
| 🗃️ **数据库管理** | SQLite 存储设置与对话内容，首次启动向导配置偏好 |
| 🖱️ **交互控制** | 左键单击随机动作、双击暂停/恢复、拖拽移动、右键菜单 |
| ⚡ **速度/缩放** | 0.25× ~ 3× 速度调节，50% ~ 200% 缩放 |
| 🧩 **对话扩展** | 右键菜单打开编辑器，自行添加对话内容 |

### 📦 快速开始

#### 方式一：下载 EXE（推荐）

1. 前往 [Releases](https://github.com/Egstar1/CodexPetsAPP/releases) 下载最新版 `CodexPets.zip`
2. 解压到任意文件夹，运行 `CodexPets.exe`
3. 首次启动会自动弹出设置向导

#### 方式二：源码运行

需要 Python 3.9+：

```bash
# 1. 克隆仓库
git clone https://github.com/Egstar1/CodexPetsAPP.git
cd CodexPetsAPP

# 2. 安装依赖
pip install PySide6 Pillow

# 3. 启动
python desktop_pet.py
```

#### 方式三：自行打包 EXE

```bash
# 安装 PyInstaller
pip install pyinstaller

# 运行打包脚本
build_exe.bat

# 打包后位于 dist/CodexPets/CodexPets.exe
```

### 📁 目录结构

```
desktop-pet/
├── desktop_pet.py         # 主程序
├── pet_db.py              # 数据库层
├── pets/                  # 宠物素材目录
│   ├── mimi/             
│   │   ├── pet.json       # 宠物元数据
│   │   └── spritesheet.webp  # 精灵图集
│   ├── anya/
│   └── ...
├── build_exe.bat          # EXE 打包脚本
└── README.md
```

### 🌐 添加更多宠物

**在线方式**：右键桌宠 → `🧩 桌宠拓展` → 浏览/搜索 → 点击下载

**手动方式**：从 [codex-pets.net](https://codex-pets.net) 或其他社区下载宠物，将 `pet.json` + `spritesheet.webp` 放入 `pets/宠物名/` 目录。

### 🗣️ 对话管理

右键桌宠 → `📝 管理对话` → 选择分类和时段 → 输入内容 → 添加

程序按以下时段自动发送对应消息：`早晨(5-8)` → `上午(8-10)` → `饭前(10-12)` → `午饭(12-13)` → `下午(13-17)` → `傍晚(17-19)` → `夜晚(19+)`

### 🔧 依赖

- Python 3.9+
- PySide6 ≥ 6.5
- Pillow ≥ 10.0

---

## English

A desktop pet application built with PySide6, compatible with the [Codex Pets](https://codex-pets.net/) sprite ecosystem. Features customizable conversations and an online pet store.

### ✨ Features

| Feature | Description |
|---------|-------------|
| 🎨 **Sprite Rendering** | Loads Codex standard 8×9 spritesheet.webp with 9 animation states |
| 🐱 **Pet Store** | Built-in store with **1600+** free pets from codex-pets.net |
| 💬 **Smart Chat** | Time-aware greetings, poems, encouragement, pet talk & more |
| 🗃️ **Database** | SQLite-powered settings & conversations, first-launch wizard |
| 🖱️ **Controls** | Click for random action, double-click pause, drag to move, right-click menu |
| ⚡ **Speed/Zoom** | 0.25× ~ 3× speed control, 50% ~ 200% scale |
| 🧩 **Message Editor** | Add your own conversation topics and messages |

### 📦 Quick Start

#### Option 1: Download EXE

1. Go to [Releases](https://github.com/Egstar1/CodexPetsAPP/releases) and download `CodexPets.zip`
2. Extract and run `CodexPets.exe`
3. The setup wizard will appear on first launch

#### Option 2: Run from Source

Requires Python 3.9+:

```bash
git clone https://github.com/Egstar1/CodexPetsAPP.git
cd CodexPetsAPP
pip install PySide6 Pillow
python desktop_pet.py
```

#### Option 3: Build EXE

```bash
pip install pyinstaller
build_exe.bat
# Output: dist/CodexPets/CodexPets.exe
```

### 📁 Project Structure

```
desktop-pet/
├── desktop_pet.py         # Main application
├── pet_db.py              # Database layer
├── pets/                  # Pet assets directory
│   ├── mimi/             
│   │   ├── pet.json       # Pet metadata
│   │   └── spritesheet.webp  # Sprite sheet
│   ├── anya/
│   └── ...
├── build_exe.bat          # EXE build script
└── README.md
```

### 🌐 Adding More Pets

**Online**: Right-click pet → `🧩 Pet Store` → Browse/Search → Download

**Manual**: Download pets from [codex-pets.net](https://codex-pets.net), place `pet.json` + `spritesheet.webp` into `pets/pet_name/` folder.

### 🗣️ Managing Conversations

Right-click pet → `📝 Manage Messages` → Select topic and time period → Input content → Add

Messages auto-send by time: `Morning(5-8)` → `Forenoon(8-10)` → `Pre-lunch(10-12)` → `Lunch(12-13)` → `Afternoon(13-17)` → `Evening(17-19)` → `Night(19+)`

### 🔧 Dependencies

- Python 3.9+
- PySide6 ≥ 6.5
- Pillow ≥ 10.0

### 📄 License

MIT License
