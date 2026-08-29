import os
import json
import asyncio
import sqlite3
import tempfile
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google import genai

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from voice_player import get_voice_player

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN غير موجود في .env")
if not GEMINI_KEY:
    raise RuntimeError("GEMINI_KEY غير موجود في .env")

MODRINTH_API = "https://api.modrinth.com/v2"
DB_FILE = "bot.db"
MOJANG_MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2"
PAPER_API = "https://api.papermc.io/v2"
SERVER_ROOT = Path("servers")
AFK_ROOT = Path("afk-client")
afk_processes = {}
voice_player = None

LANGUAGES = {
    "ar": ("🇲🇦 العربية والدارجة", "🇲🇦"), "en": ("🇬🇧 English", "🇬🇧"),
    "fr": ("🇫🇷 Français", "🇫🇷"), "es": ("🇪🇸 Español", "🇪🇸"),
    "de": ("🇩🇪 Deutsch", "🇩🇪"), "pt": ("🇵🇹 Português", "🇵🇹"),
    "tr": ("🇹🇷 Türkçe", "🇹🇷"), "ru": ("🇷🇺 Русский", "🇷🇺"),
    "zh": ("🇨🇳 中文", "🇨🇳"), "ja": ("🇯🇵 日本語", "🇯🇵"),
    "ko": ("🇰🇷 한국어", "🇰🇷"),
}

gemini_client = genai.Client(api_key=GEMINI_KEY)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            user_id INTEGER NOT NULL,
            project_id TEXT NOT NULL,
            PRIMARY KEY (user_id, project_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            version_id TEXT
        )
    """)
    conn.execute("CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'ar')")
    conn.execute("""CREATE TABLE IF NOT EXISTS servers (
        user_id INTEGER NOT NULL, name TEXT NOT NULL, host TEXT NOT NULL,
        port INTEGER NOT NULL DEFAULT 25565, username TEXT, secret TEXT,
        server_type TEXT NOT NULL DEFAULT 'external', PRIMARY KEY (user_id, name)
    )""")
    conn.commit()
    conn.close()


def add_follow(user_id, project_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR IGNORE INTO follows (user_id, project_id) VALUES (?, ?)",
        (user_id, project_id),
    )
    conn.commit()
    conn.close()


def remove_follow(user_id, project_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "DELETE FROM follows WHERE user_id = ? AND project_id = ?",
        (user_id, project_id),
    )
    conn.commit()
    conn.close()


def get_follows(user_id):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT project_id FROM follows WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_all_follows():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT user_id, project_id FROM follows"
    ).fetchall()
    conn.close()
    return rows


def get_saved_version(project_id):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute(
        "SELECT version_id FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else None


def save_version(project_id, version_id):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        INSERT INTO projects (project_id, version_id)
        VALUES (?, ?)
        ON CONFLICT(project_id)
        DO UPDATE SET version_id = excluded.version_id
    """, (project_id, version_id))
    conn.commit()
    conn.close()


def get_language(user_id):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT language FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else "ar"


def set_language(user_id, language):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT INTO user_settings(user_id, language) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET language=excluded.language", (user_id, language))
    conn.commit()
    conn.close()


def save_server(user_id, name, host, port, username, secret, server_type):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO servers(user_id,name,host,port,username,secret,server_type) VALUES (?,?,?,?,?,?,?)", (user_id, name, host, port, username, secret, server_type))
    conn.commit()
    conn.close()


def get_servers(user_id):
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT name,host,port,username,secret,server_type FROM servers WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    conn.close()
    return rows


async def modrinth_get(endpoint, params=None):
    url = f"{MODRINTH_API}{endpoint}"
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "MinecraftSearchBot/4.1"},
    ) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def format_number(number):
    try:
        number = int(number)
        if number >= 1_000_000_000:
            return f"{number / 1_000_000_000:.1f}B"
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"
        if number >= 1_000:
            return f"{number / 1_000:.1f}K"
        return str(number)
    except Exception:
        return "0"


async def http_get_json(url, params=None):
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def server_download_info(version, server_type):
    if server_type == "vanilla":
        manifest = await http_get_json(MOJANG_MANIFEST_URL)
        entry = next((v for v in manifest.get("versions", []) if v.get("id") == version), None)
        if not entry:
            return None
        data = await http_get_json(entry["url"])
        server = data.get("downloads", {}).get("server")
        return (server["url"], f"minecraft_server.{version}.jar") if server else None
    if server_type == "fabric":
        loaders = await http_get_json(f"{FABRIC_META}/versions/loader/{version}")
        installers = await http_get_json(f"{FABRIC_META}/versions/installer")
        if not loaders or not installers:
            return None
        loader = loaders[0]["loader"]["version"]
        stable = [item for item in installers if item.get("stable")]
        installer = (stable[0] if stable else installers[0])["version"]
        return (f"{FABRIC_META}/versions/loader/{version}/{loader}/{installer}/server/jar",
                f"fabric-server-{version}-{loader}.jar")
    if server_type == "paper":
        data = await http_get_json(f"{PAPER_API}/projects/paper/versions/{version}/builds")
        builds = data.get("builds", [])
        if not builds:
            return None
        stable = [b for b in builds if b.get("channel") == "default"]
        build = (stable or builds)[-1]
        filename = build["downloads"]["application"]["name"]
        return (f"{PAPER_API}/projects/paper/versions/{version}/builds/{build['build']}/downloads/{filename}", filename)
    return None


async def download_server_jar(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    output.write(chunk)


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 بحث Java", callback_data="menu_java"),
            InlineKeyboardButton("📱 بحث Bedrock", callback_data="menu_bedrock"),
        ],
        [InlineKeyboardButton("📦 Modpacks", callback_data="menu_pack")],
        [InlineKeyboardButton("🖥️ مولد سيرفر", callback_data="menu_server")],
        [InlineKeyboardButton("🎵 Voice Chat", callback_data="menu_music")],
        [InlineKeyboardButton("🤖 Minecraft AI", callback_data="menu_ai")],
        [InlineKeyboardButton("📢 التحديثات", callback_data="menu_updates")],
        [InlineKeyboardButton("🌍 اللغة", callback_data="menu_language"), InlineKeyboardButton("⚙️ الإعدادات", callback_data="menu_settings")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "⚡ Minecraft Search Bot v4.1 ⚡\n\n"
        "🎮 مرحباً بك!\n\n"
        "🔍 ابحث عن Mods\n"
        "📦 ابحث عن Modpacks\n"
        "📥 حمل الإصدارات\n"
        "🔔 تابع التحديثات\n"
        "🤖 اسأل Minecraft AI\n\n"
        "اختار الخدمة:",
        reply_markup=main_keyboard(),
    )


async def choose_filters(query, context, mod_type):
    categories = {
        "الكل": "all",
        "⚡ الأداء": "performance",
        "🏠 الديكور": "decorative",
        "⚙️ التقني": "technology",
        "🔮 السحر": "magic",
        "⚔️ المغامرة": "adventure",
    }
    keyboard = [
        [InlineKeyboardButton(k, callback_data=f"cat_{mod_type}_{v}")]
        for k, v in categories.items()
    ]
    keyboard.append([InlineKeyboardButton("⬅️ الرئيسية", callback_data="back_start")])
    await query.edit_message_text(
        f"🔍 بحث {mod_type}\n\nاختار التصنيف:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def ask_search(query, context, mod_type, category):
    context.user_data["search_type"] = mod_type
    context.user_data["search_category"] = category
    await query.edit_message_text(
        f"🔎 بحث {mod_type}\n\n"
        "كتب اسم المود اللي بغيتي:\n\n"
        "مثال: Sodium / Create / JEI"
    )


async def search_mods(update, context):
    search_text = update.message.text.strip()
    if not search_text:
        return

    mod_type = context.user_data.get("search_type")
    category = context.user_data.get("search_category", "all")

    if not mod_type:
        await ask_ai(update, context)
        return

    wait = await update.message.reply_text(
        f"🔎 كنقلب على: {search_text}..."
    )

    facets = []
    if mod_type == "Java":
        facets.append(["categories:forge", "categories:fabric", "categories:quilt"])
    elif mod_type == "Bedrock":
        facets.append(["categories:bedrock"])

    if category != "all":
        facets.append([f"categories:{category}"])

    params = {
        "query": search_text,
        "limit": 10,
        "facets": json.dumps(facets),
    }

    try:
        result = await modrinth_get("/search", params=params)
        mods = result.get("hits", [])
    except Exception:
        logger.exception("Search error")
        await wait.edit_text("❌ وقع مشكل أثناء البحث.")
        return

    if not mods:
        await wait.edit_text("😢 ما لقيت حتى نتيجة.")
        return

    keyboard = []
    for mod in mods:
        project_id = mod.get("project_id")
        title = mod.get("title", "Unknown")
        downloads = format_number(mod.get("downloads", 0))
        keyboard.append([
            InlineKeyboardButton(
                f"🎮 {title[:45]} • ⬇️ {downloads}",
                callback_data=f"mod_{project_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🏠 الرئيسية", callback_data="back_start")
    ])

    await wait.edit_text(
        f"🔎 نتائج البحث عن: {search_text}\n\n"
        f"✅ النتائج: {len(mods)}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def mod_navigation_keyboard(project_id, is_following=False):
    follow_text = "🔕 إلغاء المتابعة" if is_following else "🔔 متابعة التحديثات"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 معلومات", callback_data=f"page_info_{project_id}"),
            InlineKeyboardButton("📦 الإصدارات", callback_data=f"page_versions_{project_id}"),
        ],
        [
            InlineKeyboardButton("🖼️ الصور", callback_data=f"page_gallery_{project_id}"),
            InlineKeyboardButton("🔗 الروابط", callback_data=f"page_links_{project_id}"),
        ],
        [InlineKeyboardButton("📥 تحميل", callback_data=f"download_menu_{project_id}")],
        [InlineKeyboardButton(follow_text, callback_data=f"follow_{project_id}")],
        [InlineKeyboardButton("⬅️ الرئيسية", callback_data="back_start")],
    ])


async def show_mod(query, context, project_id):
    await query.answer("⏳ كنجيب معلومات المود...")
    try:
        project = await modrinth_get(f"/project/{project_id}")
        versions = await modrinth_get(f"/project/{project_id}/version")
    except Exception:
        logger.exception("Project error")
        await query.message.reply_text("❌ ما قدرتش نجيب معلومات المود.")
        return

    name = project.get("title", "Unknown")
    description = project.get("description", "لا يوجد وصف.")
    if len(description) > 900:
        description = description[:900] + "..."

    downloads = format_number(project.get("downloads", 0))
    followers = format_number(project.get("followers", 0))
    categories = project.get("categories", [])
    categories_text = ", ".join(categories[:8]) if categories else "غير محدد"

    latest = versions[0] if versions else {}
    loaders = latest.get("loaders", [])
    minecraft_versions = latest.get("game_versions", [])
    loader_text = ", ".join(x.upper() for x in loaders) if loaders else "غير معروف"
    minecraft_text = ", ".join(minecraft_versions[:8]) if minecraft_versions else "غير معروف"
    version_name = latest.get("name", latest.get("version_number", "غير معروف"))

    is_following = project_id in get_follows(query.from_user.id)

    text = (
        f"🎮 {name}\n\n"
        f"📝 الوصف:\n{description}\n\n"
        f"📊 التحميلات: {downloads}\n"
        f"👥 المتابعون: {followers}\n\n"
        f"⚙️ Loader: {loader_text}\n"
        f"🎯 Minecraft: {minecraft_text}\n"
        f"📦 آخر إصدار: {version_name}\n"
        f"🏷️ التصنيفات: {categories_text}\n\n"
        f"🔔 المتابعة: {'🟢 مفعلة' if is_following else '⚪ غير مفعلة'}"
    )

    keyboard = mod_navigation_keyboard(project_id, is_following)
    icon_url = project.get("icon_url")

    if icon_url:
        try:
            await query.message.reply_photo(
                photo=icon_url,
                caption=text,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    await query.edit_message_text(text, reply_markup=keyboard)


async def mod_versions_page(query, context, project_id):
    try:
        versions = await modrinth_get(f"/project/{project_id}/version")
    except Exception:
        await query.answer("❌ فشل تحميل الإصدارات", show_alert=True)
        return

    keyboard = []
    for version in versions[:15]:
        version_id = version.get("id")
        name = version.get("name", version.get("version_number", "Unknown"))
        mc_versions = version.get("game_versions", [])
        loaders = version.get("loaders", [])
        mc = mc_versions[0] if mc_versions else "?"
        loader = loaders[0].upper() if loaders else "?"
        keyboard.append([
            InlineKeyboardButton(
                f"📦 {name[:30]} | {mc} | {loader}",
                callback_data=f"version_{project_id}_{version_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ معلومات المود", callback_data=f"page_info_{project_id}")
    ])

    await query.edit_message_text(
        "📦 إصدارات المود\n\nاختار الإصدار:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def version_details_page(query, context, project_id, version_id):
    try:
        version = await modrinth_get(f"/version/{version_id}")
    except Exception:
        await query.answer("❌ فشل تحميل الإصدار", show_alert=True)
        return

    name = version.get("name", version.get("version_number", "Unknown"))
    version_number = version.get("version_number", "Unknown")
    loaders = version.get("loaders", [])
    game_versions = version.get("game_versions", [])
    version_type = version.get("version_type", "release")
    files = version.get("files", [])

    text = (
        f"📦 {name}\n\n"
        f"🔢 Version: {version_number}\n\n"
        f"🎯 Minecraft: {', '.join(game_versions)}\n\n"
        f"⚙️ Loader: {', '.join(x.upper() for x in loaders)}\n\n"
        f"🏷️ النوع: {version_type}\n\n"
        f"📁 الملفات: {len(files)}"
    )

    keyboard = []
    if files:
        primary = next((f for f in files if f.get("primary")), files[0])
        keyboard.append([
            InlineKeyboardButton(
                "📥 تحميل الملف الرئيسي",
                callback_data=f"file_{project_id}_{version_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton("⬅️ الإصدارات", callback_data=f"page_versions_{project_id}")
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def mod_gallery_page(query, context, project_id):
    try:
        project = await modrinth_get(f"/project/{project_id}")
    except Exception:
        await query.answer("❌ فشل تحميل الصور", show_alert=True)
        return

    gallery = project.get("gallery", [])
    if not gallery:
        await query.edit_message_text(
            "🖼️ هذا المود ما عندوش صور.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ رجوع",
                    callback_data=f"page_info_{project_id}",
                )
            ]]),
        )
        return

    await query.edit_message_text("🖼️ جاري إرسال صور المود...")

    for image in gallery[:8]:
        image_url = image.get("url")
        if image_url:
            try:
                await query.message.reply_photo(
                    photo=image_url,
                    caption=f"🖼️ {image.get('title', 'Minecraft Mod')}",
                )
            except Exception:
                pass

    await query.message.reply_text(
        "🖼️ انتهت الصور.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "⬅️ معلومات المود",
                callback_data=f"page_info_{project_id}",
            )
        ]]),
    )


async def mod_links_page(query, context, project_id):
    try:
        project = await modrinth_get(f"/project/{project_id}")
    except Exception:
        await query.answer("❌ فشل تحميل الروابط", show_alert=True)
        return

    links = project.get("links", {})
    keyboard = []

    for key, label in [
        ("source_url", "💻 Source Code"),
        ("issues_url", "🐛 Issues"),
        ("wiki_url", "📚 Wiki"),
        ("discord_url", "💬 Discord"),
    ]:
        if links.get(key):
            keyboard.append([InlineKeyboardButton(label, url=links[key])])

    keyboard.append([
        InlineKeyboardButton(
            "🌐 Modrinth",
            url=f"https://modrinth.com/project/{project_id}",
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ معلومات المود",
            callback_data=f"page_info_{project_id}",
        )
    ])

    await query.edit_message_text(
        "🔗 روابط المود:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def download_menu(query, context, project_id):
    try:
        versions = await modrinth_get(f"/project/{project_id}/version")
    except Exception:
        await query.answer("❌ فشل تحميل الإصدارات", show_alert=True)
        return

    minecraft_versions = []
    for version in versions:
        for mc in version.get("game_versions", []):
            if mc not in minecraft_versions:
                minecraft_versions.append(mc)

    keyboard = [
        [
            InlineKeyboardButton(
                f"🎯 Minecraft {mc}",
                callback_data=f"mc_{project_id}_{mc}",
            )
        ]
        for mc in minecraft_versions[:20]
    ]
    keyboard.append([
        InlineKeyboardButton("⬅️ معلومات", callback_data=f"page_info_{project_id}")
    ])

    await query.edit_message_text(
        "📥 تحميل المود\n\nاختار إصدار Minecraft:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def loader_select(query, context, project_id, minecraft_version):
    try:
        versions = await modrinth_get(f"/project/{project_id}/version")
    except Exception:
        await query.answer("❌ خطأ", show_alert=True)
        return

    loaders = set()
    for version in versions:
        if minecraft_version in version.get("game_versions", []):
            loaders.update(version.get("loaders", []))

    keyboard = [
        [
            InlineKeyboardButton(
                f"⚙️ {loader.upper()}",
                callback_data=f"selectver_{project_id}_{minecraft_version}_{loader}",
            )
        ]
        for loader in sorted(loaders)
    ]
    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Minecraft",
            callback_data=f"download_menu_{project_id}",
        )
    ])

    await query.edit_message_text(
        f"📥 اختيار Loader\n\nMinecraft: {minecraft_version}\n\nاختار Loader:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def final_version_select(query, context, project_id, minecraft_version, loader):
    try:
        versions = await modrinth_get(f"/project/{project_id}/version")
    except Exception:
        await query.answer("❌ خطأ", show_alert=True)
        return

    matches = [
        v for v in versions
        if minecraft_version in v.get("game_versions", [])
        and loader in v.get("loaders", [])
    ]

    keyboard = []
    for version in matches[:15]:
        version_id = version.get("id")
        name = version.get("name", version.get("version_number", "Unknown"))
        version_type = version.get("version_type", "release")
        keyboard.append([
            InlineKeyboardButton(
                f"📦 {name[:35]} • {version_type}",
                callback_data=f"version_{project_id}_{version_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Loader",
            callback_data=f"mc_{project_id}_{minecraft_version}",
        )
    ])

    await query.edit_message_text(
        f"📦 الإصدارات المتاحة\n\n"
        f"Minecraft: {minecraft_version}\n"
        f"Loader: {loader.upper()}\n\n"
        "اختار النسخة:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def download_version_file(query, context, project_id, version_id):
    await query.answer("📥 كنوجد الملف...")

    try:
        version = await modrinth_get(f"/version/{version_id}")
    except Exception:
        await query.message.reply_text("❌ فشل جلب ملف التحميل.")
        return

    files = version.get("files", [])
    if not files:
        await query.message.reply_text("❌ ما كاين حتى ملف للتحميل.")
        return

    selected = next((f for f in files if f.get("primary")), files[0])
    file_url = selected.get("url")
    file_name = selected.get("filename", "minecraft-file.jar")

    if not file_url:
        await query.message.reply_text("❌ رابط التحميل غير متوفر.")
        return

    message = await query.message.reply_text(
        f"📥 جاري تحميل: {file_name}"
    )

    temp_path = None
    try:
        suffix = Path(file_name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp_path = temp.name

        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            async with client.stream("GET", file_url) as response:
                response.raise_for_status()
                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        size = os.path.getsize(temp_path)

        if size <= 49 * 1024 * 1024:
            with open(temp_path, "rb") as document:
                await query.message.reply_document(
                    document=InputFile(document, filename=file_name),
                    caption=f"📦 {file_name}",
                )
            await message.delete()
        else:
            await message.edit_text(
                "📦 الملف كبير للإرسال المباشر.\n\n"
                f"🔗 رابط التحميل:\n{file_url}"
            )

    except Exception:
        logger.exception("Download error")
        try:
            await message.edit_text(
                "❌ وقع خطأ أثناء التحميل.\n\n"
                f"🔗 رابط التحميل:\n{file_url}"
            )
        except Exception:
            pass
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


async def follow_mod(query, context, project_id):
    user_id = query.from_user.id
    follows = get_follows(user_id)

    if project_id in follows:
        remove_follow(user_id, project_id)
        await query.answer("🔕 تم إلغاء المتابعة")
    else:
        add_follow(user_id, project_id)
        try:
            versions = await modrinth_get(f"/project/{project_id}/version")
            if versions:
                save_version(project_id, versions[0]["id"])
        except Exception:
            logger.exception("Initial version error")
        await query.answer("🔔 تمت المتابعة")

    await show_mod(query, context, project_id)


async def my_follows(update, context):
    user_id = update.effective_user.id
    follows = get_follows(user_id)

    if not follows:
        await update.message.reply_text(
            "🔔 ما عندك حتى مود متابع حالياً."
        )
        return

    keyboard = []
    for project_id in follows:
        try:
            project = await modrinth_get(f"/project/{project_id}")
            name = project.get("title", project_id)
        except Exception:
            name = project_id

        keyboard.append([
            InlineKeyboardButton(
                f"🎮 {name[:50]}",
                callback_data=f"mod_{project_id}",
            )
        ])

    await update.message.reply_text(
        "🔔 المودات اللي كتتابعها:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def pack_command(update, context):
    if not context.args:
        await update.message.reply_text(
            "📦 استعمل:\n/pack Better Minecraft"
        )
        return

    search_text = " ".join(context.args)
    await update.message.reply_text("📦 كنقلب على Modpacks...")

    params = {
        "query": search_text,
        "limit": 10,
        "facets": json.dumps([["project_type:modpack"]]),
    }

    try:
        result = await modrinth_get("/search", params=params)
        packs = result.get("hits", [])
    except Exception:
        await update.message.reply_text("❌ وقع مشكل أثناء البحث.")
        return

    if not packs:
        await update.message.reply_text("😢 ما لقيتش Modpack.")
        return

    keyboard = []
    for pack in packs:
        project_id = pack.get("project_id")
        title = pack.get("title", "Unknown")
        keyboard.append([
            InlineKeyboardButton(
                f"📦 {title[:55]}",
                callback_data=f"mod_{project_id}",
            )
        ])

    await update.message.reply_text(
        f"📦 لقيت {len(packs)} Modpacks:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def server_command(update, context):
    if len(context.args) != 2 or context.args[1].lower() not in {"vanilla", "fabric", "paper"}:
        await update.message.reply_text("🖥️ الاستعمال:\n/genserver 1.21.1 fabric\n\nالأنواع: vanilla أو fabric أو paper")
        return
    version, server_type = context.args[0], context.args[1].lower()
    wait = await update.message.reply_text("⏳ كنجيب ملف السيرفر...")
    try:
        info = await server_download_info(version, server_type)
        if not info:
            await wait.edit_text("❌ ما لقيتش هاد الإصدار أو النوع.")
            return
        url, filename = info
        folder = SERVER_ROOT / f"{server_type}-{version}"
        jar = folder / filename
        await download_server_jar(url, jar)
        (folder / "eula.txt").write_text("eula=false\n", encoding="utf-8")
        (folder / "server.properties").write_text(
            "motd=Minecraft Server\nserver-port=25565\nonline-mode=true\nmax-players=20\n",
            encoding="utf-8",
        )
        await wait.edit_text(
            f"✅ تجهز السيرفر!\n\n📦 النوع: {server_type}\n🎮 الإصدار: {version}\n📁 المجلد: {folder}\n\n"
            "قبل التشغيل بدّل eula=false إلى eula=true، ثم استعمل:\n"
            f"/serverstart {server_type}-{version}"
        )
    except Exception:
        logger.exception("Server generation error")
        await wait.edit_text("❌ وقع مشكل أثناء تجهيز السيرفر.")


async def server_panel(query):
    await query.edit_message_text(
        "🖥️ لوحة السيرفرات\n\nاختار العملية:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة سيرفر", callback_data="server_add")],
            [InlineKeyboardButton("📋 سيرفراتي", callback_data="server_list")],
            [InlineKeyboardButton("▶️ تشغيل سيرفر محلي", callback_data="server_help_start")],
            [InlineKeyboardButton("⏹️ إيقاف سيرفر محلي", callback_data="server_help_stop")],
            [InlineKeyboardButton("🔗 معلومات الدخول", callback_data="server_list_info")],
            [InlineKeyboardButton("⬅️ الرئيسية", callback_data="back_start")],
        ]),
    )


async def server_add_command(update, context):
    if len(context.args) < 3:
        await update.message.reply_text("➕ الاستعمال:\n/serveradd الاسم host port [النوع] [username]\n\nمثال:\n/serveradd survival play.example.com 25565 external Steve")
        return
    name, host = context.args[0], context.args[1]
    try:
        port = int(context.args[2])
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ الـPort خاصو يكون رقم بين 1 و65535.")
        return
    server_type = context.args[3].lower() if len(context.args) > 3 else "external"
    username = context.args[4] if len(context.args) > 4 else None
    save_server(update.effective_user.id, name, host, port, username, None, server_type)
    await update.message.reply_text(f"✅ تسجل السيرفر: {name}\n🔗 {host}:{port}")


async def server_list(query, info_only=False):
    rows = get_servers(query.from_user.id)
    if not rows:
        text = "📋 ما عندك حتى سيرفر محفوظ.\n\nاستعمل ➕ إضافة سيرفر."
    else:
        text = "🔗 معلومات السيرفرات:\n\n" if info_only else "📋 سيرفراتك:\n\n"
        text += "\n".join(f"🎮 {r[0]} — {r[1]}:{r[2]} ({r[5]})" for r in rows)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ لوحة السيرفرات", callback_data="menu_server")
    ]]))


async def language_menu(query):
    keyboard = [[InlineKeyboardButton(label, callback_data=f"lang_{code}")] for code, (label, _) in LANGUAGES.items()]
    keyboard.append([InlineKeyboardButton("⬅️ الرئيسية", callback_data="back_start")])
    await query.edit_message_text("🌍 اختار اللغة / Choose language:", reply_markup=InlineKeyboardMarkup(keyboard))


def music_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ تشغيل", callback_data="music_resume"), InlineKeyboardButton("⏸️ إيقاف مؤقت", callback_data="music_pause")],
        [InlineKeyboardButton("⏹️ إيقاف", callback_data="music_stop"), InlineKeyboardButton("🚪 مغادرة", callback_data="music_leave")],
        [InlineKeyboardButton("📋 القائمة", callback_data="music_queue")],
    ])


async def music_play(update, context):
    global voice_player
    if not context.args:
        await update.message.reply_text("🎵 الاستعمال:\n/musicplay رابط_الصوت_أو_مسار_الملف")
        return
    voice_player = voice_player or get_voice_player()
    source = " ".join(context.args)
    try:
        await voice_player.start()
        for attempt in range(3):
            try:
                await voice_player.play(update.effective_chat.id, source)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(5)
        await update.message.reply_text("🎵 تشغل الصوت داخل Voice Chat.", reply_markup=music_keyboard())
    except Exception:
        logger.exception("Voice playback error")
        await update.message.reply_text("❌ ما قدرتش ندخل للمكالمة. تأكد أنها مفتوحة والحساب المساعد داخل المجموعة.")


async def music_control(update, context, action):
    global voice_player
    if not voice_player:
        await update.callback_query.answer("ما كاين حتى تشغيل", show_alert=True)
        return
    try:
        if action == "pause": await voice_player.pause(update.effective_chat.id)
        elif action == "resume": await voice_player.resume(update.effective_chat.id)
        else: await voice_player.leave(update.effective_chat.id)
        await update.callback_query.answer("✅ تم")
    except Exception:
        await update.callback_query.answer("❌ تعذر تنفيذ العملية", show_alert=True)


async def server_start(update, context):
    if len(context.args) != 1:
        await update.message.reply_text("الاستعمال: /serverstart vanilla-1.21.1")
        return
    folder = SERVER_ROOT / context.args[0]
    jars = list(folder.glob("*.jar")) if folder.is_dir() else []
    eula = folder / "eula.txt"
    if not jars or not eula.exists():
        await update.message.reply_text("❌ السيرفر غير موجود. استعمل /genserver أولاً.")
        return
    if eula.read_text(encoding="utf-8").strip().lower() != "eula=true":
        await update.message.reply_text("❌ خاصك توافق على EULA داخل eula.txt قبل التشغيل.")
        return
    proc = await asyncio.create_subprocess_exec(
        "java", "-Xms512M", "-Xmx2G", "-jar", jars[0].name, "nogui",
        cwd=str(folder), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    context.bot_data.setdefault("server_processes", {})[context.args[0]] = proc
    await update.message.reply_text(f"✅ السيرفر بدا التشغيل: {context.args[0]}")


async def server_stop(update, context):
    if len(context.args) != 1:
        await update.message.reply_text("الاستعمال: /serverstop vanilla-1.21.1")
        return
    proc = context.bot_data.get("server_processes", {}).get(context.args[0])
    if not proc or proc.returncode is not None:
        await update.message.reply_text("ℹ️ ما كاين حتى سيرفر خدام بهاد الاسم.")
        return
    proc.terminate()
    await update.message.reply_text("🛑 طلبت إيقاف السيرفر.")


async def afk_start(update, context):
    if len(context.args) not in {3, 4}:
        await update.message.reply_text("الاستعمال: /afkstart host port username [version]")
        return
    user_id = update.effective_user.id
    if user_id in afk_processes and afk_processes[user_id].returncode is None:
        await update.message.reply_text("ℹ️ عندك AFK خدام دابا.")
        return
    host, port, username = context.args[:3]
    version = context.args[3] if len(context.args) == 4 else "1.21.1"
    script = AFK_ROOT / "afk_client.js"
    if not script.exists():
        await update.message.reply_text("❌ ملف AFK Client غير موجود.")
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", str(script), host, port, username, version,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        afk_processes[user_id] = proc
        await update.message.reply_text(f"✅ AFK Client بدا لـ {username}@{host}:{port}")
    except FileNotFoundError:
        await update.message.reply_text("❌ خاص Node.js يكون مثبت فالسيرفر.")


async def afk_stop(update, context):
    proc = afk_processes.get(update.effective_user.id)
    if proc and proc.returncode is None:
        proc.terminate()
        await update.message.reply_text("🛑 توقف AFK Client.")
    else:
        await update.message.reply_text("ℹ️ ما كاينش AFK Client خدام.")


async def afk_status(update, context):
    proc = afk_processes.get(update.effective_user.id)
    running = bool(proc and proc.returncode is None)
    await update.message.reply_text("🟢 AFK خدام." if running else "⚪ AFK ما خدامش.")


async def ask_ai(update, context):
    user_text = update.message.text.strip()
    if not user_text:
        return

    wait = await update.message.reply_text("🤖 كنفكر...")

    prompt = f"""
أنت خبير محترف في Minecraft.
جاوب المستخدم بالدارجة المغربية إذا كان يتحدث بالدارجة،
وإلا جاوبه بالعربية.
جاوب بوضوح وباختصار، ولا تخترع معلومات.

السؤال:
{user_text}
"""

    try:
        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        answer = response.text or "❌ ما قدرتش نولد جواب."
        await wait.edit_text(answer)
    except Exception:
        logger.exception("Gemini error")
        await wait.edit_text(
            "❌ وقع مشكل مع Gemini AI. تأكد من GEMINI_KEY."
        )


async def check_updates(context: ContextTypes.DEFAULT_TYPE):
    followed = get_all_follows()
    if not followed:
        return

    for user_id, project_id in followed:
        try:
            versions = await modrinth_get(f"/project/{project_id}/version")
            if not versions:
                continue

            latest = versions[0]
            latest_id = latest.get("id")
            old_id = get_saved_version(project_id)

            if old_id is None:
                save_version(project_id, latest_id)
                continue

            if old_id != latest_id:
                project = await modrinth_get(f"/project/{project_id}")
                name = project.get("title", project_id)
                version_name = latest.get(
                    "name",
                    latest.get("version_number", latest_id),
                )
                url = f"https://modrinth.com/project/{project_id}"

                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🔔 تحديث جديد للمود!\n\n"
                        f"🎮 {name}\n"
                        f"📦 الإصدار: {version_name}\n"
                        f"🔗 {url}"
                    ),
                )
                save_version(project_id, latest_id)

        except Exception:
            logger.exception(f"Update check failed: {project_id}")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_java":
        await choose_filters(query, context, "Java")
        return

    if data == "menu_bedrock":
        await choose_filters(query, context, "Bedrock")
        return

    if data == "menu_pack":
        await query.edit_message_text(
            "📦 استعمل:\n/pack اسم_الباك"
        )
        return

    if data == "menu_server":
        await server_panel(query)
        return

    if data == "menu_music":
        await query.edit_message_text("🎵 مشغل Voice Chat\n\nشغّل رابطاً صوتياً باستعمال:\n/musicplay رابط_الصوت\n\nتأكد أن المكالمة الصوتية مفتوحة والحساب المساعد داخل المجموعة.", reply_markup=music_keyboard())
        return
    if data.startswith("music_"):
        action = data[6:]
        if action == "queue":
            await query.answer("القائمة الحالية تُبنى مع التشغيل المتتابع", show_alert=True)
        else:
            await music_control(update, context, action)
        return

    if data == "server_add":
        await query.edit_message_text("➕ إضافة سيرفر\n\nاستعمل:\n/serveradd الاسم host port [النوع] [username]")
        return
    if data == "server_list":
        await server_list(query)
        return
    if data == "server_list_info":
        await server_list(query, info_only=True)
        return
    if data == "server_help_start":
        await query.edit_message_text("▶️ التشغيل:\n/serverstart اسم-المجلد\n\nالسيرفر المحلي خاصو يكون تجهز بـ /genserver.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ لوحة السيرفرات", callback_data="menu_server")]]))
        return
    if data == "server_help_stop":
        await query.edit_message_text("⏹️ الإيقاف:\n/serverstop اسم-المجلد", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ لوحة السيرفرات", callback_data="menu_server")]]))
        return
    if data == "menu_language":
        await language_menu(query)
        return
    if data.startswith("lang_"):
        code = data[5:]
        if code in LANGUAGES:
            set_language(query.from_user.id, code)
            await query.edit_message_text(f"✅ تم اختيار اللغة: {LANGUAGES[code][0]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ الرئيسية", callback_data="back_start")]]))
        return
    if data == "menu_settings":
        await query.edit_message_text("⚙️ الإعدادات\n\n🌍 اللغة: استعمل زر اللغة من الرئيسية.\n🔐 التوكنات السرية لا تُحفظ داخل البوت.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ الرئيسية", callback_data="back_start")]]))
        return

    if data == "menu_ai":
        context.user_data.clear()
        await query.edit_message_text(
            "🤖 Minecraft AI\n\n"
            "سولني أي سؤال على Minecraft."
        )
        return

    if data == "menu_updates":
        await query.edit_message_text(
            "📢 التحديثات\n\n"
            "دخل لأي مود واضغط متابعة.\n"
            "وشوف المودات ديالك باستعمال /myfollows"
        )
        return

    if data.startswith("cat_"):
        parts = data.split("_", 2)
        await ask_search(query, context, parts[1], parts[2])
        return

    if data.startswith("mod_"):
        await show_mod(query, context, data[4:])
        return

    if data.startswith("page_info_"):
        await show_mod(query, context, data[len("page_info_"):])
        return

    if data.startswith("page_versions_"):
        await mod_versions_page(query, context, data[len("page_versions_"):])
        return

    if data.startswith("page_gallery_"):
        await mod_gallery_page(query, context, data[len("page_gallery_"):])
        return

    if data.startswith("page_links_"):
        await mod_links_page(query, context, data[len("page_links_"):])
        return

    if data.startswith("download_menu_"):
        await download_menu(query, context, data[len("download_menu_"):])
        return

    if data.startswith("mc_"):
        parts = data.split("_", 2)
        await loader_select(query, context, parts[1], parts[2])
        return

    if data.startswith("selectver_"):
        parts = data.split("_", 3)
        await final_version_select(
            query, context, parts[1], parts[2], parts[3]
        )
        return

    if data.startswith("version_"):
        parts = data.split("_", 2)
        await version_details_page(
            query, context, parts[1], parts[2]
        )
        return

    if data.startswith("file_"):
        parts = data.split("_", 2)
        await download_version_file(
            query, context, parts[1], parts[2]
        )
        return

    if data.startswith("follow_"):
        await follow_mod(query, context, data[len("follow_"):])
        return

    if data == "back_start":
        context.user_data.clear()
        await query.edit_message_text(
            "⚡ Minecraft Search Bot v4.1 ⚡\n\nاختار الخدمة:",
            reply_markup=main_keyboard(),
        )


async def get_text(update, context):
    if context.user_data.get("search_type"):
        await search_mods(update, context)
        return
    await ask_ai(update, context)


async def error_handler(update, context):
    logger.exception("Unhandled exception", exc_info=context.error)


def main():
    init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pack", pack_command))
    app.add_handler(CommandHandler("genserver", server_command))
    app.add_handler(CommandHandler("serveradd", server_add_command))
    app.add_handler(CommandHandler("serverstart", server_start))
    app.add_handler(CommandHandler("serverstop", server_stop))
    app.add_handler(CommandHandler("afkstart", afk_start))
    app.add_handler(CommandHandler("afkstop", afk_stop))
    app.add_handler(CommandHandler("afkstatus", afk_status))
    app.add_handler(CommandHandler("musicplay", music_play))
    app.add_handler(CommandHandler("myfollows", my_follows))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, get_text))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(
            check_updates,
            interval=1800,
            first=60,
        )

    print("Minecraft Search Bot v4.1 is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
