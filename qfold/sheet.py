"""Describe a sheet metal part by its outside dimensions.

    tray = Sheet("tray", shop)                    # a shops.ShopSpec or a SheetMetalRule
    base = tray.base(140, 100)                    # outside width × depth
    base.flange("front", 40, name="front_wall")   # outside height
    side = base.flange("right", 38.5, name="right_wall")
    side.flange("top", 14, name="right_lip")      # folds inward
    tray.hole("front_wall", (40, 0, 20), Hole("circle", d=9.5, tag="J1"))
    lay = tray.layout()

Every number is an outside dimension, measured on the surface you would put
a ruler on: a wall's height runs from the outside of the face it rises from
to its far edge, or to the outside of a flange on that edge. Sheet works out
the bend set-backs, where each bend starts and stops, how walls meet at
corners, and the reliefs, and produces the Face/Flange tree of fold.py.

Coordinates of the finished part: the base's outside surface is z = 0 with
its front-left corner at the origin, x right, y back. Walls fold up (+z), or
down with up=False (a lid or panel whose top surface is z = 0).

Edges. On the base: "front" (y = 0), "back", "left" (x = 0), "right". On a
wall: "top" (far from its bend), "start" and "end" (its two ends, in the
direction the parent edge runs: +x for front/back, +y for left/right, away
from the bend for a wall's own start/end edges).

Corners. Where two 90° walls meet, one runs the full outside length ("over")
and the other tucks in behind it, `corner_gap` short of its inside face. The
wall made first goes over unless you pass over=True/False.

Reliefs are cut for you:
* where two bends meet at a corner of a face, a `relief_width` square is cut
  from the corner and both bends stop short of it;
* where a wall runs past the end of its bend (an "over" wall at a corner), it
  is notched `relief_depth` high beside the bend;
* where a flange covers part of an edge (start=/end=), a `relief_width` slot
  is cut into the parent at each such end.
relief_width defaults to the shop's rule, else the thickness. relief_depth
defaults to R + T + 0.5 mm from the bend's centre line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .fold import Face, Flange, Hole, SheetLayout, SheetMetalBody, SheetMetalRule, hole_at, layout

# Sides of a face in its own (u, v): S is u-min, N is u-max, W is v-min, E is v-max.
BASE_EDGES = {"left": "S", "right": "N", "front": "W", "back": "E"}
WALL_EDGES = {"top": "N", "start": "W", "end": "E"}
ENDS = {"N": ("W", "E"), "S": ("W", "E"), "W": ("S", "N"), "E": ("S", "N")}  # low end, high end


def setback(rule_T: float, R: float, angle: float) -> float:
    """From a bend's tangent line to where the two outside (reference)
    surfaces would meet. 90°: R + T."""
    half = math.tan(math.radians(abs(angle)) / 2)
    return (R + rule_T) * half if angle > 0 else R * half


@dataclass(eq=False)
class Panel:
    """One flat face of a Sheet: the base, or a wall made by `flange`."""
    sheet: "Sheet"
    name: str
    size: tuple | None = None  # base only: outside (width, depth)
    edge: str = ""  # which side of its parent it hangs from
    height: float | tuple = 0.0
    angle: float = 90.0
    radius: float | None = None
    over: bool | None = None
    start: float | None = None
    end: float | None = None
    order: int = 0
    children: list["Panel"] = field(default_factory=list)

    @property
    def is_base(self) -> bool:
        return self.size is not None

    def flange(self, edge: str, height, name: str | None = None, angle: float = 90.0,
               over: bool | None = None, start: float | None = None, end: float | None = None,
               radius: float | None = None) -> "Panel":
        """Fold a wall up from one edge.

        height: outside height, from the outside of this face to the wall's
            far edge. A pair (h_start, h_end) makes a wall with a sloped top.
        over: at a corner with another wall, True runs this wall the full
            outside length and False tucks it behind the other. Default: the
            wall made first goes over.
        start, end: cover only part of the edge, as distances along it from
            its low end (outside dimensions). Default: the whole edge.
        angle: bend angle in degrees; radius: inside radius, if not the rule's.
        """
        names = BASE_EDGES if self.is_base else WALL_EDGES
        if edge not in names:
            raise ValueError(f"{self.name}: no edge {edge!r}; "
                             f"{'the base' if self.is_base else 'a wall'} has {', '.join(names)}")
        if not self.is_base and isinstance(self.height, tuple):
            raise ValueError(f"{self.name}: a wall with a sloped top can't carry flanges")
        s = self.sheet
        child = Panel(s, name or f"{self.name}_{edge}", None, names[edge], height, angle, radius,
                      over, start, end, len(s.panels))
        if child.name in s.panels:
            raise ValueError(f"{s.name}: two faces are named {child.name!r}")
        s.panels[child.name] = child
        self.children.append(child)
        return child


class Sheet:
    def __init__(self, name: str, shop_or_rule, *, up: bool = True,
                 relief_width: float | None = None, relief_depth: float | None = None,
                 corner_gap: float | None = None):
        """shop_or_rule: a shops.ShopSpec (its rule and relief width are
        used) or a SheetMetalRule. up=False folds the walls down from a top
        surface at z = 0. corner_gap: between a tucked wall's end and the
        inside of the wall that covers it (default 0.25 T, at least 0.3)."""
        rule = getattr(shop_or_rule, "rule", shop_or_rule)
        if not isinstance(rule, SheetMetalRule):
            raise TypeError("Sheet needs a SheetMetalRule, or a ShopSpec that has one "
                            "(pass bend_radius= and k_factor= to shops.spec if the shop "
                            "doesn't publish them)")
        rules = getattr(shop_or_rule, "rules", None)
        self.name, self.rule, self.up = name, rule, up
        T = rule.T
        self.relief_width = (relief_width if relief_width is not None
                             else getattr(rules, "relief_width", None) or T)
        self.relief_depth = relief_depth
        self.corner_gap = corner_gap if corner_gap is not None else max(0.3, 0.25 * T)
        self.panels: dict[str, Panel] = {}
        self.root: Panel | None = None
        self._holes: list[tuple[str, tuple, Hole]] = []

    # -------------------------------------------------------------- describing
    def base(self, width: float, depth: float, name: str = "base") -> Panel:
        """The face everything folds from: outside width (x) × depth (y)."""
        if self.root is not None:
            raise ValueError(f"{self.name} already has a base")
        self.root = Panel(self, name, (float(width), float(depth)))
        self.panels[name] = self.root
        return self.root

    def hole(self, face: str, xyz, hole: Hole) -> Hole:
        """A hole in the named face, centred at xyz on the finished part."""
        if face not in self.panels:
            raise KeyError(f"{self.name} has no face {face!r}; it has: {', '.join(self.panels)}")
        self._holes.append((face, tuple(xyz), hole))
        return hole

    # --------------------------------------------------------------- resolving
    def _setback(self, p: Panel) -> float:
        return setback(self.rule.T, p.radius if p.radius is not None else self.rule.R, p.angle)

    def _relief_depth(self, p: Panel) -> float:
        """How far past its bend's tangent line a relief runs."""
        if self.relief_depth is not None:
            return self.relief_depth
        R = p.radius if p.radius is not None else self.rule.R
        ba = math.radians(abs(p.angle)) * (R + self.rule.K * self.rule.T)
        return max(0.5, R + self.rule.T + 0.5 - ba / 2)

    def _corner(self) -> float:
        # Two bends that end at a corner cut must end at least T apart
        # (dfm bend_end_gap), measured diagonally across the square.
        return max(self.relief_width, 0.75 * self.rule.T)

    def _face(self, p: Panel, box, bottom_cuts=(), top=None) -> Face:
        """box: the face's outside limits (U0, U1, V0, V1) in its own (u, v).
        bottom_cuts: notches beside its own bend. top: sloped top edge."""
        U0, U1, V0, V1 = box
        T, cr, rw, gap = self.rule.T, self._corner(), self.relief_width, self.corner_gap
        length = {"N": V1 - V0, "S": V1 - V0, "W": U1 - U0, "E": U1 - U0}

        def reaches(side: str, corner_side: str) -> Panel | None:
            """The flange on `side` that runs to its corner with corner_side."""
            low = ENDS[side][0] == corner_side
            for c in p.children:
                if c.edge == side and (c.start if low else c.end) is None:
                    return c
            return None

        def rect(side, t0, t1, depth):
            if side == "N":
                return (U1 - depth, U1, V0 + t0, V0 + t1)
            if side == "S":
                return (U0, U0 + depth, V0 + t0, V0 + t1)
            if side == "W":
                return (U0 + t0, U0 + t1, V0, V0 + depth)
            return (U0 + t0, U0 + t1, V1 - depth, V1)

        cuts = [(0.0, d, a, b) for a, b, d in bottom_cuts]
        flanges = []
        for c in sorted(p.children, key=lambda c: c.order):
            side, L, s = c.edge, length[c.edge], self._setback(c)
            rd = self._relief_depth(c)
            b, w = [0.0, L], [0.0, L]
            for i, (adj, pos) in enumerate(zip(ENDS[side], (c.start, c.end))):
                sign = 1 if i == 0 else -1
                at = 0.0 if i == 0 else L
                if pos is not None:  # a flange end partway along the edge
                    if not 0 < pos < L:
                        raise ValueError(f"{c.name}: start/end {pos:g} isn't inside the edge "
                                         f"(0..{L:g})")
                    b[i] = w[i] = pos
                    cuts.append(rect(side, min(pos, pos - sign * rw), max(pos, pos - sign * rw),
                                     s + rd))
                    continue
                n = reaches(adj, side)
                if n is not None:  # a corner shared with another flange
                    sn = self._setback(n)
                    b[i] = at + sign * (sn + cr)
                    square = c.angle == 90 and n.angle == 90
                    c_over = c.over if c.over is not None else (
                        not n.over if n.over is not None else c.order < n.order)
                    if c.over and n.over:
                        raise ValueError(f"{c.name} and {n.name} can't both go over at their "
                                         "corner")
                    w[i] = (at if c_over else at + sign * (T + gap)) if square else b[i]
                elif not p.is_base and adj == "S":  # beside this wall's own bend
                    b[i] = w[i] = at + sign * rd
            b = [max(b[0], w[0]), min(b[1], w[1])]  # a bend never runs past its wall
            if b[1] - b[0] <= 0:
                raise ValueError(f"{c.name}: no room for its bend on a {L:g} mm edge")
            cuts.append(rect(side, 0.0 if c.start is None else c.start,
                             L if c.end is None else c.end, s))
            sloped = isinstance(c.height, tuple)
            h0, h1 = c.height if sloped else (c.height, c.height)
            if min(h0, h1) - s <= rd:
                raise ValueError(f"{c.name}: height {min(h0, h1):g} is too short for its bend "
                                 f"(set-back {s:.2f}) and relief ({rd:.2f})")
            notches = [(lo, hi, rd) for lo, hi in ((w[0], b[0]), (b[1], w[1])) if hi - lo > 1e-9]
            wall = self._face(c, (0.0, max(h0, h1) - s, w[0], w[1]), notches,
                              (h0 - s, h1 - s) if sloped else None)
            if side in ("N", "S"):
                p0 = (U1 - s if side == "N" else U0 + s, V0)
                d, o = (0.0, 1.0), ((1.0, 0.0) if side == "N" else (-1.0, 0.0))
            else:
                p0 = (U0, V1 - s if side == "E" else V0 + s)
                d, o = (1.0, 0.0), ((0.0, 1.0) if side == "E" else (0.0, -1.0))
            flanges.append(Flange(c.name, p0, d, o, b[0], b[1], c.angle, wall, c.radius))

        # A square out of every corner where two bends meet.
        for a, b_ in (("S", "W"), ("S", "E"), ("N", "W"), ("N", "E")):
            ca, cb = reaches(a, b_), reaches(b_, a)
            if ca is not None and cb is not None:
                du, dv = self._setback(ca) + cr, self._setback(cb) + cr
                cuts.append((U0 if a == "S" else U1 - du, U0 + du if a == "S" else U1,
                             V0 if b_ == "W" else V1 - dv, V0 + dv if b_ == "W" else V1))

        outline = _outline((U0, U1, V0, V1), cuts, p.name)
        if top is not None:  # slope the far edge
            def far(v):
                return top[0] + (top[1] - top[0]) * (v - V0) / (V1 - V0)
            outline = [(far(v), v) if abs(u - U1) < 1e-9 else (u, v) for u, v in outline]
        return Face(p.name, outline, [], flanges)

    def body(self) -> SheetMetalBody:
        if self.root is None:
            raise ValueError(f"{self.name}: call base(width, depth) first")
        W, D = self.root.size
        body = SheetMetalBody(self.name, self.rule, self._face(self.root, (0.0, W, 0.0, D)),
                              N=(0.0, 0.0, 1.0 if self.up else -1.0))
        for face, xyz, hole in self._holes:
            hole_at(body, face, xyz, Hole(**{**hole.__dict__}))
        return body

    def layout(self) -> SheetLayout:
        return layout(self.body())

    def component(self, color=(180, 180, 180)):
        """A Component with this part as its body and a ref on every tagged hole."""
        from .assembly import sheet_component
        return sheet_component(self.name, self.layout(), color)


def _outline(box, cuts, name: str) -> list[tuple[float, float]]:
    """The outline of a rectangle with rectangles cut out of it."""
    U0, U1, V0, V1 = box
    us = sorted({round(x, 9) for x in (U0, U1)} | {round(min(max(c[i], U0), U1), 9)
                                                   for c in cuts for i in (0, 1)})
    vs = sorted({round(x, 9) for x in (V0, V1)} | {round(min(max(c[i], V0), V1), 9)
                                                   for c in cuts for i in (2, 3)})
    edges = {}
    for i in range(len(us) - 1):
        for j in range(len(vs) - 1):
            cu, cv = (us[i] + us[i + 1]) / 2, (vs[j] + vs[j + 1]) / 2
            if any(c[0] < cu < c[1] and c[2] < cv < c[3] for c in cuts):
                continue
            a, b, c_, d = ((us[i], vs[j]), (us[i + 1], vs[j]), (us[i + 1], vs[j + 1]),
                           (us[i], vs[j + 1]))
            for p, q in ((a, b), (b, c_), (c_, d), (d, a)):
                if (q, p) in edges:
                    del edges[(q, p)]
                else:
                    edges[(p, q)] = True
    nxt = {}
    for p, q in edges:
        if p in nxt:
            raise ValueError(f"{name}: its reliefs and flanges pinch the face in two")
        nxt[p] = q
    if not nxt:
        raise ValueError(f"{name}: nothing is left of the face after its flanges and reliefs")
    start = min(nxt)
    loop, p = [start], nxt[start]
    while p != start:
        loop.append(p)
        p = nxt[p]
    if len(loop) != len(nxt):
        raise ValueError(f"{name}: its reliefs and flanges cut the face into separate pieces")
    out = []
    for k, p in enumerate(loop):  # drop points in the middle of straight runs
        a, b = loop[k - 1], loop[(k + 1) % len(loop)]
        if abs((p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])) > 1e-12:
            out.append((float(p[0]), float(p[1])))
    return out
