"""Talks to the sheetkernel binary: builds a JSON job from fold layouts, runs it,
and returns the parsed result."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np

from .fold import Layout

REPO = Path(__file__).resolve().parent.parent
KERNEL = Path(os.environ.get("QFOLD_KERNEL", REPO / "kernel" / "build" / "sheetkernel"))


def _jsonable(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    raise TypeError(f"not JSON serialisable: {type(x)}")


def folded_body(lay: Layout, name: str | None = None) -> dict:
    return {"name": name or lay.part.name, "plates": lay.plates3d, "sectors": lay.sectors,
            "place": lay.part.placement.tolist()}


def flat_body(lay: Layout, name: str | None = None) -> dict:
    return {"name": name or f"{lay.part.name}_flat", "plates": lay.plates_flat, "sectors": []}


def run(job: dict, workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    job = dict(job)
    job["result"] = str(workdir / "kernel_result.json")
    job_path = workdir / "kernel_job.json"
    job_path.write_text(json.dumps(job, default=_jsonable))
    if not KERNEL.exists():
        raise FileNotFoundError(f"sheetkernel not built: {KERNEL} (run scripts/bootstrap.sh)")
    proc = subprocess.run([str(KERNEL), str(job_path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"sheetkernel exited {proc.returncode}: {proc.stderr.strip()}")
    res = json.loads(Path(job["result"]).read_text())
    if not res.get("ok"):
        raise RuntimeError(f"sheetkernel failed: {res.get('error')}")
    return res
