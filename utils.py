import logging
from pyrogram.errors import (
    InputUserDeactivated,
    UserNotParticipant,
    FloodWait,
    UserIsBlocked,
    PeerIdInvalid,
)
from info import AUTH_CHANNEL, LONG_IMDB_DESCRIPTION, START_IMG, PREMIUM_PLANS, TMDB_API_KEY
from imdb import Cinemagoer
import asyncio
import aiohttp
from pyrogram.types import Message, InlineKeyboardButton
from pyrogram import enums
import pytz
import re
import os
from shortzy import Shortzy
from datetime import datetime
from typing import Any
from database.users_chats_db import db
from language import small_caps


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BANNED = {}
imdb = Cinemagoer()
_SETTINGS_CACHE = {}

# TMDB is an optional primary poster source. IMDb/Cinemagoer remains the
# fallback, so existing IMDX behaviour is preserved when no TMDB key is set.
_TMDB_BASE_URL = "https://api.themoviedb.org/3"
_TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w1280"
_TMDB_CACHE = {}
_TMDB_CACHE_TTL = 30 * 60
_TMDB_TIMEOUT = aiohttp.ClientTimeout(total=4.0, connect=1.5, sock_read=3.0)
_TMDB_SESSION = None


async def _tmdb_session():
    global _TMDB_SESSION
    if _TMDB_SESSION is None or _TMDB_SESSION.closed:
        _TMDB_SESSION = aiohttp.ClientSession(timeout=_TMDB_TIMEOUT)
    return _TMDB_SESSION


def _tmdb_cache_get(key):
    item = _TMDB_CACHE.get(key)
    if not item:
        return None
    if asyncio.get_running_loop().time() - item[0] >= _TMDB_CACHE_TTL:
        _TMDB_CACHE.pop(key, None)
        return None
    return item[1]


def _tmdb_cache_put(key, value):
    _TMDB_CACHE[key] = (asyncio.get_running_loop().time(), value)


def _empty_poster_details():
    return {
        "title": None, "votes": None, "aka": "", "seasons": None,
        "box_office": None, "localized_title": None, "kind": None,
        "imdb_id": None, "cast": "", "runtime": "", "countries": "",
        "certificates": "", "languages": "", "director": "",
        "writer": "", "producer": "", "composer": "",
        "cinematographer": "", "music_team": "", "distributors": "",
        "release_date": None, "year": None, "genres": "",
        "poster": None, "plot": "", "rating": "", "url": "",
    }


async def _tmdb_get_poster(query, year=None, imdb_id=None):
    """TMDB poster lookup with full movie/TV metadata enrichment.

    The search endpoint only contains basic fields.  After selecting the best
    result, fetch its details + credits + ratings/external IDs so the existing
    poster template variables (genres, languages, director, cast, etc.) are
    actually populated instead of rendering blank values.
    """
    if not TMDB_API_KEY:
        return None

    cache_key = f"id:{imdb_id}" if imdb_id else f"q:{str(query).strip().lower()}:{year or ''}"
    cached = _tmdb_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        session = await _tmdb_session()
        headers = {"Accept": "application/json"}
        if imdb_id:
            endpoint = f"{_TMDB_BASE_URL}/find/{imdb_id}"
            params = {"api_key": TMDB_API_KEY, "external_source": "imdb_id"}
            async with session.get(endpoint, params=params, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            results = (data.get("movie_results") or []) + (data.get("tv_results") or [])
        else:
            endpoint = f"{_TMDB_BASE_URL}/search/multi"
            params = {"api_key": TMDB_API_KEY, "query": str(query).strip(), "include_adult": "false"}
            if year:
                params["year"] = int(year)
            async with session.get(endpoint, params=params, headers=headers, ssl=False) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            results = [r for r in data.get("results", []) if r.get("media_type") in ("movie", "tv")]

        if not results:
            return None

        def score(r):
            score = 0
            if r.get("poster_path"):
                score += 100
            title = (r.get("title") or r.get("name") or "").lower()
            q = str(query or "").lower().strip()
            if q and title == q:
                score += 80
            if year:
                date = r.get("release_date") or r.get("first_air_date") or ""
                if date.startswith(str(year)):
                    score += 50
            score += min(float(r.get("popularity") or 0), 20)
            return score

        result = max(results, key=score)
        media_type = result.get("media_type") or ("tv" if result.get("name") else "movie")
        tmdb_id = result.get("id")
        poster_path = result.get("poster_path")
        if not poster_path or not tmdb_id:
            return None

        # One details request supplies the fields missing from /search/multi.
        # append_to_response keeps this to one extra TMDB HTTP request.
        detail_endpoint = f"{_TMDB_BASE_URL}/{media_type}/{tmdb_id}"
        detail_params = {
            "api_key": TMDB_API_KEY,
            "append_to_response": "credits,release_dates,content_ratings,external_ids,alternative_titles",
        }
        async with session.get(detail_endpoint, params=detail_params, headers=headers, ssl=False) as resp:
            details_data = await resp.json() if resp.status == 200 else {}

        base = result.copy()
        base.update(details_data or {})
        details = _empty_poster_details()
        title = base.get("title") or base.get("name")
        release = base.get("release_date") or base.get("first_air_date")
        external_ids = base.get("external_ids") or {}
        credits = base.get("credits") or {}
        crew = credits.get("crew") or []
        cast_rows = credits.get("cast") or []

        def names(items, limit=8):
            out = []
            seen = set()
            for item in items:
                name = item.get("name") if isinstance(item, dict) else None
                if name and name not in seen:
                    seen.add(name)
                    out.append(name)
                if len(out) >= limit:
                    break
            return ", ".join(out)

        def crew_names(jobs=(), departments=(), limit=8):
            selected = []
            for person in crew:
                if not isinstance(person, dict):
                    continue
                job = str(person.get("job") or "")
                department = str(person.get("department") or "")
                if (jobs and job in jobs) or (departments and department in departments):
                    selected.append(person)
            return names(selected, limit)

        genres = ", ".join(
            g.get("name", "") for g in (base.get("genres") or []) if isinstance(g, dict) and g.get("name")
        )
        countries = ", ".join(
            c.get("name", "") for c in (base.get("production_countries") or [])
            if isinstance(c, dict) and c.get("name")
        )
        languages = ", ".join(
            l.get("english_name") or l.get("name") or l.get("iso_639_1", "")
            for l in (base.get("spoken_languages") or []) if isinstance(l, dict)
        )

        # Certificate: prefer release certification for movies and content
        # ratings for TV.  Do not invent a certificate when TMDB has none.
        certificates = []
        for country in (base.get("release_dates") or {}).get("results", []):
            for rd in country.get("release_dates", []) or []:
                cert = str(rd.get("certification") or "").strip()
                if cert:
                    certificates.append(f"{country.get('iso_3166_1', '')}: {cert}".strip(": "))
        if not certificates:
            for country in (base.get("content_ratings") or {}).get("results", []):
                cert = str(country.get("rating") or "").strip()
                if cert:
                    certificates.append(f"{country.get('iso_3166_1', '')}: {cert}".strip(": "))
        # Keep the caption compact while retaining multiple country ratings.
        certificates_text = ", ".join(dict.fromkeys(certificates))[:500]

        runtime = base.get("runtime")
        if not runtime:
            episode_runtimes = base.get("episode_run_time") or []
            runtime = episode_runtimes[0] if episode_runtimes else ""
        runtime_text = f"{runtime} min" if runtime else ""

        revenue = base.get("revenue")
        if revenue:
            try:
                box_office = f"${int(revenue):,}"
            except (TypeError, ValueError):
                box_office = str(revenue)
        else:
            box_office = ""

        details.update({
            "title": title,
            "localized_title": title,
            "year": int(release[:4]) if release and release[:4].isdigit() else year,
            "release_date": release,
            "rating": str(base.get("vote_average") or ""),
            "votes": base.get("vote_count"),
            "plot": (base.get("overview") or "")[:800],
            "poster": f"{_TMDB_IMAGE_BASE_URL}{poster_path}",
            "url": (
                f"https://www.imdb.com/title/{external_ids.get('imdb_id')}"
                if external_ids.get("imdb_id")
                else f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
            ),
            "kind": "tv series" if media_type == "tv" else "movie",
            "imdb_id": external_ids.get("imdb_id"),
            "genres": genres,
            "runtime": runtime_text,
            "countries": countries,
            "languages": languages,
            "certificates": certificates_text,
            "director": crew_names({"Director"}),
            "writer": crew_names({"Writer", "Screenplay", "Story", "Teleplay"}),
            "producer": crew_names({"Producer", "Executive Producer"}),
            "composer": crew_names({"Original Music Composer", "Composer", "Music"}),
            "cinematographer": crew_names({"Director of Photography", "Cinematography"}),
            "music_team": crew_names(departments={"Sound"}),
            "cast": names(cast_rows, 10),
            "seasons": base.get("number_of_seasons") or None,
            "box_office": box_office,
        })

        alternative_titles = base.get("titles") or []
        if alternative_titles:
            details["aka"] = names(alternative_titles, 6)

        _tmdb_cache_put(cache_key, details)
        return details
    except Exception as e:
        logger.debug("TMDB poster lookup failed: %s", e)
        return None


def premium_plan_buttons(lang="en"):
    """Build Premium plan buttons from the single PREMIUM_PLANS source.

    Button labels follow the user's existing global language preference.
    """
    rows = []
    items = list(PREMIUM_PLANS.items())
    for i in range(0, len(items), 2):
        row = []
        for key, plan in items[i:i + 2]:
            icon = "💎" if key == "lifetime" else "💳"
            row.append(InlineKeyboardButton(
                f"{icon} {small_caps(plan['name'])} {plan['price']}",
                callback_data=f"buyplan_{key}",
            ))
        rows.append(row)
    custom_labels = {
        "en":"💎 ᴄᴜsᴛᴏᴍ ᴘʟᴀɴ 💎", "hi":"💎 ᴄᴜsᴛᴏᴍ ᴘʟᴀɴ 💎", "ta":"💎 ᴄᴜsᴛᴏᴍ ᴘʟᴀɴ 💎",
        "te":"💎 ᴄᴜsᴛᴏᴍ ᴘʟᴀɴ 💎", "kn":"💎 ᴄᴜsᴛᴏᴍ ᴘʟᴀɴ 💎", "ml":"💎 ᴄᴜsᴛᴏᴍ ᴘʟᴀɴ 💎",
        "bn":"💎 কাস্টম প্ল্যান 💎", "mr":"💎 कस्टम प्लॅन 💎", "gu":"💎 કસ્ટમ પ્લાન 💎",
        "pa":"💎 ਕਸਟਮ ਪਲਾਨ 💎", "ur":"💎 کسٹم پلان 💎", "as":"💎 কাষ্টম প্লেন 💎",
        "ne":"💎 कस्टम प्लान 💎", "hinglish":"💎 Custom Plan 💎",
    }
    rows.append([InlineKeyboardButton(small_caps(custom_labels.get(lang, custom_labels["en"])), callback_data="other")])
    return rows


class temp(object):
    ME = None
    CURRENT = int(os.environ.get("SKIP", 2))
    CANCEL = False
    U_NAME = None
    B_NAME = None
    B_LINK = None
    SETTINGS = {}
    FILES_ID = {}
    USERS_CANCEL = False
    GROUPS_CANCEL = False
    CHAT = {}
    BANNED_USERS = []
    BANNED_CHATS = []


def formate_file_name(file_name):
    file_name = " ".join(
        filter(
            lambda x: not x.startswith("[")
            and not x.startswith("@")
            and not x.startswith("www."),
            file_name.split(),
        )
    )
    return file_name


async def is_req_subscribed(bot, query):
    if await db.find_join_req(query.from_user.id):
        return True
    try:
        user = await bot.get_chat_member(AUTH_CHANNEL, query.from_user.id)
    except UserNotParticipant:
        pass
    except Exception as e:
        print(e)
    else:
        if user.status != enums.ChatMemberStatus.BANNED:
            return True
    return False


async def is_subscribed(bot, user_id, channel_id):
    try:
        user = await bot.get_chat_member(channel_id, user_id)
    except UserNotParticipant:
        pass
    except Exception:
        pass
    else:
        if user.status != enums.ChatMemberStatus.BANNED:
            return True
    return False


async def _get_imdb_poster(query, bulk=False, id=False, file=None):
    if not id:
        query = (query.strip()).lower()
        title = query
        year = re.findall(r"[1-2]\d{3}$", query, re.IGNORECASE)
        if year:
            year = list_to_str(year[:1])
            title = (query.replace(year, "")).strip()
        elif file is not None:
            year = re.findall(r"[1-2]\d{3}", file, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
        else:
            year = None
        movieid = imdb.search_movie(title.lower(), results=10)
        if not movieid:
            return None
        if year:
            filtered = list(filter(lambda k: str(k.get("year")) == str(year), movieid))
            if not filtered:
                filtered = movieid
        else:
            filtered = movieid
        movieid = list(
            filter(lambda k: k.get("kind") in ["movie", "tv series"], filtered)
        )
        if not movieid:
            movieid = filtered
        if bulk:
            return movieid
        movieid = movieid[0].movieID
    else:
        movieid = query
    movie = imdb.get_movie(movieid)
    if movie.get("original air date"):
        date = movie["original air date"]
    elif movie.get("year"):
        date = movie.get("year")
    else:
        date = "N/A"
    plot = ""
    if not LONG_IMDB_DESCRIPTION:
        plot = movie.get("plot")
        if plot and len(plot) > 0:
            plot = plot[0]
    else:
        plot = movie.get("plot outline")
    if plot and len(plot) > 800:
        plot = plot[0:800] + "..."

    return {
        "title": movie.get("title"),
        "votes": movie.get("votes"),
        "aka": list_to_str(movie.get("akas")),
        "seasons": movie.get("number of seasons"),
        "box_office": movie.get("box office"),
        "localized_title": movie.get("localized title"),
        "kind": movie.get("kind"),
        "imdb_id": f"tt{movie.get('imdbID')}",
        "cast": list_to_str(movie.get("cast")),
        "runtime": list_to_str(movie.get("runtimes")),
        "countries": list_to_str(movie.get("countries")),
        "certificates": list_to_str(movie.get("certificates")),
        "languages": list_to_str(movie.get("languages")),
        "director": list_to_str(movie.get("director")),
        "writer": list_to_str(movie.get("writer")),
        "producer": list_to_str(movie.get("producer")),
        "composer": list_to_str(movie.get("composer")),
        "cinematographer": list_to_str(movie.get("cinematographer")),
        "music_team": list_to_str(movie.get("music department")),
        "distributors": list_to_str(movie.get("distributors")),
        "release_date": date,
        "year": movie.get("year"),
        "genres": list_to_str(movie.get("genres")),
        "poster": movie.get("full-size cover url", START_IMG),
        "plot": plot,
        "rating": str(movie.get("rating")),
        "url": f"https://www.imdb.com/title/tt{movieid}",
    }


async def get_poster(query, bulk=False, id=False, file=None):
    """Return poster/details using TMDB first, then the existing IMDb path.

    The returned dictionary keeps the exact IMDX keys expected by pm_filter.py.
    Bulk IMDb candidate searches are intentionally left untouched.
    """
    if not bulk:
        year = None
        if not id:
            text_query = str(query or "").strip()
            match = re.search(r"(?:^|\s)([12]\d{3})$", text_query)
            if match:
                year = int(match.group(1))
                text_query = text_query[:match.start()].strip()
            elif file:
                match = re.search(r"[12]\d{3}", str(file))
                if match:
                    year = int(match.group(0))
        else:
            text_query = str(query or "").strip()
            if not text_query.startswith("tt"):
                text_query = f"tt{text_query}"
            tmdb_details = await _tmdb_get_poster(text_query, imdb_id=text_query)
            if tmdb_details:
                return tmdb_details
            return await _get_imdb_poster(query, bulk=bulk, id=id, file=file)

        tmdb_details = await _tmdb_get_poster(text_query, year=year)
        if tmdb_details:
            # TMDB is the primary configured metadata/poster source. Do not
            # make a successful TMDB lookup wait on Cinemagoer: metadata is
            # optional enrichment and database file results are already safe.
            return tmdb_details

    return await _get_imdb_poster(query, bulk=bulk, id=id, file=file)


async def users_broadcast(user_id, message, is_pin):
    try:
        m = await message.copy(chat_id=user_id)
        if is_pin:
            await m.pin(both_sides=True)
        return True, "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await users_broadcast(user_id, message)
    except InputUserDeactivated:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id}-Removed from Database, since deleted account.")
        return False, "Deleted"
    except UserIsBlocked:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} - Removed from Database, since Blocked the bot.")
        await db.delete_user(user_id)
        return False, "Blocked"
    except PeerIdInvalid:
        await db.delete_user(int(user_id))
        logging.info(f"{user_id} - PeerIdInvalid")
        return False, "Error"
    except Exception:
        return False, "Error"


async def groups_broadcast(chat_id, message, is_pin):
    try:
        m = await message.copy(chat_id=chat_id)
        if is_pin:
            try:
                await m.pin()
            except:
                pass
        return "Success"
    except FloodWait as e:
        await asyncio.sleep(e.x)
        return await groups_broadcast(chat_id, message)
    except Exception:
        await db.delete_chat(chat_id)
        logging.info(f"{chat_id}-Removed from Database.")
        return "Error"


async def get_settings(group_id):
    group_id = int(group_id)
    cached = _SETTINGS_CACHE.get(group_id)
    if cached is not None:
        return cached.copy()
    settings = await db.get_settings(group_id)
    _SETTINGS_CACHE[group_id] = settings.copy()
    return settings.copy()


async def save_group_settings(group_id, key, value):
    group_id = int(group_id)
    current = await get_settings(group_id)
    current[key] = value
    await db.update_settings(group_id, current)
    _SETTINGS_CACHE[group_id] = current.copy()


def get_size(size):
    units = ["Bytes", "KB", "MB", "GB", "TB", "PB", "EB"]
    size = float(size)
    i = 0
    while size >= 1024.0 and i < len(units):
        i += 1
        size /= 1024.0
    return "%.2f %s" % (size, units[i])


def get_name(name):
    regex = re.sub(r"@\w+", "", name)
    return regex


def list_to_str(k):
    if not k:
        return "N/A"
    elif len(k) == 1:
        return str(k[0])
    else:
        return ", ".join(str(item) for item in k)


async def get_shortlink(
    link, grp_id, is_second_shortener=False, is_third_shortener=False
):
    settings = await get_settings(grp_id)
    if is_third_shortener:
        api, site = settings["api_three"], settings["shortner_three"]
    else:
        if is_second_shortener:
            api, site = settings["api_two"], settings["shortner_two"]
        else:
            api, site = settings["api"], settings["shortner"]
    shortzy = Shortzy(api, site)
    try:
        return await shortzy.convert(link)
    except Exception:
        try:
            return await shortzy.get_quick_link(link)
        except Exception:
            return None


def get_file_id(message: "Message") -> Any:
    media_types = (
        "audio",
        "document",
        "photo",
        "sticker",
        "animation",
        "video",
        "voice",
        "video_note",
    )
    if message.media:
        for attr in media_types:
            media = getattr(message, attr, None)
            if media:
                setattr(media, "message_type", attr)
                return media


# def get_hash(media_msg: Message) -> str:
#    media = get_file_id(media_msg)
#   return getattr(media, "file_unique_id", "")[:6]


def get_status():
    tz = pytz.timezone("Asia/Colombo")
    hour = datetime.now(tz).time().hour
    if 5 <= hour < 12:
        sts = "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ"
    elif 12 <= hour < 18:
        sts = "ɢᴏᴏᴅ ᴀꜰᴛᴇʀɴᴏᴏɴ"
    else:
        sts = "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ"
    return sts


async def is_check_admin(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        ]
    except:
        return False


async def get_seconds(time_string):
    def extract_value_and_unit(ts):
        value = ""
        unit = ""
        index = 0
        while index < len(ts) and ts[index].isdigit():
            value += ts[index]
            index += 1
        unit = ts[index:].lstrip()
        if value:
            value = int(value)
        return value, unit

    value, unit = extract_value_and_unit(time_string)
    if unit == "s":
        return value
    elif unit == "min":
        return value * 60
    elif unit == "hour":
        return value * 3600
    elif unit == "day":
        return value * 86400
    elif unit == "month":
        return value * 86400 * 30
    elif unit == "year":
        return value * 86400 * 365
    else:
        return 0


def get_readable_time(seconds):
    periods = [("days", 86400), ("hour", 3600), ("min", 60), ("sec", 1)]
    result = ""
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f"{int(period_value)}{period_name}"
    return result


async def save_default_settings(id):
    await db.reset_group_settings(id)
    current = await db.get_settings(id)
    temp.SETTINGS.update({id: current})
