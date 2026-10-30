"""
LTX-2.5 image-to-video worker. Работи в отделната среда .venv-ltx (ltx-2-mlx има свои зависимости).

  .venv-ltx/bin/python tools/ltx_worker.py jobs.json

jobs.json: {"model_dir": "<локална папка на модела>", "low_ram": false,
            "jobs": [{"id": "...", "prompt": "...", "negative": "..." | null, "image": "key.png",
                      "frames": 121, "width": 1024, "height": 576, "fps": 30, "seed": 1, "out": "shot.mp4"}]}

Моделът се зарежда веднъж за всички клипове. Готовите клипове (out съществува) се прескачат.
"negative" се прилага през NAG (Normalized Attention Guidance) – distilled моделът няма CFG.
Към главния процес пише по един ред "@@LTX {json}" на събитие; всичко останало е лог.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def emit(**event) -> None:
    print("@@LTX " + json.dumps(event), flush=True)


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text())
    jobs = [j for j in spec["jobs"] if not Path(j["out"]).exists()]
    emit(event="start", total=len(spec["jobs"]), todo=len(jobs))
    if not jobs:
        return 0

    from ltx_core_mlx.guidance.nag import NAGConfig
    from ltx_pipelines_mlx.distilled import DistilledPipeline

    pipe = DistilledPipeline(model_dir=spec["model_dir"], low_memory=True,
                             low_ram_streaming=bool(spec.get("low_ram")))
    pipe.generate_audio = False  # гласът идва от пайплайна (Kokoro)
    pipe.verbose = bool(spec.get("verbose"))

    for job in jobs:
        out = Path(job["out"])
        tmp = out.with_suffix(".tmp.mp4")
        neg = dict(negative_prompt=job["negative"], nag=NAGConfig()) if job.get("negative") else {}
        t0 = time.perf_counter()
        pipe.generate_and_save(prompt=job["prompt"], output_path=str(tmp), height=job["height"],
                               width=job["width"], num_frames=job["frames"], frame_rate=job["fps"],
                               seed=job["seed"], image=job["image"], **neg)
        tmp.replace(out)
        emit(event="done", id=job["id"], frames=job["frames"], seconds=round(time.perf_counter() - t0, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
