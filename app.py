import os, sys, shutil, tempfile, sqlite3, subprocess, time, ast, re, zipfile
from datetime import datetime
import telebot
from telebot import types

try:
    import psutil
    PSUTIL = True
except ImportError:
    PSUTIL = False

BOT_TOKEN   = "8733440452:AAGZSroDubpgFfSRnBGfKmsmW4GqqzhnCqk"
ADMIN_IDS   = [123456789]
SUPPORT_LINK = "https://t.me/sarsamanxdd"
DB_PATH     = "panel.db"
BOTS_DIR    = "user_bots"
LOGS_DIR    = "bot_logs"

PACKAGES = {
    "free":     {"name": "Ücretsiz",       "max_bots": 1,  "daily_ops": 5,   "zip_rights": 0,  "price": "Ücretsiz"},
    "standart": {"name": "Standart Paket", "max_bots": 6,  "daily_ops": 30,  "zip_rights": 3,  "price": "₺49/ay"},
    "pro":      {"name": "Pro Paket",      "max_bots": 15, "daily_ops": 100, "zip_rights": 10, "price": "₺99/ay"},
    "premium":  {"name": "Premium Paket",  "max_bots": 30, "daily_ops": 500, "zip_rights": 30, "price": "₺199/ay"},
}

UPLOAD_WARNING = (
    "🚫 <b>YASAK MODÜLLER &amp; İMPORTLAR</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "Bot dosyanızda aşağıdaki modüller/importlar kesinlikle yasaktır.\n"
    "Tespit edilmesi durumunda hesabınız kalıcı olarak banlanır ve tüm botlarınız silinir.\n\n"
    "❌ subprocess\n"
    "❌ os.system / os.popen / os.execv / os.execve / os.execl\n"
    "❌ multiprocessing\n"
    "❌ ctypes\n"
    "❌ __import__\n"
    "❌ eval() / exec() / compile()\n"
    "❌ importlib\n"
    "❌ pickle.loads\n"
    "❌ socket.socket / socket.connect / socket.bind\n"
    "❌ shutil.copy / shutil.move\n"
    "❌ urllib.request / urllib.urlopen\n"
    "❌ httpx.get / httpx.post / httpx.Client\n"
    "❌ pathlib.Path.read / pathlib.Path.write\n"
    "❌ base64.b64decode / zlib.decompress\n"
    "❌ marshal.loads / codecs.decode\n\n"
    "⚠️ Kod gizleme (obfuscation), filigranı atlatma ve Telegram API'ye "
    "direkt bağlantı girişimleri de yasaktır ve anında ban tetikler.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📤 Yukarıdaki kuralları okuduysanız <b>.py</b> veya <b>.zip</b> dosyanızı gönderin:"
)

BANNED_IMPORTS = {"subprocess","multiprocessing","ctypes","importlib","socket","shutil","marshal","pickle","httpx"}
BANNED_CALLS   = {"eval","exec","compile","__import__"}
BANNED_REGEX   = [
    (r"os\s*\.\s*(system|popen|execv|execve|execl|execle|execlp|execvp|execvpe)\s*\(", "os.system/popen/exec"),
    (r"urllib\s*\.\s*(request|urlopen)",                                                "urllib.request/urlopen"),
    (r"pickle\s*\.\s*loads\s*\(",                                                       "pickle.loads()"),
    (r"socket\s*\.\s*(socket|connect|bind)\s*\(",                                       "socket kullanımı"),
    (r"shutil\s*\.\s*(copy|move|rmtree)\s*\(",                                          "shutil.copy/move"),
    (r"base64\s*\.\s*b64decode\s*\(",                                                   "base64.b64decode()"),
    (r"zlib\s*\.\s*decompress\s*\(",                                                    "zlib.decompress()"),
    (r"marshal\s*\.\s*loads\s*\(",                                                      "marshal.loads()"),
    (r"codecs\s*\.\s*decode\s*\(",                                                      "codecs.decode()"),
    (r"httpx\s*\.\s*(get|post|Client)\s*\(",                                            "httpx kullanımı"),
    (r"pathlib\s*\.\s*Path.*\.(read_text|write_text|read_bytes|write_bytes)\s*\(",      "pathlib read/write"),
]
OBFUSC_REGEX = [
    (r"exec\s*\(\s*base64",                   "exec(base64) → gizlenmiş kod"),
    (r"eval\s*\(\s*base64",                   "eval(base64) → gizlenmiş kod"),
    (r"exec\s*\(\s*zlib",                     "exec(zlib) → sıkıştırılmış gizli kod"),
    (r"exec\s*\(\s*bytes\s*\.fromhex",        "exec(bytes.fromhex) → hex gizleme"),
    (r"eval\s*\(\s*__import__",               "eval(__import__) → dinamik import"),
    (r"__builtins__\s*[\[.]",                 "__builtins__ erişimi"),
    (r"getattr\s*\(\s*__builtins__",          "getattr(__builtins__) → builtin bypass"),
    (r"chr\s*\(\d+\)\s*\+\s*chr\s*\(\d+\)",  "chr() birleştirme → karakter gizleme"),
    (r"lambda\s*[^:]+:\s*__import__",         "lambda + __import__ gizleme"),
    (r"[A-Za-z0-9+/]{300,}={0,2}",           "Gömülü base64 bloğu (300+ karakter)"),
    (r"(\\x[0-9a-fA-F]{2}){8,}",             "Ardışık hex escape → string gizleme"),
]
EXFIL_SOLO = [
    (r"/sdcard/|/storage/emulated/|/data/data/com\.termux", "Android sistem dizini erişimi"),
    (r"DCIM|WhatsApp.Media|Telegram.*Video|Telegram.*Images","Medya klasörü tarama"),
    (r"api\.telegram\.org.*(sendDocument|sendPhoto|sendVideo|sendAudio)", "Telegram API'ye direkt dosya gönderme"),
]

def scan_code(code):
    violations, obfusc = [], []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in BANNED_IMPORTS:
                        violations.append(f"Yasak import: {a.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in BANNED_IMPORTS:
                    violations.append(f"Yasak import: from {node.module}")
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id in BANNED_CALLS:
                    violations.append(f"Yasak fonksiyon: {f.id}()")
                elif isinstance(f, ast.Attribute) and f.attr in BANNED_CALLS:
                    violations.append(f"Yasak fonksiyon: .{f.attr}()")
    except SyntaxError as e:
        violations.append(f"Syntax hatası: {e}")

    for pat, lbl in BANNED_REGEX:
        if re.search(pat, code, re.IGNORECASE):
            violations.append(f"Yasak kullanım: {lbl}")

    if re.search(r"os\s*\.\s*walk\s*\(", code) and re.search(r"sendDocument|sendPhoto|sendVideo", code, re.IGNORECASE):
        violations.append("Veri sızdırma: os.walk + Telegram dosya gönderme")
    for pat, lbl in EXFIL_SOLO:
        if re.search(pat, code, re.IGNORECASE):
            violations.append(f"Şüpheli: {lbl}")

    for pat, lbl in OBFUSC_REGEX:
        if re.search(pat, code, re.IGNORECASE):
            obfusc.append(lbl)

    return list(dict.fromkeys(violations)), obfusc

def scan_file(path):
    try:
        code = open(path, "r", encoding="utf-8", errors="replace").read()
    except Exception as e:
        return [f"Dosya okunamadı: {e}"], [], False
    v, o = scan_code(code)
    return v, o, bool(o)

def scan_zip(path):
    all_v, all_o, encoded = [], [], False
    try:
        with zipfile.ZipFile(path, "r") as zf:
            py_files = [n for n in zf.namelist() if n.endswith(".py")]
            if not py_files:
                return [], [], False
            with tempfile.TemporaryDirectory() as td:
                for member in py_files:
                    norm = os.path.normpath(member)
                    if norm.startswith("..") or os.path.isabs(norm):
                        all_v.append("Path traversal girişimi")
                        continue
                    target = os.path.join(td, os.path.basename(member))
                    with zf.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    v, o, enc = scan_file(target)
                    all_v += v; all_o += o
                    if enc: encoded = True
    except zipfile.BadZipFile:
        all_v.append("Geçersiz ZIP dosyası")
    except Exception as e:
        all_v.append(f"ZIP tarama hatası: {e}")
    return list(dict.fromkeys(all_v)), all_o, encoded

def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', full_name TEXT DEFAULT '',
            is_banned INTEGER DEFAULT 0, warning_count INTEGER DEFAULT 0,
            package TEXT DEFAULT 'free', expire_date TEXT,
            daily_ops INTEGER DEFAULT 0, last_op_date TEXT, zip_rights INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bots(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
            file_path TEXT, log_path TEXT, status TEXT DEFAULT 'stopped',
            pid INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bot_data_files(
            id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id INTEGER,
            file_name TEXT, file_path TEXT, uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        c.commit()

def db_get_user(uid):
    with db_conn() as c: return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
def db_get_user_by_uname(uname):
    with db_conn() as c: return c.execute("SELECT * FROM users WHERE username=?", (uname.lstrip("@"),)).fetchone()
def db_upsert_user(uid, uname, fname):
    with db_conn() as c:
        if not c.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone():
            c.execute("INSERT INTO users(user_id,username,full_name) VALUES(?,?,?)", (uid, uname, fname)); c.commit()
def db_get_bots(uid):
    with db_conn() as c: return c.execute("SELECT * FROM bots WHERE user_id=?", (uid,)).fetchall()
def db_get_bot(bid):
    with db_conn() as c: return c.execute("SELECT * FROM bots WHERE id=?", (bid,)).fetchone()
def db_get_running():
    with db_conn() as c: return c.execute("SELECT * FROM bots WHERE status='running'").fetchall()
def db_add_bot(uid, name, fp, lp):
    with db_conn() as c:
        cur = c.execute("INSERT INTO bots(user_id,name,file_path,log_path) VALUES(?,?,?,?)", (uid, name, fp, lp)); c.commit(); return cur.lastrowid
def db_update_bot(bid, status, pid=None):
    with db_conn() as c:
        if pid is not None: c.execute("UPDATE bots SET status=?,pid=? WHERE id=?", (status, pid, bid))
        else: c.execute("UPDATE bots SET status=?,pid=NULL WHERE id=?", (status, bid))
        c.commit()
def db_del_bot(bid):
    with db_conn() as c:
        c.execute("DELETE FROM bots WHERE id=?", (bid,)); c.execute("DELETE FROM bot_data_files WHERE bot_id=?", (bid,)); c.commit()
def db_add_warning(uid):
    with db_conn() as c:
        c.execute("UPDATE users SET warning_count=warning_count+1 WHERE user_id=?", (uid,)); c.commit()
        return c.execute("SELECT warning_count FROM users WHERE user_id=?", (uid,)).fetchone()[0]
def db_ban(uid):
    with db_conn() as c: c.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,)); c.commit()
def db_unban(uid):
    with db_conn() as c: c.execute("UPDATE users SET is_banned=0,warning_count=0 WHERE user_id=?", (uid,)); c.commit()
def db_set_pkg(uid, pkg, exp, zr):
    with db_conn() as c: c.execute("UPDATE users SET package=?,expire_date=?,zip_rights=? WHERE user_id=?", (pkg, exp, zr, uid)); c.commit()
def db_all_users():
    with db_conn() as c: return c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
def db_stats():
    with db_conn() as c:
        return {
            "total_users":   c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "total_bots":    c.execute("SELECT COUNT(*) FROM bots").fetchone()[0],
            "running_bots":  c.execute("SELECT COUNT(*) FROM bots WHERE status='running'").fetchone()[0],
            "banned_users":  c.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0],
            "premium_users": c.execute("SELECT COUNT(*) FROM users WHERE package!='free'").fetchone()[0],
        }
def db_add_data(bid, fname, fpath):
    with db_conn() as c: c.execute("INSERT INTO bot_data_files(bot_id,file_name,file_path) VALUES(?,?,?)", (bid, fname, fpath)); c.commit()
def db_del_data(bid):
    with db_conn() as c:
        files = c.execute("SELECT file_path FROM bot_data_files WHERE bot_id=?", (bid,)).fetchall()
        c.execute("DELETE FROM bot_data_files WHERE bot_id=?", (bid,)); c.commit()
        return [f[0] for f in files]

_procs = {}

def runner_start(bot_id, fp, lp):
    try:
        if bot_id in _procs and _procs[bot_id].poll() is None:
            return False, "Bot zaten çalışıyor"
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        lf = open(lp, "a", encoding="utf-8")
        lf.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] Bot başlatıldı\n"); lf.flush()
        proc = subprocess.Popen([sys.executable, fp], stdout=lf, stderr=lf, cwd=os.path.dirname(fp))
        _procs[bot_id] = proc
        db_update_bot(bot_id, "running", proc.pid)
        return True, proc.pid
    except Exception as e:
        db_update_bot(bot_id, "error"); return False, str(e)

def runner_stop(bot_id):
    try:
        proc = _procs.get(bot_id)
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill()
            _procs.pop(bot_id, None)
        elif PSUTIL:
            b = db_get_bot(bot_id)
            if b and b["pid"]:
                try: psutil.Process(b["pid"]).terminate()
                except psutil.NoSuchProcess: pass
        db_update_bot(bot_id, "stopped")
        return True, "Bot durduruldu"
    except Exception as e:
        return False, str(e)

def runner_stop_all(uid):
    for b in db_get_bots(uid):
        if b["status"] == "running": runner_stop(b["id"])

def runner_stats(bot_id):
    s = {"status": "stopped", "ram_mb": 0, "cpu_percent": 0, "uptime": "—", "disk_kb": 0}
    b = db_get_bot(bot_id)
    if not b: return s
    s["status"] = b["status"]
    if b["file_path"] and os.path.exists(b["file_path"]):
        s["disk_kb"] = round(os.path.getsize(b["file_path"]) / 1024, 1)
    if not PSUTIL or not b["pid"]: return s
    try:
        p = psutil.Process(b["pid"])
        if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
            s["ram_mb"] = round(p.memory_info().rss / 1024 / 1024, 1)
            s["cpu_percent"] = round(p.cpu_percent(interval=0.5), 1)
            e = int(time.time() - p.create_time())
            h, r = divmod(e, 3600); m, sec = divmod(r, 60)
            s["uptime"] = f"{h}s {m}d {sec}sn"
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        db_update_bot(bot_id, "stopped"); s["status"] = "stopped"
    return s

def runner_logs(lp, lines=60):
    if not lp or not os.path.exists(lp): return "Henüz log yok."
    try:
        with open(lp, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:]) or "Log dosyası boş."
    except Exception as e:
        return f"Log okunamadı: {e}"

def sync_states():
    for b in db_get_running():
        alive = False
        if PSUTIL and b["pid"]:
            try: alive = psutil.Process(b["pid"]).is_running()
            except psutil.NoSuchProcess: pass
        if not alive: db_update_bot(b["id"], "stopped")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
_states = {}

def _main_kb(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📤 Bot Yükle", callback_data="upload_bot"),
        types.InlineKeyboardButton("🤖 Botlarım",  callback_data="my_bots"),
        types.InlineKeyboardButton("💎 Premium",   callback_data="premium"),
        types.InlineKeyboardButton("👤 Profilim",  callback_data="profile"),
        types.InlineKeyboardButton("💬 Destek",    callback_data="support"),
        types.InlineKeyboardButton("❓ Yardım",    callback_data="help_menu"),
    )
    if uid in ADMIN_IDS:
        kb.add(types.InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel"))
    return kb

def _back(cb="main_menu"):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 Geri", callback_data=cb))
    return kb

def _edit(cid, mid, txt, kb=None):
    try: bot.edit_message_text(txt, cid, mid, reply_markup=kb)
    except Exception: bot.send_message(cid, txt, reply_markup=kb)

def _is_banned(uid):
    u = db_get_user(uid)
    return bool(u and u["is_banned"])

def _days_left(exp):
    if not exp: return None
    try: return (datetime.strptime(exp, "%d.%m.%Y") - datetime.now()).days
    except: return None

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid = msg.from_user.id
    db_upsert_user(uid, msg.from_user.username or "", msg.from_user.full_name or "")
    _states.pop(uid, None)
    if _is_banned(uid):
        bot.send_message(uid, f"🔴 Hesabınız banlanmıştır.\n💬 {SUPPORT_LINK}"); return
    bot.send_message(uid,
        "👋 <b>VANTORIUM VDS Panel'e Hoş Geldiniz!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 Bot yükle, başlat, durdur\n"
        "📊 İstatistik ve log takibi\n"
        "💎 Premium paketler\n\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=_main_kb(uid))

@bot.message_handler(commands=["admin"])
def cmd_admin(msg):
    if msg.from_user.id not in ADMIN_IDS: return
    _show_admin(msg.from_user.id, msg.chat.id)

def _show_admin(uid, cid, mid=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📢 Duyuru",        callback_data="adm_broadcast"),
        types.InlineKeyboardButton("🤖 Bot Sil",       callback_data="adm_del_bot"),
        types.InlineKeyboardButton("📊 İstatistik",    callback_data="adm_stats"),
        types.InlineKeyboardButton("👥 Kullanıcılar",  callback_data="adm_users"),
        types.InlineKeyboardButton("🔍 Kullanıcı Ara", callback_data="adm_find"),
        types.InlineKeyboardButton("🔙 Ana Menü",      callback_data="main_menu"),
    )
    txt = "⚙️ <b>ADMİN PANELİ</b>\n━━━━━━━━━━━━━━━━━━━━"
    if mid: _edit(cid, mid, txt, kb)
    else: bot.send_message(cid, txt, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: True)
def on_cb(call):
    uid, cid, mid, data = call.from_user.id, call.message.chat.id, call.message.message_id, call.data
    bot.answer_callback_query(call.id)
    db_upsert_user(uid, call.from_user.username or "", call.from_user.full_name or "")

    if _is_banned(uid) and data not in ("support", "main_menu"):
        bot.send_message(cid, f"🔴 Hesabınız banlanmıştır.\n💬 {SUPPORT_LINK}"); return

    if data == "main_menu":
        _states.pop(uid, None); _edit(cid, mid, "🏠 <b>Ana Menü</b>", _main_kb(uid))

    elif data == "upload_bot":
        _states[uid] = {"state": "wait_bot"}; _edit(cid, mid, UPLOAD_WARNING, _back("main_menu"))

    elif data == "my_bots":
        _show_bots(uid, cid, mid)

    elif data.startswith("bmenu_"):
        _show_bot_menu(uid, cid, mid, int(data[6:]))

    elif data.startswith("bstart_"):
        b = db_get_bot(int(data[7:]))
        if not b or b["user_id"] != uid: return
        ok, res = runner_start(b["id"], b["file_path"], b["log_path"])
        bot.send_message(cid, f"{'✅ Bot başlatıldı (PID: ' + str(res) + ')' if ok else '❌ ' + str(res)}")
        _show_bot_menu(uid, cid, mid, b["id"])

    elif data.startswith("bstop_"):
        b = db_get_bot(int(data[6:]))
        if not b or b["user_id"] != uid: return
        ok, msg_text = runner_stop(b["id"])
        bot.send_message(cid, f"{'✅' if ok else '⚠️'} {msg_text}")
        _show_bot_menu(uid, cid, mid, b["id"])

    elif data.startswith("bstats_"):
        bid = int(data[7:]); b = db_get_bot(bid)
        if not b or b["user_id"] != uid: return
        s = runner_stats(bid)
        icon = "✅" if s["status"] == "running" else "⛔"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔄 Yenile", callback_data=f"bstats_{bid}"))
        kb.add(types.InlineKeyboardButton("🔙 Geri",   callback_data=f"bmenu_{bid}"))
        _edit(cid, mid,
            f"📊 <b>İSTATİSTİK: {b['name']}</b>\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Durum: {icon} {s['status']}\n🧠 RAM: {s['ram_mb']} MB\n"
            f"⚡ CPU: {s['cpu_percent']}%\n⏱ Uptime: {s['uptime']}\n💾 Dosya: {s['disk_kb']} KB\n"
            "━━━━━━━━━━━━━━━━━━━━", kb)

    elif data.startswith("blogs_"):
        bid = int(data[6:]); b = db_get_bot(bid)
        if not b or b["user_id"] != uid: return
        logs = runner_logs(b["log_path"])
        if len(logs) > 3500: logs = "...\n" + logs[-3500:]
        bot.send_message(cid, f"📋 <b>LOGLAR: {b['name']}</b>\n\n<pre>{logs}</pre>", reply_markup=_back(f"bmenu_{bid}"))

    elif data.startswith("bdup_"):
        bid = int(data[5:]); _states[uid] = {"state": "wait_data", "bot_id": bid}
        _edit(cid, mid,
            "📤 <b>Veritabanı Dosyasını Gönderin</b>\n\n"
            "Yüklemek istediğiniz <b>.json</b>, <b>.db</b> veya <b>.sqlite</b> dosyasını gönderin.\n\n"
            "⚠️ Mevcut dosyanın üzerine yazılır!", _back(f"bmenu_{bid}"))

    elif data.startswith("bddel_"):
        bid = int(data[6:])
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✅ Evet Sil", callback_data=f"bddel_ok_{bid}"),
               types.InlineKeyboardButton("❌ İptal",    callback_data=f"bmenu_{bid}"))
        _edit(cid, mid, "⚠️ Tüm veri dosyaları silinecek. Emin misiniz?", kb)

    elif data.startswith("bddel_ok_"):
        bid = int(data[9:])
        for p in db_del_data(bid):
            if os.path.exists(p): os.remove(p)
        bot.send_message(cid, "✅ Veri dosyaları silindi.")
        _show_bot_menu(uid, cid, mid, bid)

    elif data.startswith("bdel_"):
        bid = int(data[5:])
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✅ Evet Sil", callback_data=f"bdel_ok_{bid}"),
               types.InlineKeyboardButton("❌ İptal",    callback_data=f"bmenu_{bid}"))
        _edit(cid, mid, "🗑 Bot kalıcı olarak silinecek. Emin misiniz?", kb)

    elif data.startswith("bdel_ok_"):
        bid = int(data[8:]); b = db_get_bot(bid)
        if not b or b["user_id"] != uid: return
        runner_stop(bid)
        for p in [b["file_path"], b["log_path"]]:
            if p and os.path.exists(p): os.remove(p)
        bot_dir = os.path.dirname(b["file_path"]) if b["file_path"] else None
        if bot_dir and os.path.isdir(bot_dir):
            try: shutil.rmtree(bot_dir)
            except: pass
        db_del_bot(bid)
        bot.send_message(cid, "✅ Bot ve tüm verileri silindi.")
        _show_bots(uid, cid, mid)

    elif data == "premium":
        lines = ["💎 <b>PREMIUM PAKETLER</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
        for k, p in PACKAGES.items():
            if k == "free": continue
            lines.append(f"<b>{p['name']}</b> — {p['price']}\n🤖 {p['max_bots']} bot  |  🔢 {p['daily_ops']} işlem  |  📦 {p['zip_rights']} ZIP\n")
        lines.append("━━━━━━━━━━━━━━━━━━━━\nSatın almak için destek hattımıza yazın.")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💬 Satın Al", url=SUPPORT_LINK))
        kb.add(types.InlineKeyboardButton("🔙 Geri", callback_data="main_menu"))
        _edit(cid, mid, "\n".join(lines), kb)

    elif data == "profile":
        u = db_get_user(uid)
        if not u: return
        pkg = PACKAGES.get(u["package"], PACKAGES["free"])
        bots = db_get_bots(uid)
        active = sum(1 for b in bots if b["status"] == "running")
        days = _days_left(u["expire_date"])
        exp = f"{u['expire_date']} ({days} gün kaldı ✅)" if days and days > 0 else (u["expire_date"] or "—")
        st  = "🔴 Banlı" if u["is_banned"] else ("✅ Aktif Ücretli" if u["package"] != "free" else "⚪ Ücretsiz")
        _edit(cid, mid,
            f"👤 <b>PROFİL</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 ID: <code>{uid}</code>\n🌍 Dil: TR\n📊 Durum: {st}\n\n"
            f"📦 Paket: {pkg['name']}\n📅 Bitiş: {exp}\n"
            f"🤖 Botların: {len(bots)} / {pkg['max_bots']} ({active} aktif)\n"
            f"🔢 Günlük İşlem: {u['daily_ops']} / {pkg['daily_ops']}\n"
            f"📦 ZIP Hakkı: {u['zip_rights']} / {pkg['zip_rights']}\n"
            "━━━━━━━━━━━━━━━━━━━━", _back("main_menu"))

    elif data == "support":
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💬 Destek Kanalı", url=SUPPORT_LINK))
        kb.add(types.InlineKeyboardButton("🔙 Geri", callback_data="main_menu"))
        _edit(cid, mid, "💬 <b>DESTEK</b>\n━━━━━━━━━━━━━━━━━━━━\n\nSorularınız için destek kanalımıza ulaşın:", kb)

    elif data == "help_menu":
        _edit(cid, mid,
            "❓ <b>YARDIM MERKEZİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "📤 <b>BOT YÜKLEME</b>\n"
            "Desteklenen format: .py ve .zip\n"
            "Bot dosyanız güvenlik taramasından geçtikten sonra sisteme eklenir.\n\n"
            "🤖 <b>BOT YÖNETİMİ</b>\n"
            "• ▶️ Başlat / ⏹ Durdur\n"
            "• 📊 RAM, CPU, Uptime istatistikleri\n"
            "• 📋 Gerçek zamanlı log takibi\n"
            "• 📤 .json / .db / .sqlite veri yükleme\n"
            "• 🗑 Veri ve bot silme\n\n"
            "🔒 <b>GÜVENLİK SİSTEMİ</b>\n"
            "AST tabanlı + regex tarama kullanılır.\n"
            "Yasak modüller, obfuscation ve veri sızdırma girişimleri tespit edilir.\n"
            "İhlal: 1. Uyarı → 2. Uyarı → 3. Kalıcı Ban\n\n"
            "💎 <b>PREMIUM</b>\n"
            "Daha fazla bot ve işlem hakkı için premium paket alın.\n\n"
            f"💬 <b>DESTEK</b>\n{SUPPORT_LINK}\n━━━━━━━━━━━━━━━━━━━━",
            _back("main_menu"))

    elif data == "admin_panel" and uid in ADMIN_IDS:
        _show_admin(uid, cid, mid)

    elif data == "adm_stats" and uid in ADMIN_IDS:
        s = db_stats()
        _edit(cid, mid,
            f"📊 <b>SİSTEM İSTATİSTİKLERİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Toplam Kullanıcı: {s['total_users']}\n💎 Premium: {s['premium_users']}\n"
            f"🔴 Banlı: {s['banned_users']}\n🤖 Toplam Bot: {s['total_bots']}\n"
            f"✅ Çalışan Bot: {s['running_bots']}\n━━━━━━━━━━━━━━━━━━━━",
            _back("admin_panel"))

    elif data == "adm_broadcast" and uid in ADMIN_IDS:
        _states[uid] = {"state": "adm_broadcast"}
        _edit(cid, mid, "📢 Duyuru mesajınızı gönderin:", _back("admin_panel"))

    elif data == "adm_del_bot" and uid in ADMIN_IDS:
        _states[uid] = {"state": "adm_del_bot"}
        _edit(cid, mid, "🤖 Silmek istediğiniz bot ID'sini gönderin:", _back("admin_panel"))

    elif data == "adm_find" and uid in ADMIN_IDS:
        _states[uid] = {"state": "adm_find"}
        _edit(cid, mid, "🔍 Kullanıcı ID veya @username gönderin:", _back("admin_panel"))

    elif data == "adm_users" and uid in ADMIN_IDS:
        users = db_all_users()[:25]
        lines = [f"{'🔴' if u['is_banned'] else '✅'} <code>{u['user_id']}</code> @{u['username'] or '—'} | {u['package']}" for u in users]
        _edit(cid, mid, "👥 <b>KULLANICILAR</b> (son 25)\n━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(lines), _back("admin_panel"))

    elif data.startswith("adm_ban_") and uid in ADMIN_IDS:
        tid = int(data[8:]); db_ban(tid); runner_stop_all(tid)
        bot.answer_callback_query(call.id, f"✅ {tid} banlandı", show_alert=True)

    elif data.startswith("adm_unban_") and uid in ADMIN_IDS:
        tid = int(data[10:]); db_unban(tid)
        bot.answer_callback_query(call.id, f"✅ {tid} ban kaldırıldı", show_alert=True)

def _show_bots(uid, cid, mid):
    bots = db_get_bots(uid)
    if not bots:
        _edit(cid, mid, "🤖 Henüz yüklenmiş botunuz yok.\n\n📤 Bot yüklemek için <b>Bot Yükle</b>'ye tıklayın.", _back("main_menu")); return
    kb = types.InlineKeyboardMarkup()
    for i, b in enumerate(bots, 1):
        kb.add(types.InlineKeyboardButton(f"{'✅' if b['status']=='running' else '⛔'} {i}. {b['name']}", callback_data=f"bmenu_{b['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 Geri", callback_data="main_menu"))
    _edit(cid, mid, f"🤖 <b>BOTLARIM</b>\n━━━━━━━━━━━━━━━━━━━━\n\nToplam: {len(bots)} bot", kb)

def _show_bot_menu(uid, cid, mid, bot_id):
    b = db_get_bot(bot_id)
    if not b or b["user_id"] != uid: bot.send_message(cid, "❌ Bot bulunamadı."); return
    st = {"running": "✅ Çalışıyor", "error": "⚠️ Hata", "stopped": "⛔ Durduruldu"}.get(b["status"], "⛔ Durduruldu")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("▶️ Başlat", callback_data=f"bstart_{bot_id}"),
           types.InlineKeyboardButton("⏹ Durdur",  callback_data=f"bstop_{bot_id}"))
    kb.add(types.InlineKeyboardButton("📊 Bot İstatistikleri", callback_data=f"bstats_{bot_id}"))
    kb.add(types.InlineKeyboardButton("📋 Loglar",             callback_data=f"blogs_{bot_id}"))
    kb.add(types.InlineKeyboardButton("📤 Veri Yükle", callback_data=f"bdup_{bot_id}"),
           types.InlineKeyboardButton("🗑 Veri Sil",   callback_data=f"bddel_{bot_id}"))
    kb.add(types.InlineKeyboardButton("❌ Botumu Sil", callback_data=f"bdel_{bot_id}"))
    kb.add(types.InlineKeyboardButton("🔙 Geri", callback_data="my_bots"))
    _edit(cid, mid, f"🤖 <b>BOT: {b['name']}</b>\n━━━━━━━━━━━━━━━━━━━━\n📊 Durum: {st}\n📅 Yüklendi: {b['created_at'][:10]}", kb)

@bot.message_handler(content_types=["document"])
def on_doc(msg):
    uid = msg.from_user.id
    if _is_banned(uid): return
    st = _states.get(uid, {})
    if st.get("state") == "wait_bot": _upload_bot(msg, uid)
    elif st.get("state") == "wait_data": _upload_data(msg, uid, st.get("bot_id"))
    else: bot.send_message(uid, "❓ Önce <b>Bot Yükle</b> butonuna tıklayın.")

def _upload_bot(msg, uid):
    fname = msg.document.file_name or "bot.py"
    if not (fname.endswith(".py") or fname.endswith(".zip")):
        bot.send_message(uid, "❌ Yalnızca <b>.py</b> veya <b>.zip</b> dosyası kabul edilir."); return
    u = db_get_user(uid)
    pkg = PACKAGES.get(u["package"], PACKAGES["free"])
    if len(db_get_bots(uid)) >= pkg["max_bots"]:
        bot.send_message(uid, f"❌ Maksimum bot limitine ulaştınız ({pkg['max_bots']}).\nPremium: {SUPPORT_LINK}"); return

    sm = bot.send_message(uid, "🔍 Dosya indiriliyor, güvenlik taraması yapılıyor...")
    try:
        raw = bot.download_file(bot.get_file(msg.document.file_id).file_path)
        ext = ".zip" if fname.endswith(".zip") else ".py"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(raw); tmp_path = tmp.name

        all_v, all_o, encoded = (scan_zip(tmp_path) if ext == ".zip" else scan_file(tmp_path))
        os.unlink(tmp_path)

        if all_v or encoded:
            _handle_violation(uid, sm, all_v, all_o, encoded); return

        _states.pop(uid, None)
        bot_dir   = os.path.join(BOTS_DIR, str(uid))
        os.makedirs(bot_dir, exist_ok=True)
        bot_name  = os.path.splitext(fname)[0]
        file_path = os.path.join(bot_dir, fname)
        log_path  = os.path.join(LOGS_DIR, str(uid), f"{bot_name}.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(file_path, "wb") as f: f.write(raw)
        bid = db_add_bot(uid, bot_name, file_path, log_path)
        try: bot.edit_message_text(f"✅ <b>Bot başarıyla yüklendi!</b>\n\n📁 Ad: {bot_name}\n🆔 ID: {bid}\n\nBotlarım bölümünden başlatabilirsiniz.", uid, sm.message_id)
        except: bot.send_message(uid, f"✅ Bot yüklendi. ID: {bid}")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🤖 Botlarıma Git", callback_data="my_bots"))
        kb.add(types.InlineKeyboardButton("🏠 Ana Menü",      callback_data="main_menu"))
        bot.send_message(uid, "📋 Ne yapmak istersiniz?", reply_markup=kb)
    except Exception as e:
        bot.send_message(uid, f"❌ Hata oluştu: {e}")

def _handle_violation(uid, sm, violations, obfusc, is_encoded):
    new_count = db_add_warning(uid)
    vlist = "\n".join(f"• {v}" for v in (violations + obfusc)[:10])
    if is_encoded or new_count >= 3:
        db_ban(uid); runner_stop_all(uid)
        reason = "Kod gizleme (obfuscation) tespit edildi" if is_encoded else "3. kez kural ihlali"
        try: bot.edit_message_text(
            f"🔴 <b>HESABINIZ BANLANDI</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Sebep:</b> {reason}\n\n<b>Tespit edilen ihlaller:</b>\n{vlist}\n\n"
            "Tüm botlarınız durdurulmuş ve hesabınız kalıcı olarak banlanmıştır.\n━━━━━━━━━━━━━━━━━━━━",
            uid, sm.message_id)
        except: bot.send_message(uid, "🔴 Hesabınız banlanmıştır.")
        return
    remaining = 3 - new_count
    icon  = "⛔" if new_count == 2 else "⚠️"
    title = "UYARI #2 — SON UYARI" if new_count == 2 else f"UYARI #{new_count} — KURAL İHLALİ"
    try: bot.edit_message_text(
        f"{icon} <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Bot dosyanızda yasak içerik tespit edildi:\n\n{vlist}\n\n"
        f"Bu {new_count}. uyarınızdır. {remaining} uyarı sonunda hesabınız kalıcı olarak banlanır.\n━━━━━━━━━━━━━━━━━━━━",
        uid, sm.message_id)
    except: bot.send_message(uid, f"{icon} Uyarı #{new_count}: Yasak içerik tespit edildi.")

def _upload_data(msg, uid, bot_id):
    if not bot_id: bot.send_message(uid, "❌ Bot ID bulunamadı."); return
    b = db_get_bot(bot_id)
    if not b or b["user_id"] != uid: bot.send_message(uid, "❌ Bot bulunamadı."); return
    fname = msg.document.file_name or "data.db"
    if not any(fname.endswith(e) for e in (".json", ".db", ".sqlite", ".sqlite3")):
        bot.send_message(uid, "❌ Yalnızca .json, .db veya .sqlite dosyası kabul edilir."); return
    try:
        raw = bot.download_file(bot.get_file(msg.document.file_id).file_path)
        safe = os.path.basename(fname)
        path = os.path.join(os.path.dirname(b["file_path"]), safe)
        with open(path, "wb") as f: f.write(raw)
        db_add_data(bot_id, safe, path)
        _states.pop(uid, None)
        bot.send_message(uid, f"✅ <b>{safe}</b> başarıyla yüklendi!", reply_markup=_back(f"bmenu_{bot_id}"))
    except Exception as e:
        bot.send_message(uid, f"❌ Hata: {e}")

def _try_send(user_id, text):
    try: bot.send_message(user_id, text); return True
    except: return False

@bot.message_handler(content_types=["text"])
def on_text(msg):
    uid   = msg.from_user.id
    text  = msg.text.strip()
    state = _states.get(uid, {}).get("state")
    if _is_banned(uid): return

    if state == "adm_broadcast" and uid in ADMIN_IDS:
        users = db_all_users()
        sent = sum(1 for u in users if _try_send(u["user_id"], f"📢 <b>DUYURU</b>\n\n{text}"))
        _states.pop(uid, None)
        bot.send_message(uid, f"✅ Duyuru gönderildi: {sent}/{len(users)} kullanıcı")

    elif state == "adm_del_bot" and uid in ADMIN_IDS:
        try:
            bid = int(text); b = db_get_bot(bid)
            if not b: bot.send_message(uid, "❌ Bot bulunamadı.")
            else:
                runner_stop(bid)
                if b["file_path"] and os.path.exists(b["file_path"]): os.remove(b["file_path"])
                db_del_bot(bid); bot.send_message(uid, f"✅ Bot #{bid} silindi.")
        except ValueError: bot.send_message(uid, "❌ Geçerli bir bot ID girin.")
        _states.pop(uid, None)

    elif state == "adm_find" and uid in ADMIN_IDS:
        try: u = db_get_user(int(text))
        except ValueError: u = db_get_user_by_uname(text)
        if not u: bot.send_message(uid, "❌ Kullanıcı bulunamadı.")
        else:
            tid = u["user_id"]; pkg = PACKAGES.get(u["package"], PACKAGES["free"]); bots = db_get_bots(tid)
            kb = types.InlineKeyboardMarkup(row_width=2)
            if u["is_banned"]: kb.add(types.InlineKeyboardButton("✅ Ban Kaldır", callback_data=f"adm_unban_{tid}"))
            else: kb.add(types.InlineKeyboardButton("🔴 Banla", callback_data=f"adm_ban_{tid}"))
            kb.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"))
            bot.send_message(uid,
                f"👤 <b>KULLANICI BİLGİSİ</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 ID: <code>{tid}</code>\n👤 Ad: {u['full_name']}\n📛 @{u['username'] or '—'}\n"
                f"📦 Paket: {pkg['name']}\n⚠️ Uyarı: {u['warning_count']}/3\n"
                f"🔴 Banlı: {'Evet' if u['is_banned'] else 'Hayır'}\n"
                f"🤖 Bot sayısı: {len(bots)}\n📅 Kayıt: {u['created_at'][:10]}\n━━━━━━━━━━━━━━━━━━━━",
                reply_markup=kb)
        _states.pop(uid, None)

if __name__ == "__main__":
    init_db()
    sync_states()
    os.makedirs(BOTS_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    print("VDS Panel başlatılıyor...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
