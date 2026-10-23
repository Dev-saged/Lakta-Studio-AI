import re
import subprocess
import urllib.request

YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
YUNET_MIRROR = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


class Detector:
    def __init__(self, models_dir, log):
        import cv2
        self.cv2 = cv2
        self.kind, self.det = "haar", None
        model = models_dir / "yunet_2023mar.onnx"
        try:
            if not model.exists():
                try:
                    urllib.request.urlretrieve(YUNET_URL, model)
                except Exception:
                    urllib.request.urlretrieve(YUNET_MIRROR, model)
            self.det = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.7, 0.3, 50)
            self.kind = "yunet"
        except Exception:
            model.unlink(missing_ok=True)
            self.det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            if self.det.empty():
                raise RuntimeError("لا يتوفر نموذج لكشف الوجوه")
            log("تتبع الوجه بالنموذج الاحتياطي (Haar)")

    def faces(self, frame):
        """[(cx, cy, area)] بإحداثيات الإطار"""
        h, w = frame.shape[:2]
        if self.kind == "yunet":
            self.det.setInputSize((w, h))
            _, res = self.det.detect(frame)
            return [(f[0] + f[2] / 2, f[1] + f[3] / 2, f[2] * f[3]) for f in (res if res is not None else [])]
        g = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        res = self.det.detectMultiScale(g, 1.15, 6, minSize=(w // 14, w // 14))
        return [(x + fw / 2, y + fh / 2, fw * fh) for x, y, fw, fh in res]

    def boxes(self, frame):
        """[(x, y, w, h)] لكل وجه"""
        h, w = frame.shape[:2]
        if self.kind == "yunet":
            self.det.setInputSize((w, h))
            _, res = self.det.detect(frame)
            return [tuple(float(v) for v in f[:4]) for f in (res if res is not None else [])]
        g = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        return [tuple(float(v) for v in b) for b in self.det.detectMultiScale(g, 1.15, 6, minSize=(w // 14, w // 14))]


def scenes(video, s, dur, threshold=0.3):
    """أزمنة تغيّر المشهد داخل المقطع (نسبية لبدايته)"""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{s:.3f}", "-t", f"{dur:.3f}", "-i", str(video), "-an",
                            "-vf", f"scale=192:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                           capture_output=True, text=True, errors="replace", timeout=180)
        ts = sorted({round(float(m), 2) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)})
        return [t for t in ts if 0.3 < t < dur - 0.3]
    except Exception:
        return []


def _snap(t, cuts, win=1.0):
    near = [c for c in cuts if abs(c - t) <= win]
    return min(near, key=lambda c: abs(c - t)) if near else t


_det = {}


def plan(video, s, dur_src, src_w, src_h, models_dir, log, step=0.5, layout="track", cuts=None):
    """مواقع القص 9:16 عبر الزمن: [(t_src_rel, x)] + عرض القص
    layout: track أكبر وجه · active المتكلم النشط · split شاشة مقسومة لمتكلمَين (يعيد dict)"""
    crop_w = int(src_h * 9 / 16) // 2 * 2
    center = (src_w - crop_w) // 2
    if crop_w >= src_w:
        return [(0.0, 0)], src_w
    if layout in ("active", "split"):
        try:
            res = _two_speakers(video, s, dur_src, src_w, src_h, crop_w, center, models_dir, log, step, layout, cuts or [])
            if res:
                return res
            log("لم يُعثر على متكلمَين ثابتين · تتبع الوجه الأكبر")
        except Exception as e:
            log(f"تحليل المتكلمَين غير متاح ({str(e)[:80]}) · تتبع الوجه الأكبر")
    try:
        import cv2
        if "d" not in _det:
            _det["d"] = Detector(models_dir, log)
        det = _det["d"]
        cap = cv2.VideoCapture(str(video))
        scale = 640 / src_w if src_w > 640 else 1.0
        samples = []
        t = 0.0
        while t < dur_src:
            cap.set(cv2.CAP_PROP_POS_MSEC, (s + t) * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            if scale != 1.0:
                frame = cv2.resize(frame, None, fx=scale, fy=scale)
            f = det.faces(frame)
            samples.append((t, max(f, key=lambda x: x[2])[0] / scale if len(f) else None))
            t += step
        cap.release()
    except Exception as e:
        _det["d"] = None
        log(f"تتبع الوجه غير متاح ({str(e)[:80]}) · قص من المنتصف")
        return [(0.0, center)], crop_w
    return _smooth(samples, src_w, crop_w, center, step, cuts), crop_w


def _two_speakers(video, s, dur_src, src_w, src_h, crop_w, center, models_dir, log, step, layout, cuts):
    import cv2
    if "d" not in _det or _det["d"] is None:
        _det["d"] = Detector(models_dir, log)
    det = _det["d"]
    cap = cv2.VideoCapture(str(video))
    scale = 640 / src_w if src_w > 640 else 1.0
    rows, t = [], 0.0
    while t < dur_src:
        cap.set(cv2.CAP_PROP_POS_MSEC, (s + t) * 1000)
        ok, f1 = cap.read()
        if not ok:
            break
        for _ in range(4):
            ok2, f2 = cap.read()
        f2 = f2 if ok2 else f1
        if scale != 1.0:
            f1, f2 = cv2.resize(f1, None, fx=scale, fy=scale), cv2.resize(f2, None, fx=scale, fy=scale)
        g1, g2 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY), cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY)
        faces = []
        for x, y, w, h in det.boxes(f1):
            # حركة الفم: فرق الإطارين في النصف السفلي من الوجه
            y0, y1, x0, x1 = int(y + h * 0.55), int(y + h), int(x + w * 0.2), int(x + w * 0.8)
            m = cv2.absdiff(g1[max(0, y0):y1, max(0, x0):x1], g2[max(0, y0):y1, max(0, x0):x1])
            faces.append(((x + w / 2) / scale, (y + h / 2) / scale, h / scale, float(m.mean()) if m.size else 0.0))
        rows.append((t, faces))
        t += step
    cap.release()
    xs = sorted(f[0] for _, fs in rows for f in fs)
    if len(xs) < 6:
        return None
    # فصل الوجوه إلى شخصين بتجميع أحادي البعد على المحور الأفقي
    a, b = xs[len(xs) // 4], xs[3 * len(xs) // 4]
    for _ in range(12):
        ga = [x for x in xs if abs(x - a) <= abs(x - b)]
        gb = [x for x in xs if abs(x - a) > abs(x - b)]
        if not ga or not gb:
            return None
        a, b = sum(ga) / len(ga), sum(gb) / len(gb)
    seen = lambda c: sum(1 for _, fs in rows if any(abs(f[0] - c) < crop_w * 0.4 for f in fs)) / max(1, len(rows))
    if b - a < crop_w * 0.6 or seen(a) < 0.4 or seen(b) < 0.4:
        return None
    people = []
    for c in (a, b):
        fs = [f for _, ff in rows for f in ff if abs(f[0] - c) < crop_w * 0.4]
        people.append((c, sum(f[1] for f in fs) / len(fs), sum(f[2] for f in fs) / len(fs)))
    if layout == "split":
        ch = int(min(src_h, max(p[2] for p in people) * 3.2)) // 2 * 2
        cw = int(ch * 9 / 8) // 2 * 2
        if cw * 2 > src_w * 1.05 or ch < 120:
            return None
        rects = [(int(max(0, min(src_w - cw, cx - cw / 2))) // 2 * 2, int(max(0, min(src_h - ch, cy - ch * 0.42))) // 2 * 2)
                 for cx, cy, _ in people]
        log("شاشة مقسومة لمتكلمَين")
        return {"split": rects, "w": cw, "h": ch}
    # المتكلم النشط: الأعلى حركة فم، مع تثبيت لتجنب التبديل السريع
    samples, cur = [], None
    for t, fs in rows:
        mot = [max([f[3] for f in fs if abs(f[0] - c) < crop_w * 0.4] or [0]) for c, _, _ in people]
        if max(mot) > 0.8 and (cur is None or max(mot) > mot[cur] * 1.35):
            cur = mot.index(max(mot))
        samples.append((t, people[cur][0] if cur is not None else None))
    if all(x is None for _, x in samples):
        return None
    log("تتبع المتكلم النشط")
    return _smooth(samples, src_w, crop_w, center, step, cuts), crop_w


def _smooth(samples, src_w, crop_w, center, step, cuts=None):
    found = [x for _, x in samples if x is not None]
    if not found:
        return [(0.0, center)]
    # ملء الفجوات بآخر موقع معروف
    last = found[0]
    xs = []
    for _, x in samples:
        last = x if x is not None else last
        xs.append(last)
    # وسيط متحرك لإزالة القفزات العابرة
    med = [sorted(xs[max(0, i - 2):i + 3])[len(xs[max(0, i - 2):i + 3]) // 2] for i in range(len(xs))]
    clamp = lambda cx: int(max(0, min(src_w - crop_w, cx - crop_w / 2))) // 2 * 2
    keys = [(0.0, clamp(med[0]))]
    hold = max(2, int(1.0 / step))
    i = 1
    while i < len(med):
        cur = keys[-1][1] + crop_w / 2
        if abs(med[i] - cur) > src_w * 0.10:
            win = med[i:i + hold]
            if len(win) == hold and all(abs(v - cur) > src_w * 0.10 for v in win) and samples[i][0] - keys[-1][0] >= 1.5:
                # الانتقال يقع عند قطع المشهد إن وُجد قريباً فيبدو قطعاً طبيعياً
                kt = _snap(samples[i][0], cuts or [])
                if kt - keys[-1][0] >= 1.0:
                    keys.append((round(kt, 2), clamp(sum(win) / len(win))))
                i += hold
                continue
        i += 1
    return keys[:60]


def expr(keys):
    """تعبير ffmpeg لموضع القص بحسب الزمن (لقطات ثابتة تنتقل بين المتكلمين)"""
    if len(keys) == 1:
        return str(keys[0][1])
    e = str(keys[-1][1])
    for (t, x), (t_next, _) in reversed(list(zip(keys, keys[1:]))):
        e = f"if(lt(t,{t_next}),{x},{e})"
    return e


def x_at(keys, t):
    x = keys[0][1]
    for kt, kx in keys:
        if kt <= t:
            x = kx
    return x
