from flask import Flask, render_template, request, jsonify
import os, json, threading, tempfile, subprocess, logging, urllib.parse

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2 GB

_bot = None
_fns = {}

def init(bot_instance, functions):
    global _bot, _fns
    _bot = bot_instance
    _fns = functions

def start_thread():
    t = threading.Thread(target=_run, daemon=True)
    t.start()

def _run():
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def _get_chat_id(req):
    try:
        raw = req.form.get('init_data', '') or req.json.get('init_data', '')
        params = dict(urllib.parse.parse_qsl(raw))
        user = json.loads(params.get('user', '{}'))
        return user.get('id')
    except Exception:
        return None

# ─── Routes ──────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip().lower()
    if len(q) < 2:
        return jsonify([])
    try:
        with open('movies.json', encoding='utf-8') as f:
            movies = json.load(f)
        hits = [m for m in movies if q in m.get('name', '').lower()][:12]
        return jsonify(hits)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/subtitle', methods=['POST'])
def api_subtitle():
    chat_id = _get_chat_id(request)
    if not chat_id:
        return jsonify({'ok': False, 'error': 'User not identified'}), 400

    video_file = request.files.get('video')
    if not video_file:
        return jsonify({'ok': False, 'error': 'No video uploaded'}), 400

    language    = request.form.get('language', 'hi')
    lang_label  = request.form.get('lang_label', 'Hindi')
    style_key   = request.form.get('style', 'netflix')
    translate   = request.form.get('translate', 'original')
    font_name   = request.form.get('font', 'Noto Sans Devanagari')
    color_hex   = request.form.get('color', '&H00FFFFFF')

    def process():
        tmp_dir = tempfile.mkdtemp()
        try:
            video_path  = os.path.join(tmp_dir, 'input.mp4')
            audio_path  = os.path.join(tmp_dir, 'audio.wav')
            ass_path    = os.path.join(tmp_dir, 'subs.ass')
            output_path = os.path.join(tmp_dir, 'output.mp4')

            video_file.save(video_path)

            _bot.send_message(chat_id, '⬆️ Video mili! Audio extract ho rahi hai... 30%')

            subprocess.run([
                'ffmpeg', '-y', '-i', video_path,
                '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_path
            ], check=True, capture_output=True)

            _bot.send_message(chat_id, f'🧠 Faster-Whisper (small) transcribing... 45%\n⏳ 1-4 min lag sakte hain')

            from faster_whisper import WhisperModel
            fw_model = WhisperModel('small', device='cpu', compute_type='int8')
            need_word_ts = style_key in ('shorts', 'reels', 'gaming')
            fw_segs, _ = fw_model.transcribe(
                audio_path, beam_size=5, best_of=5, temperature=0,
                condition_on_previous_text=False, task='transcribe',
                word_timestamps=need_word_ts,
                language=language if language else None,
            )
            segments = []
            for i, seg in enumerate(fw_segs):
                sd = {'id': i, 'start': seg.start, 'end': seg.end, 'text': seg.text}
                if need_word_ts and seg.words:
                    sd['words'] = [{'word': w.word, 'start': w.start, 'end': w.end} for w in seg.words]
                else:
                    sd['words'] = []
                segments.append(sd)

            if not segments:
                _bot.send_message(chat_id, '❌ Video mein koi speech nahi mili.')
                return

            _bot.send_message(chat_id, '🤖 Gemini text correct kar raha hai... 60%')
            segments = _fns['gemini_correct'](segments, lang_label)

            if translate != 'original':
                _bot.send_message(chat_id, '🌐 Gemini translate kar raha hai... 70%')
                segments = _fns['gemini_translate'](segments, translate, lang_label)

            _bot.send_message(chat_id, '😊 Emotions detect ho rahi hain... 80%')
            segments = _fns['gemini_emojis'](segments)

            words_data = None
            if need_word_ts:
                words_data = []
                for seg in segments:
                    for w in seg.get('words', []):
                        words_data.append({'word': w.get('word','').strip(), 'start': w.get('start',0), 'end': w.get('end',0)})

            ass_content = _fns['gen_ass'](segments, style_key=style_key, words_data=words_data, font_name=font_name, color=color_hex)
            with open(ass_path, 'w', encoding='utf-8') as f:
                f.write(ass_content)

            _bot.send_message(chat_id, '🎨 Subtitles burn ho rahi hain... 90%')
            fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
            subprocess.run([
                'ffmpeg', '-y', '-i', video_path,
                '-vf', f'ass={ass_path}:fontsdir={fonts_dir}',
                '-c:a', 'copy', output_path
            ], check=True, capture_output=True)

            _bot.send_message(chat_id, '📤 Video bhej raha hoon... 100% ✅')
            send_path = _fns['compress'](output_path)
            preview = ' '.join(s['text'] for s in segments)[:300]
            with open(send_path, 'rb') as vf:
                _bot.send_video(chat_id, vf,
                    caption=f'✅ <b>Subtitles Ready!</b>\n\n🎨 Style: <b>{style_key.title()}</b>\n🗣️ Lang: <b>{lang_label}</b>\n\n📝 <i>{preview}...</i>',
                    parse_mode='HTML', supports_streaming=True)
        except Exception as e:
            logging.error(f'WebApp subtitle error: {e}')
            _bot.send_message(chat_id, f'❌ Error: {e}')
        finally:
            import shutil; shutil.rmtree(tmp_dir, ignore_errors=True)

    threading.Thread(target=process, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Processing shuru! Bot pe result aayega.'})


@app.route('/api/enhance-video', methods=['POST'])
def api_enhance_video():
    chat_id = _get_chat_id(request)
    if not chat_id:
        return jsonify({'ok': False, 'error': 'User not identified'}), 400

    video_file = request.files.get('video')
    if not video_file:
        return jsonify({'ok': False, 'error': 'No video uploaded'}), 400

    quality = request.form.get('quality', '1080p30')
    QUALITY_MAP = {
        '720p30':  (1280, 720,  30,  22),
        '1080p30': (1920, 1080, 30,  20),
        '1080p60': (1920, 1080, 60,  20),
        '2k60':    (2560, 1440, 60,  18),
        '4k60':    (3840, 2160, 60,  16),
    }
    w, h, fps, crf = QUALITY_MAP.get(quality, (1920, 1080, 30, 20))

    def process():
        tmp_dir = tempfile.mkdtemp()
        try:
            input_path  = os.path.join(tmp_dir, 'input.mp4')
            output_path = os.path.join(tmp_dir, 'enhanced.mp4')
            video_file.save(input_path)

            _bot.send_message(chat_id, f'🎬 Video enhance ho rahi hai ({quality})... 40%\n⏳ Thoda wait karo')
            vf = f'scale={w}:{h}:flags=lanczos,fps={fps},unsharp=5:5:1.5:5:5:0,hqdn3d=1.5:1.5:6:6,eq=contrast=1.05:brightness=0.02:saturation=1.1'
            subprocess.run([
                'ffmpeg', '-y', '-i', input_path,
                '-vf', vf,
                '-c:v', 'libx264', '-preset', 'slow', '-crf', str(crf),
                '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', output_path
            ], check=True, capture_output=True)

            _bot.send_message(chat_id, '📤 Enhanced video bhej raha hoon... 90%')
            send_path = _fns['compress'](output_path)
            size_mb = os.path.getsize(send_path) // (1024 * 1024)
            with open(send_path, 'rb') as vf_out:
                _bot.send_video(chat_id, vf_out,
                    caption=f'✅ <b>AI Enhancement Done!</b>\n\n🎯 Quality: <b>{quality}</b>\n📐 Resolution: <b>{w}x{h}</b>\n🎞️ FPS: <b>{fps}fps</b>\n📦 Size: <b>{size_mb}MB</b>',
                    parse_mode='HTML', supports_streaming=True)
        except Exception as e:
            logging.error(f'WebApp enhance-video error: {e}')
            _bot.send_message(chat_id, f'❌ Error: {e}')
        finally:
            import shutil; shutil.rmtree(tmp_dir, ignore_errors=True)

    threading.Thread(target=process, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Enhancement shuru! Bot pe result aayega.'})


@app.route('/api/enhance-image', methods=['POST'])
def api_enhance_image():
    chat_id = _get_chat_id(request)
    if not chat_id:
        return jsonify({'ok': False, 'error': 'User not identified'}), 400

    img_file = request.files.get('image')
    if not img_file:
        return jsonify({'ok': False, 'error': 'No image uploaded'}), 400

    mode = request.form.get('mode', '4x')

    def process():
        import requests as req_lib
        tmp_dir = tempfile.mkdtemp()
        try:
            img_path = os.path.join(tmp_dir, 'input.jpg')
            img_file.save(img_path)

            _bot.send_message(chat_id, f'🖼️ Image enhance ho rahi hai ({mode})... 30%')

            with open(img_path, 'rb') as f:
                up = req_lib.post('https://tmpfiles.org/api/v1/upload', files={'file': f}, timeout=60)
            raw_url = up.json()['data']['url']
            dl_url  = raw_url.replace('tmpfiles.org/', 'tmpfiles.org/dl/')

            REPLICATE_TOKEN = os.getenv('REPLICATE_API_TOKEN', '')
            if not REPLICATE_TOKEN:
                _bot.send_message(chat_id, '❌ REPLICATE_API_TOKEN set nahi hai.')
                return

            scale = 4 if '4x' in mode else 2
            face  = 'face' in mode

            pred = req_lib.post(
                'https://api.replicate.com/v1/predictions',
                headers={'Authorization': f'Token {REPLICATE_TOKEN}', 'Content-Type': 'application/json'},
                json={'version': '42fed1c4974146d4d2414e2be2c5277c7fcf05fcc3a73abf41610695738c1d7b',
                      'input': {'image': dl_url, 'scale': scale, 'face_enhance': face}},
                timeout=30
            ).json()

            pred_id = pred.get('id')
            if not pred_id:
                _bot.send_message(chat_id, '❌ Replicate API error.')
                return

            _bot.send_message(chat_id, '⚙️ AI enhance kar raha hai... 60%\n⏳ 1-2 min')
            import time
            for _ in range(60):
                time.sleep(5)
                status = req_lib.get(
                    f'https://api.replicate.com/v1/predictions/{pred_id}',
                    headers={'Authorization': f'Token {REPLICATE_TOKEN}'},
                    timeout=15
                ).json()
                if status.get('status') == 'succeeded':
                    out_url = status['output']
                    img_data = req_lib.get(out_url, timeout=60).content
                    out_path = os.path.join(tmp_dir, 'enhanced.jpg')
                    with open(out_path, 'wb') as f:
                        f.write(img_data)
                    _bot.send_message(chat_id, '📤 Enhanced image bhej raha hoon... 100%')
                    with open(out_path, 'rb') as f:
                        _bot.send_photo(chat_id, f,
                            caption=f'✅ <b>Image Enhanced!</b>\n🔍 Mode: <b>{mode}</b>\n✨ AI Real-ESRGAN upscaling done!',
                            parse_mode='HTML')
                    return
                elif status.get('status') == 'failed':
                    _bot.send_message(chat_id, '❌ Enhancement failed.')
                    return

            _bot.send_message(chat_id, '⏰ Timeout ho gaya, baad mein try karo.')
        except Exception as e:
            logging.error(f'WebApp enhance-image error: {e}')
            _bot.send_message(chat_id, f'❌ Error: {e}')
        finally:
            import shutil; shutil.rmtree(tmp_dir, ignore_errors=True)

    threading.Thread(target=process, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Enhancement shuru! Bot pe result aayega.'})
