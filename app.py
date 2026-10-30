import asyncio
import logging
import re
import hashlib
import time
import json
import os
import aiohttp
from aiohttp import web
from typing import List, Dict, Any, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Logging ayarları
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== KONFİGÜRASYON & ADMIN ====================
MAIN_BOT_TOKEN = "8805611364:AAGrJX1rPCt6m2GpF8VXuMNLZG9ADsi42KA"
MAIN_BOT_USERNAME = "vantoriumotp_bot"  # Ana bot kullanıcı adı
MAIN_ADMIN_ID = 8913966694

# Gerekli Referans Sayısı
REQUIRED_REFERRALS = 3
FLOOD_DELAY = 1.5

# Veritabanı Dosya Yolları
DB_FILE = "user_db.json"
CLONE_BOTS_FILE = "clone_bots.json"  # {"token": "...", "owner_id": 12345, "active": True}

# Hafıza Yapıları
seen_sms_hashes = set()
user_active_numbers: Dict[int, str] = {}
user_cooldowns: Dict[int, float] = {}
failed_request_counts: Dict[str, int] = {}
waiting_for_broadcast: set = set()

# Klonlama adımlarını takip etmek için geçici hafıza
clone_process_state: Dict[int, Dict[str, Any]] = {}

# Çalışan klon botların Application referansları {token: application}
running_clone_apps: Dict[str, Application] = {}

# ==================== KALICI VERİ YÖNETİMİ (JSON) ====================

def load_db() -> Dict[str, Any]:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"DB okuma hatası: {e}")
    return {
        "user_profiles": {},
        "banned_users": [],
        "user_referrers": {},
        "user_invite_counts": {},
        "referred_users_set": [],
        "user_joined_status": {}
    }

def save_db():
    data = {
        "user_profiles": {str(k): v for k, v in user_profiles.items()},
        "banned_users": list(banned_users),
        "user_referrers": {str(k): v for k, v in user_referrers.items()},
        "user_invite_counts": {str(k): v for k, v in user_invite_counts.items()},
        "referred_users_set": list(referred_users_set),
        "user_joined_status": {str(k): v for k, v in user_joined_status.items()}
    }
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"DB kayıt hatası: {e}")

def load_clone_bots() -> List[Dict[str, Any]]:
    if os.path.exists(CLONE_BOTS_FILE):
        try:
            with open(CLONE_BOTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_clone_bot_data(token: str, owner_id: int, active: bool = True):
    bots = load_clone_bots()
    for b in bots:
        if b["token"] == token:
            b["owner_id"] = owner_id
            b["active"] = active
            break
    else:
        bots.append({"token": token, "owner_id": owner_id, "active": active})
    try:
        with open(CLONE_BOTS_FILE, "w", encoding="utf-8") as f:
            json.dump(bots, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Klon bot kayıt hatası: {e}")

def update_clone_bot_status(token: str, active: bool):
    bots = load_clone_bots()
    for b in bots:
        if b["token"] == token:
            b["active"] = active
            break
    try:
        with open(CLONE_BOTS_FILE, "w", encoding="utf-8") as f:
            json.dump(bots, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Klon bot durum güncelleme hatası: {e}")

def remove_clone_bot_data(token: str):
    bots = load_clone_bots()
    bots = [b for b in bots if b["token"] != token]
    try:
        with open(CLONE_BOTS_FILE, "w", encoding="utf-8") as f:
            json.dump(bots, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Klon bot silme hatası: {e}")

# Verileri yükle
db_data = load_db()
user_profiles: Dict[int, Dict[str, Any]] = {int(k): v for k, v in db_data.get("user_profiles", {}).items()}
banned_users = set(int(k) for k in db_data.get("banned_users", []))
user_referrers: Dict[int, int] = {int(k): int(v) for k, v in db_data.get("user_referrers", {}).items()}
user_invite_counts: Dict[int, int] = {int(k): int(v) for k, v in db_data.get("user_invite_counts", {}).items()}
referred_users_set = set(int(k) for k in db_data.get("referred_users_set", []))
user_joined_status: Dict[int, bool] = {int(k): v for k, v in db_data.get("user_joined_status", {}).items()}


# ==================== API LİSTESİ ====================
APIS_CONFIG = [
    {
        "name": "NumberPanel Lifetime (Özel)",
        "url": "https://numberpanel.tech/api/lifetime"
    },
    {
        "name": "Container IP API (Özel)",
        "url": "http://194.238.28.236:8000/container"
    },
    {
        "name": "Number Panel",
        "url": "http://147.135.212.197/crapi/st/viewstats",
        "auth": "R1ZVR0hBUzR1Z1FBQVGZiVqAd1OEd2NmW4qIRHyDi0NZdWJzYFOLeA=="
    },
    {
        "name": "Konekta API",
        "url": "http://51.77.216.195/crapi/konek/viewstats",
        "auth": "EL API"
    },
    {
        "name": "TimeSMS API 1",
        "url": "http://147.135.212.197/crapi/time/viewstats",
        "auth": "Qk9USDRSQkpqh2Blc4l3dWBtYXVEZ5djSW9lhlNfd1t0b2pneW90"
    },
    {
        "name": "TimeSMS API 2",
        "url": "http://147.135.212.197/crapi/time/viewstats",
        "auth": "Qk9XRzRSQkRjhYOLiniKWoeCalOJUIR3WnV3X4WHllljg2iKfmhm"
    },
    {
        "name": "Hadi SMS 1",
        "url": "http://147.135.212.197/crapi/had/viewstats",
        "auth": "SVVTQjRSQmRUcVhpZnSCZIeQlUh2hHiDSI5haHJvkHKCiZJVVHWY"
    },
    {
        "name": "Hadi SMS 2",
        "url": "http://147.135.212.197/crapi/had/viewstats",
        "auth": "SlFWRDRSQmlmTm5SW2qPY4ZOVneDa4hiUpSXQV-JboZog4NTiI-K"
    },
    {
        "name": "iVASMS",
        "url": "https://maroon-wombat-183778.hostingersite.com/apiivasms/api.php",
        "auth": "sk_4b5ff02b42635e4c4891bd5d2f8fcb7aff2f7af6d5daecf8498ba91092dfcbb6"
    },
    {
        "name": "Roxy SMS",
        "url": "http://www.roxysms.net",
        "user": "nooorwahba",
        "pass": "nooreldeingg123@"
    }
]


# ==================== ÜLKE VE BAYRAK SİSTEMİ ====================
COUNTRY_DATA = {
    "1": ("USA/CANADA", "🇺🇸"), "7": ("RUSSIA", "🇷🇺"), "20": ("EGYPT", "🇪🇬"), "27": ("SOUTH AFRICA", "🇿🇦"),
    "30": ("GREECE", "🇬🇷"), "31": ("NETHERLANDS", "🇳🇱"), "32": ("BELGIUM", "🇧🇪"), "33": ("FRANCE", "🇫🇷"),
    "34": ("SPAIN", "🇪🇸"), "36": ("HUNGARY", "🇭🇺"), "39": ("ITALY", "🇮🇹"), "40": ("ROMANIA", "🇷🇴"),
    "41": ("SWITZERLAND", "🇨🇭"), "43": ("AUSTRIA", "🇦🇹"), "44": ("UK", "🇬🇧"), "45": ("DENMARK", "🇩🇰"),
    "46": ("SWEDEN", "🇸🇪"), "47": ("NORWAY", "🇳🇴"), "48": ("POLAND", "🇵🇱"), "49": ("GERMANY", "🇩🇪"),
    "90": ("TURKEY", "🇹🇷"), "91": ("INDIA", "🇮🇳"), "92": ("PAKISTAN", "🇵🇰"), "93": ("AFGHANISTAN", "🇦🇫"),
    "94": ("SRI LANKA", "🇱🇰"), "95": ("MYANMAR", "🇲🇲"), "98": ("IRAN", "🇮🇷"), "212": ("MOROCCO", "🇲🇦"),
    "213": ("ALGERIA", "🇩🇿"), "216": ("TUNISIA", "🇹🇳"), "218": ("LIBYA", "🇱🇾"), "220": ("GAMBIA", "🇬🇲"),
    "221": ("SENEGAL", "🇸🇳"), "223": ("MALI", "🇲🇱"), "224": ("GUINEA", "🇬🇳"), "225": ("IVORY COAST", "🇨🇮"),
    "226": ("BURKINA FASO", "🇧🇫"), "227": ("NIGER", "🇳🇪"), "228": ("TOGO", "TOGO"), "229": ("BENIN", "🇧🇯"),
    "230": ("MAURITIUS", "🇲🇺"), "231": ("LIBERIA", "🇱🇷"), "232": ("SIERRA LEONE", "🇸🇱"), "233": ("GHANA", "🇬🇭"),
    "234": ("NIGERIA", "🇳🇬"), "235": ("CHAD", "🇹🇩"), "236": ("CENTRAL AFRICAN REP", "🇨🇫"), "237": ("CAMEROON", "🇨🇲"),
    "238": ("CAPE VERDE", "🇨🇻"), "239": ("SAO TOME", "🇸🇹"), "240": ("EQUATORIAL GUINEA", "🇬🇶"), "241": ("GABON", "🇬🇦"),
    "242": ("CONGO", "🇨🇬"), "243": ("DR CONGO", "🇨🇩"), "244": ("ANGOLA", "🇦🇴"), "245": ("GUINEA-BISSAU", "🇬🇼"),
    "248": ("SEYCHELLES", "🇸🇨"), "249": ("SUDAN", "🇸🇩"), "250": ("RWANDA", "🇷🇼"), "251": ("ETHIOPIA", "🇪🇹"),
    "252": ("SOMALIA", "🇸🇴"), "253": ("DJIBOUTI", "🇩🇯"), "254": ("KENYA", "🇰🇪"), "255": ("TANZANIA", "🇹🇿"),
    "256": ("UGANDA", "🇺🇬"), "257": ("BURUNDI", "🇧🇮"), "258": ("MOZAMBIQUE", "🇲🇿"), "260": ("ZAMBIA", "🇿🇲"),
    "261": ("MADAGASCAR", "🇲🇬"), "263": ("ZIMBABWE", "🇿🇼"), "264": ("NAMIBIA", "🇳🇦"), "265": ("MALAWI", "🇲🇼"),
    "266": ("LESOTHO", "🇱🇸"), "267": ("BOTSWANA", "🇧🇼"), "268": ("ESWATINI", "🇸🇿"), "269": ("COMOROS", "🇰🇲"),
    "350": ("GIBRALTAR", "🇬🇮"), "351": ("PORTUGAL", "🇵🇹"), "352": ("LUXEMBOURG", "🇱🇺"), "353": ("IRELAND", "🇮🇪"),
    "354": ("ICELAND", "🇮🇸"), "355": ("ALBANIA", "🇦🇱"), "356": ("MALTA", "🇲🇹"), "357": ("CYPRUS", "🇨🇾"),
    "358": ("FINLAND", "🇫🇮"), "359": ("BULGARIA", "🇧🇬"), "370": ("LITHUANIA", "🇱🇹"), "371": ("LATVIA", "🇱🇻"),
    "372": ("ESTONIA", "🇪🇪"), "373": ("MOLDOVA", "🇲🇩"), "374": ("ARMENIA", "🇦🇲"), "375": ("BELARUS", "🇧🇾"),
    "378": ("SAN MARINO", "🇸🇲"), "380": ("UKRAINE", "🇺🇦"), "381": ("SERBIA", "🇷🇸"), "382": ("MONTENEGRO", "🇲🇪"),
    "385": ("CROATIA", "🇭🇷"), "386": ("SLOVENIA", "🇸🇮"), "387": ("BOSNIA", "🇧🇦"), "389": ("NORTH MACEDONIA", "🇲🇰"),
    "420": ("CZECH REPUBLIC", "🇨🇿"), "421": ("SLOVAKIA", "🇸🇰"), "502": ("GUATEMALA", "🇬🇹"), "503": ("EL SALVADOR", "🇸🇻"),
    "504": ("HONDURAS", "🇭🇳"), "505": ("NICARAGUA", "🇳🇮"), "506": ("COSTA RICA", "🇨🇷"), "507": ("PANAMA", "🇵🇦"),
    "509": ("HAITI", "🇭🇹"), "591": ("BOLIVIA", "🇧🇴"), "593": ("ECUADOR", "🇪🇨"), "595": ("PARAGUAY", "🇵🇾"),
    "598": ("URUGUAY", "🇺🇾"), "852": ("HONG KONG", "🇭🇰"), "853": ("MACAU", "🇲🇴"), "855": ("CAMBODIA", "🇰🇭"),
    "856": ("LAOS", "🇱🇦"), "880": ("BANGLADESH", "🇧🇩"), "886": ("TAIWAN", "🇹🇼"), "960": ("MALDIVES", "🇲🇻"),
    "961": ("LEBANON", "🇱🇧"), "962": ("JORDAN", "🇯🇴"), "963": ("SYRIA", "🇸🇾"), "964": ("IRAQ", "🇮🇶"),
    "965": ("KUWAIT", "🇰🇼"), "966": ("SAUDI ARABIA", "🇸🇦"), "967": ("YEMEN", "🇾🇪"), "968": ("OMAN", "🇴🇲"),
    "971": ("UAE", "🇦🇪"), "972": ("ISRAEL", "🇮🇱"), "973": ("BAHRAIN", "🇧🇭"), "974": ("QATAR", "🇶🇦"),
    "975": ("BHUTAN", "🇧🇹"), "976": ("MONGOLIA", "🇲🇳"), "977": ("NEPAL", "🇳🇵"), "992": ("TAJIKISTAN", "🇹🇯"),
    "993": ("TURKMENISTAN", "🇹🇲"), "994": ("AZERBAIJAN", "🇦🇿"), "995": ("GEORGIA", "🇬🇪"), "996": ("KYRGYZSTAN", "🇰🇬"),
    "998": ("UZBEKISTAN", "UZ")
}

def get_country_info(num_str: str) -> tuple:
    clean = num_str.replace("+", "").strip()
    for length in [3, 2, 1]:
        prefix = clean[:length]
        if prefix in COUNTRY_DATA:
            val = COUNTRY_DATA[prefix]
            if isinstance(val, tuple):
                return val
            return (val, "🌐")
    return ("GLOBAL", "🌐")


def detect_platform(item: Dict[str, Any], sms_text: str) -> str:
    raw = str(item.get("service") or item.get("platform") or item.get("app") or item.get("site") or "").upper()
    combined = (str(sms_text) + " " + raw).upper()
    if "TELEGRAM" in combined or "TG" in combined:
        return "TELEGRAM"
    elif "WHATSAPP" in combined or "WA" in combined:
        return "WHATSAPP"
    elif "INSTAGRAM" in combined or "IG" in combined:
        return "INSTAGRAM"
    elif "TIKTOK" in combined:
        return "TIKTOK"
    elif "TWITTER" in combined or "X.COM" in combined:
        return "TWITTER"
    elif "DISCORD" in combined:
        return "DISCORD"
    elif "GOOGLE" in combined or "GMAIL" in combined:
        return "GOOGLE"
    elif "TINDER" in combined:
        return "TINDER"
    return "GENEL / DİĞER"


def parse_json_safely(data: Any, all_items: list):
    if isinstance(data, dict):
        all_items.append(data)
        for val in data.values():
            if isinstance(val, list):
                for sub in val:
                    if isinstance(sub, dict):
                        all_items.append(sub)
            elif isinstance(val, dict):
                all_items.append(val)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                all_items.append(item)


async def fetch_api_data() -> List[Dict[str, Any]]:
    all_items = []
    timeout = aiohttp.ClientTimeout(total=5, connect=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for api in APIS_CONFIG:
            url = api.get("url")
            if not url:
                continue
            headers = {}
            auth = api.get("auth")
            if auth:
                headers["Authorization"] = f"Bearer {auth}"
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        parse_json_safely(data, all_items)
            except Exception as e:
                failed_request_counts[api['name']] = failed_request_counts.get(api['name'], 0) + 1
                logger.warning(f"API Hatası [{api['name']}]: {e}")
    return all_items


def extract_sms_details(item: Any) -> Optional[Dict[str, str]]:
    if not isinstance(item, dict):
        return None
    number = item.get("number") or item.get("phone") or item.get("msisdn") or item.get("gsm") or ""
    sms_text = item.get("sms") or item.get("message") or item.get("text") or item.get("code") or ""
    raw_country = item.get("country") or item.get("region") or item.get("operator") or ""

    if number:
        clean_num = str(number).strip()
        if not clean_num.startswith("+"):
            clean_num = "+" + clean_num
        raw_signature = f"{clean_num}_{str(sms_text).strip()}"
        sms_hash = hashlib.md5(raw_signature.encode('utf-8')).hexdigest()

        if not raw_country or str(raw_country).upper() in ["UNKNOWN", "N/A", "GLOBAL", ""]:
            c_name, c_flag = get_country_info(clean_num)
        else:
            c_name = str(raw_country).upper()
            c_flag = "🌐"

        platform = detect_platform(item, str(sms_text))
        return {
            "id": sms_hash,
            "number": clean_num,
            "sms": str(sms_text),
            "country": c_name,
            "flag": c_flag,
            "platform": platform
        }
    return None


# ==================== YETKİ KONTROLÜ ====================
def is_admin(user_id: int) -> bool:
    if user_id == MAIN_ADMIN_ID:
        return True
    for bot in load_clone_bots():
        if bot.get("owner_id") == user_id:
            return True
    return False


# ==================== MENÜLER ====================

def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🚀 Genişletilmiş Numara Al", callback_data="select_platform"),
            InlineKeyboardButton("🔗 Referans Linkim", callback_data="ref_link")
        ],
        [
            InlineKeyboardButton("📊 Sistem Durumu", callback_data="system_status")
        ]
    ]
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("👑 Admin Paneli", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)


# ==================== HANDLERLER ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name
    username = user.username or "Yok"

    if user_id in banned_users:
        await update.message.reply_text("⛔ <b>Bu sistemden banlandınız!</b>", parse_mode="HTML")
        return

    user_profiles[user_id] = {
        "id": user_id,
        "ad": first_name,
        "username": username
    }

    # Referans İşleme
    args = context.args
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0].replace("ref_", ""))
            if referrer_id != user_id and user_id not in referred_users_set:
                referred_users_set.add(user_id)
                user_referrers[user_id] = referrer_id
                user_invite_counts[referrer_id] = user_invite_counts.get(referrer_id, 0) + 1
                save_db()
        except ValueError:
            pass

    save_db()
    user_joined_status[user_id] = True
    save_db()
    
    welcome_msg = (
        f"👑 <b>HOŞ GELDİNİZ, VIP KRAL {first_name}</b> 👑\n\n"
        f"💎 <i>Genişletilmiş Çoklu Platform ve Güvenlik Altyapısı Aktiftir.</i>\n\n"
        f"👇 <b>Aşağıdaki menüden işlem seçin:</b>"
    )
    await update.message.reply_text(welcome_msg, parse_mode="HTML", reply_markup=get_main_keyboard(user_id))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    user_id = user.id
    current_time = time.time()

    if user_id in banned_users:
        try:
            await query.answer("⛔ Banlandınız!", show_alert=True)
        except Exception:
            pass
        return

    if user_id in user_cooldowns:
        if current_time - user_cooldowns[user_id] < FLOOD_DELAY:
            try:
                await query.answer("⚠️ Çok hızlı işlem yapıyorsunuz!", show_alert=True)
            except Exception:
                pass
            return
    user_cooldowns[user_id] = current_time

    try:
        await query.answer()
    except Exception:
        pass

    data = query.data

    if data == "ref_link":
        ref_link = f"https://t.me/{MAIN_BOT_USERNAME}?start=ref_{user_id}"
        current_refs = user_invite_counts.get(user_id, 0)
        ref_msg = (
            f"🔗 <b>REFERANS SİSTEMİ</b> 🔗\n\n"
            f"Botu aktif olarak kullanabilmek ve arkadaşlarınla paylaşmak için kişisel davet linkin:\n\n"
            f"<code>{ref_link}</code>\n\n"
            f"📊 <b>İlerlemen:</b> <code>{current_refs} / {REQUIRED_REFERRALS}</code> kişi"
        )
        try:
            await query.message.edit_text(
                ref_msg,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]])
            )
        except Exception:
            pass

    elif data == "select_platform":
        keyboard = [
            [InlineKeyboardButton("💬 WhatsApp", callback_data="plat_WHATSAPP"), InlineKeyboardButton("✈️ Telegram", callback_data="plat_TELEGRAM")],
            [InlineKeyboardButton("📸 Instagram", callback_data="plat_INSTAGRAM"), InlineKeyboardButton("🎵 TikTok", callback_data="plat_TIKTOK")],
            [InlineKeyboardButton("🐦 Twitter (X)", callback_data="plat_TWITTER"), InlineKeyboardButton("🎮 Discord", callback_data="plat_DISCORD")],
            [InlineKeyboardButton("🌐 Google / Gmail", callback_data="plat_GOOGLE"), InlineKeyboardButton("🔥 Tinder", callback_data="plat_TINDER")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]
        ]
        try:
            await query.message.edit_text(
                "🎯 <b>GENİŞLETİLMİŞ PLATFORM SEÇİNİZ</b>\n\n<i>Lütfen numarasını almak istediğiniz servisi seçin:</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass

    elif data.startswith("plat_"):
        target_platform = data.replace("plat_", "")
        try:
            await query.message.edit_text(f"🔍 <i>{target_platform} hatları taranıyor...</i>", parse_mode="HTML")
        except Exception:
            pass
        
        items = await fetch_api_data()
        if not items:
            try:
                await query.message.edit_text(
                    "❌ <b>Aktif hat bulunamadı.</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="select_platform")]])
                )
            except Exception:
                pass
            return

        unique_entries = []
        seen_countries = set()
        for item in items:
            details = extract_sms_details(item)
            if details and (details["platform"] == target_platform or target_platform == "GENEL"):
                if details["country"] not in seen_countries:
                    seen_countries.add(details["country"])
                    unique_entries.append(details)

        if not unique_entries:
            for item in items:
                details = extract_sms_details(item)
                if details and details["country"] not in seen_countries:
                    seen_countries.add(details["country"])
                    unique_entries.append(details)

        if not unique_entries:
            try:
                await query.message.edit_text(
                    f"❌ <b>{target_platform} için uygun kategori bulunamadı.</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="select_platform")]])
                )
            except Exception:
                pass
            return

        keyboard_rows = []
        for entry in unique_entries[:4]:
            btn_text = f"{entry['flag']} {entry['country']}"
            call_data = f"sel_{target_platform}|{entry['country']}"
            keyboard_rows.append([InlineKeyboardButton(btn_text, callback_data=call_data)])

        keyboard_rows.append([InlineKeyboardButton("🔙 Platform Seçimine Dön", callback_data="select_platform")])
        keyboard_rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")])

        try:
            await query.message.edit_text(
                f"📱 <b>{target_platform} İÇİN ÜLKE SEÇİNİZ</b>\n\n<i>Aşağıdan ülke seçin:</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard_rows)
            )
        except Exception:
            pass

    elif data.startswith("sel_"):
        parts = data.replace("sel_", "").split("|")
        target_platform = parts[0]
        target_country = parts[1]

        items = await fetch_api_data()
        assigned_num = None
        for item in items:
            details = extract_sms_details(item)
            if details and details["country"] == target_country:
                assigned_num = details["number"]
                break

        if not assigned_num and items:
            any_det = extract_sms_details(items[0])
            if any_det:
                assigned_num = any_det["number"]

        if assigned_num:
            user_active_numbers[user_id] = assigned_num
            success_text = (
                f"💎 <b>BAŞARIYLA EŞLEŞTİRİLDİ</b>\n\n"
                f"📱 Platform: <code>{target_platform}</code>\n"
                f"🌐 Ülke: <code>{target_country}</code>\n"
                f"📞 Atanan Numara: <code>{assigned_num}</code>\n\n"
                f"⏳ <i>SMS bekleniyor...</i>"
            )
            try:
                await query.message.edit_text(
                    success_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Platformlara Dön", callback_data="select_platform")],
                        [InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]
                    ])
                )
            except Exception:
                pass
        else:
            try:
                await query.message.edit_text(
                    "❌ <b>Bu kategoriye ait aktif numara yok.</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]])
                )
            except Exception:
                pass

    elif data == "admin_panel":
        if not is_admin(user_id):
            return
        clone_count = len(load_clone_bots())
        admin_keyboard = [
            [InlineKeyboardButton("👥 Kullanıcı Listesi", callback_data="admin_user_list")],
            [InlineKeyboardButton("📢 Duyuru Gönder", callback_data="admin_broadcast")],
            [InlineKeyboardButton(f"🤖 Bot Klonla", callback_data="admin_clone_bot"), InlineKeyboardButton(f"📋 Klon Bot Listesi", callback_data="admin_clone_list")],
            [InlineKeyboardButton("🔨 Kullanıcı Banla (/ban ID)", callback_data="admin_help_ban")],
            [InlineKeyboardButton("🔓 Ban Kaldır (/unban ID)", callback_data="admin_help_unban")],
            [InlineKeyboardButton("🏠 Ana Menüye Dön", callback_data="main_menu")]
        ]
        try:
            await query.message.edit_text(
                f"👑 <b>GELİŞMİŞ ADMIN PANELİ</b>\n\n📊 Toplam Kullanıcı: <code>{len(user_profiles)}</code>\n🤖 Klon Bot Sayısı: <code>{clone_count}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(admin_keyboard)
            )
        except Exception:
            pass

    elif data == "admin_user_list":
        if not is_admin(user_id):
            return
        msg = f"👥 <b>KULLANICI LİSTESİ (Toplam: {len(user_profiles)})</b>\n\n"
        for uid, p in list(user_profiles.items())[:20]:
            msg += f"• {p['ad']} (@{p['username']}) | ID: <code>{uid}</code>\n"
        try:
            await query.message.edit_text(
                msg,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="admin_panel")]])
            )
        except Exception:
            pass

    elif data == "admin_broadcast":
        if not is_admin(user_id):
            return
        waiting_for_broadcast.add(user_id)
        try:
            await query.message.edit_text(
                "📢 <b>DUYURU MODU</b>\n\nLütfen tüm kullanıcılara göndermek istediğiniz duyuru mesajını yazıp gönderin:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 İptal / Admin Paneli", callback_data="admin_panel")]])
            )
        except Exception:
            pass

    elif data == "admin_clone_bot":
        if not is_admin(user_id):
            return
        clone_process_state[user_id] = {"token": None, "step": "waiting_for_token"}
        try:
            await query.message.edit_text(
                "🤖 <b>BOT KLONLAMA SİSTEMİ (1/2)</b>\n\n"
                "Lütfen @BotFather'dan aldığınız **yeni botun tokenini** buraya mesaj olarak gönderin:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 İptal / Admin Paneli", callback_data="admin_panel")]])
            )
        except Exception:
            pass

    elif data == "admin_clone_list":
        if not is_admin(user_id):
            return
        bots = load_clone_bots()
        if not bots:
            kb = [[InlineKeyboardButton("🔙 Admin Paneli", callback_data="admin_panel")]]
            await query.message.edit_text("🤖 <b>Kayıtlı klon bot bulunmuyor.</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
            return

        kb = []
        text = "🤖 <b>KLON BOT YÖNETİM LİSTESİ</b>\n\n"
        for idx, b in enumerate(bots, 1):
            t_short = b["token"][:10] + "..."
            status = "🟢 Aktif" if b.get("active", True) else "🔴 Durduruldu"
            text += f"{idx}️⃣ Token: <code>{t_short}</code>\n   👤 Sahip ID: <code>{b['owner_id']}</code> | Durum: {status}\n\n"
            
            # İşlem butonları
            if b.get("active", True):
                kb.append([InlineKeyboardButton(f"🔴 Durdur #{idx}", callback_data=f"clone_stop_{b['token']}")])
            else:
                kb.append([InlineKeyboardButton(f"🟢 Başlat #{idx}", callback_data=f"clone_start_{b['token']}")])
            kb.append([InlineKeyboardButton(f"🗑️ Sil #{idx}", callback_data=f"clone_del_{b['token']}")])

        kb.append([InlineKeyboardButton("🔙 Admin Paneli", callback_data="admin_panel")])
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            pass

    elif data.startswith("clone_stop_"):
        if not is_admin(user_id):
            return
        token_to_stop = data.replace("clone_stop_", "")
        update_clone_bot_status(token_to_stop, active=False)
        if token_to_stop in running_clone_apps:
            try:
                app_to_stop = running_clone_apps[token_to_stop]
                await app_to_stop.stop()
                await app_to_stop.shutdown()
                del running_clone_apps[token_to_stop]
            except Exception:
                pass
        try:
            await query.answer("🛑 Klon bot durduruldu!", show_alert=True)
        except Exception:
            pass
        # Listeyi yenile
        await button_handler(update, context)

    elif data.startswith("clone_start_"):
        if not is_admin(user_id):
            return
        token_to_start = data.replace("clone_start_", "")
        update_clone_bot_status(token_to_start, active=True)
        
        bots = load_clone_bots()
        owner_id = MAIN_ADMIN_ID
        for b in bots:
            if b["token"] == token_to_start:
                owner_id = b["owner_id"]
                break
        
        asyncio.create_task(start_clone_bot(token_to_start, owner_id=owner_id))
        try:
            await query.answer("🚀 Klon bot başlatıldı!", show_alert=True)
        except Exception:
            pass
        await button_handler(update, context)

    elif data.startswith("clone_del_"):
        if not is_admin(user_id):
            return
        token_to_del = data.replace("clone_del_", "")
        if token_to_del in running_clone_apps:
            try:
                app_to_del = running_clone_apps[token_to_del]
                await app_to_del.stop()
                await app_to_del.shutdown()
                del running_clone_apps[token_to_del]
            except Exception:
                pass
        remove_clone_bot_data(token_to_del)
        try:
            await query.answer("🗑️ Klon bot silindi!", show_alert=True)
        except Exception:
            pass
        await button_handler(update, context)

    elif data in ["admin_help_ban", "admin_help_unban"]:
        if not is_admin(user_id):
            return
        try:
            await query.message.edit_text(
                "💡 <b>Komutlar:</b>\n/ban ID\n/unban ID",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="admin_panel")]])
            )
        except Exception:
            pass

    elif data == "system_status":
        items = await fetch_api_data()
        status_text = (
            f"📊 <b>GELİŞMİŞ SİSTEM DURUMU</b>\n\n"
            f"🟢 Aktif API Sayısı: <code>{len(APIS_CONFIG)}</code>[span_0](start_span)[span_0](end_span)\n"
            f"👥 Toplam Kullanıcı: <code>{len(user_profiles)}</code>[span_1](start_span)[span_1](end_span)\n"
            f"📈 Anlık SMS Havuzu: <code>{len(items)}</code>[span_2](start_span)[span_2](end_span)\n"
            f"🤖 Aktif Klon Botlar: <code>{len(running_clone_apps)}</code>"
        )
        try:
            await query.message.edit_text(
                status_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="main_menu")]])
            )
        except Exception:
            pass

    elif data == "main_menu":
        try:
            await query.message.edit_text(
                "👑 <b>Kontrol Paneli:</b>",
                parse_mode="HTML",
                reply_markup=get_main_keyboard(user_id)
            )
        except Exception:
            pass


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    # Duyuru Gönderme İşlemi
    if user_id in waiting_for_broadcast:
        waiting_for_broadcast.remove(user_id)
        broadcast_text = update.message.text
        
        success_count = 0
        fail_count = 0
        
        status_msg = await update.message.reply_text("📢 Duyuru gönderiliyor, lütfen bekleyin...")
        
        target_users = user_profiles.keys()
        
        for uid in target_users:
            try:
                await context.bot.send_message(chat_id=int(uid), text=broadcast_text, parse_mode="HTML")
                success_count += 1
                await asyncio.sleep(0.05)
            except Exception:
                fail_count += 1
                
        await status_msg.edit_text(
            f"✅ <b>Duyuru Başarıyla Tamamlandı!</b>\n\n"
            f"📤 Başarılı: <code>{success_count}</code>\n"
            f"❌ Başarısız: <code>{fail_count}</code>",
            parse_mode="HTML"
        )

    # Adım 1: Klon Bot Token Alımı
    elif user_id in clone_process_state and clone_process_state[user_id]["step"] == "waiting_for_token":
        token_input = update.message.text.strip()
        if ":" not in token_input or len(token_input) < 20:
            await update.message.reply_text("❌ Geçersiz bot token formatı! Lütfen @BotFather'dan aldığınız doğru tokeni gönderin.")
            return

        clone_process_state[user_id]["token"] = token_input
        clone_process_state[user_id]["step"] = "waiting_for_owner_id"
        
        await update.message.reply_text(
            "🤖 <b>BOT KLONLAMA SİSTEMİ (2/2)</b>\n\n"
            "Token başarıyla alındı! Şimdi bu botun sahibi (owner) olacak kişinin **Telegram Kullanıcı ID'sini** (rakamsal ID) yazın:",
            parse_mode="HTML"
        )

    # Adım 2: Klon Bot Owner ID Alımı ve Kaydı
    elif user_id in clone_process_state and clone_process_state[user_id]["step"] == "waiting_for_owner_id":
        owner_id_input = update.message.text.strip()
        try:
            owner_id = int(owner_id_input)
        except ValueError:
            await update.message.reply_text("❌ Geçersiz ID! Lütfen sadece sayısal bir Telegram ID girin.")
            return

        token = clone_process_state[user_id]["token"]
        del clone_process_state[user_id]

        save_clone_bot_data(token, owner_id=owner_id, active=True)
        await update.message.reply_text(
            f"🚀 <b>Yeni bot başarıyla klonlandı ve kaydedildi!</b>\n\n"
            f"👤 Sahip (Owner) ID: <code>{owner_id}</code>\n"
            f"Bot arka planda başlatılıyor...",
            parse_mode="HTML"
        )
        
        asyncio.create_task(start_clone_bot(token, owner_id=owner_id))


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        return
    try:
        target_id = int(args[0])
        banned_users.add(target_id)
        save_db()
        await update.message.reply_text(f"🔨 Banlandı: <code>{target_id}</code>", parse_mode="HTML")
    except ValueError:
        pass


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args
    if not args:
        return
    try:
        target_id = int(args[0])
        if target_id in banned_users:
            banned_users.remove(target_id)
            save_db()
            await update.message.reply_text(f"🔓 Banı açıldı: <code>{target_id}</code>", parse_mode="HTML")
    except ValueError:
        pass


async def background_api_watcher(app: Application):
    logger.info("⚡ Gözlemci başlatıldı.")
    initial_items = await fetch_api_data()
    for item in initial_items:
        details = extract_sms_details(item)
        if details:
            seen_sms_hashes.add(details["id"])

    while True:
        try:
            await asyncio.sleep(6)
            items = await fetch_api_data()

            for item in items:
                details = extract_sms_details(item)
                if not details or not details["sms"]:
                    continue

                sms_hash = details["id"]
                num = details["number"]
                sms_content = details["sms"]

                if sms_hash not in seen_sms_hashes:
                    seen_sms_hashes.add(sms_hash)
                    
                    for uid, assigned_num in list(user_active_numbers.items()):
                        if assigned_num == num:
                            try:
                                await app.bot.send_message(
                                    chat_id=uid,
                                    text=f"📱 <b>YENİ KOD:</b>\n<code>{sms_content}</code>",
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass
        except Exception as e:
            logger.error(f"Watcher hata: {e}")
            await asyncio.sleep(6)


async def post_init(application: Application):
    asyncio.create_task(background_api_watcher(application))


# ==================== KLON BOT ÇALIŞTIRICI ====================
async def start_clone_bot(token: str, owner_id: int):
    try:
        builder = Application.builder().token(token).post_init(post_init)
        clone_app = builder.build()

        clone_app.add_handler(CommandHandler("start", start_command))
        clone_app.add_handler(CommandHandler("ban", ban_command))
        clone_app.add_handler(CommandHandler("unban", unban_command))
        clone_app.add_handler(CallbackQueryHandler(button_handler))
        clone_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

        running_clone_apps[token] = clone_app
        logger.info(f"🤖 Klon bot başarıyla başlatıldı (Sahip ID: {owner_id})")
        
        await clone_app.initialize()
        await clone_app.start()
        await clone_app.updater.start_polling()
    except Exception as e:
        logger.error(f"Klon bot başlatılamadı ({token[:10]}...): {e}")


# ==================== RENDER WEB SERVER KÖPRÜSÜ ====================
async def handle_health_check(request):
    return web.Response(text="Multi-Bot Vantorium System is running smoothly!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Render Sağlık Sunucusu {port} portunda başlatıldı.")


def main():
    # Ana bot yapılandırması
    builder = Application.builder().token(MAIN_BOT_TOKEN).post_init(post_init)
    app = builder.build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    print("🚀 Vantorium Team Genişletilmiş Multi-Bot & Klonlanabilir OTP Bot Sistemi Aktif!")
    
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    # Render port sunucusunu başlat
    loop.create_task(start_web_server())

    # Kayıtlı ve aktif olan klon botları otomatik başlat
    clone_bots = load_clone_bots()
    for b in clone_bots:
        if b.get("active", True):
            loop.create_task(start_clone_bot(b["token"], b["owner_id"]))
        
    app.run_polling()


if __name__ == "__main__":
    main()
