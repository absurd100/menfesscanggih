import logging, html, os, json, sys, subprocess, asyncio, re, signal
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

# ==========================================
# 1. KONFIGURASI UTAMA & VARIABEL GLOBAL
# ==========================================
TOKEN = os.getenv("BOT_TOKEN", 'isi')
DEFAULT_CHANNEL = os.getenv("CH_ID", 'isi')
MAIN_OWNER_ID = 123456789  # GANTI DENGAN ID ANDA
OWNER_ID = int(os.getenv("OWN_ID", MAIN_OWNER_ID))

IS_CLONE = os.getenv("IS_CLONE", "False") == "True"
suffix = f"_{OWNER_ID}" if IS_CLONE else ""

USER_DATA_FILE = f"user_stats{suffix}.json"
CONFIG_FILE = f"bot_config{suffix}.json"
USERS_LIST_FILE = f"all_users{suffix}.json"
BAN_FILE = f"banned_users{suffix}.json"
CLONE_DB = "permanent_clones.json"

DEFAULT_TEMPLATE = "===================================\n{TEXT}\n\n===================================\n😎 <i>sender</i> {SENDER}"

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# 2. SISTEM KEYBOARD UNIVERSAL (UI)
# ==========================================
USER_KB = ReplyKeyboardMarkup([
    ['📝 Tulis Menfess', '🤖 Buat/Kelola Clone'],
    ['💳 Isi Kuota', '📊 Info Akun']
], resize_keyboard=True)

ADMIN_KB = ReplyKeyboardMarkup([
    ['⚙️ Pengaturan Bot', '📢 Broadcast'],
    ['🔓 Mode Gratis', '🔒 Mode Bayar'],
    ['🤖 Buat/Kelola Clone', '👤 Mode User']
], resize_keyboard=True)

CLONE_ADMIN_KB = ReplyKeyboardMarkup([
    ['⚙️ Pengaturan Bot', '📢 Broadcast'],
    ['🔓 Mode Gratis', '🔒 Mode Bayar'],
    ['🤖 Buat/Kelola Clone', '👤 Mode User']
], resize_keyboard=True)

MODE_MENFESS_KB = ReplyKeyboardMarkup([
    ['👤 Kirim Anonim', '👁️ Tampilkan Nama'],
    ['❌ Batal']
], resize_keyboard=True)

# ==========================================
# 3. DATABASE HELPER
# ==========================================
# BUG FIX #1: Variabel 'default' dipakai di dalam except tapi tidak selalu
# terdefinisi di scope itu. Solusi: tentukan default di awal fungsi.
def load_json(file_name):
    # Tentukan nilai default berdasarkan nama file SEBELUM apapun dijalankan
    is_list_file = any(x in file_name for x in ["all_users", "clones", "permanent", "banned"])
    default = [] if is_list_file else {}

    if not os.path.exists(file_name):
        with open(file_name, "w") as f:
            json.dump(default, f)
        return default

    with open(file_name, "r") as f:
        try:
            data = json.load(f)
            # BUG FIX #2: json.load() bisa return None jika file kosong/corrupt.
            # Kembalikan default jika hasilnya None.
            return data if data is not None else default
        except (json.JSONDecodeError, ValueError):
            # File corrupt, kembalikan default dan tulis ulang file
            logging.warning(f"File {file_name} corrupt atau kosong, direset ke default.")
            with open(file_name, "w") as fw:
                json.dump(default, fw)
            return default

def save_json(file_name, data):
    with open(file_name, "w") as f:
        json.dump(data, f, indent=4)

def is_banned(uid):
    banned = load_json(BAN_FILE)
    # BUG FIX #3: Pastikan banned selalu list sebelum dicek
    if not isinstance(banned, list):
        return False
    return str(uid) in banned

# ==========================================
# 4. REGEX & LINK PARSER
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
# 5. HELPER: SEND MESSAGE KOMPATIBEL
# ==========================================
# BUG FIX #4: Duplikasi try/except TypeError untuk disable_web_page_preview
# di banyak tempat. Dibungkus jadi satu helper function yang reusable.
async def send_message_safe(bot, chat_id, text, **kwargs):
    """Kirim pesan teks dengan fallback LinkPreviewOptions untuk kompatibilitas."""
    try:
        return await bot.send_message(chat_id, text, disable_web_page_preview=True, **kwargs)
    except TypeError:
        from telegram import LinkPreviewOptions
        return await bot.send_message(chat_id, text, link_preview_options=LinkPreviewOptions(is_disabled=True), **kwargs)

# ==========================================
# 6. CALLBACK HANDLERS
# ==========================================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data.startswith("ban_") or data.startswith("unban_"):
        if user_id != OWNER_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)

        uid = data.split("_")[1]
        banned = load_json(BAN_FILE)

        # BUG FIX #5: Paksa database menjadi LIST jika berupa DICT (sudah ada
        # di kode asli tapi letaknya tidak konsisten). Dipusatkan di sini.
        if not isinstance(banned, list):
            banned = []

        if data.startswith("ban_") and uid not in banned:
            banned.append(uid)
            save_json(BAN_FILE, banned)
            status_text = "\n\n🚫 <b>STATUS: BANNED</b>"
        elif data.startswith("unban_") and uid in banned:
            banned.remove(uid)
            save_json(BAN_FILE, banned)
            status_text = "\n\n✅ <b>STATUS: AKTIF</b>"
        else:
            return await query.answer("Status sudah ter-update!")

        try:
            # BUG FIX #6: strip() status lama sebelum append status baru
            # agar tidak terjadi penumpukan status teks jika diklik berkali-kali
            def strip_status(text):
                return text.replace("\n\n🚫 <b>STATUS: BANNED</b>", "").replace("\n\n✅ <b>STATUS: AKTIF</b>", "")

            if query.message.photo or query.message.video:
                orig_text = strip_status(query.message.caption_html or "")
                await query.edit_message_caption(
                    caption=orig_text + status_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=query.message.reply_markup
                )
            else:
                orig_text = strip_status(query.message.text_html or "")
                await query.edit_message_text(
                    text=orig_text + status_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=query.message.reply_markup
                )
            await query.answer("Status User Diperbarui!")
        except Exception as e:
            await query.answer(f"Gagal mengubah log: {e}", show_alert=True)

    elif data.startswith("delclone_"):
        idx = int(data.split("_")[1])
        clones = load_json(CLONE_DB)
        if not isinstance(clones, list):
            clones = []

        if 0 <= idx < len(clones):
            target_clone = clones[idx]

            if user_id != MAIN_OWNER_ID and target_clone.get("owner") != user_id:
                return await query.answer("❌ Anda tidak berhak menghapus clone ini!", show_alert=True)

            removed = clones.pop(idx)
            save_json(CLONE_DB, clones)

            pid_target = removed.get("pid")
            kill_status = "Data dihapus dari database."

            if pid_target:
                try:
                    os.kill(int(pid_target), signal.SIGTERM)
                    kill_status = f"Proses Engine Bot (PID: {pid_target}) BERHASIL dimatikan total."
                except ProcessLookupError:
                    kill_status = f"Proses PID {pid_target} sudah mati sebelumnya di OS."
                except Exception as e:
                    kill_status = f"Gagal menghentikan proses: {e}"

            await query.edit_message_text(
                f"✅ <b>Bot Clone Berhasil Dihapus & Dimatikan!</b>\n\n"
                f"🤖 Token: <code>{removed.get('token', '')[:10]}...</code>\n"
                f"⚡ Status: {kill_status}",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.answer("Gagal: Index clone tidak valid.", show_alert=True)

    elif data.startswith("count_"):
        if user_id != OWNER_ID:
            return
        parts = data.split("_")
        # BUG FIX #7: split("_") pada "count_{tid}_{val}" berbahaya jika tid
        # mengandung underscore (misal user ID negatif). Gunakan maxsplit=2.
        _, tid, val = data.split("_", 2)
        val = max(1, int(val))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➖", callback_data=f"count_{tid}_{val-1}"),
             InlineKeyboardButton(f"💎 {val}", callback_data="n"),
             InlineKeyboardButton("➕", callback_data=f"count_{tid}_{val+1}")],
            [InlineKeyboardButton("✅ KONFIRMASI KUOTA", callback_data=f"acc_{tid}_{val}")]
        ])
        await query.edit_message_reply_markup(reply_markup=kb)

    elif data.startswith("acc_"):
        if user_id != OWNER_ID:
            return
        # BUG FIX #8: Sama seperti di atas, gunakan maxsplit=2
        _, tid, val = data.split("_", 2)
        db_user = load_json(USER_DATA_FILE)
        if not isinstance(db_user, dict):
            db_user = {}
        if tid not in db_user:
            db_user[tid] = {"kuota": 0}
        db_user[tid]["kuota"] = db_user[tid].get("kuota", 0) + int(val)
        save_json(USER_DATA_FILE, db_user)

        # BUG FIX #9: edit_message_caption bisa gagal jika pesan tidak punya caption
        try:
            await query.edit_message_caption(
                caption=(query.message.caption or "") + f"\n\n✅ <b>BERHASIL DITAMBAHKAN +{val} KUOTA</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            await query.edit_message_text(
                text=f"✅ <b>BERHASIL DITAMBAHKAN +{val} KUOTA ke user {tid}</b>",
                parse_mode=ParseMode.HTML
            )

        try:
            await context.bot.send_message(
                int(tid),
                f"🎉 <b>Pembayaran Berhasil!</b>\n+{val} kuota telah ditambahkan ke akun Anda.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    elif data == "cp_tpl":
        if user_id != OWNER_ID:
            return
        context.user_data['state'] = 'edit_template'
        await query.message.reply_text(
            "📝 <b>Kirim template postingan baru.</b>\nPastikan mengandung <code>{TEXT}</code> dan <code>{SENDER}</code>.",
            parse_mode=ParseMode.HTML
        )
    elif data == "cp_ch":
        if user_id != OWNER_ID:
            return
        context.user_data['state'] = 'edit_channel'
        await query.message.reply_text(
            "📢 <b>Kirim ID Channel target baru.</b>\nContoh: <code>-1003755410515</code>",
            parse_mode=ParseMode.HTML
        )
    elif data == "cp_qris":
        if user_id != OWNER_ID:
            return
        context.user_data['state'] = 'edit_qris'
        await query.message.reply_text(
            "🖼️ <b>Kirimkan link gambar / Telegraph baru untuk QRIS Isi Kuota Anda.</b>\nContoh: <code>https://telegra.ph/file/xxx.jpg</code>",
            parse_mode=ParseMode.HTML
        )

    await query.answer()

# ==========================================
# 7. CORE LOGIC (HANDLING PESAN & FORMATTING)
# ==========================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    uid_int = update.effective_user.id
    uid = str(uid_int)
    msg = update.message
    raw_text_input = msg.text or msg.caption or ""

    if is_banned(uid_int):
        return

    list_tombol = [
        '📝 Tulis Menfess', '🤖 Buat/Kelola Clone', '💳 Isi Kuota', '📊 Info Akun',
        '⚙️ Pengaturan Bot', '📢 Broadcast', '🔓 Mode Gratis', '🔒 Mode Bayar',
        '👤 Mode User', '👤 Kirim Anonim', '👁️ Tampilkan Nama', '❌ Batal'
    ]

    if raw_text_input in list_tombol:
        # BUG FIX #10: State harus di-clear saat tombol menu ditekan, KECUALI
        # tombol yang memang memulai state baru. Sebelumnya state di-clear
        # tanpa kondisi yang tepat, menyebabkan alur terpotong.
        if raw_text_input not in ['👤 Kirim Anonim', '👁️ Tampilkan Nama']:
            context.user_data.pop('state', None)

        if raw_text_input == '❌ Batal':
            kb = ADMIN_KB if (uid_int == MAIN_OWNER_ID and not IS_CLONE) else (CLONE_ADMIN_KB if uid_int == OWNER_ID else USER_KB)
            return await msg.reply_text("✅ Aksi dibatalkan. Kembali ke menu utama.", reply_markup=kb)

    state = context.user_data.get('state')

    # Handler foto dari non-owner (bukti pembayaran) - hanya jika BUKAN sedang tulis menfess
    if msg.photo and uid_int != OWNER_ID and state != 'tulis_menfess':
        caption_owner = (
            f"💳 <b>BUKTI PEMBAYARAN BARU</b>\n\n"
            f"👤 Dari: {html.escape(update.effective_user.first_name)}\n"
            f"🆔 ID: <code>{uid}</code>"
        )
        kb_owner = InlineKeyboardMarkup([
            [InlineKeyboardButton("➖", callback_data=f"count_{uid}_4"),
             InlineKeyboardButton("💎 5", callback_data="n"),
             InlineKeyboardButton("➕", callback_data=f"count_{uid}_6")],
            [InlineKeyboardButton("✅ KONFIRMASI", callback_data=f"acc_{uid}_5")]
        ])
        await context.bot.send_photo(
            chat_id=OWNER_ID,
            photo=msg.photo[-1].file_id,
            caption=caption_owner,
            reply_markup=kb_owner,
            parse_mode=ParseMode.HTML
        )
        return await msg.reply_text(
            "✅ <b>Bukti pembayaran terkirim!</b>\nMohon tunggu admin mengonfirmasi.",
            parse_mode=ParseMode.HTML
        )

    # --- STATE: WAITING CLONE TOKEN ---
    if state == 'waiting_clone':
        token_clean = raw_text_input.strip()
        if ":" not in token_clean or len(token_clean) < 30:
            return await msg.reply_text(
                "❌ <b>Format Token salah!</b>\nKirim ulang token valid dari @BotFather atau klik ❌ Batal.",
                parse_mode=ParseMode.HTML
            )

        context.user_data.clear()
        kb_fail = ADMIN_KB if (uid_int == MAIN_OWNER_ID and not IS_CLONE) else (CLONE_ADMIN_KB if uid_int == OWNER_ID else USER_KB)
        await msg.reply_text("⏳ Menghidupkan core server clone...")

        try:
            clones = load_json(CLONE_DB)
            if not isinstance(clones, list):
                clones = []

            env = os.environ.copy()
            env["BOT_TOKEN"] = token_clean
            env["IS_CLONE"] = "True"
            env["OWN_ID"] = str(uid_int)

            script_path = os.path.abspath(sys.argv[0])
            proc = subprocess.Popen([sys.executable, script_path], env=env)

            clones.append({
                "token": token_clean,
                "owner": uid_int,
                "ch": DEFAULT_CHANNEL,
                "pid": proc.pid
            })
            save_json(CLONE_DB, clones)

            return await msg.reply_text(
                f"✅ <b>Bot Clone Berhasil Diaktifkan!</b>\n\n"
                f"👤 <b>Owner Akses:</b> <a href='tg://user?id={uid_int}'>{html.escape(update.effective_user.first_name)}</a>\n"
                f"⚙️ <b>Sistem PID:</b> <code>{proc.pid}</code>\n\n"
                f"Silakan buka bot clone baru Anda lalu tekan /start.",
                reply_markup=kb_fail,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            return await msg.reply_text(f"❌ Gagal meluncurkan clone: {e}", reply_markup=kb_fail)

    # --- STATE: BROADCAST ---
    if uid_int == OWNER_ID and state == 'waiting_bc':
        context.user_data.clear()
        all_users = load_json(USERS_LIST_FILE)
        # BUG FIX #11: Pastikan all_users adalah list sebelum diiterasi
        if not isinstance(all_users, list):
            all_users = []
        count = 0
        await msg.reply_text("⏳ Sedang mengirim broadcast...")
        for u in all_users:
            try:
                if msg.photo:
                    await context.bot.send_photo(
                        u, msg.photo[-1].file_id,
                        caption=f"📢 <b>INFO ADMIN</b>\n\n{html.escape(raw_text_input)}",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await send_message_safe(
                        context.bot, u,
                        f"📢 <b>INFO ADMIN</b>\n\n{html.escape(raw_text_input)}",
                        parse_mode=ParseMode.HTML
                    )
                count += 1
                await asyncio.sleep(0.05)
            except Exception:
                continue
        return await msg.reply_text(
            f"✅ <b>Broadcast Selesai!</b> Pesan terkirim ke {count} user.",
            parse_mode=ParseMode.HTML
        )

    # --- STATE: EDIT CONFIG ---
    if uid_int == OWNER_ID and state in ['edit_template', 'edit_channel', 'edit_qris']:
        context.user_data.clear()
        cfg = load_json(CONFIG_FILE)
        if not isinstance(cfg, dict):
            cfg = {}
        if state == 'edit_template':
            cfg["post_template"] = raw_text_input
            await msg.reply_text("✅ Template berhasil diperbarui!")
        elif state == 'edit_channel':
            cfg["target_channel"] = raw_text_input.strip()
            await msg.reply_text(f"✅ Target ID pengiriman diubah ke: {raw_text_input}")
        elif state == 'edit_qris':
            cfg["qris_link"] = raw_text_input.strip()
            await msg.reply_text("✅ Link QRIS/Gambar Isi Kuota berhasil diperbarui!")
        save_json(CONFIG_FILE, cfg)
        return

    # --- STATE: TULIS MENFESS ---
    if state == 'tulis_menfess':
        db = load_json(USER_DATA_FILE)
        cfg = load_json(CONFIG_FILE)

        # BUG FIX #12: Pastikan db dan cfg selalu dict, bukan tipe lain
        if not isinstance(db, dict):
            db = {}
        if not isinstance(cfg, dict):
            cfg = {}

        if uid_int != OWNER_ID and not cfg.get("gratis", False) and db.get(uid, {}).get("kuota", 0) <= 0:
            context.user_data.clear()
            return await msg.reply_text(
                "❌ <b>Kuota Anda habis!</b>\nSilakan isi ulang kuota.",
                reply_markup=USER_KB,
                parse_mode=ParseMode.HTML
            )

        # BUG FIX #13: Cek apakah pesan punya konten (teks atau media)
        # Kode asli tidak menangani kasus pesan kosong tanpa caption saat kirim media
        if not raw_text_input and not msg.photo and not msg.video:
            return await msg.reply_text("❌ Pesan tidak boleh kosong. Kirim teks, foto, atau video.")

        clean_text, sosmed_text = parse_and_extract_links(raw_text_input)

        mode_kirim = context.user_data.get('menfess_mode', 'anonim')
        first_name = update.effective_user.first_name or "User"
        sender = "anonim" if mode_kirim == "anonim" else f"<a href='tg://user?id={uid_int}'>{html.escape(first_name)}</a>"

        formatted_clean_text = f"<i>{html.escape(clean_text)}</i>" if clean_text else ""
        text_with_sosmed = formatted_clean_text + sosmed_text

        template = cfg.get("post_template", DEFAULT_TEMPLATE)
        base_text = template.replace("{TEXT}", text_with_sosmed).replace("{SENDER}", sender)

        bot_me = await context.bot.get_me()
        footer_bot = f"\n🤖 <i>dikirim via</i> <a href='https://t.me/{bot_me.username}?start=help'>{html.escape(bot_me.first_name)}</a>"

        final_caption = base_text + footer_bot

        try:
            target_id = str(cfg.get("target_channel", DEFAULT_CHANNEL)).strip()

            if msg.photo:
                snt = await context.bot.send_photo(
                    target_id, msg.photo[-1].file_id,
                    caption=final_caption,
                    parse_mode=ParseMode.HTML
                )
            elif msg.video:
                snt = await context.bot.send_video(
                    target_id, msg.video.file_id,
                    caption=final_caption,
                    parse_mode=ParseMode.HTML
                )
            else:
                snt = await send_message_safe(
                    context.bot, target_id,
                    final_caption,
                    parse_mode=ParseMode.HTML
                )

            # Kurangi kuota setelah berhasil kirim
            if uid_int != OWNER_ID and not cfg.get("gratis", False):
                if uid in db and "kuota" in db[uid]:
                    db[uid]["kuota"] = max(0, db[uid]["kuota"] - 1)
                    save_json(USER_DATA_FILE, db)

            # Log ke owner
            log_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🚫 BAN USER", callback_data=f"ban_{uid}"),
                InlineKeyboardButton("✅ UNBAN", callback_data=f"unban_{uid}")
            ]])
            log_text = (
                f"📩 <b>LOG MENFESS MASUK</b>\n\n"
                f"<b>Sender:</b> {html.escape(first_name)} (<code>{uid}</code>)\n"
                f"<b>Tipe:</b> {mode_kirim.upper()}\n"
                f"<b>Isi Asli:</b>\n{html.escape(raw_text_input)}"
            )

            if msg.photo:
                await context.bot.send_photo(
                    OWNER_ID, photo=msg.photo[-1].file_id,
                    caption=log_text, reply_markup=log_kb,
                    parse_mode=ParseMode.HTML
                )
            elif msg.video:
                await context.bot.send_video(
                    OWNER_ID, video=msg.video.file_id,
                    caption=log_text, reply_markup=log_kb,
                    parse_mode=ParseMode.HTML
                )
            else:
                await send_message_safe(
                    context.bot, OWNER_ID,
                    log_text, reply_markup=log_kb,
                    parse_mode=ParseMode.HTML
                )

            context.user_data.clear()
            kb = ADMIN_KB if (uid_int == MAIN_OWNER_ID and not IS_CLONE) else (CLONE_ADMIN_KB if uid_int == OWNER_ID else USER_KB)

            # BUG FIX #14: Pembuatan link post bisa salah jika target_id adalah
            # username (@channel) — harus dihandle terpisah dari ID numerik
            if target_id.lstrip('-').isdigit():
                # Numeric ID
                if target_id.startswith("-100"):
                    link_post = f"https://t.me/c/{target_id[4:]}/{snt.message_id}"
                else:
                    link_post = f"https://t.me/c/{target_id.lstrip('-')}/{snt.message_id}"
            else:
                # Username (@channel)
                link_post = f"https://t.me/{target_id.replace('@', '')}/{snt.message_id}"

            await msg.reply_text(
                "🎉 <b>Menfess Berhasil Terkirim!</b>",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
            return await msg.reply_text(
                "Melihat hasil kiriman Anda:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Lihat Postingan ↗️", url=link_post)]])
            )

        except Exception as e:
            context.user_data.clear()
            return await msg.reply_text(f"❌ Gagal mengirim. Cek admin channel: {e}")

    # ==========================================
    # BLOK B: ROUTING TOMBOL UTAMA (MENU)
    # ==========================================
    if raw_text_input == '🤖 Buat/Kelola Clone':
        context.user_data['state'] = 'waiting_clone'
        clones = load_json(CLONE_DB)
        if not isinstance(clones, list):
            clones = []

        kb_buttons = []
        for i, c in enumerate(clones):
            if uid_int == MAIN_OWNER_ID:
                kb_buttons.append([InlineKeyboardButton(
                    f"🛑 Matikan Clone {i+1} (PID: {c.get('pid', 'N/A')})",
                    callback_data=f"delclone_{i}"
                )])
            elif c.get("owner") == uid_int:
                kb_buttons.append([InlineKeyboardButton(
                    f"🛑 Matikan Clone Saya ({c.get('token', '')[:10]}...)",
                    callback_data=f"delclone_{i}"
                )])

        msg_text = (
            "🤖 <b>PANEL CLONING SYSTEM MENFESS</b>\n\n"
            "<b>Cara Mengkloning:</b>\n"
            "1. Ambil token bot baru dari @BotFather.\n"
            "2. <b>Kirimkan / Paste Token</b> tersebut ke sini sekarang.\n"
        )
        if kb_buttons:
            await msg.reply_text(
                "📋 <b>Daftar Bot Clone Aktif (Gunakan Tombol Untuk Mematikan):</b>",
                reply_markup=InlineKeyboardMarkup(kb_buttons),
                parse_mode=ParseMode.HTML
            )
        return await msg.reply_text(
            msg_text,
            reply_markup=ReplyKeyboardMarkup([['❌ Batal']], resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )

    if uid_int == OWNER_ID:
        if raw_text_input == '⚙️ Pengaturan Bot':
            cfg = load_json(CONFIG_FILE)
            if not isinstance(cfg, dict):
                cfg = {}
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Edit Template", callback_data="cp_tpl"),
                 InlineKeyboardButton("📢 Edit Channel", callback_data="cp_ch")],
                [InlineKeyboardButton("🖼️ Edit QRIS/Gambar", callback_data="cp_qris")]
            ])
            return await msg.reply_text(
                f"⚙️ <b>PENGATURAN</b>\n\n"
                f"<b>Target ID Channel:</b> <code>{cfg.get('target_channel', DEFAULT_CHANNEL)}</code>\n"
                f"<b>Link QRIS Anda:</b> <code>{cfg.get('qris_link', 'Belum disetel')}</code>\n\n"
                f"Contoh template:\n"
                f"<code>===================================\n{{TEXT}}\n\n==================================\n😎 <i>sender</i> : {{SENDER}}</code>",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )

        if raw_text_input == '📢 Broadcast':
            context.user_data['state'] = 'waiting_bc'
            return await msg.reply_text(
                "📢 <b>Kirim pesan broadcast Anda sekarang.</b>",
                reply_markup=ReplyKeyboardMarkup([['❌ Batal']], resize_keyboard=True),
                parse_mode=ParseMode.HTML
            )

        if raw_text_input == '🔓 Mode Gratis':
            cfg = load_json(CONFIG_FILE)
            if not isinstance(cfg, dict): cfg = {}
            cfg["gratis"] = True
            save_json(CONFIG_FILE, cfg)
            return await msg.reply_text("✅ Mode GRATIS diaktifkan!")

        if raw_text_input == '🔒 Mode Bayar':
            cfg = load_json(CONFIG_FILE)
            if not isinstance(cfg, dict): cfg = {}
            cfg["gratis"] = False
            save_json(CONFIG_FILE, cfg)
            return await msg.reply_text("✅ Mode BERBAYAR diaktifkan!")

        if raw_text_input == '👤 Mode User':
            return await msg.reply_text("Berpindah ke tampilan User.", reply_markup=USER_KB)

    if raw_text_input == '📝 Tulis Menfess':
        return await msg.reply_text("Pilih mode pengiriman Anda:", reply_markup=MODE_MENFESS_KB)

    if raw_text_input in ['👤 Kirim Anonim', '👁️ Tampilkan Nama']:
        context.user_data['state'] = 'tulis_menfess'
        context.user_data['menfess_mode'] = 'anonim' if raw_text_input == '👤 Kirim Anonim' else 'nama'
        return await msg.reply_text(
            "✍ <b>Silakan ketik menfess Anda. (Link di tengah akan terdeteksi otomatis)</b>",
            reply_markup=ReplyKeyboardMarkup([['❌ Batal']], resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )

    if raw_text_input == '💳 Isi Kuota':
        cfg = load_json(CONFIG_FILE)
        if not isinstance(cfg, dict): cfg = {}
        img_qris = cfg.get("qris_link", "").strip()
        text_instruction = "💳 Kirim bukti transfer kuota Anda langsung ke chat bot ini."

        if img_qris and img_qris not in ("Belum disetel", ""):
            try:
                await context.bot.send_photo(
                    chat_id=uid_int,
                    photo=img_qris,
                    caption=text_instruction,
                    parse_mode=ParseMode.HTML
                )
                return
            except Exception as e:
                logging.error(f"Gagal memuat gambar QRIS dari link: {e}")

        return await msg.reply_text(text_instruction, parse_mode=ParseMode.HTML)

    if raw_text_input == '📊 Info Akun':
        db_user = load_json(USER_DATA_FILE)
        if not isinstance(db_user, dict): db_user = {}
        cfg_info = load_json(CONFIG_FILE)
        if not isinstance(cfg_info, dict): cfg_info = {}
        kuota_user = db_user.get(uid, {}).get('kuota', 0)
        status_mode = "Gratis" if cfg_info.get("gratis", False) else "Berbayar"
        return await msg.reply_text(
            f"📊 <b>INFO AKUN</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"💎 Sisa Kuota: <b>{kuota_user}</b>\n"
            f"⚙️ Mode Bot: <b>{status_mode}</b>",
            parse_mode=ParseMode.HTML
        )

    # Pesan tidak dikenal
    if not raw_text_input.startswith('/'):
        kb = ADMIN_KB if (uid_int == MAIN_OWNER_ID and not IS_CLONE) else (CLONE_ADMIN_KB if uid_int == OWNER_ID else USER_KB)
        await msg.reply_text("💡 Gunakan tombol menu untuk berinteraksi.", reply_markup=kb)

# ==========================================
# 8. START COMMAND
# ==========================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid_int = update.effective_user.id
    users = load_json(USERS_LIST_FILE)
    # BUG FIX #15: Pastikan users adalah list sebelum dioperasikan
    if not isinstance(users, list):
        users = []
    if str(uid_int) not in users:
        users.append(str(uid_int))
        save_json(USERS_LIST_FILE, users)

    db = load_json(USER_DATA_FILE)
    if not isinstance(db, dict):
        db = {}
    if str(uid_int) not in db:
        db[str(uid_int)] = {"kuota": 0}
        save_json(USER_DATA_FILE, db)

    context.user_data.clear()
    kb = ADMIN_KB if (uid_int == MAIN_OWNER_ID and not IS_CLONE) else (CLONE_ADMIN_KB if uid_int == OWNER_ID else USER_KB)
    await update.message.reply_text(
        "👋 <b>Selamat Datang! Bot Menfess canggih siap digunakan.\n\n"
        "bot menfess publik canggih dengan fitur :\n"
        "• buat clone botmu di bawah\n"
        "• owner clone bot bisa mengaktifkan mode gratis atau berbayar\n"
        "• mode pengirim anonim atau terlihat\n"
        "• link sosmed dicantumkan di postingan dengan rapi</b>\n\n"
        "created by : ano\nchannel dukungan : @menfesscanggih",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

# ==========================================
# 9. MAIN & AUTO-BOOT CLONE
# ==========================================
def main():
    application = Application.builder().token(TOKEN).build()

    if not IS_CLONE:
        clones = load_json(CLONE_DB)
        if not isinstance(clones, list):
            clones = []
        updated_clones = []
        for c in clones:
            try:
                env = os.environ.copy()
                env["BOT_TOKEN"] = c.get('token', '')
                env["CH_ID"] = c.get('ch', DEFAULT_CHANNEL)
                env["OWN_ID"] = str(c.get('owner', OWNER_ID))
                env["IS_CLONE"] = "True"

                if env["BOT_TOKEN"]:
                    script_path = os.path.abspath(sys.argv[0])
                    proc = subprocess.Popen([sys.executable, script_path], env=env)
                    c['pid'] = proc.pid
                    updated_clones.append(c)
                    logging.info(f"Auto-Boot Clone {c.get('token')[:10]}... PID: {proc.pid}")
            except Exception as e:
                logging.error(f"Gagal memuat boot-clone: {e}")
        save_json(CLONE_DB, updated_clones)

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND,
            handle_message
        )
    )

    logging.info(f"=== Running Engine: {'CLONE_BOT' if IS_CLONE else 'MASTER_BOT'} ===")
    application.run_polling()

if __name__ == '__main__':
    main()
