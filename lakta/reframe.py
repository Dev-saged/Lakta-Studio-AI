import urllib.request

YUNET_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"


class Detector:
    def __init__(self, models_dir, log):
        import cv2
        self.cv2 = cv2
        self.kind, self.det = "haar", None
        model = models_dir / "yunet_2023mar.onnx"
        try:
            if not model.exists():
                urllib.request.urlretrieve(YUNET_URL, model)
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


_det = {}


def plan(video, s, dur_src, src_w, src_h, models_dir, log, step=0.5):
    """مواقع القص 9:16 عبر الزمن: [(t_src_rel, x)] + عرض القص"""
    crop_w = int(src_h * 9 / 16) // 2 * 2
    center = (src_w - crop_w) // 2
    if crop_w >= src_w:
        return [(0.0, 0)], src_w
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
    return _smooth(samples, src_w, crop_w, center, step), crop_w


def _smooth(samples, src_w, crop_w, center, step):
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
                keys.append((round(samples[i][0], 2), clamp(sum(win) / len(win))))
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
