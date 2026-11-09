"""Fab shop data: bend tables and design limits, as each shop publishes them.

The tables are in qfold/shop_data/<shop>.json, copied from the shop's own pages
in the shop's own units, with the source pages and the date they were read.
`spec(shop, material, thickness)` turns one row into what qfold uses, in mm:

* `rule`: a SheetMetalRule (thickness, inside bend radius, K-factor) for the
  fold engine, so flat patterns match the shop's bends;
* `rules`: dfm.Rules with the limits that shop publishes. Anything it doesn't
  publish keeps qfold's default (see dfm.Rules).

OSH Cut and JLCCNC don't publish bend radius or K-factor outside their apps,
so their `rule` is None until you pass `bend_radius` and `k_factor` read from
the app (or a test bend). Names are the shop's own; `materials(shop)` lists
them.

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
    return [dict(zip(m["columns"], r)) for r in m.get("rows", [])]


def materials(shop: str) -> dict[str, list[float]]:
    """Material names and their stock thicknesses in mm (empty: any thickness
    in the shop's range)."""
    data = load(shop)
    scale = IN if data["units"] == "in" else 1.0
    return {name: [round(r["T"] * scale, 3) for r in _rows(data, name)]
            for name in data["materials"]}


def _pick(data: dict, material: str, thickness: float, tol: float) -> dict:
    rows = _rows(data, material)
    best = min(rows, key=lambda r: abs(r["T"] * IN - thickness))
    if abs(best["T"] * IN - thickness) > tol:
        offered = ", ".join(f"{r['T'] * IN:.2f} mm ({r['T']:.3f} in)" for r in rows)
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
        max_sheet=tuple(x * IN for x in g["max_flat_bent"]))
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
    _rows(data, material)  # validates the name
    g = data["general"]
    lo, hi = g["thickness_range"]
    if not lo <= thickness <= hi:
        raise ValueError(f"JLCCNC sheet metal is {lo:g}-{hi:g} mm thick, not {thickness:g}")
    T = thickness
    rules = Rules(
        hole_to_bend=g["hole_to_bend_tangent_per_T"] * T,
        hole_to_edge=g["hole_to_edge_per_T"] * T,
        hole_web=g["min_hole_spacing"],
        min_hole=max(1.0, T / 2),
        min_flange=g["min_flange_per_T"] * T)
    notes = ["limits are JLCCNC's design-guide multiples of T, not per-thickness shop data"]
    if bend_radius is None:
        notes.append("bend radius and K-factor aren't published (the quote tool sets them): "
                     "pass bend_radius= and k_factor=; the guide suggests R about 1 T")
    rule = SheetMetalRule(f"JLCCNC {material}", T, bend_radius, k_factor) \
        if bend_radius is not None else None
    return ShopSpec(data["shop"], material, T, rule, rules, {}, tuple(notes), data["sources"])
