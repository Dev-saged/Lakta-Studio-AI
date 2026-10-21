# إعدادات المنتج — تعديل هذا الملف فقط عند البيع لعميل جديد
CONFIG = {
    "app": {"name": "لقطة", "full_name": "استوديو لقطة", "version": "34.0.0"},
    "repo": {"owner": "Dev-saged", "name": "Lakta-Studio-AI", "branch": "main"},
    "server": {"port": 7860, "tunnel": True},
    "models": {
        "text": "gemini-flash-latest",
        "tts": "gemini-3.8-flash-tts",
        "text_pattern": r"^gemini-(\d+(?:\.\d+)?)-flash$",
        "tts_pattern": r"^gemini-(\d+(?:\.\d+)?)-flash(?:-preview)?-tts$",
    },
    "whisper": {
        "fast": {"gpu": "small", "cpu": "small"},
        "balanced": {"gpu": "large-v3-turbo", "cpu": "small"},
        "best": {"gpu": "large-v3", "cpu": "medium"},
    },
    "clip": {"min": 25, "max": 75, "target": "40-60", "max_parts": 3},
    "limits": {"upload_mb": 4096, "font_mb": 20, "image_mb": 10, "music_mb": 50, "json_kb": 512},
    "fonts": {"default": "LaktaAlexandria-ExtraBold.ttf"},
    "dialects": {
        "original": {"label": "لهجة المتكلم", "prompt": ""},
        "msa": {"label": "فصحى", "prompt": "العربية الفصحى المبسطة"},
        "gulf": {"label": "خليجي", "prompt": "اللهجة الخليجية (السعودية)"},
        "egy": {"label": "مصري", "prompt": "اللهجة المصرية"},
        "levant": {"label": "شامي", "prompt": "اللهجة الشامية"},
        "maghreb": {"label": "مغربي", "prompt": "اللهجة المغربية (الدارجة)"},
    },
    "edge_voices": {
        "original": [["ar-SA-HamedNeural", "حامد"], ["ar-SA-ZariyahNeural", "زارية"]],
        "msa": [["ar-SA-HamedNeural", "حامد"], ["ar-SA-ZariyahNeural", "زارية"]],
        "gulf": [["ar-SA-HamedNeural", "حامد"], ["ar-AE-HamdanNeural", "حمدان"], ["ar-KW-FahedNeural", "فهد"], ["ar-AE-FatimaNeural", "فاطمة"], ["ar-KW-NouraNeural", "نورة"]],
        "egy": [["ar-EG-ShakirNeural", "شاكر"], ["ar-EG-SalmaNeural", "سلمى"]],
        "levant": [["ar-SY-LaithNeural", "ليث"], ["ar-JO-TaimNeural", "تيم"], ["ar-LB-RamiNeural", "رامي"], ["ar-SY-AmanyNeural", "أماني"], ["ar-LB-LaylaNeural", "ليلى"]],
        "maghreb": [["ar-MA-JamalNeural", "جمال"], ["ar-MA-MounaNeural", "منى"]],
    },
    "gemini_voices": [["Charon", "شارون · رزين"], ["Fenrir", "فنرير · حماسي"], ["Orus", "أوروس · واثق"], ["Puck", "باك · مرح"],
                      ["Kore", "كور · واثقة"], ["Aoede", "أويدي · هادئة"], ["Leda", "ليدا · شابة"], ["Sulafat", "سُلافة · دافئة"]],
    "defaults": {
        "gemini_key": "", "text_model": "", "tts_model": "",
        "telegram_token": "", "telegram_chat": "", "telegram_zip": True,
        "whisper": "balanced", "resolution": 1080, "speed": 1.0,
        "brand": {
            "caption_style": "classic", "words": 4, "font": "LaktaAlexandria-ExtraBold.ttf",
            "frame_on": True, "frame_color": "#E8402A", "watermark": "", "watermark_pos": "tr",
        },
        "job": {
            "parts": 1, "formats": {"short": True, "wide": True}, "face": True, "hook": False,
            "audio": {"mode": "original", "engine": "edge", "text": "same", "dialect": "original",
                      "voice": "ar-SA-HamedNeural", "music": ""},
        },
    },
}
