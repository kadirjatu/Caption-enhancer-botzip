from flask import Flask, render_template, request, jsonify, send_file
import os, json, threading, tempfile, subprocess, logging, urllib.parse, uuid, time, shutil, secrets

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2 GB

_bot = None
_fns = {}

# job_id -> {step, progress, eta_sec, done, error, started_at, result_file}
_jobs = {}

# ─── Credit System ────────────────────────────────────────
USERS_FILE      = 'users.json'
CREDITS_PER_AD  = 5          # credits per ad watched
AD_WATCH_SECS   = 30         # minimum seconds user must watch
AD_SMARTLINK    = os.getenv('AD_SMARTLINK', 'https://link.stonksmonkey.com/BfRJgT')
RESULTS_DIR     = 'results'
_ad_tokens      = {}         # token -> {user_id, started_at}
_users_lock     = threading.Lock()

def _load_users():
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def _save_users(data):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _get_credits(user_id):
    with _users_lock:
        return _load_users().get(str(user_id), {}).get('credits', 0)

def _add_credits(user_id, amount):
    with _users_lock:
        users = _load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {'credits': 0}
        users[uid]['credits'] = users[uid].get('credits', 0) + amount
        _save_users(users)
        return users[uid]['credits']

def _parse_user_from_form(req):
    try:
        raw = req.form.get('init_data', '') or req.args.get('init_data', '') or ''
        params = dict(urllib.parse.parse_qsl(raw))
        return json.loads(params.get('user', '{}'))
    except Exception:
        return {}
# ──────────────────────────────────────────────────────────

def init(bot_instance, functions):
    global _bot, _fns
    _bot = bot_instance
    _fns = functions
    os.makedirs(RESULTS_DIR, exist_ok=True)

def start_thread():
    t = threading.Thread(target=_run, daemon=True)
    t.start()

def _run():
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def _get_chat_id(req):
    try:
        raw = req.form.get('init_data', '') or ''
        params = dict(urllib.parse.parse_qsl(raw))
        user = json.loads(params.get('user', '{}'))
        return user.get('id')
    except Exception:
        return None

def _update_job(jid, step, progress, eta_sec):
    if jid in _jobs:
        _jobs[jid].update({'step': step, 'progress': progress, 'eta_sec': eta_sec})

def _finish_job(jid, error=None, output_path=None):
    if jid in _jobs:
        _jobs[jid]['done'] = True
        _jobs[jid]['error'] = error
        _jobs[jid]['progress'] = 100 if not error else _jobs[jid].get('progress', 0)
        # Save result file for mini app download
        if output_path and not error and os.path.exists(output_path):
            try:
                os.makedirs(RESULTS_DIR, exist_ok=True)
                ext = os.path.splitext(output_path)[1] or '.mp4'
                dest = os.path.join(RESULTS_DIR, jid + ext)
                shutil.copy2(output_path, dest)
                _jobs[jid]['result_file'] = dest
                _jobs[jid]['result_ext'] = ext
            except Exception as e:
                logging.error(f'Result copy error: {e}')

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

@app.route('/api/status/<job_id>')
def api_status(job_id):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    elapsed = int(time.time() - job['started_at'])
    result_url = f'/api/download/{job_id}' if job.get('result_file') else None
    return jsonify({
        'step':       job.get('step', 'Starting...'),
        'progress':   job.get('progress', 0),
        'eta_sec':    max(0, job.get('eta_sec', 0) - elapsed),
        'done':       job.get('done', False),
        'error':      job.get('error'),
        'result_url': result_url,
        'result_ext': job.get('result_ext', '.mp4'),
    })

@app.route('/api/download/<job_id>')
def api_download(job_id):
    job = _jobs.get(job_id)
    if not job or not job.get('done') or job.get('error'):
        return 'Not available', 404
    fpath = job.get('result_file')
    if not fpath or not os.path.exists(fpath):
        return 'File not found', 404
    ext = job.get('result_ext', '.mp4')
    name = 'output' + ext
    return send_file(fpath, as_attachment=True, download_name=name)

@app.route('/api/credits')
def api_credits():
    raw = request.args.get('init_data', '')
    params = dict(urllib.parse.parse_qsl(raw))
    try:
        user = json.loads(params.get('user', '{}'))
        user_id = user.get('id')
    except Exception:
        user_id = None
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not identified'}), 400
    return jsonify({'ok': True, 'credits': _get_credits(user_id),
                    'ad_url': AD_SMARTLINK, 'watch_secs': AD_WATCH_SECS,
                    'credits_per_ad': CREDITS_PER_AD})

@app.route('/api/ad/start', methods=['POST'])
def api_ad_start():
    user = _parse_user_from_form(request)
    user_id = user.get('id')
    if not user_id:
        return jsonify({'ok': False, 'error': 'Not identified'}), 400
    token = secrets.token_hex(20)
    _ad_tokens[token] = {'user_id': str(user_id), 'started_at': time.time()}
    return jsonify({'ok': True, 'token': token, 'ad_url': AD_SMARTLINK,
                    'watch_secs': AD_WATCH_SECS, 'credits_per_ad': CREDITS_PER_AD})

@app.route('/api/ad/complete', methods=['POST'])
def api_ad_complete():
    user = _parse_user_from_form(request)
    user_id = user.get('id')
    token  = request.form.get('token', '')
    if not user_id or not token:
        return jsonify({'ok': False, 'error': 'Invalid request'}), 400
    entry = _ad_tokens.get(token)
    if not entry:
        return jsonify({'ok': False, 'error': '❌ Invalid ya expired token!'}), 400
    if str(entry['user_id']) != str(user_id):
        return jsonify({'ok': False, 'error': '❌ User mismatch!'}), 403
    elapsed = time.time() - entry['started_at']
    if elapsed < AD_WATCH_SECS:
        remaining = int(AD_WATCH_SECS - elapsed)
        return jsonify({'ok': False, 'error': f'⏳ Abhi {remaining}s aur ruko!'}), 400
    del _ad_tokens[token]
    new_balance = _add_credits(user_id, CREDITS_PER_AD)
    return jsonify({'ok': True, 'credits_added': CREDITS_PER_AD, 'total_credits': new_balance})

@app.route('/api/subtitle', methods=['POST'])
def api_subtitle():
    chat_id = _get_chat_id(request)
    if not chat_id:
        return jsonify({'ok': False, 'error': 'User not identified'}), 400

    video_file = request.files.get('video')
    if not video_file:
        return jsonify({'ok': False, 'error': 'No video uploaded'}), 400

    language   = request.form.get('language', 'hi')
    lang_label = request.form.get('lang_label', 'Hindi')
    style_key  = request.form.get('style', 'netflix')
    translate  = request.form.get('translate', 'original')
    font_name  = request.form.get('font', 'Noto Sans Devanagari')
    color_hex  = request.form.get('color', '&H00FFFFFF')

    # ── Save file BEFORE thread starts (Flask request context closes after return) ──
    tmp_dir    = tempfile.mkdtemp()
    video_path = os.path.join(tmp_dir, 'input.mp4')
    try:
        video_file.save(video_path)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'ok': False, 'error': f'Save failed: {e}'}), 500

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    # Rough ETA: whisper ~3min/min-of-video; assume 2MB/s video → duration in s
    # Use file size as proxy: ~1MB per 5s of typical video
    est_duration = file_size_mb * 5
    eta_total = int(est_duration * 3 + 60)  # whisper + burn overhead

    jid = str(uuid.uuid4())
    _jobs[jid] = {'step': '⏳ Queued', 'progress': 0, 'eta_sec': eta_total,
                  'done': False, 'error': None, 'started_at': time.time()}

    def process():
        audio_path  = os.path.join(tmp_dir, 'audio.wav')
        ass_path    = os.path.join(tmp_dir, 'subs.ass')
        output_path = os.path.join(tmp_dir, 'output.mp4')
        try:
            # Step 1: Audio extract
            _update_job(jid, '🎙️ Audio extract ho rahi hai...', 10, eta_total)
            _bot.send_message(chat_id, '🎙️ Audio extract ho rahi hai... 10%')
            subprocess.run([
                'ffmpeg', '-y', '-i', video_path,
                '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_path
            ], check=True, capture_output=True)

            # Step 2: Transcribe
            _update_job(jid, '🧠 AI speech-to-text chal raha hai...', 20, eta_total - 15)
            _bot.send_message(chat_id, '🧠 Faster-Whisper (small) transcribing... 20%\n⏳ 1-4 min lag sakte hain')
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
                sd['words'] = [{'word': w.word, 'start': w.start, 'end': w.end} for w in seg.words] if need_word_ts and seg.words else []
                segments.append(sd)
                # Update progress gradually during transcription
                pct = min(55, 20 + len(segments))
                _update_job(jid, f'🧠 Transcribing... ({len(segments)} segments)', pct, max(30, eta_total - 30 - len(segments)*2))

            if not segments:
                _bot.send_message(chat_id, '❌ Video mein koi speech nahi mili.')
                _finish_job(jid, '❌ Koi speech nahi mili')
                return

            # Step 3: Gemini correct
            _update_job(jid, '🤖 Gemini text correct kar raha hai...', 60, 60)
            _bot.send_message(chat_id, '🤖 Gemini text correct kar raha hai... 60%')
            segments = _fns['gemini_correct'](segments, lang_label)

            # Step 4: Translate
            if translate != 'original':
                _update_job(jid, '🌐 Gemini translate kar raha hai...', 70, 45)
                _bot.send_message(chat_id, '🌐 Gemini translate kar raha hai... 70%')
                segments = _fns['gemini_translate'](segments, translate, lang_label)

            # Step 5: Emoji
            _update_job(jid, '😊 Emotions detect ho rahi hain...', 75, 35)
            _bot.send_message(chat_id, '😊 Emotions detect ho rahi hain... 75%')
            segments = _fns['gemini_emojis'](segments)

            # Step 6: Generate ASS + Burn
            words_data = None
            if need_word_ts:
                words_data = [{'word': w.get('word','').strip(), 'start': w.get('start',0), 'end': w.get('end',0)}
                              for seg in segments for w in seg.get('words', [])]
            ass_content = _fns['gen_ass'](segments, style_key=style_key, words_data=words_data, font_name=font_name, color=color_hex)
            with open(ass_path, 'w', encoding='utf-8') as f:
                f.write(ass_content)

            _update_job(jid, '🎨 Subtitles burn ho rahi hain...', 82, 25)
            _bot.send_message(chat_id, '🎨 Subtitles burn ho rahi hain... 82%')
            fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
            subprocess.run([
                'ffmpeg', '-y', '-i', video_path,
                '-vf', f'ass={ass_path}:fontsdir={fonts_dir}',
                '-c:a', 'copy', output_path
            ], check=True, capture_output=True)

            # Step 7: Send
            _update_job(jid, '📤 Bot pe bhej raha hoon...', 92, 10)
            _bot.send_message(chat_id, '📤 Video bhej raha hoon... 92% ✅')
            send_path = _fns['compress'](output_path)
            preview = ' '.join(s['text'] for s in segments)[:300]
            with open(send_path, 'rb') as f:
                video_bytes = f.read()
            _bot.send_video(chat_id, ('output_subtitled.mp4', video_bytes),
                caption=f'✅ <b>Subtitles Ready!</b>\n\n🎨 Style: <b>{style_key.title()}</b>\n🗣️ Lang: <b>{lang_label}</b>\n\n📝 <i>{preview}...</i>',
                parse_mode='HTML', supports_streaming=True)
            _finish_job(jid, output_path=send_path)

        except Exception as e:
            logging.error(f'WebApp subtitle error: {e}')
            _bot.send_message(chat_id, f'❌ Error: {e}')
            _finish_job(jid, error=str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    threading.Thread(target=process, daemon=True).start()
    return jsonify({'ok': True, 'job_id': jid, 'eta_sec': eta_total,
                    'message': 'Processing shuru! Bot pe result aayega.'})


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
        '720p30':  (1280, 720,  30, 22),
        '1080p30': (1920, 1080, 30, 20),
        '1080p60': (1920, 1080, 60, 20),
        '2k60':    (2560, 1440, 60, 18),
        '4k60':    (3840, 2160, 60, 16),
    }
    w, h, fps, crf = QUALITY_MAP.get(quality, (1920, 1080, 30, 20))

    # Save before thread
    tmp_dir    = tempfile.mkdtemp()
    input_path = os.path.join(tmp_dir, 'input.mp4')
    try:
        video_file.save(input_path)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'ok': False, 'error': f'Save failed: {e}'}), 500

    file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
    eta_total = int(file_size_mb * 4 + 30)

    jid = str(uuid.uuid4())
    _jobs[jid] = {'step': '⏳ Queued', 'progress': 0, 'eta_sec': eta_total,
                  'done': False, 'error': None, 'started_at': time.time()}

    def process():
        output_path = os.path.join(tmp_dir, 'enhanced.mp4')
        try:
            _update_job(jid, f'⚡ Video enhance ho rahi hai ({quality})...', 10, eta_total)
            _bot.send_message(chat_id, f'⚡ Video enhance ho rahi hai ({quality})... 10%\n⏳ Thoda wait karo')
            vf = f'scale={w}:{h}:flags=lanczos,fps={fps},unsharp=5:5:1.5:5:5:0,hqdn3d=1.5:1.5:6:6,eq=contrast=1.05:brightness=0.02:saturation=1.1'
            subprocess.run([
                'ffmpeg', '-y', '-i', input_path, '-vf', vf,
                '-c:v', 'libx264', '-preset', 'slow', '-crf', str(crf),
                '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', output_path
            ], check=True, capture_output=True)

            _update_job(jid, '📤 Bot pe bhej raha hoon...', 88, 10)
            _bot.send_message(chat_id, '📤 Enhanced video bhej raha hoon... 88%')
            send_path = _fns['compress'](output_path)
            size_mb = os.path.getsize(send_path) // (1024 * 1024)
            with open(send_path, 'rb') as f:
                video_bytes = f.read()
            _bot.send_video(chat_id, ('enhanced.mp4', video_bytes),
                caption=f'✅ <b>AI Enhancement Done!</b>\n\n🎯 Quality: <b>{quality}</b>\n📐 Resolution: <b>{w}x{h}</b>\n🎞️ FPS: <b>{fps}fps</b>\n📦 Size: <b>{size_mb}MB</b>',
                parse_mode='HTML', supports_streaming=True)
            _finish_job(jid, output_path=send_path)
        except Exception as e:
            logging.error(f'WebApp enhance-video error: {e}')
            _bot.send_message(chat_id, f'❌ Error: {e}')
            _finish_job(jid, error=str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    threading.Thread(target=process, daemon=True).start()
    return jsonify({'ok': True, 'job_id': jid, 'eta_sec': eta_total,
                    'message': 'Enhancement shuru! Bot pe result aayega.'})


@app.route('/api/enhance-image', methods=['POST'])
def api_enhance_image():
    chat_id = _get_chat_id(request)
    if not chat_id:
        return jsonify({'ok': False, 'error': 'User not identified'}), 400

    img_file = request.files.get('image')
    if not img_file:
        return jsonify({'ok': False, 'error': 'No image uploaded'}), 400

    mode = request.form.get('mode', '4x')

    # Save before thread
    tmp_dir  = tempfile.mkdtemp()
    img_path = os.path.join(tmp_dir, 'input.jpg')
    try:
        img_file.save(img_path)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'ok': False, 'error': f'Save failed: {e}'}), 500

    jid = str(uuid.uuid4())
    _jobs[jid] = {'step': '⏳ Queued', 'progress': 0, 'eta_sec': 120,
                  'done': False, 'error': None, 'started_at': time.time()}

    def process():
        import requests as req_lib
        try:
            _update_job(jid, '⬆️ Image upload ho rahi hai...', 10, 110)
            _bot.send_message(chat_id, f'🖼️ Image enhance ho rahi hai ({mode})... 10%')
            with open(img_path, 'rb') as f:
                up = req_lib.post('https://tmpfiles.org/api/v1/upload', files={'file': f}, timeout=60)
            raw_url = up.json()['data']['url']
            dl_url  = raw_url.replace('tmpfiles.org/', 'tmpfiles.org/dl/')

            REPLICATE_TOKEN = os.getenv('REPLICATE_API_TOKEN', '')
            if not REPLICATE_TOKEN:
                _bot.send_message(chat_id, '❌ REPLICATE_API_TOKEN set nahi hai.')
                _finish_job(jid, 'REPLICATE_API_TOKEN missing')
                return

            scale = 4 if '4x' in mode else 2
            face  = 'face' in mode

            _update_job(jid, '🤖 AI model se enhance ho raha hai...', 30, 90)
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
                _finish_job(jid, 'Replicate API error')
                return

            _bot.send_message(chat_id, '⚙️ AI enhance kar raha hai... 40%\n⏳ 1-2 min')
            for tick in range(60):
                time.sleep(5)
                pct = min(85, 40 + tick * 2)
                _update_job(jid, f'✨ AI processing... ({tick*5}s)', pct, max(5, 90 - tick*5))
                status = req_lib.get(
                    f'https://api.replicate.com/v1/predictions/{pred_id}',
                    headers={'Authorization': f'Token {REPLICATE_TOKEN}'}, timeout=15
                ).json()
                if status.get('status') == 'succeeded':
                    out_url  = status['output']
                    img_data = req_lib.get(out_url, timeout=60).content
                    out_path = os.path.join(tmp_dir, 'enhanced.jpg')
                    with open(out_path, 'wb') as f:
                        f.write(img_data)
                    _update_job(jid, '📤 Bot pe bhej raha hoon...', 95, 5)
                    _bot.send_message(chat_id, '📤 Enhanced image bhej raha hoon... 95%')
                    with open(out_path, 'rb') as f:
                        _bot.send_photo(chat_id, f,
                            caption=f'✅ <b>Image Enhanced!</b>\n🔍 Mode: <b>{mode}</b>\n✨ Real-ESRGAN done!',
                            parse_mode='HTML')
                    _finish_job(jid, output_path=out_path)
                    return
                elif status.get('status') == 'failed':
                    _bot.send_message(chat_id, '❌ Enhancement failed.')
                    _finish_job(jid, error='Enhancement failed')
                    return

            _bot.send_message(chat_id, '⏰ Timeout ho gaya, baad mein try karo.')
            _finish_job(jid, error='Timeout')
        except Exception as e:
            logging.error(f'WebApp enhance-image error: {e}')
            _bot.send_message(chat_id, f'❌ Error: {e}')
            _finish_job(jid, error=str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    threading.Thread(target=process, daemon=True).start()
    return jsonify({'ok': True, 'job_id': jid, 'eta_sec': 120,
                    'message': 'Enhancement shuru! Bot pe result aayega.'})
