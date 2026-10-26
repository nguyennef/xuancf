import os
import io
import sys
import socket
import time
import threading
from concurrent.futures import ThreadPoolExecutor
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from flask import Flask, render_template, request, jsonify, send_file, session, Response, stream_with_context
import requests
from flask_socketio import SocketIO, emit
import qrcode

import database
import youtube

app = Flask(__name__)
app.config['SECRET_KEY'] = 'xuancf_karaoke_secret_key_2026'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # Max 50MB upload limit to prevent memory exhaustion
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 7 # 7 days

# Initialize SocketIO with threading mode for high Windows compatibility
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize SQLite database
database.init_db()

# Authentication passwords (Default: MrHT@123)
ADMIN_PIN = os.environ.get("ADMIN_PIN", "MrHT@123")
SPEAKER_PASSWORD = os.environ.get("SPEAKER_PASSWORD", "MrHT@123")

# Anti-Brute-Force Rate Limiting for Login / PIN verification
_auth_failed_attempts = {}
_auth_lock = threading.Lock()

def record_auth_failure(ip):
    now = time.time()
    with _auth_lock:
        attempts, first_time = _auth_failed_attempts.get(ip, (0, now))
        if now - first_time > 300:
            attempts = 0
            first_time = now
        attempts += 1
        _auth_failed_attempts[ip] = (attempts, first_time)

def check_auth_rate_limit(ip):
    now = time.time()
    with _auth_lock:
        if ip in _auth_failed_attempts:
            attempts, first_time = _auth_failed_attempts[ip]
            if now - first_time > 300:
                del _auth_failed_attempts[ip]
                return True, 0
            if attempts >= 10:
                lockout = int(300 - (now - first_time))
                return False, max(1, lockout)
            if attempts >= 5:
                lockout = 30
                if now - first_time < lockout:
                    return False, max(1, int(lockout - (now - first_time)))
        return True, 0

def clear_auth_failures(ip):
    with _auth_lock:
        _auth_failed_attempts.pop(ip, None)

# Thread pool executor for pre-caching audio streams (1 worker to avoid GIL contention with HTTP requests)
_warm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio_warm")
_warming_ids = set()
_warming_lock = threading.Lock()

def startup_prewarm():
    """Background task running on server boot to pre-cache audio streams so first playback is instant"""
    def _bg():
        time.sleep(0.8)
        try:
            q = database.get_active_queue()
            to_cache = []
            if q.get('current') and q['current'].get('youtube_id'):
                to_cache.append(q['current']['youtube_id'])
            if q.get('next_up') and q['next_up'].get('youtube_id'):
                to_cache.append(q['next_up']['youtube_id'])

            # If no current song, pre-warm the active playlist's top tracks
            if not to_cache:
                active_pl = database.get_active_playlist()
                if active_pl:
                    songs = database.get_playlist_songs(active_pl['id'])
                    for s in songs[:2]:
                        if s.get('youtube_id'):
                            to_cache.append(s['youtube_id'])

            for yid in to_cache:
                warm_cache_async(yid)
        except Exception as e:
            print(f"[Prewarm] Exception during boot cache warm: {e}")

    threading.Thread(target=_bg, daemon=True, name="boot_prewarm").start()

# Launch boot pre-warming
startup_prewarm()

# Order rate limiting by IP & Queue overflow protection
_order_rate_limits = {}
_order_rate_lock = threading.Lock()
MAX_QUEUE_LIMIT = 50

def check_order_rate_limit(ip):
    now = time.time()
    with _order_rate_lock:
        # Periodic cleanup of old records
        for k in list(_order_rate_limits.keys()):
            if now - _order_rate_limits[k] > 60:
                del _order_rate_limits[k]
        last_time = _order_rate_limits.get(ip, 0)
        if now - last_time < 2.0:
            wait_s = int(2.0 - (now - last_time)) + 1
            return False, wait_s
        _order_rate_limits[ip] = now
        return True, 0

def check_is_admin(payload=None):
    """Secure verification of admin privileges: checks session, HTTP header, or matching PIN/Password"""
    if session.get('is_admin'):
        return True
    req_pin = request.headers.get('X-Admin-PIN')
    if req_pin and req_pin == ADMIN_PIN:
        return True
    if payload and isinstance(payload, dict):
        p = str(payload.get('pin') or payload.get('admin_pin') or payload.get('password') or '').strip()
        if p and p == ADMIN_PIN:
            return True
    if request.form:
        f_p = str(request.form.get('admin_pin') or request.form.get('pin') or request.form.get('password') or '').strip()
        if f_p and f_p == ADMIN_PIN:
            return True
    return False

def check_is_speaker(payload=None):
    """Secure verification of speaker privileges: checks session, HTTP header, or matching password"""
    if session.get('is_speaker') or session.get('is_admin'):
        return True
    req_pass = request.headers.get('X-Speaker-Pass') or request.headers.get('X-Admin-PIN')
    if req_pass and (req_pass == SPEAKER_PASSWORD or req_pass == ADMIN_PIN):
        return True
    if payload and isinstance(payload, dict):
        p = str(payload.get('password') or payload.get('pin') or payload.get('speaker_pass') or '').strip()
        if p and (p == SPEAKER_PASSWORD or p == ADMIN_PIN):
            return True
    if request.form:
        f_p = str(request.form.get('password') or request.form.get('speaker_pass') or request.form.get('pin') or '').strip()
        if f_p and (f_p == SPEAKER_PASSWORD or f_p == ADMIN_PIN):
            return True
    return False

def get_local_ip():
    """Detect LAN IP so mobile devices on the same WiFi can connect"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def warm_cache_async(youtube_id):
    """Pre-extract direct audio stream URL in background using bounded single-worker thread pool"""
    if not youtube_id:
        return
    yid = str(youtube_id).strip()
    if yid.startswith('http') or yid.startswith('/static/') or yid.endswith(('.mp3', '.m4a', '.wav')):
        return
    if youtube.is_audio_cached(yid):
        return
    with _warming_lock:
        if yid in _warming_ids:
            return
        _warming_ids.add(yid)
    def _worker():
        try:
            youtube.get_direct_audio_url(yid)
        except Exception:
            pass
        finally:
            with _warming_lock:
                _warming_ids.discard(yid)
    try:
        _warm_executor.submit(_worker)
    except Exception:
        with _warming_lock:
            _warming_ids.discard(yid)

# ----------------------------------------------------
# PAGE ROUTES
# ----------------------------------------------------

@app.route('/')
@app.route('/user')
def index():
    local_ip = get_local_ip()
    return render_template('index.html', local_ip=local_ip)

@app.route('/speaker')
def speaker():
    local_ip = get_local_ip()
    # Note: Do not auto-set session['is_speaker']. Protected by password MrHT@123.
    return render_template('speaker.html', local_ip=local_ip)

@app.route('/admin')
def admin():
    local_ip = get_local_ip()
    # Note: ADMIN_PIN is never rendered into template for security
    return render_template('admin.html', local_ip=local_ip)

@app.route('/api/admin/verify_pin', methods=['POST'])
def api_admin_verify_pin():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    allowed, wait_s = check_auth_rate_limit(client_ip)
    if not allowed:
        return jsonify({'success': False, 'message': f'Nhập sai quá nhiều lần! Vui lòng thử lại sau {wait_s}s.'}), 429

    data = request.get_json(silent=True) or {}
    pin = str(data.get('pin') or data.get('password') or '').strip()
    if pin == ADMIN_PIN:
        clear_auth_failures(client_ip)
        session['is_admin'] = True
        return jsonify({'success': True, 'message': 'Xác thực Admin thành công!'})
    
    record_auth_failure(client_ip)
    return jsonify({'success': False, 'message': 'Mật khẩu quản trị không chính xác!'}), 401

@app.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    session.pop('is_admin', None)
    return jsonify({'success': True, 'message': 'Đã đăng xuất Admin'})

@app.route('/api/speaker/verify_pass', methods=['POST'])
def api_speaker_verify_pass():
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    allowed, wait_s = check_auth_rate_limit(client_ip)
    if not allowed:
        return jsonify({'success': False, 'message': f'Nhập sai quá nhiều lần! Vui lòng thử lại sau {wait_s}s.'}), 429

    data = request.get_json(silent=True) or {}
    pwd = str(data.get('password') or data.get('pin') or '').strip()
    if pwd == SPEAKER_PASSWORD or pwd == ADMIN_PIN:
        clear_auth_failures(client_ip)
        session['is_speaker'] = True
        return jsonify({'success': True, 'message': 'Xác thực màn hình Loa thành công!'})

    record_auth_failure(client_ip)
    return jsonify({'success': False, 'message': 'Mật khẩu Loa phát không chính xác!'}), 401

@app.route('/api/speaker/logout', methods=['POST'])
def api_speaker_logout():
    session.pop('is_speaker', None)
    return jsonify({'success': True, 'message': 'Đã khóa màn hình Loa'})

# ----------------------------------------------------
# QR CODE & SYSTEM API
# ----------------------------------------------------

@app.route('/api/qrcode')
def get_qrcode():
    # Use request.host or local IP
    host = request.args.get('url')
    if not host:
        local_ip = get_local_ip()
        port = request.environ.get('SERVER_PORT', '5000')
        host = f"http://{local_ip}:{port}/"

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(host)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1e1b4b", back_color="white")
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/api/ip')
def api_ip():
    local_ip = get_local_ip()
    port = request.environ.get('SERVER_PORT', '5000')
    base_url = f"http://{local_ip}:{port}"
    res = {
        'local_ip': local_ip,
        'user_url': f"{base_url}/"
    }
    if check_is_admin():
        res['speaker_url'] = f"{base_url}/speaker"
        res['admin_url'] = f"{base_url}/admin"
    return jsonify(res)

# ----------------------------------------------------
# QUEUE & SONG APIS
# ----------------------------------------------------

@app.route('/api/queue', methods=['GET'])
def api_get_queue():
    queue_data = database.get_active_queue()
    mode = database.get_playback_mode()
    active_pl = database.get_active_playlist()
    return jsonify({
        'success': True,
        'queue': queue_data,
        'playback_mode': mode,
        'active_playlist': active_pl,
        'speaker_state': _speaker_state
    })

@app.route('/api/user/song', methods=['GET'])
def api_get_user_song():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'Thiếu user_id'}), 400
    song = database.get_user_song(user_id)
    return jsonify({'success': True, 'song': song})

@app.route('/api/search', methods=['GET'])
def api_search():
    q = request.args.get('q', '').strip()[:150]
    if not q:
        return jsonify({'success': True, 'results': []})
    results = youtube.search_youtube(q, limit=8)
    return jsonify({'success': True, 'results': results})

def parse_duration_seconds(dur_str):
    """Converts '03:45', '01:15:30', or '180' to seconds integer. Returns 0 if invalid."""
    if not dur_str or dur_str in ('--:--', 'Audio MP3', 'MP3'):
        return 0
    try:
        parts = str(dur_str).strip().split(':')
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1:
            return int(parts[0])
    except Exception:
        pass
    return 0

@app.route('/api/audio_proxy/<path:youtube_id>', methods=['GET', 'HEAD'])
def api_audio_proxy(youtube_id):
    """Local streaming proxy for audio chunks.
    Solves YouTube CDN IP binding mismatches and cross-device playback blocks,
    supports HTTP 206 Partial Content (Range requests) for instant seeking and fast buffer filling."""
    yid = str(youtube_id).strip()
    if not yid:
        return jsonify({'error': 'Missing video id'}), 400
    
    # Handle local static files directly with strict path traversal protection
    if yid.startswith('/static/') or yid.startswith('static/'):
        static_root = os.path.abspath(os.path.join(os.path.dirname(__file__), 'static'))
        local_path = os.path.abspath(os.path.join(os.path.dirname(__file__), yid.lstrip('/')))
        if local_path.startswith(static_root) and os.path.isfile(local_path):
            return send_file(local_path)
        return jsonify({'error': 'Invalid local audio file'}), 404
            
    audio_info = youtube.get_direct_audio_url(yid)
    if not audio_info or not audio_info.get('url'):
        return jsonify({'error': 'Audio stream not found'}), 404
        
    upstream_url = audio_info['url']
    req_headers = {'User-Agent': 'Mozilla/5.0'}
    range_header = request.headers.get('Range')
    if range_header:
        req_headers['Range'] = range_header

    try:
        r = requests.get(upstream_url, headers=req_headers, stream=True, timeout=10)
        
        # If upstream gave 403 or 410 (expired CDN signature/token), refresh once
        if r.status_code in (403, 410):
            fresh_info = youtube.get_direct_audio_url(yid, force_fresh=True)
            if fresh_info and fresh_info.get('url'):
                upstream_url = fresh_info['url']
                r = requests.get(upstream_url, headers=req_headers, stream=True, timeout=10)
                
        resp_headers = {}
        for h in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Cache-Control']:
            if h in r.headers:
                resp_headers[h] = r.headers[h]
        if 'Accept-Ranges' not in resp_headers:
            resp_headers['Accept-Ranges'] = 'bytes'

        if request.method == 'HEAD':
            return Response(status=r.status_code, headers=resp_headers)

        def generate():
            try:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            except Exception:
                pass
            finally:
                r.close()

        return Response(stream_with_context(generate()), status=r.status_code, headers=resp_headers)
    except Exception as e:
        return jsonify({'error': str(e)}), 502

@app.route('/api/audio_stream/<path:youtube_id>', methods=['GET'])
def api_audio_stream(youtube_id):
    force_fresh = bool(request.args.get('fresh'))
    audio_info = youtube.get_direct_audio_url(youtube_id, force_fresh=force_fresh)
    if audio_info and audio_info.get('url'):
        res = dict(audio_info)
        res['direct_url'] = audio_info['url']
        res['proxy_url'] = f"/api/audio_proxy/{youtube_id}"
        # Point url to proxy_url for instant local streaming with zero IP conflicts
        res['url'] = f"/api/audio_proxy/{youtube_id}"
        return jsonify({'success': True, 'audio': res})
    return jsonify({'success': False, 'message': 'Không thể lấy stream audio từ video này'}), 404

@app.route('/api/queue/add', methods=['POST'])
def api_queue_add():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400

    youtube_id = (data.get('youtube_id') or '').strip()[:255]
    title = (data.get('title') or '').strip()[:150]
    thumbnail = (data.get('thumbnail') or '').strip()[:500]
    duration = (data.get('duration') or '--:--').strip()[:15]
    channel = (data.get('channel') or 'YouTube').strip()[:80]
    user_name = (data.get('user_name') or '').strip()[:50]
    user_id = (data.get('user_id') or '').strip()[:64]
    
    is_admin = check_is_admin(data)
    play_now_flag = bool(is_admin and data.get('play_now'))

    if not youtube_id or not title:
        return jsonify({'success': False, 'message': 'Thiếu thông tin bài hát!'}), 400

    if not is_admin:
        # Check livestream (anti-infinite queue hang)
        if duration.upper() in ('LIVE', 'TRỰC TIẾP'):
            return jsonify({'success': False, 'message': 'Không thể chọn video phát trực tiếp (Livestream). Vui lòng chọn bài hát thông thường!'}), 400

        # Check maximum duration (10 minutes = 600s)
        dur_secs = parse_duration_seconds(duration)
        if dur_secs > 600:
            return jsonify({'success': False, 'message': f'Thời lượng bài hát quá dài ({duration}). Quán giới hạn tối đa 10 phút/bài để mọi người đều được nghe nhạc!'}), 400

        # Check IP rate limiting (anti-spam bot)
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
        allowed, wait_s = check_order_rate_limit(client_ip)
        if not allowed:
            return jsonify({'success': False, 'message': f'Thao tác quá nhanh! Vui lòng chờ {wait_s}s trước khi gửi tiếp.'}), 429

        # Check queue capacity (anti-memory exhaustion)
        active_q = database.get_active_queue()
        if len(active_q.get('waiting', [])) >= MAX_QUEUE_LIMIT:
            return jsonify({'success': False, 'message': f'Hàng đợi hiện đã đầy (tối đa {MAX_QUEUE_LIMIT} bài). Vui lòng đợi phát bớt bài trước khi chọn thêm!'}), 429

    if not user_name:
        user_name = "Khách hàng"
    if not user_id:
        user_id = "guest_" + os.urandom(4).hex()

    success, message, new_song = database.add_to_queue(
        youtube_id=youtube_id,
        title=title,
        thumbnail=thumbnail,
        duration=duration,
        channel=channel,
        user_name=user_name,
        user_id=user_id,
        is_admin=is_admin
    )

    if success:
        global _last_speaker_next_time
        warm_cache_async(youtube_id)
        # Broadcast queue update to all connected screens
        active_queue = database.get_active_queue()
        mode = active_queue['playback_mode']

        if play_now_flag and new_song:
            with _speaker_next_lock:
                _last_speaker_next_time = time.time()
                ok_p, msg_p, played_s = database.play_specific_song(new_song['id'], is_admin=True)
            if ok_p and played_s:
                new_song = played_s
                socketio.emit('play_song', {'song': played_s})
                active_queue = database.get_active_queue()
        elif mode == 'auto_priority' and (not active_queue['current'] or active_queue['current']['user_id'] in ('SYSTEM_DEFAULT', 'admin_play')):
            # If in 'auto_priority' mode and currently playing background playlist song (SYSTEM_DEFAULT/admin_play) or idle,
            # immediately cut in and transition to the customer's song!
            with _speaker_next_lock:
                _last_speaker_next_time = time.time()
                next_s = database.next_song(force=True)
            if next_s:
                socketio.emit('play_song', {'song': next_s})
                active_queue = database.get_active_queue()

        if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
            warm_cache_async(active_queue['next_up']['youtube_id'])

        socketio.emit('queue_updated', active_queue)
        return jsonify({'success': True, 'message': message, 'song': new_song})
    else:
        return jsonify({'success': False, 'message': message}), 400

@app.route('/api/queue/delete', methods=['POST'])
def api_queue_delete():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400

    song_id = data.get('song_id')
    user_id = data.get('user_id')
    is_admin = check_is_admin(data)

    if not song_id:
        return jsonify({'success': False, 'message': 'Thiếu ID bài hát!'}), 400

    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID bài hát không hợp lệ!'}), 400

    # Get active queue before delete to check if this was the playing song
    curr_queue = database.get_active_queue()
    was_playing = curr_queue['current'] and str(curr_queue['current']['id']) == str(song_id)

    success, message = database.delete_song(song_id, user_id=user_id, is_admin=is_admin)

    if success:
        if was_playing:
            # Advance to next song automatically
            next_s = database.next_song()
            if next_s and next_s.get('youtube_id'):
                warm_cache_async(next_s['youtube_id'])
            socketio.emit('play_song', {'song': next_s})
            
        active_queue = database.get_active_queue()
        if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
            warm_cache_async(active_queue['next_up']['youtube_id'])
        socketio.emit('queue_updated', active_queue)
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'message': message}), 403

@app.route('/api/queue/clear', methods=['POST'])
def api_queue_clear():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền xóa toàn bộ!'}), 403

    success, message = database.clear_all_queue(is_admin=True)
    if success:
        active_queue = database.get_active_queue()
        socketio.emit('queue_updated', active_queue)
        return jsonify({'success': True, 'message': message})
    return jsonify({'success': False, 'message': message}), 400

# ----------------------------------------------------
# SPEAKER APIS & CONTROLS
# ----------------------------------------------------

_last_speaker_next_time = 0
_speaker_next_lock = threading.Lock()

@app.route('/api/speaker/next', methods=['POST'])
def api_speaker_next():
    """Triggered when current song ends or speaker/admin clicks skip"""
    global _last_speaker_next_time
    now = time.time()
    
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    is_force = bool(data.get('force', False))
    
    # Security check: caller must be speaker page session or verified admin
    is_admin = check_is_admin(data)
    is_speaker = check_is_speaker(data)
    if not is_admin and not is_speaker:
        return jsonify({'success': False, 'message': 'Chỉ màn hình Loa hoặc Admin mới có quyền chuyển bài!'}), 403
    
    with _speaker_next_lock:
        now = time.time()
        # Debounce:
        # - Automatic ending calls within 2.5s are debounced
        # - Forced manual skip calls within 0.15s (concurrent button mash / race condition) are debounced
        min_interval = 0.15 if is_force else 2.5
        if now - _last_speaker_next_time < min_interval:
            active_queue = database.get_active_queue()
            return jsonify({'success': True, 'song': active_queue.get('current'), 'queue': active_queue, 'debounced': True})
            
        _last_speaker_next_time = now
        next_s = database.next_song(force=is_force)
        active_queue = database.get_active_queue()
        schedules = database.get_scheduled_songs()

    if next_s and next_s.get('youtube_id'):
        warm_cache_async(next_s['youtube_id'])
    if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
        warm_cache_async(active_queue['next_up']['youtube_id'])

    socketio.emit('play_song', {'song': next_s})
    socketio.emit('queue_updated', active_queue)
    socketio.emit('schedule_updated', {'schedules': schedules})
    return jsonify({'success': True, 'song': next_s, 'queue': active_queue})

@app.route('/api/admin/play_now', methods=['POST'])
def api_admin_play_now():
    global _last_speaker_next_time
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    song_id = data.get('song_id')

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền chọn phát ngay!'}), 403

    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID bài hát không hợp lệ!'}), 400

    with _speaker_next_lock:
        _last_speaker_next_time = time.time()
        success, message, song = database.play_specific_song(song_id, is_admin=True)
    if success:
        if song and song.get('youtube_id'):
            warm_cache_async(song['youtube_id'])
        socketio.emit('play_song', {'song': song})
        active_queue = database.get_active_queue()
        if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
            warm_cache_async(active_queue['next_up']['youtube_id'])
        socketio.emit('queue_updated', active_queue)
        socketio.emit('schedule_updated', {'schedules': database.get_scheduled_songs()})
        return jsonify({'success': True, 'message': message, 'song': song})
    return jsonify({'success': False, 'message': message}), 400

@app.route('/api/admin/prioritize', methods=['POST'])
def api_admin_prioritize():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    song_id = data.get('song_id')

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền!'}), 403

    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID bài hát không hợp lệ!'}), 400

    success, message = database.prioritize_song(song_id, is_admin=True)
    if success:
        active_queue = database.get_active_queue()
        if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
            warm_cache_async(active_queue['next_up']['youtube_id'])
        socketio.emit('queue_updated', active_queue)
        return jsonify({'success': True, 'message': message})
    return jsonify({'success': False, 'message': message}), 400

_speaker_state = {
    'is_playing': True,
    'volume': 100
}

@app.route('/api/speaker/control', methods=['POST'])
def api_speaker_control():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    action = data.get('action') # 'pause', 'play', 'next', 'volume'
    value = data.get('value')

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền điều khiển!'}), 403

    if action == 'next':
        global _last_speaker_next_time
        with _speaker_next_lock:
            _last_speaker_next_time = time.time()
            next_s = database.next_song(force=True)
        _speaker_state['is_playing'] = True
        if next_s and next_s.get('youtube_id'):
            warm_cache_async(next_s['youtube_id'])
        socketio.emit('play_song', {'song': next_s})
        socketio.emit('speaker_state_sync', _speaker_state)
        active_queue = database.get_active_queue()
        if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
            warm_cache_async(active_queue['next_up']['youtube_id'])
        socketio.emit('queue_updated', active_queue)
        socketio.emit('schedule_updated', {'schedules': database.get_scheduled_songs()})
        return jsonify({'success': True, 'action': 'next', 'song': next_s, 'speaker_state': _speaker_state})
    else:
        if action == 'play':
            _speaker_state['is_playing'] = True
        elif action == 'pause':
            _speaker_state['is_playing'] = False
        elif action == 'volume' and value is not None:
            try:
                _speaker_state['volume'] = max(0, min(100, int(value)))
            except (ValueError, TypeError):
                pass
        socketio.emit('speaker_cmd', {'action': action, 'value': value, 'source': data.get('source', 'admin')})
        socketio.emit('speaker_state_sync', {**_speaker_state, 'source': data.get('source', 'admin')})
        return jsonify({'success': True, 'action': action, 'speaker_state': _speaker_state})

@app.route('/api/speaker/state', methods=['GET', 'POST'])
def api_speaker_state():
    if request.method == 'GET':
        return jsonify({'success': True, 'speaker_state': _speaker_state})
    data = request.get_json(silent=True) or {}
    # Security: Require speaker or admin authorization
    if not check_is_speaker(data) and not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Loa hoặc Admin mới có quyền cập nhật trạng thái!'}), 403
    source = data.get('source', 'speaker')
    if 'is_playing' in data:
        _speaker_state['is_playing'] = bool(data['is_playing'])
    if 'volume' in data and data['volume'] is not None:
        try:
            vol = max(0, min(100, int(data['volume'])))
            _speaker_state['volume'] = vol
            if source == 'admin':
                socketio.emit('speaker_cmd', {'action': 'volume', 'value': vol, 'source': 'admin'})
        except (ValueError, TypeError):
            pass
    socketio.emit('speaker_state_sync', {**_speaker_state, 'source': source})
    return jsonify({'success': True, 'speaker_state': _speaker_state})

# ----------------------------------------------------
# PLAYLIST MANAGEMENT APIS (PLAYLIST GỐC & DANH SÁCH PHÁT)
# ----------------------------------------------------

@app.route('/api/playlists', methods=['GET'])
def api_get_playlists():
    playlists = database.get_all_playlists()
    return jsonify({'success': True, 'playlists': playlists})

@app.route('/api/playlists/create', methods=['POST'])
def api_create_playlist():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    name = (data.get('name') or '').strip()[:100]
    description = (data.get('description') or '').strip()[:200]

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền tạo Playlist!'}), 403
    if not name:
        return jsonify({'success': False, 'message': 'Vui lòng nhập tên Playlist!'}), 400

    success, message, new_pl = database.create_playlist(name, description)
    playlists = database.get_all_playlists()
    return jsonify({'success': True, 'message': message, 'playlist': new_pl, 'playlists': playlists})

@app.route('/api/playlists/delete', methods=['POST'])
def api_delete_playlist():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    playlist_id = data.get('playlist_id')

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền xóa Playlist!'}), 403
    if not playlist_id:
        return jsonify({'success': False, 'message': 'Thiếu ID Playlist!'}), 400

    try:
        playlist_id = int(playlist_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID Playlist không hợp lệ!'}), 400

    success, message = database.delete_playlist(playlist_id)
    playlists = database.get_all_playlists()
    schedules = database.get_scheduled_songs()
    active_pl = database.get_active_playlist()

    if success:
        socketio.emit('schedule_updated', {'schedules': schedules})
        socketio.emit('active_playlist_changed', {'playlist': active_pl})
        socketio.emit('queue_updated', database.get_active_queue())

    return jsonify({'success': success, 'message': message, 'playlists': playlists, 'schedules': schedules})

@app.route('/api/playlists/activate', methods=['POST'])
def api_activate_playlist():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    playlist_id = data.get('playlist_id')

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền chọn Playlist phát!'}), 403
    if not playlist_id:
        return jsonify({'success': False, 'message': 'Thiếu ID Playlist!'}), 400

    try:
        playlist_id = int(playlist_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID Playlist không hợp lệ!'}), 400

    success, message, active_pl = database.set_active_playlist(playlist_id)
    playlists = database.get_all_playlists()
    
    # Broadcast to all that active playlist changed
    socketio.emit('active_playlist_changed', {'playlist': active_pl})

    # If current playing song is default/background song, immediately advance to the new playlist!
    global _last_speaker_next_time
    active_queue = database.get_active_queue()
    if active_queue['current'] and active_queue['current']['user_id'] in ('SYSTEM_DEFAULT', 'admin_play'):
        with _speaker_next_lock:
            _last_speaker_next_time = time.time()
            next_s = database.next_song()
        active_queue = database.get_active_queue()
        if next_s and next_s.get('youtube_id'):
            warm_cache_async(next_s['youtube_id'])
        if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
            warm_cache_async(active_queue['next_up']['youtube_id'])
        socketio.emit('play_song', {'song': next_s})
        socketio.emit('queue_updated', active_queue)

    return jsonify({'success': True, 'message': message, 'playlists': playlists, 'active_playlist': active_pl})

@app.route('/api/playlists/<int:playlist_id>/songs', methods=['GET'])
def api_get_playlist_songs(playlist_id):
    songs = database.get_playlist_songs(playlist_id)
    return jsonify({'success': True, 'songs': songs})

@app.route('/api/playlists/<int:playlist_id>/shuffle', methods=['POST'])
def api_shuffle_playlist_songs(playlist_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền trộn bài trong Playlist!'}), 403

    success, message, songs = database.shuffle_playlist_songs(playlist_id)
    if not success:
        return jsonify({'success': False, 'message': message}), 400

    active_queue = database.get_active_queue()
    if active_queue.get('next_up') and active_queue['next_up'].get('youtube_id'):
        warm_cache_async(active_queue['next_up']['youtube_id'])
    socketio.emit('queue_updated', active_queue)
    return jsonify({'success': True, 'message': message, 'songs': songs})

@app.route('/api/playlists/<int:playlist_id>/songs/add', methods=['POST'])
def api_add_song_to_playlist(playlist_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    youtube_id = (data.get('youtube_id') or '').strip()[:255]
    title = (data.get('title') or '').strip()[:150]
    thumbnail = (data.get('thumbnail') or '').strip()[:500]
    duration = (data.get('duration') or '--:--').strip()[:15]
    channel = (data.get('channel') or 'YouTube').strip()[:80]

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền thêm bài vào Playlist!'}), 403
    if not youtube_id or not title:
        return jsonify({'success': False, 'message': 'Thiếu thông tin bài hát!'}), 400

    success, message, new_song = database.add_song_to_playlist(
        playlist_id=playlist_id,
        youtube_id=youtube_id,
        title=title,
        thumbnail=thumbnail,
        duration=duration,
        channel=channel
    )
    songs = database.get_playlist_songs(playlist_id)
    playlists = database.get_all_playlists()
    return jsonify({'success': True, 'message': message, 'song': new_song, 'songs': songs, 'playlists': playlists})

@app.route('/api/playlists/save_current', methods=['POST'])
def api_save_current_song_to_playlist():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền lưu bài vào Playlist!'}), 403

    active_queue = database.get_active_queue()
    current_song = active_queue.get('current')
    if not current_song:
        return jsonify({'success': False, 'message': 'Hiện không có bài hát nào đang phát trên loa!'}), 400

    playlists = database.get_all_playlists()
    if not playlists:
        return jsonify({'success': False, 'message': 'Chưa có Playlist gốc nào trong hệ thống! Vui lòng tạo Playlist trước.'}), 400

    playlist_id = data.get('playlist_id')
    if not playlist_id:
        if len(playlists) == 1:
            playlist_id = playlists[0]['id']
        else:
            return jsonify({
                'success': False, 
                'need_choice': True,
                'message': 'Hệ thống có từ 2 Playlist trở lên, vui lòng chọn Playlist bạn muốn lưu vào!',
                'playlists': playlists
            }), 400
    else:
        try:
            playlist_id = int(playlist_id)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': 'ID Playlist không hợp lệ!'}), 400

    target_pl = next((p for p in playlists if p['id'] == playlist_id), None)
    if not target_pl:
        return jsonify({'success': False, 'message': 'Không tìm thấy Playlist đã chọn!'}), 404

    success, message, new_song = database.add_song_to_playlist(
        playlist_id=playlist_id,
        youtube_id=current_song.get('youtube_id') or '',
        title=current_song.get('title') or '',
        thumbnail=current_song.get('thumbnail') or '',
        duration=current_song.get('duration') or '--:--',
        channel=current_song.get('channel') or 'YouTube',
        check_duplicate=True
    )

    if not success:
        return jsonify({'success': False, 'message': message}), 400

    songs = database.get_playlist_songs(playlist_id)
    updated_playlists = database.get_all_playlists()
    return jsonify({
        'success': True,
        'message': f"Đã lưu bài '{current_song.get('title')}' vào Playlist '{target_pl['name']}' thành công!",
        'song': new_song,
        'songs': songs,
        'playlist_id': playlist_id,
        'playlists': updated_playlists
    })

@app.route('/api/playlists/songs/delete', methods=['POST'])
def api_delete_song_from_playlist():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    song_id = data.get('song_id')
    playlist_id = data.get('playlist_id')

    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền xóa bài khỏi Playlist!'}), 403
    if not song_id:
        return jsonify({'success': False, 'message': 'Thiếu ID bài hát!'}), 400

    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID bài hát không hợp lệ!'}), 400

    res = database.delete_song_from_playlist(song_id)
    success = res[0]
    message = res[1]
    cancelled_sched = res[2] if len(res) >= 3 else 0

    songs = database.get_playlist_songs(playlist_id) if playlist_id else []
    playlists = database.get_all_playlists()
    schedules = database.get_scheduled_songs()

    if success and cancelled_sched > 0:
        socketio.emit('schedule_updated', {'schedules': schedules})
        socketio.emit('queue_updated', database.get_active_queue())

    return jsonify({
        'success': success,
        'message': message,
        'songs': songs,
        'playlists': playlists,
        'schedules': schedules,
        'cancelled_schedules': cancelled_sched
    })

@app.route('/api/settings/playback_mode', methods=['POST'])
def api_set_playback_mode():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    mode = str(data.get('mode', ''))
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền đổi chế độ phát!'}), 403

    success, message = database.set_playback_mode(mode)
    if success:
        active_queue = database.get_active_queue()
        active_pl = database.get_active_playlist()

        # If switched to auto_priority and customer songs waiting while playing background song or idle, advance immediately!
        global _last_speaker_next_time
        if mode == 'auto_priority' and active_queue['waiting'] and (not active_queue['current'] or active_queue['current']['user_id'] in ('SYSTEM_DEFAULT', 'admin_play')):
            with _speaker_next_lock:
                _last_speaker_next_time = time.time()
                next_s = database.next_song(force=True)
            if next_s:
                if next_s.get('youtube_id'):
                    warm_cache_async(next_s['youtube_id'])
                socketio.emit('play_song', {'song': next_s})
                active_queue = database.get_active_queue()

        socketio.emit('playback_mode_changed', {
            'playback_mode': mode,
            'active_playlist': active_pl
        })
        socketio.emit('queue_updated', active_queue)
        return jsonify({'success': True, 'message': message, 'playback_mode': mode})
    return jsonify({'success': False, 'message': message}), 400

# ----------------------------------------------------
# ADMIN SCHEDULED PLAYBACK APIS (HẸN GIỜ PHÁT NHẠC)
# ----------------------------------------------------

@app.route('/api/admin/schedules', methods=['GET'])
def api_admin_get_schedules():
    if not check_is_admin():
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền xem lịch hẹn!'}), 403
    schedules = database.get_scheduled_songs()
    return jsonify({'success': True, 'schedules': schedules})

@app.route('/api/admin/schedules/add', methods=['POST'])
def api_admin_add_schedule():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền đặt lịch hẹn giờ!'}), 403

    youtube_id = (data.get('youtube_id') or '').strip()[:255]
    title = (data.get('title') or '').strip()[:150]
    thumbnail = (data.get('thumbnail') or '').strip()[:500]
    duration = (data.get('duration') or '').strip()[:15]
    channel = (data.get('channel') or 'YouTube').strip()[:80]
    scheduled_time = (data.get('scheduled_time') or '').strip()[:10]
    scheduled_date = (data.get('scheduled_date') or '').strip()[:20] if data.get('scheduled_date') else None
    try:
        repeat_daily = int(data.get('repeat_daily', 0))
    except (ValueError, TypeError):
        repeat_daily = 0

    if not youtube_id or not title or not scheduled_time:
        return jsonify({'success': False, 'message': 'Thiếu thông tin bài hát hoặc giờ hẹn!'}), 400

    success, message, item = database.add_scheduled_song(
        youtube_id=youtube_id,
        title=title,
        thumbnail=thumbnail,
        duration=duration,
        channel=channel,
        scheduled_time=scheduled_time,
        scheduled_date=scheduled_date,
        repeat_daily=repeat_daily
    )

    if success:
        warm_cache_async(youtube_id)
        socketio.emit('schedule_updated', {'schedules': database.get_scheduled_songs()})
        return jsonify({'success': True, 'message': message, 'schedule': item})
    return jsonify({'success': False, 'message': message}), 400

@app.route('/api/admin/schedules/delete', methods=['POST'])
def api_admin_delete_schedule():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền xóa lịch hẹn giờ!'}), 403

    schedule_id = data.get('schedule_id')
    if not schedule_id:
        return jsonify({'success': False, 'message': 'Thiếu ID lịch hẹn giờ!'}), 400

    try:
        schedule_id = int(schedule_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID lịch hẹn giờ không hợp lệ!'}), 400

    res = database.delete_scheduled_song(schedule_id)
    success = res[0]
    message = res[1]
    deleted_pl_songs = res[2] if len(res) >= 3 else 0

    if success:
        schedules = database.get_scheduled_songs()
        playlists = database.get_all_playlists()
        socketio.emit('schedule_updated', {'schedules': schedules})
        if deleted_pl_songs > 0:
            active_pl = database.get_active_playlist()
            socketio.emit('active_playlist_changed', {'playlist': active_pl})
            socketio.emit('queue_updated', database.get_active_queue())
        return jsonify({
            'success': True,
            'message': message,
            'schedules': schedules,
            'deleted_playlist_songs': deleted_pl_songs,
            'playlists': playlists
        })
    return jsonify({'success': False, 'message': message}), 400

@app.route('/api/admin/schedules/trigger', methods=['POST'])
def api_admin_trigger_schedule():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': 'Dữ liệu JSON không hợp lệ!'}), 400
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền kích hoạt!'}), 403

    schedule_id = data.get('schedule_id')
    if not schedule_id:
        return jsonify({'success': False, 'message': 'Thiếu ID lịch hẹn giờ!'}), 400

    try:
        schedule_id = int(schedule_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'ID lịch hẹn giờ không hợp lệ!'}), 400

    success, message, song = database.trigger_scheduled_song(schedule_id)
    if success and song:
        warm_cache_async(song.get('youtube_id'))
        active_queue = database.get_active_queue()

        # If nothing is playing, or only background filler song is playing, advance immediately!
        global _last_speaker_next_time
        if not active_queue['current'] or active_queue['current']['user_id'] in ('SYSTEM_DEFAULT', 'admin_play'):
            with _speaker_next_lock:
                _last_speaker_next_time = time.time()
                next_s = database.next_song(force=True)
            if next_s:
                socketio.emit('play_song', {'song': next_s})
                active_queue = database.get_active_queue()

        socketio.emit('queue_updated', {
            'current': active_queue['current'],
            'waiting': active_queue['waiting'],
            'next_up': active_queue.get('next_up'),
            'playback_mode': database.get_playback_mode(),
            'active_playlist': database.get_active_playlist()
        })
        socketio.emit('schedule_updated', {'schedules': database.get_scheduled_songs()})
        socketio.emit('schedule_triggered', {
            'title': song.get('title'),
            'message': message
        })
        return jsonify({'success': True, 'message': message, 'song': song})
    return jsonify({'success': False, 'message': message}), 400

def start_scheduler_thread():
    """Background thread that monitors schedules every 5 seconds"""
    def _loop():
        global _last_speaker_next_time
        time.sleep(2)  # Initial grace delay
        while True:
            try:
                due_list = database.get_due_schedules()
                for sched in due_list:
                    success, msg, song = database.trigger_scheduled_song(sched['id'])
                    if success and song:
                        print(f"⏰ [HẸN GIỜ PHÁT NHẠC] {msg} ({sched['scheduled_time']})")
                        warm_cache_async(song.get('youtube_id'))
                        active_queue = database.get_active_queue()

                        # If nothing is playing on speaker, or only background filler song is playing, advance immediately!
                        if not active_queue['current'] or active_queue['current']['user_id'] in ('SYSTEM_DEFAULT', 'admin_play'):
                            with _speaker_next_lock:
                                _last_speaker_next_time = time.time()
                                next_s = database.next_song(force=True)
                            if next_s:
                                socketio.emit('play_song', {'song': next_s})
                                active_queue = database.get_active_queue()

                        socketio.emit('queue_updated', {
                            'current': active_queue['current'],
                            'waiting': active_queue['waiting'],
                            'next_up': active_queue.get('next_up'),
                            'playback_mode': database.get_playback_mode(),
                            'active_playlist': database.get_active_playlist()
                        })
                        socketio.emit('schedule_updated', {'schedules': database.get_scheduled_songs()})
                        socketio.emit('schedule_triggered', {
                            'title': song.get('title'),
                            'scheduled_time': sched['scheduled_time'],
                            'message': f"⏰ Đã đến giờ hẹn ({sched['scheduled_time']}): Bài '{song.get('title')}' đã được đưa lên đầu hàng đợi!"
                        })
            except Exception as e:
                print("Error in scheduler loop:", e)
            time.sleep(5)

    threading.Thread(target=_loop, daemon=True).start()

# Start background scheduler
start_scheduler_thread()

# ----------------------------------------------------
# WEBSOCKET EVENTS
# ----------------------------------------------------

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'music')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

HOT_IMAGES_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'hot_items')
os.makedirs(HOT_IMAGES_FOLDER, exist_ok=True)

from werkzeug.utils import secure_filename

# ----------------------------------------------------
# HOT ITEMS APIS (MÓN HOT / MENU NỔI BẬT)
# ----------------------------------------------------

@app.route('/api/hot_items', methods=['GET'])
def api_get_hot_items():
    items = database.get_hot_items()
    categories = database.get_menu_categories()
    return jsonify({'success': True, 'items': items, 'categories': categories})

@app.route('/api/admin/hot_items/toggle_hot', methods=['POST'])
def api_admin_toggle_hot_item():
    data = request.get_json(silent=True) or {}
    if not data and request.form:
        data = request.form.to_dict()
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền chỉnh sửa!'}), 403
    item_id = data.get('item_id') or data.get('id')
    raw_hot = data.get('is_hot')
    is_hot = (1 if str(raw_hot).lower() in ('1', 'true', 'on', 'yes') else 0) if raw_hot is not None else None
    success, message, item = database.toggle_hot_item_status(item_id, is_hot=is_hot)
    if success:
        items = database.get_hot_items()
        categories = database.get_menu_categories()
        socketio.emit('hot_items_updated', {'items': items, 'categories': categories})
        return jsonify({'success': True, 'message': message, 'item': item, 'items': items, 'categories': categories})
    return jsonify({'success': False, 'message': message}), 400

@app.route('/api/admin/hot_items/toggle_new', methods=['POST'])
def api_admin_toggle_new_item():
    data = request.get_json(silent=True) or {}
    if not data and request.form:
        data = request.form.to_dict()
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền chỉnh sửa!'}), 403
    item_id = data.get('item_id') or data.get('id')
    raw_new = data.get('is_new')
    is_new = (1 if str(raw_new).lower() in ('1', 'true', 'on', 'yes') else 0) if raw_new is not None else None
    success, message, item = database.toggle_new_item_status(item_id, is_new=is_new)
    if success:
        items = database.get_hot_items()
        categories = database.get_menu_categories()
        socketio.emit('hot_items_updated', {'items': items, 'categories': categories})
        return jsonify({'success': True, 'message': message, 'item': item, 'items': items, 'categories': categories})
    return jsonify({'success': False, 'message': message}), 400

@app.route('/api/admin/menu_categories/save', methods=['POST'])
def api_admin_save_menu_categories():
    data = request.get_json(silent=True) or {}
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền chỉnh sửa phân loại!'}), 403
    cats = data.get('categories') or []
    saved_cats = database.save_menu_categories(cats)
    items = database.get_hot_items()
    socketio.emit('hot_items_updated', {'items': items, 'categories': saved_cats})
    return jsonify({'success': True, 'message': 'Đã cập nhật danh sách phân loại món!', 'categories': saved_cats, 'items': items})

@app.route('/api/admin/hot_items/save', methods=['POST'])
def api_admin_save_hot_item():
    if not check_is_admin():
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền chỉnh sửa món HOT!'}), 403

    action = (request.form.get('action') or '').strip()
    if action == 'toggle_hot':
        item_id = request.form.get('item_id')
        raw_hot = request.form.get('is_hot')
        is_hot_val = (1 if str(raw_hot).lower() in ('1', 'true', 'on', 'yes') else 0) if raw_hot is not None else None
        success, message, item = database.toggle_hot_item_status(item_id, is_hot=is_hot_val)
        if success:
            items = database.get_hot_items()
            categories = database.get_menu_categories()
            socketio.emit('hot_items_updated', {'items': items, 'categories': categories})
            return jsonify({'success': True, 'message': message, 'item': item, 'items': items, 'categories': categories})
        return jsonify({'success': False, 'message': message}), 400

    if action == 'toggle_new':
        item_id = request.form.get('item_id')
        raw_new = request.form.get('is_new')
        is_new_val = (1 if str(raw_new).lower() in ('1', 'true', 'on', 'yes') else 0) if raw_new is not None else None
        success, message, item = database.toggle_new_item_status(item_id, is_new=is_new_val)
        if success:
            items = database.get_hot_items()
            categories = database.get_menu_categories()
            socketio.emit('hot_items_updated', {'items': items, 'categories': categories})
            return jsonify({'success': True, 'message': message, 'item': item, 'items': items, 'categories': categories})
        return jsonify({'success': False, 'message': message}), 400

    if action == 'save_categories':
        import json as _json
        raw_cats = request.form.get('categories_json') or '[]'
        try:
            cats = _json.loads(raw_cats)
        except Exception:
            cats = []
        saved_cats = database.save_menu_categories(cats)
        items = database.get_hot_items()
        socketio.emit('hot_items_updated', {'items': items, 'categories': saved_cats})
        return jsonify({'success': True, 'message': 'Đã cập nhật phân loại món!', 'categories': saved_cats, 'items': items})

    item_id = request.form.get('item_id')
    name = (request.form.get('name') or '').strip()[:100]
    raw_price = (request.form.get('price') or '').strip()[:50]
    price_digits = ''.join(ch for ch in raw_price if ch.isdigit())
    price = f"{int(price_digits):,}".replace(",", ".") + "đ" if price_digits else raw_price
    description = (request.form.get('description') or '').strip()[:500]
    image_url_input = (request.form.get('image_url') or '').strip()[:500]
    category = (request.form.get('category') or 'Cà Phê').strip()[:40]
    raw_is_hot = request.form.get('is_hot')
    raw_is_new = request.form.get('is_new')
    is_new = 1 if (raw_is_new and str(raw_is_new).lower() in ('1', 'true', 'on', 'yes')) else 0
    # Món mới sẽ auto lên hot
    is_hot = 1 if (is_new or (raw_is_hot is None or str(raw_is_hot).lower() in ('1', 'true', 'on', 'yes'))) else 0

    if not name or not price:
        return jsonify({'success': False, 'message': 'Vui lòng nhập đầy đủ Tên món và Giá!'}), 400

    saved_image_url = None
    file = request.files.get('image_file')
    if file and file.filename:
        clean_base = secure_filename(file.filename)
        root, ext = os.path.splitext(clean_base)
        ext = ext.lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'):
            return jsonify({'success': False, 'message': 'Chỉ hỗ trợ file ảnh (.jpg, .png, .webp, .gif)!'}), 400
        safe_name = f"hot_{int(time.time() * 1000)}_{os.urandom(2).hex()}{ext}"
        target_path = os.path.join(HOT_IMAGES_FOLDER, safe_name)
        file.save(target_path)
        saved_image_url = f"/static/hot_items/{safe_name}"
    elif image_url_input:
        saved_image_url = image_url_input

    if item_id:
        success, message, item = database.update_hot_item(item_id, name, price, description, image_url=saved_image_url, category=category, is_hot=is_hot, is_new=is_new)
    else:
        if not saved_image_url:
            return jsonify({'success': False, 'message': 'Vui lòng chọn ảnh món hoặc dán link ảnh!'}), 400
        success, message, item = database.add_hot_item(name, price, description, saved_image_url, category=category, is_hot=is_hot, is_new=is_new)

    if success:
        items = database.get_hot_items()
        categories = database.get_menu_categories()
        socketio.emit('hot_items_updated', {'items': items, 'categories': categories})
        return jsonify({'success': True, 'message': message, 'item': item, 'items': items, 'categories': categories})
    return jsonify({'success': False, 'message': message}), 400

@app.route('/api/admin/hot_items/delete', methods=['POST'])
def api_admin_delete_hot_item():
    data = request.get_json(silent=True) or {}
    if not check_is_admin(data):
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền xóa món HOT!'}), 403

    item_id = data.get('item_id') or data.get('id')
    if not item_id:
        return jsonify({'success': False, 'message': 'Thiếu ID món!'}), 400

    success, message = database.delete_hot_item(item_id)
    if success:
        items = database.get_hot_items()
        categories = database.get_menu_categories()
        socketio.emit('hot_items_updated', {'items': items, 'categories': categories})
        return jsonify({'success': True, 'message': message, 'items': items, 'categories': categories})
    return jsonify({'success': False, 'message': message}), 400

@app.route('/api/upload/mp3', methods=['POST'])
def api_upload_mp3():
    if not check_is_admin():
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền tải nhạc lên!'}), 403

    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Không tìm thấy file tải lên!'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'success': False, 'message': 'Chưa chọn file!'}), 400

    raw_filename = file.filename
    clean_base = secure_filename(raw_filename)
    root, ext = os.path.splitext(clean_base)
    ext = ext.lower()
    if ext not in ('.mp3', '.m4a', '.wav', '.ogg', '.flac'):
        return jsonify({'success': False, 'message': 'Chỉ hỗ trợ file âm thanh: .mp3, .m4a, .wav, .ogg!'}), 400

    # Sanitize root and check Windows reserved names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    reserved = ('CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
                'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9')
    if not root or root.upper() in reserved:
        safe_name = f"song_{int(time.time())}{ext}"
    else:
        safe_name = f"{root}_{int(time.time())}{ext}"

    target_path = os.path.join(UPLOAD_FOLDER, safe_name)
    if not os.path.abspath(target_path).startswith(os.path.abspath(UPLOAD_FOLDER)):
        return jsonify({'success': False, 'message': 'Đường dẫn file không hợp lệ!'}), 400

    file.save(target_path)

    file_url = f"/static/music/{safe_name}"
    title = root.replace('_', ' ').replace('-', ' ') if root and root.upper() not in reserved else "Nhạc Tải Lên"
    
    playlist_id = request.form.get('playlist_id')
    if playlist_id:
        try:
            pl_id = int(playlist_id)
            database.add_song_to_playlist(
                playlist_id=pl_id,
                youtube_id=file_url,
                title=title,
                thumbnail="https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=120&h=90&fit=crop",
                duration="MP3",
                channel="Nhạc Quán (MP3)"
            )
        except Exception as e:
            print("Error auto-adding to playlist:", e)

    return jsonify({
        'success': True,
        'message': f"Đã tải lên bài hát '{title}' thành công!",
        'file_url': file_url,
        'title': title
    })

@app.route('/api/local_music', methods=['GET'])
def api_local_music():
    if not check_is_admin():
        return jsonify({'success': False, 'message': 'Chỉ Admin mới có quyền truy cập nhạc nội bộ!'}), 403
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for fname in os.listdir(UPLOAD_FOLDER):
            if fname.lower().endswith(('.mp3', '.m4a', '.wav', '.ogg', '.flac')):
                path = os.path.join(UPLOAD_FOLDER, fname)
                size_mb = round(os.path.getsize(path) / (1024 * 1024), 2)
                title = os.path.splitext(fname)[0].replace('_', ' ').replace('-', ' ')
                files.append({
                    'filename': fname,
                    'title': title,
                    'file_url': f"/static/music/{fname}",
                    'size_mb': size_mb
                })
    return jsonify({'success': True, 'files': files})

@socketio.on('connect')
def handle_connect():
    emit('queue_updated', database.get_active_queue())
    emit('speaker_state_sync', _speaker_state)

@socketio.on('request_queue')
def handle_request_queue():
    emit('queue_updated', database.get_active_queue())
    emit('speaker_state_sync', _speaker_state)

@socketio.on('set_volume_realtime')
def handle_set_volume_realtime(data):
    if not isinstance(data, dict):
        return
    # Security: Verify client has admin or speaker privilege
    token = str(data.get('pin') or data.get('password') or data.get('admin_pin') or '').strip()
    has_privilege = session.get('is_admin') or session.get('is_speaker') or token in (ADMIN_PIN, SPEAKER_PASSWORD)
    if not has_privilege:
        return
    try:
        vol = max(0, min(100, int(data.get('volume', 100))))
    except (ValueError, TypeError):
        return
    source = data.get('source', 'unknown')
    _speaker_state['volume'] = vol
    socketio.emit('speaker_cmd', {'action': 'volume', 'value': vol, 'source': source})
    socketio.emit('speaker_state_sync', {**_speaker_state, 'source': source})

@socketio.on('speaker_playback_state')
def handle_speaker_playback_state(data):
    if not isinstance(data, dict):
        return
    # Security: Only authenticated speaker or admin can update playback state
    token = str(data.get('pin') or data.get('password') or '').strip()
    has_privilege = session.get('is_speaker') or session.get('is_admin') or token in (ADMIN_PIN, SPEAKER_PASSWORD)
    if not has_privilege:
        return
    if 'is_playing' in data:
        _speaker_state['is_playing'] = bool(data['is_playing'])
    if 'volume' in data and data['volume'] is not None:
        try:
            _speaker_state['volume'] = max(0, min(100, int(data['volume'])))
        except (ValueError, TypeError):
            pass
    socketio.emit('speaker_state_sync', {**_speaker_state, 'source': data.get('source', 'speaker')})

# ----------------------------------------------------
# GLOBAL ERROR HANDLERS (ANTI-CRASH)
# ----------------------------------------------------

@app.errorhandler(400)
def handle_400(e):
    return jsonify({'success': False, 'message': 'Yêu cầu không hợp lệ (Bad Request)!'}), 400

@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': 'Không tìm thấy tài nguyên (Not Found)!'}), 404
    return render_template('index.html', local_ip=get_local_ip()), 404

@app.errorhandler(500)
def handle_500(e):
    return jsonify({'success': False, 'message': 'Lỗi máy chủ nội bộ. Hệ thống tự động phục hồi an toàn!'}), 500

@app.errorhandler(Exception)
def handle_generic_exception(e):
    return jsonify({'success': False, 'message': f'Lỗi hệ thống đã được chặn an toàn: {str(e)}'}), 500

if __name__ == '__main__':
    local_ip = get_local_ip()
    print("=" * 60)
    print("  KARAOKE & MUSIC QUEUE SYSTEM (XUANCF)")
    print(f"  * Khách hàng quét mã:   http://{local_ip}:5000/")
    print(f"  * Màn hình Loa/Speaker:  http://{local_ip}:5000/speaker (Mật khẩu: {SPEAKER_PASSWORD})")
    print(f"  * Trang Quản Trị Admin:  http://{local_ip}:5000/admin (Mật khẩu: {ADMIN_PIN})")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)

