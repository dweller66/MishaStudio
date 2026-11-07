"""MishaClip Studio 0.3 — открытый видеоредактор с управлением через локальную Qwen."""
import sys, os, json, html, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtCore import Qt, QUrl, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget, QPushButton,
    QListWidget, QTextBrowser, QLineEdit, QLabel, QCheckBox, QComboBox, QSpinBox, QProgressBar, QScrollArea, QSlider,
    QFileDialog, QMessageBox)
from project import Project
from timeline import Timeline
import ai, export as exporter

DATA = sys.argv[sys.argv.index("--data") + 1] if "--data" in sys.argv else os.path.join(os.path.dirname(__file__), "..", "data")
DATA = os.path.abspath(DATA); os.makedirs(os.path.join(DATA, "projects"), exist_ok=True)
CFG = {"ffmpeg_dir": None, "model": "qwen2.5:3b", "low_end": True, "nvenc": False, "whisper_model": "tiny",
       "autosave_minutes": 15, "ollama": "http://127.0.0.1:11434"}
try: CFG.update(json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8")))
except Exception: pass
AUTOSAVE = os.path.join(DATA, "projects", "autosave.json")

STYLE = """
* { font-family: 'Segoe UI'; font-size: 13px; color: #e8e8ee; }
QMainWindow, QWidget { background: #1a1a20; }
QTabWidget::pane { border: 0; }
QTabBar::tab { background: #1a1a20; padding: 8px 14px; color: #9a9aa8; }
QTabBar::tab:selected { color: #00e0d0; border-bottom: 2px solid #00e0d0; }
QPushButton { background: #2a2a33; border: 0; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover { background: #363642; }
QPushButton#accent { background: #00d8c8; color: #00201d; font-weight: 600; }
QPushButton#accent:hover { background: #2ee8d9; }
QListWidget, QTextBrowser, QLineEdit, QComboBox, QSpinBox { background: #22222a; border: 1px solid #2e2e38; border-radius: 6px; padding: 4px; }
QProgressBar { background: #22222a; border: 0; border-radius: 4px; max-height: 8px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #00d8c8; border-radius: 4px; }
QScrollArea { border: 0; }
QMenuBar, QMenu { background: #1a1a20; } QMenu::item:selected { background: #2a2a33; }
"""

class Task(QThread):
    done = Signal(object); fail = Signal(str); prog = Signal(float, str)
    def __init__(self, fn): super().__init__(); self.fn = fn
    def run(self):
        try: self.done.emit(self.fn(lambda f, m="": self.prog.emit(f, m)))
        except Exception as e: self.fail.emit(str(e))

class Stage(QWidget):
    def __init__(self):
        super().__init__(); self.setStyleSheet("background:#000"); self.video = QVideoWidget(self); self.vertical = False
    def set_vertical(self, v):
        self.vertical = v
        self.video.setAspectRatioMode(Qt.KeepAspectRatioByExpanding if v else Qt.KeepAspectRatio); self.relayout()
    def resizeEvent(self, e): self.relayout()
    def relayout(self):
        W, H = self.width(), self.height()
        if self.vertical: w = int(H * 9 / 16); self.video.setGeometry((W - w) // 2, 0, w, H)
        else: self.video.setGeometry(0, 0, W, H)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("MishaClip Studio"); self.resize(1280, 780); self.setAcceptDrops(True)
        self.p = Project(); self.playing = False; self.playhead = 0.0; self.ci = 0; self.cur_path = None; self.pending = None
        self.proxies = {}; self.transcripts = {}; self.tasks = set(); self.project_file = None
        self.player = QMediaPlayer(self); self.audio = QAudioOutput(self); self.player.setAudioOutput(self.audio)
        self.build(); self.player.setVideoOutput(self.stage.video)
        self.player.mediaStatusChanged.connect(self.on_status); self.player.positionChanged.connect(self.on_position)
        self.player.errorOccurred.connect(lambda *a: self.say(f"Плеер: {a[-1]}", "err"))
        self.autosave_timer = QTimer(self); self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start(int(CFG["autosave_minutes"]) * 60000)
        self.say("Привет! Импортируй видео и напиши мне, что смонтировать. Например: «Найди 5 лучших моментов и сделай вертикальное видео».")
        if os.path.exists(AUTOSAVE):
            if QMessageBox.question(self, "Автосохранение", "Найден автосохранённый проект. Восстановить?") == QMessageBox.Yes: self.load_project(AUTOSAVE)

    # ---------- интерфейс ----------
    def build(self):
        bar = self.menuBar(); f = bar.addMenu("Файл"); e = bar.addMenu("Правка")
        for menu, text, key, fn in [(f, "Импорт видео…", "Ctrl+I", self.import_dialog), (f, "Открыть проект…", "Ctrl+O", self.open_project),
                (f, "Сохранить проект", "Ctrl+S", self.save_project), (f, "Экспорт…", "Ctrl+E", self.export),
                (e, "Отменить", "Ctrl+Z", self.undo), (e, "Повторить", "Ctrl+Y", self.redo),
                (e, "Разрезать", "S", self.split), (e, "Удалить клип", "Delete", self.delete_sel), (e, "Play/Pause", "Space", self.toggle)]:
            a = QAction(text, self); a.setShortcut(QKeySequence(key)); a.triggered.connect(fn); menu.addAction(a)
        # левая панель
        tabs = QTabWidget(); tabs.setFixedWidth(310)
        mt = QWidget(); ml = QVBoxLayout(mt); b = QPushButton("＋ Импорт видео"); b.setObjectName("accent"); b.clicked.connect(self.import_dialog)
        self.media = QListWidget(); self.media.itemDoubleClicked.connect(self.add_whole)
        ml.addWidget(b); ml.addWidget(self.media); ml.addWidget(QLabel("Двойной клик — добавить на таймлайн"))
        qt_ = QWidget(); ql = QVBoxLayout(qt_); self.chat = QTextBrowser(); self.chat.setOpenExternalLinks(False)
        self.entry = QLineEdit(); self.entry.setPlaceholderText("Скажи Qwen, что сделать…"); self.entry.returnPressed.connect(self.send)
        ql.addWidget(self.chat)
        for t in ("Найди 5 лучших моментов", "Сделай вертикальное видео", "Экспортируй в 1080p 60 FPS"):
            q = QPushButton(t); q.clicked.connect(lambda _, t=t: self.send(t)); ql.addWidget(q)
        ql.addWidget(self.entry)
        tabs.addTab(mt, "Медиа"); tabs.addTab(qt_, "Qwen ✨")
        # центр
        self.stage = Stage(); self.stage.setMinimumSize(420, 260)
        self.btn_play = QPushButton("▶"); self.btn_play.setFixedWidth(46); self.btn_play.clicked.connect(self.toggle)
        self.time_lbl = QLabel("00:00:00 / 00:00:00")
        tr = QHBoxLayout(); tr.addStretch(); tr.addWidget(self.btn_play); tr.addWidget(self.time_lbl); tr.addStretch()
        center = QWidget(); cl = QVBoxLayout(center); cl.setContentsMargins(0, 0, 0, 0); cl.addWidget(self.stage, 1); cl.addLayout(tr)
        # правая панель
        right = QWidget(); right.setFixedWidth(240); rl = QVBoxLayout(right); rl.addWidget(QLabel("<b>Параметры</b>"))
        self.chk_vert = QCheckBox("Вертикальное 9:16 (TikTok)"); self.chk_vert.toggled.connect(self.settings_changed)
        self.cb_h = QComboBox(); self.cb_h.addItems(["480", "720", "1080", "1440"]); self.cb_h.setCurrentText("1080")
        self.cb_fps = QComboBox(); self.cb_fps.addItems(["24", "30", "60"]); self.cb_fps.setCurrentText("30")
        self.sp_br = QSpinBox(); self.sp_br.setRange(1, 60); self.sp_br.setValue(8); self.sp_br.setSuffix(" Мбит/с")
        for w in (self.cb_h, self.cb_fps): w.currentTextChanged.connect(self.settings_changed)
        self.sp_br.valueChanged.connect(self.settings_changed)
        rl.addWidget(self.chk_vert); rl.addWidget(QLabel("Разрешение (высота)")); rl.addWidget(self.cb_h)
        rl.addWidget(QLabel("FPS")); rl.addWidget(self.cb_fps); rl.addWidget(QLabel("Битрейт")); rl.addWidget(self.sp_br)
        rl.addWidget(QLabel(f"Кодирование: {'NVIDIA NVENC' if CFG.get('nvenc') else 'процессор (x264)'}"))
        rl.addWidget(QLabel(f"Слабый ПК: {'вкл — прокси 480p для предпросмотра' if CFG.get('low_end') else 'выкл'}")); rl.addWidget(QLabel(f"Модель: {CFG['model']}"))
        rl.addStretch(); ex = QPushButton("Экспорт"); ex.setObjectName("accent"); ex.clicked.connect(self.export); rl.addWidget(ex)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); rl.addWidget(self.bar); self.status = QLabel(""); self.status.setWordWrap(True); rl.addWidget(self.status)
        top = QWidget(); th = QHBoxLayout(top); th.setContentsMargins(6, 6, 6, 0); th.addWidget(tabs); th.addWidget(center, 1); th.addWidget(right)
        # таймлайн
        self.tl = Timeline(self.p); self.tl.seekRequested.connect(lambda t: self.seek(t)); self.tl.edited.connect(self.on_trim_drag); self.tl.finished.connect(self.after_edit)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.tl); self.scroll = sc
        tb = QHBoxLayout(); tb.setContentsMargins(6, 0, 6, 0)
        for t, fn in [("✂ Разрезать (S)", self.split), ("🗑 Удалить", self.delete_sel), ("◀", lambda: self.move(-1)), ("▶", lambda: self.move(1)), ("↶", self.undo), ("↷", self.redo)]:
            b = QPushButton(t); b.clicked.connect(fn); tb.addWidget(b)
        tb.addStretch(); tb.addWidget(QLabel("Масштаб")); z = QSlider(Qt.Horizontal); z.setRange(3, 200); z.setValue(40); z.setFixedWidth(160)
        z.valueChanged.connect(self.tl.set_zoom); tb.addWidget(z)
        bottom = QWidget(); bl = QVBoxLayout(bottom); bl.setContentsMargins(0, 0, 0, 0); bl.addLayout(tb); bl.addWidget(sc)
        sp = QSplitter(Qt.Vertical); sp.addWidget(top); sp.addWidget(bottom); sp.setSizes([520, 260]); self.setCentralWidget(sp)

    def say(self, text, kind="sys"):
        col = {"me": "#00e0d0", "ai": "#e8e8ee", "err": "#ff7a7a", "sys": "#9a9aa8"}[kind]
        self.chat.append(f'<p style="color:{col}">{html.escape(text)}</p>')
        try: open(os.path.join(DATA, "studio.log"), "a", encoding="utf-8").write(time.strftime("[%H:%M:%S] ") + text + "\n")
        except Exception: pass
    def run_task(self, fn, done, fail=None):
        t = Task(fn); self.tasks.add(t)
        t.prog.connect(lambda f, m: (self.bar.setValue(int(f * 1000)), self.status.setText(m)))
        t.done.connect(done); t.fail.connect(fail or (lambda m: (self.say(m, "err"), self.status.setText("Ошибка"))))
        t.finished.connect(lambda: self.tasks.discard(t)); t.start()

    # ---------- медиа ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
    def dropEvent(self, e): self.import_media([u.toLocalFile() for u in e.mimeData().urls()])
    def import_dialog(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Видео", "", "Видео (*.mp4 *.mkv *.mov *.avi *.webm *.flv *.ts);;Все файлы (*.*)")
        if ps: self.import_media(ps)
    def import_media(self, paths):
        for path in paths:
            if not os.path.isfile(path): continue
            try: dur = exporter.probe(CFG, path)
            except Exception as e: self.say(f"Не удалось прочитать {os.path.basename(path)}: {e}. Запусти загрузчик заново (кнопка «Переустановить»).", "err"); continue
            self.p.media[path] = dur; self.media.addItem(f"{os.path.basename(path)}  ({ai.fmt(dur)})"); self.media.item(self.media.count() - 1).setData(Qt.UserRole, path)
            self.say(f"Импортировано: {os.path.basename(path)} ({ai.fmt(dur)})")
            if CFG.get("low_end"): self.make_proxy(path)
            if not self.p.clips: self.p.add_clip(path, 0, dur); self.after_edit()
    def make_proxy(self, path):
        dst = exporter.proxy_path(DATA, path)
        if os.path.exists(dst): self.proxies[path] = dst; return
        self.say("Режим слабого ПК: делаю лёгкую копию для предпросмотра (на экспорт не влияет)…")
        self.run_task(lambda cb: exporter.make_proxy(CFG, path, dst), lambda d: (self.proxies.__setitem__(path, d), self.say("Прокси готов — предпросмотр станет плавнее.")))
    def preview_path(self, path): return self.proxies.get(path, path)
    def active_media(self):
        it = self.media.currentItem()
        if it: return it.data(Qt.UserRole)
        if self.p.clips: return self.p.clips[-1].path
        return next(iter(self.p.media), None)
    def add_whole(self, it):
        path = it.data(Qt.UserRole); self.p.add_clip(path, 0, self.p.media[path]); self.after_edit()

    # ---------- воспроизведение ----------
    def seek(self, t):
        if not self.p.clips: self.playhead = 0; self.tl.set_playhead(0); self.upd_time(); return
        t = max(0.0, min(t, self.p.total() - 0.001)); self.playhead = t
        i, src = self.p.locate(t); self.ci = i; c = self.p.clips[i]
        if self.cur_path != c.path:
            self.cur_path = c.path; self.pending = src * 1000; self.player.setSource(QUrl.fromLocalFile(self.preview_path(c.path)))
        else: self.player.setPosition(int(src * 1000))
        self.tl.set_playhead(t); self.upd_time()
    def on_status(self, st):
        if st in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self.pending is not None:
            pos = self.pending; self.pending = None; self.player.setPosition(int(pos))
            if self.playing: self.player.play()
    def on_position(self, ms):
        if not self.playing or self.pending is not None or self.ci >= len(self.p.clips): return
        c = self.p.clips[self.ci]; src = ms / 1000
        if src >= c.end - 0.04:
            if self.ci + 1 < len(self.p.clips): self.seek(self.p.starts()[self.ci + 1])
            else: self.pause(); self.playhead = self.p.total(); self.tl.set_playhead(self.playhead); self.upd_time()
        else:
            self.playhead = self.p.starts()[self.ci] + max(0.0, src - c.start); self.tl.set_playhead(self.playhead); self.upd_time()
            self.scroll.ensureVisible(int(self.tl.x_of(self.playhead)), 0, 80, 0)
    def toggle(self): self.pause() if self.playing else self.play()
    def play(self):
        if not self.p.clips: return
        if self.playhead >= self.p.total() - 0.05: self.seek(0)
        self.playing = True; self.btn_play.setText("⏸"); self.player.play()
    def pause(self): self.playing = False; self.btn_play.setText("▶"); self.player.pause()
    def upd_time(self): self.time_lbl.setText(f"{ai.fmt(self.playhead)} / {ai.fmt(self.p.total())}")

    # ---------- правки ----------
    def after_edit(self):
        n = len(self.p.clips); self.tl.sel = min(self.tl.sel, n - 1)
        self.tl.fit(); self.cur_path = None if not n else self.cur_path
        self.seek(min(self.playhead, max(0.0, self.p.total() - 0.01))); self.sync_settings()
    def on_trim_drag(self): self.upd_time()
    def split(self):
        if self.p.split(self.playhead): self.after_edit()
    def delete_sel(self, confirm=True):
        i = self.tl.sel
        if not (0 <= i < len(self.p.clips)): return self.say("Сначала выбери клип.")
        if confirm and QMessageBox.question(self, "Подтверждение", f"Удалить клип {i+1}?") != QMessageBox.Yes: return
        self.p.delete(i); self.tl.sel = -1; self.after_edit()
    def move(self, d):
        i = self.tl.sel
        if self.p.move(i, d): self.tl.sel = i + d; self.after_edit()
    def undo(self):
        if self.p.undo(): self.after_edit()
    def redo(self):
        if self.p.redo(): self.after_edit()
    def settings_changed(self, *a):
        self.p.vertical = self.chk_vert.isChecked(); self.p.height = int(self.cb_h.currentText())
        self.p.fps = int(self.cb_fps.currentText()); self.p.bitrate = self.sp_br.value(); self.stage.set_vertical(self.p.vertical)
    def sync_settings(self):
        for w in (self.chk_vert, self.cb_h, self.cb_fps, self.sp_br): w.blockSignals(True)
        self.chk_vert.setChecked(self.p.vertical); self.cb_h.setCurrentText(str(self.p.height)); self.cb_fps.setCurrentText(str(self.p.fps)); self.sp_br.setValue(self.p.bitrate)
        for w in (self.chk_vert, self.cb_h, self.cb_fps, self.sp_br): w.blockSignals(False)
        self.stage.set_vertical(self.p.vertical)

    # ---------- проект ----------
    def save_project(self):
        if not self.project_file:
            f, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", os.path.join(DATA, "projects", "project.misha"), "Проект (*.misha)")
            if not f: return
            self.project_file = f
        self.p.save(self.project_file); self.say("Проект сохранён: " + self.project_file)
    def autosave(self):
        try: self.p.save(AUTOSAVE)
        except Exception: pass
    def open_project(self):
        f, _ = QFileDialog.getOpenFileName(self, "Открыть проект", os.path.join(DATA, "projects"), "Проект (*.misha *.json)")
        if f: self.load_project(f); self.project_file = f
    def load_project(self, f):
        try: self.p.load(f)
        except Exception as e: return self.say(f"Не удалось открыть проект: {e}", "err")
        self.media.clear()
        for path in self.p.media:
            self.media.addItem(f"{os.path.basename(path)}  ({ai.fmt(self.p.media[path])})"); self.media.item(self.media.count() - 1).setData(Qt.UserRole, path)
            if CFG.get("low_end") and os.path.exists(path): self.make_proxy(path)
        self.tl.sel = -1; self.playhead = 0; self.cur_path = None; self.after_edit(); self.say("Проект загружен")
    def closeEvent(self, e): self.autosave(); super().closeEvent(e)

    # ---------- экспорт ----------
    def export(self):
        if not self.p.clips: return self.say("Нет клипов для экспорта.", "err")
        out, _ = QFileDialog.getSaveFileName(self, "Экспорт", os.path.join(os.path.expanduser("~"), "Videos", "MishaClip.mp4"), "MP4 (*.mp4)")
        if not out: return   # диалог сам спрашивает подтверждение при перезаписи
        self.settings_changed(); snap = Project(); snap.restore(self.p.dump()); self.pause()
        self.say(f"Экспорт: {snap.height}p, {snap.fps} FPS, {snap.bitrate} Мбит/с{', 9:16' if snap.vertical else ''}…")
        self.run_task(lambda cb: exporter.run(CFG, snap, out, cb), lambda o: self.say("Экспорт готов: " + o))

    # ---------- Qwen ----------
    def state(self):
        st = self.p.starts()
        return {"media": [os.path.basename(m) for m in self.p.media], "active": os.path.basename(self.active_media() or ""),
                "playhead": round(self.playhead, 1), "selected": self.tl.sel + 1 if self.tl.sel >= 0 else None,
                "clips": [{"index": i + 1, "file": os.path.basename(c.path), "start": round(c.start, 1), "end": round(c.end, 1), "timeline_start": round(st[i], 1)} for i, c in enumerate(self.p.clips)],
                "vertical": self.p.vertical, "export": {"height": self.p.height, "fps": self.p.fps, "bitrate": self.p.bitrate}}
    def send(self, text=None):
        text = text if isinstance(text, str) else self.entry.text().strip()
        if not text: return
        self.entry.clear(); self.say("Ты: " + text, "me"); self.status.setText("Qwen думает…"); st = self.state()
        def done(r):
            self.status.setText(""); self.say("Qwen: " + str(r.get("say", "")), "ai")
            for a in r.get("actions", []):
                if isinstance(a, dict): self.run_tool(str(a.get("tool")), a.get("args") or {})
        self.run_task(lambda cb: ai.ask_qwen(CFG, st, text), done)

    def run_tool(self, name, a):
        p = self.p
        def idx():
            i = a.get("index"); i = int(i) - 1 if i else self.tl.sel
            if not (0 <= i < len(p.clips)): raise ValueError("клип не выбран или не существует")
            return i
        try:
            if name == "add_clip":
                path = a.get("path") or self.active_media()
                if not path: raise ValueError("сначала импортируй видео")
                p.add_clip(path, ai.parse_time(a["start"]), ai.parse_time(a["end"])); self.say("Клип добавлен")
            elif name == "split_at":
                t = ai.parse_time(a["time"]) if a.get("time") is not None else self.playhead
                if not p.split(t): raise ValueError("здесь нельзя разрезать")
            elif name == "trim_start": p.trim_start(idx(), float(a["seconds"]))
            elif name == "trim_end": p.trim_end(idx(), float(a["seconds"]))
            elif name == "delete_clip":
                self.tl.sel = idx(); self.delete_sel(confirm=True); return
            elif name == "join_clips":
                if not p.join(idx()): raise ValueError("клипы нельзя склеить: они не идут подряд из одного файла")
            elif name == "move_clip":
                i = idx()
                if p.move(i, int(a.get("direction", 1))): self.tl.sel = i + int(a.get("direction", 1))
            elif name == "set_vertical": p.vertical = bool(a.get("on", True)); self.sync_settings()
            elif name == "set_export":
                p.height = int(a.get("height", p.height)); p.fps = int(a.get("fps", p.fps)); p.bitrate = int(a.get("bitrate", p.bitrate)); self.sync_settings()
            elif name == "export": return self.export()
            elif name == "save": return self.save_project()
            elif name == "find_moments": return self.find_moments(int(a.get("count", 5)), str(a.get("topic", "интересные")))
            else: return self.say(f"Неизвестная команда отклонена: {name}", "err")
            self.after_edit()
        except Exception as e: self.say(f"Не получилось ({name}): {e}", "err")

    def find_moments(self, count, topic):
        path = self.active_media()
        if not path: return self.say("Сначала импортируй видео.", "err")
        def work(cb):
            if path not in self.transcripts: self.transcripts[path] = ai.transcribe(CFG, path, cb)
            return ai.pick_moments(CFG, self.transcripts[path], count, topic, cb)
        def done(res):
            for m in res:
                try: self.p.add_clip(path, max(0, m["start"]), m["end"]); self.say(f"Момент {ai.fmt(m['start'])}: {m.get('reason', '')}", "ai")
                except Exception: pass
            self.after_edit(); self.bar.setValue(0); self.status.setText("")
        def fail(m):
            self.say("Поиск моментов: " + ("распознавание речи не установлено — переустанови с галочкой «Распознавание речи»." if "faster_whisper" in m else m), "err")
        self.run_task(work, done, fail)

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyleSheet(STYLE); w = MainWindow(); w.show(); sys.exit(app.exec())
