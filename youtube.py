import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yt_dlp
import time
import os
import threading

def format_duration(seconds):
    if not seconds:
        return "00:00"
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
    except Exception:
        return str(seconds)

def extract_video_id(query):
    query = str(query).strip()
    if re.match(r'^[a-zA-Z0-9_-]{11}$', query):
        return query
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'youtube\.com\/shorts\/([0-9A-Za-z_-]{11})',
        r'youtube\.com\/embed\/([0-9A-Za-z_-]{11})'
    ]
    for pattern in patterns:
        m = re.search(pattern, query)
        if m:
            return m.group(1)
    return None

def get_video_by_id(video_id):
    """Fast lookup using YouTube oEmbed + default HQ thumbnail"""
    try:
        url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        res = requests.get(url, timeout=4)
        if res.status_code == 200:
            data = res.json()
            return {
                'id': video_id,
                'title': data.get('title', 'YouTube Video'),
                'channel': data.get('author_name', 'YouTube'),
                'thumbnail': f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                'duration': "YouTube"
            }
    except Exception as e:
        print(f"oEmbed error: {e}")

    # Fallback to yt_dlp
    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'noplaylist': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            if info:
                return {
                    'id': video_id,
                    'title': info.get('title', 'YouTube Video'),
                    'channel': info.get('uploader') or info.get('channel', 'YouTube'),
                    'thumbnail': f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    'duration': format_duration(info.get('duration'))
                }
    except Exception as e:
        print(f"yt_dlp fallback error: {e}")

    return {
        'id': video_id,
        'title': f'Video YouTube ({video_id})',
        'channel': 'YouTube',
        'thumbnail': f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        'duration': '--:--'
    }

# Persistent HTTP session with connection pooling for ultra-fast YouTube searches
_yt_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=15, pool_maxsize=30, max_retries=Retry(total=2, backoff_factor=0.2))
_yt_session.mount('https://', _adapter)
_yt_session.mount('http://', _adapter)
_yt_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
    'Content-Type': 'application/json'
})

_search_cache = {}
_search_lock = threading.Lock()
MAX_SEARCH_CACHE_SIZE = 250
SEARCH_CACHE_TTL = 3600  # 1 hour

def _innertube_search(query, limit=8):
    """Direct InnerTube YouTube API search - executes in ~0.5s instead of ~7s"""
    url = 'https://www.youtube.com/youtubei/v1/search?prettyPrint=false'
    payload = {
        'context': {
            'client': {
                'hl': 'vi',
                'gl': 'VN',
                'clientName': 'WEB',
                'clientVersion': '2.20240101.01.00'
            }
        },
        'query': query
    }
    try:
        res = _yt_session.post(url, json=payload, timeout=4.5)
        if res.status_code != 200:
            return None
        data = res.json()
        items = []

        def walk(obj):
            if len(items) >= limit * 2:
                return
            if isinstance(obj, dict):
                if 'videoRenderer' in obj:
                    v = obj['videoRenderer']
                    v_id = v.get('videoId')
                    if v_id:
                        title = ''
                        if 'title' in v:
                            if 'runs' in v['title'] and v['title']['runs']:
                                title = ''.join(r.get('text', '') for r in v['title']['runs'])
                            elif 'simpleText' in v['title']:
                                title = v['title']['simpleText']
                        channel = 'YouTube'
                        byline = v.get('ownerText') or v.get('longBylineText') or v.get('shortBylineText')
                        if byline and 'runs' in byline and byline['runs']:
                            channel = byline['runs'][0].get('text', 'YouTube')
                        duration = '--:--'
                        if 'lengthText' in v:
                            duration = v['lengthText'].get('simpleText', '--:--')
                        is_live = False
                        for badge in v.get('badges', []):
                            label = badge.get('metadataBadgeRenderer', {}).get('label', '')
                            if 'LIVE' in label.upper() or 'TRỰC TIẾP' in label.upper():
                                is_live = True
                        if not is_live and duration != '--:--':
                            items.append({
                                'id': v_id,
                                'title': title or 'Unknown Title',
                                'channel': channel or 'YouTube',
                                'thumbnail': f'https://i.ytimg.com/vi/{v_id}/hqdefault.jpg',
                                'duration': duration
                            })
                for k, val in obj.items():
                    walk(val)
            elif isinstance(obj, list):
                for it in obj:
                    walk(it)

        walk(data)
        seen = set()
        deduped = []
        for it in items:
            if it['id'] not in seen:
                seen.add(it['id'])
                deduped.append(it)
                if len(deduped) >= limit:
                    break
        return deduped
    except Exception as e:
        print(f"InnerTube search error: {e}")
        return None

def search_youtube(query, limit=8):
    query = query.strip()
    if not query:
        return []

    # Check if direct mp3/audio URL or local music file
    if query.lower().endswith(('.mp3', '.m4a', '.wav', '.ogg', '.aac')) or query.startswith('/static/music/'):
        clean_name = os.path.basename(query).split('?')[0]
        return [{
            'id': query,
            'title': clean_name.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' '),
            'channel': 'Nhạc Cục Bộ (Audio)',
            'thumbnail': 'https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=120&h=90&fit=crop',
            'duration': 'Audio MP3'
        }]

    # Check if direct link or video ID
    vid = extract_video_id(query)
    if vid:
        video = get_video_by_id(vid)
        return [video] if video else []

    # Check in-memory search cache
    cache_key = (query.lower(), limit)
    now = time.time()
    with _search_lock:
        if cache_key in _search_cache:
            cached_time, cached_results = _search_cache[cache_key]
            if now - cached_time < SEARCH_CACHE_TTL:
                return cached_results

    # 1st Priority: Ultra-fast InnerTube API search (~0.5s)
    results = _innertube_search(query, limit=limit)
    if results is not None:
        with _search_lock:
            if len(_search_cache) >= MAX_SEARCH_CACHE_SIZE:
                # Evict oldest entry safely
                try:
                    oldest_k = min(_search_cache.keys(), key=lambda k: _search_cache[k][0])
                    _search_cache.pop(oldest_k, None)
                except Exception:
                    _search_cache.clear()
            _search_cache[cache_key] = (now, results)
        return results

    # 2nd Priority: yt_dlp fallback
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
        'noplaylist': True,
        'ignoreerrors': True,
        'match_filter': yt_dlp.utils.match_filter_func('!is_live')
    }
    search_query = f"ytsearch{limit}:{query}"
    fallback_results = []

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(search_query, download=False)
            if res and 'entries' in res:
                for entry in res['entries']:
                    if not entry:
                        continue
                    v_id = entry.get('id')
                    if not v_id:
                        continue
                    if entry.get('is_live'):
                        continue
                    fallback_results.append({
                        'id': v_id,
                        'title': entry.get('title', 'Unknown Title'),
                        'channel': entry.get('uploader') or entry.get('channel') or 'YouTube',
                        'thumbnail': f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg",
                        'duration': format_duration(entry.get('duration'))
                    })
        if fallback_results:
            with _search_lock:
                if len(_search_cache) >= MAX_SEARCH_CACHE_SIZE:
                    try:
                        oldest_k = min(_search_cache.keys(), key=lambda k: _search_cache[k][0])
                        _search_cache.pop(oldest_k, None)
                    except Exception:
                        _search_cache.clear()
                _search_cache[cache_key] = (now, fallback_results)
    except Exception as e:
        print(f"yt_dlp search fallback error: {e}")

    return fallback_results

_audio_cache = {}
_audio_lock = threading.Lock()
MAX_AUDIO_CACHE_SIZE = 300

def is_audio_cached(youtube_id):
    if not youtube_id:
        return False
    yid_str = str(youtube_id).strip()
    now = time.time()
    with _audio_lock:
        if yid_str in _audio_cache:
            return now < _audio_cache[yid_str][3]
    return False

_ydl_thread_local = threading.local()

def _get_shared_ydl():
    """Thread-local YoutubeDL instance with clean pure-audio extraction options for 100% thread safety"""
    ydl = getattr(_ydl_thread_local, 'ydl', None)
    if ydl is None:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/140/251/best',
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'noplaylist': True,
            'socket_timeout': 6
        }
        ydl = yt_dlp.YoutubeDL(ydl_opts)
        _ydl_thread_local.ydl = ydl
    return ydl

def get_direct_audio_url(youtube_id, force_fresh=False):
    """Extract direct audio stream URL for pure audio playback without YouTube iframe.
    Direct CDN audio streams naturally contain ZERO YouTube video ads."""
    if not youtube_id:
        return None

    yid_str = str(youtube_id).strip()
    if yid_str.startswith('http') or yid_str.startswith('/static/'):
        return {
            'url': yid_str,
            'title': 'Direct Audio',
            'duration': '--:--'
        }

    # Strict YouTube video ID format check (11 chars) before invoking yt_dlp
    if not re.match(r'^[a-zA-Z0-9_-]{11}$', yid_str):
        return None
    
    now = time.time()
    with _audio_lock:
        if not force_fresh and yid_str in _audio_cache:
            cached_url, cached_title, cached_dur, expire_time = _audio_cache[yid_str]
            if now < expire_time:
                return {
                    'url': cached_url,
                    'title': cached_title,
                    'duration': cached_dur
                }
            else:
                _audio_cache.pop(yid_str, None)

    try:
        ydl = _get_shared_ydl()
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={yid_str}", download=False)
        
        # Fallback to standard extraction if android/web client returned no direct audio format
        if not (info and info.get('url')):
            fallback_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
                'noplaylist': True,
                'socket_timeout': 6
            }
            with yt_dlp.YoutubeDL(fallback_opts) as f_ydl:
                info = f_ydl.extract_info(f"https://www.youtube.com/watch?v={yid_str}", download=False)

        if info and info.get('url'):
            stream_url = info.get('url')
            title = info.get('title')
            dur = format_duration(info.get('duration'))
            with _audio_lock:
                if len(_audio_cache) >= MAX_AUDIO_CACHE_SIZE:
                    try:
                        oldest_k = min(_audio_cache.keys(), key=lambda k: _audio_cache[k][3])
                        _audio_cache.pop(oldest_k, None)
                    except Exception:
                        _audio_cache.clear()
                # Cache for 2 hours (7200s)
                _audio_cache[yid_str] = (stream_url, title, dur, now + 7200)
            return {
                'url': stream_url,
                'title': title,
                'duration': dur
            }
    except Exception as e:
        print(f"Error getting direct audio url: {e}")
    return None

