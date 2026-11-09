"""Fab shop data: bend tables and design limits, as each shop publishes them.

The tables are in qfold/shop_data/<shop>.json, copied from the shop's own pages
in the shop's own units, with the source pages and the date they were read.
`spec(shop, material, thickness)` turns one row into what qfold uses, in mm:

* `rule`: a SheetMetalRule (thickness, inside bend radius, K-factor) for the
  fold engine, so flat patterns match the shop's bends;
* `rules`: dfm.Rules with the limits that shop publishes. Anything it doesn't
  publish keeps qfold's default (see dfm.Rules).

OSH Cut doesn't publish bend radius or K-factor outside its app, so its
`rule` is None until you pass `bend_radius` and `k_factor` read from there.
JLCCNC publishes the radius but not K. It unfolds the STEP you send itself, so
K only shapes qfold's own flat pattern, and the middle of JLCCNC's design-guide
range is used unless you pass one. Names are the shop's own; `materials(shop)`
lists them.

    s = shops.spec("sendcutsend", "5052 Aluminum", 1.6)
    CaseParams(rule=s.rule)  # and dfm checks with s.rules
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .dfm import Rules
from .fold import SheetMetalRule

IN = 25.4
DATA = Path(__file__).parent / "shop_data"
SHOPS = ("sendcutsend", "oshcut", "jlccnc")


@dataclass(frozen=True)
class ShopSpec:
    shop: str
    material: str
    thickness: float  # mm: the shop's nominal stock thickness
    rule: SheetMetalRule | None  # None: the shop doesn't publish R and K
    rules: Rules
    row: dict  # the shop's published row, in its own units
    notes: tuple[str, ...]
    sources: dict


@cache
def load(shop: str) -> dict:
    if shop not in SHOPS:
        raise ValueError(f"unknown shop {shop!r}; known: {', '.join(SHOPS)}")
    return json.loads((DATA / f"{shop}.json").read_text())


def _rows(data: dict, material: str) -> list[dict]:
    m = data["materials"].get(material)
    if m is None:
        raise ValueError(f"{data['shop']} has no material {material!r}; it has: "
                         + ", ".join(repr(k) for k in data["materials"]))
    table = m if "rows" in m else data["stock"]  # one stock table for every material
    return [dict(zip(table["columns"], r)) for r in table["rows"]]


def _mm(data: dict) -> float:
    return IN if data["units"] == "in" else 1.0


def materials(shop: str) -> dict[str, list[float]]:
    """Material names and their stock thicknesses in mm."""
    data = load(shop)
    return {name: [round(r["T"] * _mm(data), 3) for r in _rows(data, name)]
            for name in data["materials"]}


def _pick(data: dict, material: str, thickness: float, tol: float) -> dict:
    rows, s = _rows(data, material), _mm(data)
    best = min(rows, key=lambda r: abs(r["T"] * s - thickness))
    if abs(best["T"] * s - thickness) > tol:
        offered = ", ".join(f"{r['T'] * IN:.2f} mm ({r['T']:.3f} in)" if s == IN
                            else f"{r['T']:g} mm" for r in rows)
        raise ValueError(f"{data['shop']} {material} isn't offered at {thickness:g} mm; "
                         f"thicknesses: {offered}")
    return best


def k_from_bend_deduction(T: float, R: float, BD90: float) -> float:
    """The K-factor that reproduces a bend deduction at 90 degrees:
    BD = 2(R + T) - (pi/2)(R + K T)."""
    return ((2 * (R + T) - BD90) * 2 / math.pi - R) / T


def spec(shop: str, material: str, thickness: float, *, bend_radius: float | None = None,
         k_factor: float | None = None, tol: float = 0.05) -> ShopSpec:
    """One material at one thickness (mm, matched to the shop's stock within
    tol) at one shop. bend_radius (mm) and k_factor fill in what the shop
    doesn't publish."""
    data = load(shop)
    if (bend_radius is None) != (k_factor is None):
        raise ValueError("pass bend_radius and k_factor together")
    return {"sendcutsend": _sendcutsend, "oshcut": _oshcut, "jlccnc": _jlccnc}[shop](
        data, material, thickness, bend_radius, k_factor, tol)


def _sendcutsend(data, material, thickness, bend_radius, k_factor, tol) -> ShopSpec:
    if bend_radius is not None:
        raise ValueError("SendCutSend bends at a fixed radius per material and thickness; "
                         "custom radii aren't offered")
    r = _pick(data, material, thickness, tol)
    T, R = r["T"], r["R"]
    K = k_from_bend_deduction(T, R, r["BD90"])
    BA90 = 2 * (R + T) - r["BD90"]
    notes = [f"K {K:.3f} reproduces BD90 {r['BD90']} in (printed K {r['K']})"] \
        if abs(K - r["K"]) > 0.01 else []
    g = data["general"]
    rules = Rules(
        # Cut features at least half the die width from the bend centre line,
        # in the flat; qfold measures from the bend tangent line, BA/2 closer.
        hole_to_bend=(r["die"] - BA90) / 2 * IN,
        min_hole=max(g["min_hole_per_T"] * T, g["min_feature"]) * IN,
        # flange90 is outside to edge; the flat wall past the bend is R + T less.
        min_flange=(r["flange90"] - (R + T)) * IN,
        max_sheet=tuple(x * IN for x in g["max_flat_bent"]),
        relief_width=max(T / 2, 0.030) * IN)  # relief width >= T/2; not below 0.76 mm
    if r.get("max_bend") is not None:
        notes.append(f"max bend length {r['max_bend']} in ({r['max_bend'] * IN:.0f} mm)")
    rule = SheetMetalRule(f"SendCutSend {material}", T * IN, R * IN, K)
    return ShopSpec(data["shop"], material, T * IN, rule, rules, r, tuple(notes),
                    data["sources"])


def _oshcut(data, material, thickness, bend_radius, k_factor, tol) -> ShopSpec:
    r = _pick(data, material, thickness, tol)
    if not r["bend"]:
        raise ValueError(f"OSH Cut doesn't bend {material} at {r['T']} in")
    T = r["T"] * IN
    rules = Rules(min_hole=r["min_hole_rec"] * IN, hole_web=r["min_wall_rec"] * IN,
                  max_sheet=(r["max_x"] * IN, r["max_y"] * IN))
    notes = [] if bend_radius is not None else [
        "bend radius and K-factor aren't published outside OSH Cut's app "
        "(app.oshcut.com/catalog): pass bend_radius= and k_factor= from there"]
    rule = SheetMetalRule(f"OSH Cut {material}", T, bend_radius, k_factor) \
        if bend_radius is not None else None
    return ShopSpec(data["shop"], material, T, rule, rules, r, tuple(notes), data["sources"])


def _jlccnc(data, material, thickness, bend_radius, k_factor, tol) -> ShopSpec:
    r = _pick(data, material, thickness, tol)
    g = data["general"]
    T = r["T"]
    R = bend_radius if bend_radius is not None else (
        r["R_outer"] - T if r["R_outer"] is not None else None)
    K = k_factor if k_factor is not None else data["k_midpoint"][material]
    notes = []
    if bend_radius is None and R is not None:
        notes.append(f"inside radius {R:g} mm = published outer corner radius "
                     f"{r['R_outer']:g} - T")
    if R is None:
        notes.append(f"no published bend radius at {T:g} mm: pass bend_radius= and k_factor=")
    if k_factor is None:
        notes.append(f"K {K} is the middle of JLCCNC's design-guide range, not shop data. "
                     "JLCCNC unfolds the STEP you send, so K only affects qfold's own flat "
                     "pattern")
    notes.append(f"actual sheet thickness {r['T_actual']:g} mm for nominal {T:g}")
    # Minimum flange: published for 1-3 mm, else the article's V/2 + 2 mm with
    # V = 6 T. Read as outside-to-edge; the flat wall past the bend is R + T
    # shorter (R taken as 0 when unknown, the stricter case).
    flange = r["flange"] if r["flange"] is not None else g["v_die_per_T"] * T / 2 + 2
    rules = Rules(
        # 2 T from the bend tangent (design guide), and nothing within 3 mm of
        # the bend (bending article; taken from the tangent, the stricter case).
        hole_to_bend=max(2 * T, 3.0),
        hole_to_edge=g["hole_to_edge_per_T"] * T,
        hole_web=g["min_hole_spacing"],
        min_hole=max(1.0, T / 2),
        min_flange=flange - ((R or 0.0) + T),
        relief_width=float(T),  # design guide: 1-2 T
        max_sheet=(g["max_length"], g["max_length"]))
    rule = SheetMetalRule(f"JLCCNC {material}", T, R, K) if R is not None else None
    return ShopSpec(data["shop"], material, T, rule, rules, r, tuple(notes), data["sources"])
