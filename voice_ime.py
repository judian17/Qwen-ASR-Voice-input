import os
import threading
import time
import json
import logging
import configparser
import ctypes
import queue
import wx
import pyaudio
import websocket
import pyperclip
import audioop
from ctypes import wintypes
from openai import OpenAI
from pynput import keyboard, mouse
from pynput.keyboard import Controller, Key

# ======================================================================================
# 0. 多语言配置字典 (新增)
# ======================================================================================

TRANS = {
    "cn": {
        "title": "AI 语音输入助手",
        "grp_api": "API 与 模型配置",
        "lbl_asr_key": "ASR Key:",
        "lbl_asr_wss": "ASR WSS:",
        "lbl_asr_model": "ASR Model:",
        "lbl_llm_key": "LLM Key:",
        "lbl_llm_base": "LLM Base URL:",
        "lbl_llm_model": "LLM Model:",
        "grp_ctrl": "控制与功能",
        "lbl_trigger": "触发按键:",
        "lbl_mode": "触发模式:",
        "mode_hold": "长按 (Hold)",
        "mode_toggle": "点击 (Toggle)",
        "lbl_mic": "麦克风:",
        "mic_default": "系统默认",
        "lbl_ai": "AI 处理:",
        "chk_llm": "启用模版处理",
        "lbl_paste": "上屏方式:",
        "chk_paste": "使用粘贴 (微信请勾选)",
        "lbl_vad": "断句(ms):",
        "btn_save": "保存配置并重启服务",
        "tip_save": "(进行修改后需点击此保存按钮才会生效)",
        "lbl_timeout": "⏳ 无响应自动停止(秒): ",
        "lbl_timeout_suf": " (0为禁用)",
        "status_idle": " [未运行] ",
        "status_rec": " [🔴 正在录音...] ",
        "status_off": " [未启用] ",
        "grp_template": "AI 模版配置",
        "lbl_tpl_mode": "模式: ",
        "btn_ren": "重命名",
        "lbl_sys_prompt": "系统提示词 (System Prompt):",
        "lbl_user_prefix": "用户前缀: ",
        "lang_sel": "Language/语言:",
        "msg_saved": "配置已保存，正在重启...",
        "msg_service_started": "服务启动。键: ",
        "msg_service_reset": "服务已彻底重置并启动。",
        "msg_config_missing": "请配置 Key 并保存重启"
    },
    "en": {
        "title": "AI Voice IME",
        "grp_api": "API & Model Settings",
        "lbl_asr_key": "ASR Key:",
        "lbl_asr_wss": "ASR WSS:",
        "lbl_asr_model": "ASR Model:",
        "lbl_llm_key": "LLM Key:",
        "lbl_llm_base": "LLM Base URL:",
        "lbl_llm_model": "LLM Model:",
        "grp_ctrl": "Control & Features",
        "lbl_trigger": "Trigger Key:",
        "lbl_mode": "Trigger Mode:",
        "mode_hold": "Hold",
        "mode_toggle": "Toggle",
        "lbl_mic": "Microphone:",
        "mic_default": "System Default",
        "lbl_ai": "AI Process:",
        "chk_llm": "Enable Template",
        "lbl_paste": "Output Method:",
        "chk_paste": "Use Paste (Simulate Ctrl+V)",
        "lbl_vad": "VAD (ms):",
        "btn_save": "Save & Restart Service",
        "tip_save": "(Click Save to apply changes)",
        "lbl_timeout": "⏳ Auto Stop Timeout(s): ",
        "lbl_timeout_suf": " (0 to disable)",
        "status_idle": " [Idle] ",
        "status_rec": " [🔴 Recording...] ",
        "status_off": " [Disabled] ",
        "grp_template": "AI Template Config",
        "lbl_tpl_mode": "Mode: ",
        "btn_ren": "Rename",
        "lbl_sys_prompt": "System Prompt:",
        "lbl_user_prefix": "User Prefix: ",
        "lang_sel": "Language:",
        "msg_saved": "Config saved, restarting...",
        "msg_service_started": "Service Started. Key: ",
        "msg_service_reset": "Service fully reset and started.",
        "msg_config_missing": "Please config Keys and Save"
    }
}

# ======================================================================================
# 1. 底层 Win32 输入
# ======================================================================================

ULONG_PTR = ctypes.c_ulonglong 

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", ctypes.c_ubyte * 32), ("hi", ctypes.c_ubyte * 32)]
    _anonymous_ = ("_input",)
    _fields_ = [("type", wintypes.DWORD), ("_input", _INPUT)]

class BatchWin32Injector:
    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.INPUT_KEYBOARD = 1
        self.KEYEVENTF_KEYUP = 0x0002
        self.KEYEVENTF_UNICODE = 0x0004

    def inject(self, text):
        if not text: return
        text = text.replace('\r', '').replace('\n', '').replace('\t', ' ')
        for char in text:
            inputs = []
            ki_down = KEYBDINPUT(0, ord(char), self.KEYEVENTF_UNICODE, 0, 0)
            inputs.append(INPUT(self.INPUT_KEYBOARD, INPUT._INPUT(ki=ki_down)))
            ki_up = KEYBDINPUT(0, ord(char), self.KEYEVENTF_UNICODE | self.KEYEVENTF_KEYUP, 0, 0)
            inputs.append(INPUT(self.INPUT_KEYBOARD, INPUT._INPUT(ki=ki_up)))
            count = len(inputs)
            input_array = (INPUT * count)(*inputs)
            self.user32.SendInput(count, input_array, ctypes.sizeof(INPUT))
            time.sleep(0.003) 

injector = BatchWin32Injector()
kb_controller = Controller()

# ======================================================================================
# 2. 全局配置
# ======================================================================================

class GlobalConfig:
    def __init__(self):
        self.config_file = 'config.ini'
        self.language = "cn" # [新增] 默认语言
        self.asr_api_key = ""
        self.asr_url = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
        self.asr_model = "qwen3-asr-flash-realtime"
        self.llm_api_key = ""
        self.llm_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.llm_model = "qwen-plus"
        self.trigger_key = "F2"
        self.input_mode = "HOLD" 
        self.audio_device_index = -1
        self.enable_llm = True 
        self.vad_threshold = 800 
        self.use_paste = False
        self.auto_stop_timeout = 60 
        
        self.templates = self._default_templates()
        self.current_template_index = 0
        self.window_geometry = None 

    def _default_templates(self):
        return [
            {"name": "语音润色 (默认)", "prefix": "待处理文本:", "content": "你是一个语音输入助手。任务：1.去除语气词。2.修正错别字。3.不要输出解释，只输出纯文本。"},
            {"name": "中译英", "prefix": "请翻译:", "content": "将用户输入直接翻译成英文。只输出译文，不要解释。"},
            {"name": "AI 问答", "prefix": "", "content": "简短回答用户的问题，字数限制100字以内。"}
        ]

    def get_current_prompt(self):
        if 0 <= self.current_template_index < len(self.templates):
            return self.templates[self.current_template_index].get('content', '')
        return ""

    def get_current_prefix(self):
        if 0 <= self.current_template_index < len(self.templates):
            return self.templates[self.current_template_index].get('prefix', '')
        return ""

    def load(self):
        config = configparser.ConfigParser()
        if os.path.exists(self.config_file):
            config.read(self.config_file, encoding='utf-8')
            if 'Settings' in config:
                s = config['Settings']
                self.language = s.get('language', 'cn') # [新增]
                self.asr_api_key = s.get('asr_api_key', '')
                self.asr_url = s.get('asr_url', self.asr_url)
                self.asr_model = s.get('asr_model', self.asr_model)
                self.llm_api_key = s.get('llm_api_key', '')
                self.llm_base_url = s.get('llm_base_url', self.llm_base_url)
                self.llm_model = s.get('llm_model', self.llm_model)
                self.trigger_key = s.get('trigger_key', 'F2')
                self.input_mode = s.get('input_mode', 'HOLD')
                self.audio_device_index = s.getint('audio_device_index', -1)
                self.enable_llm = s.getboolean('enable_llm', True)
                self.vad_threshold = s.getint('vad_threshold', 800)
                self.use_paste = s.getboolean('use_paste', False)
                self.auto_stop_timeout = s.getint('auto_stop_timeout', 60)
                
                try:
                    geo = s.get('window_geometry', '')
                    if geo: self.window_geometry = json.loads(geo)
                except: pass

                try:
                    tpl_json = s.get('templates', '')
                    if tpl_json: self.templates = json.loads(tpl_json)
                    self.current_template_index = s.getint('current_template_index', 0)
                except: pass

    def save(self):
        config = configparser.ConfigParser()
        config['Settings'] = {
            'language': self.language, # [新增]
            'asr_api_key': self.asr_api_key,
            'asr_url': self.asr_url,
            'asr_model': self.asr_model,
            'llm_api_key': self.llm_api_key,
            'llm_base_url': self.llm_base_url,
            'llm_model': self.llm_model,
            'trigger_key': self.trigger_key,
            'input_mode': self.input_mode,
            'audio_device_index': str(self.audio_device_index),
            'enable_llm': str(self.enable_llm),
            'vad_threshold': str(self.vad_threshold),
            'use_paste': str(self.use_paste),
            'auto_stop_timeout': str(self.auto_stop_timeout),
            'templates': json.dumps(self.templates, ensure_ascii=False),
            'current_template_index': str(self.current_template_index),
            'window_geometry': json.dumps(self.window_geometry) if self.window_geometry else ""
        }
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f: config.write(f)
        except Exception as e: print(f"保存失败: {e}")

cfg = GlobalConfig()
typing_queue = queue.Queue()

# ======================================================================================
# 3. 消费者线程
# ======================================================================================

def typing_worker(log_func):
    while True:
        try:
            raw_text = typing_queue.get()
            if raw_text is None: break 
            final_text = raw_text.strip()
            
            system_prompt = cfg.get_current_prompt()
            user_prefix = cfg.get_current_prefix()
            
            if cfg.enable_llm and len(final_text) > 1 and cfg.llm_api_key:
                try:
                    client = OpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url)
                    completion = client.chat.completions.create(
                        model=cfg.llm_model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": f"{user_prefix}{final_text}"}
                        ],
                        timeout=20 
                    )
                    content = completion.choices[0].message.content.strip()
                    if not content.startswith(("(", "（", "空字符串")):
                        final_text = content
                        log_func(f"[LLM] 结果: {final_text}")
                except Exception as e:
                    log_func(f"[LLM Error] {e}")

            if final_text:
                log_func(f"上屏: {final_text}")
                try:
                    if cfg.use_paste:
                        pyperclip.copy(final_text)
                        with kb_controller.pressed(Key.ctrl):
                            kb_controller.tap('v')
                        time.sleep(0.05)
                    else:
                        injector.inject(final_text)
                except Exception as e:
                    log_func(f"上屏失败: {e}")

            typing_queue.task_done()
        except Exception as e:
            log_func(f"Worker Error: {e}")

# ======================================================================================
# 4. ASR 核心 (流式传输 + 文本缓存)
# ======================================================================================

class QwenAsrManager:
    def __init__(self, log_callback, activity_callback=None):
        self.ws = None
        self.is_running = False
        self.log = log_callback
        self.activity_callback = activity_callback 
        self.transcript_accumulator = [] 
        self.last_resp_time = 0 
        self.waiting_for_final = False

    def start(self):
        if not cfg.asr_api_key:
            self.log("未配置 ASR Key，服务暂停。")
            return
        if self.is_running: return
        self.is_running = True
        threading.Thread(target=self.run_ws, daemon=True).start()
        
    def stop(self):
        self.is_running = False
        if self.ws: self.ws.close()

    def run_ws(self):
        if not cfg.asr_api_key: return
        
        raw_url = cfg.asr_url.strip()
        if "?" in raw_url: url = raw_url
        else: url = f"{raw_url}?model={cfg.asr_model}"
        headers = [f"Authorization: Bearer {cfg.asr_api_key}", "OpenAI-Beta: realtime=v1"]
        
        while self.is_running:
            try:
                self.log(f"连接 ASR...")
                self.ws = websocket.WebSocketApp(
                    url, header=headers,
                    on_open=self.on_open, on_message=self.on_message,
                    on_error=self.on_error, on_close=self.on_close
                )
                self.ws.run_forever()
                if self.is_running: time.sleep(3)
            except Exception as e:
                self.log(f"连接失败: {e}")
                time.sleep(5)

    def on_open(self, ws):
        self.log("ASR 服务已连接")
        self.transcript_accumulator = []
        event_init = {
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "silence_duration_ms": cfg.vad_threshold 
                }
            }
        }
        ws.send(json.dumps(event_init))
        threading.Thread(target=self.send_audio_loop, args=(ws,), daemon=True).start()

    def send_audio_loop(self, ws):
        p = pyaudio.PyAudio()
        stream = None
        TARGET_RATE = 16000
        actual_rate = TARGET_RATE
        
        try:
            device_idx = cfg.audio_device_index if cfg.audio_device_index >= 0 else None
            try:
                stream = p.open(format=pyaudio.paInt16, channels=1, rate=TARGET_RATE, input=True, 
                              input_device_index=device_idx, frames_per_buffer=3200)
            except Exception as e:
                if device_idx is None: dev_info = p.get_default_input_device_info()
                else: dev_info = p.get_device_info_by_index(device_idx)
                actual_rate = int(dev_info.get('defaultSampleRate', 48000))
                stream = p.open(format=pyaudio.paInt16, channels=1, rate=actual_rate, input=True, 
                              input_device_index=device_idx, frames_per_buffer=3200)
            
            state = None
            
            while self.is_running and ws.keep_running:
                read_frames = int(3200 * (actual_rate / TARGET_RATE))
                data = stream.read(read_frames, exception_on_overflow=False)
                
                # 只要录音开启，就始终流式发送音频 (低延迟核心)
                if UniversalListener.is_recording_active:
                    if actual_rate != TARGET_RATE:
                        data, state = audioop.ratecv(data, 2, 1, actual_rate, TARGET_RATE, state)
                    try:
                        import base64
                        b64_data = base64.b64encode(data).decode("utf-8")
                        ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": b64_data}))
                    except: pass
                else:
                    time.sleep(0.005)
        except Exception as e:
            self.log(f"麦克风错误: {e}")
        finally:
            if stream: stream.close()
            p.terminate()

    def finish_hold_session(self):
        """松开按键后调用：提交，等待文本结果，然后聚合"""
        if not self.ws or not self.ws.keep_running: return
        self.log("<<< 提交数据，等待结果...")
        self.waiting_for_final = True
        commit_timestamp = time.time()
        
        try: self.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        except: pass
        
        # 智能等待尾音
        max_wait_cycles = 15 # 1.5s
        got_new_data = False
        for _ in range(max_wait_cycles):
            time.sleep(0.1)
            if self.last_resp_time > commit_timestamp:
                if not got_new_data:
                    got_new_data = True
                    time.sleep(0.3) 
                    break 
        
        self.waiting_for_final = False
        
        if self.transcript_accumulator:
            full_text = "".join(self.transcript_accumulator)
            self.log(f"[聚合结果] {full_text}")
            typing_queue.put(full_text)
            self.transcript_accumulator = []
        else:
            self.log("未检测到有效语音")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == "conversation.item.input_audio_transcription.completed":
                self.last_resp_time = time.time()
                
                if self.activity_callback:
                    self.activity_callback()

                transcript = data.get("transcript", "").strip()
                if transcript:
                    if cfg.input_mode == "HOLD":
                        # 长按模式：缓存文本
                        self.log(f"[缓冲] {transcript}")
                        self.transcript_accumulator.append(transcript)
                    else:
                        # Toggle 模式：直接流式输出
                        self.log(f"[流式] {transcript}")
                        typing_queue.put(transcript)
        except: pass
    def on_error(self, ws, error): pass
    def on_close(self, ws, *args): pass

# ======================================================================================
# 5. 监听器
# ======================================================================================

class UniversalListener:
    is_recording_active = False
    def __init__(self, asr_manager, log_func, status_callback=None):
        self.asr = asr_manager
        self.log = log_func
        self.status_cb = status_callback 
        self.keyboard_listener = None
        self.mouse_listener = None
        self.trigger_mode = "KEY"
        self.trigger_val = None
        self.key_down = False 
        self.last_active_ts = 0

    def refresh_activity(self):
        self.last_active_ts = time.time()

    def update_config(self):
        key_str = cfg.trigger_key.strip().upper()
        if key_str == "MOUSEX1": self.trigger_mode = "MOUSE"; self.trigger_val = mouse.Button.x1
        elif key_str == "MOUSEX2": self.trigger_mode = "MOUSE"; self.trigger_val = mouse.Button.x2
        else: self.trigger_mode = "KEY"; self.trigger_val = key_str

    def start(self):
        self.update_config()
        self.keyboard_listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        self.keyboard_listener.start()
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.mouse_listener.start()

    def stop(self):
        if self.keyboard_listener: self.keyboard_listener.stop()
        if self.mouse_listener: self.mouse_listener.stop()

    def _update_ui(self, active):
        if self.status_cb: wx.CallAfter(self.status_cb, active)

    def _start_rec(self):
        if not UniversalListener.is_recording_active:
            UniversalListener.is_recording_active = True
            # 清空缓存
            if self.asr: self.asr.transcript_accumulator = []
            self.log(">>> 录音开始")
            self._update_ui(True)
            
            # 启动看门狗线程
            self.last_active_ts = time.time() 
            threading.Thread(target=self._watchdog_loop, daemon=True).start()

    def _stop_rec(self):
        if UniversalListener.is_recording_active:
            UniversalListener.is_recording_active = False
            self.log("<<< 录音停止")
            self._update_ui(False)
            if self.asr: 
                if cfg.input_mode == "HOLD":
                    threading.Thread(target=self.asr.finish_hold_session).start()
                else:
                    try: self.asr.ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                    except: pass

    def _watchdog_loop(self):
        """后台线程：检测是否长时间无响应"""
        while UniversalListener.is_recording_active:
            time.sleep(1)
            if cfg.auto_stop_timeout > 0:
                duration = time.time() - self.last_active_ts
                if duration > cfg.auto_stop_timeout:
                    self.log(f"[超时] {cfg.auto_stop_timeout}秒无响应，自动停止录音")
                    self._stop_rec()
                    break

    def handle_press(self):
        if not self.key_down:
            self.key_down = True
            if cfg.input_mode == "HOLD": self._start_rec()
            else:
                if UniversalListener.is_recording_active: self._stop_rec()
                else: self._start_rec()

    def handle_release(self):
        if self.key_down:
            self.key_down = False
            if cfg.input_mode == "HOLD": self._stop_rec()

    def on_press(self, key):
        if self.trigger_mode != "KEY": return
        try: k = key.char.upper() if hasattr(key, 'char') and key.char else key.name.upper()
        except: return
        if k == self.trigger_val: self.handle_press()

    def on_release(self, key):
        if self.trigger_mode != "KEY": return
        try: k = key.char.upper() if hasattr(key, 'char') and key.char else key.name.upper()
        except: return
        if k == self.trigger_val: self.handle_release()

    def on_click(self, x, y, button, pressed):
        if self.trigger_mode != "MOUSE": return
        if button == self.trigger_val:
            if pressed: self.handle_press()
            else: self.handle_release()

# ======================================================================================
# 6. GUI 界面 (重构为多语言支持)
# ======================================================================================

class MainFrame(wx.Frame):
    def __init__(self):
        # 初始加载配置
        cfg.load()
        # 初始化标题
        super().__init__(None, title=TRANS[cfg.language]["title"], size=(1200, 800))
        
        if cfg.window_geometry and len(cfg.window_geometry) == 2:
            w, h = cfg.window_geometry
            self.SetSize((w, h))
            self.Center() 
        else:
            self.Center()

        self.panel = wx.Panel(self)
        self.main_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        # 左侧
        self.left_sizer = wx.BoxSizer(wx.VERTICAL)
        self.create_left_panel()
        self.main_sizer.Add(self.left_sizer, 1, wx.EXPAND | wx.ALL, 10)
        
        # 分割线
        self.main_sizer.Add(wx.StaticLine(self.panel, style=wx.LI_VERTICAL), 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 10)
        
        # 右侧
        self.right_sizer = wx.BoxSizer(wx.VERTICAL)
        self.create_right_panel()
        self.main_sizer.Add(self.right_sizer, 1, wx.EXPAND | wx.ALL, 10)

        self.panel.SetSizer(self.main_sizer)
        self.Center()
        
        threading.Thread(target=typing_worker, args=(self.append_log,), daemon=True).start()
        
        self.listener = None 
        def on_activity_signal():
            if self.listener:
                self.listener.refresh_activity()

        self.asr_manager = QwenAsrManager(self.append_log, activity_callback=on_activity_signal)
        self.listener = UniversalListener(self.asr_manager, self.append_log, self.update_status_indicator)
        
        # 首次初始化文字
        self.update_ui_text()
        
        self.start_services()
        self.Bind(wx.EVT_CLOSE, self.on_close)

    def create_left_panel(self):
        # [新增] 语言切换栏
        box_lang = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_lang_sel = wx.StaticText(self.panel) # 内容动态设置
        box_lang.Add(self.lbl_lang_sel, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        
        self.choice_lang = wx.Choice(self.panel, choices=["中文", "English"])
        self.choice_lang.SetSelection(0 if cfg.language == 'cn' else 1)
        self.choice_lang.Bind(wx.EVT_CHOICE, self.on_language_change)
        box_lang.Add(self.choice_lang, 0, wx.ALIGN_CENTER_VERTICAL)
        
        self.left_sizer.Add(box_lang, 0, wx.ALIGN_RIGHT | wx.BOTTOM, 10)

        # API 配置 (保存 StaticBox 和 StaticText 引用以便修改)
        self.box_api_obj = wx.StaticBox(self.panel, label="")
        box_api = wx.StaticBoxSizer(self.box_api_obj, wx.VERTICAL)
        g = wx.FlexGridSizer(0, 2, 5, 5); g.AddGrowableCol(1, 1)
        
        self.l_asr_key = wx.StaticText(self.panel); g.Add(self.l_asr_key, 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_asr_key = wx.TextCtrl(self.panel, value=cfg.asr_api_key, style=wx.TE_PASSWORD)
        g.Add(self.txt_asr_key, 1, wx.EXPAND)
        
        self.l_asr_wss = wx.StaticText(self.panel); g.Add(self.l_asr_wss, 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_asr_url = wx.TextCtrl(self.panel, value=cfg.asr_url)
        g.Add(self.txt_asr_url, 1, wx.EXPAND)
        
        self.l_asr_model = wx.StaticText(self.panel); g.Add(self.l_asr_model, 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_asr_model = wx.TextCtrl(self.panel, value=cfg.asr_model)
        g.Add(self.txt_asr_model, 1, wx.EXPAND)
        
        g.Add(wx.StaticLine(self.panel), 0, wx.EXPAND|wx.TOP|wx.BOTTOM, 5)
        g.Add(wx.StaticLine(self.panel), 0, wx.EXPAND|wx.TOP|wx.BOTTOM, 5)
        
        self.l_llm_key = wx.StaticText(self.panel); g.Add(self.l_llm_key, 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_llm_key = wx.TextCtrl(self.panel, value=cfg.llm_api_key, style=wx.TE_PASSWORD)
        g.Add(self.txt_llm_key, 1, wx.EXPAND)
        
        self.l_llm_base = wx.StaticText(self.panel); g.Add(self.l_llm_base, 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_llm_url = wx.TextCtrl(self.panel, value=cfg.llm_base_url)
        g.Add(self.txt_llm_url, 1, wx.EXPAND)
        
        self.l_llm_model = wx.StaticText(self.panel); g.Add(self.l_llm_model, 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_llm_model = wx.TextCtrl(self.panel, value=cfg.llm_model)
        g.Add(self.txt_llm_model, 1, wx.EXPAND)
        
        box_api.Add(g, 1, wx.EXPAND|wx.ALL, 5)
        self.left_sizer.Add(box_api, 0, wx.EXPAND|wx.BOTTOM, 10)

        # 功能控制
        self.box_ctrl_obj = wx.StaticBox(self.panel, label="")
        box_ctrl = wx.StaticBoxSizer(self.box_ctrl_obj, wx.VERTICAL)
        g2 = wx.FlexGridSizer(6, 2, 5, 5); g2.AddGrowableCol(1, 1)
        
        self.l_trigger = wx.StaticText(self.panel); g2.Add(self.l_trigger, 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_key = wx.TextCtrl(self.panel, value=cfg.trigger_key)
        g2.Add(self.txt_key, 1, wx.EXPAND)
        
        self.l_mode = wx.StaticText(self.panel); g2.Add(self.l_mode, 0, wx.ALIGN_CENTER_VERTICAL)
        self.combo_mode = wx.ComboBox(self.panel, style=wx.CB_READONLY)
        # 初始化列表
        self.update_mode_choices() 
        g2.Add(self.combo_mode, 1, wx.EXPAND)
        
        self.l_mic = wx.StaticText(self.panel); g2.Add(self.l_mic, 0, wx.ALIGN_CENTER_VERTICAL)
        self.choice_mic = wx.Choice(self.panel)
        self.refresh_devices()
        g2.Add(self.choice_mic, 1, wx.EXPAND)
        
        self.l_ai = wx.StaticText(self.panel); g2.Add(self.l_ai, 0, wx.ALIGN_CENTER_VERTICAL)
        self.chk_llm = wx.CheckBox(self.panel)
        self.chk_llm.SetValue(cfg.enable_llm)
        g2.Add(self.chk_llm, 1, wx.EXPAND)
        
        self.l_paste = wx.StaticText(self.panel); g2.Add(self.l_paste, 0, wx.ALIGN_CENTER_VERTICAL)
        self.chk_paste = wx.CheckBox(self.panel)
        self.chk_paste.SetValue(cfg.use_paste)
        g2.Add(self.chk_paste, 1, wx.EXPAND)
        
        self.l_vad = wx.StaticText(self.panel); g2.Add(self.l_vad, 0, wx.ALIGN_CENTER_VERTICAL)
        self.slider_vad = wx.Slider(self.panel, value=cfg.vad_threshold, minValue=300, maxValue=3000, style=wx.SL_HORIZONTAL|wx.SL_LABELS)
        g2.Add(self.slider_vad, 1, wx.EXPAND)
        
        box_ctrl.Add(g2, 1, wx.EXPAND|wx.ALL, 5)
        self.left_sizer.Add(box_ctrl, 0, wx.EXPAND|wx.BOTTOM, 10)
        
        # 保存按钮
        self.btn_save = wx.Button(self.panel, label="", size=(-1, 45))
        self.btn_save.SetFont(wx.Font(11, wx.DEFAULT, wx.NORMAL, wx.BOLD))
        self.btn_save.Bind(wx.EVT_BUTTON, self.on_save_and_restart)
        self.left_sizer.Add(self.btn_save, 0, wx.EXPAND)

        self.lbl_tip = wx.StaticText(self.panel, label="", style=wx.ALIGN_CENTER)
        self.lbl_tip.SetForegroundColour("#666666") 
        self.left_sizer.Add(self.lbl_tip, 0, wx.ALIGN_CENTER | wx.TOP, 5)

    def create_right_panel(self):
        # 超时自动停止设置
        box_timeout = wx.BoxSizer(wx.HORIZONTAL)
        self.l_timeout = wx.StaticText(self.panel)
        box_timeout.Add(self.l_timeout, 0, wx.ALIGN_CENTER_VERTICAL)
        
        self.spin_timeout = wx.SpinCtrl(self.panel, value=str(cfg.auto_stop_timeout), min=0, max=3600, size=(80, -1))
        self.spin_timeout.Bind(wx.EVT_SPINCTRL, self.on_timeout_change)
        self.spin_timeout.Bind(wx.EVT_TEXT, self.on_timeout_change)     
        
        box_timeout.Add(self.spin_timeout, 0, wx.ALIGN_CENTER_VERTICAL)
        self.l_timeout_suf = wx.StaticText(self.panel)
        box_timeout.Add(self.l_timeout_suf, 0, wx.ALIGN_CENTER_VERTICAL)
        
        self.right_sizer.Add(box_timeout, 0, wx.EXPAND | wx.BOTTOM, 10)

        # 状态指示器
        self.lbl_status = wx.StaticText(self.panel, label="", style=wx.ALIGN_CENTER)
        self.lbl_status.SetFont(wx.Font(14, wx.DEFAULT, wx.NORMAL, wx.BOLD))
        self.lbl_status.SetBackgroundColour("#EEEEEE")
        self.lbl_status.SetForegroundColour("#999999")
        self.right_sizer.Add(self.lbl_status, 0, wx.EXPAND | wx.BOTTOM, 10)

        # 模版配置
        self.box_prompt_obj = wx.StaticBox(self.panel, label="")
        box_prompt = wx.StaticBoxSizer(self.box_prompt_obj, wx.VERTICAL)
        
        # 模版选择栏
        toolbar = wx.BoxSizer(wx.HORIZONTAL)
        self.l_tpl_mode = wx.StaticText(self.panel)
        toolbar.Add(self.l_tpl_mode, 0, wx.ALIGN_CENTER_VERTICAL, 5)
        self.combo_template = wx.ComboBox(self.panel, style=wx.CB_READONLY)
        self.update_template_combo()
        self.combo_template.Bind(wx.EVT_COMBOBOX, self.on_template_select)
        toolbar.Add(self.combo_template, 1, wx.EXPAND|wx.RIGHT, 5)
        
        btn_add = wx.Button(self.panel, label="+", size=(25, -1)); btn_add.Bind(wx.EVT_BUTTON, self.on_add_template)
        btn_del = wx.Button(self.panel, label="-", size=(25, -1)); btn_del.Bind(wx.EVT_BUTTON, self.on_del_template)
        self.btn_ren = wx.Button(self.panel, label="", size=(75, -1)); self.btn_ren.Bind(wx.EVT_BUTTON, self.on_rename_template)
        toolbar.Add(btn_add, 0); toolbar.Add(btn_del, 0); toolbar.Add(self.btn_ren, 0)
        box_prompt.Add(toolbar, 0, wx.EXPAND|wx.BOTTOM, 10)
        
        # 系统提示词
        self.l_sys_prompt = wx.StaticText(self.panel, label="")
        box_prompt.Add(self.l_sys_prompt, 0, wx.ALIGN_LEFT|wx.BOTTOM, 2)
        self.txt_prompt = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE, size=(-1, 100))
        self.txt_prompt.Bind(wx.EVT_TEXT, self.on_prompt_edit)
        box_prompt.Add(self.txt_prompt, 1, wx.EXPAND|wx.BOTTOM, 10)
        
        # 用户前缀
        prefix_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.l_user_prefix = wx.StaticText(self.panel)
        prefix_sizer.Add(self.l_user_prefix, 0, wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, 5)
        self.txt_prefix = wx.TextCtrl(self.panel)
        self.txt_prefix.Bind(wx.EVT_TEXT, self.on_prompt_edit) 
        prefix_sizer.Add(self.txt_prefix, 1, wx.EXPAND)
        box_prompt.Add(prefix_sizer, 0, wx.EXPAND)
        
        self.on_template_select(None)
        self.right_sizer.Add(box_prompt, 0, wx.EXPAND|wx.BOTTOM, 10)

        # 日志
        self.log_ctrl = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE|wx.TE_READONLY|wx.TE_RICH)
        self.right_sizer.Add(self.log_ctrl, 1, wx.EXPAND)

    # ================= UI 更新逻辑 =================

    def update_ui_text(self):
        """根据当前语言刷新所有界面文本"""
        T = TRANS[cfg.language]
        
        self.SetTitle(T["title"])
        self.lbl_lang_sel.SetLabel(T["lang_sel"])
        
        # Left Panel - API
        self.box_api_obj.SetLabel(T["grp_api"])
        self.l_asr_key.SetLabel(T["lbl_asr_key"])
        self.l_asr_wss.SetLabel(T["lbl_asr_wss"])
        self.l_asr_model.SetLabel(T["lbl_asr_model"])
        self.l_llm_key.SetLabel(T["lbl_llm_key"])
        self.l_llm_base.SetLabel(T["lbl_llm_base"])
        self.l_llm_model.SetLabel(T["lbl_llm_model"])
        
        # Left Panel - Control
        self.box_ctrl_obj.SetLabel(T["grp_ctrl"])
        self.l_trigger.SetLabel(T["lbl_trigger"])
        self.l_mode.SetLabel(T["lbl_mode"])
        self.l_mic.SetLabel(T["lbl_mic"])
        self.l_ai.SetLabel(T["lbl_ai"])
        self.chk_llm.SetLabel(T["chk_llm"])
        self.l_paste.SetLabel(T["lbl_paste"])
        self.chk_paste.SetLabel(T["chk_paste"])
        self.l_vad.SetLabel(T["lbl_vad"])
        
        self.btn_save.SetLabel(T["btn_save"])
        self.lbl_tip.SetLabel(T["tip_save"])
        
        # Right Panel
        self.l_timeout.SetLabel(T["lbl_timeout"])
        self.l_timeout_suf.SetLabel(T["lbl_timeout_suf"])
        
        # Refresh Status (调用现有逻辑，它会读取当前语言)
        self.update_status_indicator(UniversalListener.is_recording_active)
        
        self.box_prompt_obj.SetLabel(T["grp_template"])
        self.l_tpl_mode.SetLabel(T["lbl_tpl_mode"])
        self.btn_ren.SetLabel(T["btn_ren"])
        self.l_sys_prompt.SetLabel(T["lbl_sys_prompt"])
        self.l_user_prefix.SetLabel(T["lbl_user_prefix"])

        # 刷新下拉框（特别是麦克风里的“系统默认”和触发模式的“长按/Hold”）
        self.update_mode_choices()
        self.refresh_devices()
        
        # 强制刷新布局，防止文字长度变化导致遮挡
        self.panel.Layout()

    def update_mode_choices(self):
        """刷新触发模式下拉框，保持选中值不变"""
        # 保存当前选中项的真实值 (HOLD 或 TOGGLE)
        current_val = "HOLD"
        # 尝试从 ComboBox 获取当前选中
        if self.combo_mode.GetCount() > 0 and self.combo_mode.GetSelection() != wx.NOT_FOUND:
            try:
                current_val = self.combo_mode.GetClientData(self.combo_mode.GetSelection())
            except: pass
        # 如果获取失败，回退到配置
        elif cfg.input_mode:
            current_val = cfg.input_mode

        self.combo_mode.Clear()
        T = TRANS[cfg.language]
        # Append(显示文本, 真实值 ClientData)
        self.combo_mode.Append(T["mode_hold"], "HOLD")
        self.combo_mode.Append(T["mode_toggle"], "TOGGLE")
        
        if current_val == "TOGGLE": self.combo_mode.SetSelection(1)
        else: self.combo_mode.SetSelection(0)

    def on_language_change(self, event):
        sel = self.choice_lang.GetSelection()
        cfg.language = "cn" if sel == 0 else "en"
        cfg.save() # 保存偏好
        self.update_ui_text() # 立即应用

    # ================= 其他原有逻辑 =================

    def update_status_indicator(self, active):
        T = TRANS[cfg.language]
        if active:
            self.lbl_status.SetLabel(T["status_rec"])
            self.lbl_status.SetBackgroundColour("#FFDDDD") 
            self.lbl_status.SetForegroundColour("RED")
        else:
            if not self.listener:
                self.lbl_status.SetLabel(T["status_off"])
            else:
                self.lbl_status.SetLabel(T["status_idle"])
            self.lbl_status.SetBackgroundColour("#DDFFDD") 
            self.lbl_status.SetForegroundColour("GREEN")
        self.panel.Layout()

    def on_timeout_change(self, event):
        cfg.auto_stop_timeout = self.spin_timeout.GetValue()

    def update_template_combo(self):
        self.combo_template.Clear()
        for t in cfg.templates: self.combo_template.Append(t['name'])
        if 0 <= cfg.current_template_index < len(cfg.templates):
            self.combo_template.SetSelection(cfg.current_template_index)

    def on_template_select(self, event):
        idx = self.combo_template.GetSelection()
        if idx == wx.NOT_FOUND and cfg.templates:
            idx = 0; self.combo_template.SetSelection(0)
        if idx != wx.NOT_FOUND:
            cfg.current_template_index = idx
            tpl = cfg.templates[idx]
            self.txt_prompt.ChangeValue(tpl.get('content', ''))
            self.txt_prefix.ChangeValue(tpl.get('prefix', ''))

    def on_prompt_edit(self, event):
        val_prompt = self.txt_prompt.GetValue()
        val_prefix = self.txt_prefix.GetValue()
        if 0 <= cfg.current_template_index < len(cfg.templates):
            cfg.templates[cfg.current_template_index]['content'] = val_prompt
            cfg.templates[cfg.current_template_index]['prefix'] = val_prefix

    def on_add_template(self, event):
        dlg = wx.TextEntryDialog(self, "New Template Name / 新模版名称:", "New/新建")
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue().strip()
            if name:
                new_tpl = {"name": name, "prefix": "", "content": "..."}
                cfg.templates.append(new_tpl)
                cfg.current_template_index = len(cfg.templates) - 1
                self.update_template_combo()
                self.on_template_select(None)
        dlg.Destroy()

    def on_del_template(self, event):
        if len(cfg.templates) <= 1: return
        cfg.templates.pop(cfg.current_template_index)
        cfg.current_template_index = max(0, cfg.current_template_index - 1)
        self.update_template_combo()
        self.on_template_select(None)

    def on_rename_template(self, event):
        idx = cfg.current_template_index
        old_name = cfg.templates[idx]['name']
        dlg = wx.TextEntryDialog(self, "New Name / 新名称:", "Rename/重命名", value=old_name)
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue().strip()
            if name:
                cfg.templates[idx]['name'] = name
                self.update_template_combo()
        dlg.Destroy()

    def refresh_devices(self):
        # 记录旧的选中状态
        old_sel_idx = self.choice_mic.GetSelection()
        old_dev_id = -1
        if old_sel_idx != wx.NOT_FOUND and hasattr(self, 'dev_map'):
             old_dev_id = self.dev_map[old_sel_idx]
        
        self.choice_mic.Clear(); self.dev_map = []
        p = pyaudio.PyAudio()
        
        # 翻译 "系统默认"
        default_str = TRANS[cfg.language]["mic_default"]
        self.choice_mic.Append(default_str); self.dev_map.append(-1)
        
        try:
            cnt = p.get_host_api_info_by_index(0).get('deviceCount')
            for i in range(cnt):
                info = p.get_device_info_by_host_api_device_index(0, i)
                if info.get('maxInputChannels') > 0:
                    name = info.get('name')
                    try: name = name.encode('cp1252').decode('gbk')
                    except: pass
                    self.choice_mic.Append(f"[{i}] {name}")
                    self.dev_map.append(i)
        except: pass
        p.terminate()

        # 尝试恢复选中状态
        found = False
        # 1. 尝试匹配旧 ID
        for idx, dev_id in enumerate(self.dev_map):
            if dev_id == old_dev_id:
                self.choice_mic.SetSelection(idx)
                found = True
                break
        
        # 2. 尝试匹配 Config
        if not found:
            for idx, dev_id in enumerate(self.dev_map):
                if dev_id == cfg.audio_device_index:
                    self.choice_mic.SetSelection(idx)
                    found = True
                    break
        
        if not found: self.choice_mic.SetSelection(0)

    def append_log(self, text):
        wx.CallAfter(lambda: self.log_ctrl.AppendText(f"[{time.strftime('%H:%M:%S')}] {text}\n"))

    def start_services(self):
        self.asr_manager.start()
        self.listener.start()
        T = TRANS[cfg.language]
        if not cfg.asr_api_key: self.append_log(T["msg_config_missing"])
        else: self.append_log(f"{T['msg_service_started']}{cfg.trigger_key}")

    def on_save_and_restart(self, event):
        # [修改] 使用 GetClientData 获取真实的 HOLD/TOGGLE 值
        mode_idx = self.combo_mode.GetSelection()
        if mode_idx != wx.NOT_FOUND:
            cfg.input_mode = self.combo_mode.GetClientData(mode_idx)
        else:
            cfg.input_mode = "HOLD"

        cfg.asr_api_key = self.txt_asr_key.GetValue().strip()
        cfg.asr_url = self.txt_asr_url.GetValue().strip()
        cfg.asr_model = self.txt_asr_model.GetValue().strip()
        cfg.llm_api_key = self.txt_llm_key.GetValue().strip()
        cfg.llm_base_url = self.txt_llm_url.GetValue().strip()
        cfg.llm_model = self.txt_llm_model.GetValue().strip()
        cfg.trigger_key = self.txt_key.GetValue().strip()
        # cfg.input_mode 已在上方处理
        cfg.enable_llm = self.chk_llm.GetValue()
        cfg.use_paste = self.chk_paste.GetValue()
        cfg.vad_threshold = self.slider_vad.GetValue()
        sel = self.choice_mic.GetSelection()
        cfg.audio_device_index = self.dev_map[sel] if sel != wx.NOT_FOUND else -1
        cfg.save()
        
        self.append_log(TRANS[cfg.language]["msg_saved"])
        threading.Thread(target=self.do_restart, daemon=True).start()

    def do_restart(self):
        if self.listener: self.listener.stop()
        if self.asr_manager: self.asr_manager.stop()
        time.sleep(1) 
        
        def on_activity_signal():
            if self.listener:
                self.listener.refresh_activity()

        self.asr_manager = QwenAsrManager(self.append_log, activity_callback=on_activity_signal)
        self.listener = UniversalListener(self.asr_manager, self.append_log, self.update_status_indicator)
        self.start_services()
        self.append_log(TRANS[cfg.language]["msg_service_reset"])

    def on_close(self, event):
        size = self.GetSize()
        cfg.window_geometry = [size.width, size.height]
        cfg.save()
        self.asr_manager.stop()
        self.listener.stop()
        typing_queue.put(None)
        self.Destroy()

if __name__ == '__main__':
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except: pass
    app = wx.App(False)
    MainFrame().Show()
    app.MainLoop()