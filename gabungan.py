import os
import sys
import json
import re
import html
import time
import signal
import asyncio
import logging
import sqlite3
import subprocess
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==========================================
# 1. KONFIGURASI UTAMA & VARIABEL GLOBAL
# ==========================================
API_ID = 31339570  # Sesuai API_ID rate_me.py
API_HASH = "1f14c1c891126b5bcd0800b94822c821"  # Sesuai API_HASH rate_me.py
TOKEN = os.getenv("BOT_TOKEN", "isi_token_master_di_sini")

DEFAULT_CHANNEL = os.getenv("CH_ID", "-1001234567890")  # WAJIB FORMAT ID -100
MAIN_OWNER_ID = 123456789  # GANTI DENGAN ID ANDA
OWNER_ID = int(os.getenv("OWN_ID", MAIN_OWNER_ID))

IS_CLONE = os.getenv("IS_CLONE", "False") == "True"
NAMA_BOT = "Bilik Rahasia Menfess"
DEFAULT_TEMPLATE = "===================================\n{TEXT}\n\n===================================\n😎 <i>sender</i> {SENDER}"

# Timer hancur otomatis (Bilik Rahasia)
expiry_timers = {}

# ==========================================
# 2. DATABASE INITIALIZATION (SQLite)
# ==========================================
# Menggunakan satu file database yang sama agar antar clone bisa saling menyebarkan konten
DB_FILE = "menfess_universe.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    cursor = conn.cursor()
    # Tabel User Terintegrasi
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            gender TEXT,
            mode TEXT DEFAULT 'all',
            anon INTEGER DEFAULT 1,
            pinned_msg INTEGER DEFAULT 0,
            kuota INTEGER DEFAULT 0
        )
    """)
    # Tabel Konfigurasi Bot (Master & Clone)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            bot_id INTEGER PRIMARY KEY,
            target_channel TEXT,
            qris_link TEXT DEFAULT '',
            post_template TEXT,
            gratis INTEGER DEFAULT 0
        )
    """)
    # Tabel Banned Users
    cursor.execute("CREATE TABLE IF NOT EXISTS banned_users (id INTEGER PRIMARY KEY)")
    # Tabel Postingan & Rating Trackers
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            msg_id INTEGER,
            bot_id INTEGER,
            sender_id INTEGER,
            dashboard_msg_id INTEGER,
            r1 INTEGER DEFAULT 0,
            r2 INTEGER DEFAULT 0,
            r3 INTEGER DEFAULT 0,
            r4 INTEGER DEFAULT 0,
            r5 INTEGER DEFAULT 0,
            PRIMARY KEY (msg_id, bot_id)
        )
    """)
    # Tabel Clone Engine
    cursor.execute("CREATE TABLE IF NOT EXISTS clones (token TEXT PRIMARY KEY, owner INTEGER, ch TEXT, pid INTEGER)")
    conn.commit()
    conn.close()

init_db()

def db_query(query, params=(), commit=False, fetch="all"):
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        if commit:
            conn.commit()
            return cursor.lastrowid
        if fetch == "all":
            return cursor.fetchall()
        elif fetch == "one":
            return cursor.fetchone()
    except Exception as e:
        logging.error(f"Database Error: {e}")
    finally:
        conn.close()

# ==========================================
# 3. WORKER TIMER (AUTO DELETE)
# ==========================================
async def auto_delete_worker(client, chat_id, message_id, key):
    while True:
        sisa_waktu = expiry_timers.get(key, 0) - time.time()
        if sisa_waktu <= 0:
            break
        await asyncio.sleep(min(sisa_waktu, 10))
    try:
        await client.delete_messages(chat_id, message_id)
    except:
        pass
    expiry_timers.pop(key, None)

# ==========================================
# 4. SISTEM KEYBOARD UNIVERSAL (UI)
# ==========================================
def kb_pilih_gender():
    return ReplyKeyboardMarkup([["Laki-laki", "Perempuan"]], resize_keyboard=True)

def kb_home(user_id, bot_id):
    # Cek apakah user adalah owner utama bot ini atau owner clone ini
    is_owner = (user_id == MAIN_OWNER_ID and not IS_CLONE) or (user_id == OWNER_ID)
    
    if is_owner:
        # Tombol Toggle Mode Gratis/Bayar Dinamis (Menghemat space & praktis)
        cfg = db_query("SELECT gratis FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
        status_gratis = cfg["gratis"] if cfg else 0
        txt_mode = "🔓 Mode: GRATIS (Klik Ke Berbayar)" if status_gratis else "🔒 Mode: BERBAYAR (Klik Ke Gratis)"
        
        return ReplyKeyboardMarkup([
            ['📝 POSTING', '🤖 Buat/Kelola Clone'],
            ['⚙️ Pengaturan', '📢 Broadcast'],
            [txt_mode],
            ['👤 Mode User']
        ], resize_keyboard=True)
    else:
        return ReplyKeyboardMarkup([
            ['📝 POSTING', '🤖 Buat/Kelola Clone'],
            ['💳 Isi Kuota', '📊 Info Akun']
        ], resize_keyboard=True)

def kb_posting_menu():
    return ReplyKeyboardMarkup([
        ['👤 Kirim Anonim', '👁️ Tampilkan Nama'],
        ['🎧 Pengaturan Menyimak', '⚧ Ubah Gender'],
        ['🏠 HOME']
    ], resize_keyboard=True)

def kb_menyimak_filter():
    return ReplyKeyboardMarkup([
        ["Terima Semua Jenis (Teks & Media)"],
        ["Terima Media Saja (Foto/Video)", "Terima Teks Saja"],
        ["🏠 HOME"]
    ], resize_keyboard=True)

def rating_kb(msg_id, sent_msg_id, origin_bot_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1⭐", callback_data=f"rate_1_{msg_id}_{sent_msg_id}_{origin_bot_id}"),
         InlineKeyboardButton("2⭐", callback_data=f"rate_2_{msg_id}_{sent_msg_id}_{origin_bot_id}"),
         InlineKeyboardButton("3⭐", callback_data=f"rate_3_{msg_id}_{sent_msg_id}_{origin_bot_id}")],
        [InlineKeyboardButton("4⭐", callback_data=f"rate_4_{msg_id}_{sent_msg_id}_{origin_bot_id}"),
         InlineKeyboardButton("5⭐", callback_data=f"rate_5_{msg_id}_{sent_msg_id}_{origin_bot_id}")]
    ])

# ==========================================
# 5. REGEX & LINK PARSER (Fitur Asli memfess.py)
# ==========================================
def parse_and_extract_links(raw_text):
    url_pattern = r'((?:https?://|www\.)[^\s]+|(?:instagram\.com|facebook\.com|fb\.com|fb\.watch|fb\.gg|twitter\.com|x\.com|tiktok\.com|vt\.tiktok\.com|youtube\.com|youtu\.be|threads\.net|linkedin\.com|pinterest\.com|pin\.it|snapchat\.com|twitch\.tv|discord\.gg|discord\.com|reddit\.com|t\.me|telegram\.me|wa\.me|spotify\.com|soundcloud\.com|github\.com|medium\.com)[^\s]*)'
    urls = re.findall(url_pattern, raw_text, re.IGNORECASE)
    clean_text = raw_text
    for u in urls:
        clean_text = clean_text.replace(u, '')
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    categories = {
        "facebook": r'facebook\.com|fb\.com|fb\.watch|fb\.gg',
        "instagram": r'instagram\.com|ig\.me',
        "x (twitter)": r'twitter\.com|x\.com',
        "tiktok": r'tiktok\.com|vt\.tiktok\.com',
        "youtube": r'youtube\.com|youtu\.be',
        "threads": r'threads\.net',
        "linkedin": r'linkedin\.com',
        "pinterest": r'pinterest\.com|pin\.it',
        "snapchat": r'snapchat\.com',
        "twitch": r'twitch\.tv',
        "discord": r'discord\.gg|discord\.com',
        "reddit": r'reddit\.com',
        "telegram": r't\.me|telegram\.me',
        "whatsapp": r'wa\.me|api\.whatsapp\.com',
        "spotify": r'spotify\.com',
        "soundcloud": r'soundcloud\.com',
        "github": r'github\.com',
        "medium": r'medium\.com'
    }

    grouped = {}
    for url in urls:
        href = url if url.startswith('http') else 'https://' + url
        matched = False
        for cat, pattern in categories.items():
            if re.search(pattern, url, re.IGNORECASE):
                grouped.setdefault(cat, []).append(href)
                matched = True
                break
        if not matched:
            grouped.setdefault("link sosmed", []).append(href)

    sosmed_text = ""
    if grouped:
        sosmed_text += "\n\n"
        links_list = []
        for cat, links in grouped.items():
            for i, href in enumerate(links):
                label = cat if len(links) == 1 else f"{cat} {i+1}"
                links_list.append(f"🔗 <a href='{href}'>{label}</a>")
        sosmed_text += "\n".join(links_list)

    return clean_text, sosmed_text

# ==========================================
# 6. INSTANCE APP PYROGRAM
# ==========================================
app = Client("menfess_engine", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN)

# Helper Ambil ID Bot Aktif Secara Dinamis
async def get_bot_id(client):
    if not hasattr(client, "me_id"):
        me = await client.get_me()
        client.me_id = me.id
        client.me_username = me.username
        client.me_name = me.first_name
    return client.me_id

# ==========================================
# 7. ROUTING PERINTAH & ACTIONS
# ==========================================

# --- POINT 1 & 2: /START SELESAI & PIN GENDER ---
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(client, message):
    user_id = message.from_user.id
    banned = db_query("SELECT id FROM banned_users WHERE id = ?", (user_id,), fetch="one")
    if banned: return

    # Inisialisasi User Baru
    user_exist = db_query("SELECT id FROM users WHERE id = ?", (user_id,), fetch="one")
    if not user_exist:
        db_query("INSERT INTO users (id, kuota) VALUES (?, 0)", (user_id,), commit=True)

    teks_start = (f"👋 **Selamat datang di {NAMA_BOT}!**\n\n"
                  "Platform Menfess & Bilik Rahasia antar lawan jenis.\n"
                  "🔒 Konten aman, anti-save & anti-forward (`protect_content`).\n"
                  "⏳ Pesan hancur dalam 1 Jam, bisa diperpanjang via Rating.\n\n"
                  "Silakan tentukan atau ubah identitas gender Anda untuk masuk:")
    await message.reply(teks_start, reply_markup=kb_pilih_gender())

# --- ACTION: HANDLE SELECTION GENDER & HOME ROUTING ---
@app.on_message(filters.private & (filters.text | filters.photo | filters.video))
async def handle_core_messages(client, message):
    user_id = message.from_user.id
    bot_id = await get_bot_id(client)
    text = message.text if message.text else ""

    # Cek Blacklist
    if db_query("SELECT id FROM banned_users WHERE id = ?", (user_id,), fetch="one"):
        return

    # Sembuhkan / Cek konfigurasi default bot ini di tabel config
    cfg_exist = db_query("SELECT bot_id FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
    if not cfg_exist:
        db_query("INSERT INTO bot_config (bot_id, target_channel, post_template, gratis) VALUES (?, ?, ?, 0)",
                 (bot_id, DEFAULT_CHANNEL, DEFAULT_TEMPLATE), commit=True)

    # --- PENGATURAN PILIHAN GENDER (POINT 1 & 2) ---
    if text in ["Laki-laki", "Perempuan"]:
        gender_val = "pria" if text == "Laki-laki" else "wanita"
        old_data = db_query("SELECT pinned_msg FROM users WHERE id = ?", (user_id,), fetch="one")
        if old_data and old_data["pinned_msg"] != 0:
            try: await client.delete_messages(user_id, old_data["pinned_msg"])
            except: pass

        pesan_pin = (f"📌 Status Identitas: **{text.upper()}**\n"
                     f"**MODE STANDBY {client.me_name.upper()} AKTIF**\n\n"
                     "Halaman bersih. Anda sekarang berada di halaman HOME dan siap menerima sebaran pesan lawan jenis sesuai kriteria.")
        
        pin_msg = await message.reply(pesan_pin, reply_markup=kb_home(user_id, bot_id))
        try: await pin_msg.pin(disable_notification=True)
        except: pass

        db_query("UPDATE users SET gender = ?, pinned_msg = ? WHERE id = ?", (gender_val, pin_msg.id, user_id), commit=True)
        # Clear state penulisan jika ada
        client.storage.USER_STATUS = client.storage.__dict__.get("USER_STATUS", {})
        client.storage.USER_STATUS[user_id] = None
        return

    # --- HOME / BACK NAVIGATION ---
    if text == "🏠 HOME":
        # Point 4: Kembali ke Home Tanpa Reset Gender
        user_info = db_query("SELECT gender FROM users WHERE id = ?", (user_id,), fetch="one")
        if not user_info or not user_info["gender"]:
            return await message.reply("Tentukan gender terlebih dahulu:", reply_markup=kb_pilih_gender())
        
        client.storage.USER_STATUS = client.storage.__dict__.get("USER_STATUS", {})
        client.storage.USER_STATUS[user_id] = None
        return await message.reply("Kembali ke Halaman Utama (HOME):", reply_markup=kb_home(user_id, bot_id))

    # --- BUTTON ROUTING MENU UTAMA & SUB-MENU ---
    if text == '📝 POSTING':
        return await message.reply("Menu Posting Menfess & Bilik Rahasia. Pilih opsi:", reply_markup=kb_posting_menu())

    if text in ['👤 Kirim Anonim', '👁️ Tampilkan Nama']:
        mode_val = 1 if text == '👤 Kirim Anonim' else 0
        db_query("UPDATE users SET anon = ? WHERE id = ?", (mode_val, user_id), commit=True)
        
        client.storage.USER_STATUS = client.storage.__dict__.get("USER_STATUS", {})
        client.storage.USER_STATUS[user_id] = "WAITING_MENFESS"
        return await message.reply("✍ **Silakan kirimkan menfess Anda sekarang.**\n(Teks / Foto / Video. Link sosial media di dalam teks otomatis diubah jadi tombol tautan dinamis)", 
                                   reply_markup=ReplyKeyboardMarkup([['🏠 HOME']], resize_keyboard=True))

    if text == '🎧 Pengaturan Menyimak':
        return await message.reply("Jenis konten apa yang ingin Anda terima di DM dari lawan jenis?", reply_markup=kb_menyimak_filter())

    if text in ["Terima Semua Jenis (Teks & Media)", "Terima Media Saja (Foto/Video)", "Terima Teks Saja"]:
        mode_val = "all" if text == "Terima Semua Jenis (Teks & Media)" else ("media" if text == "Terima Media Saja (Foto/Video)" else "teks")
        db_query("UPDATE users SET mode = ? WHERE id = ?", (mode_val, user_id), commit=True)
        return await message.reply("✅ Kriteria filter menyimak berhasil disimpan!", reply_markup=kb_posting_menu())

    if text == '⚧ Ubah Gender':
        # Point 4: Memicu ulang pemilihan gender awal seperti /start
        return await message.reply("Silakan set ulang identitas gender Anda:", reply_markup=kb_pilih_gender())

    if text == '📊 Info Akun':
        u_data = db_query("SELECT kuota FROM users WHERE id = ?", (user_id,), fetch="one")
        c_data = db_query("SELECT gratis FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
        kuota_saat_ini = u_data["kuota"] if u_data else 0
        bot_mode = "Gratis" if (c_data and c_data["gratis"] == 1) else "Berbayar"
        return await message.reply(f"📊 **INFO AKUN ANDA**\n\n🆔 User ID: `{user_id}`\n💎 Sisa Kuota: **{kuota_saat_ini}**\n⚙️ Aturan Bot: **{bot_mode}**", reply_markup=kb_home(user_id, bot_id))

    if text == '💳 Isi Kuota':
        c_data = db_query("SELECT qris_link FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
        qris = c_data["qris_link"] if c_data else ""
        txt_instruksi = "💳 **Silakan transfer dan kirimkan Foto Bukti Pembayaran Anda langsung ke bot ini.**"
        if qris and qris != "Belum disetel":
            try:
                await client.send_photo(chat_id=user_id, photo=qris, caption=txt_instruksi)
                return
            except: pass
        return await message.reply(txt_instruksi)

    # --- HANDLER BACKEND PEMBAYARAN (NON-OWNER SEND PHOTO) ---
    if message.photo and user_id != OWNER_ID and client.storage.__dict__.get("USER_STATUS", {}).get(user_id) != "WAITING_MENFESS":
        kb_confirm = InlineKeyboardMarkup([
            [InlineKeyboardButton("➖", callback_data=f"cnt_{user_id}_4"),
             InlineKeyboardButton("💎 5", callback_data="none"),
             InlineKeyboardButton("➕", callback_data=f"cnt_{user_id}_6")],
            [InlineKeyboardButton("✅ KONFIRMASI", callback_data=f"acc_{user_id}_5")]
        ])
        await client.send_photo(
            chat_id=OWNER_ID,
            photo=message.photo.file_id,
            caption=f"💳 **BUKTI TRANSFERS MASUK**\n\n👤 User: {html.escape(message.from_user.first_name)}\n🆔 ID: `{user_id}`",
            reply_markup=kb_confirm
        )
        return await message.reply("✅ **Bukti pembayaran telah terkirim ke Admin.** Mohon tunggu proses validasi.")

    # --- ADMIN / OWNER INTERFACE CONTROL ---
    is_owner_privilege = (user_id == MAIN_OWNER_ID and not IS_CLONE) or (user_id == OWNER_ID)
    if is_owner_privilege:
        if text == '⚙️ Pengaturan':
            cfg = db_query("SELECT target_channel, qris_link FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
            ch_target = cfg["target_channel"] if cfg else DEFAULT_CHANNEL
            qr_target = cfg["qris_link"] if cfg else "Belum disetel"
            
            kb_set = InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Edit Template", callback_data="set_tpl"),
                 InlineKeyboardButton("📢 Edit Target Channel", callback_data="set_ch")],
                [InlineKeyboardButton("🖼️ Edit Link QRIS", callback_data="set_qris")]
            ])
            return await message.reply(
                f"⚙️ **PENGATURAN ENGINE**\n\n"
                f"📢 **ID Target Channel:** `{ch_target}`\n"
                f"🖼️ **Link QRIS Asset:** `{qr_target}`",
                reply_markup=kb_set
            )

        if text.startswith("🔓 Mode: GRATIS") or text.startswith("🔒 Mode: BERBAYAR"):
            # Point 3: Mengubah status dalam 1 tombol yang berubah otomatis
            cfg = db_query("SELECT gratis FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
            now_gratis = cfg["gratis"] if cfg else 0
            new_gratis = 0 if now_gratis == 1 else 1
            db_query("UPDATE bot_config SET gratis = ? WHERE bot_id = ?", (new_gratis, bot_id), commit=True)
            
            str_alert = "Diubah menjadi Mode GRATIS!" if new_gratis else "Diubah menjadi Mode BERBAYAR!"
            await message.reply(f"✅ {str_alert}")
            return await message.reply("Menu Utama Diperbarui:", reply_markup=kb_home(user_id, bot_id))

        if text == '📢 Broadcast':
            client.storage.USER_STATUS = client.storage.__dict__.get("USER_STATUS", {})
            client.storage.USER_STATUS[user_id] = "WAITING_BC"
            return await message.reply("📢 **Silakan ketik / kirim materi broadcast Anda:**", reply_markup=ReplyKeyboardMarkup([['🏠 HOME']], resize_keyboard=True))

        if text == '👤 Mode User':
            # Mengelabui UI sementara menjadi user biasa
            return await message.reply("Berpindah ke tampilan User biasa.", 
                                       reply_markup=ReplyKeyboardMarkup([['📝 POSTING', '🤖 Buat/Kelola Clone'], ['💳 Isi Kuota', '📊 Info Akun']], resize_keyboard=True))

    # --- PROCESS BROADCAST HANDLING ---
    if is_owner_privilege and client.storage.__dict__.get("USER_STATUS", {}).get(user_id) == "WAITING_BC":
        client.storage.USER_STATUS[user_id] = None
        all_users = db_query("SELECT id FROM users")
        await message.reply("⏳ Memulai pemrosesan kiriman massal (Broadcast)...")
        bc_count = 0
        for u in all_users:
            try:
                if message.photo:
                    await client.send_photo(chat_id=u["id"], photo=message.photo.file_id, caption=f"📢 **INFO ADMIN**\n\n{message.caption or ''}")
                elif message.video:
                    await client.send_video(chat_id=u["id"], video=message.video.file_id, caption=f"📢 **INFO ADMIN**\n\n{message.caption or ''}")
                else:
                    await client.send_message(chat_id=u["id"], text=f"📢 **INFO ADMIN**\n\n{message.text}")
                bc_count += 1
                await asyncio.sleep(0.05)
            except: continue
        return await message.reply(f"✅ Broadcast Selesai! Pesan sukses diterima oleh {bc_count} user.", reply_markup=kb_home(user_id, bot_id))

    # --- PROCESS CONFIGURATION INPUTS ---
    st_conf = client.storage.__dict__.get("USER_STATUS", {}).get(user_id)
    if is_owner_privilege and st_conf in ["EDIT_TPL", "EDIT_CH", "EDIT_QRIS"]:
        client.storage.USER_STATUS[user_id] = None
        if st_conf == "EDIT_TPL":
            db_query("UPDATE bot_config SET post_template = ? WHERE bot_id = ?", (text, bot_id), commit=True)
            await message.reply("✅ Template postingan berhasil diperbarui!")
        elif st_conf == "EDIT_CH":
            db_query("UPDATE bot_config SET target_channel = ? WHERE bot_id = ?", (text.strip(), bot_id), commit=True)
            await message.reply(f"✅ Target channel ID berhasil diubah menjadi: `{text.strip()}`")
        elif st_conf == "EDIT_QRIS":
            db_query("UPDATE bot_config SET qris_link = ? WHERE bot_id = ?", (text.strip(), bot_id), commit=True)
            await message.reply("✅ Tautan media QRIS berhasil diperbarui!")
        return

    # --- SYSTEM MANAGEMENT CLONING SYSTEM ---
    if text == '🤖 Buat/Kelola Clone':
        client.storage.USER_STATUS = client.storage.__dict__.get("USER_STATUS", {})
        client.storage.USER_STATUS[user_id] = "WAITING_TOKEN_CLONE"
        
        clones = db_query("SELECT * FROM clones")
        kb_list = []
        for c in clones:
            if user_id == MAIN_OWNER_ID:
                kb_list.append([InlineKeyboardButton(f"🛑 Kill Clone {c['token'][:8]}... (PID: {c['pid']})", callback_data=f"kill_{c['token'][-8:]}")])
            elif c["owner"] == user_id:
                kb_list.append([InlineKeyboardButton(f"🛑 Matikan Clone Saya ({c['token'][:8]}...)", callback_data=f"kill_{c['token'][-8:]}")])
                
        guide_txt = ("🤖 **PANEL CLONING ENGINE SYSTEM**\n\n"
                     "1. Buat bot baru di @BotFather lalu copy Tokennya.\n"
                     "2. **Kirim / Paste token** tersebut kesini sekarang untuk menghidupkan clone engine Anda.")
        if kb_list:
            await message.reply("📋 **Daftar Engine Clone Aktif Saat Ini:**", reply_markup=InlineKeyboardMarkup(kb_list))
        return await message.reply(guide_txt, reply_markup=ReplyKeyboardMarkup([['🏠 HOME']], resize_keyboard=True))

    if client.storage.__dict__.get("USER_STATUS", {}).get(user_id) == "WAITING_TOKEN_CLONE":
        clean_token = text.strip()
        if ":" not in clean_token or len(clean_token) < 25:
            return await message.reply("❌ Format token BotFather tidak sah! Silakan kirim ulang token yang valid.")
        
        client.storage.USER_STATUS[user_id] = None
        await message.reply("⏳ Menginisiasi core engine sub-process server clone...")
        
        try:
            env = os.environ.copy()
            env["BOT_TOKEN"] = clean_token
            env["IS_CLONE"] = "True"
            env["OWN_ID"] = str(user_id)
            
            # Ekstrak target channel default milik master untuk clone baru
            cfg_mst = db_query("SELECT target_channel FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
            ch_current = cfg_mst["target_channel"] if cfg_mst else DEFAULT_CHANNEL
            env["CH_ID"] = str(ch_target_fix := ch_current)
            
            script_path = os.path.abspath(sys.argv[0])
            proc = subprocess.Popen([sys.executable, script_path], env=env)
            
            db_query("INSERT OR REPLACE INTO clones (token, owner, ch, pid) VALUES (?, ?, ?, ?)",
                     (clean_token, user_id, ch_target_fix, proc.pid), commit=True)
            
            return await message.reply(f"✅ **Bot Clone Berhasil Diaktifkan!**\n⚡ PID Engine: `{proc.pid}`\nSilakan buka bot clone Anda dan tekan /start.", reply_markup=kb_home(user_id, bot_id))
        except Exception as e:
            return await message.reply(f"❌ Gagal meluncurkan clone subprocess: {e}", reply_markup=kb_home(user_id, bot_id))

    # ==========================================
    # 8. UTAMA CORE : PEMROSESAN MENFESS & SEBARAN (POINT 5, 6, 7, 8, 9, 10)
    # ==========================================
    if client.storage.__dict__.get("USER_STATUS", {}).get(user_id) == "WAITING_MENFESS":
        # Validasi Identitas Gender Sebelum Mengirim
        u_info = db_query("SELECT gender, anon, kuota FROM users WHERE id = ?", (user_id,), fetch="one")
        if not u_info or not u_info["gender"]:
            client.storage.USER_STATUS[user_id] = None
            return await message.reply("Aksi dibatalkan. Anda belum memilih gender.", reply_markup=kb_pilih_gender())

        cfg = db_query("SELECT target_channel, post_template, gratis FROM bot_config WHERE bot_id = ?", (bot_id,), fetch="one")
        target_channel = cfg["target_channel"] if cfg else DEFAULT_CHANNEL
        post_template = cfg["post_template"] if cfg else DEFAULT_TEMPLATE
        is_gratis = cfg["gratis"] if cfg else 0

        # Validasi Kuota
        if user_id != OWNER_ID and not is_gratis and u_info["kuota"] <= 0:
            client.storage.USER_STATUS[user_id] = None
            return await message.reply("❌ **Kuota kiriman Anda telah habis!**\nSilakan isi ulang terlebih dahulu.", reply_markup=kb_home(user_id, bot_id))

        raw_input_text = message.text or message.caption or ""
        clean_txt, sosmed_txt = parse_and_extract_links(raw_input_text)
        
        # Penentuan Nama Pengirim
        sender_format = "anonim" if u_info["anon"] == 1 else f"<a href='tg://user?id={user_id}'>{html.escape(message.from_user.first_name)}</a>"
        formatted_body = f"<i>{html.escape(clean_txt)}</i>" if clean_txt else ""
        text_with_links = formatted_body + sosmed_txt

        # Pembuatan Output Teks Sesuai Template (Hanya untuk Channel - POINT 10)
        final_channel_caption = post_template.replace("{TEXT}", text_with_links).replace("{SENDER}", sender_format) + f"\n🤖 *dikirim via {client.me_name}*"

        # --- KIRIM KE CHANNEL UTAMA BOT MASING-MASING (POINT 5 - WAJIB FORMAT ID NUMERIK) ---
        target_channel_id = int(target_channel) if str(target_channel).lstrip('-').isdigit() else target_channel
        
        snt_msg = None
        try:
            if message.photo:
                snt_msg = await client.send_photo(chat_id=target_channel_id, photo=message.photo.file_id, caption=final_channel_caption)
            elif message.video:
                snt_msg = await client.send_video(chat_id=target_channel_id, video=message.video.file_id, caption=final_channel_caption)
            else:
                snt_msg = await client.send_message(chat_id=target_channel_id, text=final_channel_caption)
        except Exception as e:
            client.storage.USER_STATUS[user_id] = None
            return await message.reply(f"❌ Gagal mengirim ke channel target. Pastikan bot sudah menjadi admin di `{target_channel_id}`. Error: {e}", reply_markup=kb_home(user_id, bot_id))

        # Kurangi Kuota Jika Bukan Gratisan & Bukan Owner
        if user_id != OWNER_ID and not is_gratis:
            db_query("UPDATE users SET kuota = MAX(0, kuota - 1) WHERE id = ?", (user_id,), commit=True)

        # Buat Tautan Link Postingan Channel Dinamis (POINT 7)
        ch_raw_str = str(target_channel_id).replace("-100", "")
        link_url = f"https://t.me/c/{ch_raw_str}/{snt_msg.id}"
        kb_to_channel = InlineKeyboardMarkup([[InlineKeyboardButton("Lihat Postingan Channel ↗️", url=link_url)]])
        
        # Kirim Bukti Berhasil ke Pengirim (POINT 7)
        await message.reply("🎉 **Menfess Berhasil Terkirim Secara Global!**", reply_markup=kb_to_channel)

        # --- LOGGING KE OWNER UTAMA / OWNER CLONE (POINT 8 - HANYA LOG + BAN/UNBAN, TANPA SEBARAN DM) ---
        log_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 BAN USER", callback_data=f"ban_{user_id}"),
             InlineKeyboardButton("🔓 UNBAN USER", callback_data=f"unban_{user_id}")]
        ])
        log_caption = f"📩 **LOG MENFESS MASUK**\n\n👤 Pengirim: {html.escape(message.from_user.first_name)} (`{user_id}`)\n📝 Isi Asli:\n{html.escape(raw_input_text)}"
        try:
            if message.photo:
                await client.send_photo(chat_id=OWNER_ID, photo=message.photo.file_id, caption=log_caption, reply_markup=log_kb)
            elif message.video:
                await client.send_video(chat_id=OWNER_ID, video=message.video.file_id, caption=log_caption, reply_markup=log_kb)
            else:
                await client.send_message(chat_id=OWNER_ID, text=log_caption, reply_markup=log_kb)
        except: pass

        # --- SISTEM SEBARAN DM ANTAR GENDER KE SELURUH BOT (POINT 5, 6, 9, 10) ---
        # Mengambil daftar seluruh lawan jenis di database yang menggunakan bot apapun
        target_gender = "wanita" if u_info["gender"] == "pria" else "pria"
        listeners = db_query("SELECT id, mode FROM users WHERE gender = ?", (target_gender,))
        
        # POINT 10: Sebaran antar gender tidak menggunakan format template, melainkan raw text asli + billing rating + protect content
        identitas_tambahan = f"\n\n📢 _Dikirim oleh: {message.from_user.first_name}_" if u_info["anon"] == 0 else ""
        teks_dasar_sebaran = (clean_txt if clean_txt else "") + identitas_tambahan + sosmed_txt
        
        # Simpan struktur postingan ke database untuk pelacakan rating laporan otomatis
        db_query("INSERT OR REPLACE INTO posts (msg_id, bot_id, sender_id, dashboard_msg_id) VALUES (?, ?, ?, ?)",
                 (message.id, bot_id, user_id, snt_msg.id), commit=True)

        is_input_media = True if (message.photo or message.video) else False

        for target in listeners:
            t_id, t_mode = target["id"], target["mode"]
            
            # POINT 8: Owner Utama dan Owner Clone dikecualikan dari sebaran acak antar gender
            if t_id in [MAIN_OWNER_ID, OWNER_ID]:
                continue
                
            # Filter Menyimak Konten
            if t_mode == "media" and not is_input_media: continue
            if t_mode == "teks" and is_input_media: continue

            try:
                # Kirim Notifikasi Bilik Rahasia dengan Fitur Keamanan Sesuai rate_me.py (POINT 9)
                kb_view = InlineKeyboardMarkup([[InlineKeyboardButton("👀 Buka Pesan Rahasia", callback_data=f"view_{message.id}_{user_id}_{bot_id}")]])
                noti = await client.send_message(
                    chat_id=t_id,
                    text="📬 **Ada pesan rahasia baru dari lawan jenis!**\n_Ketuk tombol di bawah untuk membacanya._",
                    reply_markup=kb_view,
                    protect_content=True
                )
                
                # Masukkan ke Worker Auto-Destruct (1 Jam Default)
                n_key = f"{t_id}_{noti.id}"
                expiry_timers[n_key] = time.time() + 3600
                asyncio.create_task(auto_delete_worker(client, t_id, noti.id, n_key))
            except: pass

        # Reset State Status Menfess Selesai
        client.storage.USER_STATUS[user_id] = None
        await message.reply("Kembali ke Halaman Menu Utama:", reply_markup=kb_home(user_id, bot_id))
        return

    # Jika pesan dikirim tanpa menekan tombol menu apa-apa
    if not text.startswith('/'):
        await message.reply("💡 Sila gunakan tombol menu keyboard di bawah untuk berinteraksi dengan sistem bot.", reply_markup=kb_home(user_id, bot_id))

# ==========================================
# 9. CALLBACK INLINE HANDLER (RATING, BAN, CONFIRMATION)
# ==========================================
@app.on_callback_query()
async def global_callback_handler(client, query):
    user_id = query.from_user.id
    bot_id = await get_bot_id(client)
    data = query.data

    # --- ACTION INLINE: BAN & UNBAN SYSTEM (Fitur Asli memfess.py) ---
    if data.startswith("ban_") or data.startswith("unban_"):
        if user_id != OWNER_ID:
            return await query.answer("Akses Ditolak! Anda bukan admin bot clone/master ini.", show_alert=True)
        
        target_uid = int(data.split("_")[1])
        if data.startswith("ban_"):
            db_query("INSERT OR IGNORE INTO banned_users (id) VALUES (?)", (target_uid,), commit=True)
            status_text = "\n\n🚫 **STATUS SENDER: BANNED**"
        else:
            db_query("DELETE FROM banned_users WHERE id = ?", (target_uid,), commit=True)
            status_text = "\n\n✅ **STATUS SENDER: AKTIF**"

        try:
            def strip_status(t):
                return t.replace("\n\n🚫 **STATUS SENDER: BANNED**", "").replace("\n\n✅ **STATUS SENDER: AKTIF**", "")
            
            if query.message.photo or query.message.video:
                orig = strip_status(query.message.caption or "")
                await query.edit_message_caption(caption=orig + status_text, reply_markup=query.message.reply_markup)
            else:
                orig = strip_status(query.message.text or "")
                await query.edit_message_text(text=orig + status_text, reply_markup=query.message.reply_markup)
            await query.answer("Status Akses User Berhasil Diupdate!")
        except Exception as e:
            await query.answer(f"Gagal memperbarui log: {e}", show_alert=True)

    # --- ACTION INLINE: ISI KUOTA MANAGEMENT ---
    elif data.startswith("cnt_"):
        if user_id != OWNER_ID: return
        _, target_id, current_val = data.split("_")
        val = max(1, int(current_val))
        kb_change = InlineKeyboardMarkup([
            [InlineKeyboardButton("➖", callback_data=f"cnt_{target_id}_{val-1}"),
             InlineKeyboardButton(f"💎 {val}", callback_data="none"),
             InlineKeyboardButton("➕", callback_data=f"cnt_{target_id}_{val+1}")],
            [InlineKeyboardButton("✅ KONFIRMASI KUOTA", callback_data=f"acc_{target_id}_{val}")]
        ])
        await query.edit_message_reply_markup(reply_markup=kb_change)

    elif data.startswith("acc_"):
        if user_id != OWNER_ID: return
        _, target_id, add_val = data.split("_")
        
        db_query("UPDATE users SET kuota = kuota + ? WHERE id = ?", (int(add_val), int(target_id)), commit=True)
        
        try:
            if query.message.photo or query.message.video:
                await query.edit_message_caption(caption=(query.message.caption or "") + f"\n\n✅ **SUKSES DITAMBAHKAN +{add_val} KUOTA**")
            else:
                await query.edit_message_text(text=(query.message.text or "") + f"\n\n✅ **SUKSES DITAMBAHKAN +{add_val} KUOTA**")
        except: pass

        try:
            await client.send_message(chat_id=int(target_id), text=f"🎉 **Top Up Berhasil!**\n`+{add_val}` kuota pengiriman baru telah ditambahkan ke akun Anda.")
        except: pass
        await query.answer("Kuota sukses dikirim!")

    # --- ACTION INLINE CONFIG SETTINGS ---
    elif data.startswith("set_"):
        if user_id != OWNER_ID: return
        cmd = data.split("_")[1]
        client.storage.USER_STATUS = client.storage.__dict__.get("USER_STATUS", {})
        
        if cmd == "tpl":
            client.storage.USER_STATUS[user_id] = "EDIT_TPL"
            await query.message.reply_text("📝 **Kirimkan template pesan postingan baru Anda.**\nWajib mengandung parameter `{TEXT}` dan `{SENDER}`.")
        elif cmd == "ch":
            client.storage.USER_STATUS[user_id] = "EDIT_CH"
            await query.message.reply_text("📢 **Kirimkan ID Channel target pengiriman baru Anda.**\nContoh format: `-1003755410515`")
        elif cmd == "qris":
            client.storage.USER_STATUS[user_id] = "EDIT_QRIS"
            await query.message.reply_text("🖼 **Kirimkan tautan link gambar / telegraph QRIS baru Anda:**")
        await query.answer()

    # --- ACTION INLINE: DELETE CLONE ENGINE ---
    elif data.startswith("kill_"):
        target_suffix_token = data.split("_")[1]
        clone_data = db_query("SELECT * FROM clones")
        
        target_clone = None
        for c in clone_data:
            if c["token"].endswith(target_suffix_token):
                target_clone = c
                break
                
        if target_clone:
            if user_id != MAIN_OWNER_ID and target_clone["owner"] != user_id:
                return await query.answer("❌ Anda tidak berhak mematikan engine bot clone ini!", show_alert=True)
            
            db_query("DELETE FROM clones WHERE token = ?", (target_clone["token"],), commit=True)
            
            pid = target_clone["pid"]
            try:
                os.kill(int(pid), signal.SIGTERM)
                kill_msg = f"Engine PID `{pid}` berhasil dihentikan total dari server."
            except:
                kill_msg = f"Engine PID `{pid}` sudah mati sebelumnya di system runtime."
                
            await query.edit_message_text(f"✅ **Bot Clone Sukses Dihapus & Dimatikan!**\n🤖 Token Asset: `{target_clone['token'][:10]}...`\n⚡ Status: {kill_msg}")
        else:
            await query.answer("Data clone tidak ditemukan!", show_alert=True)

    # --- BILIK RAHASIA ACTION: BUKA PESAN RAHASIA (Fitur Asli rate_me.py) ---
    elif data.startswith("view_"):
        _, target_msg_id, sender_id, origin_bot_id = data.split("_")
        target_msg_id, sender_id, origin_bot_id = int(target_msg_id), int(sender_id), int(origin_bot_id)

        # Hapus Notifikasi Masuk
        n_key = f"{user_id}_{query.message.id}"
        expiry_timers[n_key] = 0
        try: await query.message.delete()
        except: pass

        try:
            # Mengambil data preferensi anonim milik pengirim asli
            sender_db = db_query("SELECT anon FROM users WHERE id = ?", (sender_id,), fetch="one")
            is_anon = sender_db["anon"] if sender_db else 1
            
            # Tarik pesan asli langsung melalui API Client
            msg = await client.get_messages(chat_id=sender_id, message_ids=target_msg_id)
            
            sender_name_txt = ""
            if is_anon == 0:
                sender_user_profile = await client.get_users(sender_id)
                sender_name_txt = f"\n\n📢 _Dikirim oleh: {sender_user_profile.first_name}_"

            panduan_waktu = "\n\n⏱ **Info:** _Pesan ini hancur otomatis dalam 1 Jam. Berikan Bintang rating di bawah untuk memperpanjang usia pesan (+1 Bintang = +1 Jam umur konten)._"

            # Duplikasi Konten Dengan Keamanan Tinggi Sesuai Aturan rate_me.py
            if msg.media:
                caption_build = (msg.caption or "") + sender_name_txt + panduan_waktu
                if msg.photo:
                    sent = await client.send_photo(chat_id=user_id, photo=msg.photo.file_id, caption=caption_build, has_spoiler=True, protect_content=True)
                elif msg.video:
                    sent = await client.send_video(chat_id=user_id, video=msg.video.file_id, caption=caption_build, has_spoiler=True, protect_content=True)
                else:
                    sent = await msg.copy(chat_id=user_id, protect_content=True)
            else:
                teks_build = (msg.text or "") + sender_name_txt + panduan_waktu
                sent = await client.send_message(chat_id=user_id, text=teks_build, protect_content=True)

            # Tempel Keyboard Rating Bintang
            await sent.edit_reply_markup(reply_markup=rating_kb(target_msg_id, sent.id, origin_bot_id))

            # Set Timer Hancur Otomatis Pesan Rahasia Terbuka
            timer_key = f"{user_id}_{sent.id}"
            expiry_timers[timer_key] = time.time() + 3600
            asyncio.create_task(auto_delete_worker(client, user_id, sent.id, timer_key))
        except Exception as e:
            logging.error(f"Gagal memuat pesan rahasia: {e}")
            await client.send_message(chat_id=user_id, text="❌ Maaf, pesan rahasia gagal dimuat atau pengirim telah menghapus postingannya.")

    # --- BILIK RAHASIA ACTION: PROSES RATING BINTANG ---
    elif data.startswith("rate_"):
        _, star_rate, msg_id, sent_msg_id, origin_bot_id = data.split("_")
        star_rate, msg_id, sent_msg_id, origin_bot_id = int(star_rate), int(msg_id), int(sent_msg_id), int(origin_bot_id)

        timer_key = f"{user_id}_{sent_msg_id}"
        if timer_key in expiry_timers:
            expiry_timers[timer_key] += (star_rate * 3600)
            waktu_hapus_timestamp = expiry_timers[timer_key]
            # Menyesuaikan zona waktu WIB (GMT+7) tanpa library eksternal
            jam_hapus = datetime.utcfromtimestamp(waktu_hapus_timestamp + (7 * 3600)).strftime('%H:%M')
        else:
            jam_hapus = "N/A"

        kb_voted = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ {star_rate} Bintang | Dihapus jam {jam_hapus} WIB", callback_data="none")]])
        try: await query.message.edit_reply_markup(reply_markup=kb_voted)
        except: pass
        
        await query.answer(f"Terima kasih! Durasi diperpanjang {star_rate} Jam. Pesan rahasia hilang pada {jam_hapus} WIB.", show_alert=True)

        # Update Database Rekap Rating
        db_query(f"UPDATE posts SET r{star_rate} = r{star_rate} + 1 WHERE msg_id = ? AND bot_id = ?", (msg_id, origin_bot_id), commit=True)
        
        # Ambil total rekap rating terbaru untuk diperbarui ke dashboard pengirim
        stats = db_query("SELECT sender_id, dashboard_msg_id, r1, r2, r3, r4, r5 FROM posts WHERE msg_id = ? AND bot_id = ?", (msg_id, origin_bot_id), fetch="one")
        if stats and stats["dashboard_msg_id"]:
            report = (f"📊 **LAPORAN RATING KONTEN ANDA**\n\n"
                      f"⭐ 1: {stats['r1']}x\n⭐ 2: {stats['r2']}x\n"
                      f"⭐ 3: {stats['r3']}x\n⭐ 4: {stats['r4']}x\n"
                      f"⭐ 5: {stats['r5']}x")
            try:
                # Update dashboard laporan rating milik pengirim asli
                await client.edit_message_text(chat_id=stats["sender_id"], message_id=stats["dashboard_msg_id"], text=report)
            except: pass

# ==========================================
# 10. RUNNING ENGINE & AUTO-BOOT CLONES SYSTEM
# ==========================================
def main():
    if not IS_CLONE:
        # Menghidupkan ulang seluruh bot clone secara otomatis saat Master Bot menyala
        active_clones = db_query("SELECT * FROM clones")
        updated_clones = []
        for c in active_clones:
            try:
                env = os.environ.copy()
                env["BOT_TOKEN"] = c["token"]
                env["CH_ID"] = c["ch"]
                env["OWN_ID"] = str(c["owner"])
                env["IS_CLONE"] = "True"
                
                script_path = os.path.abspath(sys.argv[0])
                proc = subprocess.Popen([sys.executable, script_path], env=env)
                
                db_query("UPDATE clones SET pid = ? WHERE token = ?", (proc.pid, c["token"]), commit=True)
                logging.info(f"Auto-Booted Clone {c['token'][:8]}... dengan Engine PID: {proc.pid}")
            except Exception as e:
                logging.error(f"Gagal melakukan booting otomatis clone: {e}")

    logging.info(f"=== Menjalankan Core Engine: {'CLONE_BOT' if IS_CLONE else 'MASTER_BOT'} ===")
    app.run()

if __name__ == '__main__':
    main()
