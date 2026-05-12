<br />

***

## 📋 整体演进历程

```
Version 1 (tkinter 像素鲸鱼)  →  代码画角色，28×28像素
Version 2 (tkinter 哆啦A梦)   →  矢量Canvas绘图，动作关键帧
Version 3 (tkinter 精灵图)    →  加载Codex标准spritesheet.webp
Version 4 (PySide6 + SQLite)  →  当前版本
```

***

## 🎯 新框架的核心设计亮点

### 一、数据与表现分离

```
pet_db.py          ←  数据层（SQLite）
  ├── settings     ←  用户设置（称呼、偏好）
  ├── topics       ←  11个主题分类
  └── messages     ←  按主题+时段存储对话内容

desktop_pet.py     ←  表现层（PySide6）
  ├── PetAssets    ←  精灵图加载器
  ├── AnimController  ←  动画状态机
  ├── PetWindow    ←  桌面窗口
  ├── BubbleWidget ←  气泡渲染
  ├── SetupWizard  ←  首次启动向导
  ├── StoreDialog  ←  在线商店
  └── MessageEditor ←  对话管理
```

**优势**：对话内容、主题偏好、用户设置全部存在数据库里，用户可以新增内容而无需改代码。启动时根据 `launch_count` 判断是否是首次运行。

### 二、精灵图自动帧检测（解决"消失"的第一道关口）

```python
# 加载时自动检测每行实际帧数
for col in range(8):
    frame = crop(row, col)
    non_transparent = 统计非透明像素数
    if non_transparent >= 50:
        real_count = col + 1   # 更新真实帧数

# 动画播放时用实际帧数
cfg["frames"] = real_frames  # 不再是硬编码8
```

**解决了**：精灵图 idle 只有6帧、waving只有4帧，但之前写死8帧导致播放空白帧"消失"。

### 三、永不删除的渲染对象（解决"消失"的第二道关口）

```python
# 创建一次，终身使用
if self.pet_item is None:
    self.pet_item = QGraphicsPixmapItem()  # 只创建一次
    self.scene.addItem(self.pet_item)

# 之后只更新内容
self.pet_item.setPixmap(new_pixmap)  # 不删不建
self.pet_item.setPos(x, y)
```

**解决了**：旧 tkinter 版 `canvas.delete("pet") → create_image` 的"先删后建"窗口期。现在**永远不删除图像对象**，只是替换内容，即使帧加载失败也保持上一帧。

### 四、瞬间状态切换（解决"消失"的第三道关口）

```python
def update(self):
    ...
    if not cfg["loop"] and absolute_frame >= total_frames:
        self.set_state(cfg.get("return_to", "idle"))  # 直接切，不等
        return True
```

**解决了**：非循环动作（waving/jumping）完成后，不经过任何延迟、不绕回 frame 0，直接 `set_state("idle")` → `_last_abs = 0` → 下一渲染就是 idle 的 frame 0。从最后动作帧直接到 idle 帧 0，零帧空白。

### 五、三层帧兜底

```python
pm = self._get_pixmap(row, col)       # 当前帧
if pm is None:
    pm = self._get_pixmap(row, 0)     # 同状态第0帧
if pm is None:
    pm = self._get_pixmap(idle_row, 0)  # idle第0帧
if pm is None:
    return  # 什么都不做，保留旧画面
```

### 六、Windows DWM 装饰消除

PySide6 在 Windows 上会多出圆角阴影和半透明边框（DWM 自动添加）。通过 Win32 API 精确移除：

```python
DwmSetWindowAttribute(禁用圆角)
DwmSetWindowAttribute(禁用过渡动画)
SetWindowLong(清除所有窗口样式位)
DwmExtendFrameIntoClientArea(-1)  # 消除阴影
```

### 七、关键帧→整数tick计数

```
旧版：self.frame = (self.frame + 0.064) % 8.0  
      → 浮点累加误差导致帧长短不齐，循环时"跳一下"

新版：absolute_frame = tick_count // ticks_per_frame
      → 每帧长度完全相等，8帧x6tick=48tick一模一样的循环
```

### 八、完整的功能矩阵

| 功能        | 实现                                    |
| :-------- | :------------------------------------ |
| **精灵图加载** | Codex 标准 8×9 网格，自动检测实际帧数              |
| **动画控制**  | 9种状态，独立FPS，循环/单次自动回退                  |
| **窗口控制**  | 无边框置顶、可拖拽、单击/双击交互                     |
| **在线商店**  | 对接 codex-pets.net API，1611个宠物，网格卡片+搜索 |
| **数据库**   | SQLite 存储设置/主题/对话，首次启动向导              |
| **对话系统**  | 按时段智能问候，11个话题分类，用户可新增内容               |
| **气泡渲染**  | QPainter 自绘，圆角矩形+三角形尾巴+自动换行           |
| **速度控制**  | 0.25× \~ 3× 六级调速                      |
| **缩放**    | 50% \~ 200%                           |
| **Log监控** | 文件变化监听，气泡弹出                           |

***

## 🔍 "消失"问题的解决总结

| 原因                   | 解决方案                         | 影响程度                      |
| :------------------- | :--------------------------- | :------------------------ |
| **精灵图帧6写死8**         | 自动检测非透明像素确定真实帧数              | 最大——所有宠物、所有状态的周期性消失       |
| **delete→create窗口期** | 改为永久图像对象+itemconfig更新        | 第二——偶发的中间态空白              |
| **非循环动画绕回frame0**    | 完成时直接set\_state(idle)，不经过模运算 | 第三——waving/jumping结束时短暂消失 |
| **浮点帧推进不均匀**         | 整数tick计数，帧长严格相等              | 第四——循环末端的微小跳动             |
| **render先删后绘**       | 加载成功后才删旧图画新图                 | 第五——叠加风险                  |

