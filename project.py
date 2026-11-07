"""Модель проекта: магнитный таймлайн (клипы идут подряд, как в CapCut)."""
import json
from dataclasses import dataclass, asdict

@dataclass
class Clip:
    path: str
    start: float
    end: float
    src_dur: float = 0.0
    @property
    def dur(self): return self.end - self.start

class Project:
    def __init__(self):
        self.clips = []; self.media = {}   # path -> длительность
        self.vertical = False; self.height = 1080; self.fps = 30; self.bitrate = 8
        self._undo = []; self._redo = []

    # --- undo/redo ---
    def dump(self):
        return json.dumps({"clips": [asdict(c) for c in self.clips], "media": self.media, "vertical": self.vertical,
                           "height": self.height, "fps": self.fps, "bitrate": self.bitrate}, ensure_ascii=False)
    def restore(self, js):
        d = json.loads(js); self.clips = [Clip(**c) for c in d["clips"]]; self.media = d["media"]
        self.vertical = d["vertical"]; self.height = d["height"]; self.fps = d["fps"]; self.bitrate = d["bitrate"]
    def snap(self):
        self._undo.append(self.dump()); self._undo = self._undo[-60:]; self._redo.clear()
    def undo(self):
        if not self._undo: return False
        self._redo.append(self.dump()); self.restore(self._undo.pop()); return True
    def redo(self):
        if not self._redo: return False
        self._undo.append(self.dump()); self.restore(self._redo.pop()); return True

    # --- геометрия ---
    def total(self): return sum(c.dur for c in self.clips)
    def starts(self):
        out, acc = [], 0.0
        for c in self.clips: out.append(acc); acc += c.dur
        return out
    def locate(self, t):
        acc = 0.0
        for i, c in enumerate(self.clips):
            if t < acc + c.dur or i == len(self.clips) - 1:
                return i, c.start + min(max(t - acc, 0), c.dur)
            acc += c.dur
        return None

    # --- операции ---
    def add_clip(self, path, start, end):
        dur = self.media.get(path, 0.0)
        if dur: end = min(end, dur)
        start = max(0.0, start)
        if end - start < 0.2: raise ValueError("слишком короткий или неверный диапазон")
        self.snap(); self.clips.append(Clip(path, start, end, dur)); return len(self.clips) - 1
    def split(self, t):
        loc = self.locate(t)
        if not loc: return False
        i, src = loc; c = self.clips[i]
        if src - c.start < 0.15 or c.end - src < 0.15: return False
        self.snap(); self.clips.insert(i + 1, Clip(c.path, src, c.end, c.src_dur)); c.end = src; return True
    def delete(self, i): self.snap(); self.clips.pop(i)
    def move(self, i, d):
        j = i + d
        if not (0 <= i < len(self.clips) and 0 <= j < len(self.clips)): return False
        self.snap(); self.clips[i], self.clips[j] = self.clips[j], self.clips[i]; return True
    def trim_start(self, i, sec):
        c = self.clips[i]
        if c.dur - sec < 0.2 or c.start + sec < 0: raise ValueError("слишком сильная обрезка")
        self.snap(); c.start += sec
    def trim_end(self, i, sec):
        c = self.clips[i]
        if c.dur - sec < 0.2: raise ValueError("слишком сильная обрезка")
        self.snap(); c.end -= sec
    def join(self, i):
        if i + 1 >= len(self.clips): return False
        a, b = self.clips[i], self.clips[i + 1]
        if a.path != b.path or abs(a.end - b.start) > 0.1: return False
        self.snap(); a.end = b.end; self.clips.pop(i + 1); return True

    def save(self, path):
        open(path, "w", encoding="utf-8").write(self.dump())
    def load(self, path):
        self.restore(open(path, encoding="utf-8").read()); self._undo.clear(); self._redo.clear()
