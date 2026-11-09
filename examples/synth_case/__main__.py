"""python -m examples.synth_case [out_dir]"""

import sys
from pathlib import Path

from . import design

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
case = design.build(workdir=out.resolve() / "work")
sys.exit(0 if design.project(case).build(out).ok else 1)
