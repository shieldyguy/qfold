"""Manufacturing drawings as SVG sheets (A3 landscape, mm), converted to PDF.

Views come from the kernel's hidden-line projection, so every line is exact
model geometry. Dimension values are measured on the model, never typed in.
Holes are tagged on the views and listed in schedules rather than dimensioned
one by one, which keeps dense panels readable.
"""

from __future__ import annotations

import base64
import datetime as _dt
import html
import math
from dataclasses import dataclass, field

import numpy as np

STYLE = """
.vis{stroke:#111;stroke-width:.42;fill:none;stroke-linecap:round;stroke-linejoin:round}
.tan{stroke:#9a9a9a;stroke-width:.14;fill:none}
.hid{stroke:#777;stroke-width:.2;fill:none;stroke-dasharray:1.4 .8}
.dim{stroke:#1d3f8f;stroke-width:.16;fill:none}
.arrow{fill:#1d3f8f;stroke:none}
.bend{stroke:#c0392b;stroke-width:.28;fill:none;stroke-dasharray:5 1.2 .8 1.2}
.frame{stroke:#111;stroke-width:.5;fill:none}
.grid{stroke:#111;stroke-width:.25;fill:none}
.cell{stroke:#888;stroke-width:.12;fill:none}
.lead{stroke:#1d3f8f;stroke-width:.12;fill:none}
text{font-family:'DejaVu Sans',sans-serif;fill:#111}
.dt{fill:#1d3f8f}
.bt{fill:#c0392b}
.pass{fill:#1e7d32}.warn{fill:#b26a00}.fail{fill:#b00020}
"""


def _f(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".")


def fmt_len(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s


class Sheet:
    def __init__(self, w: float = 420.0, h: float = 297.0):
        self.w, self.h = w, h
        self.items: list[str] = []

    def raw(self, s: str):
        self.items.append(s)

    def line(self, x1, y1, x2, y2, cls="vis"):
        self.raw(f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}" class="{cls}"/>')

    def poly(self, pts, cls="vis", closed=False):
        if len(pts) < 2:
            return
        d = "M" + " L".join(f"{_f(x)},{_f(y)}" for x, y in pts) + (" Z" if closed else "")
        self.raw(f'<path d="{d}" class="{cls}"/>')

    def rect(self, x, y, w, h, cls="frame"):
        self.raw(f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" class="{cls}"/>')

    def circle(self, x, y, r, cls="vis"):
        self.raw(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(r)}" class="{cls}"/>')

    def arc(self, cx, cy, r, a0, a1, cls="vis"):
        """CCW arc in model sense; paper y points down, so it is drawn CW."""
        span = (a1 - a0) % 360.0 or 360.0
        p0 = (cx + r * math.cos(math.radians(a0)), cy - r * math.sin(math.radians(a0)))
        p1 = (cx + r * math.cos(math.radians(a0 + span)), cy - r * math.sin(math.radians(a0 + span)))
        large = 1 if span > 180 else 0
        self.raw(f'<path d="M{_f(p0[0])},{_f(p0[1])} A{_f(r)},{_f(r)} 0 {large} 0 '
                 f'{_f(p1[0])},{_f(p1[1])}" class="{cls}"/>')

    def text(self, x, y, s, size=3.0, anchor="start", weight="normal", rot=0.0, cls=""):
        tr = f' transform="rotate({_f(rot)} {_f(x)} {_f(y)})"' if rot else ""
        c = f' class="{cls}"' if cls else ""
        self.raw(f'<text x="{_f(x)}" y="{_f(y)}" font-size="{_f(size)}" text-anchor="{anchor}" '
                 f'font-weight="{weight}"{tr}{c}>{html.escape(s)}</text>')

    def image(self, x, y, w, h, png: bytes):
        b64 = base64.b64encode(png).decode()
        self.raw(f'<image x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
                 f'href="data:image/png;base64,{b64}" preserveAspectRatio="xMidYMid meet"/>')

    def arrow(self, x, y, dx, dy, size=2.2):
        n = math.hypot(dx, dy) or 1.0
        ux, uy = dx / n, dy / n
        bx, by = x - ux * size, y - uy * size
        px, py = -uy * size * 0.28, ux * size * 0.28
        self.raw(f'<path d="M{_f(x)},{_f(y)} L{_f(bx + px)},{_f(by + py)} '
                 f'L{_f(bx - px)},{_f(by - py)} Z" class="arrow"/>')

    def svg(self) -> str:
        body = "\n".join(self.items)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}mm" height="{self.h}mm" '
                f'viewBox="0 0 {self.w} {self.h}"><style>{STYLE}</style>'
                f'<rect width="{self.w}" height="{self.h}" fill="#fff"/>\n{body}\n</svg>')


# --------------------------------------------------------------------- dimensions

def dim_h(sh: Sheet, a, b, y, value: float | None = None, text: str | None = None, above=True):
    """Horizontal dimension between paper points a and b, dimension line at paper y."""
    (x1, y1), (x2, y2) = a, b
    for x, yy in ((x1, y1), (x2, y2)):
        s = 1 if y > yy else -1
        sh.line(x, yy + s * 1.0, x, y + s * 1.5, "dim")
    sh.line(x1, y, x2, y, "dim")
    sh.arrow(x1, y, x1 - x2, 0)
    sh.arrow(x2, y, x2 - x1, 0)
    t = text if text is not None else fmt_len(value if value is not None else abs(x2 - x1))
    sh.text((x1 + x2) / 2, y - 1.0 if above else y + 3.4, t, 2.8, "middle", cls="dt")


def dim_v(sh: Sheet, a, b, x, value: float | None = None, text: str | None = None):
    (x1, y1), (x2, y2) = a, b
    for xx, yy in ((x1, y1), (x2, y2)):
        s = 1 if x > xx else -1
        sh.line(xx + s * 1.0, yy, x + s * 1.5, yy, "dim")
    sh.line(x, y1, x, y2, "dim")
    sh.arrow(x, y1, 0, y1 - y2)
    sh.arrow(x, y2, 0, y2 - y1)
    t = text if text is not None else fmt_len(value if value is not None else abs(y2 - y1))
    sh.text(x - 1.0, (y1 + y2) / 2, t, 2.8, "middle", rot=-90, cls="dt")


# ----------------------------------------------------------------------- views

@dataclass
class View:
    hlr: dict
    toward: np.ndarray
    right: np.ndarray
    scale: float
    x: float = 0.0  # paper position of the view's bounding box, top-left
    y: float = 0.0
    lo: np.ndarray = field(default_factory=lambda: np.zeros(2))
    hi: np.ndarray = field(default_factory=lambda: np.zeros(2))

    def __post_init__(self):
        self.toward = np.asarray(self.toward, float)
        self.right = np.asarray(self.right, float)
        self.up = np.cross(self.toward, self.right)
        pts = [np.array(p) for key in ("visible", "outline") for pl in self.hlr[key] for p in pl]
        allp = np.array(pts) if pts else np.zeros((1, 2))
        self.lo, self.hi = allp.min(axis=0), allp.max(axis=0)

    @property
    def size(self):
        return (self.hi - self.lo) * self.scale

    def P(self, x: float, y: float):
        return (self.x + (x - self.lo[0]) * self.scale, self.y + (self.hi[1] - y) * self.scale)

    def proj(self, p3) -> tuple[float, float]:
        p3 = np.asarray(p3, float)
        return float(p3 @ self.right), float(p3 @ self.up)

    def P3(self, p3):
        return self.P(*self.proj(p3))

    def draw(self, sh: Sheet, hidden=False, tangent=True, label: str | None = None):
        keys = [("visible", "vis"), ("outline", "vis")]
        if tangent:
            keys.append(("smooth", "tan"))
        if hidden:
            keys += [("hidden", "hid"), ("hidden_outline", "hid")]
        for key, cls in keys:
            for pl in self.hlr[key]:
                sh.poly([self.P(*p) for p in pl], cls)
        if label:
            w, h = self.size
            sh.text(self.x + w / 2, self.y + h + 6.5, label, 3.0, "middle", "bold")


def tag(sh: Sheet, x, y, r_paper: float, s: str):
    """Hole tag up and to the right of a hole, with a short leader."""
    ox, oy = x + r_paper * 0.72 + 1.1, y - r_paper * 0.72 - 1.1
    sh.line(x + r_paper * 0.7, y - r_paper * 0.7, ox, oy, "lead")
    sh.text(ox + 0.3, oy - 0.3, s, 2.1, cls="dt")


def table(sh: Sheet, x, y, cols: list[tuple[str, float, str]], rows: list[list[str]],
          row_h=4.2, size=2.3, title: str | None = None, row_cls: list[str] | None = None) -> float:
    """cols: (heading, width, anchor). Returns the y below the table."""
    if title:
        sh.text(x, y - 1.5, title, 2.8, weight="bold")
    total_w = sum(c[1] for c in cols)
    n = len(rows) + 1
    sh.rect(x, y, total_w, n * row_h, "grid")
    sh.line(x, y + row_h, x + total_w, y + row_h, "grid")
    for i in range(2, n):
        sh.line(x, y + i * row_h, x + total_w, y + i * row_h, "cell")
    cx = x
    for j, (head, w, anchor) in enumerate(cols):
        if j:
            sh.line(cx, y, cx, y + n * row_h, "cell")
        tx = cx + 1.2 if anchor == "start" else (cx + w - 1.2 if anchor == "end" else cx + w / 2)
        sh.text(tx, y + row_h - 1.25, head, size, anchor, "bold")
        for i, row in enumerate(rows):
            cls = row_cls[i] if (row_cls and j == len(cols) - 1) else ""
            sh.text(tx, y + (i + 2) * row_h - 1.25, row[j], size, anchor, cls=cls)
        cx += w
    return y + n * row_h


def frame(sh: Sheet, title: str, part: str, sheet_no: int, of: int, material: str, notes: list[str],
          scale: str, rev: str = "A"):
    m = 10.0
    sh.rect(m, m, sh.w - 2 * m, sh.h - 2 * m, "frame")
    # Title block, bottom right.
    bw, bh = 175.0, 36.0
    bx, by = sh.w - m - bw, sh.h - m - bh
    sh.rect(bx, by, bw, bh, "frame")
    sh.line(bx, by + 12, bx + bw, by + 12, "grid")
    sh.line(bx, by + 24, bx + bw, by + 24, "grid")
    sh.line(bx + 105, by + 12, bx + 105, by + bh, "grid")
    sh.line(bx + 140, by + 12, bx + 140, by + bh, "grid")
    sh.text(bx + 3, by + 8.4, title, 5.0, weight="bold")
    sh.text(bx + bw - 3, by + 8.4, "qfold", 3.4, "end")
    sh.text(bx + 3, by + 17, "PART", 1.9)
    sh.text(bx + 3, by + 21.5, part, 3.2, weight="bold")
    sh.text(bx + 3, by + 29, "MATERIAL", 1.9)
    sh.text(bx + 3, by + 33.5, material, 2.6)
    sh.text(bx + 108, by + 17, "SCALE", 1.9)
    sh.text(bx + 108, by + 21.5, scale, 3.2)
    sh.text(bx + 108, by + 29, "UNITS", 1.9)
    sh.text(bx + 108, by + 33.5, "mm", 3.0)
    sh.text(bx + 143, by + 17, "SHEET", 1.9)
    sh.text(bx + 143, by + 21.5, f"{sheet_no} of {of}", 3.2)
    sh.text(bx + 143, by + 29, "REV / DATE", 1.9)
    sh.text(bx + 143, by + 33.5, f"{rev}  {_dt.date.today().isoformat()}", 2.6)
    # Notes, above the title block.
    ny = by - 3 - 4.0 * len(notes)
    sh.text(bx, ny - 2, "NOTES", 2.8, weight="bold")
    for i, n in enumerate(notes):
        sh.text(bx, ny + 3.6 + i * 4.0, f"{i + 1}. {n}", 2.35)
