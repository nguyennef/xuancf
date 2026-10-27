import sqlite3
import os
import random
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'songs.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA synchronous = NORMAL")
    except Exception:
        pass
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_id TEXT NOT NULL,
            title TEXT NOT NULL,
            thumbnail TEXT,
            duration TEXT,
            channel TEXT,
            user_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT DEFAULT 'waiting', -- 'waiting', 'playing', 'finished', 'cancelled'
            order_num INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            is_active INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS playlist_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            youtube_id TEXT NOT NULL,
            title TEXT NOT NULL,
            thumbnail TEXT,
            duration TEXT,
            channel TEXT,
            order_num INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
        )
    ''')
    
    # Seed sample playlists if empty
    cursor.execute("SELECT COUNT(*) FROM playlists")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO playlists (name, description, is_active) VALUES ('Lofi & Chill Cafe', 'Nhạc chill nhẹ nhàng, thư giãn', 1)")
        p1_id = cursor.lastrowid
        
        cursor.execute("INSERT INTO playlists (name, description, is_active) VALUES ('Acoustic & Guitar', 'Nhạc mộc acoustic êm đềm', 0)")
        p2_id = cursor.lastrowid

        p1_songs = [
            ("BYTxPFj44uo", "Why The Rush - Lo-fi Beats For Work & Study (3.5 Hours)", "https://i.ytimg.com/vi/BYTxPFj44uo/hqdefault.jpg", "03:31:18", "Lofi Cafe"),
            ("CBSlu_VMS9U", "Jazz Lofi Mix [3 Hours] - Relaxing Music to Chill/Study to", "https://i.ytimg.com/vi/CBSlu_VMS9U/hqdefault.jpg", "03:01:04", "Lofi Jazz"),
            ("CLeZyIID9Bo", "Chill Lofi Mix - Beats to Relax / Study (1.7 Hours)", "https://i.ytimg.com/vi/CLeZyIID9Bo/hqdefault.jpg", "01:44:52", "Lofi Girl"),
            ("n61ULEU7CO0", "Best of Lofi Hip Hop - Chill Beats (6 Hours)", "https://i.ytimg.com/vi/n61ULEU7CO0/hqdefault.jpg", "06:10:58", "Lofi Records")
        ]
        for idx, s in enumerate(p1_songs):
            cursor.execute('''
                INSERT INTO playlist_songs (playlist_id, youtube_id, title, thumbnail, duration, channel, order_num)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (p1_id, s[0], s[1], s[2], s[3], s[4], idx))

        p2_songs = [
            ("32sYGCOYJUM", "Chạy Ngay Đi (Acoustic Version)", "https://i.ytimg.com/vi/32sYGCOYJUM/hqdefault.jpg", "04:12", "Sơn Tùng M-TP"),
            ("dQw4w9WgXcQ", "Never Gonna Give You Up (Acoustic)", "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", "03:33", "Rick Astley")
        ]
        for idx, s in enumerate(p2_songs):
            cursor.execute('''
                INSERT INTO playlist_songs (playlist_id, youtube_id, title, thumbnail, duration, channel, order_num)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (p2_id, s[0], s[1], s[2], s[3], s[4], idx))

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('playback_mode', 'auto_priority')")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_id TEXT NOT NULL,
            title TEXT NOT NULL,
            thumbnail TEXT,
            duration TEXT,
            channel TEXT,
            scheduled_time TEXT NOT NULL,
            scheduled_date TEXT,
            repeat_daily INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_triggered_date TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hot_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            description TEXT DEFAULT '',
            image_url TEXT NOT NULL,
            category TEXT DEFAULT 'coffee',
            is_hot INTEGER DEFAULT 1,
            order_num INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    try:
        cursor.execute("ALTER TABLE hot_items ADD COLUMN category TEXT DEFAULT 'coffee'")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE hot_items ADD COLUMN is_hot INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    cursor.execute("SELECT value FROM settings WHERE key = 'hot_items_seeded'")
    if not cursor.fetchone():
        cursor.execute("SELECT COUNT(*) FROM hot_items")
        if cursor.fetchone()[0] == 0:
            default_hot_items = [
                (
                    "Cà Phê Muối Kem Béo",
                    "35.000đ",
                    "Cà phê phin đậm đà kết hợp lớp kem muối mặn ngọt béo ngậy đặc trưng của Xuân Cà Phê.",
                    "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?auto=format&fit=crop&w=600&q=80"
                ),
                (
                    "Trà Đào Cam Sả Nhiệt Đới",
                    "39.000đ",
                    "Trà đào thanh mát ủ lạnh cùng cam vàng tươi, sả cây thơm dịu và miếng đào giòn ngọt.",
                    "https://images.unsplash.com/photo-1556679343-c7306c1976bc?auto=format&fit=crop&w=600&q=80"
                ),
                (
                    "Matcha Latte Kem Sữa",
                    "42.000đ",
                    "Bột trà xanh Uji Nhật Bản đánh bông cùng sữa tươi thanh trùng và lớp foam kem mịn.",
                    "https://images.unsplash.com/photo-1536256263959-770b48d82b0a?auto=format&fit=crop&w=600&q=80"
                ),
                (
                    "Croissant Bơ Tươi Nướng",
                    "32.000đ",
                    "Bánh sừng bò ngàn lớp nướng mới mỗi ngày, vỏ giòn rụm thơm lừng hương bơ Pháp.",
                    "https://images.unsplash.com/photo-1555507036-ab1f4038808a?auto=format&fit=crop&w=600&q=80"
                )
            ]
            for idx, item in enumerate(default_hot_items):
                cursor.execute('''
                    INSERT INTO hot_items (name, price, description, image_url, order_num)
                    VALUES (?, ?, ?, ?, ?)
                ''', (item[0], item[1], item[2], item[3], idx + 1))
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('hot_items_seeded', '1')")

    conn.commit()
    conn.close()

def get_bank_settings():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings WHERE key IN ('bank_name', 'bank_account', 'bank_owner')")
    rows = dict(cursor.fetchall())
    conn.close()
    return {
        "bank_name": rows.get("bank_name", "MB"),
        "bank_account": rows.get("bank_account", "0939194508"),
        "bank_owner": rows.get("bank_owner", "TIEM CA PHE XUAN")
    }

def set_bank_settings(bank_name, bank_account, bank_owner):
    conn = get_connection()
    cursor = conn.cursor()
    for k, v in [('bank_name', (bank_name or 'MB').strip()), 
                 ('bank_account', (bank_account or '').strip()), 
                 ('bank_owner', (bank_owner or '').strip())]:
        cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?", (k, v, v))
    conn.commit()
    conn.close()
    return True

def get_playback_mode():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'playback_mode'")
    row = cursor.fetchone()
    conn.close()
    return row['value'] if row else 'auto_priority'

def set_playback_mode(mode):
    if mode not in ('auto_priority', 'playlist_only'):
        return False, "Chế độ phát không hợp lệ!"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO settings (key, value) VALUES ('playback_mode', ?) ON CONFLICT(key) DO UPDATE SET value = ?", (mode, mode))
    conn.commit()
    conn.close()
    mode_name = "Bình thường (Ưu tiên khách)" if mode == 'auto_priority' else "Chỉ phát Playlist Quán"
    return True, f"Đã chuyển sang chế độ: {mode_name}!"

def get_active_playlist():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM playlists WHERE is_active = 1 LIMIT 1")
    row = cursor.fetchone()
    if not row:
        cursor.execute("SELECT * FROM playlists ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
    pl = dict(row) if row else None
    conn.close()
    return pl

def add_to_queue(youtube_id, title, thumbnail, duration, channel, user_name, user_id, is_admin=False):
    conn = get_connection()
    cursor = conn.cursor()
    
    # Check if mode is playlist_only
    if not is_admin:
        mode = get_playback_mode()
        if mode == 'playlist_only':
            conn.close()
            return False, "Quán đang ở chế độ phát Playlist Sự kiện/Quán - Tạm dừng nhận bài chọn từ khách!", None
    
    # Check if user already has an active song (waiting or playing), unless is_admin
    if not is_admin:
        cursor.execute('''
            SELECT id, title, status FROM queue 
            WHERE user_id = ? AND status IN ('waiting', 'playing')
        ''', (user_id,))
        existing = cursor.fetchone()
        if existing:
            conn.close()
            status_text = "đang được phát" if existing['status'] == 'playing' else "đang trong hàng đợi"
            return False, f"Bạn đã chọn bài '{existing['title']}' ({status_text}). Mỗi người chỉ được chọn 1 bài tại một thời điểm!", None

    # Get max order_num
    cursor.execute("SELECT COALESCE(MAX(order_num), 0) + 1 FROM queue WHERE status = 'waiting'")
    next_order = cursor.fetchone()[0]

    cursor.execute('''
        INSERT INTO queue (youtube_id, title, thumbnail, duration, channel, user_name, user_id, status, order_num)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?)
    ''', (youtube_id, title, thumbnail, duration, channel, user_name, user_id, next_order))
    
    new_id = cursor.lastrowid
    conn.commit()

    cursor.execute("SELECT * FROM queue WHERE id = ?", (new_id,))
    item = dict(cursor.fetchone())
    conn.close()
    return True, "Thêm bài hát vào hàng đợi thành công!", item

def get_active_queue():
    """Returns currently playing song and all waiting songs sorted by order_num"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Current playing song
    cursor.execute('''
        SELECT * FROM queue 
        WHERE status = 'playing' 
        ORDER BY id DESC LIMIT 1
    ''')
    current_row = cursor.fetchone()
    current = dict(current_row) if current_row else None

    # Waiting songs
    cursor.execute('''
        SELECT * FROM queue 
        WHERE status = 'waiting' 
        ORDER BY order_num ASC, id ASC
    ''')
    waiting = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    next_up = peek_next_song()
    return {
        'current': current,
        'waiting': waiting,
        'next_up': next_up,
        'playback_mode': get_playback_mode(),
        'active_playlist': get_active_playlist()
    }

def get_user_song(user_id):
    """Get active song for a specific user"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM queue 
        WHERE user_id = ? AND status IN ('waiting', 'playing')
        ORDER BY id DESC LIMIT 1
    ''', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_song(song_id, user_id=None, is_admin=False):
    """Delete or cancel a song. Users can only delete their own songs when in waiting queue."""
    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return False, "ID bài hát không hợp lệ!"
        
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM queue WHERE id = ?", (song_id,))
    song = cursor.fetchone()
    if not song:
        conn.close()
        return False, "Không tìm thấy bài hát!"
        
    song_dict = dict(song)
    
    if not is_admin:
        if song_dict['user_id'] != user_id:
            conn.close()
            return False, "Bạn không có quyền xóa bài hát của người khác!"
        if song_dict['status'] == 'playing':
            conn.close()
            return False, "Bài hát đang được phát trên loa, bạn không thể hủy nữa!"
        if song_dict['status'] != 'waiting':
            conn.close()
            return False, "Bài hát này không còn trong hàng đợi để hủy!"

    cursor.execute("UPDATE queue SET status = 'cancelled' WHERE id = ?", (song_id,))
    if song_dict.get('user_id') == 'ADMIN_SCHEDULED':
        cursor.execute("DELETE FROM scheduled_songs WHERE youtube_id = ? AND repeat_daily = 0", (song_dict['youtube_id'],))
    conn.commit()
    conn.close()
    return True, f"Đã xóa bài '{song_dict['title']}'!"

def clear_all_queue(is_admin=False):
    """Admin only: cancel all waiting songs"""
    if not is_admin:
        return False, "Chỉ Admin mới có quyền xóa toàn bộ hàng đợi!"
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE queue SET status = 'cancelled' WHERE status = 'waiting'")
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return True, f"Đã xóa toàn bộ {affected} bài trong hàng đợi!"

def get_all_playlists():
    """Retrieve all playlists with song counts and is_active flag"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.*, (SELECT COUNT(*) FROM playlist_songs WHERE playlist_id = p.id) as song_count
        FROM playlists p
        ORDER BY p.id ASC
    ''')
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def create_playlist(name, description=""):
    """Create a new playlist"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM playlists")
    is_first = (cursor.fetchone()[0] == 0)
    cursor.execute("INSERT INTO playlists (name, description, is_active) VALUES (?, ?, ?)",
                   (name, description, 1 if is_first else 0))
    new_id = cursor.lastrowid
    conn.commit()
    cursor.execute('''
        SELECT p.*, 0 as song_count FROM playlists p WHERE p.id = ?
    ''', (new_id,))
    item = dict(cursor.fetchone())
    conn.close()
    return True, f"Đã tạo playlist '{name}'!", item

def delete_playlist(playlist_id):
    """Delete a playlist and all its songs, and synchronously cancel any corresponding scheduled songs"""
    try:
        playlist_id = int(playlist_id)
    except (ValueError, TypeError):
        return False, "ID playlist không hợp lệ!"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM playlists WHERE id = ?", (playlist_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "Không tìm thấy playlist!"
    was_active = row['is_active']

    # Synchronously cancel active schedules for songs in this playlist
    cursor.execute("""
        UPDATE scheduled_songs 
        SET status = 'cancelled' 
        WHERE youtube_id IN (SELECT youtube_id FROM playlist_songs WHERE playlist_id = ?) 
          AND status != 'cancelled'
    """, (playlist_id,))

    cursor.execute("DELETE FROM playlist_songs WHERE playlist_id = ?", (playlist_id,))
    cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    if was_active:
        cursor.execute("SELECT id FROM playlists ORDER BY id ASC LIMIT 1")
        first = cursor.fetchone()
        if first:
            cursor.execute("UPDATE playlists SET is_active = 1 WHERE id = ?", (first['id'],))
    conn.commit()
    conn.close()
    return True, "Đã xóa playlist thành công!"

def set_active_playlist(playlist_id):
    """Set which playlist should be played on the speaker when queue is empty"""
    try:
        playlist_id = int(playlist_id)
    except (ValueError, TypeError):
        return False, "ID playlist không hợp lệ!", None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE playlists SET is_active = 0")
    cursor.execute("UPDATE playlists SET is_active = 1 WHERE id = ?", (playlist_id,))
    conn.commit()
    cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    row = cursor.fetchone()
    pl = dict(row) if row else None
    conn.close()
    return True, f"Đã chọn phát Playlist: {pl['name'] if pl else ''}!", pl

def get_playlist_songs(playlist_id):
    """Get songs in a specific playlist"""
    try:
        playlist_id = int(playlist_id)
    except (ValueError, TypeError):
        return []
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM playlist_songs 
        WHERE playlist_id = ? 
        ORDER BY order_num ASC, id ASC
    ''', (playlist_id,))
    songs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return songs

def shuffle_playlist_songs(playlist_id):
    """Randomly shuffle the order_num positions of songs in a playlist so they play sequentially top-to-bottom in the new order."""
    try:
        playlist_id = int(playlist_id)
    except (ValueError, TypeError):
        return False, "ID playlist không hợp lệ!", []

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    pl = cursor.fetchone()
    if not pl:
        conn.close()
        return False, "Không tìm thấy playlist!", []

    cursor.execute('''
        SELECT id FROM playlist_songs 
        WHERE playlist_id = ? 
        ORDER BY order_num ASC, id ASC
    ''', (playlist_id,))
    rows = cursor.fetchall()
    original_ids = [r['id'] for r in rows]

    if len(original_ids) < 2:
        cursor.execute('''
            SELECT * FROM playlist_songs 
            WHERE playlist_id = ? 
            ORDER BY order_num ASC, id ASC
        ''', (playlist_id,))
        songs = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return True, "Playlist cần có ít nhất 2 bài hát để trộn vị trí!", songs

    shuffled_ids = list(original_ids)
    for _ in range(10):
        random.shuffle(shuffled_ids)
        if shuffled_ids != original_ids:
            break
    if shuffled_ids == original_ids:
        shuffled_ids = original_ids[1:] + original_ids[:1]

    for idx, song_id in enumerate(shuffled_ids):
        cursor.execute("UPDATE playlist_songs SET order_num = ? WHERE id = ?", (idx + 1, song_id))

    conn.commit()
    cursor.execute('''
        SELECT * FROM playlist_songs 
        WHERE playlist_id = ? 
        ORDER BY order_num ASC, id ASC
    ''', (playlist_id,))
    songs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return True, f"Đã trộn ngẫu nhiên vị trí {len(songs)} bài trong Playlist '{pl['name']}' (phát lần lượt từ trên xuống)!", songs

def add_song_to_playlist(playlist_id, youtube_id, title, thumbnail, duration, channel, check_duplicate=False):
    """Add a song to a specific playlist, with optional duplicate check"""
    try:
        playlist_id = int(playlist_id)
    except (ValueError, TypeError):
        return False, "ID playlist không hợp lệ!", None
    conn = get_connection()
    cursor = conn.cursor()

    if check_duplicate:
        cursor.execute("SELECT id FROM playlist_songs WHERE playlist_id = ? AND youtube_id = ?", (playlist_id, youtube_id))
        if cursor.fetchone():
            cursor.execute("SELECT name FROM playlists WHERE id = ?", (playlist_id,))
            pl_row = cursor.fetchone()
            pl_name = pl_row['name'] if pl_row else ''
            conn.close()
            return False, f"Bài hát '{title}' đã có sẵn trong Playlist '{pl_name}' rồi!", None

    cursor.execute("SELECT COALESCE(MAX(order_num), 0) + 1 FROM playlist_songs WHERE playlist_id = ?", (playlist_id,))
    next_order = cursor.fetchone()[0]
    cursor.execute('''
        INSERT INTO playlist_songs (playlist_id, youtube_id, title, thumbnail, duration, channel, order_num)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (playlist_id, youtube_id, title, thumbnail, duration, channel, next_order))
    new_id = cursor.lastrowid
    conn.commit()
    cursor.execute("SELECT * FROM playlist_songs WHERE id = ?", (new_id,))
    song = dict(cursor.fetchone())
    conn.close()
    return True, "Đã thêm bài hát vào playlist!", song

def delete_song_from_playlist(song_id):
    """Delete a song from a playlist and synchronously cancel any corresponding scheduled song."""
    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return False, "ID bài hát không hợp lệ!", 0
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM playlist_songs WHERE id = ?", (song_id,))
    song = cursor.fetchone()
    if not song:
        conn.close()
        return False, "Không tìm thấy bài hát!", 0

    yid = song['youtube_id']
    cursor.execute("DELETE FROM playlist_songs WHERE id = ?", (song_id,))
    affected = cursor.rowcount

    # Synchronously cancel any active schedule matching this song's youtube_id
    cursor.execute("UPDATE scheduled_songs SET status = 'cancelled' WHERE youtube_id = ? AND status != 'cancelled'", (yid,))
    cancelled_sched = cursor.rowcount

    # Also cancel if it's currently waiting in queue as ADMIN_SCHEDULED
    cursor.execute("UPDATE queue SET status = 'cancelled' WHERE youtube_id = ? AND user_id = 'ADMIN_SCHEDULED' AND status = 'waiting'", (yid,))

    conn.commit()
    conn.close()
    return (affected > 0), ("Đã xóa bài hát khỏi playlist!" if affected > 0 else "Không tìm thấy bài hát!"), cancelled_sched

def peek_next_song():
    """Look ahead to find which song will play next without modifying the database."""
    conn = get_connection()
    cursor = conn.cursor()
    mode = get_playback_mode()

    # 1. Admin scheduled song (always priority 1) OR customer queue if in auto_priority
    cursor.execute('''
        SELECT * FROM queue 
        WHERE status = 'waiting' AND (user_id = 'ADMIN_SCHEDULED' OR ? = 'auto_priority')
        ORDER BY order_num ASC, id ASC 
        LIMIT 1
    ''', (mode,))
    row = cursor.fetchone()
    if row:
        song = dict(row)
        conn.close()
        return song

    # 2. Next song from active playlist
    cursor.execute("SELECT * FROM playlists WHERE is_active = 1 LIMIT 1")
    active_pl = cursor.fetchone()
    if not active_pl:
        cursor.execute("SELECT * FROM playlists ORDER BY id ASC LIMIT 1")
        active_pl = cursor.fetchone()

    if active_pl:
        pl_dict = dict(active_pl)
        cursor.execute('''
            SELECT * FROM playlist_songs 
            WHERE playlist_id = ? 
            ORDER BY order_num ASC, id ASC
        ''', (pl_dict['id'],))
        songs = [dict(row) for row in cursor.fetchall()]

        if songs:
            cursor.execute('''
                SELECT youtube_id FROM queue 
                WHERE user_id IN ('SYSTEM_DEFAULT', 'admin_play') 
                ORDER BY id DESC LIMIT 1
            ''')
            last_played = cursor.fetchone()
            next_song_item = songs[0]

            if last_played:
                last_yid = last_played['youtube_id']
                for idx, item in enumerate(songs):
                    if item['youtube_id'] == last_yid:
                        next_song_item = songs[(idx + 1) % len(songs)]
                        break

            item_copy = dict(next_song_item)
            item_copy['user_id'] = 'SYSTEM_DEFAULT'
            item_copy['user_name'] = f"Playlist: {pl_dict['name']}"
            conn.close()
            return item_copy

    conn.close()
    return None

def next_song(force=False):
    """Advance to next song in queue for speaker.
    Mode 'auto_priority': Priority 1 is customer queue. Priority 2 is active playlist.
    Mode 'playlist_only': Directly plays from active playlist, ignores customer queue."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Auto-clean any one-time schedule whose song has played/finished
    cursor.execute('''
        SELECT youtube_id FROM queue 
        WHERE status = 'playing' AND user_id = 'ADMIN_SCHEDULED'
    ''')
    finishing_sched = cursor.fetchall()
    for fs in finishing_sched:
        cursor.execute('''
            DELETE FROM scheduled_songs 
            WHERE youtube_id = ? AND repeat_daily = 0
        ''', (fs['youtube_id'],))

    # Mark current playing as finished
    cursor.execute('''
        UPDATE queue 
        SET status = 'finished', finished_at = CURRENT_TIMESTAMP 
        WHERE status = 'playing'
    ''')
    
    mode = get_playback_mode()

    # 1. Admin scheduled song (always priority 1) OR waiting song if in auto_priority
    cursor.execute('''
        SELECT * FROM queue 
        WHERE status = 'waiting' AND (user_id = 'ADMIN_SCHEDULED' OR ? = 'auto_priority')
        ORDER BY order_num ASC, id ASC 
        LIMIT 1
    ''', (mode,))
    next_row = cursor.fetchone()
    
    if next_row:
        next_id = next_row['id']
        cursor.execute('''
            UPDATE queue 
            SET status = 'playing', started_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (next_id,))
        # If this song was an ADMIN_SCHEDULED song, auto-delete its one-time schedule now that it is playing!
        if next_row['user_id'] == 'ADMIN_SCHEDULED':
            cursor.execute('''
                DELETE FROM scheduled_songs 
                WHERE youtube_id = ? AND repeat_daily = 0
            ''', (next_row['youtube_id'],))
        conn.commit()
        cursor.execute("SELECT * FROM queue WHERE id = ?", (next_id,))
        song = dict(cursor.fetchone())
        conn.close()
        return song

    # 2. If no customer songs waiting, pick from the currently ACTIVE playlist selected by Admin
    cursor.execute("SELECT * FROM playlists WHERE is_active = 1 LIMIT 1")
    active_pl = cursor.fetchone()
    if not active_pl:
        cursor.execute("SELECT * FROM playlists ORDER BY id ASC LIMIT 1")
        active_pl = cursor.fetchone()
    
    if active_pl:
        pl_dict = dict(active_pl)
        cursor.execute('''
            SELECT * FROM playlist_songs 
            WHERE playlist_id = ? 
            ORDER BY order_num ASC, id ASC
        ''', (pl_dict['id'],))
        songs = [dict(row) for row in cursor.fetchall()]
        
        if songs:
            cursor.execute('''
                SELECT youtube_id FROM queue 
                WHERE user_id IN ('SYSTEM_DEFAULT', 'admin_play') 
                ORDER BY id DESC LIMIT 1
            ''')
            last_played = cursor.fetchone()
            next_song_item = songs[0]
            
            if last_played:
                last_yid = last_played['youtube_id']
                for idx, item in enumerate(songs):
                    if item['youtube_id'] == last_yid:
                        next_song_item = songs[(idx + 1) % len(songs)]
                        break

            cursor.execute('''
                INSERT INTO queue (youtube_id, title, thumbnail, duration, channel, user_name, user_id, status, order_num, started_at)
                VALUES (?, ?, ?, ?, ?, ?, 'SYSTEM_DEFAULT', 'playing', 0, CURRENT_TIMESTAMP)
            ''', (next_song_item['youtube_id'], next_song_item['title'], next_song_item['thumbnail'], next_song_item['duration'], next_song_item['channel'], f"Playlist: {pl_dict['name']}"))
            
            new_id = cursor.lastrowid
            conn.commit()
            cursor.execute("SELECT * FROM queue WHERE id = ?", (new_id,))
            song = dict(cursor.fetchone())
            conn.close()
            return song

    conn.commit()
    conn.close()
    return None

def play_specific_song(song_id, is_admin=False):
    """Admin: play specific song immediately"""
    if not is_admin:
        return False, "Chỉ Admin mới có quyền chọn phát ngay bài này!", None
    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return False, "ID bài hát không hợp lệ!", None
        
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM queue WHERE id = ?", (song_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        return False, "Không tìm thấy bài hát!", None

    # Auto-clean any one-time schedule whose song has played/finished
    cursor.execute("SELECT youtube_id FROM queue WHERE status = 'playing' AND user_id = 'ADMIN_SCHEDULED' AND id != ?", (song_id,))
    for fs in cursor.fetchall():
        cursor.execute("DELETE FROM scheduled_songs WHERE youtube_id = ? AND repeat_daily = 0", (fs['youtube_id'],))

    # Mark OTHER current playing song(s) as finished
    cursor.execute('''
        UPDATE queue 
        SET status = 'finished', finished_at = CURRENT_TIMESTAMP 
        WHERE status = 'playing' AND id != ?
    ''', (song_id,))
    
    # Set target song to playing
    cursor.execute('''
        UPDATE queue 
        SET status = 'playing', started_at = CURRENT_TIMESTAMP 
        WHERE id = ?
    ''', (song_id,))

    if target['user_id'] == 'ADMIN_SCHEDULED':
        cursor.execute("DELETE FROM scheduled_songs WHERE youtube_id = ? AND repeat_daily = 0", (target['youtube_id'],))
    
    conn.commit()
    cursor.execute("SELECT * FROM queue WHERE id = ?", (song_id,))
    song = dict(cursor.fetchone())
    conn.close()
    return True, f"Bắt đầu phát bài: {song['title']}", song

def prioritize_song(song_id, is_admin=False):
    """Admin: move song to top of waiting list"""
    if not is_admin:
        return False, "Chỉ Admin mới có quyền đẩy bài lên đầu!"
    try:
        song_id = int(song_id)
    except (ValueError, TypeError):
        return False, "ID bài hát không hợp lệ!"
        
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT MIN(order_num) FROM queue WHERE status = 'waiting'")
    min_order = cursor.fetchone()[0]
    new_order = (min_order - 1) if min_order is not None else 0
    
    cursor.execute("UPDATE queue SET order_num = ? WHERE id = ?", (new_order, song_id))
    conn.commit()
    conn.close()
    return True, "Đã đẩy bài lên đầu hàng đợi!"

# ----------------------------------------------------
# SCHEDULED SONGS (HẸN GIỜ PHÁT NHẠC)
# ----------------------------------------------------

def add_scheduled_song(youtube_id, title, thumbnail, duration, channel, scheduled_time, scheduled_date=None, repeat_daily=0):
    """Add a new scheduled song"""
    conn = get_connection()
    cursor = conn.cursor()
    
    parts = scheduled_time.strip().split(':')
    if len(parts) >= 2:
        try:
            h = int(parts[0])
            m = int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                conn.close()
                return False, "Thời gian hẹn giờ phải nằm trong khoảng 00:00 đến 23:59!", None
            formatted_time = f"{h:02d}:{m:02d}"
        except Exception:
            conn.close()
            return False, "Thời gian hẹn giờ không hợp lệ!", None
    else:
        conn.close()
        return False, "Thời gian hẹn giờ không đúng định dạng HH:MM!", None
        
    cursor.execute('''
        INSERT INTO scheduled_songs (youtube_id, title, thumbnail, duration, channel, scheduled_time, scheduled_date, repeat_daily, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    ''', (youtube_id, title, thumbnail, duration, channel, formatted_time, scheduled_date, 1 if repeat_daily else 0))
    
    new_id = cursor.lastrowid
    conn.commit()
    cursor.execute("SELECT * FROM scheduled_songs WHERE id = ?", (new_id,))
    item = dict(cursor.fetchone())
    conn.close()
    return True, f"Đã đặt lịch hẹn giờ phát bài '{title}' lúc {formatted_time}!", item

def get_scheduled_songs():
    """Get list of active scheduled songs (pending or daily repeating)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM scheduled_songs 
        WHERE status = 'pending' OR (repeat_daily = 1 AND status != 'cancelled')
        ORDER BY scheduled_time ASC, id DESC
    ''')
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def delete_scheduled_song(schedule_id):
    """Cancel / remove a scheduled song and synchronously remove it from any playlists."""
    try:
        schedule_id = int(schedule_id)
    except (ValueError, TypeError):
        return False, "ID lịch hẹn giờ không hợp lệ!", 0
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM scheduled_songs WHERE id = ?", (schedule_id,))
    sched = cursor.fetchone()
    if not sched:
        conn.close()
        return False, "Không tìm thấy lịch hẹn giờ!", 0

    yid = sched['youtube_id']
    cursor.execute("UPDATE scheduled_songs SET status = 'cancelled' WHERE id = ?", (schedule_id,))
    affected = cursor.rowcount

    # Synchronously remove matching song from playlist_songs if present
    cursor.execute("DELETE FROM playlist_songs WHERE youtube_id = ?", (yid,))
    deleted_pl_songs = cursor.rowcount

    # Also cancel if currently waiting in queue as ADMIN_SCHEDULED
    cursor.execute("UPDATE queue SET status = 'cancelled' WHERE youtube_id = ? AND user_id = 'ADMIN_SCHEDULED' AND status = 'waiting'", (yid,))

    conn.commit()
    conn.close()
    return (affected > 0), ("Đã xóa lịch hẹn giờ!" if affected > 0 else "Không tìm thấy lịch hẹn giờ!"), deleted_pl_songs

def get_due_schedules():
    """Find scheduled songs that should be triggered at current local time"""
    conn = get_connection()
    cursor = conn.cursor()
    
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    now_m = now.hour * 60 + now.minute
    
    cursor.execute('''
        SELECT * FROM scheduled_songs 
        WHERE status = 'pending' OR (repeat_daily = 1 AND (last_triggered_date IS NULL OR last_triggered_date != ?))
    ''', (today_str,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    
    due = []
    for s in rows:
        if s.get('last_triggered_date') == today_str:
            continue
        if not s.get('repeat_daily') and s.get('scheduled_date') and s.get('scheduled_date') != today_str:
            continue
            
        try:
            sh, sm = map(int, s['scheduled_time'].split(':'))
            sched_m = sh * 60 + sm
            diff = now_m - sched_m
            # Allow trigger within 0 to 3 minutes of scheduled time
            if 0 <= diff <= 3:
                due.append(s)
        except Exception as e:
            print(f"Error parsing schedule time {s.get('scheduled_time')}: {e}")
            
    return due

def trigger_scheduled_song(schedule_id):
    """Triggers a scheduled song:
    - If song is currently playing on speaker, keeps it playing.
    - If song is already in waiting queue, jumps to position #1.
    - If song is NOT in queue, inserts at position #1 with user_id = 'ADMIN_SCHEDULED'.
    - Does NOT cut off currently playing song; it waits for current song to finish, then plays!
    """
    try:
        schedule_id = int(schedule_id)
    except (ValueError, TypeError):
        return False, "ID lịch hẹn giờ không hợp lệ!", None
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM scheduled_songs WHERE id = ?", (schedule_id,))
    sched_row = cursor.fetchone()
    if not sched_row:
        conn.close()
        return False, "Không tìm thấy lịch hẹn giờ!", None
        
    sched = dict(sched_row)
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    
    # Update schedule status
    if sched['repeat_daily']:
        cursor.execute("UPDATE scheduled_songs SET last_triggered_date = ? WHERE id = ?", (today_str, schedule_id))
    else:
        cursor.execute("UPDATE scheduled_songs SET status = 'triggered', last_triggered_date = ? WHERE id = ?", (today_str, schedule_id))
    conn.commit()

    # 1. Check if song is currently playing on speaker
    cursor.execute("SELECT * FROM queue WHERE youtube_id = ? AND status = 'playing' ORDER BY id DESC LIMIT 1", (sched['youtube_id'],))
    curr_playing = cursor.fetchone()
    if curr_playing:
        item = dict(curr_playing)
        conn.close()
        return True, f"Bài '{item['title']}' đang được phát trên loa!", item

    # 2. Check min order_num of current waiting songs to place this song at the very top (position #1)
    cursor.execute("SELECT MIN(order_num) FROM queue WHERE status = 'waiting'")
    min_res = cursor.fetchone()[0]
    new_order = (min_res - 1) if min_res is not None else 0

    # 3. Check if song is already in waiting queue
    cursor.execute("SELECT * FROM queue WHERE youtube_id = ? AND status = 'waiting' ORDER BY order_num ASC LIMIT 1", (sched['youtube_id'],))
    existing_waiting = cursor.fetchone()
    if existing_waiting:
        queue_id = existing_waiting['id']
        cursor.execute('''
            UPDATE queue 
            SET order_num = ?, user_id = 'ADMIN_SCHEDULED' 
            WHERE id = ?
        ''', (new_order, queue_id))
        conn.commit()
        cursor.execute("SELECT * FROM queue WHERE id = ?", (queue_id,))
        item = dict(cursor.fetchone())
        conn.close()
        return True, f"Đã đẩy bài '{item['title']}' lên đầu hàng đợi (Ưu tiên hẹn giờ {sched['scheduled_time']})!", item

    # 4. Song is not in waiting queue -> insert at position #1
    sched_label = f"Quán (Hẹn giờ {sched['scheduled_time']})"
    cursor.execute('''
        INSERT INTO queue (youtube_id, title, thumbnail, duration, channel, user_name, user_id, status, order_num)
        VALUES (?, ?, ?, ?, ?, ?, 'ADMIN_SCHEDULED', 'waiting', ?)
    ''', (sched['youtube_id'], sched['title'], sched['thumbnail'], sched['duration'], sched['channel'], sched_label, new_order))
    new_id = cursor.lastrowid
    conn.commit()
    
    cursor.execute("SELECT * FROM queue WHERE id = ?", (new_id,))
    item = dict(cursor.fetchone())
    conn.close()
    return True, f"Đã thêm bài hẹn giờ '{item['title']}' lên đầu hàng đợi ({sched['scheduled_time']})!", item

# ----------------------------------------------------
# HOT ITEMS (MÓN HOT / MENU NỔI BẬT)
# ----------------------------------------------------

import json

DEFAULT_MENU_CATEGORIES = [
    "Cà Phê",
    "Rau Má – Sữa Đậu",
    "Trà",
    "Nước Ép",
    "Đá Xay",
    "Trà Sữa",
    "Soda",
    "Sinh Tố",
    "Sữa Chua"
]

def _ensure_hot_items_columns(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE hot_items ADD COLUMN category TEXT DEFAULT 'Cà Phê'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE hot_items ADD COLUMN is_hot INTEGER DEFAULT 1")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE hot_items ADD COLUMN is_new INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    # Migrate legacy English codes or infer category for seeded items if needed
    cursor.execute("SELECT id, name, category FROM hot_items")
    rows = cursor.fetchall()
    updated = False
    for r in rows:
        cat = (r['category'] or '').strip()
        name_lower = (r['name'] or '').lower()
        new_cat = cat
        if cat in ('', 'coffee'):
            if 'trà' in name_lower or 'đào' in name_lower or 'vải' in name_lower or 'chanh' in name_lower:
                new_cat = "Trà"
            elif 'matcha' in name_lower or 'latte' in name_lower or 'sữa' in name_lower or 'cacao' in name_lower:
                new_cat = "Sữa & Matcha"
            elif 'bánh' in name_lower or 'croissant' in name_lower or 'hạt' in name_lower:
                new_cat = "Bánh & Ăn Vặt"
            else:
                new_cat = "Cà Phê"
        elif cat == 'tea':
            new_cat = "Trà"
        elif cat == 'milk':
            new_cat = "Sữa & Matcha"
        elif cat == 'snack':
            new_cat = "Bánh & Ăn Vặt"
        if new_cat != cat:
            cursor.execute("UPDATE hot_items SET category = ? WHERE id = ?", (new_cat, r['id']))
            updated = True
    if updated:
        conn.commit()

def get_menu_categories():
    """Retrieve list of custom menu categories configured by Admin"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'menu_categories'")
    row = cursor.fetchone()
    conn.close()
    if row and row['value']:
        try:
            cats = json.loads(row['value'])
            if isinstance(cats, list) and len(cats) > 0:
                return [str(c).strip() for c in cats if str(c).strip()]
        except Exception:
            pass
    return list(DEFAULT_MENU_CATEGORIES)

def save_menu_categories(categories_list):
    """Save custom menu categories list"""
    cleaned = []
    for c in (categories_list or []):
        s = str(c).strip()[:40]
        if s and s not in cleaned:
            cleaned.append(s)
    if not cleaned:
        cleaned = list(DEFAULT_MENU_CATEGORIES)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('menu_categories', ?)",
        (json.dumps(cleaned, ensure_ascii=False),)
    )
    conn.commit()
    conn.close()
    return cleaned

def toggle_hot_item_status(item_id, is_hot=None):
    """Quickly toggle or set is_hot status for a menu item"""
    try:
        item_id = int(item_id)
    except (ValueError, TypeError):
        return False, "ID món không hợp lệ!", None
    conn = get_connection()
    _ensure_hot_items_columns(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (item_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        return False, "Không tìm thấy món này!", None
    existing_dict = dict(existing)
    current_hot = 1 if existing_dict.get('is_hot', 1) else 0
    new_hot = (1 if is_hot else 0) if is_hot is not None else (0 if current_hot == 1 else 1)
    
    # If unsetting hot (new_hot == 0), also unset is_new (since new items must always be hot)
    if new_hot == 0:
        cursor.execute("UPDATE hot_items SET is_hot = 0, is_new = 0 WHERE id = ?", (item_id,))
    else:
        cursor.execute("UPDATE hot_items SET is_hot = 1 WHERE id = ?", (item_id,))
        
    conn.commit()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (item_id,))
    item = dict(cursor.fetchone())
    conn.close()
    status_str = "đã đưa lên ô HOT" if new_hot else "đã ẩn khỏi ô HOT (vẫn hiện trong Menu)"
    return True, f"Món '{item['name']}' {status_str}!", item

def toggle_new_item_status(item_id, is_new=None):
    """Quickly toggle or set is_new status for a menu item. If marked as new, it automatically becomes HOT."""
    try:
        item_id = int(item_id)
    except (ValueError, TypeError):
        return False, "ID món không hợp lệ!", None
    conn = get_connection()
    _ensure_hot_items_columns(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (item_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        return False, "Không tìm thấy món này!", None
    existing_dict = dict(existing)
    current_new = 1 if existing_dict.get('is_new', 0) else 0
    new_status = (1 if is_new else 0) if is_new is not None else (0 if current_new == 1 else 1)
    
    # If is_new == 1, auto-enable is_hot as well
    if new_status == 1:
        cursor.execute("UPDATE hot_items SET is_new = 1, is_hot = 1 WHERE id = ?", (item_id,))
    else:
        cursor.execute("UPDATE hot_items SET is_new = 0 WHERE id = ?", (item_id,))
        
    conn.commit()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (item_id,))
    item = dict(cursor.fetchone())
    conn.close()
    status_str = "đã đánh dấu là MÓN MỚI (Super Saiyan) & tự động đưa lên HOT" if new_status else "đã bỏ đánh dấu Món Mới"
    return True, f"Món '{item['name']}' {status_str}!", item

def get_hot_items():
    """Retrieve all hot/menu items ordered by is_new DESC, order_num ASC, id DESC"""
    conn = get_connection()
    _ensure_hot_items_columns(conn)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM hot_items
        ORDER BY is_new DESC, order_num ASC, id DESC
    ''')
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def add_hot_item(name, price, description, image_url, category='coffee', is_hot=1, is_new=0):
    """Add a new hot/menu item"""
    conn = get_connection()
    _ensure_hot_items_columns(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(order_num), 0) + 1 FROM hot_items")
    next_order = cursor.fetchone()[0]
    final_new = 1 if is_new else 0
    final_hot = 1 if (final_new or is_hot) else 0
    cursor.execute('''
        INSERT INTO hot_items (name, price, description, image_url, category, is_hot, is_new, order_num)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (name, price, description, image_url, category or 'coffee', final_hot, final_new, next_order))
    new_id = cursor.lastrowid
    conn.commit()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (new_id,))
    item = dict(cursor.fetchone())
    conn.close()
    return True, f"Đã thêm món '{name}' vào Menu thành công!", item

def update_hot_item(item_id, name, price, description, image_url=None, category=None, is_hot=None, is_new=None):
    """Update an existing hot/menu item"""
    try:
        item_id = int(item_id)
    except (ValueError, TypeError):
        return False, "ID món không hợp lệ!", None
    conn = get_connection()
    _ensure_hot_items_columns(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (item_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        return False, "Không tìm thấy món này!", None

    existing_dict = dict(existing)
    final_img = image_url if image_url else existing_dict['image_url']
    final_cat = category if category else existing_dict.get('category', 'coffee')
    final_new = (1 if is_new else 0) if is_new is not None else existing_dict.get('is_new', 0)
    if is_hot is not None:
        final_hot = 1 if is_hot else 0
    else:
        final_hot = existing_dict.get('is_hot', 1)
    if final_new == 1:
        final_hot = 1

    cursor.execute('''
        UPDATE hot_items
        SET name = ?, price = ?, description = ?, image_url = ?, category = ?, is_hot = ?, is_new = ?
        WHERE id = ?
    ''', (name, price, description, final_img, final_cat, final_hot, final_new, item_id))
    conn.commit()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (item_id,))
    item = dict(cursor.fetchone())
    conn.close()
    return True, f"Đã cập nhật món '{name}'!", item

def delete_hot_item(item_id):
    """Delete a hot item"""
    try:
        item_id = int(item_id)
    except (ValueError, TypeError):
        return False, "ID món không hợp lệ!"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM hot_items WHERE id = ?", (item_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        return False, "Không tìm thấy món cần xóa!"
    cursor.execute("DELETE FROM hot_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return True, f"Đã xóa món '{existing['name']}' khỏi danh sách HOT!"



