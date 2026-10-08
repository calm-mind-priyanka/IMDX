from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors.exceptions.bad_request_400 import MessageTooLong
from info import ADMINS, LOG_CHANNEL, USERNAME, MULTIPLE_DB, DATABASE_URI2
from database.users_chats_db import db, mydb as user_db
from database.ia_filterdb import Media, Media2, mydb as movie_db1, mydb2 as movie_db2
from utils import get_size, temp
from Script import script
import psutil
import time
import asyncio

_PROCESS_START = time.monotonic()


async def _db_stats(database):
    try:
        stats = await database.command("dbstats")
        return stats.get("dataSize", 0), stats.get("indexSize", 0)
    except Exception:
        return 0, 0


def _storage_text(pair):
    return get_size(sum(pair))



@Client.on_message(filters.new_chat_members & filters.group)
async def save_group(bot, message):
    check = [u.id for u in message.new_chat_members]
    if temp.ME in check:
        if not await db.get_chat(message.chat.id):
            total = await bot.get_chat_members_count(message.chat.id)
            user = message.from_user.mention if message.from_user else "Dear"
            group_link = await message.chat.export_invite_link()
            await bot.send_message(
                LOG_CHANNEL,
                script.NEW_GROUP_TXT.format(
                    temp.B_LINK,
                    message.chat.title,
                    message.chat.id,
                    message.chat.username,
                    group_link,
                    total,
                    user,
                ),
                disable_web_page_preview=True,
            )
            await db.add_chat(message.chat.id, message.chat.title)
            btn = [[InlineKeyboardButton("⚡️ sᴜᴘᴘᴏʀᴛ ⚡️", url=USERNAME)]]
            reply_markup = InlineKeyboardMarkup(btn)
            await bot.send_message(
                chat_id=message.chat.id,
                text=f"<b>☤ ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ᴀᴅᴅɪɴɢ ᴍᴇ ɪɴ {message.chat.title}\n\n🤖 ᴅᴏɴ’ᴛ ꜰᴏʀɢᴇᴛ ᴛᴏ ᴍᴀᴋᴇ ᴍᴇ ᴀᴅᴍɪɴ 🤖\n\n㊝ ɪꜰ ʏᴏᴜ ʜᴀᴠᴇ ᴀɴʏ ᴅᴏᴜʙᴛ ʏᴏᴜ ᴄʟᴇᴀʀ ɪᴛ ᴜsɪɴɢ ʙᴇʟᴏᴡ ʙᴜᴛᴛᴏɴs ㊜</b>",
                reply_markup=reply_markup,
            )


@Client.on_message(filters.command("leave") & filters.user(ADMINS))
async def leave_a_chat(bot, message):
    r = message.text.split(None)
    if len(message.command) == 1:
        return await message.reply(
            "<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ʟɪᴋᴇ ᴛʜɪꜱ `/leave -100******`</b>"
        )
    if len(r) > 2:
        reason = message.text.split(None, 2)[2]
        chat = message.text.split(None, 2)[1]
    else:
        chat = message.command[1]
        reason = "ɴᴏ ʀᴇᴀꜱᴏɴ ᴘʀᴏᴠɪᴅᴇᴅ..."
    try:
        chat = int(chat)
    except:
        chat = chat
    try:
        btn = [[InlineKeyboardButton("⚡️ ᴏᴡɴᴇʀ ⚡️", url=USERNAME)]]
        reply_markup = InlineKeyboardMarkup(btn)
        await bot.send_message(
            chat_id=chat,
            text=f"😞 ʜᴇʟʟᴏ ᴅᴇᴀʀ,\nᴍʏ ᴏᴡɴᴇʀ ʜᴀꜱ ᴛᴏʟᴅ ᴍᴇ ᴛᴏ ʟᴇᴀᴠᴇ ꜰʀᴏᴍ ɢʀᴏᴜᴘ ꜱᴏ ɪ ɢᴏ 😔\n\n🚫 ʀᴇᴀꜱᴏɴ ɪꜱ - <code>{reason}</code>\n\nɪꜰ ʏᴏᴜ ɴᴇᴇᴅ ᴛᴏ ᴀᴅᴅ ᴍᴇ ᴀɢᴀɪɴ ᴛʜᴇɴ ᴄᴏɴᴛᴀᴄᴛ ᴍʏ ᴏᴡɴᴇʀ 👇",
            reply_markup=reply_markup,
        )
        await bot.leave_chat(chat)
        await db.delete_chat(chat)
        await message.reply(f"<b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ʟᴇꜰᴛ ꜰʀᴏᴍ ɢʀᴏᴜᴘ - `{chat}`</b>")
    except Exception as e:
        await message.reply(f"<b>🚫 ᴇʀʀᴏʀ - `{e}`</b>")


@Client.on_message(filters.command("groups") & filters.user(ADMINS))
async def groups_list(bot, message):
    msg = await message.reply("<b>Searching...</b>")
    chats = await db.get_all_chats()
    out = "Groups saved in the database:\n\n"
    count = 1
    async for chat in chats:
        chat_info = await bot.get_chat(chat["id"])
        members_count = (
            chat_info.members_count if chat_info.members_count else "Unknown"
        )
        out += f"<b>{count}. Title - `{chat['title']}`\nID - `{chat['id']}`\nMembers - `{members_count}`</b>"
        out += "\n\n"
        count += 1
    try:
        if count > 1:
            await msg.edit_text(out)
        else:
            await msg.edit_text("<b>No groups found</b>")
    except MessageTooLong:
        with open("chats.txt", "w+") as outfile:
            outfile.write(out)
        await message.reply_document("chats.txt", caption="<b>List of all groups</b>")


@Client.on_message(filters.command("stats") & filters.user(ADMINS) & filters.incoming)
async def get_ststs(bot, message):
    users, groups, d3, d1, d2, files1, files2 = await asyncio.gather(
        db.total_users_count(),
        db.total_chat_count(),
        _db_stats(user_db),
        _db_stats(movie_db1),
        _db_stats(movie_db2) if (MULTIPLE_DB and DATABASE_URI2) else asyncio.sleep(0, result=(0, 0)),
        Media.count_documents(),
        Media2.count_documents() if (MULTIPLE_DB and DATABASE_URI2) else asyncio.sleep(0, result=0),
    )
    total_files = files1 + files2
    uptime_seconds = int(time.monotonic() - _PROCESS_START)
    uptime = time.strftime("%dd %Hh %Mm %Ss", time.gmtime(uptime_seconds))
    ram = psutil.virtual_memory().percent
    cpu = psutil.cpu_percent(interval=None)
    d1_used = _storage_text(d1)
    d2_used = _storage_text(d2)
    d3_used = _storage_text(d3)
    total_movie_storage = get_size(sum(d1) + sum(d2))
    total_storage = get_size(sum(d1) + sum(d2) + sum(d3))
    await message.reply_text(
        f"<b><u>♻️ ʙᴏᴛ ᴅᴀᴛᴀʙᴀsᴇ</u>\n\n"
        f"» ᴜsᴇʀs - <code>{users}</code>\n"
        f"» ɢʀᴏᴜᴘs - <code>{groups}</code>\n\n"
        f"<u>🎬 D1 — ᴍᴏᴠɪᴇ ᴅᴀᴛᴀʙᴀsᴇ (ᴘʀɪᴍᴀʀʏ)</u>\n"
        f"» ғɪʟᴇs - <code>{files1}</code>\n"
        f"» sᴛᴏʀᴀɢᴇ (ᴅᴀᴛᴀ + ɪɴᴅᴇx) - <code>{d1_used}</code>\n\n"
        f"<u>🎬 D2 — ᴍᴏᴠɪᴇ ᴅᴀᴛᴀʙᴀsᴇ (sᴇᴄᴏɴᴅᴀʀʏ)</u>\n"
        f"» ғɪʟᴇs - <code>{files2}</code>\n"
        f"» sᴛᴏʀᴀɢᴇ (ᴅᴀᴛᴀ + ɪɴᴅᴇx) - <code>{d2_used}</code>\n\n"
        f"<u>🤖 D3 — ᴜsᴇʀ / ʙᴏᴛ ᴅᴀᴛᴀʙᴀsᴇ</u>\n"
        f"» ᴜsᴇʀ + ɢʀᴏᴜᴘ + ʙᴏᴛ ᴅᴀᴛᴀ sᴛᴏʀᴀɢᴇ - <code>{d3_used}</code>\n\n"
        f"» ᴛᴏᴛᴀʟ ᴍᴏᴠɪᴇ ғɪʟᴇs - <code>{total_files}</code>\n"
        f"» ᴛᴏᴛᴀʟ ᴍᴏᴠɪᴇ sᴛᴏʀᴀɢᴇ - <code>{total_movie_storage}</code>\n"
        f"» ᴛᴏᴛᴀʟ ᴅʙ sᴛᴏʀᴀɢᴇ - <code>{total_storage}</code>\n\n"
        f"<u>🛠️ ʙᴏᴛ ᴅᴇᴛᴀɪʟs</u>\n"
        f"» ᴜᴘᴛɪᴍᴇ - <code>{uptime}</code>\n"
        f"» ʀᴀᴍ - <code>{ram}%</code>\n"
        f"» ᴄᴘᴜ - <code>{cpu}%</code></b>"
    )


@Client.on_message(filters.command("invite") & filters.private & filters.user(ADMINS))
async def invite(client, message):
    toGenInvLink = message.command[1]
    if len(toGenInvLink) != 14:
        return await message.reply(
            "Invalid chat id\nAdd -100 before chat id if You did not add any yet."
        )
    try:
        link = await client.export_chat_invite_link(toGenInvLink)
        await message.reply(link)
    except Exception as e:
        print(f"Error while generating invite link : {e}\nFor chat:{toGenInvLink}")
        await message.reply(
            f"Error while generating invite link : {e}\nFor chat:{toGenInvLink}"
        )
