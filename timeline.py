from PySide6.QtCore import Qt, QRectF, Signal, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF, QFont
from PySide6.QtWidgets import QWidget
import os
from ai import fmt

class Timeline(QWidget):
    seekRequested = Signal(float); selectionChanged = Signal(int); edited = Signal(); finished = Signal()
    LEFT, RULER, VY, VH, AY, AH = 12, 28, 40, 62, 108, 34

    def __init__(self, project):
        super().__init__(); self.p = project; self.pps = 40.0; self.playhead = 0.0; self.sel = -1; self.drag = None
        self.setMouseTracking(True); self.setMinimumHeight(165)

    def x_of(self, t): return self.LEFT + t * self.pps
    def t_of(self, x): return max(0.0, (x - self.LEFT) / self.pps)
    def fit(self): self.setMinimumWidth(int(self.LEFT * 2 + max(self.p.total() + 20, 60) * self.pps)); self.update()
    def set_zoom(self, pps): self.pps = pps; self.fit()
    def set_playhead(self, t): self.playhead = t; self.update()

    def paintEvent(self, e):
        qp = QPainter(self); qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor("#15151a"))
        qp.setFont(QFont("Segoe UI", 8))
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800) if s * self.pps >= 80), 3600)
        t = 0
        while self.x_of(t) < self.width():
            x = self.x_of(t); qp.setPen(QColor("#3a3a46")); qp.drawLine(int(x), self.RULER - 8, int(x), self.RULER)
            qp.setPen(QColor("#8a8a98")); qp.drawText(int(x) + 4, self.RULER - 10, fmt(t)); t += step
        if not self.p.clips:
            qp.setPen(QColor("#6a6a78")); qp.setFont(QFont("Segoe UI", 11))
            qp.drawText(self.rect().adjusted(0, 20, 0, 0), Qt.AlignCenter, "Перетащи видео в окно или дважды щёлкни файл во вкладке «Медиа»")
        qp.setFont(QFont("Segoe UI", 8))
        for i, (c, st) in enumerate(zip(self.p.clips, self.p.starts())):
            x = self.x_of(st); w = max(c.dur * self.pps, 3)
            for y, h, col, txt in ((self.VY, self.VH, "#1fb5a8", True), (self.AY, self.AH, "#2f9e5b", False)):
                r = QRectF(x + 1, y, w - 2, h)
                qp.setBrush(QColor(col)); qp.setPen(QPen(QColor("#ffffff"), 2) if i == self.sel else Qt.NoPen)
                qp.drawRoundedRect(r, 6, 6)
                if txt:
                    qp.setClipRect(r); qp.setPen(QColor("#04201d"))
                    qp.drawText(r.adjusted(8, 6, -4, 0), Qt.AlignLeft | Qt.AlignTop, os.path.basename(c.path))
                    qp.drawText(r.adjusted(8, 0, -4, -6), Qt.AlignLeft | Qt.AlignBottom, f"{c.dur:.1f} c"); qp.setClipping(False)
            qp.setPen(QColor("#6a6a78")); qp.drawText(int(self.LEFT), self.VY - 6, "")
        qp.setPen(QColor("#6a6a78")); qp.drawText(self.LEFT, self.AY + self.AH + 14, "▲ видео   ▲ звук (связан с видео)")
        x = self.x_of(self.playhead); qp.setPen(QPen(QColor("#ffffff"), 2)); qp.drawLine(int(x), 0, int(x), self.height())
        qp.setBrush(QColor("#ffffff")); qp.setPen(Qt.NoPen)
        qp.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 10)]))

    def hit(self, x, y):
        if not (self.VY <= y <= self.AY + self.AH): return None
        for i, (c, st) in enumerate(zip(self.p.clips, self.p.starts())):
            x0 = self.x_of(st); x1 = x0 + c.dur * self.pps
            if x0 - 4 <= x <= x1 + 4:
                if abs(x - x0) <= 6: return i, "l"
                if abs(x - x1) <= 6: return i, "r"
                if x0 <= x <= x1: return i, "b"
        return None

    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if y < self.RULER: self.drag = ("head",); self.seekRequested.emit(self.t_of(x)); return
        h = self.hit(x, y)
        if h:
            i, part = h; self.sel = i; self.selectionChanged.emit(i)
            if part in "lr":
                c = self.p.clips[i]; self.p.snap(); self.drag = (part, i, x, c.start, c.end)
            else: self.drag = ("head",); self.seekRequested.emit(self.t_of(x))
        else:
            self.sel = -1; self.selectionChanged.emit(-1); self.drag = ("head",); self.seekRequested.emit(self.t_of(x))
        self.update()

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if not self.drag:
            h = self.hit(x, y); self.setCursor(Qt.SizeHorCursor if h and h[1] in "lr" else Qt.ArrowCursor); return
        if self.drag[0] == "head": self.seekRequested.emit(self.t_of(x)); return
        part, i, x0, s0, e0 = self.drag; c = self.p.clips[i]; dt = (x - x0) / self.pps
        if part == "l": c.start = min(max(0.0, s0 + dt), c.end - 0.2)
        else:
            lim = c.src_dur if c.src_dur else 1e9
            c.end = min(max(c.start + 0.2, e0 + dt), lim)
        self.fit(); self.edited.emit()

    def mouseReleaseEvent(self, e):
        d = self.drag; self.drag = None
        if d and d[0] in ("l", "r"): self.finished.emit()
