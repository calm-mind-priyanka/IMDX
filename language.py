"""Shared user-language helpers.

This module lives at project root intentionally: bot.py auto-loads every
plugins/*.py as a handler module, so shared language code must not be placed
inside plugins/.
"""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.users_chats_db import db

# Exactly the languages already offered by the Premium language system.
# Labels remain in English so users can identify them before selecting one.
DEFAULT_LANGUAGE = "en"

LANGUAGES = {
    "en": "🇬🇧 English",
    "hi": "🇮🇳 Hindi",
    "ta": "🇮🇳 Tamil",
    "te": "🇮🇳 Telugu",
    "kn": "🇮🇳 Kannada",
    "ml": "🇮🇳 Malayalam",
    "bn": "🇮🇳 Bengali",
    "mr": "🇮🇳 Marathi",
    "gu": "🇮🇳 Gujarati",
    "pa": "🇮🇳 Punjabi",
    "ur": "🇮🇳 Urdu",
    "as": "🇮🇳 Assamese",
    "ne": "🇳🇵 Nepali",
    "hinglish": "🇮🇳 Hinglish",
}

ALIASES = {
    "en": "en", "en-us": "en", "en-gb": "en",
    "hi": "hi", "hi-in": "hi", "hi-latn": "hinglish",
    "ta": "ta", "te": "te", "kn": "kn", "ml": "ml",
    "bn": "bn", "mr": "mr", "gu": "gu", "pa": "pa",
    "ur": "ur", "as": "as", "ne": "ne", "hinglish": "hinglish",
}


def language_markup(callback_prefix="global_lang:"):
    codes = list(LANGUAGES)
    rows = []
    for i in range(0, len(codes), 2):
        row = [InlineKeyboardButton(LANGUAGES[codes[i]], callback_data=f"{callback_prefix}{codes[i]}")]
        if i + 1 < len(codes):
            row.append(InlineKeyboardButton(LANGUAGES[codes[i + 1]], callback_data=f"{callback_prefix}{codes[i + 1]}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def get_user_language(user_id, telegram_user=None):
    try:
        data = await db.get_user(int(user_id))
        saved = (data or {}).get("language") or (data or {}).get("language_code")
        if saved in LANGUAGES:
            return saved
    except Exception:
        pass
    code = str(getattr(telegram_user, "language_code", "") or "").lower().replace("_", "-")
    return ALIASES.get(code) or ALIASES.get(code.split("-", 1)[0]) or "en"


async def has_saved_language(user_id):
    try:
        data = await db.get_user(int(user_id))
        saved = (data or {}).get("language") or (data or {}).get("language_code")
        return saved in LANGUAGES
    except Exception:
        return False


# Core global UI translations. Labels are intentionally kept short so the
# existing button layout remains unchanged.
UI = {
    "en": {
        "language_title": "🌐 <b>Choose Your Language</b>",
        "language_body": "Select the language you want the bot to use. You can change it anytime.",
        "language_saved": "🌐 Language updated successfully.",
        "language_button": "🌐 Language",
        "back": "⋞ Back",
        "home": "⋞ Back to Home",
        "language": "Language",
        "quality": "Quality",
        "season": "Season",
        "send_all": "Send All Files",
        "no_more": "↭ No More Pages Available ↭",
        "choose_language": "<b>Choose a language from below ↓↓</b>",
        "select_first": "🌐 <b>Please choose your language first.</b>", "season_choose":"Choose the season you want ↓↓", "quality_choose":"Choose the quality you want ↓↓", "language_choose":"Choose the content language you want ↓↓",
    },
    "hi": {
        "language_title": "🌐 <b>अपनी भाषा चुनें</b>", "language_body": "Bot किस भाषा में इस्तेमाल करना है, वह चुनें। आप इसे कभी भी बदल सकते हैं।", "language_saved": "🌐 भाषा सफलतापूर्वक बदल दी गई।", "language_button": "🌐 भाषा", "back": "⋞ वापस", "home": "⋞ होम पर वापस", "language": "भाषा", "quality": "क्वालिटी", "season": "सीज़न", "send_all": "सभी फाइल भेजें", "no_more": "↭ और पेज उपलब्ध नहीं ↭", "choose_language": "<b>नीचे से अपनी भाषा चुनें ↓↓</b>", "select_first": "🌐 <b>पहले अपनी भाषा चुनें।</b>"},
    "ta": {
        "language_title": "🌐 <b>உங்கள் மொழியைத் தேர்வு செய்யவும்</b>", "language_body": "Bot பயன்படுத்த வேண்டிய மொழியைத் தேர்வு செய்யவும். எப்போது வேண்டுமானாலும் மாற்றலாம்.", "language_saved": "🌐 மொழி வெற்றிகரமாக மாற்றப்பட்டது.", "language_button": "🌐 மொழி", "back": "⋞ பின்செல்", "home": "⋞ முகப்புக்கு", "language": "மொழி", "quality": "தரம்", "season": "சீசன்", "send_all": "அனைத்து கோப்புகளையும் அனுப்பு", "no_more": "↭ மேலும் பக்கங்கள் இல்லை ↭", "choose_language": "<b>கீழே இருந்து மொழியைத் தேர்வு செய்யவும் ↓↓</b>", "select_first": "🌐 <b>முதலில் உங்கள் மொழியைத் தேர்வு செய்யவும்.</b>"},
    "te": {
        "language_title": "🌐 <b>మీ భాషను ఎంచుకోండి</b>", "language_body": "Bot ఏ భాషలో ఉండాలో ఎంచుకోండి. ఎప్పుడైనా మార్చవచ్చు.", "language_saved": "🌐 భాష విజయవంతంగా మార్చబడింది.", "language_button": "🌐 భాష", "back": "⋞ వెనక్కి", "home": "⋞ హోమ్‌కు", "language": "భాష", "quality": "క్వాలిటీ", "season": "సీజన్", "send_all": "అన్ని ఫైళ్లను పంపు", "no_more": "↭ మరిన్ని పేజీలు లేవు ↭", "choose_language": "<b>కింద నుంచి భాషను ఎంచుకోండి ↓↓</b>", "select_first": "🌐 <b>ముందుగా మీ భాషను ఎంచుకోండి.</b>"},
    "kn": {
        "language_title": "🌐 <b>ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ</b>", "language_body": "Bot ಯಾವ ಭಾಷೆಯಲ್ಲಿ ಇರಬೇಕು ಎಂದು ಆಯ್ಕೆಮಾಡಿ. ಯಾವಾಗ ಬೇಕಾದರೂ ಬದಲಾಯಿಸಬಹುದು.", "language_saved": "🌐 ಭಾಷೆ ಯಶಸ್ವಿಯಾಗಿ ಬದಲಾಯಿಸಲಾಗಿದೆ.", "language_button": "🌐 ಭಾಷೆ", "back": "⋞ ಹಿಂದೆ", "home": "⋞ ಹೋಮ್‌ಗೆ", "language": "ಭಾಷೆ", "quality": "ಗುಣಮಟ್ಟ", "season": "ಸೀಸನ್", "send_all": "ಎಲ್ಲಾ ಫೈಲ್‌ಗಳನ್ನು ಕಳುಹಿಸಿ", "no_more": "↭ ಇನ್ನಷ್ಟು ಪುಟಗಳಿಲ್ಲ ↭", "choose_language": "<b>ಕೆಳಗಿನಿಂದ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ ↓↓</b>", "select_first": "🌐 <b>ಮೊದಲು ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ.</b>"},
    "ml": {
        "language_title": "🌐 <b>നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക</b>", "language_body": "Bot ഉപയോഗിക്കേണ്ട ഭാഷ തിരഞ്ഞെടുക്കുക. എപ്പോൾ വേണമെങ്കിലും മാറ്റാം.", "language_saved": "🌐 ഭാഷ വിജയകരമായി മാറ്റി.", "language_button": "🌐 ഭാഷ", "back": "⋞ തിരികെ", "home": "⋞ ഹോമിലേക്ക്", "language": "ഭാഷ", "quality": "ക്വാളിറ്റി", "season": "സീസൺ", "send_all": "എല്ലാ ഫയലുകളും അയയ്ക്കുക", "no_more": "↭ കൂടുതൽ പേജുകളില്ല ↭", "choose_language": "<b>താഴെ നിന്ന് ഭാഷ തിരഞ്ഞെടുക്കുക ↓↓</b>", "select_first": "🌐 <b>ആദ്യം നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക.</b>"},
    "bn": {
        "language_title": "🌐 <b>আপনার ভাষা বেছে নিন</b>", "language_body": "Bot কোন ভাষায় ব্যবহার করবেন তা বেছে নিন। পরে যেকোনো সময় বদলাতে পারবেন।", "language_saved": "🌐 ভাষা সফলভাবে পরিবর্তন হয়েছে।", "language_button": "🌐 ভাষা", "back": "⋞ ফিরে যান", "home": "⋞ হোমে ফিরে যান", "language": "ভাষা", "quality": "কোয়ালিটি", "season": "সিজন", "send_all": "সব ফাইল পাঠান", "no_more": "↭ আর কোনো পেজ নেই ↭", "choose_language": "<b>নিচ থেকে ভাষা বেছে নিন ↓↓</b>", "select_first": "🌐 <b>আগে আপনার ভাষা বেছে নিন।</b>"},
    "mr": {
        "language_title": "🌐 <b>तुमची भाषा निवडा</b>", "language_body": "Bot कोणत्या भाषेत वापरायचा ते निवडा. कधीही बदलू शकता.", "language_saved": "🌐 भाषा यशस्वीपणे बदलली.", "language_button": "🌐 भाषा", "back": "⋞ मागे", "home": "⋞ होमवर", "language": "भाषा", "quality": "क्वालिटी", "season": "सीझन", "send_all": "सर्व फाइल्स पाठवा", "no_more": "↭ आणखी पेज उपलब्ध नाहीत ↭", "choose_language": "<b>खालीलमधून भाषा निवडा ↓↓</b>", "select_first": "🌐 <b>आधी तुमची भाषा निवडा.</b>"},
    "gu": {
        "language_title": "🌐 <b>તમારી ભાષા પસંદ કરો</b>", "language_body": "Bot કઈ ભાષામાં વાપરવો તે પસંદ કરો. તમે ક્યારે પણ બદલી શકો છો.", "language_saved": "🌐 ભાષા સફળતાપૂર્વક બદલાઈ ગઈ.", "language_button": "🌐 ભાષા", "back": "⋞ પાછા", "home": "⋞ હોમ પર", "language": "ભાષા", "quality": "ક્વોલિટી", "season": "સીઝન", "send_all": "બધી ફાઇલો મોકલો", "no_more": "↭ વધુ પેજ ઉપલબ્ધ નથી ↭", "choose_language": "<b>નીચેથી ભાષા પસંદ કરો ↓↓</b>", "select_first": "🌐 <b>પહેલા તમારી ભાષા પસંદ કરો.</b>"},
    "pa": {
        "language_title": "🌐 <b>ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ</b>", "language_body": "Bot ਲਈ ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ। ਤੁਸੀਂ ਇਸਨੂੰ ਕਦੇ ਵੀ ਬਦਲ ਸਕਦੇ ਹੋ।", "language_saved": "🌐 ਭਾਸ਼ਾ ਸਫਲਤਾਪੂਰਵਕ ਬਦਲ ਦਿੱਤੀ ਗਈ।", "language_button": "🌐 ਭਾਸ਼ਾ", "back": "⋞ ਵਾਪਸ", "home": "⋞ ਹੋਮ ਤੇ", "language": "ਭਾਸ਼ਾ", "quality": "ਕੁਆਲਿਟੀ", "season": "ਸੀਜ਼ਨ", "send_all": "ਸਾਰੀਆਂ ਫਾਈਲਾਂ ਭੇਜੋ", "no_more": "↭ ਹੋਰ ਪੇਜ ਨਹੀਂ ਹਨ ↭", "choose_language": "<b>ਹੇਠਾਂ ਤੋਂ ਭਾਸ਼ਾ ਚੁਣੋ ↓↓</b>", "select_first": "🌐 <b>ਪਹਿਲਾਂ ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ।</b>"},
    "ur": {
        "language_title": "🌐 <b>اپنی زبان منتخب کریں</b>", "language_body": "Bot کے لیے اپنی پسند کی زبان منتخب کریں۔ آپ اسے کبھی بھی تبدیل کر سکتے ہیں۔", "language_saved": "🌐 زبان کامیابی سے تبدیل ہو گئی۔", "language_button": "🌐 زبان", "back": "⋞ واپس", "home": "⋞ ہوم پر", "language": "زبان", "quality": "کوالٹی", "season": "سیزن", "send_all": "تمام فائلیں بھیجیں", "no_more": "↭ مزید صفحات دستیاب نہیں ↭", "choose_language": "<b>نیچے سے زبان منتخب کریں ↓↓</b>", "select_first": "🌐 <b>پہلے اپنی زبان منتخب کریں۔</b>"},
    "as": {
        "language_title": "🌐 <b>আপোনাৰ ভাষা বাছক</b>", "language_body": "Bot কোন ভাষাত ব্যৱহাৰ কৰিব বিচাৰে বাছক। পিছত যিকোনো সময়ত সলনি কৰিব পাৰে।", "language_saved": "🌐 ভাষা সফলভাৱে সলনি কৰা হৈছে।", "language_button": "🌐 ভাষা", "back": "⋞ পিছলৈ", "home": "⋞ হোমলৈ", "language": "ভাষা", "quality": "কোৱালিটি", "season": "ছিজন", "send_all": "সকলো ফাইল পঠাওক", "no_more": "↭ আৰু পৃষ্ঠা নাই ↭", "choose_language": "<b>তলৰ পৰা ভাষা বাছক ↓↓</b>", "select_first": "🌐 <b>আগতে আপোনাৰ ভাষা বাছক।</b>"},
    "ne": {
        "language_title": "🌐 <b>आफ्नो भाषा छान्नुहोस्</b>", "language_body": "Bot कुन भाषामा प्रयोग गर्ने हो छान्नुहोस्। पछि जुनसुकै बेला बदल्न सक्नुहुन्छ।", "language_saved": "🌐 भाषा सफलतापूर्वक बदलियो।", "language_button": "🌐 भाषा", "back": "⋞ पछाडि", "home": "⋞ होममा", "language": "भाषा", "quality": "क्वालिटी", "season": "सिजन", "send_all": "सबै फाइल पठाउनुहोस्", "no_more": "↭ थप पेज उपलब्ध छैन ↭", "choose_language": "<b>तलबाट भाषा छान्नुहोस् ↓↓</b>", "select_first": "🌐 <b>पहिले आफ्नो भाषा छान्नुहोस्।</b>"},
    "hinglish": {
        "language_title": "🌐 <b>Apni Language Choose Karo</b>", "language_body": "Bot ko kis language mein use karna hai choose karo. Baad mein kabhi bhi change kar sakte ho.", "language_saved": "🌐 Language successfully update ho gayi.", "language_button": "🌐 Language", "back": "⋞ Back", "home": "⋞ Home Par", "language": "Language", "quality": "Quality", "season": "Season", "send_all": "Saari Files Send Karo", "no_more": "↭ Aur Pages Available Nahi Hain ↭", "choose_language": "<b>Neeche se language choose karo ↓↓</b>", "select_first": "🌐 <b>Pehle apni language choose karo.</b>"},
}


_SMALL_CAPS = str.maketrans({"a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"ғ","g":"ɢ","h":"ʜ","i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ","m":"ᴍ","n":"ɴ","o":"ᴏ","p":"ᴘ","q":"ǫ","r":"ʀ","s":"ꜱ","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x","y":"ʏ","z":"ᴢ"})

def small_caps(text):
    """Apply the single requested Unicode Small-Caps style without corrupting HTML."""
    if text is None: return text
    parts = __import__("re").split(r"(<[^>]*>)", str(text))
    return "".join(part if part.startswith("<") and part.endswith(">") else part.lower().translate(_SMALL_CAPS) for part in parts)

def tr(lang, key):
    return small_caps(UI.get(lang, UI["en"]).get(key, UI["en"].get(key, key)))


# User-facing core text used throughout the normal bot flow.  English is the
# safe default for old accounts and for helper/system contexts that have no
# saved language yet.
CORE = {
    "en": {
        "start": "ʜᴇʏ {mention}, {status}\n\nɪ ᴀᴍ ᴀ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ. ᴜsᴇ ᴍᴇ ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ ᴀɴᴅ ᴘᴍ ᴛᴏ ғɪɴᴅ ᴍᴏᴠɪᴇs ᴀɴᴅ sᴇʀɪᴇs. 😍\n<blockquote>🌿 ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ : <a href=\"https://t.me/+DiOcxJnNQXdmNDdl\">sandy Bots &lt;/&gt;</a></blockquote>",
        "help": "<b>ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ᴛᴏ ᴠɪᴇᴡ ᴛʜᴇ ʙᴏᴛ ᴅᴏᴄᴜᴍᴇɴᴛᴀᴛɪᴏɴ.</b>",
        "about": "<blockquote><b>‣ ᴍʏ ɴᴀᴍᴇ : Jisshu filter bot\n‣ ᴄʀᴇᴀᴛᴏʀ : <a href='https://t.me/+DiOcxJnNQXdmNDdl'>sandy Bots &lt;/&gt;</a>\n‣ ʟɪʙʀᴀʀʏ : ᴘʏʀᴏɢʀᴀᴍ\n‣ ʟᴀɴɢᴜᴀɢᴇ : ᴘʏᴛʜᴏɴ\n‣ ᴅᴀᴛᴀ ʙᴀsᴇ : ᴍᴏɴɢᴏ ᴅʙ\n‣ ʜᴏsᴛᴇᴅ ᴏɴ : ᴡᴇʙ\n‣ ʙᴜɪʟᴅ sᴛᴀᴛᴜs : V-4.1 [sᴛᴀʙʟᴇ]</b></blockquote>",
        "alert": "ᴡʜᴀᴛ ᴀʀᴇ ʏᴏᴜ sᴇᴀʀᴄʜɪɴɢ!?",
        "old_alert": "ʏᴏᴜ ᴀʀᴇ ᴜsɪɴɢ ᴀɴ ᴏʟᴅ ᴍᴇssᴀɢᴇ. sᴇɴᴅ ᴀ ɴᴇᴡ ʀᴇǫᴜᴇsᴛ.",
        "no_result": "<b>ᴛʜɪs ᴍᴏᴠɪᴇ ᴏʀ sᴇʀɪᴇs ᴡᴀs ɴᴏᴛ ғᴏᴜɴᴅ ɪɴ ᴛʜᴇ ᴅᴀᴛᴀʙᴀsᴇ. 🙄</b>",
        "quality_choose": "<b>ᴄʜᴏᴏsᴇ ᴛʜᴇ ǫᴜᴀʟɪᴛʏ ʏᴏᴜ ᴡᴀɴᴛ ↓↓</b>",
        "season_choose": "<b>ᴄʜᴏᴏsᴇ ᴛʜᴇ sᴇᴀsᴏɴ ʏᴏᴜ ᴡᴀɴᴛ ↓↓</b>",
        "language_choose": "<b>ᴄʜᴏᴏsᴇ ᴛʜᴇ ᴄᴏɴᴛᴇɴᴛ ʟᴀɴɢᴜᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ↓↓</b>",
        "not_found": "sᴏʀʀʏ, {kind} {value} ɴᴏᴛ ғᴏᴜɴᴅ ғᴏʀ {search}.",
        "back_main": "⋞ ʙᴀᴄᴋ ᴛᴏ ᴍᴀɪɴ ᴘᴀɢᴇ",
        "back": "⋞ ʙᴀᴄᴋ",
        "next": "ɴᴇxᴛ ⋟",
        "send_all": "sᴇɴᴅ ᴀʟʟ ғɪʟᴇs",
        "language": "ʟᴀɴɢᴜᴀɢᴇ",
        "quality": "ǫᴜᴀʟɪᴛʏ",
        "season": "sᴇᴀsᴏɴ",
        "no_more": "↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇs ᴀᴠᴀɪʟᴀʙʟᴇ ↭",
    },

    "hi": {"start":"ʜᴇʏ {mention}, {status}\n\nʏᴇ ᴇᴋ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ ʜᴀɪ. ɢʀᴏᴜᴘ ᴀᴜʀ ᴘᴍ ᴍᴇɪɴ ᴍᴏᴠɪᴇs ᴀᴜʀ sᴇʀɪᴇs ᴋᴇ ʟɪʏᴇ ᴍᴜᴊʜᴇ ᴜsᴇ ᴋᴀʀᴇɴ. 😍"},
    "ta": {"start":"ʜᴇʏ {mention}, {status}\n\nɪᴛᴜ ᴏʀᴜ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ. ɢʀᴏᴜᴘ ᴍᴀᴛᴛʀᴜᴍ ᴘᴍ-ɪʟ ᴍᴏᴠɪᴇs ᴍᴀᴛᴛʀᴜᴍ sᴇʀɪᴇs ᴛᴇᴛᴀ ᴇɴɴᴀɪ ᴜsᴇ ᴘᴀɴɴᴀʟᴀᴍ. 😍"},
    "te": {"start":"ʜᴇʏ {mention}, {status}\n\nɪᴅɪ ᴏᴋᴀ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ. ɢʀᴏᴜᴘ ᴀʟᴀɢᴇ ᴘᴍʟᴏ ᴍᴏᴠɪᴇs ᴍᴀʀɪʏᴜ sᴇʀɪᴇs ᴋᴏsᴀᴍ ᴜsᴇ ᴄʜᴇʏᴀɴᴅɪ. 😍"},
    "kn": {"start":"ʜᴇʏ {mention}, {status}\n\nɪᴅᴜ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ. ɢʀᴏᴜᴘ ʜᴀɢᴜ ᴘᴍ-ɴᴀʟʟɪ ᴍᴏᴠɪᴇ ʜᴀɢᴜ sᴇʀɪᴇs ᴘᴀᴅᴇʏᴀʟᴜ ᴜsᴇ ᴍᴀᴅɪ. 😍"},
    "ml": {"start":"ʜᴇʏ {mention}, {status}\n\nɪᴛʜᴜ ᴏʀᴜ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ ᴀᴀɴᴜ. ɢʀᴏᴜᴘɪʟᴜᴍ ᴘᴍ-ɪʟᴜᴍ ᴍᴏᴠɪᴇsᴜᴍ sᴇʀɪᴇsᴜᴍ ᴛʜᴇᴛᴀᴀɴ ᴜsᴇ ᴄʜᴇʏʏᴀᴀᴍ. 😍"},
    "bn": {"start":"ʜᴇʏ {mention}, {status}\n\nএটি একটি শক্তিশালী অটোফিল্টার বট। গ্রুপ ও PM-এ মুভি এবং সিরিজ খুঁজতে আমাকে ব্যবহার করুন। 😍"},
    "mr": {"start":"ʜᴇʏ {mention}, {status}\n\nʜᴀ ᴇᴋ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ ᴀʜᴇ. ɢʀᴜᴘ ᴀɴɪ ᴘᴍ ᴍᴀᴅʜʏᴇ ᴍᴏᴠɪᴇs ᴀᴀɴɪ sᴇʀɪᴇs sʜᴏᴅɴʏᴀsᴀᴛʜɪ ᴠᴀᴘʀᴀ. 😍"},
    "gu": {"start":"ʜᴇʏ {mention}, {status}\n\nᴀᴀ ᴇᴋ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ ᴄʜᴇ. ɢʀᴏᴜᴘ ᴀɴᴇ ᴘᴍ ᴍᴀɴ ᴍᴏᴠɪᴇ ᴀɴᴇ sᴇʀɪᴇs sʜᴏᴅᴠᴀ ᴍᴀᴛᴇ ᴍᴀɴᴇ ᴠᴀᴘʀᴏ. 😍"},
    "pa": {"start":"ʜᴇʏ {mention}, {status}\n\nᴇʜ ᴇᴋ ᴘᴏᴡᴇʀғᴜʟ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ ʜᴀɪ. ɢʀᴏᴜᴘ ᴀᴛᴇ ᴘᴍ ᴠɪᴄʜ ᴍᴏᴠɪᴇᴀɴ ᴛᴇ sᴇʀɪᴇs ʟᴀʙʜᴀɴ ʟᴀɪ ᴠᴀʀᴛᴏ. 😍"},
    "ur": {"start":"ʜᴇʏ {mention}, {status}\n\nʏᴇ ᴇᴋ ᴛᴀǫᴀᴛᴡᴀʀ ᴀᴜᴛᴏғɪʟᴛᴇʀ ʙᴏᴛ ʜᴀɪ. ɢʀᴏᴜᴘ ᴀᴜʀ ᴘᴍ ᴍᴇɪɴ ᴍᴏᴠɪᴇs ᴀᴜʀ sᴇʀɪᴇs ᴛᴀʟᴀsʜ ᴋᴀʀɴᴇ ᴋᴇ ʟɪʏᴇ ᴜsᴇ ᴋᴀʀᴇɪɴ. 😍"},
    "as": {"start":"ʜᴇʏ {mention}, {status}\n\nই এটা শক্তিশালী অটোফিল্টাৰ বট। গ্ৰুপ আৰু PM-ত মুভি আৰু ছিৰিজ বিচাৰিবলৈ মোক ব্যৱহাৰ কৰক। 😍"},
    "ne": {"start":"ʜᴇʏ {mention}, {status}\n\nयो एउटा शक्तिशाली अटोफिल्टर बोट हो। ग्रुप र PM मा चलचित्र तथा सिरिज खोज्न मलाई प्रयोग गर्नुहोस्। 😍"},
    "hinglish": {"start":"ʜᴇʏ {mention}, {status}\n\nYeh ek powerful AutoFilter bot hai. Group aur PM mein movies aur series find karne ke liye mujhe use karo. 😍"},
}
# For the remaining languages, keep the complete UI controls localized via UI;
# core long-form text falls back to English until an exact translation exists.
for _code, _ui in UI.items():
    CORE.setdefault(_code, {})
    for _key in ("back","send_all","language","quality","season","no_more"):
        CORE[_code][_key] = _ui.get(_key, CORE["en"][_key])

# Direct alert shown by Season/Quality/Content-Language filters.  The searched
# title/value is inserted unchanged; only the surrounding bot text is localized.
_NOT_FOUND = {
 "en":"sᴏʀʀʏ, {kind} {value} ɴᴏᴛ ғᴏᴜɴᴅ ғᴏʀ {search}.",
 "hi":"माफ़ कीजिए, {search} के लिए {kind} {value} नहीं मिला।",
 "ta":"மன்னிக்கவும், {search} க்கு {kind} {value} கிடைக்கவில்லை.",
 "te":"క్షమించండి, {search} కోసం {kind} {value} కనుగొనబడలేదు.",
 "kn":"ಕ್ಷಮಿಸಿ, {search} ಗೆ {kind} {value} ಕಂಡುಬಂದಿಲ್ಲ.",
 "ml":"ക്ഷമിക്കണം, {search} ന് {kind} {value} കണ്ടെത്താനായില്ല.",
 "bn":"দুঃখিত, {search}-এর জন্য {kind} {value} পাওয়া যায়নি।",
 "mr":"माफ करा, {search} साठी {kind} {value} सापडले नाही.",
 "gu":"માફ કરશો, {search} માટે {kind} {value} મળ્યું નથી.",
 "pa":"ਮਾਫ਼ ਕਰਨਾ, {search} ਲਈ {kind} {value} ਨਹੀਂ ਮਿਲਿਆ।",
 "ur":"معذرت، {search} کے لیے {kind} {value} نہیں ملا۔",
 "as":"ক্ষমা কৰিব, {search}ৰ বাবে {kind} {value} পোৱা নগ'ল।",
 "ne":"माफ गर्नुहोस्, {search} का लागि {kind} {value} फेला परेन।",
 "hinglish":"Sorry, {search} ke liye {kind} {value} nahi mila.",
}
for _code, _text in _NOT_FOUND.items():
    CORE.setdefault(_code, {})["not_found"] = _text


def core_tr(lang, key, **values):
    data = CORE.get(lang) or CORE[DEFAULT_LANGUAGE]
    text = data.get(key) or CORE[DEFAULT_LANGUAGE].get(key, key)
    try:
        return small_caps(text.format(**values))
    except Exception:
        return small_caps(text)


# User-facing pages that are shown when navigating back from Home.  These are
# deliberately separate from admin/settings text; the selected language belongs
# to the Telegram user and never changes another user's UI.
PAGE_I18N = {
    "en": {
        "help": "<b>ᴄʟɪᴄᴋ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ᴛᴏ ᴠɪᴇᴡ ᴛʜᴇ ʙᴏᴛ ᴅᴏᴄᴜᴍᴇɴᴛᴀᴛɪᴏɴ.</b>",
        "about": "<blockquote><b>‣ ᴍʏ ɴᴀᴍᴇ : Jisshu filter bot\n‣ ᴄʀᴇᴀᴛᴏʀ : sandy Bots\n‣ ʟɪʙʀᴀʀʏ : ᴘʏʀᴏɢʀᴀᴍ\n‣ ʟᴀɴɢᴜᴀɢᴇ : ᴘʏᴛʜᴏɴ\n‣ ᴅᴀᴛᴀʙᴀsᴇ : ᴍᴏɴɢᴏ ᴅʙ\n‣ ʙᴜɪʟᴅ : V-4.1 [sᴛᴀʙʟᴇ]</b></blockquote>",
    },
    "hi": {"help":"<b>नीचे दिए गए बटन दबाकर Bot की जानकारी देखें।</b>","about":"<blockquote><b>‣ नाम : Jisshu filter bot\n‣ क्रिएटर : sandy Bots\n‣ लाइब्रेरी : Pyrogram\n‣ भाषा : Python\n‣ डेटाबेस : MongoDB\n‣ बिल्ड : V-4.1 [stable]</b></blockquote>"},
    "ta": {"help":"<b>கீழே உள்ள பொத்தான்களை அழுத்தி Bot தகவல்களைப் பார்க்கவும்.</b>","about":"<blockquote><b>‣ பெயர் : Jisshu filter bot\n‣ உருவாக்கியவர் : sandy Bots\n‣ Library : Pyrogram\n‣ மொழி : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "te": {"help":"<b>Bot వివరాలను చూడటానికి క్రింద ఉన్న బటన్లను నొక్కండి.</b>","about":"<blockquote><b>‣ పేరు : Jisshu filter bot\n‣ క్రియేటర్ : sandy Bots\n‣ Library : Pyrogram\n‣ భాష : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "kn": {"help":"<b>Bot ಮಾಹಿತಿಯನ್ನು ನೋಡಲು ಕೆಳಗಿನ ಬಟನ್‌ಗಳನ್ನು ಒತ್ತಿರಿ.</b>","about":"<blockquote><b>‣ ಹೆಸರು : Jisshu filter bot\n‣ ಸೃಷ್ಟಿಕರ್ತ : sandy Bots\n‣ Library : Pyrogram\n‣ ಭಾಷೆ : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "ml": {"help":"<b>Bot വിവരങ്ങൾ കാണാൻ താഴെയുള്ള ബട്ടണുകൾ അമർത്തുക.</b>","about":"<blockquote><b>‣ പേര് : Jisshu filter bot\n‣ സ്രഷ്ടാവ് : sandy Bots\n‣ Library : Pyrogram\n‣ ഭാഷ : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "bn": {"help":"<b>Bot-এর তথ্য দেখতে নিচের বাটনগুলো চাপুন।</b>","about":"<blockquote><b>‣ নাম : Jisshu filter bot\n‣ নির্মাতা : sandy Bots\n‣ Library : Pyrogram\n‣ ভাষা : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "mr": {"help":"<b>Bot ची माहिती पाहण्यासाठी खालील बटणे दाबा.</b>","about":"<blockquote><b>‣ नाव : Jisshu filter bot\n‣ निर्माता : sandy Bots\n‣ Library : Pyrogram\n‣ भाषा : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "gu": {"help":"<b>Bot ની માહિતી જોવા નીચેના બટનો દબાવો.</b>","about":"<blockquote><b>‣ નામ : Jisshu filter bot\n‣ નિર્માતા : sandy Bots\n‣ Library : Pyrogram\n‣ ભાષા : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "pa": {"help":"<b>Bot ਦੀ ਜਾਣਕਾਰੀ ਦੇਖਣ ਲਈ ਹੇਠਾਂ ਦਿੱਤੇ ਬਟਨ ਦਬਾਓ।</b>","about":"<blockquote><b>‣ ਨਾਮ : Jisshu filter bot\n‣ ਨਿਰਮਾਤਾ : sandy Bots\n‣ Library : Pyrogram\n‣ ਭਾਸ਼ਾ : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "ur": {"help":"<b>Bot کی معلومات دیکھنے کے لیے نیچے دیے گئے بٹن دبائیں۔</b>","about":"<blockquote><b>‣ نام : Jisshu filter bot\n‣ تخلیق کار : sandy Bots\n‣ Library : Pyrogram\n‣ زبان : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "as": {"help":"<b>Bot-ৰ তথ্য চাবলৈ তলৰ বুটামসমূহ টিপক।</b>","about":"<blockquote><b>‣ নাম : Jisshu filter bot\n‣ নিৰ্মাতা : sandy Bots\n‣ Library : Pyrogram\n‣ ভাষা : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "ne": {"help":"<b>Bot को जानकारी हेर्न तलका बटनहरू थिच्नुहोस्।</b>","about":"<blockquote><b>‣ नाम : Jisshu filter bot\n‣ निर्माता : sandy Bots\n‣ Library : Pyrogram\n‣ भाषा : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
    "hinglish": {"help":"<b>Bot ki information dekhne ke liye neeche diye buttons par click karo.</b>","about":"<blockquote><b>‣ Name : Jisshu filter bot\n‣ Creator : sandy Bots\n‣ Library : Pyrogram\n‣ Language : Python\n‣ Database : MongoDB\n‣ Build : V-4.1 [stable]</b></blockquote>"},
}

def page_tr(lang, key):
    return small_caps(PAGE_I18N.get(lang, PAGE_I18N[DEFAULT_LANGUAGE]).get(key, PAGE_I18N[DEFAULT_LANGUAGE].get(key, key)))

HOME_LABELS = {
"en": {"add_group":"⇋ Add Me To Your Group ⇋","disable_ads":"• Disable Ads •","special":"• Special •","help":"• Help •","about":"• About •","earn":"• Earn Unlimited Money •"},
"hi": {"add_group":"⇋ मुझे अपने ग्रुप में जोड़ें ⇋","disable_ads":"• Ads बंद करें •","special":"• विशेष •","help":"• मदद •","about":"• जानकारी •","earn":"• कमाई करें •"},
"ta": {"add_group":"⇋ உங்கள் குழுவில் என்னை சேர்க்கவும் ⇋","disable_ads":"• Ads நீக்கு •","special":"• சிறப்பு •","help":"• உதவி •","about":"• பற்றி •","earn":"• சம்பாதிக்கவும் •"},
"te": {"add_group":"⇋ నన్ను మీ గ్రూప్‌లో చేర్చండి ⇋","disable_ads":"• Ads ఆపండి •","special":"• ప్రత్యేకం •","help":"• సహాయం •","about":"• గురించి •","earn":"• సంపాదించండి •"},
"kn": {"add_group":"⇋ ನನ್ನನ್ನು ನಿಮ್ಮ ಗ್ರೂಪ್‌ಗೆ ಸೇರಿಸಿ ⇋","disable_ads":"• Ads ನಿಲ್ಲಿಸಿ •","special":"• ವಿಶೇಷ •","help":"• ಸಹಾಯ •","about":"• ಬಗ್ಗೆ •","earn":"• ಸಂಪಾದಿಸಿ •"},
"ml": {"add_group":"⇋ എന്നെ നിങ്ങളുടെ ഗ്രൂപ്പിൽ ചേർക്കുക ⇋","disable_ads":"• Ads ഒഴിവാക്കുക •","special":"• പ്രത്യേകത •","help":"• സഹായം •","about":"• കുറിച്ച് •","earn":"• സമ്പാദിക്കുക •"},
"bn": {"add_group":"⇋ আমাকে আপনার গ্রুপে যোগ করুন ⇋","disable_ads":"• Ads বন্ধ করুন •","special":"• বিশেষ •","help":"• সাহায্য •","about":"• সম্পর্কে •","earn":"• আয় করুন •"},
"mr": {"add_group":"⇋ मला तुमच्या ग्रुपमध्ये जोडा ⇋","disable_ads":"• Ads बंद करा •","special":"• विशेष •","help":"• मदत •","about":"• माहिती •","earn":"• कमवा •"},
"gu": {"add_group":"⇋ મને તમારા ગ્રુપમાં ઉમેરો ⇋","disable_ads":"• Ads બંધ કરો •","special":"• ખાસ •","help":"• મદદ •","about":"• વિશે •","earn":"• કમાઓ •"},
"pa": {"add_group":"⇋ ਮੈਨੂੰ ਆਪਣੇ ਗਰੁੱਪ ਵਿੱਚ ਜੋੜੋ ⇋","disable_ads":"• Ads ਬੰਦ ਕਰੋ •","special":"• ਖਾਸ •","help":"• ਮਦਦ •","about":"• ਜਾਣਕਾਰੀ •","earn":"• ਕਮਾਓ •"},
"ur": {"add_group":"⇋ مجھے اپنے گروپ میں شامل کریں ⇋","disable_ads":"• Ads بند کریں •","special":"• خاص •","help":"• مدد •","about":"• تعارف •","earn":"• کمائیں •"},
"as": {"add_group":"⇋ মোক আপোনাৰ গ্ৰুপত যোগ কৰক ⇋","disable_ads":"• Ads বন্ধ কৰক •","special":"• বিশেষ •","help":"• সহায় •","about":"• পৰিচয় •","earn":"• উপাৰ্জন কৰক •"},
"ne": {"add_group":"⇋ मलाई आफ्नो ग्रुपमा थप्नुहोस् ⇋","disable_ads":"• Ads बन्द गर्नुहोस् •","special":"• विशेष •","help":"• मद्दत •","about":"• परिचय •","earn":"• कमाउनुहोस् •"},
"hinglish": {"add_group":"⇋ Mujhe Apne Group Mein Add Karo ⇋","disable_ads":"• Ads Disable Karo •","special":"• Special •","help":"• Help •","about":"• About •","earn":"• Earn Karo •"},
}

def home_tr(lang, key):
    return small_caps(HOME_LABELS.get(lang, HOME_LABELS[DEFAULT_LANGUAGE]).get(key, HOME_LABELS[DEFAULT_LANGUAGE][key]))

_MOVIE_DISPLAY_NAMES = {"en":"Venom","hi":"वेनम","ta":"வெனோம்","te":"వెనమ్","kn":"ವೆನಮ್","ml":"വെനം","bn":"ভেনম","mr":"व्हेनम","gu":"વેનમ","pa":"ਵੇਨਮ","ur":"وینم","as":"ভেনম","ne":"भेनम","hinglish":"Venom"}
def display_movie_name(lang, name):
    if name and str(name).strip().casefold() == "venom": return _MOVIE_DISPLAY_NAMES.get(lang, "Venom")
    return name

def search_tr(lang, key, search):
    texts={"searching":{"en":"🎯 Searching {search}","hi":"🎯 {search} खोजा जा रहा है","ta":"🎯 {search} தேடப்படுகிறது","te":"🎯 {search} కోసం వెతుకుతున్నాము","kn":"🎯 {search} ಹುಡುಕಲಾಗುತ್ತಿದೆ","ml":"🎯 {search} തിരയുന്നു","bn":"🎯 {search} খোঁজা হচ্ছে","mr":"🎯 {search} शोधत आहोत","gu":"🎯 {search} શોધી રહ્યા છીએ","pa":"🎯 {search} ਖੋਜਿਆ ਜਾ ਰਿਹਾ ਹੈ","ur":"🎯 {search} تلاش کیا جا رہا ہے","as":"🎯 {search} বিচৰা হৈছে","ne":"🎯 {search} खोजिँदैछ","hinglish":"🎯 {search} Search Ho Raha Hai"},"found":{"en":"📂 Here I found for your search {search}","hi":"📂 आपके {search} सर्च के लिए मिल गया","ta":"📂 உங்கள் {search} தேடலுக்கான கோப்புகள் கிடைத்தன","te":"📂 మీ {search} శోధన కోసం ఫైళ్లు దొరికాయి","kn":"📂 ನಿಮ್ಮ {search} ಹುಡುಕಾಟಕ್ಕೆ ಫೈಲ್‌ಗಳು ಸಿಕ್ಕಿವೆ","ml":"📂 നിങ്ങളുടെ {search} തിരച്ചിലിന് ഫയലുകൾ കണ്ടെത്തി","bn":"📂 আপনার {search} সার্চের জন্য পাওয়া গেছে","mr":"📂 तुमच्या {search} शोधासाठी फाइल्स सापडल्या","gu":"📂 તમારા {search} સર્ચ માટે ફાઇલો મળી ગઈ","pa":"📂 ਤੁਹਾਡੀ {search} ਖੋਜ ਲਈ ਫਾਈਲਾਂ ਮਿਲ ਗਈਆਂ","ur":"📂 آپ کی {search} تلاش کے لیے فائلیں مل گئیں","as":"📂 আপোনাৰ {search} সন্ধানৰ বাবে ফাইল পোৱা গৈছে","ne":"📂 तपाईंको {search} खोजका लागि फाइलहरू भेटिए","hinglish":"📂 Aapke {search} search ke liye files mil gayi"}}
    return small_caps(texts.get(key,{}).get(lang,texts.get(key,{}).get("en",key))).format(search=display_movie_name(lang,search))

VERIFY = {
"en": {
 "verify1":"<b>👋 Hey {mention}, {status},\n\n📌 You are not verified today. Click Verify to get unlimited access until the next verification.\n\n#Verification: 1/3 ✓\n\nIf you want direct files without verification, buy Premium. 😊\n\n💎 Send /plan to buy Premium.</b>",
 "verify2":"<b>👋 Hey {mention}, {status},\n\n📌 You are not verified. Tap the verification link to get unlimited access until the next verification.\n\n#Verification: 2/3\n\nIf you want direct files without verification, buy Premium. 😊\n\n💎 Send /plan to buy Premium.</b>",
 "verify3":"<b>👋 Hey {mention},\n\n📌 You are not verified today. Tap the verification link to get unlimited access for the next full day.\n\n#Verification: 3/3\n\nWant direct files? Premium gives you direct access with no verification.</b>",
 "done":"<b>👋 Hey {mention},\n\nYou completed verification {num} ✓\n\nYou now have unlimited access for the next <code>{duration}</code>.</b>",
 "short1":"<b>👋 Good {greeting}, {mention}!\n\n🎬 <b>File Ready</b>\n\n📁 <b>{name}</b>\n📦 <b>Size:</b> {size}\n\n🔗 Complete the step below to unlock it.\n\n🔐 <b>Shortlink Verification</b>\n📊 <b>Progress:</b> 🟢 <b>1 / 3</b>\n\n🔹 <b>Step 1:</b> Complete the verification below.</b>",
 "short2":"<b>👋 Good {greeting}, {mention}!\n\n🎬 <b>File Ready</b>\n\n📁 <b>{name}</b>\n📦 <b>Size:</b> {size}\n\n🔗 One step is complete. Continue with the next step.\n\n🔐 <b>Shortlink Verification</b>\n📊 <b>Progress:</b> 🟡 <b>2 / 3</b>\n\n🔹 <b>Step 2:</b> Complete the next verification.</b>",
 "short3":"<b>👋 Good {greeting}, {mention}!\n\n🎬 <b>File Ready</b>\n\n📁 <b>{name}</b>\n📦 <b>Size:</b> {size}\n\n🔗 This is the final step to unlock your file.\n\n🔐 <b>Shortlink Verification</b>\n📊 <b>Progress:</b> 🔴 <b>3 / 3</b>\n\n🔹 <b>Step 3:</b> Complete the final verification to unlock the file.</b>",
},
}
# Concise translations for the actual verification gate. The layout and placeholders
# stay identical so existing links/file information remain untouched.
VERIFY.update({
"hi":{"verify1":"<b>👋 Hey {mention}, {status},\n\n📌 आज आप verified नहीं हैं। Verify करके अगले verification तक unlimited access पाएं।\n\n#Verification: 1/3 ✓\n\nबिना verification direct files चाहिए तो Premium खरीदें। 😊\n\n💎 Premium के लिए /plan भेजें।</b>","verify2":"<b>👋 Hey {mention}, {status},\n\n📌 आप verified नहीं हैं। Verification link खोलकर अगली verification तक unlimited access पाएं।\n\n#Verification: 2/3\n\nबिना verification direct files चाहिए तो Premium खरीदें। 😊\n\n💎 Premium के लिए /plan भेजें।</b>","verify3":"<b>👋 Hey {mention},\n\n📌 आज आप verified नहीं हैं। Verification link खोलकर अगले पूरे दिन का access पाएं।\n\n#Verification: 3/3\n\nDirect files के लिए Premium लें; verification की जरूरत नहीं होगी।</b>","done":"<b>👋 Hey {mention},\n\nआपने verification {num} पूरा कर लिया ✓\n\nअब आपके पास अगले <code>{duration}</code> तक unlimited access है।</b>"},
"hinglish":{"verify1":"<b>👋 Hey {mention}, {status},\n\n📌 Aaj aap verified nahi ho. Verify karo aur next verification tak unlimited access pao.\n\n#Verification: 1/3 ✓\n\nBina verification direct files chahiye to Premium lo. 😊\n\n💎 Premium ke liye /plan bhejo.</b>","verify2":"<b>👋 Hey {mention}, {status},\n\n📌 Aap verified nahi ho. Verification link open karo aur next verification tak unlimited access pao.\n\n#Verification: 2/3\n\nBina verification direct files chahiye to Premium lo. 😊\n\n💎 Premium ke liye /plan bhejo.</b>","verify3":"<b>👋 Hey {mention},\n\n📌 Aaj aap verified nahi ho. Verification link open karke next full day ka access pao.\n\n#Verification: 3/3\n\nDirect files ke liye Premium lo; verification ki zarurat nahi hogi.</b>","done":"<b>👋 Hey {mention},\n\nAapne verification {num} complete kar liya ✓\n\nAb aapke paas next <code>{duration}</code> tak unlimited access hai.</b>"},
})
for _c in LANGUAGES:
    VERIFY.setdefault(_c, VERIFY["en"])
# Every offered language gets its own verification/shortlink copy.
_VERIFY_CORE = {
 "ta":("👋 வணக்கம் {mention}, {status}","இன்று நீங்கள் verified செய்யப்படவில்லை. Verify செய்து அடுத்த verification வரை unlimited access பெறுங்கள்.","Premium வாங்கினால் verification தேவையில்லை."),
 "te":("👋 హాయ్ {mention}, {status}","ఈ రోజు మీరు verified కాలేదు. Verify చేసి తదుపరి verification వరకు unlimited access పొందండి.","Premium కొనుగోలు చేస్తే verification అవసరం లేదు."),
 "kn":("👋 ನಮಸ್ಕಾರ {mention}, {status}","ಇಂದು ನೀವು verified ಆಗಿಲ್ಲ. Verify ಮಾಡಿ ಮುಂದಿನ verification ವರೆಗೆ unlimited access ಪಡೆಯಿರಿ.","Premium ಖರೀದಿಸಿದರೆ verification ಅಗತ್ಯವಿಲ್ಲ."),
 "ml":("👋 ഹായ് {mention}, {status}","ഇന്ന് നിങ്ങൾ verified അല്ല. Verify ചെയ്ത് അടുത്ത verification വരെ unlimited access നേടുക.","Premium വാങ്ങിയാൽ verification ആവശ്യമില്ല."),
 "bn":("👋 হ্যালো {mention}, {status}","আজ আপনি verified নন। Verify করে পরবর্তী verification পর্যন্ত unlimited access নিন।","Premium কিনলে verification লাগবে না।"),
 "mr":("👋 हाय {mention}, {status}","आज तुम्ही verified नाही. Verify करून पुढील verification पर्यंत unlimited access मिळवा.","Premium घेतल्यास verification ची गरज नाही."),
 "gu":("👋 હાય {mention}, {status}","આજે તમે verified નથી. Verify કરીને આગળના verification સુધી unlimited access મેળવો.","Premium ખરીદશો તો verification જરૂરી નથી."),
 "pa":("👋 ਸਤ ਸ੍ਰੀ ਅਕਾਲ {mention}, {status}","ਅੱਜ ਤੁਸੀਂ verified ਨਹੀਂ ਹੋ। Verify ਕਰਕੇ ਅਗਲੇ verification ਤੱਕ unlimited access ਲਵੋ।","Premium ਲੈਣ ਨਾਲ verification ਦੀ ਲੋੜ ਨਹੀਂ।"),
 "ur":("👋 ہیلو {mention}, {status}","آج آپ verified نہیں ہیں۔ Verify کرکے اگلی verification تک unlimited access حاصل کریں۔","Premium خریدنے پر verification کی ضرورت نہیں۔"),
 "as":("👋 হেল্ল' {mention}, {status}","আজি আপুনি verified নহয়। Verify কৰি পৰৱৰ্তী verification লৈ unlimited access লওক।","Premium কিনিলে verification নালাগে।"),
 "ne":("👋 नमस्ते {mention}, {status}","आज तपाईं verified हुनुहुन्न। Verify गरेर अर्को verification सम्म unlimited access पाउनुहोस्।","Premium किन्दा verification चाहिँदैन।"),
}
_VERIFY_TEXTS = {
"ta": {"verify2":"<b>👋 வணக்கம் {mention}, {status}\n\n📌 நீங்கள் verified இல்லை. அடுத்த verification-ஐ தொடர verification link-ஐ திறக்கவும்.\n\n#Verification: 2/3\n\n💎 Verification இல்லாமல் direct files பெற Premium வாங்குங்கள்.</b>","verify3":"<b>👋 வணக்கம் {mention},\n\n📌 இறுதி verification link-ஐ திறந்து அடுத்த முழு நாளுக்கான access பெறுங்கள்.\n\n#Verification: 3/3</b>","done":"<b>👋 வணக்கம் {mention},\n\nVerification {num} முடித்துவிட்டீர்கள் ✓\n\n<code>{duration}</code> வரை unlimited access உள்ளது.</b>"},
"te": {"verify2":"<b>👋 హాయ్ {mention}, {status}\n\n📌 మీరు verified కాలేదు. తదుపరి verification కోసం link తెరవండి.\n\n#Verification: 2/3\n\n💎 Verification లేకుండా files కోసం Premium కొనండి.</b>","verify3":"<b>👋 హాయ్ {mention},\n\n📌 చివరి verification link తెరిచి పూర్తి రోజు access పొందండి.\n\n#Verification: 3/3</b>","done":"<b>👋 హాయ్ {mention},\n\nVerification {num} పూర్తైంది ✓\n\n<code>{duration}</code> వరకు unlimited access ఉంది.</b>"},
"kn": {"verify2":"<b>👋 ನಮಸ್ಕಾರ {mention}, {status}\n\n📌 ನೀವು verified ಆಗಿಲ್ಲ. ಮುಂದಿನ verification ಗಾಗಿ link ತೆರೆಯಿರಿ.\n\n#Verification: 2/3\n\n💎 Verification ಇಲ್ಲದೆ files ಪಡೆಯಲು Premium ಖರೀದಿಸಿ.</b>","verify3":"<b>👋 ನಮಸ್ಕಾರ {mention},\n\n📌 ಕೊನೆಯ verification link ತೆರೆಯಿರಿ ಮತ್ತು ಪೂರ್ಣ ದಿನದ access ಪಡೆಯಿರಿ.\n\n#Verification: 3/3</b>","done":"<b>👋 ನಮಸ್ಕಾರ {mention},\n\nVerification {num} ಪೂರ್ಣಗೊಂಡಿದೆ ✓\n\n<code>{duration}</code> ವರೆಗೆ unlimited access ಇದೆ.</b>"},
"ml": {"verify2":"<b>👋 ഹായ് {mention}, {status}\n\n📌 നിങ്ങൾ verified അല്ല. അടുത്ത verification-നായി link തുറക്കുക.\n\n#Verification: 2/3\n\n💎 Verification ഇല്ലാതെ files ലഭിക്കാൻ Premium വാങ്ങുക.</b>","verify3":"<b>👋 ഹായ് {mention},\n\n📌 അവസാന verification link തുറന്ന് ഒരു പൂർണ്ണ ദിവസത്തേക്ക് access നേടുക.\n\n#Verification: 3/3</b>","done":"<b>👋 ഹായ് {mention},\n\nVerification {num} പൂർത്തിയായി ✓\n\n<code>{duration}</code> വരെ unlimited access ലഭിക്കും.</b>"},
"bn": {"verify2":"<b>👋 হ্যালো {mention}, {status}\n\n📌 আপনি verified নন। পরবর্তী verification-এর জন্য link খুলুন।\n\n#Verification: 2/3\n\n💎 Verification ছাড়া files পেতে Premium কিনুন।</b>","verify3":"<b>👋 হ্যালো {mention},\n\n📌 শেষ verification link খুলে পুরো এক দিনের access নিন।\n\n#Verification: 3/3</b>","done":"<b>👋 হ্যালো {mention},\n\nVerification {num} সম্পূর্ণ হয়েছে ✓\n\n<code>{duration}</code> পর্যন্ত unlimited access আছে।</b>"},
"mr": {"verify2":"<b>👋 हाय {mention}, {status}\n\n📌 तुम्ही verified नाही. पुढील verification साठी link उघडा.\n\n#Verification: 2/3\n\n💎 Verification शिवाय files साठी Premium घ्या.</b>","verify3":"<b>👋 हाय {mention},\n\n📌 शेवटची verification link उघडा आणि पूर्ण दिवसाचा access मिळवा.\n\n#Verification: 3/3</b>","done":"<b>👋 हाय {mention},\n\nVerification {num} पूर्ण झाले ✓\n\n<code>{duration}</code> पर्यंत unlimited access आहे.</b>"},
"gu": {"verify2":"<b>👋 હાય {mention}, {status}\n\n📌 તમે verified નથી. આગળના verification માટે link ખોલો.\n\n#Verification: 2/3\n\n💎 Verification વગર files માટે Premium ખરીદો.</b>","verify3":"<b>👋 હાય {mention},\n\n📌 છેલ્લી verification link ખોલીને આખા દિવસનો access મેળવો.\n\n#Verification: 3/3</b>","done":"<b>👋 હાય {mention},\n\nVerification {num} પૂર્ણ થયું ✓\n\n<code>{duration}</code> સુધી unlimited access છે.</b>"},
"pa": {"verify2":"<b>👋 ਸਤ ਸ੍ਰੀ ਅਕਾਲ {mention}, {status}\n\n📌 ਤੁਸੀਂ verified ਨਹੀਂ ਹੋ। ਅਗਲੇ verification ਲਈ link ਖੋਲ੍ਹੋ।\n\n#Verification: 2/3\n\n💎 Verification ਤੋਂ ਬਿਨਾਂ files ਲਈ Premium ਲਵੋ।</b>","verify3":"<b>👋 ਸਤ ਸ੍ਰੀ ਅਕਾਲ {mention},\n\n📌 ਆਖਰੀ verification link ਖੋਲ੍ਹ ਕੇ ਪੂਰੇ ਦਿਨ ਦਾ access ਲਵੋ।\n\n#Verification: 3/3</b>","done":"<b>👋 ਸਤ ਸ੍ਰੀ ਅਕਾਲ {mention},\n\nVerification {num} ਪੂਰਾ ਹੋ ਗਿਆ ✓\n\n<code>{duration}</code> ਤੱਕ unlimited access ਹੈ।</b>"},
"ur": {"verify2":"<b>👋 ہیلو {mention}، {status}\n\n📌 آپ verified نہیں ہیں۔ اگلی verification کے لیے link کھولیں۔\n\n#Verification: 2/3\n\n💎 Verification کے بغیر files کے لیے Premium خریدیں۔</b>","verify3":"<b>👋 ہیلو {mention}،\n\n📌 آخری verification link کھول کر پورے دن کا access حاصل کریں۔\n\n#Verification: 3/3</b>","done":"<b>👋 ہیلو {mention}،\n\nVerification {num} مکمل ہوگئی ✓\n\n<code>{duration}</code> تک unlimited access ہے۔</b>"},
"as": {"verify2":"<b>👋 হেল্ল' {mention}, {status}\n\n📌 আপুনি verified নহয়। পৰৱৰ্তী verification-ৰ বাবে link খোলক।\n\n#Verification: 2/3</b>","verify3":"<b>👋 হেল্ল' {mention},\n\n📌 শেষ verification link খুলি সম্পূৰ্ণ দিনৰ access লওক।\n\n#Verification: 3/3</b>","done":"<b>👋 হেল্ল' {mention},\n\nVerification {num} সম্পূৰ্ণ হৈছে ✓\n\n<code>{duration}</code> লৈ unlimited access আছে।</b>"},
"ne": {"verify2":"<b>👋 नमस्ते {mention}, {status}\n\n📌 तपाईं verified हुनुहुन्न। अर्को verification का लागि link खोल्नुहोस्।\n\n#Verification: 2/3</b>","verify3":"<b>👋 नमस्ते {mention},\n\n📌 अन्तिम verification link खोलेर पूरा दिनको access पाउनुहोस्।\n\n#Verification: 3/3</b>","done":"<b>👋 नमस्ते {mention},\n\nVerification {num} पूरा भयो ✓\n\n<code>{duration}</code> सम्म unlimited access छ।</b>"},
}
for _c, _vals in _VERIFY_TEXTS.items():
    VERIFY[_c].update(_vals)
for _c, _vals in _VERIFY_TEXTS.items():
    VERIFY[_c]["verify1"] = _vals["verify2"].replace("2/3", "1/3")

def verify_tr(lang, key, **values):
    text = VERIFY.get(lang, VERIFY[DEFAULT_LANGUAGE]).get(key, VERIFY[DEFAULT_LANGUAGE].get(key, key))
    return small_caps(text).format(**values)

def verify_button_tr(lang, key):
    return small_caps(VERIFY_BUTTONS.get(lang, VERIFY_BUTTONS[DEFAULT_LANGUAGE]).get(key, key))
