#!/usr/bin/env python3
"""
B站有声书下载切割工具 - tkinter GUI版
功能: 下载B站分P合集 → 按章节切割 → 变速处理
依赖: ffmpeg (exe同目录或系统PATH), yt-dlp (已打包进exe)
"""

import os
import sys
import re
import glob
import shutil
import subprocess
import threading
import logging
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Callable, Tuple

# ============================================================
# 常量定义
# ============================================================

VERSION = "2.0.0"
DEFAULT_CHAPTER_MINUTES = 20
DEFAULT_SPEED = 1.5
MIN_SPEED = 0.5
MAX_SPEED = 3.0
MIN_FILE_SIZE_BYTES = 5000  # 忽略小于此大小的文件
MAX_CONCURRENT_DOWNLOADS = 4
MAX_RETRIES = 5
DEFAULT_AUDIO_QUALITY = "0"
DEFAULT_OUTPUT_DIR = "有声书"

# 日志配置
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ============================================================
# 日志系统
# ============================================================

def setup_logger(log_file: Optional[str] = None) -> logging.Logger:
    """配置日志系统"""
    logger = logging.getLogger("B站有声书工具")
    logger.setLevel(logging.DEBUG)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    logger.addHandler(console_handler)

    # 文件处理器（可选）
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        logger.addHandler(file_handler)

    return logger


# 全局日志实例
logger = setup_logger()


# ============================================================
# 路径工具 (PyInstaller 兼容)
# ============================================================

def get_exe_dir() -> str:
    """获取exe所在目录 (开发模式和打包模式均可用)"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


def find_ffmpeg() -> str:
    """按优先级查找ffmpeg: exe同目录 > 系统PATH > 常见位置"""
    exe_dir = get_exe_dir()

    candidates = [
        os.path.join(exe_dir, "ffmpeg.exe"),
        os.path.join(exe_dir, "ffmpeg"),
    ]

    # PATH查找
    for name in ["ffmpeg.exe", "ffmpeg"]:
        p = shutil.which(name)
        if p:
            candidates.append(p)

    # 兜底位置（仅Windows）
    if sys.platform == "win32":
        candidates.extend([
            r"C:\Windows\System32\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        ])

    for loc in candidates:
        if os.path.isfile(loc):
            logger.debug(f"找到ffmpeg: {loc}")
            return loc

    logger.warning("未找到ffmpeg，将使用默认命令")
    return "ffmpeg"


def find_ytdlp() -> List[str]:
    """
    查找yt-dlp:
    - 打包模式: 使用 sys.executable -m yt_dlp (pyinstaller --collect-all 后可用)
    - 开发模式: 使用 PATH 中的 yt-dlp
    - 兜底: python -m yt_dlp
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后 yt_dlp 模块内嵌, 通过 -m 调用
        return [sys.executable, "-m", "yt_dlp"]

    # 开发模式: 优先 PATH
    p = shutil.which("yt-dlp")
    if p:
        return [p]

    # pip安装的入口点
    return [sys.executable, "-m", "yt_dlp"]


# 全局工具路径
FFMPEG = find_ffmpeg()
YTDLP_CMD = find_ytdlp()


# ============================================================
# 配置管理
# ============================================================

class ConfigManager:
    """配置管理器，支持持久化存储"""

    def __init__(self, config_file: Optional[str] = None):
        if config_file is None:
            config_dir = os.path.join(get_exe_dir(), "config")
            os.makedirs(config_dir, exist_ok=True)
            self.config_file = os.path.join(config_dir, "settings.json")
        else:
            self.config_file = config_file

        self.default_config = {
            "chapter_minutes": DEFAULT_CHAPTER_MINUTES,
            "default_speed": DEFAULT_SPEED,
            "output_dir": str(Path.home() / DEFAULT_OUTPUT_DIR),
            "delete_original": False,
            "last_urls": [],
            "max_concurrent": MAX_CONCURRENT_DOWNLOADS,
        }
        self.config = self.load()

    def load(self) -> dict:
        """加载配置文件"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # 合并默认配置
                    config = self.default_config.copy()
                    config.update(loaded)
                    logger.debug(f"配置已加载: {self.config_file}")
                    return config
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"配置文件加载失败: {e}")

        return self.default_config.copy()

    def save(self) -> None:
        """保存配置文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            logger.debug(f"配置已保存: {self.config_file}")
        except IOError as e:
            logger.error(f"配置文件保存失败: {e}")

    def get(self, key: str, default=None):
        """获取配置项"""
        return self.config.get(key, default)

    def set(self, key: str, value) -> None:
        """设置配置项"""
        self.config[key] = value

    def add_recent_url(self, url: str, max_count: int = 10) -> None:
        """添加最近使用的URL"""
        urls = self.config.get("last_urls", [])
        if url in urls:
            urls.remove(url)
        urls.insert(0, url)
        self.config["last_urls"] = urls[:max_count]


# 全局配置实例
config_manager = ConfigManager()


# ============================================================
# 输入验证
# ============================================================

def validate_bilibili_url(url: str) -> Tuple[bool, str]:
    """
    验证B站链接有效性
    返回: (是否有效, 错误信息)
    """
    if not url or not url.strip():
        return False, "请输入B站合集链接"

    url = url.strip()

    # 检查是否为B站链接
    bilibili_patterns = [
        r"bilibili\.com",
        r"b23\.tv",
        r"BV[a-zA-Z0-9]+",
    ]

    if not any(re.search(pattern, url) for pattern in bilibili_patterns):
        return False, "请输入有效的B站链接（bilibili.com 或 b23.tv）"

    # 检查URL格式
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return True, ""


def validate_directory(path: str, name: str = "目录") -> Tuple[bool, str]:
    """
    验证目录有效性
    返回: (是否有效, 错误信息)
    """
    if not path:
        return False, f"请选择{name}"

    if not os.path.exists(path):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            return False, f"无法创建{name}: {e}"

    if not os.path.isdir(path):
        return False, f"{name}路径无效"

    return True, ""


def validate_speed(speed: float) -> Tuple[bool, str, float]:
    """
    验证速度值
    返回: (是否有效, 错误信息, 调整后的速度)
    """
    if speed < MIN_SPEED or speed > MAX_SPEED:
        adjusted = max(MIN_SPEED, min(MAX_SPEED, speed))
        return True, f"速度已调整为 {adjusted}x（范围: {MIN_SPEED}-{MAX_SPEED}）", adjusted
    return True, "", speed


# ============================================================
# 命令执行工具
# ============================================================

def run_cmd(
    cmd: List[str],
    log_callback: Optional[Callable[[str], None]] = None,
    timeout: Optional[int] = None,
    progress_callback: Optional[Callable[[float], None]] = None
) -> Tuple[int, str]:
    """
    执行命令并实时回调输出

    Args:
        cmd: 命令列表
        log_callback: 日志回调函数
        timeout: 超时时间（秒）
        progress_callback: 进度回调函数（0-100）

    Returns:
        (返回码, 输出内容)
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags
        )
    except FileNotFoundError as e:
        error_msg = f"命令未找到: {cmd[0]}"
        logger.error(error_msg)
        if log_callback:
            log_callback(f"[错误] {error_msg}")
        return -1, error_msg
    except PermissionError as e:
        error_msg = f"权限不足: {cmd[0]}"
        logger.error(error_msg)
        if log_callback:
            log_callback(f"[错误] {error_msg}")
        return -1, error_msg

    output_lines = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            output_lines.append(line)
            if log_callback:
                log_callback(line)

            # 尝试解析进度（用于yt-dlp）
            if progress_callback:
                _parse_progress(line, progress_callback)

        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        logger.warning("进程超时被终止")
        if log_callback:
            log_callback("[超时] 进程被终止")
    except KeyboardInterrupt:
        proc.kill()
        logger.info("用户中断操作")
        if log_callback:
            log_callback("[中断] 操作被用户取消")
        raise

    return proc.returncode, "\n".join(output_lines)


def _parse_progress(line: str, callback: Callable[[float], None]) -> None:
    """解析yt-dlp进度输出"""
    # 匹配 [download]  50.2% 格式
    match = re.search(r'\[download\]\s+(\d+\.?\d*)%', line)
    if match:
        try:
            progress = float(match.group(1))
            callback(progress)
        except ValueError:
            pass


def get_audio_duration(filepath: str) -> float:
    """
    获取音频文件时长(秒)

    Args:
        filepath: 音频文件路径

    Returns:
        时长（秒），失败返回0
    """
    if not os.path.exists(filepath):
        logger.warning(f"文件不存在: {filepath}")
        return 0

    cmd = [FFMPEG, "-i", filepath]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr or "")
        if m:
            duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            logger.debug(f"音频时长: {filepath} -> {duration:.2f}秒")
            return duration
    except subprocess.SubprocessError as e:
        logger.error(f"获取音频时长失败: {e}")
    except re.error as e:
        logger.error(f"正则表达式错误: {e}")

    return 0


# ============================================================
# 批量处理队列
# ============================================================

class DownloadTask:
    """下载任务"""

    def __init__(self, url: str, output_dir: str, chapter_minutes: int = DEFAULT_CHAPTER_MINUTES):
        self.url = url
        self.output_dir = output_dir
        self.chapter_minutes = chapter_minutes
        self.status = "pending"  # pending, downloading, processing, completed, failed, cancelled
        self.progress = 0.0
        self.files: List[str] = []
        self.chapters: List[str] = []
        self.error: Optional[str] = None
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

    @property
    def duration(self) -> Optional[float]:
        """获取任务耗时（秒）"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "url": self.url,
            "output_dir": self.output_dir,
            "chapter_minutes": self.chapter_minutes,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }


class BatchDownloadQueue:
    """批量下载队列"""

    def __init__(self):
        self.tasks: List[DownloadTask] = []
        self.is_running = False
        self.current_index = -1
        self._lock = threading.Lock()

    def add_task(self, url: str, output_dir: str, chapter_minutes: int = DEFAULT_CHAPTER_MINUTES) -> DownloadTask:
        """添加任务到队列"""
        task = DownloadTask(url, output_dir, chapter_minutes)
        with self._lock:
            self.tasks.append(task)
        return task

    def remove_task(self, index: int) -> bool:
        """移除任务"""
        with self._lock:
            if 0 <= index < len(self.tasks):
                task = self.tasks[index]
                if task.status in ("pending", "completed", "failed", "cancelled"):
                    self.tasks.pop(index)
                    return True
        return False

    def clear_completed(self) -> int:
        """清除已完成的任务"""
        with self._lock:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if t.status not in ("completed", "failed", "cancelled")]
            return before - len(self.tasks)

    def get_next_pending(self) -> Optional[DownloadTask]:
        """获取下一个待处理任务"""
        with self._lock:
            for task in self.tasks:
                if task.status == "pending":
                    return task
        return None

    @property
    def total(self) -> int:
        """总任务数"""
        return len(self.tasks)

    @property
    def completed(self) -> int:
        """已完成任务数"""
        return len([t for t in self.tasks if t.status == "completed"])

    @property
    def failed(self) -> int:
        """失败任务数"""
        return len([t for t in self.tasks if t.status == "failed"])

    @property
    def pending(self) -> int:
        """待处理任务数"""
        return len([t for t in self.tasks if t.status == "pending"])

    def to_list(self) -> List[dict]:
        """转换为字典列表"""
        return [t.to_dict() for t in self.tasks]

    def save_to_file(self, filepath: str) -> None:
        """保存队列到文件"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.to_list(), f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"保存队列失败: {e}")

    def load_from_file(self, filepath: str) -> bool:
        """从文件加载队列"""
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.tasks = []
                    for item in data:
                        task = DownloadTask(item['url'], item['output_dir'], item.get('chapter_minutes', DEFAULT_CHAPTER_MINUTES))
                        task.status = item.get('status', 'pending')
                        task.progress = item.get('progress', 0)
                        task.error = item.get('error')
                        if item.get('start_time'):
                            task.start_time = datetime.fromisoformat(item['start_time'])
                        if item.get('end_time'):
                            task.end_time = datetime.fromisoformat(item['end_time'])
                        self.tasks.append(task)
                    return True
        except (json.JSONDecodeError, IOError, KeyError) as e:
            logger.error(f"加载队列失败: {e}")
        return False


# 全局批量队列
batch_queue = BatchDownloadQueue()


# ============================================================
# 核心功能
# ============================================================

def download_bilibili(
    url: str,
    output_dir: str,
    log_callback: Callable[[str], None],
    progress_callback: Optional[Callable[[float], None]] = None
) -> List[str]:
    """
    下载B站合集音频

    Args:
        url: B站合集链接
        output_dir: 输出目录
        log_callback: 日志回调
        progress_callback: 进度回调

    Returns:
        下载的文件路径列表
    """
    log_callback(f"[下载] 开始下载: {url}")
    log_callback(f"[下载] 输出目录: {output_dir}")
    log_callback(f"[下载] 使用 yt-dlp: {' '.join(YTDLP_CMD)}")

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        log_callback(f"[错误] 无法创建输出目录: {e}")
        return []

    cmd = YTDLP_CMD + [
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", os.path.join(output_dir, "%(playlist_title)s_P%(playlist_index)02d.%(ext)s"),
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", DEFAULT_AUDIO_QUALITY,
        "--no-playlist",
        "--yes-playlist",
        "--concurrent-fragments", str(MAX_CONCURRENT_DOWNLOADS),
        "--retries", str(MAX_RETRIES),
        "--fragment-retries", str(MAX_RETRIES),
        url
    ]

    ret, out = run_cmd(cmd, log_callback, progress_callback=progress_callback)
    if ret != 0:
        log_callback(f"[警告] yt-dlp 返回码 {ret}")

    downloaded = sorted(glob.glob(os.path.join(output_dir, "*.mp3")))
    log_callback(f"[下载] 完成，共 {len(downloaded)} 个文件")

    # 保存最近使用的URL
    config_manager.add_recent_url(url)
    config_manager.save()

    return downloaded


def cut_by_fixed_duration(
    input_files: List[str],
    output_dir: str,
    minutes_per_chapter: int,
    log_callback: Callable[[str], None],
    start_chapter: int = 1
) -> List[str]:
    """
    固定时长切割：使用segment muxer批量分段

    Args:
        input_files: 输入文件列表
        output_dir: 输出目录
        minutes_per_chapter: 每章时长（分钟）
        log_callback: 日志回调
        start_chapter: 起始章节号

    Returns:
        输出文件路径列表
    """
    if not input_files:
        log_callback("[切割] 错误: 没有输入文件")
        return []

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        log_callback(f"[错误] 无法创建输出目录: {e}")
        return []

    chap_sec = minutes_per_chapter * 60
    chapter_num = start_chapter

    temp_dir = os.path.join(output_dir, "_temp_segments")
    try:
        os.makedirs(temp_dir, exist_ok=True)
    except OSError as e:
        log_callback(f"[错误] 无法创建临时目录: {e}")
        return []

    all_outputs = []

    for fp in input_files:
        if not os.path.exists(fp):
            log_callback(f"[警告] 文件不存在，跳过: {fp}")
            continue

        fname = os.path.splitext(os.path.basename(fp))[0]
        seg_pattern = os.path.join(temp_dir, f"{fname}_seg_%04d.mp3")

        log_callback(f"[切割] 处理: {os.path.basename(fp)}")

        cmd = [
            FFMPEG, "-y", "-i", fp, "-vn",
            "-acodec", "libmp3lame", "-q:a", "2",
            "-f", "segment", "-segment_time", str(chap_sec),
            "-reset_timestamps", "1",
            seg_pattern
        ]

        ret, _ = run_cmd(cmd, log_callback)
        if ret != 0:
            log_callback(f"[警告] ffmpeg 切割失败: {os.path.basename(fp)}")
            continue

        seg_files = sorted(glob.glob(os.path.join(temp_dir, f"{fname}_seg_*.mp3")))
        log_callback(f"[切割] 产生 {len(seg_files)} 个分段")

        for sf in seg_files:
            try:
                if os.path.getsize(sf) > MIN_FILE_SIZE_BYTES:
                    output_name = f"第{chapter_num:04d}章.mp3"
                    output_path = os.path.join(output_dir, output_name)
                    shutil.copy2(sf, output_path)
                    all_outputs.append(output_path)
                    chapter_num += 1
            except OSError as e:
                log_callback(f"[警告] 处理分段失败: {e}")
            finally:
                try:
                    os.remove(sf)
                except OSError:
                    pass

    # 清理临时目录
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except OSError as e:
        logger.warning(f"清理临时目录失败: {e}")

    log_callback(f"[切割] 完成，共 {len(all_outputs)} 个章节文件")
    return all_outputs


def change_speed(
    input_dir: str,
    output_dir: str,
    speed: float,
    group_chapters: int,
    log_callback: Callable[[str], None],
    progress_callback: Optional[Callable[[float], None]] = None
) -> List[str]:
    """
    变速处理：atempo滤镜，不变调

    Args:
        input_dir: 输入目录
        output_dir: 输出目录
        speed: 变速倍数
        group_chapters: 合并章节数（0表示不合并）
        log_callback: 日志回调
        progress_callback: 进度回调

    Returns:
        输出文件路径列表
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        log_callback(f"[错误] 无法创建输出目录: {e}")
        return []

    # 验证并调整速度
    valid, msg, speed = validate_speed(speed)
    if msg:
        log_callback(f"[变速] {msg}")

    input_files = sorted(glob.glob(os.path.join(input_dir, "*.mp3")))
    if not input_files:
        log_callback("[变速] 错误: 源目录中没有找到MP3文件")
        return []

    log_callback(f"[变速] 找到 {len(input_files)} 个文件, 倍速: {speed}x")
    if group_chapters > 0:
        log_callback(f"[变速] 合并: 每{group_chapters}章")

    temp_dir = os.path.join(output_dir, "_temp_speed")
    try:
        os.makedirs(temp_dir, exist_ok=True)
    except OSError as e:
        log_callback(f"[错误] 无法创建临时目录: {e}")
        return []

    all_outputs = []
    total = len(input_files)

    for i, fp in enumerate(input_files):
        fname = os.path.basename(fp)
        temp_out = os.path.join(temp_dir, fname)

        # 检查是否已处理（断点续传）
        if os.path.exists(temp_out) and os.path.getsize(temp_out) > MIN_FILE_SIZE_BYTES:
            all_outputs.append(temp_out)
            if progress_callback:
                progress_callback((i + 1) * 100 / total)
            continue

        # 构建atempo滤镜链
        remaining = speed
        atempo_chain = []
        while remaining > 2.0:
            atempo_chain.append("atempo=2.0")
            remaining /= 2.0
        atempo_chain.append(f"atempo={remaining:.3f}")

        filter_str = ",".join(atempo_chain)

        cmd = [
            FFMPEG, "-y", "-i", fp,
            "-filter:a", filter_str,
            "-vn",
            "-acodec", "libmp3lame", "-q:a", "2",
            temp_out
        ]

        ret, _ = run_cmd(cmd, log_callback)

        if os.path.exists(temp_out) and os.path.getsize(temp_out) > MIN_FILE_SIZE_BYTES:
            all_outputs.append(temp_out)
        else:
            log_callback(f"[变速] 失败: {fname}")

        # 更新进度
        if progress_callback:
            progress_callback((i + 1) * 100 / total)

        progress = (i + 1) * 100 // total
        if progress % 20 == 0:
            log_callback(f"[变速] 进度: {progress}% ({i+1}/{total})")

    # 合并或复制
    final_outputs = []
    if group_chapters <= 0:
        # 不合并，直接复制
        for fp in all_outputs:
            dest = os.path.join(output_dir, os.path.basename(fp))
            try:
                shutil.copy2(fp, dest)
                final_outputs.append(dest)
            except OSError as e:
                log_callback(f"[警告] 复制文件失败: {e}")
    else:
        # 按组合并
        groups = [all_outputs[i:i+group_chapters] for i in range(0, len(all_outputs), group_chapters)]
        for gi, group in enumerate(groups):
            if len(group) == 1:
                dest = os.path.join(output_dir, os.path.basename(group[0]))
                try:
                    shutil.copy2(group[0], dest)
                    final_outputs.append(dest)
                except OSError as e:
                    log_callback(f"[警告] 复制文件失败: {e}")
            else:
                list_file = os.path.join(temp_dir, f"_merge_{gi}.txt")
                try:
                    with open(list_file, "w", encoding="utf-8") as f:
                        for g in group:
                            f.write(f"file '{g}'\n")

                    first_name = os.path.basename(group[0])
                    dest = os.path.join(output_dir, first_name)
                    cmd = [
                        FFMPEG, "-y", "-f", "concat", "-safe", "0",
                        "-i", list_file, "-c", "copy", dest
                    ]
                    ret, _ = run_cmd(cmd, log_callback)

                    if os.path.exists(dest) and os.path.getsize(dest) > MIN_FILE_SIZE_BYTES:
                        final_outputs.append(dest)
                    else:
                        log_callback(f"[合并] 失败: group {gi+1}")
                except IOError as e:
                    log_callback(f"[错误] 创建合并列表失败: {e}")

    # 清理临时目录
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except OSError as e:
        logger.warning(f"清理临时目录失败: {e}")

    log_callback(f"[变速] 完成，输出 {len(final_outputs)} 个文件")
    return final_outputs


# ============================================================
# GUI
# ============================================================

class AudiobookApp:
    """B站有声书工具主应用"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"B站有声书工具 v{VERSION}")
        self.root.geometry("720x650")
        self.root.minsize(620, 520)

        # 加载配置
        self.config = config_manager

        # 初始化变量
        self._init_variables()

        # 构建UI
        self.build_ui()

        # 绑定关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _init_variables(self) -> None:
        """初始化界面变量"""
        # 下载与切割
        self.download_url = tk.StringVar()
        self.cut_mode = tk.StringVar(value="fixed")
        self.chapter_minutes = tk.IntVar(value=self.config.get("chapter_minutes", DEFAULT_CHAPTER_MINUTES))
        self.novel_path = tk.StringVar()
        self.output_dir = tk.StringVar(value=self.config.get("output_dir", str(Path.home() / DEFAULT_OUTPUT_DIR)))
        self.delete_original = tk.BooleanVar(value=self.config.get("delete_original", False))

        # 变速处理
        self.speed_input_dir = tk.StringVar()
        self.speed_value = tk.DoubleVar(value=self.config.get("default_speed", DEFAULT_SPEED))
        self.speed_group = tk.IntVar(value=0)
        self.speed_output_dir = tk.StringVar()

        # 进度状态
        self.is_downloading = False
        self.is_processing = False
        self.batch_is_running = False

        # 加载批量队列
        queue_file = os.path.join(get_exe_dir(), "config", "batch_queue.json")
        batch_queue.load_from_file(queue_file)

    def build_ui(self) -> None:
        """构建主界面"""
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        # 标签页1: 下载与切割
        tab1 = ttk.Frame(notebook)
        notebook.add(tab1, text="下载与切割")
        self.build_tab1(tab1)

        # 标签页2: 变速处理
        tab2 = ttk.Frame(notebook)
        notebook.add(tab2, text="变速处理")
        self.build_tab2(tab2)

        # 标签页3: 日志
        tab3 = ttk.Frame(notebook)
        notebook.add(tab3, text="日志")
        self.build_tab3(tab3)

        # 标签页4: 批量处理
        tab4 = ttk.Frame(notebook)
        notebook.add(tab4, text="批量处理")
        self.build_tab4(tab4)

        # 标签页5: 历史记录
        tab5 = ttk.Frame(notebook)
        notebook.add(tab5, text="历史记录")
        self.build_tab5(tab5)

    def build_tab1(self, parent: ttk.Frame) -> None:
        """构建下载与切割标签页"""
        pad = {"padx": 10, "pady": 5}
        r = 0

        # URL输入
        ttk.Label(parent, text="B站合集链接:").grid(row=r, column=0, sticky="w", **pad)
        url_frame = ttk.Frame(parent)
        url_frame.grid(row=r, column=1, columnspan=2, sticky="ew", **pad)
        ttk.Entry(url_frame, textvariable=self.download_url, width=50).pack(side="left", fill="x", expand=True)
        ttk.Button(url_frame, text="粘贴", command=self._paste_url, width=6).pack(side="left", padx=5)

        # 切割模式
        r += 1
        ttk.Label(parent, text="切割模式:").grid(row=r, column=0, sticky="w", **pad)
        ttk.Radiobutton(parent, text="固定时长", variable=self.cut_mode, value="fixed",
                       command=self._on_cut_mode).grid(row=r, column=1, sticky="w", **pad)
        ttk.Radiobutton(parent, text="章节对齐(需TXT+Whisper)", variable=self.cut_mode, value="novel",
                       command=self._on_cut_mode).grid(row=r, column=2, sticky="w", **pad)

        # 固定时长选项
        self.fixed_frame = ttk.Frame(parent)
        self.fixed_frame.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(self.fixed_frame, text="每章时长:").pack(side="left")
        ttk.Spinbox(self.fixed_frame, from_=1, to=60, textvariable=self.chapter_minutes, width=5).pack(side="left", padx=5)
        ttk.Label(self.fixed_frame, text="分钟").pack(side="left")

        # 小说文件选项
        self.novel_frame = ttk.Frame(parent)
        self.novel_frame.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(self.novel_frame, text="小说TXT:").pack(side="left")
        ttk.Entry(self.novel_frame, textvariable=self.novel_path, width=40).pack(side="left", padx=5)
        ttk.Button(self.novel_frame, text="选择", command=self._sel_novel).pack(side="left")

        self._on_cut_mode()

        # 输出目录
        r = 4
        ttk.Label(parent, text="输出目录:").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(parent, textvariable=self.output_dir, width=45).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(parent, text="选择", command=self._sel_outdir).grid(row=r, column=2, **pad)

        # 选项
        r += 1
        ttk.Checkbutton(parent, text="下载完成后删除原始音频文件", variable=self.delete_original).grid(
            row=r, column=0, columnspan=3, sticky="w", **pad)

        # 按钮
        r += 1
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=r, column=0, columnspan=3, pady=15)
        self.dl_btn = ttk.Button(btn_frame, text="开始下载并切割", command=self._start_dl)
        self.dl_btn.pack(side="left", padx=5)
        ttk.Button(btn_frame, text="取消", command=self._cancel_dl).pack(side="left", padx=5)

        # 进度条
        r += 1
        progress_frame = ttk.Frame(parent)
        progress_frame.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.dl_pb = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.dl_pb.pack(fill="x", expand=True)

        # 状态
        r += 1
        self.dl_status = ttk.Label(parent, text="就绪", foreground="gray")
        self.dl_status.grid(row=r, column=0, columnspan=3, sticky="w", **pad)

        parent.columnconfigure(1, weight=1)

    def build_tab2(self, parent: ttk.Frame) -> None:
        """构建变速处理标签页"""
        pad = {"padx": 10, "pady": 5}
        r = 0

        # 源文件夹
        ttk.Label(parent, text="源文件夹:").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(parent, textvariable=self.speed_input_dir, width=45).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(parent, text="选择", command=self._sel_src).grid(row=r, column=2, **pad)

        # 倍速滑块
        r += 1
        ttk.Label(parent, text="倍速:").grid(row=r, column=0, sticky="w", **pad)
        self.speed_slider = ttk.Scale(parent, from_=MIN_SPEED, to=MAX_SPEED, variable=self.speed_value,
                                       orient="horizontal", command=self._on_slide)
        self.speed_slider.grid(row=r, column=1, sticky="ew", **pad)
        self.speed_label = ttk.Label(parent, text=f"{self.speed_value.get():.2f}x")
        self.speed_label.grid(row=r, column=2, sticky="w", **pad)

        # 精确倍速输入
        r += 1
        ttk.Label(parent, text="精确倍速:").grid(row=r, column=0, sticky="w", **pad)
        speed_input_frame = ttk.Frame(parent)
        speed_input_frame.grid(row=r, column=1, sticky="w", **pad)
        self.speed_entry = ttk.Entry(speed_input_frame, width=10)
        self.speed_entry.insert(0, str(DEFAULT_SPEED))
        self.speed_entry.pack(side="left")
        ttk.Button(speed_input_frame, text="应用", command=self._on_speed_entry).pack(side="left", padx=10)
        ttk.Label(speed_input_frame, text=f"范围: {MIN_SPEED}-{MAX_SPEED}x").pack(side="left", padx=10)

        # 合并选项
        r += 1
        ttk.Label(parent, text="合并输出:").grid(row=r, column=0, sticky="w", **pad)
        gf = ttk.Frame(parent)
        gf.grid(row=r, column=1, columnspan=2, sticky="w", **pad)
        ttk.Radiobutton(gf, text="每章独立", variable=self.speed_group, value=0).pack(side="left")
        ttk.Radiobutton(gf, text="每", variable=self.speed_group, value=3).pack(side="left")
        ttk.Spinbox(gf, from_=2, to=50, textvariable=self.speed_group, width=4).pack(side="left")
        ttk.Label(gf, text="章合并").pack(side="left")

        # 输出目录
        r += 1
        ttk.Label(parent, text="输出目录:").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(parent, textvariable=self.speed_output_dir, width=45).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(parent, text="选择", command=self._sel_dst).grid(row=r, column=2, **pad)

        # 按钮
        r += 1
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=r, column=0, columnspan=3, pady=15)
        self.sp_btn = ttk.Button(btn_frame, text="开始变速", command=self._start_sp)
        self.sp_btn.pack(side="left", padx=5)
        ttk.Button(btn_frame, text="取消", command=self._cancel_sp).pack(side="left", padx=5)

        # 进度条
        r += 1
        progress_frame = ttk.Frame(parent)
        progress_frame.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.sp_pb = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.sp_pb.pack(fill="x", expand=True)

        # 状态
        r += 1
        self.sp_status = ttk.Label(parent, text="就绪", foreground="gray")
        self.sp_status.grid(row=r, column=0, columnspan=3, sticky="w", **pad)

        parent.columnconfigure(1, weight=1)

    def build_tab3(self, parent: ttk.Frame) -> None:
        """构建日志标签页"""
        # 日志文本框
        self.log_text = scrolledtext.ScrolledText(parent, wrap=tk.WORD, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

        # 工具栏
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", padx=5, pady=2)
        ttk.Button(toolbar, text="清空日志", command=self._clear_log).pack(side="left")
        ttk.Button(toolbar, text="保存日志", command=self._save_log).pack(side="left", padx=5)

        # 初始化日志
        self._init_log()

    def build_tab4(self, parent: ttk.Frame) -> None:
        """构建批量处理标签页"""
        pad = {"padx": 10, "pady": 5}

        # 顶部输入区域
        input_frame = ttk.LabelFrame(parent, text="添加任务", padding=10)
        input_frame.pack(fill="x", padx=10, pady=5)

        # URL输入
        url_frame = ttk.Frame(input_frame)
        url_frame.pack(fill="x", pady=2)
        ttk.Label(url_frame, text="B站链接:").pack(side="left")
        self.batch_url = tk.StringVar()
        ttk.Entry(url_frame, textvariable=self.batch_url, width=50).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(url_frame, text="添加", command=self._add_batch_task).pack(side="left")
        ttk.Button(url_frame, text="从文件导入", command=self._import_batch_urls).pack(side="left", padx=5)

        # 输出目录
        dir_frame = ttk.Frame(input_frame)
        dir_frame.pack(fill="x", pady=2)
        ttk.Label(dir_frame, text="输出目录:").pack(side="left")
        self.batch_output_dir = tk.StringVar(value=str(Path.home() / DEFAULT_OUTPUT_DIR))
        ttk.Entry(dir_frame, textvariable=self.batch_output_dir, width=40).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(dir_frame, text="选择", command=self._sel_batch_outdir).pack(side="left")

        # 任务列表
        list_frame = ttk.LabelFrame(parent, text="任务队列", padding=10)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Treeview
        columns = ("序号", "URL", "状态", "进度")
        self.batch_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)

        for col in columns:
            self.batch_tree.heading(col, text=col)
        self.batch_tree.column("序号", width=50)
        self.batch_tree.column("URL", width=350)
        self.batch_tree.column("状态", width=80)
        self.batch_tree.column("进度", width=80)

        self.batch_tree.pack(fill="both", expand=True, side="left")

        # 滚动条
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.batch_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.batch_tree.configure(yscrollcommand=scrollbar.set)

        # 按钮区域
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Button(btn_frame, text="开始批量下载", command=self._start_batch_download).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="停止", command=self._stop_batch_download).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="移除选中", command=self._remove_batch_task).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="清除已完成", command=self._clear_completed_tasks).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="清空队列", command=self._clear_batch_queue).pack(side="left", padx=5)

        # 状态栏
        self.batch_status = ttk.Label(parent, text="就绪", foreground="gray")
        self.batch_status.pack(fill="x", padx=10, pady=5)

        # 进度条
        self.batch_pb = ttk.Progressbar(parent, mode="determinate", maximum=100)
        self.batch_pb.pack(fill="x", padx=10, pady=5)

        # 初始化
        self.batch_is_running = False
        self._refresh_batch_tree()

    def build_tab5(self, parent: ttk.Frame) -> None:
        """构建历史记录标签页"""
        # 历史记录列表
        columns = ("时间", "URL", "状态")
        self.history_tree = ttk.Treeview(parent, columns=columns, show="headings", height=15)

        for col in columns:
            self.history_tree.heading(col, text=col)
            self.history_tree.column(col, width=150)

        self.history_tree.pack(fill="both", expand=True, padx=5, pady=5)

        # 滚动条
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.history_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.history_tree.configure(yscrollcommand=scrollbar.set)

        # 工具栏
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", padx=5, pady=2)
        ttk.Button(toolbar, text="刷新", command=self._refresh_history).pack(side="left")
        ttk.Button(toolbar, text="清空历史", command=self._clear_history).pack(side="left", padx=5)

        # 加载历史记录
        self._refresh_history()

    # ---------- 初始化 ----------
    def _init_log(self) -> None:
        """初始化日志显示"""
        self.log_text.insert(tk.END, f"=== B站有声书工具 v{VERSION} ===\n")
        self.log_text.insert(tk.END, f"=== ffmpeg: {FFMPEG} ===\n")
        self.log_text.insert(tk.END, f"=== yt-dlp: {' '.join(YTDLP_CMD)} ===\n")
        self.log_text.insert(tk.END, f"=== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
        self.log_text.see(tk.END)

    # ---------- 回调 ----------
    def _on_cut_mode(self) -> None:
        """切换切割模式显示"""
        if self.cut_mode.get() == "fixed":
            self.fixed_frame.grid()
            self.novel_frame.grid_remove()
        else:
            self.fixed_frame.grid_remove()
            self.novel_frame.grid()

    def _on_slide(self, val: str) -> None:
        """滑块值改变"""
        self.speed_label.config(text=f"{float(val):.2f}x")

    def _on_speed_entry(self) -> None:
        """精确速度输入"""
        try:
            v = float(self.speed_entry.get())
            valid, msg, v = validate_speed(v)
            if msg:
                messagebox.showinfo("提示", msg)
            self.speed_value.set(v)
            self.speed_label.config(text=f"{v:.2f}x")
        except ValueError:
            messagebox.showwarning("提示", "请输入有效的数字")

    def _paste_url(self) -> None:
        """粘贴URL"""
        try:
            clipboard = self.root.clipboard_get()
            if clipboard:
                self.download_url.set(clipboard.strip())
        except tk.TclError:
            pass

    def _sel_novel(self) -> None:
        """选择小说文件"""
        p = filedialog.askopenfilename(filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if p:
            self.novel_path.set(p)

    def _sel_outdir(self) -> None:
        """选择输出目录"""
        p = filedialog.askdirectory()
        if p:
            self.output_dir.set(p)

    def _sel_src(self) -> None:
        """选择源目录"""
        p = filedialog.askdirectory()
        if p:
            self.speed_input_dir.set(p)

    def _sel_dst(self) -> None:
        """选择目标目录"""
        p = filedialog.askdirectory()
        if p:
            self.speed_output_dir.set(p)

    def log(self, msg: str) -> None:
        """添加日志"""
        def _log():
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
            self.log_text.see(tk.END)
        self.root.after(0, _log)

    def _clear_log(self) -> None:
        """清空日志"""
        self.log_text.delete(1.0, tk.END)
        self._init_log()

    def _save_log(self) -> None:
        """保存日志"""
        p = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile=f"日志_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if p:
            try:
                with open(p, 'w', encoding='utf-8') as f:
                    f.write(self.log_text.get(1.0, tk.END))
                messagebox.showinfo("成功", f"日志已保存到: {p}")
            except IOError as e:
                messagebox.showerror("错误", f"保存日志失败: {e}")

    def _refresh_history(self) -> None:
        """刷新历史记录"""
        # 清空现有项目
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)

        # 加载历史URL
        urls = self.config.get("last_urls", [])
        for url in urls:
            self.history_tree.insert("", "end", values=(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url, "已记录"))

    def _clear_history(self) -> None:
        """清空历史记录"""
        if messagebox.askyesno("确认", "确定要清空所有历史记录吗？"):
            self.config.set("last_urls", [])
            self.config.save()
            self._refresh_history()

    # ---------- 下载线程 ----------
    def _start_dl(self) -> None:
        """开始下载"""
        if self.is_downloading:
            messagebox.showwarning("提示", "正在下载中，请等待完成")
            return

        url = self.download_url.get().strip()

        # 验证URL
        valid, msg = validate_bilibili_url(url)
        if not valid:
            messagebox.showwarning("提示", msg)
            return

        # 验证输出目录
        output_dir = self.output_dir.get()
        valid, msg = validate_directory(output_dir, "输出目录")
        if not valid:
            messagebox.showwarning("提示", msg)
            return

        # 更新状态
        self.is_downloading = True
        self.dl_btn.config(state="disabled")
        self.dl_pb['value'] = 0
        self.dl_status.config(text="正在下载...", foreground="blue")

        # 保存配置
        self.config.set("chapter_minutes", self.chapter_minutes.get())
        self.config.set("output_dir", output_dir)
        self.config.set("delete_original", self.delete_original.get())
        self.config.save()

        # 启动下载线程
        threading.Thread(target=self._dl_thread, args=(url,), daemon=True).start()

    def _cancel_dl(self) -> None:
        """取消下载"""
        if self.is_downloading:
            if messagebox.askyesno("确认", "确定要取消当前下载吗？"):
                self.is_downloading = False
                self.log("[下载] 用户取消操作")

    def _dl_thread(self, url: str) -> None:
        """下载线程"""
        try:
            out = self.output_dir.get()
            raw_dir = os.path.join(out, "_raw")
            ch_dir = os.path.join(out, "章节")

            self.log("=== 开始下载 ===")

            # 定义进度回调
            def progress_callback(progress: float):
                if not self.is_downloading:
                    raise KeyboardInterrupt("用户取消")
                self.root.after(0, lambda p=progress: self.dl_pb.configure(value=p))
                self.root.after(0, lambda p=progress: self.dl_status.config(text=f"下载中... {p:.1f}%"))

            files = download_bilibili(url, raw_dir, self.log, progress_callback)

            if not self.is_downloading:
                self._dl_end("下载已取消", "orange")
                return

            if not files:
                self._dl_end("下载失败或无文件", "red")
                return

            self.log("=== 开始切割 ===")
            self.root.after(0, lambda: self.dl_status.config(text="正在切割..."))
            chapters = cut_by_fixed_duration(files, ch_dir, self.chapter_minutes.get(), self.log)

            if self.delete_original.get():
                self.log("=== 清理原始文件 ===")
                try:
                    shutil.rmtree(raw_dir, ignore_errors=True)
                    self.log("原始文件已删除")
                except OSError as e:
                    self.log(f"清理失败: {e}")

            self.log(f"=== 完成 === 输出 {len(chapters)} 个章节 → {ch_dir}")
            self._dl_end(f"完成！{len(chapters)} 个章节 → {ch_dir}", "green")

            # 记录历史
            self.config.add_recent_url(url)
            self.config.save()
            self.root.after(0, self._refresh_history)

        except KeyboardInterrupt:
            self._dl_end("下载已取消", "orange")
        except Exception as e:
            logger.exception("下载过程出错")
            self.log(f"错误: {e}")
            self._dl_end(f"错误: {e}", "red")
        finally:
            self.is_downloading = False

    def _dl_end(self, msg: str, color: str) -> None:
        """下载结束"""
        def _update():
            self.dl_status.config(text=msg, foreground=color)
            self.dl_pb['value'] = 100 if color == "green" else 0
            self.dl_btn.config(state="normal")
        self.root.after(0, _update)

    # ---------- 变速线程 ----------
    def _start_sp(self) -> None:
        """开始变速处理"""
        if self.is_processing:
            messagebox.showwarning("提示", "正在处理中，请等待完成")
            return

        src = self.speed_input_dir.get()

        # 验证源目录
        valid, msg = validate_directory(src, "源文件夹")
        if not valid:
            messagebox.showwarning("提示", msg)
            return

        # 验证输出目录
        dst = self.speed_output_dir.get() or os.path.join(src, f"_{self.speed_value.get():.1f}x加速")
        valid, msg = validate_directory(dst, "输出目录")
        if not valid:
            messagebox.showwarning("提示", msg)
            return

        # 更新状态
        self.is_processing = True
        self.sp_btn.config(state="disabled")
        self.sp_pb['value'] = 0
        self.sp_status.config(text="正在变速...", foreground="blue")

        # 启动处理线程
        threading.Thread(target=self._sp_thread, daemon=True).start()

    def _cancel_sp(self) -> None:
        """取消变速处理"""
        if self.is_processing:
            if messagebox.askyesno("确认", "确定要取消当前处理吗？"):
                self.is_processing = False
                self.log("[变速] 用户取消操作")

    def _sp_thread(self) -> None:
        """变速处理线程"""
        try:
            src = self.speed_input_dir.get()
            dst = self.speed_output_dir.get() or os.path.join(src, f"_{self.speed_value.get():.1f}x加速")
            speed = self.speed_value.get()
            grp = self.speed_group.get()

            self.log("=== 开始变速 ===")

            # 定义进度回调
            def progress_callback(progress: float):
                if not self.is_processing:
                    raise KeyboardInterrupt("用户取消")
                self.root.after(0, lambda p=progress: self.sp_pb.configure(value=p))
                self.root.after(0, lambda p=progress: self.sp_status.config(text=f"变速中... {p:.1f}%"))

            outputs = change_speed(src, dst, speed, grp, self.log, progress_callback)

            if not self.is_processing:
                self._sp_end("处理已取消", "orange")
                return

            self.log(f"=== 完成 === 输出 {len(outputs)} 个文件 → {dst}")
            self._sp_end(f"完成！{len(outputs)} 个文件 → {dst}", "green")

        except KeyboardInterrupt:
            self._sp_end("处理已取消", "orange")
        except Exception as e:
            logger.exception("变速处理出错")
            self.log(f"错误: {e}")
            self._sp_end(f"错误: {e}", "red")
        finally:
            self.is_processing = False

    def _sp_end(self, msg: str, color: str) -> None:
        """变速处理结束"""
        def _update():
            self.sp_status.config(text=msg, foreground=color)
            self.sp_pb['value'] = 100 if color == "green" else 0
            self.sp_btn.config(state="normal")
        self.root.after(0, _update)

    # ---------- 批量处理 ----------
    def _add_batch_task(self) -> None:
        """添加批量任务"""
        url = self.batch_url.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入B站链接")
            return

        valid, msg = validate_bilibili_url(url)
        if not valid:
            messagebox.showwarning("提示", msg)
            return

        output_dir = self.batch_output_dir.get()
        valid, msg = validate_directory(output_dir, "输出目录")
        if not valid:
            messagebox.showwarning("提示", msg)
            return

        task = batch_queue.add_task(url, output_dir, self.chapter_minutes.get())
        self.batch_url.set("")
        self._refresh_batch_tree()
        self.log(f"[批量] 添加任务: {url}")

    def _import_batch_urls(self) -> None:
        """从文件导入URL"""
        p = filedialog.askopenfilename(
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if not p:
            return

        try:
            with open(p, 'r', encoding='utf-8') as f:
                urls = [line.strip() for line in f if line.strip()]

            output_dir = self.batch_output_dir.get()
            valid, msg = validate_directory(output_dir, "输出目录")
            if not valid:
                messagebox.showwarning("提示", msg)
                return

            count = 0
            for url in urls:
                valid, _ = validate_bilibili_url(url)
                if valid:
                    batch_queue.add_task(url, output_dir, self.chapter_minutes.get())
                    count += 1

            self._refresh_batch_tree()
            self.log(f"[批量] 从文件导入 {count} 个有效URL")
            messagebox.showinfo("成功", f"成功导入 {count} 个URL")
        except IOError as e:
            messagebox.showerror("错误", f"读取文件失败: {e}")

    def _sel_batch_outdir(self) -> None:
        """选择批量输出目录"""
        p = filedialog.askdirectory()
        if p:
            self.batch_output_dir.set(p)

    def _remove_batch_task(self) -> None:
        """移除选中的批量任务"""
        selected = self.batch_tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要移除的任务")
            return

        for item in selected:
            index = self.batch_tree.index(item)
            if batch_queue.remove_task(index):
                self.log(f"[批量] 移除任务 #{index + 1}")

        self._refresh_batch_tree()

    def _clear_completed_tasks(self) -> None:
        """清除已完成的任务"""
        count = batch_queue.clear_completed()
        if count > 0:
            self.log(f"[批量] 清除 {count} 个已完成任务")
            self._refresh_batch_tree()

    def _clear_batch_queue(self) -> None:
        """清空批量队列"""
        if messagebox.askyesno("确认", "确定要清空所有任务吗？"):
            batch_queue.tasks.clear()
            self._refresh_batch_tree()
            self.log("[批量] 队列已清空")

    def _refresh_batch_tree(self) -> None:
        """刷新批量任务列表"""
        for item in self.batch_tree.get_children():
            self.batch_tree.delete(item)

        for i, task in enumerate(batch_queue.tasks):
            status_text = {
                "pending": "等待中",
                "downloading": "下载中",
                "processing": "处理中",
                "completed": "已完成",
                "failed": "失败",
                "cancelled": "已取消"
            }.get(task.status, task.status)

            self.batch_tree.insert("", "end", values=(
                i + 1,
                task.url[:60] + "..." if len(task.url) > 60 else task.url,
                status_text,
                f"{task.progress:.1f}%"
            ))

    def _start_batch_download(self) -> None:
        """开始批量下载"""
        if self.batch_is_running:
            messagebox.showwarning("提示", "批量下载正在运行中")
            return

        if batch_queue.pending == 0:
            messagebox.showwarning("提示", "没有待处理的任务")
            return

        self.batch_is_running = True
        self.batch_status.config(text="批量下载中...", foreground="blue")
        threading.Thread(target=self._batch_download_thread, daemon=True).start()

    def _stop_batch_download(self) -> None:
        """停止批量下载"""
        if self.batch_is_running:
            if messagebox.askyesno("确认", "确定要停止批量下载吗？"):
                self.batch_is_running = False
                self.log("[批量] 用户停止批量下载")

    def _batch_download_thread(self) -> None:
        """批量下载线程"""
        try:
            total = batch_queue.total
            completed = 0

            while self.batch_is_running:
                task = batch_queue.get_next_pending()
                if not task:
                    break

                task.status = "downloading"
                task.start_time = datetime.now()
                self.root.after(0, self._refresh_batch_tree)

                self.log(f"[批量] 开始下载: {task.url}")

                # 定义进度回调
                def progress_callback(progress: float, t=task):
                    if not self.batch_is_running:
                        raise KeyboardInterrupt("用户停止")
                    t.progress = progress
                    self.root.after(0, self._refresh_batch_tree)
                    self.root.after(0, lambda p=progress: self.batch_pb.configure(value=p))

                try:
                    raw_dir = os.path.join(task.output_dir, "_raw")
                    files = download_bilibili(task.url, raw_dir, self.log, progress_callback)

                    if not self.batch_is_running:
                        task.status = "cancelled"
                        break

                    if files:
                        task.status = "processing"
                        self.root.after(0, self._refresh_batch_tree)

                        ch_dir = os.path.join(task.output_dir, "章节")
                        chapters = cut_by_fixed_duration(files, ch_dir, task.chapter_minutes, self.log)

                        task.files = files
                        task.chapters = chapters
                        task.status = "completed"
                        task.end_time = datetime.now()
                        completed += 1

                        self.log(f"[批量] 完成: {task.url} -> {len(chapters)} 章节")
                    else:
                        task.status = "failed"
                        task.error = "下载失败"
                        task.end_time = datetime.now()

                except Exception as e:
                    task.status = "failed"
                    task.error = str(e)
                    task.end_time = datetime.now()
                    self.log(f"[批量] 失败: {task.url} - {e}")

                self.root.after(0, self._refresh_batch_tree)

                # 更新总进度
                overall_progress = (completed / total) * 100
                self.root.after(0, lambda p=overall_progress: self.batch_pb.configure(value=p))

            # 完成
            self.root.after(0, lambda: self.batch_status.config(
                text=f"批量下载完成: {completed}/{total}", foreground="green"))

        except Exception as e:
            logger.exception("批量下载出错")
            self.root.after(0, lambda: self.batch_status.config(
                text=f"批量下载出错: {e}", foreground="red"))
        finally:
            self.batch_is_running = False
            self.root.after(0, self._refresh_batch_tree)

    # ---------- 窗口事件 ----------
    def _on_closing(self) -> None:
        """窗口关闭事件"""
        if self.is_downloading or self.is_processing or self.batch_is_running:
            if messagebox.askyesno("确认", "有任务正在运行，确定要退出吗？"):
                self.is_downloading = False
                self.is_processing = False
                self.batch_is_running = False
                self.root.destroy()
        else:
            # 保存配置
            self.config.save()
            # 保存批量队列
            queue_file = os.path.join(get_exe_dir(), "config", "batch_queue.json")
            batch_queue.save_to_file(queue_file)
            self.root.destroy()


# ============================================================
# 入口
# ============================================================

def main() -> None:
    """主入口函数"""
    root = tk.Tk()

    # 设置图标（如果存在）
    icon_path = os.path.join(get_exe_dir(), "icon.ico")
    if os.path.exists(icon_path):
        try:
            root.iconbitmap(icon_path)
        except tk.TclError:
            pass

    app = AudiobookApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
