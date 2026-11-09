"""Talks to the sheetkernel binary: writes a JSON job, runs it, parses the result."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np

from .fold import SheetLayout

REPO = Path(__file__).resolve().parent.parent
KERNEL = Path(os.environ.get("QFOLD_KERNEL", REPO / "kernel" / "build" / "sheetkernel"))


def _jsonable(x):
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    raise TypeError(f"not JSON serialisable: {type(x)}")


def flat_body(lay: SheetLayout, name: str | None = None) -> dict:
    """The flat pattern of a sheet metal body as a kernel body prototype."""
    return {"name": name or f"{lay.body.name}#flat", "plates": lay.plates_flat, "sectors": []}


def run(job: dict, workdir: Path, tag: str = "kernel") -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    job = dict(job)
    job.setdefault("bodies", [])
    job["result"] = str(workdir / f"{tag}_result.json")
    job_path = workdir / f"{tag}_job.json"
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


def inspect_step(path: Path, workdir: Path) -> list[dict]:
    """The product tree of a STEP file: names, local transforms, paths."""
    path = Path(path).resolve()
    res = run({"outputs": {"inspect_step": [str(path)]}}, workdir, tag="inspect")
    return res["inspect_step"][str(path)]
