"""A blocked-out Huaxin 37-key keybed (C to C), to its measured sizes.

One white volume for the keys and the frame under them, one charcoal volume
for each group of black keys, and the eight mounting bosses. The gaps
between keys aren't modelled. Origin: the front-left corner of the lowest
white key, at white key top height; x right, y back, z up.
"""

from qfold.assembly import Body, Component, cyl, extrude

W, D = 511.4, 161.4  # width; depth to the back of the frame
KEYS = 160.0  # depth to the back ends of the keys
FRAME_FRONT = 47.5  # the keys overhang the frame's front by this much
FRONT_SKIRT = 8.0  # underside of a white key at its front, below its top
SKIRT = 14.0  # the same where the key meets the frame: the underside slopes
FLOOR = 48.5  # boss bottoms below the white key tops
BLACK_FRONT, BLACK_BACK, BLACK_TOP = 51.1, 136.1, 11.0  # the black keys' raised part
BOSS_X = (20.4, 156.0, 332.8, 495.9)
BOSS_Y = (70.7, 146.2)
BOSS_D, BOSS_BORE = 7.5, 3.2

# Side view (y, z) of everything that isn't a black key or a boss.
PROFILE = [(0, 0), (0, -FRONT_SKIRT), (FRAME_FRONT, -SKIRT), (FRAME_FRONT, -32.0), (50.6, -32.0),
           (50.6, -37.0), (66.0, -37.0), (66.0, -24.8), (82.7, -24.8), (82.7, -26.5),
           (138.8, -26.5), (138.8, -24.8), (139.0, -24.8), (139.0, -25.3), (D, -25.3),
           (D, -18.5), (KEYS, -18.5), (KEYS, 0)]
# Side view of a black key above the white keys: a sloped front, a level
# top, a top that falls away to the back, and a square back face.
BLACK = [(BLACK_FRONT, 0), (BLACK_BACK, 0), (BLACK_BACK, 7.0), (96.6, BLACK_TOP),
         (60.2, BLACK_TOP)]
# Each group of black keys, from the first key's left side to the last one's
# right (at their base): C#-D#, F#-A#, and so on up the keyboard.
BLACK_GROUPS = [(15.585, 53.125), (84.455, 147.435), (178.685, 216.225), (247.555, 310.535),
                (341.785, 379.325), (410.655, 473.635)]
BOSS_TOP = {BOSS_Y[0]: -24.8, BOSS_Y[1]: -25.3}  # where each row meets the frame


def keybed(white=(235, 235, 230), black=(52, 52, 56), boss=(150, 150, 150)) -> Component:
    side = dict(x=(0, 1, 0), y=(0, 0, 1))  # outlines drawn in (y, z), extruded along x
    bodies = [Body("keys_and_frame", prims=[extrude(PROFILE, W, **side)], color=white)]
    for i, (x0, x1) in enumerate(BLACK_GROUPS, 1):
        bodies.append(Body(f"black_keys_{i}", prims=[extrude(BLACK, x1 - x0, o=(x0, 0, 0), **side)],
                           color=black))
    for i, (x, y) in enumerate([(x, y) for y in BOSS_Y for x in BOSS_X], 1):
        bodies.append(Body(f"boss_{i}", color=boss, prims=[
            cyl((x, y, -FLOOR), BOSS_D, FLOOR + BOSS_TOP[y], bore=BOSS_BORE)]))
    return Component("keybed", bodies, source="stand-in, measured from the maker's model")
