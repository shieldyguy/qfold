"""Stand-in hardware: simple box-and-cylinder models with honest envelopes.

Each part's origin is its mounting point, with z pointing out of the case:
panel parts mount on the panel's inside surface with the shaft along +z,
screws sit under the head with the shank along -z, and feet sit on the base
with the foot along +z. Dimensions are typical values, not datasheet values.
Swap in vendor STEP models with `insert_step` when you have them.
"""

from __future__ import annotations

from .assembly import Component, box, cyl, prim_component

DARK, METAL, BLACK, GREEN = (52, 54, 58), (170, 172, 176), (28, 28, 30), (36, 110, 62)


def pot_9mm(body_h: float) -> Component:
    return prim_component("pot_9mm", [box((-4.75, -5.5, -body_h), 9.5, 11.0, body_h),
                                      cyl((0, 0, 0), 7.0, 5.0), cyl((0, 0, 5.0), 6.0, 15.0)], DARK)


def toggle_m6(body_h: float) -> Component:
    return prim_component("toggle_m6", [box((-6.5, -4.0, -body_h), 13.0, 8.0, body_h),
                                        cyl((0, 0, 0), 6.0, 7.0), cyl((0, 0, 7.0), 3.0, 11.0)], METAL)


def button_12mm(body_h: float) -> Component:
    return prim_component("button_12mm", [box((-6.0, -6.0, -body_h), 12.0, 12.0, body_h),
                                          cyl((0, 0, 0), 11.6, 4.0)], BLACK)


def led_3mm(body_h: float) -> Component:
    return prim_component("led_3mm", [cyl((0, 0, -body_h), 3.0, body_h + 2.0)], (200, 40, 40))


def oled_096(body_h: float) -> Component:
    return prim_component("oled_096", [box((-17.5, -9.5, -body_h), 35.0, 19.0, body_h)], BLACK)


def screw_m3x6() -> Component:
    return prim_component("screw_M3x6_pan", [cyl((0, 0, -6.0), 3.0, 6.0), cyl((0, 0, 0), 5.5, 2.0)],
                          METAL)


def rubber_foot() -> Component:
    return prim_component("rubber_foot_12x4", [cyl((0, 0, 0), 12.0, 4.0)], BLACK)


def pcb(w: float, h: float, t: float = 1.6) -> Component:
    return prim_component("control_board_pcb", [box((0, 0, -t), w, h, t)], GREEN)


STANDIN = {"pot_9mm": pot_9mm, "toggle_m6": toggle_m6, "button_12mm": button_12mm,
           "led_3mm": led_3mm, "oled_096": oled_096}

# Render colours for the stand-ins once they come back from STEP (which
# carries no colours here).
COLORS = {"control_board_pcb": GREEN, "pot_9mm": DARK, "toggle_m6": METAL,
          "button_12mm": BLACK, "led_3mm": (200, 40, 40), "oled_096": BLACK}
