import os
import glob
import json
import re
import gc
import shutil
import traceback
import textwrap
import requests
import numpy as np
import gradio as gr
import yt_dlp
import webvtt
import librosa
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont, features
from moviepy.editor import VideoFileClip, concatenate_videoclips, CompositeVideoClip, ImageClip, CompositeAudioClip
from moviepy.audio.AudioClip import AudioArrayClip

# 1. بيانات تيليجرام (تقرأ من السيرفر أو تستخدم القيم الافتراضية)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8200509516:AAHVrAUZHu-MKvVmLMjsguJELWalaagqHcw")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "277824019")

def send_telegram_message(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception:
        pass

def send_telegram_document(file_path, caption=""):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
        with open(file_path, 'rb') as f:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"document": f}, timeout=120)
    except Exception:
        pass

# 2. التعرف التلقائي على البيئة وتجهيز المسارات (VPS / Kaggle / Colab)
if os.path.exists('/kaggle/working'):
    BASE_DIR = '/kaggle/working/Lakta_System'
    TEMP_DIR = '/kaggle/working/'
elif os.path.exists('/content'):
    try:
        from google.colab import drive
        drive.mount('/content/drive')
        BASE_DIR = '/content/drive/MyDrive/Lakta_System'
    except Exception:
        BASE_DIR = '/content/Lakta_System'
    TEMP_DIR = '/content/'
else:
    BASE_DIR = os.path.abspath('./Lakta_System')
    TEMP_DIR = os.path.abspath('./temp_work')

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
FONT_DIR = os.path.join(BASE_DIR, 'Fonts')
os.makedirs(FONT_DIR, exist_ok=True)

# 3. محرك الخطوط الذكي
def get_best_font():
    fallback = os.path.join(TEMP_DIR, "emergency_font.ttf")
    if os.path.exists(fallback):
        try:
            ImageFont.truetype(fallback, 50)
        except Exception:
            os.remove(fallback)
            
    if not os.path.exists(fallback):
        try:
            r = requests.get("https://github.com/google/fonts/raw/main/ofl/cairo/Cairo-Bold.ttf", timeout=15)
            with open(fallback, "wb") as f:
                f.write(r.content)
        except Exception:
            os.system(f'wget -q -O "{fallback}" "https://github.com/google/fonts/raw/main/ofl/cairo/Cairo-Bold.ttf"')
    return fallback

def test_api_connection(api_key):
    if not api_key:
        return "⚠️ الرجاء إدخال مفتاح API أولاً."
    try:
        genai.configure(api_key=api_key)
        models = [m.name for m in genai.list_models()]
        if models:
            return "✅ الاتصال ممتاز! مفتاحك يعمل بنجاح."
        return "⚠️ الاتصال تم ولكن لا يوجد نماذج متاحة."
    except Exception as e:
        return f"❌ فشل الاتصال! التفاصيل:\n{str(e)}"

# 4. نظام الهندسة الصوتية (لتخطي الحقوق - حلال 100%)
def make_ambient_audio(duration):
    sr = 22050
    samples = int(sr * duration)
    noise = np.random.normal(0, 0.015, samples)
    stereo_noise = np.column_stack((noise, noise))
    audio_clip = AudioArrayClip(stereo_noise, fps=sr)
    return audio_clip.set_duration(duration)

# 5. رسم النص العربي باحترافية
def draw_arabic_text_pro(draw, pos, text, font, fill, stroke_width=0, stroke_fill="black", align='center'):
    if features.check_feature('raqm'):
        bbox = draw.textbbox(pos, text, font=font, direction='rtl', language='ar', stroke_width=stroke_width, align=align, anchor="mm")
        draw.multiline_text(pos, text, font=font, fill=fill, direction='rtl', language='ar', stroke_width=stroke_width, stroke_fill=stroke_fill, align=align, anchor="mm")
        return bbox
    else:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaper = arabic_reshaper.ArabicReshaper(configuration={'delete_harakat': True})
        bidi_text = get_display(reshaper.reshape(text))
        bbox = draw.textbbox(pos, bidi_text, font=font, stroke_width=stroke_width, align=align, anchor="mm")
        draw.multiline_text(pos, bidi_text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill, align=align, anchor="mm")
        return bbox

# 6. صانع الترجمة المطبوعة والخطاف
def create_subtitle_clip(text, duration, video_width, video_height, font_path, is_shorts=False, y_pos_pct=None):
    video_width, video_height = int(video_width), int(video_height)
    img = Image.new('RGBA', (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    font_size = int(video_width * 0.08) if is_shorts else int(video_height * 0.07)
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception:
        font = ImageFont.load_default()
    
    x_center = video_width / 2
    if y_pos_pct is not None:
        y_center = video_height * y_pos_pct
        fill_color = "#00FF00"
    else:
        y_center = video_height * 0.80 if not is_shorts else video_height * 0.70
        fill_color = "#FFFF00"
    
    for i in range(4, 0, -1):
        draw_arabic_text_pro(draw, (x_center - i, y_center + i), text, font, fill="black", stroke_width=6, stroke_fill="black")
    draw_arabic_text_pro(draw, (x_center, y_center), text, font, fill=fill_color, stroke_width=3, stroke_fill="black")
    
    return ImageClip(np.array(img)).set_duration(duration)

# 7. المونتير الذكي
def ai_semantic_editor(api_key, vtt_file, log_tracker, num_parts=1):
    if not vtt_file:
        return [[10.0, 60.0]], log_tracker + "⚠️ لا يوجد سكربت.\n"
    transcript = ""
    for caption in list(webvtt.read(vtt_file))[:200]:
        transcript += f"[{caption.start_in_seconds:.1f} - {caption.end_in_seconds:.1f}] {caption.text}\n"

    prompt = f"""أنت مخرج يوتيوب محترف. إليك سكربت زمني:
{transcript}
اختر أفضل وأقوى {num_parts} مقاطع منفصلة (أجزاء). كل جزء يجب أن يكون مشوقاً ومدته بين 40 و 60 ثانية.
رد *فقط* بـ JSON مصفوفة تحتوي على الفترات، مثال لمقطعين: [[12.5, 65.0], [120.0, 175.0]]"""
    
    genai.configure(api_key=api_key)
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        match = re.search(r'\[\s*\[.*?\]\s*\]', response.text, re.DOTALL)
        if match:
            log_tracker += f"🧠 [AI] تم تحليل السكربت واختيار {num_parts} أجزاء بنجاح.\n"
            return json.loads(match.group(0)), log_tracker
    except Exception:
        pass
    fallback_cuts = [[15.0, 65.0], [80.0, 130.0], [150.0, 200.0]]
    return fallback_cuts[:num_parts], log_tracker + "⚠️ [AI] فشل التحليل، سيتم استخدام التقطيع الافتراضي.\n"

# 8. الذكاء الاصطناعي للنصوص (SEO + Hook)
def generate_masterpiece_seo(api_key, title, orig_desc, log_tracker):
    genai.configure(api_key=api_key)
    prompt = f"""الفيديو: {title}. استخرج مفصولاً بكلمة ---SEP--- :
    1. عنوان قوي جداً.
    2. جملة صادمة للغلاف (3 كلمات بحد أقصى). (ممنوع Emojis).
    3. وصف مع هاشتاقات.
    4. جملة خطافية (Hook) للطباعة أعلى الفيديو: سؤال مستفز أو معلومة من 3-4 كلمات.
    مهم جداً: استخرج النص الخام فقط بدون رموز مثل **."""
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response_text = model.generate_content(prompt).text.replace('**', '').replace('__', '')
        parts = response_text.split('---SEP---')
        if len(parts) >= 3:
            cln_title = re.sub(r'^[\d\.\-\*]+\s*(العنوان:)?\s*', '', parts[0].strip())
            cln_thumb = re.sub(r'^[\d\.\-\*]+\s*(الغلاف:)?\s*', '', parts[1].strip())
            cln_desc = re.sub(r'^[\d\.\-\*]+\s*(الوصف:)?\s*', '', parts[2].strip())
            cln_hook = re.sub(r'^[\d\.\-\*]+\s*(الخطاف:)?\s*', '', parts[3].strip()) if len(parts) > 3 else ""
            return cln_title, cln_thumb, cln_desc, cln_hook, log_tracker
    except Exception:
        pass
    return "🔥 لقطة حصرية!", "شاهد الصدمة", "#لقطة", "هل تصدق هذا؟", log_tracker

def clean_pil_color(color_val):
    if isinstance(color_val, str) and color_val.startswith(('rgb', 'rgba')):
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", color_val)
        if len(nums) >= 3:
            return (int(float(nums[0])), int(float(nums[1])), int(float(nums[2])), 255)
    return color_val

# 9. تصميم الغلاف الجبار
def create_thumbnails(image_path, text, dir_path, font_path, use_frame, frame_color, part_num):
    part_words = ["الأول", "الثاني", "الثالث", "الرابع", "الخامس"]
    part_word = part_words[part_num-1] if part_num-1 < len(part_words) else str(part_num)
    part_text = f"الجزء {part_word}"
    
    img_wide = Image.open(image_path).convert("RGBA")
    w, h = int(img_wide.size[0]), int(img_wide.size[1])
    
    img_w_comp = img_wide.copy()
    draw_w = ImageDraw.Draw(img_w_comp)
    font_size_w = int(w * 0.12)
    font_w = ImageFont.truetype(font_path, font_size_w)
    x_w, y_w = w / 2, h * 0.70
    
    if use_frame:
        safe_color = clean_pil_color(frame_color)
        thickness_w = int(w * 0.02)
        draw_w.rectangle([0, 0, w, h], outline=safe_color, width=thickness_w)
        draw_w.rectangle([thickness_w, thickness_w, w-thickness_w, h-thickness_w], outline="white", width=int(thickness_w/4))

    for i in range(8, 0, -1):
        draw_arabic_text_pro(draw_w, (x_w - i, y_w + i), text, font_w, fill="#1A0000", stroke_width=6, stroke_fill="black")
    draw_arabic_text_pro(draw_w, (x_w, y_w), text, font_w, fill="#FFEA00", stroke_width=4, stroke_fill="black")
    
    y_part_w = y_w + (font_size_w * 1.3)
    font_part_w = ImageFont.truetype(font_path, int(font_size_w * 0.8))
    for i in range(5, 0, -1):
        draw_arabic_text_pro(draw_w, (x_w - i, y_part_w + i), part_text, font_part_w, fill="#1A0000", stroke_width=4, stroke_fill="black")
    draw_arabic_text_pro(draw_w, (x_w, y_part_w), part_text, font_part_w, fill="#00FF00", stroke_width=3, stroke_fill="black")
    
    path_wide = os.path.join(dir_path, f"Thumbnail_Wide_Part{part_num}.jpg")
    img_w_comp.convert("RGB").save(path_wide, quality=95)
    
    target_w = int((h * 9) / 16)
    target_w = target_w - (target_w % 2)
    x_center = w / 2
    img_short = img_wide.crop((x_center - target_w/2, 0, x_center + target_w/2, h))
    sw, sh = int(img_short.size[0]), int(img_short.size[1])
    
    img_s_comp = img_short.copy()
    draw_s = ImageDraw.Draw(img_s_comp)
    font_size_s = int(sw * 0.16)
    font_s = ImageFont.truetype(font_path, font_size_s)
    x_s, y_s = sw / 2, sh * 0.55
    
    if use_frame:
        safe_color = clean_pil_color(frame_color)
        thickness_s = int(sw * 0.02)
        draw_s.rectangle([0, 0, sw, sh], outline=safe_color, width=thickness_s)
        draw_s.rectangle([thickness_s, thickness_s, sw-thickness_s, sh-thickness_s], outline="white", width=int(thickness_s/4))

    wrapped_text = "\n".join(textwrap.wrap(text, width=10))
    for i in range(6, 0, -1):
        draw_arabic_text_pro(draw_s, (x_s - i, y_s + i), wrapped_text, font_s, fill="#1A0000", stroke_width=6, stroke_fill="black")
    draw_arabic_text_pro(draw_s, (x_s, y_s), wrapped_text, font_s, fill="#FFEA00", stroke_width=3, stroke_fill="black")
    
    lines_count = len(wrapped_text.split('\n'))
    y_part_s = y_s + (font_size_s * lines_count * 1.1)
    font_part_s = ImageFont.truetype(font_path, int(font_size_s * 0.9))
    for i in range(5, 0, -1):
        draw_arabic_text_pro(draw_s, (x_s - i, y_part_s + i), part_text, font_part_s, fill="#1A0000", stroke_width=4, stroke_fill="black")
    draw_arabic_text_pro(draw_s, (x_s, y_part_s), part_text, font_part_s, fill="#00FFFF", stroke_width=2, stroke_fill="black")
    
    path_short = os.path.join(dir_path, f"Thumbnail_Shorts_Part{part_num}.jpg")
    img_s_comp.convert("RGB").save(path_short, quality=95)
    return path_wide, path_short

# 10. نظام القالب والعلامة المائية
def add_decorative_frame(video_clip, is_shorts, color, font_path, part_num):
    w, h = int(video_clip.size[0]), int(video_clip.size[1])
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    safe_color = clean_pil_color(color)
    thickness = int(w * 0.02)
    draw.rectangle([0, 0, w, h], outline=safe_color, width=thickness)
    draw.rectangle([thickness, thickness, w-thickness, h-thickness], outline="white", width=int(thickness/4))
    frame_clip = ImageClip(np.array(img)).set_duration(video_clip.duration)
    return CompositeVideoClip([video_clip, frame_clip]).set_duration(video_clip.duration)

def add_watermark(video_clip, watermark_path, position):
    if not watermark_path or not os.path.exists(watermark_path):
        return video_clip
    try:
        wm = ImageClip(watermark_path)
        wm_w = int(video_clip.size[0] * 0.15)
        wm = wm.resize(width=wm_w).set_opacity(0.8)
        pad = int(video_clip.size[0] * 0.03)
        w, h = video_clip.size
        
        if position == "أعلى اليمين":
            x, y = w - wm.w - pad, pad
        elif position == "أعلى اليسار":
            x, y = pad, pad
        elif position == "أسفل اليمين":
            x, y = w - wm.w - pad, h - wm.h - pad
        else:
            x, y = pad, h - wm.h - pad
        
        wm = wm.set_position((x, y)).set_duration(video_clip.duration)
        return CompositeVideoClip([video_clip, wm]).set_duration(video_clip.duration)
    except Exception:
        return video_clip

def get_available_fonts():
    return [f for f in os.listdir(FONT_DIR) if f.endswith(('.ttf', '.otf', '.TTF', '.OTF'))]

def handle_font_upload(files):
    if files:
        for file in files:
            file_name = os.path.basename(file.name)
            shutil.copy(file.name, os.path.join(FONT_DIR, file_name))
    fonts = get_available_fonts()
    return gr.update(choices=fonts, value=fonts[0] if fonts else None)

# 11. المحرك الأساسي
def process_full_system(mode, input_text, api_key, num_parts, use_hook, use_audio_eng, custom_watermark, watermark_pos, use_frame, frame_color, custom_frame, selected_font, progress=gr.Progress()):
    overall_log = "🚀 بدأ تشغيل مصنع لقطة (النسخة الجراحية المدرعة V33.1)...\n\n"
    working_font = os.path.join(FONT_DIR, selected_font) if selected_font else get_best_font()
        
    links = [u.strip() for u in input_text.split('\n') if u.strip()] if mode == "رابط" else [input_text]
    if not links:
        return None, None, None, None, "", "", "⚠️ الرجاء إدخال رابط واحد على الأقل.", None
    
    send_telegram_message(f"⚙️ مصنع لقطة: جاري إنتاج {num_parts} أجزاء لكل رابط. إجمالي الروابط: {len(links)} 🚀")
    
    last_wide, last_short, last_thumb_wide, last_thumb_short = None, None, None, None
    last_title, last_desc, last_zip = "", "", None

    part_words = ["الأول", "الثاني", "الثالث", "الرابع", "الخامس"]

    for index, link_or_trend in enumerate(links):
        try:
            overall_log += f"\n=============================\n🔄 معالجة الفيديو ({index+1}/{len(links)})\n"
            for f in glob.glob(os.path.join(TEMP_DIR, "lakta_master*")):
                os.remove(f)
            
            ydl_opts = {
                'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
                'outtmpl': os.path.join(TEMP_DIR, 'lakta_master.%(ext)s'),
                'merge_output_format': 'mp4', 'quiet': True, 'no_warnings': True,
                'writesubtitles': True, 'writeautomaticsub': True, 'subtitleslangs': ['ar'],
            }
            if mode == "تريند":
                ydl_opts['extract_flat'] = False
                link_or_trend = f"ytsearch1:{link_or_trend}"
                
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(link_or_trend, download=True)
                v_title, v_desc = info.get('title', 'فيديو'), info.get('description', '')
                
                video_files = [f for f in glob.glob(os.path.join(TEMP_DIR, "lakta_master.*")) if f.endswith(('.mp4', '.webm', '.mkv'))]
                actual_filename = video_files[0] if video_files else None
                vtt_files = glob.glob(os.path.join(TEMP_DIR, "lakta_master*.vtt"))
                vtt_file = vtt_files[0] if vtt_files else None

            ai_title, ai_thumb_text, ai_desc, hook_text, overall_log = generate_masterpiece_seo(api_key, v_title, v_desc, overall_log)
            safe_folder_name = "".join([c for c in ai_title[:20] if c.isalpha() or c.isdigit() or c==' ']).strip()
            vid_dir = os.path.join(BASE_DIR, safe_folder_name if safe_folder_name else f"Lakta_Video_{index}")
            os.makedirs(vid_dir, exist_ok=True)

            semantic_cuts, overall_log = ai_semantic_editor(api_key, vtt_file, overall_log, num_parts)
            main_clip = VideoFileClip(actual_filename)
            w, h = int(main_clip.size[0]), int(main_clip.size[1])
            target_w = int(h * 9 / 16)
            target_w = target_w - (target_w % 2)
            x_center = w / 2
            
            subs_list = []
            if vtt_file:
                last_words = []
                for caption in webvtt.read(vtt_file):
                    text = caption.text.replace('\n', ' ').strip()
                    if not text:
                        continue
                    current_words = text.split()
                    
                    overlap_len = 0
                    max_possible_overlap = min(len(last_words), len(current_words))
                    for i in range(max_possible_overlap, 0, -1):
                        if last_words[-i:] == current_words[:i]:
                            overlap_len = i
                            break
                    new_words = current_words[overlap_len:]
                    last_words = current_words
                    if not new_words:
                        continue
                    
                    start_t = caption.start_in_seconds
                    end_t = caption.end_in_seconds
                    dur = end_t - start_t
                    total_chars = sum(len(w) for w in new_words)
                    
                    current_start = start_t
                    chunk_size = 4
                    for i in range(0, len(new_words), chunk_size):
                        chunk = new_words[i:i+chunk_size]
                        chunk_str = " ".join(chunk)
                        chunk_chars = sum(len(w) for w in chunk)
                        word_dur = (chunk_chars / max(total_chars, 1)) * dur
                        subs_list.append({'start': current_start, 'end': current_start + word_dur, 'text': chunk_str})
                        current_start += word_dur
            
            SPEED_FACTOR = 1.05
            
            for part_idx, cut in enumerate(semantic_cuts[:num_parts]):
                part_num = part_idx + 1
                part_word = part_words[part_num-1] if part_num-1 < len(part_words) else str(part_num)
                
                overall_log += f"🎬 جاري إنتاج الجزء {part_num} من {len(semantic_cuts[:num_parts])}...\n"
                
                part_title_str = f"{ai_title} - الجزء {part_word}"
                final_desc_dynamic = f"🔥 الجزء {part_word} من المقطع المثير!\n\n{ai_desc}\n\n🔗 رابط الحلقة كاملة هنا:\n{link_or_trend}\n\n#الجزء_{part_word} #لقطة #تريند"
                
                c_dur = min(cut[1], main_clip.duration) - cut[0]
                c = main_clip.subclip(cut[0], min(cut[1], main_clip.duration)).speedx(factor=SPEED_FACTOR)
                c = c.set_duration(c_dur / SPEED_FACTOR)
                
                if use_audio_eng and c.audio is not None:
                    try:
                        ambient_audio = make_ambient_audio(c.duration)
                        new_audio = CompositeAudioClip([c.audio.set_duration(c.duration), ambient_audio])
                        new_audio = new_audio.set_duration(c.duration)
                        c = c.set_audio(new_audio)
                    except Exception as e:
                        overall_log += f"⚠️ تحذير: خطأ في الهندسة الصوتية ({e})، تم تخطيها.\n"

                thumb_temp = os.path.join(TEMP_DIR, f"temp_thumb_{index}_{part_num}.png")
                c.save_frame(thumb_temp, t=min(1.0, c.duration - 0.5))
                thumb_wide, thumb_short = create_thumbnails(thumb_temp, ai_thumb_text, vid_dir, working_font, use_frame, frame_color, part_num)

                clip_subs_16_9, clip_subs_9_16 = [], []
                
                if use_hook and hook_text:
                    h_16 = create_subtitle_clip(hook_text, c.duration, w, h, working_font, False, y_pos_pct=0.12).set_start(0)
                    h_9 = create_subtitle_clip(hook_text, c.duration, target_w, h, working_font, True, y_pos_pct=0.15).set_start(0)
                    clip_subs_16_9.append(h_16)
                    clip_subs_9_16.append(h_9)

                for sub in subs_list:
                    if sub['start'] >= cut[0] and sub['start'] < min(cut[1], main_clip.duration):
                        sub_t_start = (sub['start'] - cut[0]) / SPEED_FACTOR
                        sub_dur = (min(sub['end'], min(cut[1], main_clip.duration)) - sub['start']) / SPEED_FACTOR
                        if sub_dur > 0.05:
                            txt_16_9 = create_subtitle_clip(sub['text'], sub_dur, w, h, working_font, False).set_start(sub_t_start)
                            clip_subs_16_9.append(txt_16_9)
                            txt_9_16 = create_subtitle_clip(sub['text'], sub_dur, target_w, h, working_font, True).set_start(sub_t_start)
                            clip_subs_9_16.append(txt_9_16)

                c_wide = CompositeVideoClip([c] + clip_subs_16_9) if clip_subs_16_9 else c
                c_wide = c_wide.set_duration(c.duration)
                if c_wide.audio is not None:
                    try:
                        c_wide = c_wide.audio_fadein(0.05).audio_fadeout(0.05)
                    except Exception:
                        pass
                
                if use_frame and custom_frame is None:
                    c_wide = add_decorative_frame(c_wide, False, frame_color, working_font, part_num)
                c_wide = add_watermark(c_wide, custom_watermark, watermark_pos)
                
                t_aud_w = os.path.join(TEMP_DIR, f"ta_{index}_{part_num}_w.m4a")
                path_wide = os.path.join(vid_dir, f"Wide_Part{part_num}.mp4")
                c_wide.write_videofile(path_wide, fps=30, codec="libx264", audio_codec="aac", temp_audiofile=t_aud_w, remove_temp=True, preset="ultrafast", threads=4, ffmpeg_params=["-crf", "28"], logger=None)
                
                c_short_base = c.crop(x1=x_center - target_w/2, y1=0, x2=x_center + target_w/2, y2=h)
                c_short = CompositeVideoClip([c_short_base] + clip_subs_9_16) if clip_subs_9_16 else c_short_base
                c_short = c_short.set_duration(c.duration)
                if c_short.audio is not None:
                    try:
                        c_short = c_short.audio_fadein(0.05).audio_fadeout(0.05)
                    except Exception:
                        pass
                
                if use_frame and custom_frame is None:
                    c_short = add_decorative_frame(c_short, True, frame_color, working_font, part_num)
                c_short = add_watermark(c_short, custom_watermark, watermark_pos)
                
                t_aud_s = os.path.join(TEMP_DIR, f"ta_{index}_{part_num}_s.m4a")
                path_short = os.path.join(vid_dir, f"Shorts_Part{part_num}.mp4")
                c_short.write_videofile(path_short, fps=30, codec="libx264", audio_codec="aac", temp_audiofile=t_aud_s, remove_temp=True, preset="ultrafast", threads=4, ffmpeg_params=["-crf", "28"], logger=None)
                
                with open(os.path.join(vid_dir, f"Title_Part{part_num}.txt"), "w", encoding="utf-8-sig") as f:
                    f.write(part_title_str)
                with open(os.path.join(vid_dir, f"Desc_Part{part_num}.txt"), "w", encoding="utf-8-sig") as f:
                    f.write(final_desc_dynamic)
                
                try:
                    c_wide.close()
                    c_short.close()
                    c.close()
                except Exception:
                    pass
                gc.collect()

                last_wide, last_short = path_wide, path_short
                last_thumb_wide, last_thumb_short = thumb_wide, thumb_short
                last_title, last_desc = part_title_str, final_desc_dynamic

            try:
                main_clip.close()
            except Exception:
                pass
            
            zip_path = shutil.make_archive(vid_dir, 'zip', vid_dir)
            last_zip = zip_path

            overall_log += "📲 جاري إرسال الملفات إلى تيليجرام...\n"
            send_telegram_message(f"✅ اكتمل الفيديو ({index+1}/{len(links)})!\n🎬 تم استخراج {num_parts} أجزاء.\n\nالعنوان: {ai_title}\n\nجاري إرسال الملف المضغوط (يحتوي كل الأجزاء)...")
            send_telegram_document(last_zip, caption=f"مشروع {ai_title} (عدد الأجزاء: {num_parts})")

        except Exception as e:
            overall_log += f"\n❌ حدث خطأ في الرابط {index+1}: {str(e)}\n"
            send_telegram_message(f"⚠️ فشل الرابط {index+1}.\nالخطأ: {str(e)}")
            continue

    overall_log += "\n🌟 اكتمل المصنع بالكامل!"
    send_telegram_message("🎉 مصنع لقطة: تمت معالجة جميع الروابط بنجاح. راجع الملفات المضغوطة أعلاه.")
    return last_wide, last_short, last_thumb_wide, last_thumb_short, last_title, last_desc, overall_log, last_zip

# 12. الواجهة التفاعلية
with gr.Blocks(theme=gr.themes.Soft()) as app:
    gr.Markdown("# 👑 مصنع لقطة V 33.1 (الأتمتة المدرعة والتسويق)")
    
    with gr.Row():
        api_key_input = gr.Textbox(label="مفتاح (Gemini API Key)", type="password", scale=3)
        btn_test_api = gr.Button("🔌 اختبار الاتصال", variant="secondary", scale=1)
    api_status = gr.Textbox(label="حالة الاتصال", interactive=False)
    btn_test_api.click(fn=test_api_connection, inputs=api_key_input, outputs=api_status)
    
    with gr.Row():
        with gr.Column():
            gr.Markdown("### ⚙️ إعداد المحتوى والتسويق")
            with gr.Tabs():
                with gr.TabItem("🔗 روابط يوتيوب (الأتمتة)"):
                    manual_url = gr.Textbox(label="الروابط (انسخ كل رابط في سطر منفصل)", lines=4)
                    num_parts = gr.Slider(minimum=1, maximum=3, step=1, value=1, label="عدد الأجزاء المستخرجة من كل فيديو (1-3)")
                    use_hook = gr.Checkbox(label="🪝 تفعيل الخطاف (سؤال مستفز أعلى الشاشة)", value=False)
                    use_audio_eng = gr.Checkbox(label="🎵 تفعيل الهندسة الصوتية (تخطي حقوق وحلال)", value=True)
                    btn_manual = gr.Button("🎬 تشغيل المصنع الآلي 🚀", variant="primary")
                    
                with gr.TabItem("🔥 صيد التريند"):
                    trend_keyword = gr.Textbox(label="كلمة مفتاحية")
                    btn_trend = gr.Button("اصطد التريند 🚀", variant="primary")
                    
        
