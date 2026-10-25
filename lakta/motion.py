"""موشن فخم: حلقات وتوهّج متحركة عند عبارة الذروة والافتتاحية والأرقام المعدودة
نفس أسلوب الزوم الموجود (scale+fade زمنيين) لا geq — geq بطيء جداً على دقة الشورتس الكاملة"""
import numpy as np
from PIL import Image

S = 512


def assets(assets_dir):
    """حلقة وتوهّج أبيض شفاف يُرسمان مرة ويُعاد تلوينهما بلون العلامة وقت الرندر (colorchannelmixer)"""
    ring, glow = assets_dir / "motion_ring.png", assets_dir / "motion_glow.png"
    yy, xx = np.mgrid[0:S, 0:S]
    d = np.sqrt((xx - S / 2) ** 2 + (yy - S / 2) ** 2) / (S / 2)
    if not ring.exists():
        a = np.clip(255 * np.exp(-((d - 0.78) / 0.07) ** 2), 0, 255).astype("uint8")
        rgba = np.zeros((S, S, 4), "uint8"); rgba[..., :3] = 255; rgba[..., 3] = a
        Image.fromarray(rgba, "RGBA").save(ring)
    if not glow.exists():
        a = np.clip(255 * (1 - d) ** 2, 0, 255); a[d > 1] = 0
        rgba = np.zeros((S, S, 4), "uint8"); rgba[..., :3] = 255; rgba[..., 3] = a.astype("uint8")
        Image.fromarray(rgba, "RGBA").save(glow)
    return ring, glow


def cues(ex, fx, out_dur, cap=3):
    """نقاط الوميض من extras/fx الموجودين أصلاً؛ أحجام نسبية لعرض اللقطة، مركزية أفقياً"""
    pts = []
    punch = ex.get("punch")
    if punch and 0 <= punch[0] < out_dur:
        pts.append({"asset": "ring", "cy": 0.5, "w0": 0.55, "w1": 1.05, "t0": float(punch[0]), "dur": 0.65, "fin": 0.1, "fout": 0.3})
    flash = (fx or {}).get("flash")
    if flash is not None and 0 <= flash < out_dur:
        pts.append({"asset": "glow", "cy": 0.5, "w0": 0.3, "w1": 1.3, "t0": float(flash), "dur": 0.5, "fin": 0.05, "fout": 0.3})
    for t0, _ in (ex.get("marks") or [])[:cap]:
        if 0 <= t0 < out_dur:
            pts.append({"asset": "ring", "cy": 0.15, "w0": 0.12, "w1": 0.28, "t0": float(t0), "dur": 0.4, "fin": 0.06, "fout": 0.16})
    return pts
