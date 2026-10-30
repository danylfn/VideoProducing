"""Автоматична проверка на всеки генериран shot: „дишащ“ zoom (навътре и пак навън), рязък zoom
или прекалено голям zoom. Мащабът кадър по кадър се мери с ORB точки + similarity transform (OpenCV).
Праговете са калибрирани върху 34 shots (лошите от първата версия + новите): всички лоши се хващат,
плавни push-in до ~25% минават."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REVERSAL = 0.04    # zoom > 4% в едната посока и после връщане с > 4% = „дишане“
MAX_STEP = 0.06    # > 6% zoom за STEP_WINDOW кадъра (1/6 s) = рязък zoom
STEP_WINDOW = 5    # по-къс прозорец хваща шум (ръце/предмети на преден план), не истински скок
MAX_NET = 0.30     # > 30% общ zoom за един shot = преиграно движение (героят излиза от кадър)
VERSION = 2        # сменя се при промяна на праговете -> старите резултати се преизчисляват


def zoom_profile(video: Path) -> list[float]:
    """Мащаб между всеки два поредни кадъра (1.0 = без zoom)."""
    import cv2
    orb = cv2.ORB_create(1500)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    cap = cv2.VideoCapture(str(video))
    prev, scales = None, []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(cv2.resize(frame, (512, 288)), cv2.COLOR_BGR2GRAY)
        kp, des = orb.detectAndCompute(gray, None)
        scale = 1.0
        if prev is not None and des is not None and prev[1] is not None and len(kp) > 20:
            m = matcher.match(prev[1], des)
            if len(m) > 20:
                a = np.float32([prev[0][x.queryIdx].pt for x in m])
                b = np.float32([kp[x.trainIdx].pt for x in m])
                M, _ = cv2.estimateAffinePartial2D(a, b, ransacReprojThreshold=2.0)
                if M is not None:
                    scale = float(np.hypot(M[0, 0], M[1, 0]))
        if prev is not None:
            scales.append(scale)
        prev = (kp, des)
    cap.release()
    return scales


def check_shot(video: Path) -> dict:
    """{"ok", "reason", "net", "step"}; резултатът се кешира до клипа (<shot>.qa.json)."""
    qa_file = video.with_suffix(".qa.json")
    if qa_file.exists() and qa_file.stat().st_mtime >= video.stat().st_mtime:
        res = json.loads(qa_file.read_text())
        if res.get("v") == VERSION:
            return res
    s = np.clip(np.array(zoom_profile(video) or [1.0]), 0.9, 1.1)
    z = np.cumprod(s)
    zs = np.convolve(z, np.ones(9) / 9, mode="valid") if len(z) > 9 else z  # без шума от отделни кадри
    net, hi, lo = zs[-1] / zs[0] - 1, zs.max() / zs[0], zs.min() / zs[0]
    lz = np.concatenate([[0.0], np.cumsum(np.log(s))])
    w = min(STEP_WINDOW, len(lz) - 1)
    step = float(np.abs(lz[w:] - lz[:-w]).max()) if w > 0 else 0.0
    reasons = []
    if (hi - 1 > REVERSAL and zs[-1] < zs.max() * (1 - REVERSAL)) or \
       (1 - lo > REVERSAL and zs[-1] > zs.min() * (1 + REVERSAL)):
        reasons.append(f"zoom навътре и навън ({(lo - 1) * 100:+.0f}%..{(hi - 1) * 100:+.0f}%)")
    if step > MAX_STEP:
        reasons.append(f"рязък zoom ({step * 100:.1f}% за {STEP_WINDOW} кадъра)")
    if abs(net) > MAX_NET:
        reasons.append(f"прекалено голям zoom ({net * 100:+.0f}%)")
    res = {"ok": not reasons, "reason": "; ".join(reasons), "net": round(float(net), 4), "step": round(step, 4),
           "v": VERSION}
    qa_file.write_text(json.dumps(res))
    return res
