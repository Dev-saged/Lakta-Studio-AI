"""هويات بصرية محفوظة: قناة أو عميل لكل هوية، والتبديل بضغطة"""
import copy
import json
import re


def _path(env):
    return env.base / "brands.json"


def load(env):
    try:
        return json.loads(_path(env).read_text("utf-8"))
    except Exception:
        return {}


def _write(env, data):
    tmp = _path(env).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tmp.replace(_path(env))


def act(env, settings, action, name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
    data = load(env)
    if action == "save":
        if not name:
            raise ValueError("اكتب اسماً للهوية")
        if name not in data and len(data) >= 24:
            raise ValueError("وصلت للحد الأقصى من الهويات المحفوظة")
        data[name] = copy.deepcopy(settings.data["brand"])
    elif action == "apply":
        if name not in data:
            raise ValueError("هوية غير موجودة")
        settings.update({"brand": data[name]})
    elif action == "delete":
        data.pop(name, None)
    else:
        raise ValueError("إجراء غير معروف")
    _write(env, data)
    return sorted(data)
