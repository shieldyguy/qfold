"""Describe a sheet metal part by its dimensions; the bend math is done here.

    tray = Sheet("tray", shop)                    # a shops.ShopSpec or a SheetMetalRule
    base = tray.base(140, 100)                    # width × depth
    base.flange("front", 40, name="front_wall")   # height
    side = base.flange("right", 38.5, name="right_wall")
    side.flange("top", 14, name="right_lip")      # folds inward
    tray.hole("front_wall", (40, 0, 20), Hole("circle", d=9.5, tag="J1"))
    lay = tray.layout()

Dimensions are outside dimensions unless you say otherwise: a wall's height
runs from the outside of the face it rises from to its far edge, or to the
outside of a flange on that edge. `Sheet(..., dims="inside")`, or
`inside=True` on one base() or flange(), gives inside dimensions instead: the
base between the inside faces of its walls, a wall's height from the inside
of the face it rises from. Sheet works out the bend set-backs, where each
bend starts and stops, how walls meet at corners, and the reliefs, and
produces the Face/Flange tree of fold.py.

Coordinates of the finished part: the base's outside surface is z = 0 with
its outside front-left corner at the origin, x right, y back. Walls fold up
(+z), or down with up=False (a lid or panel whose top surface is z = 0).

Edges. On the base: "front" (y = 0), "back", "left" (x = 0), "right". On a
wall: "top" (far from its bend), "start" and "end" (its two ends).

Corner seams, where two 90° walls meet (corner=, corner_gap=):
* "overlap": one wall runs the full outside length and the other tucks in
  behind it, corner_gap short of its inside face. The wall made first goes
  over unless you pass over=True/False.
* "butt": both walls stop at the inside corner, corner_gap apart, leaving
  the outside corner open by one thickness (a weld prep).
* "open": each wall is only as wide as its bend.

Corner relief, where two bends meet at a corner of a face (corner_relief=):
* "square": a relief_width square cut from the corner; both bends stop short.
* "round": the same with a round inside, radius relief_width.
* "tear": nothing is cut and the bends run to the corner, where the
  material tears. The tightest corner; the DFM check reports it as a warning.

Bend relief, beside a bend that ends next to more material (bend_relief=, or
relief= on one flange): under a wall that runs past its bend at a corner, and
each side of a flange on part of an edge (start=/end=).
* "straight": a relief_width slot, relief_depth past the bend.
* "round": the same with rounded ends.
* "tear" (also "shear"): a slit one laser kerf wide (kerf=, default 0.2 mm)
  that stops at the bend, so the parts sit as close as they can be cut.

relief_width defaults to the shop's rule, else the thickness. relief_depth
defaults to R + T + 0.5 mm from the bend's centre line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .fold import (Face, Flange, Hole, SheetLayout, SheetMetalBody, SheetMetalRule, arc_bulge,
                   hole_at, layout)

# Sides of a face in its own (u, v): S is u-min, N is u-max, W is v-min, E is v-max.
BASE_EDGES = {"left": "S", "right": "N", "front": "W", "back": "E"}
WALL_EDGES = {"top": "N", "start": "W", "end": "E"}
ENDS = {"N": ("W", "E"), "S": ("W", "E"), "W": ("S", "N"), "E": ("S", "N")}  # low end, high end


CORNERS = ("overlap", "butt", "open")
CORNER_RELIEFS = ("square", "round", "tear")
BEND_RELIEFS = {"straight": "straight", "round": "round", "tear": "tear", "shear": "tear"}


def _style(value: str, allowed, what: str) -> str:
    if value not in allowed:
        raise ValueError(f"unknown {what} {value!r}; choose from {', '.join(allowed)}")
    return allowed[value] if isinstance(allowed, dict) else value


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
    inside: bool | None = None
    relief: str | None = None
    extend: tuple = (0.0, 0.0)
    extend_drop: tuple = (None, None)
    round: tuple = (0.0, 0.0)
    extend_round: tuple = (0.0, 0.0)
    extend_level: tuple = (None, None)
    extend_fillet: tuple = (0.0, 0.0)
    children: list["Panel"] = field(default_factory=list)

    @property
    def is_base(self) -> bool:
        return self.size is not None

    def flange(self, edge: str, height, name: str | None = None, angle: float = 90.0,
               over: bool | None = None, start: float | None = None, end: float | None = None,
               radius: float | None = None, inside: bool | None = None,
               relief: str | None = None, extend: tuple = (0.0, 0.0),
               extend_drop: tuple = (None, None), round: tuple = (0.0, 0.0),
               extend_round: tuple = (0.0, 0.0), extend_level: tuple = (None, None),
               extend_fillet: tuple = (0.0, 0.0)) -> "Panel":
        """Fold a wall up from one edge.

        height: from the outside of this face to the wall's far edge (or,
            with inside=True, from its inside). A pair (h_start, h_end)
            makes a wall with a sloped top.
        inside: measure this wall's height on the inside; default: the
            sheet's dims.
        relief: this flange's bend relief style, if not the sheet's.
        extend: (at the start, at the end) how far the wall runs on past the
            end of the edge it hangs from, where no other flange meets it:
            a cheek that carries on beyond the panel it folds from.
        extend_drop: for each extension, how far below the parent's outside
            surface its edge is at its tip; the edge slopes there from the
            end of the bend. Default: level with the bend.
        extend_level: for each extension, how far below the parent's
            outside surface its edge runs, level. 0 puts it flush with the
            parent's surface: the wall's flat face then stands above where
            the bend starts, parted from the bend's end by a relief slot in
            this flange's bend relief style (a real part needs a slot with
            some width there: relief="round").
        extend_fillet: for each extension, a radius drawn in the flat
            pattern across the inside corner where the parent's edge meets
            the extension's. The fillet crosses the bend, so the bend ends
            on a curve, with no relief; the extension's edge starts on the
            bend's tangent line.
        extend_round: for each extension, a radius on the corner of its tip
            nearest the bend (a level extension only).
        round: (at the start, at the end) radius on the wall's two far
            corners, the ones away from its bend. Use it to nest a wall
            inside another part's bend (radius = that bend's inside radius
            less the gap) or around one (its outside radius plus the gap).
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
        if relief is not None:
            _style(relief, BEND_RELIEFS, "bend relief")
        child = Panel(s, name or f"{self.name}_{edge}", None, names[edge], height, angle, radius,
                      over, start, end, len(s.panels), inside, relief, tuple(extend),
                      tuple(extend_drop), tuple(round), tuple(extend_round),
                      tuple(extend_level), tuple(extend_fillet))
        if child.name in s.panels:
            raise ValueError(f"{s.name}: two faces are named {child.name!r}")
        s.panels[child.name] = child
        self.children.append(child)
        return child


class Sheet:
    def __init__(self, name: str, shop_or_rule, *, up: bool = True, dims: str = "outside",
                 corner: str = "overlap", corner_gap: float | None = None,
                 corner_relief: str = "square", bend_relief: str = "straight",
                 relief_width: float | None = None, relief_depth: float | None = None,
                 kerf: float = 0.2):
        """shop_or_rule: a shops.ShopSpec (its rule and relief width are
        used) or a SheetMetalRule. up=False folds the walls down from a top
        surface at z = 0. The rest is described in the module docstring.
        corner_gap defaults to 0.25 T, at least 0.3 mm."""
        rule = getattr(shop_or_rule, "rule", shop_or_rule)
        if not isinstance(rule, SheetMetalRule):
            raise TypeError("Sheet needs a SheetMetalRule, or a ShopSpec that has one "
                            "(pass bend_radius= and k_factor= to shops.spec if the shop "
                            "doesn't publish them)")
        rules = getattr(shop_or_rule, "rules", None)
        self.name, self.rule, self.up = name, rule, up
        T = rule.T
        if dims not in ("outside", "inside"):
            raise ValueError(f"dims is 'outside' or 'inside', not {dims!r}")
        self.inside = dims == "inside"
        self.corner = _style(corner, CORNERS, "corner")
        self.corner_relief = _style(corner_relief, CORNER_RELIEFS, "corner relief")
        self.bend_relief = _style(bend_relief, BEND_RELIEFS, "bend relief")
        self.relief_width = (relief_width if relief_width is not None
                             else getattr(rules, "relief_width", None) or T)
        self.relief_depth = relief_depth
        self.kerf = kerf
        self.corner_gap = corner_gap if corner_gap is not None else max(0.3, 0.25 * T)
        self.panels: dict[str, Panel] = {}
        self.root: Panel | None = None
        self._holes: list[tuple[str, tuple, Hole]] = []

    # -------------------------------------------------------------- describing
    def base(self, width: float, depth: float, name: str = "base",
             inside: bool | None = None) -> Panel:
        """The face everything folds from: width (x) × depth (y). Outside
        size, or with inside=True the size between the inside faces of the
        walls on its edges."""
        if self.root is not None:
            raise ValueError(f"{self.name} already has a base")
        self.root = Panel(self, name, (float(width), float(depth)), inside=inside)
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
        """Size of the cut where two bends meet."""
        if self.corner_relief == "tear":
            return 0.0
        # Two bends that end at a corner cut must end at least T apart
        # (dfm bend_end_gap), measured diagonally across it.
        return max(self.relief_width, 0.75 * self.rule.T)

    def _is_inside(self, p: Panel) -> bool:
        return self.inside if p.inside is None else p.inside

    def _skin(self, p: Panel) -> float:
        """What a bend adds between an inside and an outside dimension: T at 90°."""
        return self.rule.T * math.tan(math.radians(abs(p.angle)) / 2)

    def _outside_height(self, c: Panel):
        """A wall's height as an outside dimension."""
        add = 0.0
        if self._is_inside(c):
            add = self._skin(c) + sum(self._skin(g) for g in c.children if g.edge == "N")
        return tuple(h + add for h in c.height) if isinstance(c.height, tuple) else c.height + add

    def _relief(self, c: Panel):
        """(style, slot width, depth past the bend) of a flange's bend relief."""
        style = BEND_RELIEFS[c.relief] if c.relief else self.bend_relief
        if style == "tear":
            return style, self.kerf, self.kerf
        rd, rw = self._relief_depth(c), self.relief_width
        return style, rw, max(rd, rw / 2) if style == "round" else rd

    def _face(self, p: Panel, box, own_cuts=(), top=None, fillets=(), moves=()) -> Face:
        """box: the face's outside limits (U0, U1, V0, V1) in its own (u, v).
        own_cuts: rectangles (u0, u1, v0, v1) cut beside its own bend. top:
        sloped top edge."""
        U0, U1, V0, V1 = box
        T, cr, gap = self.rule.T, self._corner(), self.corner_gap
        length = {"N": V1 - V0, "S": V1 - V0, "W": U1 - U0, "E": U1 - U0}
        fillets = list(fillets)

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

        def point(side, t, depth):
            r = rect(side, t, t, depth)
            return (r[0] if side == "N" else r[1] if side == "S" else r[0],
                    r[2] if side in ("N", "S") else (r[3] if side == "W" else r[2]))

        cuts = list(own_cuts)
        flanges, horns = [], []
        for c in sorted(p.children, key=lambda c: c.order):
            side, L, s = c.edge, length[c.edge], self._setback(c)
            style, rw, rd = self._relief(c)
            b, w = [0.0, L], [0.0, L]
            for i, (adj, pos) in enumerate(zip(ENDS[side], (c.start, c.end))):
                sign = 1 if i == 0 else -1
                at = 0.0 if i == 0 else L
                if pos is not None:  # a flange end partway along the edge: a slot beside it
                    if not 0 < pos < L:
                        raise ValueError(f"{c.name}: start/end {pos:g} isn't inside the edge "
                                         f"(0..{L:g})")
                    b[i] = w[i] = pos
                    far = pos - sign * rw
                    cuts.append(rect(side, min(pos, far), max(pos, far), s + rd))
                    if style == "round":
                        fillets += [(point(side, pos, s + rd), rw / 2),
                                    (point(side, far, s + rd), rw / 2)]
                    continue
                n = reaches(adj, side)
                if n is not None:  # a corner shared with another flange
                    b[i] = at + sign * (self._setback(n) + cr)
                    if c.over and n.over:
                        raise ValueError(f"{c.name} and {n.name} can't both go over at their "
                                         "corner")
                    if c.angle != 90 or n.angle != 90 or self.corner == "open":
                        w[i] = b[i]
                    elif self.corner == "butt":
                        w[i] = at + sign * (T + gap / math.sqrt(2))
                    else:
                        c_over = c.over if c.over is not None else (
                            not n.over if n.over is not None else c.order < n.order)
                        w[i] = at if c_over else at + sign * (T + gap)
                elif not p.is_base and adj == "S":  # beside this wall's own bend
                    b[i] = w[i] = at + sign * max(rd, self._relief_depth(c))
                elif c.extend[i] > 0:  # the wall carries on past the end of the edge
                    w[i] = at - sign * c.extend[i]
                if c.extend[i] > 0 and w[i] != at - sign * c.extend[i]:
                    raise ValueError(f"{c.name}: can't extend past an end that meets another "
                                     "flange or stops partway")
            b = [max(b[0], w[0]), min(b[1], w[1])]  # a bend never runs past its wall
            if b[1] - b[0] <= 0:
                raise ValueError(f"{c.name}: no room for its bend on a {L:g} mm edge")
            cuts.append(rect(side, 0.0 if c.start is None else c.start,
                             L if c.end is None else c.end, s))
            height = self._outside_height(c)
            sloped = isinstance(height, tuple)
            h0, h1 = height if sloped else (height, height)
            if min(h0, h1) - s <= rd:
                raise ValueError(f"{c.name}: height {min(h0, h1):g} is too short for its bend "
                                 f"(set-back {s:.2f}) and relief ({rd:.2f})")
            wall_cuts, wall_fillets, wall_moves = [], [], []
            # A raised extension rises above the bend's tangent line (u < 0),
            # up to the parent's outside surface at u = -s.
            lifted = [c.extend[i] > 0 and c.extend_level[i] is not None
                      and c.extend_level[i] < s for i in (0, 1)]
            u_lo = -s if any(lifted) else 0.0
            # A fillet across the bend: the bend's flat strip ends on an arc,
            # from the parent's edge to the extension's edge on the wall's
            # tangent line. Wider than the strip, it starts on the parent.
            R = c.radius if c.radius is not None else self.rule.R
            BA = math.radians(abs(c.angle)) * (R + self.rule.K * T)
            ends = []  # each end of the strip, from the parent's side to the wall's
            for i, (sign, at) in enumerate(((1, 0.0), (-1, L))):
                r = c.extend_fillet[i]
                if r <= 0:
                    ends.append([(0.0, b[i]), (BA, b[i])])
                    continue
                if c.extend[i] <= 0 or c.extend_level[i] is not None \
                        or c.extend_drop[i] is not None:
                    raise ValueError(f"{c.name}: extend_fillet needs a plain extension at "
                                     "that end (no extend_level or extend_drop)")
                if r > c.extend[i] - c.extend_round[i]:
                    raise ValueError(f"{c.name}: a {r:g} mm extend_fillet doesn't fit a "
                                     f"{c.extend[i]:g} mm extension")
                centre, foot = (BA - r, at - sign * r), (BA, at - sign * r)
                if r > BA:  # the arc starts on the parent: a small horn on its corner
                    hd = r - math.sqrt(r * r - (r - BA) ** 2)
                    head = (0.0, at - sign * hd)
                    horns.append((point(side, at, s),
                                  [point(side, at, s + r - BA), point(side, at - sign * hd, s)],
                                  point(side, at - sign * r, s + r - BA)))
                    ends.append([head, foot])
                else:
                    head = (BA - r, at)
                    ends.append([(0.0, at), head, foot] if r < BA else [head, foot])
                ends[-1] = [(*q, centre) if q == head else q for q in ends[-1]]
                b[i] = at - sign * r
            strip = None
            if any(r > 0 for r in c.extend_fillet):
                loop = ends[0] + ends[1][::-1]
                strip = []
                for k, q in enumerate(loop):
                    nxt = loop[(k + 1) % len(loop)]
                    # An arc runs between a head and the foot after (or before) it.
                    centre = q[2] if len(q) > 2 and k < len(ends[0]) else (
                        nxt[2] if len(nxt) > 2 and len(ends[0]) <= k < len(loop) - 1 else None)
                    strip.append((q[0], q[1], arc_bulge(q[:2], nxt[:2], centre))
                                 if centre else q[:2])
            for i, (lo, hi, corner_v, tip_v) in enumerate(((w[0], b[0], b[0], w[0]),
                                                           (b[1], w[1], b[1], w[1]))):
                if hi - lo <= 1e-9:
                    continue  # the wall ends with its bend here
                drop = c.extend_drop[i] if c.extend[i] > 0 else None
                if c.extend_fillet[i] > 0:
                    edge = 0.0  # the extension's edge is the tangent line: nothing to cut
                elif not lifted[i]:
                    edge = rd
                    if c.extend[i] > 0 and c.extend_level[i] is not None:
                        edge = c.extend_level[i] - s  # a level edge further down
                    wall_cuts.append((u_lo, edge, lo, hi))
                    if drop is not None:  # slope the extension's edge down to its tip
                        if not rd < drop - s < min(h0, h1) - s:
                            raise ValueError(f"{c.name}: extend_drop {drop:g} must be between "
                                             f"{s + rd:.2f} and the wall's height")
                        wall_moves.append(((rd, tip_v), (drop - s, tip_v)))
                    elif style == "round" and edge == rd:
                        wall_fillets.append(((rd, corner_v), min(rd, hi - lo)))
                else:
                    if drop is not None:
                        raise ValueError(f"{c.name}: extend_drop can't be combined with "
                                         "extend_level")
                    # A relief slot parts the extension from the bend's end.
                    sgn = 1 if i == 0 else -1
                    slit = corner_v - sgn * rw  # the extension's edge of the slot
                    wall_cuts.append((u_lo, rd, min(slit, corner_v), max(slit, corner_v)))
                    if style == "round" and rw > 0:
                        wall_fillets += [((rd, corner_v), rw / 2), ((rd, slit), rw / 2)]
                    edge = c.extend_level[i] - s
                    if edge > u_lo:
                        wall_cuts.append((u_lo, edge, min(tip_v, slit), max(tip_v, slit)))
                if c.extend[i] > 0 and c.extend_round[i] > 0:
                    if drop is not None:
                        raise ValueError(f"{c.name}: extend_round needs a level extension "
                                         "(no extend_drop)")
                    wall_fillets.append(((edge, tip_v), c.extend_round[i]))
            if u_lo < 0:  # nothing above the tangent line where the bend itself is
                wall_cuts.append((u_lo, 0.0, b[0], b[1]))
            for i in (0, 1):
                if c.round[i] > 0:
                    if sloped:
                        raise ValueError(f"{c.name}: a wall with a sloped top can't have "
                                         "rounded far corners")
                    wall_fillets.append(((h0 - s, w[i]), c.round[i]))
            wall = self._face(c, (u_lo, max(h0, h1) - s, w[0], w[1]), wall_cuts,
                              (h0 - s, h1 - s) if sloped else None, wall_fillets, wall_moves)
            if side in ("N", "S"):
                p0 = (U1 - s if side == "N" else U0 + s, V0)
                d, o = (0.0, 1.0), ((1.0, 0.0) if side == "N" else (-1.0, 0.0))
            else:
                p0 = (U0, V1 - s if side == "E" else V0 + s)
                d, o = (1.0, 0.0), ((0.0, 1.0) if side == "E" else (0.0, -1.0))
            flanges.append(Flange(c.name, p0, d, o, b[0], b[1], c.angle, wall, c.radius, strip))

        # A cut out of every corner where two bends meet.
        if cr > 0:
            for a, b_ in (("S", "W"), ("S", "E"), ("N", "W"), ("N", "E")):
                ca, cb = reaches(a, b_), reaches(b_, a)
                if ca is not None and cb is not None:
                    du, dv = self._setback(ca) + cr, self._setback(cb) + cr
                    cuts.append((U0 if a == "S" else U1 - du, U0 + du if a == "S" else U1,
                                 V0 if b_ == "W" else V1 - dv, V0 + dv if b_ == "W" else V1))
                    if self.corner_relief == "round":
                        fillets.append(((U0 + du if a == "S" else U1 - du,
                                         V0 + dv if b_ == "W" else V1 - dv), cr))

        outline = _outline((U0, U1, V0, V1), cuts, p.name, fillets, moves, horns)
        if top is not None:  # slope the far edge
            def far_u(v):
                return top[0] + (top[1] - top[0]) * (v - V0) / (V1 - V0)
            outline = [(far_u(q[1]), *q[1:]) if abs(q[0] - U1) < 1e-9 else q for q in outline]
        return Face(p.name, outline, [], flanges)

    def body(self) -> SheetMetalBody:
        if self.root is None:
            raise ValueError(f"{self.name}: call base(width, depth) first")
        W, D = self.root.size
        if self._is_inside(self.root):  # add the walls standing on each edge
            for side in ("S", "N", "W", "E"):
                skin = max((self._skin(c) for c in self.root.children if c.edge == side),
                           default=0.0)
                W, D = (W + skin, D) if side in ("S", "N") else (W, D + skin)
        body = SheetMetalBody(self.name, self.rule, self._face(self.root, (0.0, W, 0.0, D)),
                              N=(0.0, 0.0, 1.0 if self.up else -1.0))
        body.tear_corners = self.corner_relief == "tear"
        for face, xyz, hole in self._holes:
            hole_at(body, face, xyz, Hole(**{**hole.__dict__}))
        return body

    def layout(self) -> SheetLayout:
        return layout(self.body())

    def component(self, color=(180, 180, 180)):
        """A Component with this part as its body and a ref on every tagged hole."""
        from .assembly import sheet_component
        return sheet_component(self.name, self.layout(), color)


def _outline(box, cuts, name: str, fillets=(), moves=(), horns=()) -> list[tuple]:
    """The outline of a rectangle with rectangles cut out of it, then with
    the corners in moves ((from, to)) shifted, those in fillets
    ((point, radius)) rounded, and those in horns ((corner, [a, b], centre))
    swapped for an arc from a to b that bulges out past the corner."""
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
            out.append([float(p[0]), float(p[1]), 0.0])
    for (fu, fv), (a, b), centre in horns:
        k = next((k for k, q in enumerate(out) if abs(q[0] - fu) < 1e-7 and abs(q[1] - fv) < 1e-7),
                 None)
        if k is None:
            raise ValueError(f"{name}: no corner at ({fu:g}, {fv:g}) for a fillet to start on")
        prev = out[k - 1]
        if abs((a[0] - prev[0]) * (fv - prev[1]) - (a[1] - prev[1]) * (fu - prev[0])) > 1e-7:
            a, b = b, a  # walk in from the side the outline comes from
        out[k:k + 1] = [[float(a[0]), float(a[1]), arc_bulge(a, b, centre)],
                        [float(b[0]), float(b[1]), 0.0]]
    for (fu, fv), to in moves:
        q = next((q for q in out if abs(q[0] - fu) < 1e-7 and abs(q[1] - fv) < 1e-7), None)
        if q is None:
            raise ValueError(f"{name}: no corner at ({fu:g}, {fv:g}) to move")
        q[0], q[1] = float(to[0]), float(to[1])
    for (fu, fv), r in fillets:
        k = next((k for k, q in enumerate(out) if abs(q[0] - fu) < 1e-7 and abs(q[1] - fv) < 1e-7),
                 None)
        if k is None:
            raise ValueError(f"{name}: no corner at ({fu:g}, {fv:g}) to round")
        P, A, B = out[k], out[k - 1], out[(k + 1) % len(out)]
        ends = []
        for Q in (A, B):
            d = math.hypot(Q[0] - P[0], Q[1] - P[1])
            if d < r - 1e-7:
                raise ValueError(f"{name}: a {r:g} mm round doesn't fit a {d:.2f} mm edge")
            ends.append((P[0] + r * (Q[0] - P[0]) / d, P[1] + r * (Q[1] - P[1]) / d, d - r < 1e-7))
        (su, sv, at_a), (eu, ev, at_b) = ends
        cu, cv = su + eu - P[0], sv + ev - P[1]  # the arc's centre
        m = math.hypot(P[0] - cu, P[1] - cv)
        mu, mv = cu + r * (P[0] - cu) / m, cv + r * (P[1] - cv) / m  # its middle
        du, dv = eu - su, ev - sv
        bulge = -2 * (du * (mv - sv) - dv * (mu - su)) / (du * du + dv * dv)
        new = ([] if at_a else [[su, sv, bulge]]) + ([] if at_b else [[eu, ev, 0.0]])
        if at_a:
            A[2] = bulge
        out[k:k + 1] = new
    return [tuple(q) if q[2] else (q[0], q[1]) for q in out]
