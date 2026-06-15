# B站有声书工具

一个用于下载B站合集音频并进行处理的桌面工具，支持按章节切割、变速处理和批量下载。

## 功能特性

### 核心功能

- **音频下载** - 下载B站分P合集的音频
- **章节切割** - 按固定时长切割音频文件
- **变速处理** - 支持 0.5x-3.0x 变速，不变调
- **批量下载** - 支持批量URL导入和队列管理

### 特色功能

- 真实进度显示 - 下载进度实时更新
- 配置持久化 - 用户设置自动保存
- 历史记录 - 记录下载历史
- 断点续传 - 变速处理支持断点续传
- 取消功能 - 支持取消正在进行的任务

## 截图

> TODO: 添加应用截图

## 安装说明

### 方式一：直接使用（推荐）

1. 从 [Releases](https://github.com/cmend4j/bilibili-audiobook/releases) 下载以下两个文件：
   - `bilibili-audiobook.exe`（主程序）
   - `ffmpeg.exe`（音频处理引擎）
2. 将两个文件放在同一目录下
3. 双击 `bilibili-audiobook.exe` 运行

### 方式二：从源码运行

```bash
# 克隆仓库
git clone https://github.com/cmend4j/bilibili-audiobook.git
cd bilibili-audiobook

# 安装依赖
pip install yt-dlp

# 运行程序
python bilibili_audiobook.py
```

## 系统要求

- Windows 10/11
- 网络连接（下载功能）
- ffmpeg（已包含在发布包中）

## 使用方法

### 下载与切割

1. 切换到「下载与切割」标签页
2. 输入B站合集链接
3. 选择切割模式（固定时长/章节对齐）
4. 设置输出目录
5. 点击「开始下载并切割」

### 变速处理

1. 切换到「变速处理」标签页
2. 选择源文件夹
3. 设置倍速（0.5x-3.0x）
4. 选择输出目录
5. 点击「开始变速」

### 批量下载

1. 切换到「批量处理」标签页
2. 添加B站链接或从文件导入
3. 点击「开始批量下载」

## 配置文件

配置文件保存在 `config/` 目录下：

- `settings.json` - 用户设置
- `batch_queue.json` - 批量队列

## 技术栈

- Python 3.8+
- tkinter - GUI框架
- yt-dlp - 视频下载
- ffmpeg - 音频处理
- PyInstaller - 打包

## 项目结构

```
bilibili-audiobook/
├── bilibili_audiobook.py   # 主程序源码
├── requirements.txt        # Python依赖
├── README.md              # 项目说明
├── LICENSE                # 开源许可证
├── CHANGELOG.md           # 更新日志
├── 使用说明.txt             # 使用说明
├── .gitignore             # Git忽略文件
└── .github/workflows/     # CI/CD配置
```

> 注意：`bilibili-audiobook.exe` 和 `ffmpeg.exe` 不包含在源码仓库中，请从 [Releases](https://github.com/cmend4j/bilibili-audiobook/releases) 下载。

## 开发

### 环境搭建

```bash
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 确保ffmpeg.exe在当前目录下
# 从 https://github.com/BtbN/FFmpeg-Builds/releases 下载ffmpeg.exe

# 打包
pyinstaller --onefile --windowed --name "bilibili-audiobook" --add-data "ffmpeg.exe;." bilibili_audiobook.py
```

## 更新日志

### v2.0.0 (2026-06-15)

- 新增批量下载功能
- 新增真实进度显示
- 新增配置持久化
- 新增历史记录
- 改进错误处理
- 改进输入验证
- 修复多个bug

### v1.0.0 (初始版本)

- 基础下载和切割功能
- 变速处理功能
- GUI界面

## 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建你的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交你的改动 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开一个 Pull Request

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 致谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 视频下载工具
- [ffmpeg](https://ffmpeg.org/) - 音频处理框架
- [Bilibili](https://www.bilibili.com/) - 视频平台

## 免责声明

本工具仅供个人学习和研究使用。请遵守相关法律法规和B站的使用条款。下载的内容版权归原作者所有，请勿用于商业用途。

## 联系方式

- GitHub: [cmend4j](https://github.com/cmend4j)

---

如果这个项目对你有帮助，请给个 Star 支持一下！
