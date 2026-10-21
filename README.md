# استوديو لقطة · Lakta Studio AI (V34)

مصنع لقطات قصيرة يحوّل الحلقات الطويلة والبودكاست إلى Shorts/Reels ومقاطع يوتيوب عريضة جاهزة للنشر، بواجهة عربية تعمل من المتصفح على Kaggle و Colab و VPS.

## المميزات

- **مونتير ذكي (Gemini):** يقرأ التفريغ الكامل للحلقة ويختار أقوى 1-3 لقطات، مع عنوان وخطاف ونص غلاف ودرجة فيروسية لكل لقطة.
- **تفريغ كلمة بكلمة (faster-whisper):** توقيت دقيق لكل كلمة، ويعمل على GPU تلقائياً. ترجمة يوتيوب تبقى احتياطياً.
- **ترجمة كاريوكي:** الكلمة المنطوقة تُبرَز لحظياً، بثلاثة أنماط (أصفر كلاسيكي، صندوق كوبالت، قلم تظليل) وبخطّك الخاص.
- **صوت جديد بلهجات:** إعادة توليد الكلام بصوت معلّق جديد (Edge مجاناً أو Gemini TTS)، بنفس الكلام أو بإعادة صياغة، وبأي لهجة. الترجمة على الشاشة متزامنة مع الصوت الجديد.
- **ثلاثة أوضاع للصوت:** الأصلي كما هو (مونتاج فقط)، أو صوت جديد فوق الخلفية الأصلية (فصل بـ Demucs)، أو صوت جديد فقط مع موسيقى من مكتبتك.
- **قص 9:16 بتتبع الوجه:** القص يتبع المتكلم بدل القص من المنتصف.
- **محرك FFmpeg:** أسرع بكثير من MoviePy، ويستخدم NVENC على GPU عند توفره.
- **مصادر متعددة:** روابط دفعية، أو صيد التريند، أو رفع ملف من جهازك.
- **الهوية البصرية:** أغلفة عريضة وشورتس برقم الجزء، وإطار ملوّن، وعلامة مائية، وخطوط مرفوعة.
- **تيليجرام:** يُرسل ملف ZIP فيه اللقطات والأغلفة والعناوين، وإن تجاوز 50MB تُرسل الملفات منفردة.

## التشغيل

### Kaggle (مجاني · GPU T4)
1. فعّل **Internet** و **GPU T4** من إعدادات الدفتر.
2. أضف من **Add-ons → Secrets** المفتاح `GEMINI_API_KEY`، واختيارياً `TELEGRAM_TOKEN` و `TELEGRAM_CHAT_ID`.
3. شغّل هذه الخلية:
```python
!git clone -q https://github.com/Dev-saged/Lakta-Studio-AI /kaggle/working/lakta 2>/dev/null || git -C /kaggle/working/lakta pull -q
%cd /kaggle/working/lakta
from run import main; main()
```
4. افتح الرابط الذي يظهر (`https://….trycloudflare.com/?k=…`) من أي متصفح، على الجوال أو الكمبيوتر.

### Colab
نفس الخلية مع تغيير المسار إلى `/content/lakta`. الأسرار من 🔑 Secrets بنفس الأسماء، والملفات تُحفظ في Google Drive داخل `MyDrive/Lakta_System`.

### VPS
```bash
sudo apt install -y ffmpeg git
git clone https://github.com/Dev-saged/Lakta-Studio-AI && cd Lakta-Studio-AI
pip install -r requirements.txt
GEMINI_API_KEY=… python run.py            # مع نفق Cloudflare
python run.py --no-tunnel --port 7860      # أو على المنفذ مباشرة
```

### فحص ذاتي
`python run.py --selftest` يمرّر فيديو اصطناعياً عبر خط الإنتاج كاملاً بدون إنترنت. وفي الواجهة: الإعدادات ← فحص ذاتي.

## الأمان
- رابط الاستوديو محمي بمفتاح دخول عشوائي (ملف `Lakta_System/.access_key`). من يملك الرابط الكامل يدخل، فلا تنشره.
- الأسرار تُقرأ من Secrets أو متغيرات البيئة، ولا تُكتب في الكود أبداً.

## البنية
```
run.py            خلية التشغيل: المكتبات، البيئة، السيرفر، النفق
config.py         CONFIG: الهوية، النماذج، اللهجات، الأصوات، القيم الافتراضية
lakta/
  env.py          اكتشاف Kaggle/Colab/VPS، الأسرار، الإعدادات، أدوات ffmpeg
  server.py       واجهة API، البث الحي (SSE)، الرفع، الملفات
  jobs.py         الطابور وحالة المهام
  pipeline.py     خط الإنتاج كاملاً
  ingest.py       yt-dlp، التريند، الملفات المرفوعة
  transcribe.py   faster-whisper وموجة الصوت
  director.py     Gemini: اختيار اللقطات، SEO، الصياغة واللهجات
  voice.py        Edge TTS و Gemini TTS
  audio.py        Demucs، ضبط المدة، المزج
  reframe.py      تتبع الوجه للقص 9:16
  captions.py     ترجمة ASS كاريوكي
  render.py       الإخراج بـ FFmpeg
  thumbs.py       الأغلفة
  notify.py       تيليجرام
  tunnel.py       نفق Cloudflare
web/index.html    الواجهة كملف واحد (PWA)
assets/fonts/     خطوط عربية افتراضية (OFL)
```
