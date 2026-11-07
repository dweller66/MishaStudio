"""MishaClip Studio — единый загрузчик. Один файл/exe: сам скачивает всё нужное и запускает редактор."""
import sys, os, json, shutil, subprocess, threading, queue, time, zipfile, urllib.request, urllib.error, ctypes, traceback
import tkinter as tk
from tkinter import ttk

VERSION = "0.3.1"
FROZEN = getattr(sys, "frozen", False)
SELF_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
SRC_APP = os.path.join(getattr(sys, "_MEIPASS", SELF_DIR), "app")
DATA = os.path.join(os.environ.get("LOCALAPPDATA", SELF_DIR), "MishaClipStudio")
PYDIR, FFDIR = os.path.join(DATA, "python"), os.path.join(DATA, "ffmpeg")
APPDIR, DL, LOGS = os.path.join(DATA, "app"), os.path.join(DATA, "downloads"), os.path.join(DATA, "logs")
for d in (DATA, DL, LOGS): os.makedirs(d, exist_ok=True)
STATE = os.path.join(DATA, "state.json")
NOWIN = 0x08000000
API = "http://127.0.0.1:11434"
URLS = {"python": "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip",
        "getpip": "https://bootstrap.pypa.io/get-pip.py",
        "ffmpeg": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "ollama": "https://ollama.com/download/OllamaSetup.exe"}

def load_state():
    try: return json.load(open(STATE, encoding="utf-8"))
    except Exception: return {}
def save_state(s): json.dump(s, open(STATE, "w", encoding="utf-8"), indent=2)

def log_file(msg):
    open(os.path.join(LOGS, "launcher.log"), "a", encoding="utf-8").write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")

def explain(e):
    s = str(e).lower()
    if any(k in s for k in ("getaddrinfo", "timed out", "urlopen", "connection", "remote end")): return "Нет интернета или сайт недоступен. Проверь соединение и нажми «Повторить» — докачка продолжится с того же места."
    if "permission" in s or "access is denied" in s: return "Нет прав записи. Закрой антивирус-блокировку или запусти от имени пользователя, у которого есть доступ к своей папке AppData."
    if "no space" in s: return "Закончилось место на диске (нужно около 5 ГБ)."
    return "Неизвестная ошибка. Подробности — в файле " + os.path.join(LOGS, "launcher.log")

def ram_gb():
    # MEMORYSTATUSEX: DWORD, DWORD, затем семь DWORDLONG.
    # Явные типы фиксируют размер структуры на Windows x64.
    class M(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    try:
        m = M()
        m.dwLength = ctypes.sizeof(M)
        fn = ctypes.windll.kernel32.GlobalMemoryStatusEx
        fn.argtypes = [ctypes.POINTER(M)]
        fn.restype = ctypes.c_bool
        if not fn(ctypes.byref(m)):
            raise ctypes.WinError()
        return m.ullTotalPhys / (1024 ** 3)
    except Exception:
        # Не занижаем память произвольным значением: безопасный режим.
        return 8.0
def has_nvidia():
    try: return subprocess.run(["nvidia-smi"], capture_output=True, timeout=10, creationflags=NOWIN).returncode == 0
    except Exception: return False

def download(url, dest, cb):
    """Скачивание с докачкой."""
    part = dest + ".part"; pos = os.path.getsize(part) if os.path.exists(part) else 0
    hdr = {"User-Agent": "MishaClipStudio"}
    if pos: hdr["Range"] = f"bytes={pos}-"
    try: r = urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30)
    except urllib.error.HTTPError as e:
        if e.code == 416: os.replace(part, dest); return
        raise
    resumed = pos > 0 and r.status == 206
    if not resumed: pos = 0
    total = int(r.headers.get("Content-Length", 0)) + pos; done = pos
    with open(part, "ab" if resumed else "wb") as f:
        while True:
            b = r.read(1 << 16)
            if not b: break
            f.write(b); done += len(b)
            if total: cb(done / total)
    os.replace(part, dest)

def ollama_up():
    try: urllib.request.urlopen(API + "/api/tags", timeout=2); return True
    except Exception: return False
def ollama_exe():
    for p in (shutil.which("ollama"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
              os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe")):
        if p and os.path.exists(p): return p
def start_ollama(wait=40):
    if ollama_up(): return True
    exe = ollama_exe()
    if not exe: return False
    subprocess.Popen([exe, "serve"], creationflags=NOWIN)
    for _ in range(wait):
        if ollama_up(): return True
        time.sleep(1)
    return False

def py_exe(): return os.path.join(PYDIR, "pythonw.exe")

def copy_app():
    if os.path.isdir(APPDIR): shutil.rmtree(APPDIR, ignore_errors=True)
    shutil.copytree(SRC_APP, APPDIR)

def launch():
    copy_app(); start_ollama(10)
    subprocess.Popen([py_exe(), os.path.join(APPDIR, "main.py"), "--data", DATA], cwd=DATA, creationflags=NOWIN)

# ---------------- шаги установки ----------------
def step_python(opts, cb, log):
    if os.path.exists(py_exe()) and os.path.exists(os.path.join(PYDIR, "Lib", "site-packages", "pip")): return
    z = os.path.join(DL, "python-embed.zip")
    if not os.path.exists(os.path.join(PYDIR, "python.exe")):
        log("Скачиваю встроенный Python (~10 МБ)…"); download(URLS["python"], z, lambda f: cb(f * 0.5))
        shutil.rmtree(PYDIR, ignore_errors=True)
        with zipfile.ZipFile(z) as zf: zf.extractall(PYDIR)
        pth = [f for f in os.listdir(PYDIR) if f.endswith("._pth")][0]
        open(os.path.join(PYDIR, pth), "w").write("python311.zip\n.\nLib\\site-packages\nimport site\n")
    log("Ставлю pip…"); gp = os.path.join(DL, "get-pip.py"); download(URLS["getpip"], gp, lambda f: cb(0.5 + f * 0.2))
    r = subprocess.run([os.path.join(PYDIR, "python.exe"), gp, "--no-warn-script-location"], capture_output=True, text=True, creationflags=NOWIN)
    if r.returncode: raise RuntimeError("pip: " + r.stderr[-500:])

def step_pip(opts, cb, log):
    pk = ["PySide6-Essentials", "PySide6-Addons", "requests"] + (["faster-whisper"] if opts["whisper"] else [])
    st = load_state()
    if st.get("pip") == pk: return
    log("Ставлю библиотеки (интерфейс, видео, распознавание речи) — это самый долгий шаг…")
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1")
    pr = subprocess.Popen([os.path.join(PYDIR, "python.exe"), "-m", "pip", "install", "--no-warn-script-location", *pk],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=NOWIN, env=env)
    n = 0; tail = []
    for line in pr.stdout:
        line = line.strip(); tail = (tail + [line])[-8:]
        if line.startswith(("Downloading", "Collecting", "Installing")): n += 1; cb(min(0.95, n / 40)); log("  " + line[:90])
    if pr.wait(): raise RuntimeError("pip install не удался:\n" + "\n".join(tail))

def step_ffmpeg(opts, cb, log):
    def find():
        for d, _, fs in os.walk(FFDIR):
            if "ffmpeg.exe" in fs and "ffprobe.exe" in fs: return d
    if find(): return
    z = os.path.join(DL, "ffmpeg.zip"); log("Скачиваю FFmpeg (~80 МБ)…"); download(URLS["ffmpeg"], z, lambda f: cb(f * 0.9))
    with zipfile.ZipFile(z) as zf: zf.extractall(FFDIR)
    if not find(): raise RuntimeError("В архиве FFmpeg нет ffmpeg.exe")

def step_ollama(opts, cb, log):
    if ollama_up() or ollama_exe(): start_ollama(); return
    setup = os.path.join(DL, "OllamaSetup.exe"); log("Скачиваю Ollama (локальный запуск нейросетей)…"); download(URLS["ollama"], setup, lambda f: cb(f * 0.8))
    log("Устанавливаю Ollama…"); subprocess.run([setup, "/SILENT", "/NORESTART"], creationflags=NOWIN); cb(0.9)
    if not start_ollama(60): raise RuntimeError("Ollama установилась, но не запустилась. Перезагрузи ПК и открой загрузчик снова.")

def step_model(opts, cb, log):
    model = opts["model"]; tags = json.load(urllib.request.urlopen(API + "/api/tags"))
    if any(m["name"].startswith(model) for m in tags.get("models", [])): return
    log(f"Загружаю нейросеть {model}…")
    req = urllib.request.Request(API + "/api/pull", json.dumps({"name": model}).encode(), {"Content-Type": "application/json"})
    last = ""
    with urllib.request.urlopen(req) as r:
        for line in r:
            d = json.loads(line)
            if "error" in d: raise RuntimeError(d["error"])
            if d.get("total"): cb(d.get("completed", 0) / d["total"])
            if d.get("status") != last: last = d.get("status", ""); log("  " + last)

def step_config(opts, cb, log):
    ffd = next((d for d, _, fs in os.walk(FFDIR) if "ffmpeg.exe" in fs), None)
    json.dump({"ffmpeg_dir": ffd, "model": opts["model"], "low_end": opts["low_end"], "nvenc": opts["nvenc"],
               "whisper_model": "tiny" if opts["low_end"] else "base", "autosave_minutes": 15, "ollama": API},
              open(os.path.join(DATA, "config.json"), "w", encoding="utf-8"), indent=2)

STEPS = [("python", "Встроенный Python", step_python), ("pip", "Библиотеки редактора", step_pip), ("ffmpeg", "FFmpeg (видео)", step_ffmpeg),
         ("ollama", "Ollama (нейросети)", step_ollama), ("model", "Модель Qwen", step_model), ("config", "Настройка под твой ПК", step_config)]

# ---------------- окно ----------------
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__(); self.title("MishaClip Studio — установка"); self.geometry("640x560"); self.configure(bg="#1a1a20"); self.resizable(False, False)
        try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception: pass
        self.q = queue.Queue(); self.ram = ram_gb(); self.gpu = has_nvidia(); self.low = self.ram < 12 or not self.gpu
        model = "qwen2.5:1.5b" if self.ram < 10 else "qwen2.5:3b"
        fg, bg = "#e8e8ee", "#1a1a20"
        tk.Label(self, text="MishaClip Studio", font=("Segoe UI", 20, "bold"), fg="#00e0d0", bg=bg).pack(pady=(18, 0))
        tk.Label(self, text=f"Открытый видеоредактор с локальной Qwen · v{VERSION}", fg="#9a9aa8", bg=bg).pack()
        tk.Label(self, text=f"Твой ПК: {self.ram:.0f} ГБ ОЗУ, {'видеокарта NVIDIA' if self.gpu else 'без NVIDIA'} → "
                 f"{'режим слабого ПК' if self.low else 'обычный режим'}, нейросеть {model}", fg=fg, bg=bg, wraplength=580).pack(pady=8)
        self.whisper = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="Распознавание речи (нужно для «Найди смешные моменты»)", variable=self.whisper, fg=fg, bg=bg, selectcolor="#22222a", activebackground=bg, activeforeground=fg).pack()
        self.opts = {"model": model, "low_end": self.low, "nvenc": self.gpu}
        self.rows = {}; fr = tk.Frame(self, bg=bg); fr.pack(pady=10, fill="x", padx=40)
        for k, t, _ in STEPS:
            l = tk.Label(fr, text="○  " + t, anchor="w", fg="#9a9aa8", bg=bg, font=("Segoe UI", 11)); l.pack(fill="x"); self.rows[k] = (l, t)
        st = ttk.Style(self); st.theme_use("clam"); st.configure("T.Horizontal.TProgressbar", troughcolor="#22222a", background="#00d8c8", bordercolor=bg, lightcolor="#00d8c8", darkcolor="#00d8c8")
        self.pb = ttk.Progressbar(self, style="T.Horizontal.TProgressbar", length=560, maximum=1000); self.pb.pack(pady=6)
        self.lbl = tk.Label(self, text="Всё скачается автоматически (~3–4 ГБ). Нужен интернет только сейчас.", fg="#9a9aa8", bg=bg, wraplength=580); self.lbl.pack()
        self.logbox = tk.Text(self, height=7, width=78, bg="#22222a", fg="#9a9aa8", bd=0, state="disabled"); self.logbox.pack(pady=8)
        bf = tk.Frame(self, bg=bg); bf.pack()
        self.btn = tk.Button(bf, text="Установить и запустить", bg="#00d8c8", fg="#00201d", bd=0, padx=18, pady=8, font=("Segoe UI", 11, "bold"), command=self.start); self.btn.pack(side="left", padx=6)
        tk.Button(bf, text="Переустановить с нуля", bg="#2a2a33", fg=fg, bd=0, padx=12, pady=8, command=self.repair).pack(side="left", padx=6)
        self.after(100, self.poll)

    def log(self, m): self.q.put(("log", m)); log_file(m)
    def poll(self):
        try:
            while True:
                k, *a = self.q.get_nowait()
                if k == "log":
                    self.logbox.config(state="normal"); self.logbox.insert("end", a[0] + "\n"); self.logbox.see("end"); self.logbox.config(state="disabled")
                elif k == "prog": self.pb["value"] = a[0] * 1000; self.lbl.config(text=a[1])
                elif k == "row": l, t = self.rows[a[0]]; l.config(text=("✓  " if a[1] else "⏳  ") + t, fg="#00e0d0" if a[1] else "#e8e8ee")
                elif k == "err": self.lbl.config(text=a[0], fg="#ff7a7a"); self.btn.config(state="normal", text="Повторить")
                elif k == "done": self.destroy(); return
        except queue.Empty: pass
        self.after(100, self.poll)

    def repair(self):
        try: os.remove(STATE)
        except OSError: pass
        shutil.rmtree(PYDIR, ignore_errors=True); self.lbl.config(text="Сброшено. Нажми «Установить и запустить».", fg="#9a9aa8")

    def start(self):
        self.btn.config(state="disabled"); self.lbl.config(fg="#9a9aa8"); self.opts["whisper"] = self.whisper.get()
        threading.Thread(target=self.work, daemon=True).start()

    def work(self):
        try:
            st = load_state(); n = len(STEPS)
            for i, (k, title, fn) in enumerate(STEPS):
                self.q.put(("row", k, False)); self.q.put(("prog", i / n, title + "…"))
                fn(self.opts, lambda f, i=i, t=title: self.q.put(("prog", (i + min(f, 1)) / n, t + f" — {int(min(f, 1) * 100)}%")), self.log)
                if k == "pip": st["pip"] = ["PySide6-Essentials", "PySide6-Addons", "requests"] + (["faster-whisper"] if self.opts["whisper"] else [])
                save_state(st); self.q.put(("row", k, True))
            st["version"] = VERSION; st["complete"] = True; st["model"] = self.opts["model"]; save_state(st)
            self.q.put(("prog", 1.0, "Запускаю редактор…")); launch(); self.q.put(("done",))
        except Exception as e:
            self.log("ОШИБКА: " + traceback.format_exc()); self.q.put(("err", explain(e)))

def main():
    st = load_state()
    if "--repair" not in sys.argv and st.get("complete") and os.path.exists(py_exe()) and os.path.exists(os.path.join(DATA, "config.json")):
        try: launch(); return      # уже установлено — сразу открываем редактор, без окна
        except Exception: log_file("Быстрый запуск не удался:\n" + traceback.format_exc())
    Launcher().mainloop()

if __name__ == "__main__": main()
