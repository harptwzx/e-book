import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk, messagebox
from ebooklib import epub
from bs4 import BeautifulSoup
from PIL import Image, ImageTk
import io
import os
import posixpath
import html
import requests
import threading
import ebooklib
import time
import webbrowser
import urllib.parse
import queue
from ttkthemes import ThemedTk
import datetime
import re
import gc
import concurrent.futures
import functools
import hashlib
import weakref

# 缓存装饰器，用于缓存耗时操作的结果
def memoize(maxsize=128):
    def decorator(func):
        cache = {}
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            
            # 使用弱引用避免内存泄漏
            if key in cache:
                result = cache[key]()
                if result is not None:
                    return result
            
            result = func(*args, **kwargs)
            if len(cache) >= maxsize:
                cache.popitem(last=False)
            
            # 使用弱引用存储结果
            cache[key] = weakref.ref(result)
            return result
        
        return wrapper
    return decorator

# 图像缓存类
class ImageCache:
    def __init__(self, max_size=50):
        self.cache = {}
        self.max_size = max_size
        self.access_counter = {}
        self.counter = 0
        
    def get(self, key, chapter_dir, text_width):
        # 生成复合键，包含文本宽度以适应不同尺寸
        composite_key = (key, text_width)
        
        if composite_key in self.cache:
            self.access_counter[composite_key] = self.counter
            self.counter += 1
            return self.cache[composite_key]
        return None
        
    def put(self, key, value, chapter_dir, text_width):
        composite_key = (key, text_width)
        
        if len(self.cache) >= self.max_size:
            # 找到最近最少使用的项目
            lru_key = min(self.access_counter, key=self.access_counter.get)
            del self.cache[lru_key]
            del self.access_counter[lru_key]
        
        self.cache[composite_key] = value
        self.access_counter[composite_key] = self.counter
        self.counter += 1

class EPubReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EPub Reader Pro (优化版)")
        self.root.geometry("1200x850")
        self.root.minsize(800, 600)
        
        # 添加全屏切换支持
        self.fullscreen = False
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False) if self.fullscreen else None)
        
        try:
            self.root.set_theme("arc")
        except:
            try:
                self.root.set_theme("clam")
            except:
                pass
        
        # 创建样式 - 修复按钮显示问题
        self.style = ttk.Style()
        # 移除按钮的背景色和前景色设置
        self.style.configure("TButton", padding=6, relief="flat", font=("Arial", 10, "bold"))
        self.style.configure("Title.TLabel", font=("Arial", 16, "bold"), foreground="#2c3e50")
        self.style.configure("Status.TLabel", font=("Arial", 10), foreground="#7f8c8d")
        self.style.configure("Treeview", font=("Arial", 11), rowheight=30, background="#f5f5f5")
        self.style.configure("Treeview.Heading", font=("Arial", 11, "bold"), background="#3498db", foreground="white")
        self.style.configure("Progress.Horizontal.TProgressbar", thickness=20, background="#2ecc71")
        self.style.map("Treeview.Heading", background=[('active', '#2980b9')])
        
        # 添加按钮状态映射 - 确保按钮文字始终可见
        self.style.map("TButton",
                      foreground=[('active', 'black'), ('!active', 'black')],
                      background=[('active', '#e0e0e0'), ('!active', '#f0f0f0')])
        
        # 创建主框架
        main_frame = ttk.Frame(root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 创建顶部标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))
        
        title_label = ttk.Label(title_frame, text="EPUB Reader Pro (优化版)", style="Title.TLabel")
        title_label.pack(side=tk.LEFT, padx=10)
        
        # 创建状态标签
        self.status_label = ttk.Label(title_frame, text="正在初始化...", style="Status.TLabel")
        self.status_label.pack(side=tk.RIGHT, padx=10)
        
        # 创建主面板
        self.paned_window = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True)
        
        # 左侧面板
        left_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(left_frame, weight=1)
        
        # 使用垂直PanedWindow分割搜索和书架区域
        self.left_paned = ttk.PanedWindow(left_frame, orient=tk.VERTICAL)
        self.left_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 搜索框架
        search_frame = ttk.LabelFrame(self.left_paned, text="在线电子书库")
        self.left_paned.add(search_frame, weight=2)
        
        # 创建搜索框架的网格布局
        search_frame.columnconfigure(0, weight=1)
        search_frame.rowconfigure(2, weight=1)
        
        # 搜索框
        search_container = ttk.Frame(search_frame)
        search_container.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        search_container.columnconfigure(0, weight=1)
        
        self.search_entry = ttk.Entry(search_container)
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.search_entry.insert(0, "")
        self.search_entry.bind("<KeyRelease>", self.filter_books)
        
        refresh_button = ttk.Button(search_container, text="刷新", command=self.refresh_book_list)
        refresh_button.grid(row=0, column=1, sticky="e")
        
        # 进度条
        progress_frame = ttk.Frame(search_frame)
        progress_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 5))
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            progress_frame, 
            variable=self.progress_var, 
            maximum=100, 
            mode='determinate',
            style="Progress.Horizontal.TProgressbar"
        )
        self.progress_bar.pack(fill=tk.X)
        
        # 搜索结果树状视图
        tree_frame = ttk.Frame(search_frame)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        
        # 修改：添加"书名"列
        self.search_tree = ttk.Treeview(
            tree_frame, 
            columns=("title", "size", "date"),  # 增加书名列
            show="headings",
            selectmode="browse"
        )
        # 设置列标题
        self.search_tree.heading("title", text="书名")
        self.search_tree.heading("size", text="大小")
        self.search_tree.heading("date", text="日期")
        
        # 设置列宽并允许调整
        self.search_tree.column("title", width=300, minwidth=250, stretch=tk.YES)
        self.search_tree.column("size", width=80, anchor=tk.CENTER, stretch=tk.NO)
        self.search_tree.column("date", width=100, anchor=tk.CENTER, stretch=tk.NO)
        
        self.search_tree.bind("<<TreeviewSelect>>", self.on_search_select)
        
        # 添加滚动条
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.search_tree.yview)
        self.search_tree.configure(yscrollcommand=scrollbar.set)
        
        self.search_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        
        # 下载按钮
        button_frame = ttk.Frame(search_frame)
        button_frame.grid(row=3, column=0, sticky="nsew", pady=(5, 0))
        button_frame.columnconfigure(0, weight=1)
        button_frame.columnconfigure(1, weight=1)  # 增加第二列的权重分配
        
        self.download_button = ttk.Button(
            button_frame, 
            text="下载选中书籍", 
            command=self.download_selected,
            state=tk.DISABLED
        )
        self.download_button.grid(row=0, column=0, sticky="w", padx=(0, 5))
        
        open_github_button = ttk.Button(
            button_frame, 
            text="访问GitHub", 
            command=lambda: webbrowser.open("https://github.com/harptwzx/e-book")
        )
        open_github_button.grid(row=0, column=1, sticky="e")
        
        # 书架框架
        bookshelf_frame = ttk.LabelFrame(self.left_paned, text="我的书架")
        self.left_paned.add(bookshelf_frame, weight=3)
        
        # 创建书架框架的网格布局
        bookshelf_frame.columnconfigure(0, weight=1)
        bookshelf_frame.rowconfigure(0, weight=1)
        
        # 书架树状视图
        bookshelf_tree_frame = ttk.Frame(bookshelf_frame)
        bookshelf_tree_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        bookshelf_tree_frame.columnconfigure(0, weight=1)
        bookshelf_tree_frame.rowconfigure(0, weight=1)
        
        # 修改：添加"书名"列
        self.bookshelf_tree = ttk.Treeview(
            bookshelf_tree_frame, 
            columns=("title", "size", "date"),  # 增加书名列
            show="headings",
            selectmode="browse"
        )
        # 设置列标题
        self.bookshelf_tree.heading("title", text="书名")
        self.bookshelf_tree.heading("size", text="大小")
        self.bookshelf_tree.heading("date", text="日期")
        
        # 设置列宽并允许调整
        self.bookshelf_tree.column("title", width=300, minwidth=250, stretch=tk.YES)
        self.bookshelf_tree.column("size", width=80, anchor=tk.CENTER, stretch=tk.NO)
        self.bookshelf_tree.column("date", width=100, anchor=tk.CENTER, stretch=tk.NO)
        
        self.bookshelf_tree.bind("<<TreeviewSelect>>", self.on_bookshelf_select)
        
        bookshelf_scrollbar = ttk.Scrollbar(bookshelf_tree_frame, orient=tk.VERTICAL, command=self.bookshelf_tree.yview)
        self.bookshelf_tree.configure(yscrollcommand=bookshelf_scrollbar.set)
        
        self.bookshelf_tree.grid(row=0, column=0, sticky="nsew")
        bookshelf_scrollbar.grid(row=0, column=1, sticky="ns")
        
        # 书架按钮
        bookshelf_btn_frame = ttk.Frame(bookshelf_frame)
        bookshelf_btn_frame.grid(row=1, column=0, sticky="nsew", pady=(5, 0))
        bookshelf_btn_frame.columnconfigure(0, weight=1)  # 左侧按钮区域
        bookshelf_btn_frame.columnconfigure(1, weight=1)  # 中间空白区域
        bookshelf_btn_frame.columnconfigure(2, weight=1)  # 右侧按钮区域
        
        # 左侧按钮容器
        left_btn_frame = ttk.Frame(bookshelf_btn_frame)
        left_btn_frame.grid(row=0, column=0, sticky="w")
        
        self.load_button = ttk.Button(
            left_btn_frame, 
            text="加载选中", 
            command=self.load_from_bookshelf,
            state=tk.DISABLED,
            width=12
        )
        self.load_button.pack(side=tk.LEFT, padx=(0, 5))
        
        self.remove_button = ttk.Button(
            left_btn_frame, 
            text="移除选中", 
            command=self.remove_from_bookshelf,
            state=tk.DISABLED,
            width=12
        )
        self.remove_button.pack(side=tk.LEFT)
        
        # 右侧按钮容器
        right_btn_frame = ttk.Frame(bookshelf_btn_frame)
        right_btn_frame.grid(row=0, column=2, sticky="e")
        
        load_local_button = ttk.Button(
            right_btn_frame, 
            text="加载本地EPUB", 
            command=lambda: self.load_epub(None),
            width=15
        )
        load_local_button.pack(side=tk.RIGHT)
        
        # 右侧面板
        right_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(right_frame, weight=4)
        
        # 阅读器控制面板
        control_frame = ttk.Frame(right_frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        control_frame.columnconfigure(0, weight=1)
        
        # 章节选择下拉菜单
        self.chapter_var = tk.StringVar()
        self.chapter_combo = ttk.Combobox(control_frame, textvariable=self.chapter_var, state="readonly", width=40)
        self.chapter_combo.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        self.chapter_combo.bind("<<ComboboxSelected>>", self.on_chapter_select)
        
        # 翻页按钮容器
        button_container = ttk.Frame(control_frame)
        button_container.grid(row=0, column=1, sticky="e")
        
        self.prev_button = ttk.Button(button_container, text="上一章", command=self.show_previous, state=tk.DISABLED, width=10)
        self.prev_button.pack(side=tk.LEFT, padx=(0, 5))
        
        # 页码标签
        self.page_label = ttk.Label(button_container, text="章节: 0/0")
        self.page_label.pack(side=tk.LEFT, padx=(0, 10))
        
        self.next_button = ttk.Button(button_container, text="下一章", command=self.show_next, state=tk.DISABLED, width=10)
        self.next_button.pack(side=tk.LEFT)
        
        # 文本区域框架
        text_frame = ttk.Frame(right_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        # 滚动文本框
        self.text_area = scrolledtext.ScrolledText(
            text_frame, 
            wrap=tk.WORD, 
            font=("Arial", 12),
            padx=15,
            pady=15,
            bg="#ffffff",
            relief="flat"
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)
        self.text_area.config(state=tk.DISABLED)
        self.text_area.tag_configure("center", justify='center')
        self.text_area.tag_configure("heading", font=("Arial", 16, "bold"), foreground="#2c3e50", spacing3=10)
        self.text_area.tag_configure("subheading", font=("Arial", 14, "bold"), foreground="#3498db", spacing3=8)
        self.text_area.tag_configure("chapter_title", font=("Arial", 14, "bold"), foreground="#e74c3c", spacing3=10)
        self.text_area.tag_configure("normal", font=("Arial", 12), lmargin1=20, lmargin2=20, rmargin=20)
        self.text_area.tag_configure("quote", font=("Arial", 11, "italic"), foreground="#7f8c8d", 
                                    lmargin1=30, lmargin2=30, rmargin=30, spacing1=5, spacing3=5)
        
        # 初始化变量
        self.book = None
        self.chapters = []
        self.chapter_titles = []
        self.current_chapter_index = 0
        self.book_title = ""
        self.image_references = []
        self.image_resources = {}
        self.ncx_toc = None
        self.bookshelf_dir = "bookshelf"
        self.github_url = "https://api.github.com/repos/harptwzx/e-book/contents/books"
        self.remote_books = []
        self.queue = queue.Queue()
        
        # 性能优化相关变量
        self.image_cache = ImageCache(max_size=50)  # 图片缓存
        self.last_text_width = 0  # 用于检测文本区域宽度变化
        self.resize_timer = None  # 窗口调整大小计时器
        self.chapter_cache = {}  # 章节内容缓存
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)  # 线程池
        self.active_threads = set()  # 跟踪活动线程
        self.loading_chapter = None  # 当前正在加载的章节
        
        # 创建书架目录
        if not os.path.exists(self.bookshelf_dir):
            os.makedirs(self.bookshelf_dir)
        
        # 显示欢迎信息
        self.show_welcome_message()
        
        # 启动后自动加载书籍列表
        self.root.after(100, self.start_book_loading)
        
        # 设置初始分割比例
        self.root.update()
        self.paned_window.sashpos(0, int(self.root.winfo_width() * 0.25))
        self.left_paned.sashpos(0, int(self.root.winfo_height() * 0.4))
        
        # 绑定窗口大小变化事件
        self.root.bind("<Configure>", self.on_window_resize)
        
        # 强制完成所有挂起的GUI更新
        self.root.update_idletasks()
        # 确保按钮可见
        self.ensure_buttons_visible()

    def on_window_resize(self, event):
        """窗口大小变化时调整布局 - 使用延迟重绘优化性能"""
        if event.widget == self.root:
            # 取消之前的计时器
            if self.resize_timer:
                self.root.after_cancel(self.resize_timer)
                
            # 设置新的计时器，延迟300ms后执行
            self.resize_timer = self.root.after(300, self.delayed_resize_handler)
            
            # 立即更新按钮可见性
            self.ensure_buttons_visible()

    def delayed_resize_handler(self):
        """延迟处理窗口大小变化"""
        # 调整主分割线位置
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        
        # 设置主分割线位置为窗口宽度的25%
        self.paned_window.sashpos(0, int(width * 0.25))
        
        # 设置左侧垂直分割线位置为窗口高度的40%
        self.left_paned.sashpos(0, int(height * 0.4))
        
        # 更新文本区域中的图片大小（如果文本区域宽度变化超过50像素）
        if self.chapters and self.current_chapter_index < len(self.chapters):
            current_width = self.text_area.winfo_width()
            if abs(current_width - self.last_text_width) > 50:
                self.last_text_width = current_width
                self.root.after(100, self.update_image_sizes)
                
        # 确保所有按钮可见
        self.ensure_buttons_visible()

    def ensure_buttons_visible(self):
        """确保所有按钮可见 - 优化按钮宽度"""
        # 设置最小按钮宽度
        min_button_width = 12
        
        # 搜索区域按钮
        self.download_button.config(width=max(15, min_button_width))
        
        # 书架区域按钮
        self.load_button.config(width=max(12, min_button_width))
        self.remove_button.config(width=max(12, min_button_width))
        
        # 翻页按钮
        self.prev_button.config(width=max(10, min_button_width))
        self.next_button.config(width=max(10, min_button_width))
        
        # 强制更新布局
        self.root.update_idletasks()

    def update_image_sizes(self):
        """更新文本区域中的图片大小 - 使用缓存优化性能"""
        if not self.chapters or self.current_chapter_index >= len(self.chapters):
            return
            
        # 获取当前章节索引
        index = self.current_chapter_index
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete(1.0, tk.END)
        
        # 重新显示当前章节
        self.show_chapter(index)
        self.text_area.config(state=tk.DISABLED)

    def toggle_fullscreen(self, event=None):
        """切换全屏模式 - 优化性能"""
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        if not self.fullscreen:
            self.root.geometry("1500x850")
        
        # 全屏时调整按钮大小
        self.ensure_buttons_visible()

    def start_book_loading(self):
        """启动书籍加载过程 - 使用线程池优化性能"""
        self.status_label.config(text="正在加载远程书籍列表...")
        self.progress_var.set(0)
        self.progress_bar.start()
        
        # 清空现有搜索结果
        for item in self.search_tree.get_children():
            self.search_tree.delete(item)
            
        # 在线程池中加载书籍
        future = self.executor.submit(self.load_book_list)
        future.add_done_callback(self.on_book_loading_complete)
        
        # 启动队列处理器
        self.root.after(100, self.process_queue)

    def on_book_loading_complete(self, future):
        """书籍加载完成后的回调"""
        try:
            future.result()
        except Exception as e:
            self.queue.put(("error", str(e)))

    def process_queue(self):
        """处理来自后台线程的消息队列"""
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "progress":
                    self.progress_var.set(msg[1])
                elif msg[0] == "book":
                    book = msg[1]
                    # 存储书籍信息
                    self.remote_books.append(book)
                    # 在树状视图中显示书籍
                    # 修改：使用书名列
                    self.search_tree.insert("", tk.END, values=(book["name"], book["size"], book["date"]))
                elif msg[0] == "done":
                    self.progress_bar.stop()
                    self.status_label.config(text=f"找到 {msg[1]} 本电子书")
                    self.refresh_bookshelf()
                    break
                elif msg[0] == "error":
                    self.progress_bar.stop()
                    self.status_label.config(text=f"错误: {msg[1]}")
                    messagebox.showerror("加载错误", f"无法获取书籍列表: {msg[1]}")
                    break
        except queue.Empty:
            self.root.after(100, self.process_queue)

    def load_book_list(self):
        """从GitHub加载书籍列表 - 优化请求性能"""
        try:
            # 获取GitHub仓库内容
            headers = {"User-Agent": "EPubReaderApp/1.0"}
            response = requests.get(self.github_url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # 过滤EPUB文件
            epub_files = [item for item in data if item["name"].lower().endswith(".epub")]
            total = len(epub_files)
            
            if total == 0:
                self.queue.put(("error", "未找到EPUB文件"))
                return
                
            # 处理每个文件
            for idx, item in enumerate(epub_files):
                # 转换文件大小
                size = item["size"]
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size/1024:.1f} KB"
                else:
                    size_str = f"{size/(1024*1024):.1f} MB"
                
                # 尝试获取日期，如果不可用则使用当前日期
                try:
                    # 尝试不同的日期字段
                    if "updated_at" in item:
                        date_str = item["updated_at"].split("T")[0]
                    elif "git_last_modified" in item:
                        date_str = item["git_last_modified"].split("T")[0]
                    else:
                        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                except:
                    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
                
                # 添加到队列
                self.queue.put(("book", {
                    "name": item["name"],
                    "size": size_str,
                    "date": date_str,
                    "download_url": item["download_url"]
                }))
                
                # 更新进度
                progress = (idx + 1) / total * 100
                self.queue.put(("progress", progress))
                time.sleep(0.01)  # 减少延迟以加快处理速度
            
            # 完成加载
            self.queue.put(("done", total))
            
        except Exception as e:
            self.queue.put(("error", str(e)))

    def filter_books(self, event=None):
        """根据搜索框内容过滤书籍 - 优化性能"""
        query = self.search_entry.get().strip().lower()
        
        # 如果没有查询，显示所有书籍
        if not query:
            for child in self.search_tree.get_children():
                self.search_tree.reattach(child, "", "end")
            return
            
        # 隐藏不匹配的书籍
        for child in self.search_tree.get_children():
            item_values = self.search_tree.item(child, "values")
            if item_values and len(item_values) > 0:
                item_text = item_values[0].lower()  # 书名是第一列
                if query in item_text:
                    self.search_tree.reattach(child, "", "end")
                else:
                    self.search_tree.detach(child)

    def refresh_book_list(self):
        """刷新书籍列表 - 优化性能"""
        self.remote_books = []
        self.start_book_loading()

    def show_welcome_message(self):
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete(1.0, tk.END)
        
        welcome_text = """
        📚 EPUB Reader Pro (优化版)
        
        欢迎使用性能优化版EPUB阅读器！
        
        主要优化点：
        1. 多线程加载与异步处理
        2. 图像缓存与延迟加载
        3. 资源管理与内存优化
        4. 响应式UI与延迟重绘
        5. 章节内容缓存
        
        使用说明：
        1. 程序启动时会自动加载电子书列表
        2. 在左侧列表中选择电子书并下载到书架
        3. 从书架中选择电子书加载阅读
        4. 使用章节下拉菜单和翻页按钮导航
        
        提示：您也可以使用"加载本地EPUB"按钮加载本地文件
        """
        
        self.text_area.insert(tk.END, welcome_text, "center")
        self.text_area.config(state=tk.DISABLED)

    def on_search_select(self, event):
        selected = self.search_tree.selection()
        if selected:
            self.download_button.config(state=tk.NORMAL)
        else:
            self.download_button.config(state=tk.DISABLED)

    def download_selected(self):
        selected = self.search_tree.selection()
        if not selected:
            return
            
        item = self.search_tree.item(selected[0])
        # 修改：从values中获取书名
        values = item["values"]
        if values and len(values) > 0:
            book_name = values[0]
        else:
            return
        
        # 检查是否已存在
        local_path = os.path.join(self.bookshelf_dir, book_name)
        if os.path.exists(local_path):
            if not messagebox.askyesno("确认", f"'{book_name}' 已存在，是否覆盖？"):
                return
        
        # 获取下载URL - 从远程书籍列表中查找
        download_url = None
        for book in self.remote_books:
            if book["name"] == book_name:
                download_url = book["download_url"]
                break
        
        if not download_url:
            # 如果API没有提供下载URL，尝试直接构建
            download_url = f"https://github.com/harptwzx/e-book/raw/main/books/{urllib.parse.quote(book_name)}"
        
        self.status_label.config(text=f"正在下载 {book_name}...")
        # 使用线程池下载
        future = self.executor.submit(self.download_book, book_name, download_url)
        future.add_done_callback(lambda f: self.on_download_complete(f, book_name))

    def on_download_complete(self, future, book_name):
        """下载完成后的回调"""
        try:
            future.result()
            self.root.after(0, lambda: self.status_label.config(text=f"下载完成: {book_name}"))
            self.root.after(0, self.refresh_bookshelf)
            self.root.after(0, lambda: messagebox.showinfo("下载成功", f"'{book_name}' 已添加到书架"))
        except Exception as e:
            self.root.after(0, lambda: self.status_label.config(text=f"下载失败: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("下载错误", f"无法下载电子书: {str(e)}"))

    def download_book(self, book_name, download_url):
        """下载书籍 - 优化下载性能"""
        try:
            # 下载文件
            headers = {"User-Agent": "EPubReaderApp/1.0"}
            response = requests.get(download_url, stream=True, headers=headers, timeout=30)
            response.raise_for_status()
            
            # 保存文件
            local_path = os.path.join(self.bookshelf_dir, book_name)
            with open(local_path, "wb") as f:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                start_time = time.time()
                last_update = time.time()
                
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:  # 过滤掉保持连接的新块
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # 限制更新频率 - 每0.5秒更新一次
                        current_time = time.time()
                        if current_time - last_update > 0.5:
                            last_update = current_time
                            
                            # 计算下载速度和剩余时间
                            elapsed_time = current_time - start_time
                            download_speed = downloaded / (1024 * elapsed_time) if elapsed_time > 0 else 0
                            remaining_time = (total_size - downloaded) / (download_speed * 1024) if download_speed > 0 else 0
                            
                            # 更新状态
                            self.root.after(0, lambda: self.status_label.config(
                                text=f"下载 {book_name}: {downloaded/1024:.1f}KB/{total_size/1024:.1f}KB "
                                     f"({downloaded/total_size*100:.1f}%) "
                                     f"速度: {download_speed:.1f}KB/s "
                                     f"剩余: {remaining_time:.1f}s"
                            ))
            
        except Exception as e:
            raise e

    def refresh_bookshelf(self):
        """刷新书架 - 优化性能"""
        # 清空书架
        for item in self.bookshelf_tree.get_children():
            self.bookshelf_tree.delete(item)
            
        # 添加书架中的电子书
        for filename in os.listdir(self.bookshelf_dir):
            if filename.lower().endswith(".epub"):
                filepath = os.path.join(self.bookshelf_dir, filename)
                size = os.path.getsize(filepath)
                date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(filepath)))
                
                # 转换文件大小
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size/1024:.1f} KB"
                else:
                    size_str = f"{size/(1024*1024):.1f} MB"
                
                # 提取书名（去除扩展名）
                book_name = os.path.splitext(filename)[0]
                # 修改：使用书名列
                self.bookshelf_tree.insert("", tk.END, values=(book_name, size_str, date))

    def on_bookshelf_select(self, event):
        selected = self.bookshelf_tree.selection()
        if selected:
            self.load_button.config(state=tk.NORMAL)
            self.remove_button.config(state=tk.NORMAL)
        else:
            self.load_button.config(state=tk.DISABLED)
            self.remove_button.config(state=tk.DISABLED)

    def load_from_bookshelf(self):
        """从书架加载书籍 - 优化性能"""
        selected = self.bookshelf_tree.selection()
        if not selected:
            return
            
        item = self.bookshelf_tree.item(selected[0])
        # 修改：从values中获取书名
        values = item["values"]
        if values and len(values) > 0:
            book_name = values[0]
        else:
            return
        
        # 在书架目录中查找匹配的文件
        file_path = None
        for filename in os.listdir(self.bookshelf_dir):
            if filename.lower().endswith(".epub") and os.path.splitext(filename)[0] == book_name:
                file_path = os.path.join(self.bookshelf_dir, filename)
                break
        
        if file_path:
            self.load_epub(file_path)
        else:
            messagebox.showerror("错误", f"找不到文件: {book_name}.epub")

    def remove_from_bookshelf(self):
        """从书架移除书籍 - 优化性能"""
        selected = self.bookshelf_tree.selection()
        if not selected:
            return
            
        item = self.bookshelf_tree.item(selected[0])
        # 修改：从values中获取书名
        values = item["values"]
        if values and len(values) > 0:
            book_name = values[0]
        else:
            return
        
        if not messagebox.askyesno("确认删除", f"确定要从书架中移除 '{book_name}' 吗？"):
            return
            
        # 在书架目录中查找匹配的文件
        file_path = None
        for filename in os.listdir(self.bookshelf_dir):
            if filename.lower().endswith(".epub") and os.path.splitext(filename)[0] == book_name:
                file_path = os.path.join(self.bookshelf_dir, filename)
                break
        
        if file_path:
            try:
                os.remove(file_path)
                self.refresh_bookshelf()
                self.status_label.config(text=f"已移除: {book_name}")
            except Exception as e:
                messagebox.showerror("删除错误", f"无法删除文件: {str(e)}")
        else:
            messagebox.showerror("错误", f"找不到文件: {book_name}.epub")

    def load_epub(self, file_path=None):
        """加载EPUB文件 - 使用缓存优化性能"""
        if not file_path:
            file_path = filedialog.askopenfilename(
                filetypes=[("EPub files", "*.epub"), ("All files", "*.*")]
            )
            if not file_path:
                return
        
        try:
            # 读取EPUB文件
            self.book = epub.read_epub(file_path)
            self.chapters = []
            self.chapter_titles = []
            self.image_references = []
            self.image_resources = {}
            self.ncx_toc = None
            self.chapter_cache = {}  # 清除之前的章节缓存
            
            # 获取书籍标题
            self.book_title = self.extract_book_title()
            self.status_label.config(text=f"正在加载: {self.book_title}")
            self.root.update()
            
            # 收集图片资源
            self.collect_image_resources()
            
            # 解析目录结构
            self.parse_table_of_contents()
            
            # 如果没有通过目录找到章节，尝试备用方法
            if not self.chapters:
                self.status_label.config(text=f"使用备用方法加载: {self.book_title}")
                self.root.update()
                self.parse_chapters_fallback()
            
            # 更新UI
            if self.chapters:
                self.chapter_combo.config(values=self.chapter_titles)
                self.chapter_combo.current(0)
                self.prev_button.config(state=tk.NORMAL)
                self.next_button.config(state=tk.NORMAL)
                self.current_chapter_index = 0
                self.show_chapter(self.current_chapter_index)
                self.status_label.config(text=f"已加载: {self.book_title} - 共 {len(self.chapters)} 章")
            else:
                self.status_label.config(text=f"错误: 在 {self.book_title} 中未找到章节")
                self.clear_text_area()
            
        except Exception as e:
            self.status_label.config(text=f"错误: {str(e)}")
            self.clear_text_area()
            messagebox.showerror("加载错误", f"无法加载EPUB文件: {str(e)}")
            
        # 强制垃圾回收释放内存
        gc.collect()

    def extract_book_title(self):
        """从元数据中提取书籍标题 - 优化性能"""
        try:
            # 方法1: 从DC元数据获取
            metadata = self.book.get_metadata('DC', 'title')
            if metadata:
                return metadata[0][0]
            
            # 方法2: 尝试从封面或第一页获取标题
            for item in self.book.get_items():
                if isinstance(item, epub.EpubHtml):
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    if soup.title and soup.title.string:
                        return soup.title.string.strip()
            
            # 方法3: 使用文件名作为标题
            return os.path.splitext(os.path.basename(self.book.file_name))[0]
        except:
            return "未知标题"

    def collect_image_resources(self):
        """收集所有图片资源 - 优化性能"""
        for item in self.book.get_items():
            # 修复ITEM_IMAGE问题 - 检查项目是否是图片类型
            if isinstance(item, epub.EpubImage):
                path = item.file_name
                self.image_resources[path] = item.get_content()
                filename = os.path.basename(path)
                if filename not in self.image_resources:
                    self.image_resources[filename] = item.get_content()
            # 备用方法：检查媒体类型是否为图片
            elif hasattr(item, 'media_type') and item.media_type and item.media_type.startswith('image/'):
                path = item.file_name
                self.image_resources[path] = item.get_content()
                filename = os.path.basename(path)
                if filename not in self.image_resources:
                    self.image_resources[filename] = item.get_content()

    def parse_table_of_contents(self):
        """解析目录结构获取章节信息 - 优化性能"""
        try:
            # 获取NCX目录（标准目录格式）
            ncx_items = [item for item in self.book.get_items() 
                         if isinstance(item, epub.EpubNcx)]
            
            if ncx_items:
                ncx_content = ncx_items[0].get_content()
                ncx_soup = BeautifulSoup(ncx_content, 'xml')
                nav_points = ncx_soup.find_all('navPoint')
                self.process_nav_points(nav_points)
                return
            
            # 尝试HTML目录（较新的EPUB3格式）
            nav_items = [item for item in self.book.get_items() 
                         if isinstance(item, epub.EpubNav)]
            
            if not nav_items:
                # 备选方法：查找包含目录的HTML文件
                nav_items = [item for item in self.book.get_items()
                            if isinstance(item, epub.EpubHtml) and 
                            ('toc' in item.file_name.lower() or 'nav' in item.file_name.lower())]
            
            if nav_items:
                nav_content = nav_items[0].get_content()
                nav_soup = BeautifulSoup(nav_content, 'html.parser')
                nav_links = nav_soup.find_all('a', href=True)
                self.process_nav_links(nav_links)
                return
            
        except Exception as e:
            print(f"解析目录时出错: {e}")

    def process_nav_points(self, nav_points):
        """处理NCX目录点 - 优化性能"""
        for nav_point in nav_points:
            # 提取章节标题
            title = nav_point.find('text').get_text().strip()
            
            # 提取内容路径
            content_src = nav_point.find('content')['src']
            content_path = self.resolve_path(content_src.split('#')[0])
            
            # 获取章节内容
            chapter_item = self.book.get_item_with_href(content_path)
            if chapter_item and isinstance(chapter_item, epub.EpubHtml):
                self.add_chapter(chapter_item, title)
            
            # 递归处理子目录
            child_points = nav_point.find_all('navPoint', recursive=False)
            if child_points:
                self.process_nav_points(child_points)

    def process_nav_links(self, nav_links):
        """处理HTML导航链接 - 优化性能"""
        for link in nav_links:
            title = link.get_text().strip()
            content_src = link['href']
            content_path = self.resolve_path(content_src.split('#')[0])
            
            chapter_item = self.book.get_item_with_href(content_path)
            if chapter_item and isinstance(chapter_item, epub.EpubHtml):
                self.add_chapter(chapter_item, title)

    def parse_chapters_fallback(self):
        """备用的章节解析方法 - 优化性能"""
        try:
            # 获取spine顺序（阅读顺序）
            spine_items = [self.book.get_item_with_id(item[0]) 
                          for item in self.book.spine 
                          if self.book.get_item_with_id(item[0])]
            
            # 按阅读顺序处理项目
            for idx, item in enumerate(spine_items):
                if isinstance(item, epub.EpubHtml):
                    soup = BeautifulSoup(item.get_content(), 'html.parser')
                    title = f"章节 {idx+1}"
                    
                    # 尝试从文档中提取标题
                    if soup.title and soup.title.string:
                        title = soup.title.string.strip()
                    elif soup.find('h1'):
                        title = soup.find('h1').get_text().strip()
                    elif soup.find('h2'):
                        title = soup.find('h2').get_text().strip()
                    
                    self.add_chapter(item, title)
        
        except Exception as e:
            print(f"备用章节解析失败: {e}")

    def add_chapter(self, item, title):
        """添加章节到列表中 - 优化性能"""
        # 确保章节标题唯一
        base_title = title
        counter = 1
        while title in self.chapter_titles:
            title = f"{base_title} ({counter})"
            counter += 1
        
        self.chapter_titles.append(title)
        
        # 解析章节内容
        content = item.get_content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # 存储章节信息
        self.chapters.append({
            "soup": soup,
            "path": item.file_name,
            "title": title,
            "item": item
        })

    def resolve_path(self, path):
        """解析相对路径为绝对路径 - 优化性能"""
        # 处理绝对路径
        if path.startswith('/'):
            return path[1:]
        
        # 处理相对路径 - 这里需要知道基础路径，但EPUBlib不直接提供
        # 在大多数情况下，路径已经是绝对路径
        return path

    def clear_text_area(self):
        """清除文本区域 - 优化内存管理"""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete(1.0, tk.END)
        self.text_area.config(state=tk.DISABLED)
        
        # 清除图片引用以释放内存
        self.image_references = []
        gc.collect()

    def show_chapter(self, index):
        """显示章节内容 - 使用缓存优化性能"""
        if not self.chapters or index < 0 or index >= len(self.chapters):
            return
            
        # 检查是否正在加载同一章节
        if self.loading_chapter == index:
            return
            
        self.loading_chapter = index
            
        # 清除文本区域
        self.clear_text_area()
        self.text_area.config(state=tk.NORMAL)
        
        # 更新UI状态
        self.page_label.config(text=f"章节: {index+1}/{len(self.chapters)}")
        self.chapter_combo.current(index)
        self.current_chapter_index = index
        
        # 更新翻页按钮状态
        self.prev_button.config(state=tk.NORMAL if index > 0 else tk.DISABLED)
        self.next_button.config(state=tk.NORMAL if index < len(self.chapters) - 1 else tk.DISABLED)
        
        # 获取章节数据
        chapter = self.chapters[index]
        path = chapter["path"]
        title = chapter["title"]
        
        # 显示章节标题
        self.text_area.insert(tk.END, f"\n{title}\n", "chapter_title")
        self.text_area.insert(tk.END, "\n" + "=" * len(title) + "\n\n", "chapter_title")
        
        # 在后台线程中处理章节内容
        self.executor.submit(self.process_chapter_content, index, chapter)
        
        # 滚动到顶部
        self.text_area.yview_moveto(0)
        
    def process_chapter_content(self, index, chapter):
        """在后台线程中处理章节内容"""
        if index != self.current_chapter_index:
            return
            
        # 检查缓存
        cache_key = f"{self.book_title}_{index}"
        if cache_key in self.chapter_cache:
            # 使用缓存内容
            cached_content = self.chapter_cache[cache_key]
            self.root.after(0, lambda: self.insert_cached_content(cached_content))
            return
            
        # 处理章节内容
        soup = chapter["soup"]
        path = chapter["path"]
        
        # 创建章节目录
        toc = self.create_chapter_toc(soup)
        
        # 移除不需要的元素
        for element in soup(['script', 'style', 'header', 'footer', 'nav', 'aside', 'svg']):
            element.decompose()
        
        # 处理正文内容
        body = soup.body if soup.body else soup
        
        # 构建缓存内容
        cached_content = {
            "toc": toc,
            "body": body,
            "path": path
        }
        
        # 存储到缓存
        self.chapter_cache[cache_key] = cached_content
        
        # 更新UI
        self.root.after(0, lambda: self.insert_cached_content(cached_content))
        
    def insert_cached_content(self, cached_content):
        """将缓存内容插入文本区域"""
        if not cached_content:
            return
            
        toc = cached_content["toc"]
        body = cached_content["body"]
        path = cached_content["path"]
        
        # 显示章节目录
        if toc:
            self.text_area.insert(tk.END, "本章目录:\n\n", "subheading")
            for level, title in toc:
                indent = "    " * (level - 1)
                self.text_area.insert(tk.END, f"{indent}- {title}\n", "normal")
            self.text_area.insert(tk.END, "\n" + "-" * 40 + "\n\n")
        
        # 处理正文内容
        self.process_element(body, path)
        
        # 添加章节结束标记
        self.text_area.insert(tk.END, "\n\n" + "-" * 40 + "\n\n")
        
        # 禁用文本区域
        self.text_area.config(state=tk.DISABLED)
        
        # 重置加载状态
        self.loading_chapter = None

    def create_chapter_toc(self, soup):
        """创建章节内目录 - 优化性能"""
        toc = []
        headings = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
        
        for heading in headings:
            level = int(heading.name[1])
            title = heading.get_text().strip()
            if title:
                toc.append((level, title))
        
        return toc if toc else None

    def process_element(self, element, chapter_path):
        """递归处理HTML元素 - 优化性能"""
        if isinstance(element, str):
            # 处理文本节点
            text = html.unescape(element.strip())
            if text:
                self.text_area.insert(tk.END, text + " ", "normal")
        elif hasattr(element, 'children'):
            # 处理元素节点
            if element.name == 'img' and 'src' in element.attrs:
                self.insert_image(element['src'], chapter_path)
            elif element.name == 'p':
                self.text_area.insert(tk.END, '\n\n', "normal")
                for child in element.children:
                    self.process_element(child, chapter_path)
                self.text_area.insert(tk.END, '\n', "normal")
            elif element.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                level = int(element.name[1])
                self.text_area.insert(tk.END, '\n\n', "normal")
                self.text_area.insert(tk.END, element.get_text().strip() + '\n', "subheading")
                self.text_area.insert(tk.END, '-' * len(element.get_text().strip()) + '\n\n', "normal")
            elif element.name == 'br':
                self.text_area.insert(tk.END, '\n', "normal")
            elif element.name == 'hr':
                self.text_area.insert(tk.END, '\n' + '-' * 40 + '\n', "normal")
            elif element.name == 'blockquote':
                self.text_area.insert(tk.END, '\n  ', "quote")
                for child in element.children:
                    self.process_element(child, chapter_path)
                self.text_area.insert(tk.END, '\n\n', "quote")
            elif element.name == 'div' or element.name == 'section':
                self.text_area.insert(tk.END, '\n', "normal")
                for child in element.children:
                    self.process_element(child, chapter_path)
                self.text_area.insert(tk.END, '\n', "normal")
            elif element.name == 'li':
                self.text_area.insert(tk.END, '\n• ', "normal")
                for child in element.children:
                    self.process_element(child, chapter_path)
            elif element.name == 'a' and 'href' in element.attrs:
                # 处理超链接但不显示URL
                for child in element.children:
                    self.process_element(child, chapter_path)
            else:
                # 默认处理：递归处理所有子元素
                for child in element.children:
                    self.process_element(child, chapter_path)
        elif element is not None:
            # 处理其他类型的节点
            self.text_area.insert(tk.END, str(element), "normal")

    def insert_image(self, src, chapter_dir):
        """插入图片到文本区域 - 使用缓存优化性能"""
        try:
            # 解析图片路径
            image_path = self.resolve_image_path(src, chapter_dir)
            
            # 获取当前文本区域宽度
            text_width = self.text_area.winfo_width() - 50
            if text_width < 100:
                text_width = 600
            
            # 检查缓存
            cached_image = self.image_cache.get(image_path, chapter_dir, text_width)
            if cached_image:
                # 使用缓存的图片
                self.text_area.image_create(tk.END, image=cached_image)
                self.text_area.tag_add("center", "insert-1c", "insert")
                self.text_area.insert(tk.END, '\n\n', "normal")
                return
            
            # 查找图片资源
            image_data = None
            if image_path in self.image_resources:
                image_data = self.image_resources[image_path]
            else:
                filename = os.path.basename(image_path)
                if filename in self.image_resources:
                    image_data = self.image_resources[filename]
                elif src in self.image_resources:
                    image_data = self.image_resources[src]
            
            if not image_data:
                self.text_area.insert(tk.END, f"\n[图片未找到: {image_path}]\n\n", "normal")
                return
            
            # 处理图片
            image = Image.open(io.BytesIO(image_data))
            
            # 调整图片大小
            width, height = image.size
            if width > text_width:
                ratio = text_width / width
                new_size = (int(width * ratio), int(height * ratio))
                image = image.resize(new_size, Image.LANCZOS)
            
            # 显示图片
            photo = ImageTk.PhotoImage(image)
            self.image_references.append(photo)
            
            # 添加到缓存
            self.image_cache.put(image_path, photo, chapter_dir, text_width)
            
            # 居中显示
            self.text_area.image_create(tk.END, image=photo)
            self.text_area.tag_add("center", "insert-1c", "insert")
            self.text_area.insert(tk.END, '\n\n', "normal")
            
        except Exception as e:
            self.text_area.insert(tk.END, f"\n[图片错误: {str(e)}]\n\n", "normal")

    def resolve_image_path(self, src, chapter_dir):
        """解析图片路径 - 优化性能"""
        if src.startswith('/'):
            return src[1:]
        
        if chapter_dir:
            # 处理相对路径
            base_dir = os.path.dirname(chapter_dir) if chapter_dir else ""
            resolved_path = posixpath.normpath(posixpath.join(base_dir, src))
        else:
            resolved_path = src
        
        return resolved_path.replace("\\", "/")

    def on_chapter_select(self, event):
        selected_index = self.chapter_combo.current()
        if 0 <= selected_index < len(self.chapters) and selected_index != self.current_chapter_index:
            self.show_chapter(selected_index)

    def show_previous(self):
        if self.current_chapter_index > 0:
            self.show_chapter(self.current_chapter_index - 1)

    def show_next(self):
        if self.current_chapter_index < len(self.chapters) - 1:
            self.show_chapter(self.current_chapter_index + 1)

    def __del__(self):
        """析构函数，清理资源"""
        self.executor.shutdown(wait=False)
        gc.collect()

if __name__ == "__main__":
    root = ThemedTk()
    try:
        root.set_theme("arc")
    except:
        try:
            root.set_theme("clam")
        except:
            pass
    app = EPubReaderApp(root)
    root.mainloop()