"""
Premium plan selection, payment screenshot intake, subscription lifecycle and
admin controls. This module is intentionally additive: it uses the bot's
existing users collection and db.has_premium_access/remove_premium_access.
"""
import asyncio
import datetime
import hashlib
import io
import logging
import re
import os
import shutil
import subprocess
import time
from html import escape

import pytz
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
import pytesseract

from info import (
    ADMINS,
    LOG_CHANNEL,
    PAYMENT_BOT_TOKEN,
    PAYMENT_BOT_USERNAME,
    PAYMENT_ADMIN_IDS,
    OWNER_USERNAME,
    PREMIUM_PLANS,
    PAYMENT_OCR_ENABLED,
    PAYMENT_MAX_DELAY_MINUTES,
    PAYMENT_FUTURE_TOLERANCE_MINUTES,
    PAYMENT_OCR_PASS_TIMEOUT,
    PAYMENT_OCR_JOB_TIMEOUT_SECONDS,
    API_ID,
    API_HASH,
)
from database.users_chats_db import db
from language import LANGUAGES as GLOBAL_LANGUAGES, get_user_language as _global_user_language, small_caps

LOGGER = logging.getLogger(__name__)

# Screenshot OCR is intentionally kept identical to the original analysis
# (all OCR variants and PSM passes remain). These limits only protect the bot
# from CPU/RAM spikes when screenshots arrive close together.
PAYMENT_OCR_MAX_CONCURRENT = max(
    1, int(os.getenv("PAYMENT_OCR_MAX_CONCURRENT", "1"))
)
_PAYMENT_OCR_SEMAPHORE = asyncio.Semaphore(PAYMENT_OCR_MAX_CONCURRENT)

# Tesseract can otherwise create several native worker threads per pass.
# Limiting native parallelism prevents one screenshot from exhausting a small
# Koyeb instance. It does not change the OCR variants or matching logic.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OMP_DYNAMIC", "FALSE")

IST = pytz.timezone("Asia/Kolkata")
UTC = datetime.timezone.utc
LIFETIME_EXPIRY = datetime.datetime(9999, 12, 31, 23, 59, 59)


def _now():
    # Existing Premium code stores naive datetimes in MongoDB. Keep the same
    # convention for compatibility, representing UTC.
    return datetime.datetime.utcnow()




def _naive_utc(value):
    if not isinstance(value, datetime.datetime):
        return value
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value

def _aware_ist(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def _fmt_dt(value):
    value = _aware_ist(value)
    return value.strftime("%d %b %Y %I:%M %p") if value else "N/A"


def _admins():
    return set(ADMINS) | set(PAYMENT_ADMIN_IDS)


TEMP_MESSAGE_DELETE_SECONDS = 300  # normal temporary bot notices remain 5 minutes unless overridden


# ---------------------------------------------------------------------------
# User-facing language system
# ---------------------------------------------------------------------------
# Admin analysis/review reports intentionally remain in English for consistent
# moderation. Only normal user-facing Premium/payment messages are localized.
LANGUAGES = GLOBAL_LANGUAGES

LANGUAGE_ALIASES = {
    "en": "en", "en-us": "en", "en-gb": "en",
    "hi": "hi", "ta": "ta", "te": "te", "kn": "kn", "ml": "ml",
    "bn": "bn", "mr": "mr", "gu": "gu", "pa": "pa", "ur": "ur",
    "as": "as", "ne": "ne", "hinglish": "hinglish", "hi-latn": "hinglish",
}

I18N = {
    "en": {
        "progress_title": "🔎 <b>Payment screenshot received</b>",
        "progress_body": "⏳ Your payment is being securely analyzed.\nThis may take <b>1–2 minutes</b>. Please do not resend the screenshot or switch to another bot.\n\n✅ You will receive the final result automatically.",
        "no_order_title": "⚠️ <b>No Premium order found</b>",
        "no_order_body": "We could not find an active Premium order linked to your account.\nPlease select a Premium plan first, complete the payment, and then send the screenshot here.\n\n🧹 This notice will disappear automatically after 10 seconds.",
        "manual_title": "⚠️ <b>Premium Activated — Payment Under Review</b>",
        "manual_body": "Your payment screenshot could not be automatically approved and has been sent to the admin for manual review. Your selected Premium plan is already active temporarily. If the payment or screenshot is found to be invalid or misleading, this access may be removed.",
        "activated": "Thank you for purchasing Premium!",
        "renewed": "Thank you for renewing Premium!",
        "approved": "Your payment has been confirmed. Your Premium access remains active.",
        "rejected": "The Premium access added for this payment has been removed. Please contact the admin if you think this is a mistake.",
        "expired": "Your Premium access has ended.\n\n🔄 Purchase a new Premium plan to continue.",
        "expiring": "Renew your Premium plan to continue using the service.",
        "language_title": "🌐 <b>Choose Your Language</b>",
        "language_body": "Select the language you want the bot to use for normal messages. You can change it anytime.",
        "language_saved": "🌐 Language updated successfully.",
        "contact": "💬 CONTACT ADMIN",
    },
    "hi": {
        "progress_title": "🔎 <b>भुगतान स्क्रीनशॉट प्राप्त हुआ</b>",
        "progress_body": "⏳ आपके भुगतान की सुरक्षित जाँच की जा रही है।\nइसमें <b>1–2 मिनट</b> लग सकते हैं। कृपया स्क्रीनशॉट दोबारा न भेजें और दूसरा बॉट न खोलें।\n\n✅ जाँच पूरी होने पर आपको परिणाम अपने आप मिल जाएगा।",
        "no_order_title": "⚠️ <b>कोई Premium Order नहीं मिला</b>",
        "no_order_body": "आपके खाते से कोई सक्रिय Premium Order जुड़ा नहीं मिला।\nकृपया पहले Premium Plan चुनें, भुगतान पूरा करें और फिर स्क्रीनशॉट भेजें।\n\n🧹 यह संदेश 10 सेकंड बाद अपने आप हट जाएगा।",
        "manual_title": "⚠️ <b>Premium सक्रिय — भुगतान जाँच में</b>",
        "manual_body": "आपका भुगतान स्क्रीनशॉट अपने आप स्वीकृत नहीं हो सका और इसे Admin की मैनुअल जाँच के लिए भेज दिया गया है। आपका चुना हुआ Premium Plan अस्थायी रूप से सक्रिय है। भुगतान गलत या भ्रामक मिलने पर यह access हटाया जा सकता है।",
        "activated": "Premium खरीदने के लिए धन्यवाद!",
        "renewed": "Premium renew करने के लिए धन्यवाद!",
        "approved": "आपका भुगतान पुष्टि हो गया है। आपका Premium access सक्रिय है।",
        "rejected": "इस भुगतान से दिया गया Premium access हटा दिया गया है। यदि आपको लगता है कि यह गलती है, तो Admin से संपर्क करें।",
        "expired": "आपका Premium access समाप्त हो गया है।\n\n🔄 जारी रखने के लिए नया Premium Plan खरीदें।",
        "expiring": "सेवा जारी रखने के लिए अपना Premium Plan renew करें।",
        "language_title": "🌐 <b>अपनी भाषा चुनें</b>",
        "language_body": "सामान्य bot messages के लिए अपनी पसंदीदा भाषा चुनें। आप इसे कभी भी बदल सकते हैं।",
        "language_saved": "🌐 भाषा सफलतापूर्वक बदल दी गई।",
        "contact": "💬 ADMIN से संपर्क करें",
    },
    "ta": {
        "progress_title": "🔎 <b>Payment Screenshot பெறப்பட்டது</b>",
        "progress_body": "⏳ உங்கள் payment பாதுகாப்பாக சரிபார்க்கப்படுகிறது.\nஇதற்கு <b>1–2 நிமிடங்கள்</b> ஆகலாம். Screenshot-ஐ மீண்டும் அனுப்ப வேண்டாம்; வேறு bot-க்கு மாற வேண்டாம்.\n\n✅ சரிபார்ப்பு முடிந்ததும் முடிவு தானாக வரும்.",
        "no_order_title": "⚠️ <b>Premium Order கிடைக்கவில்லை</b>",
        "no_order_body": "உங்கள் கணக்குடன் செயலில் உள்ள Premium Order எதுவும் இணைக்கப்படவில்லை.\nமுதலில் Premium Plan-ஐ தேர்வு செய்து payment முடித்த பிறகு screenshot அனுப்பவும்.\n\n🧹 இந்த செய்தி 10 விநாடிகளில் தானாக நீக்கப்படும்.",
        "manual_title": "⚠️ <b>Premium செயல்படுத்தப்பட்டது — Payment சரிபார்ப்பில்</b>",
        "manual_body": "உங்கள் payment screenshot தானாக approve செய்யப்படவில்லை; Admin manual review-க்கு அனுப்பப்பட்டுள்ளது. உங்கள் தேர்ந்தெடுத்த Premium Plan தற்காலிகமாக active-ஆக உள்ளது. Payment தவறானது என கண்டறியப்பட்டால் access நீக்கப்படலாம்.",
        "activated": "Premium வாங்கியதற்கு நன்றி!", "renewed": "Premium renew செய்ததற்கு நன்றி!",
        "approved": "உங்கள் payment உறுதிப்படுத்தப்பட்டது. Premium access active-ஆக உள்ளது.",
        "rejected": "இந்த payment மூலம் வழங்கப்பட்ட Premium access நீக்கப்பட்டது. தவறு என நினைத்தால் Admin-ஐ தொடர்புகொள்ளவும்.",
        "expired": "உங்கள் Premium access முடிந்துவிட்டது.\n\n🔄 தொடர புதிய Premium Plan வாங்கவும்.",
        "expiring": "சேவையைத் தொடர Premium Plan-ஐ renew செய்யவும்.",
        "language_title": "🌐 <b>உங்கள் மொழியைத் தேர்வு செய்யவும்</b>", "language_body": "Bot-ன் சாதாரண messages-க்கு விருப்பமான மொழியைத் தேர்வு செய்யவும்.", "language_saved": "🌐 மொழி வெற்றிகரமாக மாற்றப்பட்டது.", "contact": "💬 ADMIN-ஐ தொடர்புகொள்ளவும்",
    },
    "te": {
        "progress_title": "🔎 <b>Payment Screenshot అందింది</b>", "progress_body": "⏳ మీ payment సురక్షితంగా పరిశీలించబడుతోంది.\nదీనికి <b>1–2 నిమిషాలు</b> పట్టవచ్చు. Screenshot మళ్లీ పంపవద్దు మరియు మరో bot‌కి మారవద్దు.\n\n✅ పరిశీలన పూర్తయ్యాక ఫలితం ఆటోమేటిక్‌గా వస్తుంది.",
        "no_order_title": "⚠️ <b>Premium Order కనుగొనబడలేదు</b>", "no_order_body": "మీ ఖాతాతో active Premium Order ఏదీ కనుగొనబడలేదు.\nముందుగా Premium Plan ఎంచుకుని payment పూర్తి చేసి, తర్వాత screenshot పంపండి.\n\n🧹 ఈ సందేశం 5 నిమిషాల్లో ఆటోమేటిక్‌గా తొలగించబడుతుంది.",
        "manual_title": "⚠️ <b>Premium యాక్టివ్ — Payment Reviewలో ఉంది</b>", "manual_body": "మీ payment screenshot ఆటోమేటిక్‌గా approve కాలేదు; Admin manual reviewకి పంపబడింది. మీరు ఎంచుకున్న Premium Plan తాత్కాలికంగా active‌లో ఉంది. Payment తప్పుగా ఉంటే access తొలగించబడవచ్చు.",
        "activated": "Premium కొనుగోలు చేసినందుకు ధన్యవాదాలు!", "renewed": "Premium renew చేసినందుకు ధన్యవాదాలు!", "approved": "మీ payment నిర్ధారించబడింది. Premium access active‌లో ఉంది.", "rejected": "ఈ payment ద్వారా ఇచ్చిన Premium access తొలగించబడింది. ఇది పొరపాటు అనుకుంటే Admin‌ను సంప్రదించండి.", "expired": "మీ Premium access ముగిసింది.\n\n🔄 కొనసాగడానికి కొత్త Premium Plan కొనండి.", "expiring": "సేవను కొనసాగించడానికి మీ Premium Plan‌ను renew చేయండి.", "language_title": "🌐 <b>మీ భాషను ఎంచుకోండి</b>", "language_body": "సాధారణ bot messages కోసం మీ భాషను ఎంచుకోండి.", "language_saved": "🌐 భాష విజయవంతంగా మార్చబడింది.", "contact": "💬 ADMIN‌ను సంప్రదించండి",
    },
    "kn": {
        "progress_title": "🔎 <b>Payment Screenshot ಸ್ವೀಕರಿಸಲಾಗಿದೆ</b>", "progress_body": "⏳ ನಿಮ್ಮ payment ಅನ್ನು ಸುರಕ್ಷಿತವಾಗಿ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ.\nಇದಕ್ಕೆ <b>1–2 ನಿಮಿಷಗಳು</b> ಬೇಕಾಗಬಹುದು. Screenshot ಅನ್ನು ಮತ್ತೆ ಕಳುಹಿಸಬೇಡಿ ಮತ್ತು ಬೇರೆ bot ಗೆ ಬದಲಾಯಿಸಬೇಡಿ.\n\n✅ ಪರಿಶೀಲನೆ ಮುಗಿದ ನಂತರ ಫಲಿತಾಂಶ ಸ್ವಯಂಚಾಲಿತವಾಗಿ ಬರುತ್ತದೆ.",
        "no_order_title": "⚠️ <b>Premium Order ಕಂಡುಬಂದಿಲ್ಲ</b>", "no_order_body": "ನಿಮ್ಮ ಖಾತೆಗೆ ಯಾವುದೇ active Premium Order ಕಂಡುಬಂದಿಲ್ಲ.\nಮೊದಲು Premium Plan ಆಯ್ಕೆ ಮಾಡಿ, payment ಪೂರ್ಣಗೊಳಿಸಿ, ನಂತರ screenshot ಕಳುಹಿಸಿ.\n\n🧹 ಈ ಸಂದೇಶ 10 ಸೆಕೆಂಡುಗಳ ನಂತರ ಸ್ವಯಂಚಾಲಿತವಾಗಿ ಅಳಿಸಲಾಗುತ್ತದೆ.",
        "manual_title": "⚠️ <b>Premium ಸಕ್ರಿಯ — Payment ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ</b>", "manual_body": "ನಿಮ್ಮ payment screenshot ಸ್ವಯಂಚಾಲಿತವಾಗಿ approve ಆಗಲಿಲ್ಲ; Admin manual review ಗೆ ಕಳುಹಿಸಲಾಗಿದೆ. ನೀವು ಆಯ್ಕೆ ಮಾಡಿದ Premium Plan ತಾತ್ಕಾಲಿಕವಾಗಿ active ಆಗಿದೆ. Payment ತಪ್ಪಾಗಿದೆ ಎಂದು ಕಂಡುಬಂದರೆ access ತೆಗೆದುಹಾಕಬಹುದು.",
        "activated": "Premium ಖರೀದಿಸಿದ್ದಕ್ಕಾಗಿ ಧನ್ಯವಾದಗಳು!", "renewed": "Premium renew ಮಾಡಿದ್ದಕ್ಕಾಗಿ ಧನ್ಯವಾದಗಳು!", "approved": "ನಿಮ್ಮ payment ದೃಢೀಕರಿಸಲಾಗಿದೆ. Premium access active ಆಗಿದೆ.", "rejected": "ಈ payment ಮೂಲಕ ನೀಡಿದ Premium access ತೆಗೆದುಹಾಕಲಾಗಿದೆ. ಇದು ತಪ್ಪು ಎಂದು ಭಾವಿಸಿದರೆ Admin ಅನ್ನು ಸಂಪರ್ಕಿಸಿ.", "expired": "ನಿಮ್ಮ Premium access ಮುಗಿದಿದೆ.\n\n🔄 ಮುಂದುವರಿಸಲು ಹೊಸ Premium Plan ಖರೀದಿಸಿ.", "expiring": "ಸೇವೆಯನ್ನು ಮುಂದುವರಿಸಲು Premium Plan renew ಮಾಡಿ.", "language_title": "🌐 <b>ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ</b>", "language_body": "ಸಾಮಾನ್ಯ bot messages ಗಾಗಿ ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ.", "language_saved": "🌐 ಭಾಷೆ ಯಶಸ್ವಿಯಾಗಿ ಬದಲಾಯಿಸಲಾಗಿದೆ.", "contact": "💬 ADMIN ಸಂಪರ್ಕಿಸಿ",
    },
    "ml": {
        "progress_title": "🔎 <b>Payment Screenshot ലഭിച്ചു</b>", "progress_body": "⏳ നിങ്ങളുടെ payment സുരക്ഷിതമായി പരിശോധിക്കുന്നു.\nഇതിന് <b>1–2 മിനിറ്റ്</b> വരെ എടുക്കാം. Screenshot വീണ്ടും അയയ്ക്കരുത്; മറ്റൊരു bot-ലേക്ക് മാറരുത്.\n\n✅ പരിശോധന പൂർത്തിയായാൽ ഫലം സ്വയമേവ ലഭിക്കും.",
        "no_order_title": "⚠️ <b>Premium Order കണ്ടെത്താനായില്ല</b>", "no_order_body": "നിങ്ങളുടെ അക്കൗണ്ടുമായി ബന്ധിപ്പിച്ച active Premium Order കണ്ടെത്താനായില്ല.\nആദ്യം Premium Plan തിരഞ്ഞെടുക്കുക, payment പൂർത്തിയാക്കി ശേഷം screenshot അയയ്ക്കുക.\n\n🧹 ഈ സന്ദേശം 10 സെക്കൻഡിന് ശേഷം സ്വയമേവ ഇല്ലാതാകും.",
        "manual_title": "⚠️ <b>Premium സജീവമാക്കി — Payment പരിശോധനയിൽ</b>", "manual_body": "നിങ്ങളുടെ payment screenshot സ്വയമേവ approve ചെയ്യാനായില്ല; Admin manual review-ലേക്ക് അയച്ചു. നിങ്ങൾ തിരഞ്ഞെടുത്ത Premium Plan താൽക്കാലികമായി active ആണ്. Payment തെറ്റാണെന്ന് കണ്ടെത്തിയാൽ access നീക്കം ചെയ്യാം.",
        "activated": "Premium വാങ്ങിയതിന് നന്ദി!", "renewed": "Premium renew ചെയ്തതിന് നന്ദി!", "approved": "നിങ്ങളുടെ payment സ്ഥിരീകരിച്ചു. Premium access active ആണ്.", "rejected": "ഈ payment വഴി നൽകിയ Premium access നീക്കം ചെയ്തു. തെറ്റാണെന്ന് തോന്നുന്നുവെങ്കിൽ Admin-നെ ബന്ധപ്പെടുക.", "expired": "നിങ്ങളുടെ Premium access അവസാനിച്ചു.\n\n🔄 തുടരാൻ പുതിയ Premium Plan വാങ്ങുക.", "expiring": "സേവനം തുടരാൻ Premium Plan renew ചെയ്യുക.", "language_title": "🌐 <b>നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക</b>", "language_body": "സാധാരണ bot messages-നായി നിങ്ങളുടെ ഇഷ്ടഭാഷ തിരഞ്ഞെടുക്കുക.", "language_saved": "🌐 ഭാഷ വിജയകരമായി മാറ്റി.", "contact": "💬 ADMIN-നെ ബന്ധപ്പെടുക",
    },
}
# Missing regional languages safely fall back to polished English instead of
# showing broken/partial translations.
for _code in ("bn", "mr", "gu", "pa", "ur"):
    I18N[_code] = I18N["en"]

# Complete the remaining Indian-language payment messages too (rather than
# silently falling back to English).
I18N.update({
    "bn": {
        "progress_title":"🔎 <b>পেমেন্ট স্ক্রিনশট পাওয়া গেছে</b>", "progress_body":"⏳ আপনার পেমেন্ট নিরাপদভাবে যাচাই করা হচ্ছে। এতে <b>১–২ মিনিট</b> লাগতে পারে। স্ক্রিনশট আবার পাঠাবেন না বা অন্য বটে যাবেন না।\n\n✅ যাচাই শেষ হলে ফলাফল স্বয়ংক্রিয়ভাবে পাবেন।", "no_order_title":"⚠️ <b>কোনও Premium Order পাওয়া যায়নি</b>", "no_order_body":"আপনার অ্যাকাউন্টের সঙ্গে কোনও Premium Order পাওয়া যায়নি।\nআগে একটি Premium Plan বেছে নিয়ে payment সম্পূর্ণ করুন, তারপর এখানে screenshot পাঠান।\n\n🧹 এই বার্তাটি ১০ সেকেন্ড পরে নিজে থেকে মুছে যাবে।", "manual_title":"⚠️ <b>Premium সক্রিয় — Payment যাচাই চলছে</b>", "manual_body":"আপনার payment screenshot স্বয়ংক্রিয়ভাবে approve হয়নি এবং Admin manual review-এর জন্য পাঠানো হয়েছে। আপনার নির্বাচিত Premium Plan সাময়িকভাবে active আছে। Payment ভুল হলে access সরিয়ে দেওয়া হতে পারে।", "activated":"Premium কেনার জন্য ধন্যবাদ!", "renewed":"Premium renew করার জন্য ধন্যবাদ!", "approved":"আপনার payment নিশ্চিত হয়েছে। Premium access active আছে।", "rejected":"এই payment-এর জন্য দেওয়া Premium access সরিয়ে দেওয়া হয়েছে। ভুল মনে হলে Admin-এর সঙ্গে যোগাযোগ করুন।", "expired":"আপনার Premium access শেষ হয়েছে।\n\n🔄 চালিয়ে যেতে নতুন Premium Plan কিনুন।", "expiring":"সেবা চালিয়ে যেতে আপনার Premium Plan renew করুন।", "language_title":"🌐 <b>আপনার ভাষা বেছে নিন</b>", "language_body":"সাধারণ bot messages-এর জন্য আপনার পছন্দের ভাষা বেছে নিন। চাইলে পরে পরিবর্তন করতে পারবেন।", "language_saved":"🌐 ভাষা সফলভাবে পরিবর্তন হয়েছে।", "contact":"💬 ADMIN-এর সঙ্গে যোগাযোগ করুন"
    },
    "mr": {
        "progress_title":"🔎 <b>पेमेंट स्क्रीनशॉट मिळाला</b>", "progress_body":"⏳ तुमचे payment सुरक्षितपणे तपासले जात आहे. यासाठी <b>१–२ मिनिटे</b> लागू शकतात. Screenshot पुन्हा पाठवू नका किंवा दुसऱ्या bot वर जाऊ नका.\n\n✅ तपासणी पूर्ण झाल्यावर निकाल आपोआप मिळेल.", "no_order_title":"⚠️ <b>Premium Order सापडला नाही</b>", "no_order_body":"तुमच्या खात्याशी जोडलेला Premium Order सापडला नाही.\nआधी Premium Plan निवडा, payment पूर्ण करा आणि नंतर screenshot पाठवा.\n\n🧹 हा संदेश ५ मिनिटांनी आपोआप हटेल.", "manual_title":"⚠️ <b>Premium सक्रिय — Payment तपासणीमध्ये</b>", "manual_body":"तुमचा payment screenshot आपोआप approve झाला नाही आणि Admin manual review साठी पाठवला आहे. निवडलेला Premium Plan तात्पुरता active आहे. Payment चुकीचा आढळल्यास access काढला जाऊ शकतो.", "activated":"Premium खरेदी केल्याबद्दल धन्यवाद!", "renewed":"Premium renew केल्याबद्दल धन्यवाद!", "approved":"तुमचे payment निश्चित झाले आहे. Premium access active आहे.", "rejected":"या payment साठी दिलेले Premium access काढले आहे. चूक वाटत असल्यास Admin शी संपर्क करा.", "expired":"तुमचा Premium access संपला आहे.\n\n🔄 सुरू ठेवण्यासाठी नवीन Premium Plan खरेदी करा.", "expiring":"सेवा सुरू ठेवण्यासाठी Premium Plan renew करा.", "language_title":"🌐 <b>तुमची भाषा निवडा</b>", "language_body":"सामान्य bot messages साठी तुमची आवडती भाषा निवडा. तुम्ही ती कधीही बदलू शकता.", "language_saved":"🌐 भाषा यशस्वीपणे बदलली.", "contact":"💬 ADMIN शी संपर्क करा"
    },
    "gu": {
        "progress_title":"🔎 <b>પેમેન્ટ સ્ક્રીનશોટ મળ્યો</b>", "progress_body":"⏳ તમારું payment સુરક્ષિત રીતે તપાસવામાં આવી રહ્યું છે. તેમાં <b>1–2 મિનિટ</b> લાગી શકે છે. Screenshot ફરી મોકલશો નહીં અને બીજા bot પર ન જશો.\n\n✅ તપાસ પૂર્ણ થયા પછી પરિણામ આપમેળે મળશે.", "no_order_title":"⚠️ <b>Premium Order મળ્યો નથી</b>", "no_order_body":"તમારા ખાતા સાથે કોઈ Premium Order મળ્યો નથી.\nપહેલા Premium Plan પસંદ કરો, payment પૂર્ણ કરો અને પછી screenshot મોકલો.\n\n🧹 આ સંદેશ 10 સેકન્ડમાં આપમેળે દૂર થશે.", "manual_title":"⚠️ <b>Premium સક્રિય — Payment તપાસમાં</b>", "manual_body":"તમારો payment screenshot આપમેળે approve થયો નથી અને Admin manual review માટે મોકલાયો છે. પસંદ કરેલો Premium Plan તાત્કાલિક active છે. Payment ખોટું હોય તો access દૂર થઈ શકે છે.", "activated":"Premium ખરીદવા બદલ આભાર!", "renewed":"Premium renew કરવા બદલ આભાર!", "approved":"તમારું payment પુષ્ટિ થયું છે. Premium access active છે.", "rejected":"આ payment માટે આપવામાં આવેલ Premium access દૂર કરવામાં આવ્યું છે. ભૂલ લાગે તો Admin નો સંપર્ક કરો.", "expired":"તમારું Premium access સમાપ્ત થયું છે.\n\n🔄 ચાલુ રાખવા માટે નવો Premium Plan ખરીદો.", "expiring":"સેવા ચાલુ રાખવા માટે Premium Plan renew કરો.", "language_title":"🌐 <b>તમારી ભાષા પસંદ કરો</b>", "language_body":"સામાન્ય bot messages માટે તમારી ભાષા પસંદ કરો. તમે તેને ક્યારે પણ બદલી શકો છો.", "language_saved":"🌐 ભાષા સફળતાપૂર્વક બદલાઈ.", "contact":"💬 ADMIN નો સંપર્ક કરો"
    },
    "pa": {
        "progress_title":"🔎 <b>ਪੇਮੈਂਟ ਸਕ੍ਰੀਨਸ਼ਾਟ ਮਿਲ ਗਿਆ</b>", "progress_body":"⏳ ਤੁਹਾਡੀ payment ਸੁਰੱਖਿਅਤ ਤਰੀਕੇ ਨਾਲ ਜਾਂਚੀ ਜਾ ਰਹੀ ਹੈ। ਇਸ ਵਿੱਚ <b>1–2 ਮਿੰਟ</b> ਲੱਗ ਸਕਦੇ ਹਨ। Screenshot ਦੁਬਾਰਾ ਨਾ ਭੇਜੋ ਅਤੇ ਕਿਸੇ ਹੋਰ bot ਤੇ ਨਾ ਜਾਓ।\n\n✅ ਜਾਂਚ ਪੂਰੀ ਹੋਣ ਤੇ ਨਤੀਜਾ ਆਪਣੇ ਆਪ ਮਿਲੇਗਾ।", "no_order_title":"⚠️ <b>Premium Order ਨਹੀਂ ਮਿਲਿਆ</b>", "no_order_body":"ਤੁਹਾਡੇ ਖਾਤੇ ਨਾਲ ਕੋਈ Premium Order ਨਹੀਂ ਮਿਲਿਆ।\nਪਹਿਲਾਂ Premium Plan ਚੁਣੋ, payment ਪੂਰੀ ਕਰੋ ਅਤੇ ਫਿਰ screenshot ਭੇਜੋ।\n\n🧹 ਇਹ ਸੁਨੇਹਾ 10 ਸਕਿੰਟ ਬਾਅਦ ਆਪਣੇ ਆਪ ਮਿਟ ਜਾਵੇਗਾ।", "manual_title":"⚠️ <b>Premium ਸਰਗਰਮ — Payment ਜਾਂਚ ਵਿੱਚ</b>", "manual_body":"ਤੁਹਾਡਾ payment screenshot ਆਪਣੇ ਆਪ approve ਨਹੀਂ ਹੋਇਆ ਅਤੇ Admin manual review ਲਈ ਭੇਜਿਆ ਗਿਆ ਹੈ। ਚੁਣਿਆ Premium Plan ਅਸਥਾਈ ਤੌਰ ਤੇ active ਹੈ। Payment ਗਲਤ ਹੋਣ ਤੇ access ਹਟਾਇਆ ਜਾ ਸਕਦਾ ਹੈ।", "activated":"Premium ਖਰੀਦਣ ਲਈ ਧੰਨਵਾਦ!", "renewed":"Premium renew ਕਰਨ ਲਈ ਧੰਨਵਾਦ!", "approved":"ਤੁਹਾਡੀ payment ਦੀ ਪੁਸ਼ਟੀ ਹੋ ਗਈ ਹੈ। Premium access active ਹੈ.", "rejected":"ਇਸ payment ਲਈ ਦਿੱਤਾ Premium access ਹਟਾ ਦਿੱਤਾ ਗਿਆ ਹੈ। ਗਲਤੀ ਲੱਗੇ ਤਾਂ Admin ਨਾਲ ਸੰਪਰਕ ਕਰੋ।", "expired":"ਤੁਹਾਡਾ Premium access ਖਤਮ ਹੋ ਗਿਆ ਹੈ।\n\n🔄 ਜਾਰੀ ਰੱਖਣ ਲਈ ਨਵਾਂ Premium Plan ਖਰੀਦੋ।", "expiring":"ਸੇਵਾ ਜਾਰੀ ਰੱਖਣ ਲਈ Premium Plan renew ਕਰੋ।", "language_title":"🌐 <b>ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ</b>", "language_body":"ਆਮ bot messages ਲਈ ਆਪਣੀ ਪਸੰਦ ਦੀ ਭਾਸ਼ਾ ਚੁਣੋ। ਤੁਸੀਂ ਇਸਨੂੰ ਕਦੇ ਵੀ ਬਦਲ ਸਕਦੇ ਹੋ।", "language_saved":"🌐 ਭਾਸ਼ਾ ਸਫਲਤਾਪੂਰਵਕ ਬਦਲ ਗਈ।", "contact":"💬 ADMIN ਨਾਲ ਸੰਪਰਕ ਕਰੋ"
    },
    "ur": {
        "progress_title":"🔎 <b>ادائیگی کا اسکرین شاٹ موصول ہوا</b>", "progress_body":"⏳ آپ کی payment محفوظ طریقے سے چیک کی جا رہی ہے۔ اس میں <b>1–2 منٹ</b> لگ سکتے ہیں۔ Screenshot دوبارہ نہ بھیجیں اور دوسرے bot پر نہ جائیں۔\n\n✅ چیک مکمل ہونے پر نتیجہ خودکار طور پر مل جائے گا۔", "no_order_title":"⚠️ <b>Premium Order نہیں ملا</b>", "no_order_body":"آپ کے اکاؤنٹ کے ساتھ کوئی Premium Order نہیں ملا۔\nپہلے Premium Plan منتخب کریں، payment مکمل کریں اور پھر screenshot بھیجیں۔\n\n🧹 یہ پیغام 10 سیکنڈ بعد خودکار طور پر حذف ہو جائے گا۔", "manual_title":"⚠️ <b>Premium فعال — Payment جانچ میں</b>", "manual_body":"آپ کا payment screenshot خودکار طور پر approve نہیں ہوا اور Admin manual review کے لیے بھیج دیا گیا ہے۔ منتخب Premium Plan عارضی طور پر active ہے۔ Payment غلط ہونے پر access ہٹایا جا سکتا ہے۔", "activated":"Premium خریدنے کا شکریہ!", "renewed":"Premium renew کرنے کا شکریہ!", "approved":"آپ کی payment کی تصدیق ہو گئی ہے۔ Premium access active ہے۔", "rejected":"اس payment کے لیے دیا گیا Premium access ہٹا دیا گیا ہے۔ غلطی لگے تو Admin سے رابطہ کریں۔", "expired":"آپ کا Premium access ختم ہو گیا ہے۔\n\n🔄 جاری رکھنے کے لیے نیا Premium Plan خریدیں۔", "expiring":"سروس جاری رکھنے کے لیے Premium Plan renew کریں۔", "language_title":"🌐 <b>اپنی زبان منتخب کریں</b>", "language_body":"عام bot messages کے لیے اپنی پسند کی زبان منتخب کریں۔ آپ اسے کبھی بھی تبدیل کر سکتے ہیں۔", "language_saved":"🌐 زبان کامیابی سے تبدیل ہو گئی۔", "contact":"💬 ADMIN سے رابطہ کریں"
    },
})

# Full payment-flow translations for the additional languages.
I18N.update({
    "as": {
        "progress_title": "🔎 <b>Payment screenshot পোৱা গৈছে</b>", "progress_body": "⏳ আপোনাৰ payment সুৰক্ষিতভাৱে পৰীক্ষা কৰা হৈছে। ইয়াত <b>1–2 মিনিট</b> লাগিব পাৰে। Screenshot পুনৰ নপঠিয়াব আৰু আন bot লৈ নাযাব।\n\n✅ পৰীক্ষা সম্পূৰ্ণ হ'লে ফলাফল স্বয়ংক্ৰিয়ভাৱে পাব।",
        "no_order_title": "⚠️ <b>Premium Order পোৱা নগ'ল</b>", "no_order_body": "আপোনাৰ account-ৰ সৈতে কোনো active Premium Order পোৱা নগ'ল।\nপ্ৰথমে এটা Premium Plan বাছক, payment সম্পূৰ্ণ কৰক আৰু তাৰ পিছত screenshot পঠিয়াওক।\n\n🧹 এই notice 5 মিনিটৰ পিছত নিজে আঁতৰি যাব।",
        "manual_title": "⚠️ <b>Premium সক্ৰিয় — Payment পৰীক্ষাধীন</b>", "manual_body": "আপোনাৰ payment screenshot auto-approve হোৱা নাই আৰু Admin manual review-লৈ পঠিওৱা হৈছে। আপোনাৰ বাছনি কৰা Premium Plan সাময়িকভাৱে active আছে। Payment ভুল বা misleading পোৱা গ'লে access আঁতৰাব পাৰে।",
        "activated": "Premium ক্ৰয় কৰাৰ বাবে ধন্যবাদ!", "renewed": "Premium renew কৰাৰ বাবে ধন্যবাদ!", "approved": "আপোনাৰ payment নিশ্চিত কৰা হৈছে। Premium access active আছে।", "rejected": "এই payment-ৰ বাবে দিয়া Premium access আঁতৰোৱা হৈছে। ভুল বুলি ভাবিলে Admin-ৰ সৈতে যোগাযোগ কৰক।", "expired": "আপোনাৰ Premium access শেষ হৈছে।\n\n🔄 আগবঢ়িবলৈ নতুন Premium Plan ক্ৰয় কৰক।", "expiring": "সেৱা চলাই নিবলৈ আপোনাৰ Premium Plan renew কৰক।", "language_title": "🌐 <b>আপোনাৰ ভাষা বাছক</b>", "language_body": "সাধাৰণ bot messages-ৰ বাবে আপোনাৰ ভাষা বাছক। আপুনি পিছতো সলনি কৰিব পাৰে।", "language_saved": "🌐 ভাষা সফলভাৱে সলনি কৰা হৈছে।", "contact": "💬 ADMIN-ৰ সৈতে যোগাযোগ কৰক"
    },
    "ne": {
        "progress_title": "🔎 <b>Payment screenshot प्राप्त भयो</b>", "progress_body": "⏳ तपाईंको payment सुरक्षित रूपमा जाँच भइरहेको छ। यसमा <b>1–2 मिनेट</b> लाग्न सक्छ। Screenshot फेरि नपठाउनुहोस् र अर्को bot मा नजानुहोस्।\n\n✅ जाँच पूरा भएपछि परिणाम आफैं प्राप्त हुनेछ।",
        "no_order_title": "⚠️ <b>Premium Order भेटिएन</b>", "no_order_body": "तपाईंको account सँग जोडिएको active Premium Order भेटिएन।\nपहिले Premium Plan छान्नुहोस्, payment पूरा गर्नुहोस् र त्यसपछि screenshot पठाउनुहोस्।\n\n🧹 यो notice 5 मिनेटपछि आफैं हट्नेछ।",
        "manual_title": "⚠️ <b>Premium सक्रिय — Payment Review मा</b>", "manual_body": "तपाईंको payment screenshot स्वतः approve हुन सकेन र Admin manual review का लागि पठाइएको छ। तपाईंले छानेको Premium Plan अस्थायी रूपमा active छ। Payment गलत वा misleading भए access हटाउन सकिन्छ।",
        "activated": "Premium किन्नुभएकोमा धन्यवाद!", "renewed": "Premium renew गर्नुभएकोमा धन्यवाद!", "approved": "तपाईंको payment पुष्टि भयो। Premium access active छ।", "rejected": "यस payment का लागि दिइएको Premium access हटाइएको छ। गल्ती हो जस्तो लागेमा Admin लाई सम्पर्क गर्नुहोस्।", "expired": "तपाईंको Premium access सकिएको छ।\n\n🔄 जारी राख्न नयाँ Premium Plan किन्नुहोस्।", "expiring": "सेवा जारी राख्न आफ्नो Premium Plan renew गर्नुहोस्।", "language_title": "🌐 <b>आफ्नो भाषा छान्नुहोस्</b>", "language_body": "सामान्य bot messages का लागि आफ्नो भाषा छान्नुहोस्। तपाईं यसलाई पछि पनि बदल्न सक्नुहुन्छ।", "language_saved": "🌐 भाषा सफलतापूर्वक बदलियो।", "contact": "💬 ADMIN लाई सम्पर्क गर्नुहोस्"
    },
    "hinglish": {
        "progress_title": "🔎 <b>Payment Screenshot Mil Gaya</b>", "progress_body": "⏳ Aapka payment securely check ho raha hai. Isme <b>1–2 minutes</b> lag sakte hain. Screenshot dobara mat bhejo aur kisi aur bot par mat jao.\n\n✅ Check complete hote hi final result automatically mil jayega.",
        "no_order_title": "⚠️ <b>Premium Order Nahi Mila</b>", "no_order_body": "Aapke account se koi active Premium Order linked nahi mila.\nPehle Premium Plan choose karo, payment complete karo aur phir screenshot yahan bhejo.\n\n🧹 Ye notice 10 seconds baad automatically delete ho jayega.",
        "manual_title": "⚠️ <b>Premium Activated — Payment Review Mein</b>", "manual_body": "Aapka payment screenshot automatically approve nahi ho saka aur Admin manual review ke liye bheja gaya hai. Aapka selected Premium Plan temporary active hai. Agar payment galat ya misleading mila, to access remove kiya ja sakta hai.",
        "activated": "Premium purchase karne ke liye thank you!", "renewed": "Premium renew karne ke liye thank you!", "approved": "Aapka payment confirm ho gaya hai. Premium access active hai.", "rejected": "Is payment se diya gaya Premium access remove kar diya gaya hai. Agar aapko lagta hai ye mistake hai, Admin se contact karo.", "expired": "Aapka Premium access khatam ho gaya hai.\n\n🔄 Continue karne ke liye naya Premium Plan purchase karo.", "expiring": "Service continue karne ke liye apna Premium Plan renew karo.", "language_title": "🌐 <b>Apni Language Choose Karo</b>", "language_body": "Normal bot messages ke liye apni language choose karo. Aap ise kabhi bhi change kar sakte ho.", "language_saved": "🌐 Language successfully update ho gayi.", "contact": "💬 ADMIN Se Contact Karo"
    },
})

# Clear language-control labels so users understand exactly what this button does.
I18N.update({
    "en": {"language_button": "🌐 CHOOSE / CHANGE LANGUAGE", "language_first_guide": "First, choose your language. After you choose it, all Premium and payment messages for your account will use that language."},
    "hi": {"language_button": "🌐 भाषा चुनें / बदलें", "language_first_guide": "पहले अपनी भाषा चुनें। भाषा चुनने के बाद आपके सभी Premium और payment messages इसी भाषा में दिखेंगे।"},
    "ta": {"language_button": "🌐 மொழியைத் தேர்வு / மாற்ற", "language_first_guide": "முதலில் உங்கள் மொழியைத் தேர்வு செய்யவும். அதன் பிறகு உங்கள் Premium மற்றும் payment messages அனைத்தும் அந்த மொழியில் காட்டப்படும்."},
    "te": {"language_button": "🌐 భాషను ఎంచుకోండి / మార్చండి", "language_first_guide": "ముందుగా మీ భాషను ఎంచుకోండి. ఆ తర్వాత మీ Premium మరియు payment messages అన్నీ అదే భాషలో కనిపిస్తాయి."},
    "kn": {"language_button": "🌐 ಭಾಷೆ ಆಯ್ಕೆ / ಬದಲಿಸಿ", "language_first_guide": "ಮೊದಲು ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ. ನಂತರ ನಿಮ್ಮ ಎಲ್ಲಾ Premium ಮತ್ತು payment messages ಅದೇ ಭಾಷೆಯಲ್ಲಿ ಕಾಣಿಸುತ್ತವೆ."},
    "ml": {"language_button": "🌐 ഭാഷ തിരഞ്ഞെടുക്കുക / മാറ്റുക", "language_first_guide": "ആദ്യം നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക. അതിന് ശേഷം നിങ്ങളുടെ എല്ലാ Premium, payment messages അതേ ഭാഷയിൽ കാണിക്കും."},
    "bn": {"language_button": "🌐 ভাষা বাছুন / বদলান", "language_first_guide": "প্রথমে আপনার ভাষা বেছে নিন। এরপর আপনার সব Premium ও payment message সেই ভাষাতেই দেখানো হবে।"},
    "mr": {"language_button": "🌐 भाषा निवडा / बदला", "language_first_guide": "आधी तुमची भाषा निवडा. त्यानंतर तुमचे सर्व Premium आणि payment messages त्याच भाषेत दिसतील."},
    "gu": {"language_button": "🌐 ભાષા પસંદ / બદલો", "language_first_guide": "સૌપ્રથમ તમારી ભાષા પસંદ કરો. ત્યાર પછી તમારા બધા Premium અને payment messages એ જ ભાષામાં દેખાશે."},
    "pa": {"language_button": "🌐 ਭਾਸ਼ਾ ਚੁਣੋ / ਬਦਲੋ", "language_first_guide": "ਪਹਿਲਾਂ ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ। ਇਸ ਤੋਂ ਬਾਅਦ ਤੁਹਾਡੇ ਸਾਰੇ Premium ਅਤੇ payment messages ਉਸੇ ਭਾਸ਼ਾ ਵਿੱਚ ਦਿਖਣਗੇ।"},
    "ur": {"language_button": "🌐 زبان منتخب / تبدیل کریں", "language_first_guide": "سب سے پہلے اپنی زبان منتخب کریں۔ اس کے بعد آپ کے تمام Premium اور payment messages اسی زبان میں دکھائے جائیں گے۔"},
    "as": {"language_button": "🌐 ভাষা বাছক / সলনি কৰক", "language_first_guide": "প্ৰথমে আপোনাৰ ভাষা বাছক। তাৰ পিছত আপোনাৰ সকলো Premium আৰু payment message সেই ভাষাতেই দেখুওৱা হ'ব।"},
    "ne": {"language_button": "🌐 भाषा छान्नुहोस् / बदल्नुहोस्", "language_first_guide": "पहिले आफ्नो भाषा छान्नुहोस्। त्यसपछि तपाईंका सबै Premium र payment messages यही भाषामा देखाइनेछन्।"},
    "hinglish": {"language_button": "🌐 Language Choose / Change Karo", "language_first_guide": "Pehle apni language choose karo. Uske baad aapke saare Premium aur payment messages isi language mein dikhenge."},
})

# Complete user-facing payment result templates. These are used for every
# selected language so headings/labels do not fall back to English.

for _lang_key, _lang_data in I18N.items():
    _lang_data.setdefault("processing_error", "⚠️ Your screenshot was received, but processing failed temporarily. Please contact the admin.")

_RESULT_I18N = {
    "en": {"activated_title":"Premium Activated Successfully!","renewed_title":"Premium Renewed Successfully!","plan":"Plan","duration":"Duration","added":"Added","activated_at":"Activated","expires":"Expires","new_expiry":"New Expiry","status":"Status: Active","thanks_purchase":"Thank you for purchasing Premium!","thanks_renew":"Thank you for renewing Premium!","rejected_title":"Payment Rejected","rejected_body":"The Premium access added for this payment has been removed. Please contact the admin if you think this is a mistake."},
    "hi": {"activated_title":"Premium सफलतापूर्वक सक्रिय हुआ!","renewed_title":"Premium सफलतापूर्वक Renew हुआ!","plan":"प्लान","duration":"अवधि","added":"जोड़ा गया","activated_at":"सक्रिय किया गया","expires":"समाप्ति","new_expiry":"नई समाप्ति","status":"स्थिति: सक्रिय","thanks_purchase":"Premium खरीदने के लिए धन्यवाद!","thanks_renew":"Premium renew करने के लिए धन्यवाद!","rejected_title":"Payment अस्वीकार किया गया","rejected_body":"इस payment से दिया गया Premium access हटा दिया गया है। गलती लगने पर Admin से संपर्क करें।"},
    "ta": {"activated_title":"Premium வெற்றிகரமாக செயல்படுத்தப்பட்டது!","renewed_title":"Premium வெற்றிகரமாக Renew செய்யப்பட்டது!","plan":"Plan","duration":"காலம்","added":"சேர்க்கப்பட்டது","activated_at":"செயல்படுத்தப்பட்டது","expires":"காலாவதி","new_expiry":"புதிய காலாவதி","status":"நிலை: Active","thanks_purchase":"Premium வாங்கியதற்கு நன்றி!","thanks_renew":"Premium renew செய்ததற்கு நன்றி!","rejected_title":"Payment நிராகரிக்கப்பட்டது","rejected_body":"இந்த payment மூலம் வழங்கப்பட்ட Premium access நீக்கப்பட்டது. தவறு என நினைத்தால் Admin-ஐ தொடர்புகொள்ளவும்."},
    "te": {"activated_title":"Premium విజయవంతంగా యాక్టివేట్ అయింది!","renewed_title":"Premium విజయవంతంగా Renew అయింది!","plan":"Plan","duration":"వ్యవధి","added":"జోడించబడింది","activated_at":"యాక్టివేట్ చేసిన సమయం","expires":"గడువు","new_expiry":"కొత్త గడువు","status":"స్థితి: Active","thanks_purchase":"Premium కొనుగోలు చేసినందుకు ధన్యవాదాలు!","thanks_renew":"Premium renew చేసినందుకు ధన్యవాదాలు!","rejected_title":"Payment తిరస్కరించబడింది","rejected_body":"ఈ payment ద్వారా ఇచ్చిన Premium access తొలగించబడింది. ఇది పొరపాటు అనుకుంటే Admin‌ను సంప్రదించండి."},
    "kn": {"activated_title":"Premium ಯಶಸ್ವಿಯಾಗಿ ಸಕ್ರಿಯಗೊಂಡಿದೆ!","renewed_title":"Premium ಯಶಸ್ವಿಯಾಗಿ Renew ಮಾಡಲಾಗಿದೆ!","plan":"Plan","duration":"ಅವಧಿ","added":"ಸೇರಿಸಲಾಗಿದೆ","activated_at":"ಸಕ್ರಿಯಗೊಳಿಸಿದ ಸಮಯ","expires":"ಅವಧಿ ಮುಗಿಯುವಿಕೆ","new_expiry":"ಹೊಸ ಅವಧಿ","status":"ಸ್ಥಿತಿ: Active","thanks_purchase":"Premium ಖರೀದಿಸಿದ್ದಕ್ಕಾಗಿ ಧನ್ಯವಾದಗಳು!","thanks_renew":"Premium renew ಮಾಡಿದ್ದಕ್ಕಾಗಿ ಧನ್ಯವಾದಗಳು!","rejected_title":"Payment ತಿರಸ್ಕರಿಸಲಾಗಿದೆ","rejected_body":"ಈ payment ಮೂಲಕ ನೀಡಿದ Premium access ತೆಗೆದುಹಾಕಲಾಗಿದೆ. ಇದು ತಪ್ಪು ಎಂದು ಭಾವಿಸಿದರೆ Admin ಅನ್ನು ಸಂಪರ್ಕಿಸಿ."},
    "ml": {"activated_title":"Premium വിജയകരമായി സജീവമാക്കി!","renewed_title":"Premium വിജയകരമായി Renew ചെയ്തു!","plan":"Plan","duration":"കാലാവധി","added":"ചേർത്തത്","activated_at":"സജീവമാക്കിയ സമയം","expires":"കാലാവധി","new_expiry":"പുതിയ കാലാവധി","status":"സ്ഥിതി: Active","thanks_purchase":"Premium വാങ്ങിയതിന് നന്ദി!","thanks_renew":"Premium renew ചെയ്തതിന് നന്ദി!","rejected_title":"Payment നിരസിച്ചു","rejected_body":"ഈ payment വഴി നൽകിയ Premium access നീക്കം ചെയ്തു. തെറ്റാണെന്ന് തോന്നുന്നുവെങ്കിൽ Admin-നെ ബന്ധപ്പെടുക."},
    "bn": {"activated_title":"Premium সফলভাবে সক্রিয় হয়েছে!","renewed_title":"Premium সফলভাবে Renew হয়েছে!","plan":"প্ল্যান","duration":"সময়কাল","added":"যোগ হয়েছে","activated_at":"সক্রিয় হয়েছে","expires":"মেয়াদ শেষ","new_expiry":"নতুন মেয়াদ শেষ","status":"স্ট্যাটাস: সক্রিয়","thanks_purchase":"Premium কেনার জন্য ধন্যবাদ!","thanks_renew":"Premium renew করার জন্য ধন্যবাদ!","rejected_title":"Payment প্রত্যাখ্যাত হয়েছে","rejected_body":"এই payment-এর মাধ্যমে দেওয়া Premium access সরিয়ে দেওয়া হয়েছে। ভুল মনে হলে Admin-এর সাথে যোগাযোগ করুন।"},
    "mr": {"activated_title":"Premium यशस्वीरित्या सक्रिय झाले!","renewed_title":"Premium यशस्वीरित्या Renew झाले!","plan":"प्लॅन","duration":"कालावधी","added":"वाढवले","activated_at":"सक्रिय वेळ","expires":"समाप्ती","new_expiry":"नवीन समाप्ती","status":"स्थिती: सक्रिय","thanks_purchase":"Premium खरेदी केल्याबद्दल धन्यवाद!","thanks_renew":"Premium renew केल्याबद्दल धन्यवाद!","rejected_title":"Payment नाकारले गेले","rejected_body":"या payment मुळे दिलेले Premium access काढून टाकले आहे. चूक वाटत असल्यास Admin शी संपर्क करा."},
    "gu": {"activated_title":"Premium સફળતાપૂર્વક સક્રિય થયું!","renewed_title":"Premium સફળતાપૂર્વક Renew થયું!","plan":"પ્લાન","duration":"સમયગાળો","added":"ઉમેરાયું","activated_at":"સક્રિય સમય","expires":"સમાપ્તિ","new_expiry":"નવી સમાપ્તિ","status":"સ્થિતિ: સક્રિય","thanks_purchase":"Premium ખરીદવા બદલ આભાર!","thanks_renew":"Premium renew કરવા બદલ આભાર!","rejected_title":"Payment નામંજૂર થયું","rejected_body":"આ payment દ્વારા આપવામાં આવેલ Premium access દૂર કરવામાં આવ્યું છે. ભૂલ લાગે તો Adminનો સંપર્ક કરો."},
    "pa": {"activated_title":"Premium ਸਫਲਤਾਪੂਰਵਕ ਐਕਟੀਵੇਟ ਹੋ ਗਿਆ!","renewed_title":"Premium ਸਫਲਤਾਪੂਰਵਕ Renew ਹੋ ਗਿਆ!","plan":"ਪਲਾਨ","duration":"ਮਿਆਦ","added":"ਜੋੜਿਆ ਗਿਆ","activated_at":"ਐਕਟੀਵੇਟ ਸਮਾਂ","expires":"ਮਿਆਦ ਖਤਮ","new_expiry":"ਨਵੀਂ ਮਿਆਦ ਖਤਮ","status":"ਸਥਿਤੀ: Active","thanks_purchase":"Premium ਖਰੀਦਣ ਲਈ ਧੰਨਵਾਦ!","thanks_renew":"Premium renew ਕਰਨ ਲਈ ਧੰਨਵਾਦ!","rejected_title":"Payment ਰੱਦ ਕੀਤਾ ਗਿਆ","rejected_body":"ਇਸ payment ਨਾਲ ਦਿੱਤਾ Premium access ਹਟਾ ਦਿੱਤਾ ਗਿਆ ਹੈ। ਜੇ ਇਹ ਗਲਤੀ ਲੱਗਦੀ ਹੈ ਤਾਂ Admin ਨਾਲ ਸੰਪਰਕ ਕਰੋ।"},
    "ur": {"activated_title":"Premium کامیابی سے فعال ہو گیا!","renewed_title":"Premium کامیابی سے Renew ہو گیا!","plan":"پلان","duration":"مدت","added":"شامل کیا گیا","activated_at":"فعال ہونے کا وقت","expires":"میعاد ختم","new_expiry":"نئی میعاد","status":"حیثیت: فعال","thanks_purchase":"Premium خریدنے کا شکریہ!","thanks_renew":"Premium renew کرنے کا شکریہ!","rejected_title":"Payment مسترد کر دی گئی","rejected_body":"اس payment سے دیا گیا Premium access ہٹا دیا گیا ہے۔ اگر یہ غلطی ہے تو Admin سے رابطہ کریں۔"},
    "as": {"activated_title":"Premium সফলভাৱে সক্ৰিয় কৰা হৈছে!","renewed_title":"Premium সফলভাৱে Renew কৰা হৈছে!","plan":"Plan","duration":"সময়কাল","added":"যোগ কৰা হৈছে","activated_at":"সক্ৰিয় কৰাৰ সময়","expires":"ম্যাদ শেষ","new_expiry":"নতুন ম্যাদ শেষ","status":"স্থিতি: সক্ৰিয়","thanks_purchase":"Premium ক্ৰয় কৰাৰ বাবে ধন্যবাদ!","thanks_renew":"Premium renew কৰাৰ বাবে ধন্যবাদ!","rejected_title":"Payment নাকচ কৰা হৈছে","rejected_body":"এই payment-ৰ জৰিয়তে দিয়া Premium access আঁতৰাই দিয়া হৈছে। ভুল বুলি ভাবিলে Admin-ৰ সৈতে যোগাযোগ কৰক।"},
    "ne": {"activated_title":"Premium सफलतापूर्वक सक्रिय भयो!","renewed_title":"Premium सफलतापूर्वक Renew भयो!","plan":"प्लान","duration":"अवधि","added":"थपिएको","activated_at":"सक्रिय समय","expires":"म्याद सकिने","new_expiry":"नयाँ म्याद","status":"स्थिति: सक्रिय","thanks_purchase":"Premium खरिद गर्नुभएकोमा धन्यवाद!","thanks_renew":"Premium renew गर्नुभएकोमा धन्यवाद!","rejected_title":"Payment अस्वीकार गरियो","rejected_body":"यस payment बाट दिइएको Premium access हटाइएको छ। गल्ती भएको जस्तो लागेमा Admin लाई सम्पर्क गर्नुहोस्।"},
    "hinglish": {"activated_title":"Premium Successfully Activate Ho Gaya!","renewed_title":"Premium Successfully Renew Ho Gaya!","plan":"Plan","duration":"Duration","added":"Added","activated_at":"Activated","expires":"Expires","new_expiry":"New Expiry","status":"Status: Active","thanks_purchase":"Premium purchase karne ke liye thank you!","thanks_renew":"Premium renew karne ke liye thank you!","rejected_title":"Payment Reject Ho Gaya","rejected_body":"Is payment se mila Premium access remove kar diya gaya hai. Agar aapko lagta hai ye mistake hai, Admin se contact karo."},
}

_NO_ORDER_WARNING_I18N = {
    "en": "We could not find an active Premium order linked to your account.\nPlease select a Premium plan first, complete the payment, and then send the screenshot here.\n\n🧹 This notice will disappear automatically after 10 seconds.",
    "hi": "आपके खाते से कोई सक्रिय Premium Order नहीं मिला।\nपहले Premium Plan चुनें, payment पूरा करें और फिर screenshot भेजें।\n\n🧹 यह संदेश 10 सेकंड बाद अपने आप हट जाएगा।",
    "ta": "உங்கள் கணக்குடன் செயலில் உள்ள Premium Order எதுவும் இல்லை.\nமுதலில் Premium Plan தேர்வு செய்து payment முடித்து screenshot அனுப்பவும்.\n\n🧹 இந்த செய்தி 10 விநாடிகளில் தானாக நீக்கப்படும்.",
    "te": "మీ ఖాతాతో active Premium Order కనుగొనబడలేదు.\nముందుగా Premium Plan ఎంచుకుని payment పూర్తి చేసి screenshot పంపండి.\n\n🧹 ఈ సందేశం 10 సెకన్లలో ఆటోమేటిక్‌గా తొలగించబడుతుంది.",
    "kn": "ನಿಮ್ಮ ಖಾತೆಗೆ ಸಕ್ರಿಯ Premium Order ಕಂಡುಬಂದಿಲ್ಲ.\nಮೊದಲು Premium Plan ಆಯ್ಕೆ ಮಾಡಿ payment ಪೂರ್ಣಗೊಳಿಸಿ screenshot ಕಳುಹಿಸಿ.\n\n🧹 ಈ ಸಂದೇಶ 10 ಸೆಕೆಂಡುಗಳಲ್ಲಿ ಅಳಿಸಲಾಗುತ್ತದೆ.",
    "ml": "നിങ്ങളുടെ അക്കൗണ്ടുമായി സജീവമായ Premium Order കണ്ടെത്തിയില്ല.\nആദ്യം Premium Plan തിരഞ്ഞെടുക്കുക, payment പൂർത്തിയാക്കി screenshot അയയ്ക്കുക.\n\n🧹 ഈ സന്ദേശം 10 സെക്കൻഡിൽ സ്വയം നീക്കും.",
    "bn": "আপনার অ্যাকাউন্টের সঙ্গে কোনো সক্রিয় Premium Order পাওয়া যায়নি।\nপ্রথমে Premium Plan বেছে payment সম্পূর্ণ করে screenshot পাঠান।\n\n🧹 এই বার্তাটি ১০ সেকেন্ড পরে নিজে থেকে মুছে যাবে।",
    "mr": "तुमच्या खात्याशी कोणताही सक्रिय Premium Order सापडला नाही.\nआधी Premium Plan निवडा, payment पूर्ण करा आणि screenshot पाठवा.\n\n🧹 हा संदेश 10 सेकंदांनी आपोआप हटेल.",
    "gu": "તમારા ખાતા સાથે કોઈ active Premium Order મળ્યો નથી.\nપહેલા Premium Plan પસંદ કરો, payment પૂર્ણ કરો અને screenshot મોકલો.\n\n🧹 આ સંદેશ 10 સેકન્ડમાં આપમેળે દૂર થશે.",
    "pa": "ਤੁਹਾਡੇ ਖਾਤੇ ਨਾਲ ਕੋਈ active Premium Order ਨਹੀਂ ਮਿਲਿਆ।\nਪਹਿਲਾਂ Premium Plan ਚੁਣੋ, payment ਪੂਰਾ ਕਰੋ ਅਤੇ screenshot ਭੇਜੋ।\n\n🧹 ਇਹ message 10 ਸਕਿੰਟ ਬਾਅਦ ਆਪਣੇ ਆਪ delete ਹੋ ਜਾਵੇਗਾ।",
    "ur": "آپ کے اکاؤنٹ کے ساتھ کوئی فعال Premium Order نہیں ملا۔\nپہلے Premium Plan منتخب کریں، payment مکمل کریں اور screenshot بھیجیں۔\n\n🧹 یہ پیغام 10 سیکنڈ بعد خود حذف ہو جائے گا۔",
    "as": "আপোনাৰ একাউণ্টৰ সৈতে কোনো সক্ৰিয় Premium Order পোৱা নগ'ল।\nআগতে Premium Plan বাছি payment সম্পূৰ্ণ কৰি screenshot পঠাওক।\n\n🧹 এই বাৰ্তা ১০ ছেকেণ্ড পিছত নিজে মচি যাব।",
    "ne": "तपाईंको खातासँग सक्रिय Premium Order भेटिएन।\nपहिले Premium Plan छान्नुहोस्, payment पूरा गरेर screenshot पठाउनुहोस्।\n\n🧹 यो सन्देश १० सेकेन्डपछि आफैं हट्नेछ।",
    "hinglish": "Aapke account se koi active Premium Order nahi mila.\nPehle Premium Plan choose karo, payment complete karo aur phir screenshot bhejo.\n\n🧹 Ye message 10 seconds baad automatically delete ho jayega.",
}

def _no_order_warning(lang):
    title = _tr(lang, "no_order_title")
    body = _NO_ORDER_WARNING_I18N.get(lang, _NO_ORDER_WARNING_I18N["en"])
    return title + "\n\n" + body

_PROCESSING_ERROR_I18N = {
    "en":"⚠️ Your screenshot was received, but processing failed temporarily. Please try again later or contact the admin.",
    "hi":"⚠️ आपका screenshot मिल गया, लेकिन processing में अस्थायी समस्या हुई। बाद में फिर कोशिश करें या Admin से संपर्क करें।",
    "ta":"⚠️ உங்கள் screenshot பெறப்பட்டது, ஆனால் processing-ல் தற்காலிக சிக்கல் ஏற்பட்டது. பின்னர் மீண்டும் முயற்சிக்கவும் அல்லது Admin-ஐ தொடர்புகொள்ளவும்.",
    "te":"⚠️ మీ screenshot అందింది, కానీ processingలో తాత్కాలిక సమస్య వచ్చింది. తర్వాత మళ్లీ ప్రయత్నించండి లేదా Admin‌ను సంప్రదించండి.",
    "kn":"⚠️ ನಿಮ್ಮ screenshot ಬಂದಿದೆ, ಆದರೆ processing ನಲ್ಲಿ ತಾತ್ಕಾಲಿಕ ಸಮಸ್ಯೆ ಉಂಟಾಗಿದೆ. ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ ಅಥವಾ Admin ಸಂಪರ್ಕಿಸಿ.",
    "ml":"⚠️ നിങ്ങളുടെ screenshot ലഭിച്ചു, പക്ഷേ processing-ൽ താൽക്കാലിക പ്രശ്നമുണ്ടായി. പിന്നീട് വീണ്ടും ശ്രമിക്കുക അല്ലെങ്കിൽ Admin-നെ ബന്ധപ്പെടുക.",
    "bn":"⚠️ আপনার screenshot পাওয়া গেছে, কিন্তু processing-এ সাময়িক সমস্যা হয়েছে। পরে আবার চেষ্টা করুন বা Admin-এর সঙ্গে যোগাযোগ করুন।",
    "mr":"⚠️ तुमचा screenshot मिळाला, पण processing मध्ये तात्पुरती अडचण आली. नंतर पुन्हा प्रयत्न करा किंवा Admin शी संपर्क करा.",
    "gu":"⚠️ તમારો screenshot મળ્યો, પરંતુ processingમાં તાત્કાલિક સમસ્યા આવી. પછી ફરી પ્રયાસ કરો અથવા Adminનો સંપર્ક કરો.",
    "pa":"⚠️ ਤੁਹਾਡਾ screenshot ਮਿਲ ਗਿਆ, ਪਰ processing ਵਿੱਚ ਅਸਥਾਈ ਸਮੱਸਿਆ ਆਈ। ਬਾਅਦ ਵਿੱਚ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ ਜਾਂ Admin ਨਾਲ ਸੰਪਰਕ ਕਰੋ।",
    "ur":"⚠️ آپ کا screenshot موصول ہو گیا، لیکن processing میں عارضی مسئلہ آیا۔ بعد میں دوبارہ کوشش کریں یا Admin سے رابطہ کریں۔",
    "as":"⚠️ আপোনাৰ screenshot পোৱা গ'ল, কিন্তু processing-ত সাময়িক সমস্যা হৈছে। পিছত পুনৰ চেষ্টা কৰক বা Admin-ৰ সৈতে যোগাযোগ কৰক।",
    "ne":"⚠️ तपाईंको screenshot प्राप्त भयो, तर processing मा अस्थायी समस्या भयो। पछि फेरि प्रयास गर्नुहोस् वा Admin लाई सम्पर्क गर्नुहोस्।",
    "hinglish":"⚠️ Aapka screenshot mil gaya, lekin processing mein temporary problem hui. Baad mein dobara try karo ya Admin se contact karo.",
}

_EXPIRY_LABELS = {
    "en": ("Status: Expired", "4 days or less"),
    "hi": ("स्थिति: समाप्त", "4 दिन या कम"),
    "ta": ("நிலை: காலாவதி", "4 நாட்கள் அல்லது குறைவு"),
    "te": ("స్థితి: గడువు ముగిసింది", "4 రోజులు లేదా తక్కువ"),
    "kn": ("ಸ್ಥಿತಿ: ಅವಧಿ ಮುಗಿದಿದೆ", "4 ದಿನಗಳು ಅಥವಾ ಕಡಿಮೆ"),
    "ml": ("സ്ഥിതി: കാലാവധി കഴിഞ്ഞു", "4 ദിവസമോ അതിൽ കുറവോ"),
    "bn": ("স্ট্যাটাস: মেয়াদ শেষ", "৪ দিন বা কম"),
    "mr": ("स्थिती: समाप्त", "4 दिवस किंवा कमी"),
    "gu": ("સ્થિતિ: સમાપ્ત", "4 દિવસ અથવા ઓછા"),
    "pa": ("ਸਥਿਤੀ: ਸਮਾਪਤ", "4 ਦਿਨ ਜਾਂ ਘੱਟ"),
    "ur": ("حیثیت: میعاد ختم", "4 دن یا کم"),
    "as": ("স্থিতি: ম্যাদ শেষ", "৪ দিন বা কম"),
    "ne": ("स्थिति: म्याद सकियो", "४ दिन वा कम"),
    "hinglish": ("Status: Expired", "4 days ya usse kam"),
}
def _expiry_label(lang, kind):
    return small_caps(_EXPIRY_LABELS.get(lang, _EXPIRY_LABELS["en"])[0 if kind == "status" else 1])

def _result_text(lang, key):
    return small_caps(_RESULT_I18N.get(lang, _RESULT_I18N["en"]).get(key, _RESULT_I18N["en"][key]))


# Premium entry/order UI is also localized so the language choice is useful
# from the very first Premium screen, not only after a screenshot is sent.
PREMIUM_FLOW_I18N = {
    "en": {
        "intro": "💎 <b>Premium Membership</b>\n\nChoose your Premium plan to remove ads and unlock Premium access.",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nSelect a plan below to continue with payment.",
        "order": "💳 <b>Premium Order Created</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ Duration: <b>{duration}</b>\n💰 Price: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nComplete the payment, then send your payment screenshot to the dedicated payment bot.\n\n⚠️ Your screenshot will be checked before the payment is confirmed.",
        "send": "📸 SEND PAYMENT SCREENSHOT", "back": "• BACK TO PLANS •", "close": "• CLOSE •",
        "selected": "Premium plan selected.",
    },
    "hi": {
        "intro": "💎 <b>Premium Membership</b>\n\nAds हटाने और Premium access पाने के लिए अपना Premium Plan चुनें।",
        "plans": "💎 <b>Premium Plans और Prices</b>\n\nPayment जारी रखने के लिए नीचे अपना Plan चुनें।",
        "order": "💳 <b>Premium Order बन गया</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ अवधि: <b>{duration}</b>\n💰 कीमत: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment पूरा करें और फिर अपना screenshot dedicated payment bot पर भेजें।\n\n⚠️ Payment confirm होने से पहले screenshot की जाँच की जाएगी।",
        "send": "📸 PAYMENT SCREENSHOT भेजें", "back": "• PLANS पर वापस •", "close": "• बंद करें •", "selected": "Premium Plan चुना गया है।",
    },
    "ta": {
        "intro": "💎 <b>Premium Membership</b>\n\nவிளம்பரங்களை நீக்கவும் Premium access பெறவும் உங்கள் Plan-ஐ தேர்வு செய்யவும்.",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nPayment தொடர கீழே ஒரு Plan-ஐ தேர்வு செய்யவும்.",
        "order": "💳 <b>Premium Order உருவாக்கப்பட்டது</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ காலம்: <b>{duration}</b>\n💰 விலை: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment முடித்து screenshot-ஐ dedicated payment bot-க்கு அனுப்பவும்.\n\n⚠️ Payment confirm செய்வதற்கு முன் screenshot சரிபார்க்கப்படும்.",
        "send": "📸 PAYMENT SCREENSHOT அனுப்பவும்", "back": "• PLANS-க்கு திரும்பு •", "close": "• மூடு •", "selected": "Premium Plan தேர்வு செய்யப்பட்டது.",
    },
    "te": {
        "intro": "💎 <b>Premium Membership</b>\n\nAds తొలగించి Premium access పొందడానికి మీ Premium Plan ఎంచుకోండి.",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nPayment కొనసాగించడానికి క్రింద Plan ఎంచుకోండి.",
        "order": "💳 <b>Premium Order రూపొందించబడింది</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ వ్యవధి: <b>{duration}</b>\n💰 ధర: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment పూర్తి చేసి screenshot‌ను dedicated payment bot‌కు పంపండి.\n\n⚠️ Payment confirm చేయడానికి ముందు screenshot పరిశీలించబడుతుంది.",
        "send": "📸 PAYMENT SCREENSHOT పంపండి", "back": "• PLANS కు తిరిగి •", "close": "• మూసివేయండి •", "selected": "Premium Plan ఎంచుకోబడింది.",
    },
    "kn": {
        "intro": "💎 <b>Premium Membership</b>\n\nAds ತೆಗೆದು Premium access ಪಡೆಯಲು ನಿಮ್ಮ Premium Plan ಆಯ್ಕೆಮಾಡಿ.",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nPayment ಮುಂದುವರಿಸಲು ಕೆಳಗೆ Plan ಆಯ್ಕೆಮಾಡಿ.",
        "order": "💳 <b>Premium Order ರಚಿಸಲಾಗಿದೆ</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ ಅವಧಿ: <b>{duration}</b>\n💰 ಬೆಲೆ: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment ಪೂರ್ಣಗೊಳಿಸಿ screenshot ಅನ್ನು dedicated payment bot ಗೆ ಕಳುಹಿಸಿ.\n\n⚠️ Payment confirm ಮಾಡುವ ಮೊದಲು screenshot ಪರಿಶೀಲಿಸಲಾಗುತ್ತದೆ.",
        "send": "📸 PAYMENT SCREENSHOT ಕಳುಹಿಸಿ", "back": "• PLANS ಗೆ ಹಿಂದಿರುಗಿ •", "close": "• ಮುಚ್ಚಿ •", "selected": "Premium Plan ಆಯ್ಕೆಮಾಡಲಾಗಿದೆ.",
    },
    "ml": {
        "intro": "💎 <b>Premium Membership</b>\n\nAds ഒഴിവാക്കി Premium access ലഭിക്കാൻ നിങ്ങളുടെ Premium Plan തിരഞ്ഞെടുക്കുക.",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nPayment തുടരാൻ താഴെ ഒരു Plan തിരഞ്ഞെടുക്കുക.",
        "order": "💳 <b>Premium Order സൃഷ്ടിച്ചു</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ കാലാവധി: <b>{duration}</b>\n💰 വില: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment പൂർത്തിയാക്കി screenshot dedicated payment bot-ലേക്ക് അയയ്ക്കുക.\n\n⚠️ Payment സ്ഥിരീകരിക്കുന്നതിന് മുമ്പ് screenshot പരിശോധിക്കും.",
        "send": "📸 PAYMENT SCREENSHOT അയയ്ക്കുക", "back": "• PLANS-ലേക്ക് മടങ്ങുക •", "close": "• അടയ്ക്കുക •", "selected": "Premium Plan തിരഞ്ഞെടുത്തു.",
    },
    "as": {
        "intro": "💎 <b>Premium Membership</b>\n\nAds আঁতৰাবলৈ আৰু Premium access পাবলৈ আপোনাৰ Premium Plan বাছক।",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nPayment আগবঢ়াবলৈ তলৰ পৰা এটা Plan বাছক।",
        "order": "💳 <b>Premium Order তৈয়াৰ হৈছে</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ সময়কাল: <b>{duration}</b>\n💰 মূল্য: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment সম্পূৰ্ণ কৰি screenshot-টো dedicated payment bot-লৈ পঠিয়াওক।\n\n⚠️ Payment confirm কৰাৰ আগতে screenshot পৰীক্ষা কৰা হ'ব।",
        "send": "📸 PAYMENT SCREENSHOT পঠিয়াওক", "back": "• PLANS লৈ উভতি যাওক •", "close": "• বন্ধ কৰক •", "selected": "Premium Plan বাছনি কৰা হৈছে।",
    },
    "ne": {
        "intro": "💎 <b>Premium Membership</b>\n\nAds हटाउन र Premium access पाउन आफ्नो Premium Plan छान्नुहोस्।",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nPayment अगाडि बढाउन तलबाट एउटा Plan छान्नुहोस्।",
        "order": "💳 <b>Premium Order तयार भयो</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ अवधि: <b>{duration}</b>\n💰 मूल्य: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment पूरा गरेर screenshot dedicated payment bot मा पठाउनुहोस्।\n\n⚠️ Payment confirm गर्नुअघि screenshot जाँच गरिनेछ।",
        "send": "📸 PAYMENT SCREENSHOT पठाउनुहोस्", "back": "• PLANS मा फर्कनुहोस् •", "close": "• बन्द गर्नुहोस् •", "selected": "Premium Plan छानिएको छ।",
    },
    "hinglish": {
        "intro": "💎 <b>Premium Membership</b>\n\nAds hatane aur Premium access paane ke liye apna Premium Plan choose karo.",
        "plans": "💎 <b>Premium Plans & Prices</b>\n\nPayment continue karne ke liye neeche se ek Plan choose karo.",
        "order": "💳 <b>Premium Order Create Ho Gaya</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ Duration: <b>{duration}</b>\n💰 Price: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment complete karo, phir screenshot dedicated payment bot par bhejo.\n\n⚠️ Payment confirm hone se pehle screenshot check kiya jayega.",
        "send": "📸 PAYMENT SCREENSHOT BHEJO", "back": "• PLANS PAR WAPAS •", "close": "• CLOSE •", "selected": "Premium Plan select ho gaya hai.",
    },
}
# Complete the Premium entry/order UI for every advertised language.
PREMIUM_FLOW_I18N["bn"] = {
    "intro":"💎 <b>Premium Membership</b>\n\nAds সরিয়ে Premium access পেতে আপনার Premium Plan বেছে নিন.",
    "plans":"💎 <b>Premium Plans & Prices</b>\n\nPayment চালিয়ে যেতে নিচের একটি Plan বেছে নিন।",
    "order":"💳 <b>Premium Order তৈরি হয়েছে</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ সময়কাল: <b>{duration}</b>\n💰 মূল্য: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment সম্পূর্ণ করে screenshot dedicated payment bot-এ পাঠান।\n\n⚠️ Payment confirm করার আগে screenshot যাচাই করা হবে।",
    "send":"📸 PAYMENT SCREENSHOT পাঠান","back":"• PLANS-এ ফিরে যান •","close":"• বন্ধ করুন •","selected":"Premium Plan বেছে নেওয়া হয়েছে।"}
PREMIUM_FLOW_I18N["mr"] = {
    "intro":"💎 <b>Premium Membership</b>\n\nAds काढण्यासाठी आणि Premium access मिळवण्यासाठी तुमचा Premium Plan निवडा.",
    "plans":"💎 <b>Premium Plans & Prices</b>\n\nPayment पुढे नेण्यासाठी खालील Plan निवडा.",
    "order":"💳 <b>Premium Order तयार झाले</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ कालावधी: <b>{duration}</b>\n💰 किंमत: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment पूर्ण करा आणि screenshot dedicated payment bot वर पाठवा.\n\n⚠️ Payment confirm करण्यापूर्वी screenshot तपासला जाईल.",
    "send":"📸 PAYMENT SCREENSHOT पाठवा","back":"• PLANS कडे परत •","close":"• बंद करा •","selected":"Premium Plan निवडला आहे."}
PREMIUM_FLOW_I18N["gu"] = {
    "intro":"💎 <b>Premium Membership</b>\n\nAds દૂર કરવા અને Premium access મેળવવા તમારો Premium Plan પસંદ કરો.",
    "plans":"💎 <b>Premium Plans & Prices</b>\n\nPayment ચાલુ રાખવા નીચેનો Plan પસંદ કરો.",
    "order":"💳 <b>Premium Order બની ગયો</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ સમયગાળો: <b>{duration}</b>\n💰 કિંમત: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment પૂર્ણ કરો અને screenshot dedicated payment bot પર મોકલો.\n\n⚠️ Payment confirm કરતા પહેલાં screenshot તપાસવામાં આવશે.",
    "send":"📸 PAYMENT SCREENSHOT મોકલો","back":"• PLANS પર પાછા •","close":"• બંધ કરો •","selected":"Premium Plan પસંદ થયો છે."}
PREMIUM_FLOW_I18N["pa"] = {
    "intro":"💎 <b>Premium Membership</b>\n\nAds ਹਟਾਉਣ ਅਤੇ Premium access ਲੈਣ ਲਈ ਆਪਣਾ Premium Plan ਚੁਣੋ।",
    "plans":"💎 <b>Premium Plans & Prices</b>\n\nPayment ਜਾਰੀ ਰੱਖਣ ਲਈ ਹੇਠਾਂ ਇੱਕ Plan ਚੁਣੋ।",
    "order":"💳 <b>Premium Order ਬਣ ਗਿਆ</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ ਮਿਆਦ: <b>{duration}</b>\n💰 ਕੀਮਤ: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment ਪੂਰਾ ਕਰੋ ਅਤੇ screenshot dedicated payment bot ਨੂੰ ਭੇਜੋ।\n\n⚠️ Payment confirm ਕਰਨ ਤੋਂ ਪਹਿਲਾਂ screenshot ਚੈੱਕ ਕੀਤਾ ਜਾਵੇਗਾ।",
    "send":"📸 PAYMENT SCREENSHOT ਭੇਜੋ","back":"• PLANS ਵੱਲ ਵਾਪਸ •","close":"• ਬੰਦ ਕਰੋ •","selected":"Premium Plan ਚੁਣਿਆ ਗਿਆ ਹੈ।"}
PREMIUM_FLOW_I18N["ur"] = {
    "intro":"💎 <b>Premium Membership</b>\n\nAds ہٹانے اور Premium access حاصل کرنے کے لیے اپنا Premium Plan منتخب کریں۔",
    "plans":"💎 <b>Premium Plans & Prices</b>\n\nPayment جاری رکھنے کے لیے نیچے ایک Plan منتخب کریں۔",
    "order":"💳 <b>Premium Order بن گیا</b>\n\n📦 Plan: <b>{plan}</b>\n⏳ مدت: <b>{duration}</b>\n💰 قیمت: <b>{price}</b>\n\n🟡 Payment status: <code>waiting_for_payment</code>\n\nPayment مکمل کریں اور screenshot dedicated payment bot پر بھیجیں۔\n\n⚠️ Payment confirm کرنے سے پہلے screenshot چیک کیا جائے گا۔",
    "send":"📸 PAYMENT SCREENSHOT بھیجیں","back":"• PLANS پر واپس •","close":"• بند کریں •","selected":"Premium Plan منتخب کر لیا گیا ہے۔"}

_CONTINUE_I18N = {
    "en":"• CONTINUE TO PLANS •","hi":"• PLANS पर जाएँ •","ta":"• PLANS-க்கு தொடரவும் •","te":"• PLANS కి కొనసాగండి •","kn":"• PLANS ಗೆ ಮುಂದುವರಿಯಿರಿ •","ml":"• PLANS-ലേക്ക് തുടരുക •","bn":"• PLANS-এ যান •","mr":"• PLANS कडे जा •","gu":"• PLANS પર જાઓ •","pa":"• PLANS ਵੱਲ ਜਾਓ •","ur":"• PLANS پر جائیں •","as":"• PLANS লৈ আগবাঢ়ক •","ne":"• PLANS मा जानुहोस् •","hinglish":"• PLANS PAR CHALO •"}
for _code in PREMIUM_FLOW_I18N:
    PREMIUM_FLOW_I18N[_code].setdefault("continue", _CONTINUE_I18N.get(_code, _CONTINUE_I18N["en"]))

# Critical screenshot states are available in every advertised language so a
# user never gets an English processing/no-order message after choosing a language.
for _c in GLOBAL_LANGUAGES:
    I18N.setdefault(_c, dict(I18N["en"]))
I18N["bn"].update({"progress_title":"🔎 <b>পেমেন্ট স্ক্রিনশট পাওয়া গেছে</b>","progress_body":"⏳ আপনার পেমেন্ট নিরাপদভাবে পরীক্ষা করা হচ্ছে। এতে <b>১–২ মিনিট</b> লাগতে পারে। স্ক্রিনশট আবার পাঠাবেন না এবং মেনু বন্ধ করবেন না।\n\n✅ পরীক্ষা শেষ হলে ফলাফল স্বয়ংক্রিয়ভাবে পাবেন।","no_order_title":"⚠️ <b>কোনো Premium Order পাওয়া যায়নি</b>","no_order_body":"প্রথমে একটি Premium Plan নির্বাচন করে payment সম্পূর্ণ করুন, তারপর screenshot পাঠান।\n\n🧹 এই বার্তাটি ১০ সেকেন্ড পরে নিজে থেকে মুছে যাবে।"})
I18N["mr"].update({"progress_title":"🔎 <b>Payment Screenshot मिळाला</b>","progress_body":"⏳ तुमचे payment सुरक्षितपणे तपासले जात आहे. यासाठी <b>1–2 मिनिटे</b> लागू शकतात. Screenshot पुन्हा पाठवू नका आणि menu बंद करू नका.\n\n✅ तपासणी पूर्ण झाल्यावर निकाल आपोआप मिळेल.","no_order_title":"⚠️ <b>Premium Order सापडला नाही</b>","no_order_body":"आधी Premium Plan निवडा, payment पूर्ण करा आणि नंतर screenshot पाठवा.\n\n🧹 हा संदेश 10 सेकंदांनी आपोआप हटेल."})
I18N["gu"].update({"progress_title":"🔎 <b>Payment Screenshot મળ્યો</b>","progress_body":"⏳ તમારું payment સુરક્ષિત રીતે તપાસાઈ રહ્યું છે. તેમાં <b>1–2 મિનિટ</b> લાગી શકે છે. Screenshot ફરી મોકલશો નહીં અને menu બંધ કરશો નહીં.\n\n✅ તપાસ પૂર્ણ થયા પછી પરિણામ આપમેળે મળશે.","no_order_title":"⚠️ <b>Premium Order મળ્યો નથી</b>","no_order_body":"પહેલા Premium Plan પસંદ કરો, payment પૂર્ણ કરો અને પછી screenshot મોકલો.\n\n🧹 આ સંદેશ 10 સેકન્ડમાં આપમેળે દૂર થશે."})
I18N["pa"].update({"progress_title":"🔎 <b>Payment Screenshot ਮਿਲ ਗਿਆ</b>","progress_body":"⏳ ਤੁਹਾਡਾ payment ਸੁਰੱਖਿਅਤ ਤਰੀਕੇ ਨਾਲ check ਹੋ ਰਿਹਾ ਹੈ। ਇਸ ਵਿੱਚ <b>1–2 ਮਿੰਟ</b> ਲੱਗ ਸਕਦੇ ਹਨ। Screenshot ਦੁਬਾਰਾ ਨਾ ਭੇਜੋ ਅਤੇ menu ਬੰਦ ਨਾ ਕਰੋ।\n\n✅ Check ਪੂਰਾ ਹੋਣ ਤੇ result ਆਪਣੇ ਆਪ ਮਿਲੇਗਾ.","no_order_title":"⚠️ <b>Premium Order ਨਹੀਂ ਮਿਲਿਆ</b>","no_order_body":"ਪਹਿਲਾਂ Premium Plan ਚੁਣੋ, payment complete ਕਰੋ ਅਤੇ ਫਿਰ screenshot ਭੇਜੋ।\n\n🧹 ਇਹ message 10 ਸਕਿੰਟ ਬਾਅਦ ਆਪਣੇ ਆਪ delete ਹੋ ਜਾਵੇਗਾ."})
I18N["ur"].update({"progress_title":"🔎 <b>Payment Screenshot موصول ہو گیا</b>","progress_body":"⏳ آپ کی payment محفوظ طریقے سے چیک کی جا رہی ہے۔ اس میں <b>1–2 منٹ</b> لگ سکتے ہیں۔ Screenshot دوبارہ نہ بھیجیں اور menu بند نہ کریں۔\n\n✅ چیک مکمل ہونے پر نتیجہ خود مل جائے گا۔","no_order_title":"⚠️ <b>Premium Order نہیں ملا</b>","no_order_body":"پہلے Premium Plan منتخب کریں، payment مکمل کریں اور پھر screenshot بھیجیں۔\n\n🧹 یہ پیغام 10 سیکنڈ بعد خود حذف ہو جائے گا۔"})
I18N["as"].update({"progress_title":"🔎 <b>Payment Screenshot পোৱা গ'ল</b>","progress_body":"⏳ আপোনাৰ payment সুৰক্ষিতভাৱে পৰীক্ষা কৰা হৈছে। <b>১–২ মিনিট</b> লাগিব পাৰে। Screenshot পুনৰ নপঠাব আৰু menu বন্ধ নকৰিব।\n\n✅ পৰীক্ষা শেষ হ'লে ফলাফল নিজে পাব।","no_order_title":"⚠️ <b>Premium Order পোৱা নগ'ল</b>","no_order_body":"আগতে Premium Plan বাছি payment সম্পূৰ্ণ কৰক, তাৰ পিছত screenshot পঠাওক।\n\n🧹 এই বাৰ্তা ১০ ছেকেণ্ড পিছত নিজে মচি যাব।"})
I18N["ne"].update({"progress_title":"🔎 <b>Payment Screenshot प्राप्त भयो</b>","progress_body":"⏳ तपाईंको payment सुरक्षित रूपमा जाँच भइरहेको छ। यसलाई <b>१–२ मिनेट</b> लाग्न सक्छ। Screenshot फेरि नपठाउनुहोस् र menu बन्द नगर्नुहोस्।\n\n✅ जाँच पूरा भएपछि परिणाम आफैं आउनेछ।","no_order_title":"⚠️ <b>Premium Order भेटिएन</b>","no_order_body":"पहिले Premium Plan छान्नुहोस्, payment पूरा गर्नुहोस् र त्यसपछि screenshot पठाउनुहोस्।\n\n🧹 यो सन्देश १० सेकेन्डपछि आफैं हट्नेछ।"})
I18N["hinglish"].update({"progress_title":"🔎 <b>Payment Screenshot Mil Gaya</b>","progress_body":"⏳ Aapka payment safely check ho raha hai. Isme <b>1–2 minutes</b> lag sakte hain. Screenshot dobara mat bhejo aur menu close mat karo.\n\n✅ Check complete hone ke baad result automatically mil jayega.","no_order_title":"⚠️ <b>Premium Order Nahi Mila</b>","no_order_body":"Pehle Premium Plan choose karo, payment complete karo aur phir screenshot bhejo.\n\n🧹 Ye message 10 seconds baad automatically delete ho jayega."})

def _premium_flow_text(lang, key, **values):
    text = PREMIUM_FLOW_I18N.get(lang, PREMIUM_FLOW_I18N["en"]).get(key, PREMIUM_FLOW_I18N["en"].get(key, key))
    return text.format(**values) if values else text


def _language_button_text(lang):
    return I18N.get(lang, I18N["en"]).get("language_button", I18N["en"]["language_button"])


def _lang_from_code(code):
    code = str(code or "").lower().replace("_", "-")
    base = code.split("-", 1)[0]
    return LANGUAGE_ALIASES.get(code) or LANGUAGE_ALIASES.get(base) or "en"


async def _user_language(user_id, telegram_user=None):
    # One global language source for the entire bot, including Premium/payment.
    # Existing accounts without a saved preference safely default to English.
    return await _global_user_language(user_id, telegram_user)


def _tr(lang, key, **values):
    text = I18N.get(lang, I18N["en"]).get(key, I18N["en"].get(key, key))
    return text.format(**values) if values else text


def _language_markup():
    codes = list(LANGUAGES)
    rows = []
    for i in range(0, len(codes), 2):
        rows.append([InlineKeyboardButton(LANGUAGES[codes[i]], callback_data=f"global_lang:{codes[i]}")])
        if i + 1 < len(codes):
            rows[-1].append(InlineKeyboardButton(LANGUAGES[codes[i + 1]], callback_data=f"global_lang:{codes[i+1]}"))
    return InlineKeyboardMarkup(rows)


async def _delete_message_later(sent_message, delay=TEMP_MESSAGE_DELETE_SECONDS):
    """Delete a temporary bot message after a fixed retention window.

    Telegram bots do not receive a reliable private-chat "message seen/read"
    event, so deletion is scheduled from send time rather than pretending to
    know when the user viewed it.
    """
    try:
        await asyncio.sleep(delay)
        await sent_message.delete()
    except Exception:
        pass


def _schedule_temp_delete(sent_message, delay=TEMP_MESSAGE_DELETE_SECONDS):
    if sent_message is not None:
        asyncio.create_task(_delete_message_later(sent_message, delay))
    return sent_message


async def _reply_temp(message, text, delay=TEMP_MESSAGE_DELETE_SECONDS, **kwargs):
    sent = await message.reply_text(text, **kwargs)
    return _schedule_temp_delete(sent, delay)


async def _send_user_temp(client, user_id, text, delay=TEMP_MESSAGE_DELETE_SECONDS, **kwargs):
    sent = await client.send_message(user_id, text, **kwargs)
    return _schedule_temp_delete(sent, delay)


def _contact_admin_markup(lang="en"):
    """Return a direct Telegram contact button using the configured owner username."""
    username = (OWNER_USERNAME or "").strip().lstrip("@")
    if not username:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(_tr(lang, "contact"), url=f"https://t.me/{username}")]]
    )


def _plan_key(value):
    value = str(value).lower().strip()
    aliases = {
        "7": "week", "7day": "week", "7days": "week", "week": "week",
        "30": "month", "30day": "month", "30days": "month", "month": "month",
        "90": "3month", "3month": "3month", "3months": "3month",
        "180": "6month", "6month": "6month", "6months": "6month",
        "365": "year", "1year": "year", "year": "year", "12month": "year",
        "lifetime": "lifetime", "life": "lifetime",
    }
    return aliases.get(value, value if value in PREMIUM_PLANS else None)


def _expiry_from(base, plan):
    days = PREMIUM_PLANS[plan]["days"]
    if days is None:
        return LIFETIME_EXPIRY
    return base + datetime.timedelta(days=days)


def _remaining_label(expires_at):
    seconds = max(0, int((expires_at - _now()).total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"



def _money_number(value):
    """Normalize a displayed amount to numeric rupees.

    Currency symbols and leading zeroes are formatting, not value.  Thus
    ₹23, ₹23.00, Rs 23, INR 23 and ₹023 all normalize to 23.00.
    """
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"(?i)(?:₹|rs\.?|inr)\s*", "", text)
    m = re.search(r"(?<!\d)(\d+(?:[.]\d{1,2})?)(?!\d)", text)
    if not m:
        return None
    try:
        return round(float(m.group(1)), 2)
    except ValueError:
        return None


def _expected_amount(plan_price):
    return _money_number(plan_price)


def _extract_amount(text, expected):
    """Extract the most plausible payment amount from varied OCR output.

    Payment apps often omit the currency symbol.  We therefore consider
    standalone numeric candidates, currency-labelled candidates, and common
    OCR corruptions.  A candidate matching the pending order amount is strongly
    preferred, which prevents transaction/reference numbers from being chosen.
    """
    if not text:
        return None
    expected = _money_number(expected)
    normalized = text.replace("\u00a0", " ")
    lines = [re.sub(r"\s+", " ", x.strip()) for x in normalized.splitlines() if x.strip()]
    candidates = []

    def add(value, score, source):
        if value is None or value < 0 or value >= 10000000:
            return
        candidates.append((round(value, 2), score, source))

    currency_patterns = [
        r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:[.]\d{1,2})?)",
        r"(?:amount\s*(?:paid|sent|debited|received)?|paid\s*(?:amount)?|sent\s*amount|total|payment)\D{0,40}([0-9][0-9,]*(?:[.]\d{1,2})?)",
    ]
    for line in lines:
        for pat in currency_patterns:
            for m in re.finditer(pat, line, re.I):
                add(_money_number(m.group(1)), 100, "labelled")

    # Standalone amount lines. OCR may turn ₹23.00 into <23:00.
    for line in lines:
        stripped = line.strip()
        m = re.fullmatch(r"[^0-9]{0,8}(\d{1,7})(?:[.,:]([0-9]{1,2}))?[^0-9]{0,8}", stripped)
        if m:
            whole, frac = m.groups()
            value = float(f"{whole}.{frac}") if frac is not None else float(whole)
            add(value, 85, "standalone")

    # A bare amount can be embedded beside a payment label.
    for line in lines:
        if re.search(r"\b(?:amount|paid|sent|received|debited|credited|total|payment)\b", line, re.I):
            for token in re.findall(r"(?<!\d)\d{1,7}(?:[.]\d{1,2})?(?!\d)", line):
                add(_money_number(token), 80, "labelled_bare")

    # If OCR removed line breaks, search the whole OCR text for currency forms.
    flat = re.sub(r"\s+", " ", normalized)
    for pat in currency_patterns:
        for m in re.finditer(pat, flat, re.I):
            add(_money_number(m.group(1)), 95, "flat_labelled")

    # On some screenshots Tesseract reads the rupee glyph as a leading 2, so
    # visible ₹23 may become 223. Only apply this correction for the exact
    # selected ₹23 plan and only as a payment-candidate pattern.
    if expected is not None and abs(expected - 23.00) < 0.01:
        if re.search(r"(?<!\d)223(?:[.,:]00)?(?!\d)", normalized):
            add(23.00, 82, "rupee_glyph_ocr")
        if re.search(r"[¥₹]\s*23(?:[.,]00)?", normalized, re.I):
            add(23.00, 105, "currency_23")

    if not candidates:
        return None
    if expected is not None:
        matches = [c for c in candidates if abs(c[0] - expected) < 0.01]
        if matches:
            matches.sort(key=lambda c: c[1], reverse=True)
            return matches[0][0]
        return None
    candidates.sort(key=lambda c: c[1], reverse=True)
    return candidates[0][0]


def _parse_transaction_datetime(text, reference, expected_amount=None):
    """Extract the actual transaction date/time from repeated payment OCR text.

    Priority is given to a complete date+AM/PM time pair that appears together
    in the screenshot text. This avoids selecting an unrelated OCR clock value.
    """
    if not text:
        return None, False

    normalized = text.replace("\u00a0", " ")
    ref_ist = _aware_ist(reference) or reference

    month_names = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    date_time_pat = re.compile(
        rf"\b(\d{{1,2}})\s*,?\s*({month_names})\s*,?\s*(\d{{2,4}})\s*,?\s*"
        rf"(\d{{1,2}})\s*[:.]\s*(\d{{2}})(?:\s*[:.]\s*(\d{{2}}))?\s*(AM|PM|A\.M\.|P\.M\.)\b",
        re.I,
    )

    # First: exact date+time pairs. These are the strongest candidates because
    # the time is physically attached to the transaction date in the screenshot.
    exact = {}
    for m in date_time_pat.finditer(normalized):
        try:
            day = int(m.group(1))
            month = datetime.datetime.strptime(m.group(2)[:3].title(), "%b").month
            year = int(m.group(3))
            if year < 100:
                year += 2000
            raw_hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
            ap = m.group(7).upper().replace(".", "")
            if not (1 <= raw_hour <= 12 and minute <= 59 and second <= 59):
                continue
            hour = raw_hour % 12 + (12 if ap == "PM" else 0)
            dt = datetime.datetime(year, month, day, hour, minute, second)
            exact[dt] = exact.get(dt, 0) + 1
        except (ValueError, TypeError):
            continue

    if exact:
        # Most repeated exact transaction date+time wins. This directly handles
        # OCR output where the same screenshot is read multiple times.
        best_dt, best_count = max(exact.items(), key=lambda item: item[1])
        if best_count >= 2:
            return best_dt, True
        # Even a single complete date+time pair is stronger than a stray time.
        return best_dt, True

    # Fallback only when OCR did not produce a complete date+time pair.
    cleaned = re.sub(r"\s+", " ", normalized).strip()
    date_candidates = []
    for pat in (
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b",
        r"\b(\d{1,2})[.](\d{1,2})[.](\d{2,4})\b",
    ):
        for m in re.finditer(pat, cleaned):
            d, mo, y = map(int, m.groups())
            if y < 100:
                y += 2000
            try:
                date_candidates.append((datetime.date(y, mo, d), m.start()))
            except ValueError:
                pass

    month_pat = re.compile(
        rf"\b(\d{{1,2}})\s*,?\s*({month_names})\s*,?\s*(\d{{2,4}})?\b|"
        rf"\b({month_names})\s*,?\s*(\d{{1,2}})\s*,?\s*(\d{{2,4}})?\b", re.I)
    for m in month_pat.finditer(cleaned):
        try:
            if m.group(1):
                d, mon, year = int(m.group(1)), m.group(2), m.group(3)
            else:
                mon, d, year = m.group(4), int(m.group(5)), m.group(6)
            y = int(year) if year else ref_ist.year
            if y < 100:
                y += 2000
            mo = datetime.datetime.strptime(mon[:3].title(), "%b").month
            date_candidates.append((datetime.date(y, mo, d), m.start()))
        except (ValueError, TypeError):
            pass

    time_pat = re.compile(
        r"(?<!\d)(\d{1,2})\s*[:.]\s*(\d{2})(?:\s*[:.]\s*(\d{2}))?\s*(AM|PM|A\.M\.|P\.M\.)(?!\w)", re.I)
    times = {}
    for m in time_pat.finditer(cleaned):
        try:
            raw_hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
            ap = m.group(4).upper().replace(".", "")
            if not (1 <= raw_hour <= 12 and minute <= 59 and second <= 59):
                continue
            hour = raw_hour % 12 + (12 if ap == "PM" else 0)
            key = datetime.time(hour, minute, second)
            times[key] = times.get(key, 0) + 1
        except (ValueError, TypeError):
            pass

    if not date_candidates or not times:
        return None, bool(date_candidates)

    best_time = max(times.items(), key=lambda item: item[1])[0]
    best_date = max(date_candidates, key=lambda item: -abs(item[1] - cleaned.find(best_time.strftime("%I:%M"))))[0]
    return datetime.datetime.combine(best_date, best_time), True

def _payment_success_signal(text):
    """Detect a clear payment-success status from OCR text.

    Supports common wording used by GPay, PhonePe, Paytm, BHIM, UPI and bank
    payment apps. A clear failure/pending status always overrides positives.
    """
    lower = re.sub(r"\s+", " ", (text or "").lower()).strip()

    # These indicate that the payment is not successfully completed.
    negative = [
        "payment failed", "transaction failed", "transfer failed", "failed",
        "declined", "reversed", "cancelled", "canceled",
        "pending", "processing", "in progress",
    ]
    if any(word in lower for word in negative):
        return False

    # Common explicit success statuses across payment apps.
    positive = [
        "payment successful", "payment success",
        "payment completed", "payment complete",
        "transaction successful", "transaction success",
        "transaction completed", "transaction complete",
        "transfer successful", "transfer success",
        "transfer completed", "transfer complete",
        "paid successfully", "paid successfully",
        "payment done", "transaction done",
        "completed successfully", "successfully completed",
        "completed", "successful", "success",
    ]
    return any(phrase in lower for phrase in positive)


def _payment_match_result(order, ocr_text, received_at):
    """Validate payment evidence using amount + transaction date + success status.

    Transaction TIME is informational only and is never used as an approval gate.
    This deliberately removes the old time-window matching so OCR cannot reject
    a genuine payment because it selected/read a wrong transaction time.
    """
    expected = _expected_amount(order.get("plan_price"))
    found = _extract_amount(ocr_text, expected)
    amount_match = None if found is None else (expected is not None and abs(found - expected) < 0.01)

    parsed_tx_dt, parsed_confident = _parse_transaction_datetime(ocr_text, received_at, expected)
    tx_dt = parsed_tx_dt if parsed_confident else None
    reference = _aware_ist(received_at)
    date_match = None
    date_note = "Transaction date could not be read."
    if tx_dt is not None and reference is not None:
        tx_aware = tx_dt.astimezone(IST) if tx_dt.tzinfo else IST.localize(tx_dt)
        date_match = tx_aware.date() == reference.date()
        date_note = f"Transaction date: {tx_aware.date().isoformat()}"
    elif tx_dt is not None:
        date_match = True

    success_signal = _payment_success_signal(ocr_text) if ocr_text else None
    if not PAYMENT_OCR_ENABLED:
        return True, {"ocr_status": "disabled", "amount_found": found, "amount_match": None,
                      "transaction_at": tx_dt, "date_match": None,
                      "success_signal": None, "confidence": 0,
                      "date_note": "OCR checks disabled; sender/order matching used."}

    hard_fail = amount_match is False or date_match is False
    score = 0
    if amount_match:
        score += 60
    if date_match:
        score += 30
    if success_signal:
        score += 10
    passed = (not hard_fail) and amount_match is True and date_match is True and success_signal is True
    return passed, {
        "ocr_status": "matched" if passed else "manual_review",
        "amount_found": found, "amount_match": amount_match,
        "transaction_at": tx_dt, "date_match": date_match,
        "success_signal": success_signal, "confidence": min(score, 100),
        "date_note": date_note,
        "date_detected": tx_dt.date().isoformat() if tx_dt else None,
        "time_detected": tx_dt.strftime("%I:%M %p") if tx_dt else None,
    }


async def _ocr_payment_message(payment_client, message):
    """Run the original payment OCR safely without starving the bot.

    IMPORTANT: The OCR analysis itself is intentionally unchanged:
    - same 3600px image limit
    - same grayscale/contrast/upscaled/threshold variants
    - same PSM 6/11/12 passes for every variant
    - same image_to_data pass
    - same OCR output and perceptual hash generation

    The only changes here are resource-safety measures:
    - one screenshot OCR job at a time by default;
    - CPU-heavy Pillow/Tesseract work runs in a worker thread;
    - large temporary Pillow objects are released as soon as each phase ends.
    """
    if not PAYMENT_OCR_ENABLED:
        return "", "disabled", None, None

    async with _PAYMENT_OCR_SEMAPHORE:
        try:
            raw = await payment_client.download_media(message, in_memory=True)
            if raw is None:
                return "", "download_failed", None, None

            raw.seek(0)
            blob = raw.read()
            if not blob:
                return "", "download_failed", None, None

            sha256 = hashlib.sha256(blob).hexdigest()

            # Keep the download outside the CPU worker, then do all CPU-heavy
            # image/Tesseract work away from the main Pyrogram event loop.
            try:
                return await asyncio.to_thread(
                    _run_original_payment_ocr_sync,
                    blob,
                    sha256,
                )
            except Exception as exc:
                LOGGER.exception("Payment screenshot OCR worker failed: %s", exc)
                return "", "ocr_failed", sha256, None

        except Exception as exc:
            LOGGER.exception("Payment screenshot OCR failed: %s", exc)
            return "", "ocr_failed", None, None


def _run_original_payment_ocr_sync(blob, sha256):
    """Original OCR algorithm, executed outside the asyncio event loop."""
    tess_cmd = shutil.which("tesseract")
    if not tess_cmd:
        LOGGER.error("Tesseract executable was not found in PATH.")
        return "", "tesseract_missing", sha256, None

    pytesseract.pytesseract.tesseract_cmd = tess_cmd

    try:
        version = subprocess.run(
            [tess_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.splitlines()[0]
    except Exception:
        version = "unknown"
    LOGGER.info("Payment OCR using %s (%s)", tess_cmd, version)

    image = None
    gray = None
    enlarged = None
    deadline = time.monotonic() + PAYMENT_OCR_JOB_TIMEOUT_SECONDS
    try:
        image = ImageOps.exif_transpose(
            Image.open(io.BytesIO(blob)).convert("RGB")
        )
        image.thumbnail((3600, 3600), Image.Resampling.LANCZOS)

        gray = ImageOps.grayscale(image)

        # Preserve the original four OCR variants and their exact order.
        # Process them one at a time so temporary variants do not all remain
        # resident in RAM simultaneously.
        variant_builders = [
            ("gray", lambda: gray),
            ("contrast", lambda: ImageOps.autocontrast(gray)),
        ]

        enlarged = gray.resize(
            (max(1, gray.width * 2), max(1, gray.height * 2)),
            Image.Resampling.LANCZOS,
        )
        enlarged = ImageEnhance.Contrast(enlarged).enhance(1.6)
        enlarged = enlarged.filter(ImageFilter.SHARPEN)

        variant_builders.extend([
            ("upscaled", lambda: enlarged),
            (
                "threshold",
                lambda: ImageOps.autocontrast(enlarged).point(
                    lambda px: 255 if px >= 175 else 0
                ),
            ),
        ])

        texts = []
        errors = []
        successful_passes = 0

        # EXACTLY the original 4 x 3 OCR passes.
        for name, build_variant in variant_builders:
            if time.monotonic() >= deadline:
                errors.append("OCR job deadline reached")
                break
            variant = build_variant()
            try:
                for psm in (6, 11, 12):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        errors.append("OCR job deadline reached")
                        break
                    try:
                        value = pytesseract.image_to_string(
                            variant,
                            lang="eng",
                            config=f"--oem 3 --psm {psm}",
                            timeout=max(1, min(PAYMENT_OCR_PASS_TIMEOUT, int(remaining))),
                        )
                        if value and value.strip():
                            successful_passes += 1
                            texts.append(value.strip())
                    except Exception as exc:
                        errors.append(f"{name}/psm{psm}: {exc}")
                        LOGGER.warning(
                            "Payment OCR pass failed (%s/psm%s): %s",
                            name,
                            psm,
                            exc,
                        )
            finally:
                # Do not close shared gray/enlarged objects here.
                if name not in ("gray", "upscaled"):
                    try:
                        variant.close()
                    except Exception:
                        pass

        # Preserve the original OCR-data recovery pass.
        try:
            if time.monotonic() >= deadline:
                raise RuntimeError("OCR job deadline reached before recovery pass")
            data = pytesseract.image_to_data(
                ImageOps.autocontrast(enlarged),
                lang="eng",
                config="--oem 3 --psm 11",
                output_type=pytesseract.Output.DICT,
                timeout=PAYMENT_OCR_PASS_TIMEOUT,
            )
            words = [
                x.strip() for x in data.get("text", [])
                if x and x.strip()
            ]
            if words:
                texts.append(" ".join(words))
                successful_passes += 1
        except Exception as exc:
            errors.append(f"data: {exc}")
            LOGGER.warning("Payment OCR data pass failed: %s", exc)

        text = "\n".join(dict.fromkeys(texts))[:30000]

        # Preserve the original perceptual fingerprint algorithm.
        tiny = ImageOps.grayscale(image).resize(
            (32, 32), Image.Resampling.LANCZOS
        )
        pixels = list(tiny.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if px >= avg else "0" for px in pixels)
        perceptual = hex(int(bits, 2))[2:].zfill(256)
        try:
            tiny.close()
        except Exception:
            pass

        if text:
            LOGGER.info(
                "Payment OCR succeeded: %d passes, %d characters",
                successful_passes,
                len(text),
            )
            return text, "ok", sha256, perceptual

        if errors:
            LOGGER.error(
                "Payment OCR produced no text. First error: %s",
                errors[0],
            )
        else:
            LOGGER.error(
                "Payment OCR produced no text and no exception was reported."
            )
        return "", "ocr_no_text", sha256, perceptual

    except Exception as exc:
        LOGGER.exception("Payment screenshot OCR failed: %s", exc)
        return "", "ocr_failed", sha256, None
    finally:
        # Explicitly release large Pillow buffers after the OCR job.
        for obj in (gray, enlarged, image):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


async def _send_payment_result_with_screenshot(client, user_id, screenshot_message_id, text, order=None, reply_markup=None):
    """Send the final user result with the submitted screenshot as one message.

    The screenshot is copied into the payment chat with the localized final
    result as its caption. If Telegram rejects the copy/caption operation, a
    normal text result is used as a safe fallback.
    """
    try:
        source_chat = int((order or {}).get("payment_chat_id") or user_id)
        return await client.copy_message(
            chat_id=int(user_id),
            from_chat_id=source_chat,
            message_id=int(screenshot_message_id),
            caption=text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=reply_markup,
        )
    except Exception as exc:
        LOGGER.warning("Could not send final payment result with screenshot for %s: %s", user_id, exc)
        return await _send_user_temp(client, user_id, text, parse_mode=enums.ParseMode.HTML, reply_markup=reply_markup)


async def _activate_order(client, order, screenshot_message_id):
    """Grant Premium using the exact plan stored on the payment order.

    Older orders may contain a display name (for example ``01 WEEK``) instead
    of the internal plan key (``week``), so resolve both forms before touching
    the user or order. This prevents manual approval from falsely failing.
    """
    user_id = int(order["user_id"])
    now = _now()

    raw_plan = str(order.get("selected_plan") or "").strip()
    plan_key = _plan_key(raw_plan)
    if not plan_key:
        raw_lower = raw_plan.lower()
        for key, item in PREMIUM_PLANS.items():
            if raw_lower == str(item.get("name", "")).lower():
                plan_key = key
                break
    if not plan_key:
        # Final compatibility fallback for records that only preserved duration.
        raw_duration = str(order.get("plan_duration") or "").lower().strip()
        for key, item in PREMIUM_PLANS.items():
            if raw_duration == str(item.get("duration", "")).lower().strip():
                plan_key = key
                break
    if not plan_key or plan_key not in PREMIUM_PLANS:
        raise RuntimeError(f"Unknown Premium plan on payment order: {raw_plan or order.get('plan_duration')!r}")

    # Renewal rule: preserve remaining time. If current Premium is active,
    # add the selected duration to its existing expiry instead of overwriting it.
    current = await db.get_user(user_id)
    current_expiry = _naive_utc(current.get("expiry_time")) if current else None
    previous_premium_state = {
        "expiry_time": current.get("expiry_time") if current else None,
        "premium_plan": current.get("premium_plan") if current else None,
        "premium_plan_name": current.get("premium_plan_name") if current else None,
        "premium_price": current.get("premium_price") if current else None,
    }
    temporary_review = bool(order.get("temporary_review_access"))
    if (not temporary_review) and isinstance(current_expiry, datetime.datetime) and current_expiry > now:
        base = current_expiry
    else:
        base = now

    new_expiry = _expiry_from(base, plan_key)

    # This is the existing Premium access store used by the rest of the bot.
    await db.update_user({
        "id": user_id,
        "expiry_time": new_expiry,
        "premium_plan": plan_key,
        "premium_plan_name": order["plan_duration"],
        "premium_price": order["plan_price"],
    })
    await db.premium_orders.update_one(
        {"user_id": user_id, "screenshot_message_id": int(screenshot_message_id)},
        {"$set": {"payment_previous_premium_state": previous_premium_state}},
    )

    await db.set_order_activation(user_id, now, new_expiry)
    await db.premium_orders.update_one(
        {"user_id": user_id},
        {"$set": {
            "screenshot_message_id": int(screenshot_message_id),
            "selected_plan": plan_key,
            "payment_status": "manually_verified",
            "premium_status": "active",
            "temporary_review_access": False,
            "temporary_review_expires_at": None,
        }},
    )

    is_renewal = (not temporary_review) and isinstance(current_expiry, datetime.datetime) and current_expiry > now
    plan = PREMIUM_PLANS[plan_key]
    lang = await _user_language(user_id)
    if is_renewal:
        text = (
            f"♻️ <b>{_result_text(lang, 'renewed_title')}</b>\n\n"
            f"📦 {_result_text(lang, 'plan')}: <b>{escape(plan['name'])}</b>\n"
            f"⏳ {_result_text(lang, 'added')}: <b>{escape(plan['duration'])}</b>\n"
            f"📅 {_result_text(lang, 'new_expiry')}: {_fmt_dt(new_expiry)}\n"
            f"🟢 {_result_text(lang, 'status')}\n\n"
            f"{_result_text(lang, 'thanks_renew')}"
        )
    else:
        text = (
            f"✅ <b>{_result_text(lang, 'activated_title')}</b>\n\n"
            f"📦 {_result_text(lang, 'plan')}: <b>{escape(plan['name'])}</b>\n"
            f"⏳ {_result_text(lang, 'duration')}: <b>{escape(plan['duration'])}</b>\n"
            f"📅 {_result_text(lang, 'activated_at')}: {_fmt_dt(now)}\n"
            f"⏳ {_result_text(lang, 'expires')}: {_fmt_dt(new_expiry)}\n"
            f"🟢 {_result_text(lang, 'status')}\n\n"
            f"{_result_text(lang, 'thanks_purchase')}"
        )
    try:
        await _send_payment_result_with_screenshot(
            client, user_id, screenshot_message_id, text, order=order,
            reply_markup=_contact_admin_markup(lang),
        )
    except Exception as exc:
        LOGGER.warning("Could not send Premium activation to %s: %s", user_id, exc)

    try:
        await client.send_message(
            LOG_CHANNEL,
            f"#PREMIUM_PAYMENT_SUBMITTED\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Plan: {escape(plan['name'])}\n"
            f"Price: {escape(plan['price'])}\n"
            f"Screenshot message: <code>{screenshot_message_id}</code>\n"
            f"Payment status: <code>manually_verified</code>\n"
            f"Premium status: <code>active</code>\n"
            f"Expires: {_fmt_dt(new_expiry)}\n\n"
            "⚠️ Screenshot is a payment submission only. Manual transaction "
            "verification is still required.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as exc:
        LOGGER.warning("Could not write payment log: %s", exc)


async def process_payment_submission(payment_client, message):
    """Handle a payment screenshot through the verified Premium-order pipeline."""
    return await _process_payment_submission_impl(payment_client, message)

async def _process_payment_submission_impl(payment_client, message):
    """Handle a screenshot, verify sender/order and perform a soft OCR check."""
    sender = message.from_user
    if not sender:
        return

    user_id = int(sender.id)
    received_at = _now()
    media_kind = "photo" if message.photo else "document"
    file_id = None
    file_unique_id = None
    if message.photo:
        file_id = message.photo.file_id
        file_unique_id = message.photo.file_unique_id
    elif message.document:
        file_id = message.document.file_id
        file_unique_id = message.document.file_unique_id

    # First guard against Telegram/Pyrogram delivering the same screenshot
    # message more than once.  The payment_submissions collection has a unique
    # (chat_id, message_id) index, so an already-recorded message must be a
    # silent no-op rather than falling through to the unmatched-order warning.
    existing_submission = await db.get_payment_submission(user_id, message.id)
    if existing_submission:
        LOGGER.info("Ignoring duplicate payment screenshot message %s from %s", message.id, user_id)
        return

    # A selected Premium plan is the proof that the user followed the
    # purchase flow. Do NOT require the order to still be in exactly
    # ``waiting_for_payment`` here: another payment/review transition can
    # legitimately change that status before the screenshot is processed.
    # Only a truly missing/unconfigured order is unmatched.
    lang = await _user_language(user_id, sender)
    order = await db.get_premium_order(user_id)
    if order and not order.get("selected_plan"):
        order = None

    # Once the current order has already consumed a screenshot, require the
    # user to select a Premium plan again before another screenshot can enter
    # the pipeline. This prevents repeated screenshots from being treated as
    # fresh payments while still allowing a genuinely new order to proceed.
    if order and order.get("screenshot_message_id") and order.get("payment_status") != "waiting_for_payment":
        LOGGER.info(
            "Ignoring additional screenshot %s for user %s; current Premium order already has a payment screenshot",
            message.id, user_id,
        )
        return

    # If this user already has a screenshot attached to the current Premium
    # order (for example the first copy is already in manual review), do not
    # incorrectly call the next delivery an "unmatched" payment.  The order
    # has already entered the review pipeline, so the extra screenshot is
    # simply ignored.
    if not order:
        current_order = await db.get_premium_order(user_id)
        if current_order and current_order.get("screenshot_message_id"):
            LOGGER.info(
                "Ignoring additional screenshot %s for user %s; existing Premium payment is already under review",
                message.id, user_id,
            )
            return

    submission = {
        "user_id": user_id,
        "username": sender.username or "",
        "full_name": (sender.first_name or "") + ((" " + sender.last_name) if sender.last_name else ""),
        "payment_bot_message_id": int(message.id),
        "payment_chat_id": int(message.chat.id),
        "media_type": media_kind,
        "file_id": file_id,
        "file_unique_id": file_unique_id,
        "caption": message.caption or "",
        "received_at": received_at,
        "matched_order": bool(order),
        "status": "matched" if order else "unmatched",
        "review_status": "pending" if order else "not_required",
    }
    await db.record_payment_submission(submission)

    if not order:
        # Unmatched screenshots are NEVER sent to admins/log channels and do
        # not enter the Premium review pipeline. The only response is a
        # temporary user-facing notice which is deleted after 10 seconds.
        try:
            notice = await _reply_temp(
                message,
                _no_order_warning(lang),
                parse_mode=enums.ParseMode.HTML,
                delay=10,
            )
            LOGGER.info(
                "Unmatched payment screenshot %s from %s: user-only 10-second notice sent",
                message.id, user_id,
            )
        except Exception:
            pass
        return

    processing_message = None
    try:
        processing_message = await _reply_temp(
            message,
            _tr(lang, "progress_title") + "\n\n" +
            _tr(lang, "progress_body"),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        LOGGER.exception("Could not send payment processing notice")

    ocr_text, ocr_status, file_sha256, perceptual_hash = await _ocr_payment_message(payment_client, message)
    passed, check = _payment_match_result(order, ocr_text, received_at)

    # A perceptual image hash alone is too aggressive: two genuinely different
    # payment screenshots can look almost identical except for the transaction
    # time or other small text. Only treat an exact file match as an automatic
    # duplicate. Similar-image matches require the OCR transaction details to
    # agree as well, so a new payment with a different transaction time is not
    # incorrectly sent to manual review.
    duplicate = await db.find_duplicate_payment_submission(file_sha256, None, user_id, message.id)
    if duplicate:
        passed = False
        check["duplicate_suspected"] = True
    else:
        check["duplicate_suspected"] = False
    await db.update_payment_submission(
        user_id,
        message.id,
        {
            "ocr_status": check["ocr_status"],
            "ocr_text": ocr_text[:4000],
            "amount_found": check["amount_found"],
            "amount_match": check["amount_match"],
            "transaction_at": check["transaction_at"],
            "date_match": check.get("date_match"),
            "ocr_engine_status": ocr_status,
            "file_sha256": file_sha256,
            "perceptual_hash": perceptual_hash,
            "confidence": check.get("confidence"),
            "success_signal": check.get("success_signal"),
            "duplicate_suspected": check.get("duplicate_suspected", False),
        },
    )

    if not passed:
        await db.update_payment_submission(
            user_id, message.id,
            {"review_status": "manual_review_required"}
        )
        await db.update_order_payment_review(user_id, message.id, check)
        # Failed/uncertain screenshots still receive short temporary Premium
        # while the owner manually checks the submitted screenshot.
        review_expiry = await _grant_review_access(user_id, order, minutes=PAYMENT_MAX_DELAY_MINUTES)
        # Build an admin-only verification report. Keep the exact technical reason
        # visible to reviewers so they can understand why auto-approval stopped.
        reason = []
        if check["amount_match"] is False:
            reason.append("The detected amount does not match the selected plan.")
        elif check["amount_match"] is None:
            reason.append("The payment amount was not detected in the screenshot.")
        if check.get("date_match") is False:
            reason.append("Transaction date does not match the screenshot submission date.")
        elif check.get("date_match") is None:
            reason.append("Transaction date could not be read confidently.")
        if check.get("success_signal") is False:
            reason.append("A payment-success confirmation was not detected.")
        if check.get("duplicate_suspected"):
            reason.append("The exact same screenshot file was already submitted.")
        if check.get("ocr_status") == "disabled":
            reason.append("OCR verification is disabled, so automatic evidence checks were unavailable.")
        elif ocr_status == "download_failed":
            reason.append("The screenshot could not be downloaded for analysis.")
        elif ocr_status == "ocr_failed":
            reason.append("OCR analysis failed while reading this screenshot.")
        elif not reason:
            reason.append("The available evidence did not reach the automatic approval threshold.")

        amount_found = check.get("amount_found")
        tx_at = check.get("transaction_at")
        amount_result = "Matched" if check.get("amount_match") is True else ("Not matched" if check.get("amount_match") is False else "Not confidently detected")
        success_result = "Detected" if check.get("success_signal") is True else ("Not detected" if check.get("success_signal") is False else "Not available")
        duplicate_result = "Suspected duplicate" if check.get("duplicate_suspected") else "No duplicate detected"
        ocr_result = str(ocr_status or "unknown").replace("_", " ").title()
        confidence = check.get("confidence")
        confidence_text = f"{confidence}%" if isinstance(confidence, (int, float)) else "N/A"
        reasons_block = "\n".join(f"• {item}" for item in reason)

        sender_name = " ".join(part for part in [sender.first_name, sender.last_name] if part) or "Unknown"
        sender_username = f"@{sender.username}" if sender.username else "none"
        review_text = (
            "🟡 <b>Payment screenshot needs manual review</b>\n\n"
            f"👤 User: {escape(sender_name)}\n"
            f"🔗 Username: {escape(sender_username)}\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            f"📦 Plan: {escape(str(order.get('plan_duration', 'N/A')))}\n"
            f"💰 Expected amount: {escape(str(order.get('plan_price', 'N/A')))}\n"
            f"🆔 Screenshot message: <code>{message.id}</code>\n\n"
            "<b>🔎 Automatic analysis report</b>\n"
            f"• OCR engine: {escape(str(ocr_status or 'unknown').replace('_', ' ').title())}\n"
            f"• Analysis result: {escape(ocr_result)}\n"
            f"• Amount detected: {escape(str(amount_found) if amount_found is not None else 'NOT DETECTED')}\n"
            f"• Amount comparison: {escape(amount_result)}\n"
            f"• Date detected: {escape(tx_at.strftime('%d %B %Y') if tx_at else 'NOT DETECTED')}\n"
            f"• Time detected: {escape(tx_at.strftime('%I:%M %p') if tx_at else 'NOT DETECTED')}\n"
            f"• Transaction time detected (informational): {escape(tx_at.strftime('%I:%M %p') if tx_at else 'NOT DETECTED')}\n"
            f"• Payment-success signal: {escape(success_result)}\n"
            f"• Duplicate check: {escape(duplicate_result)}\n"
            f"• Verification confidence: {escape(confidence_text)}\n"
            f"• OCR text read: <code>{escape((ocr_text[:900] if ocr_text else 'NO TEXT READ'))}</code>\n\n"
            "<b>⚠️ Exact reason(s) for manual review</b>\n"
            f"{escape(reasons_block)}\n\n"
            "The selected Premium plan has been activated for this payment review. It is not permanent. Please review the screenshot and choose Approve or Reject."
        )
        review_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ APPROVE PAYMENT", callback_data=f"payapprove:{user_id}:{message.id}"),
                InlineKeyboardButton("❌ REJECT PAYMENT", callback_data=f"payreject:{user_id}:{message.id}"),
            ]
        ])
        for admin_id in _admins():
            try:
                await payment_client.copy_message(
                    admin_id,
                    message.chat.id,
                    message.id,
                    caption=review_text,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=review_buttons,
                )
            except Exception as exc:
                LOGGER.warning("Could not send manual payment review to %s: %s", admin_id, exc)
        try:
            plan_key = _plan_key(order.get("selected_plan"))
            plan = PREMIUM_PLANS.get(plan_key, {}) if plan_key else {}
            activated_at = order.get("review_started_at") or _now()
            lang = await _user_language(user_id, sender)
            user_text = (
                _tr(lang, "manual_title") + "\n\n"
                f"📦 {_result_text(lang, 'plan')}: <b>{escape(str(plan.get('name') or order.get('plan_duration', 'N/A')))}</b>\n"
                f"⏳ {_result_text(lang, 'duration')}: <b>{escape(str(plan.get('duration') or order.get('plan_duration', 'N/A')))}</b>\n"
                f"📅 {_result_text(lang, 'activated_at')}: {_fmt_dt(activated_at)}\n"
                f"⏳ {_result_text(lang, 'expires')}: {_fmt_dt(review_expiry)}\n"
                f"🟢 {_result_text(lang, 'status')}\n\n" +
                _tr(lang, "manual_body")
            )
            if processing_message:
                try:
                    await processing_message.delete()
                except Exception:
                    pass
            await _send_payment_result_with_screenshot(
                payment_client,
                user_id,
                message.id,
                user_text,
                order=order,
                reply_markup=_contact_admin_markup(lang),
            )
        except Exception:
            pass
        return

    await db.update_payment_submission(
        user_id, message.id,
        {"review_status": "auto_approved"}
    )
    claimed = await db.activate_premium_order(user_id, message.id)
    if not claimed:
        await db.update_payment_submission(
            user_id, message.id, {"status": "duplicate_after_activation"}
        )
        return

    try:
        await payment_client.copy_message(
            chat_id=LOG_CHANNEL,
            from_chat_id=message.chat.id,
            message_id=message.id,
        )
    except Exception as exc:
        LOGGER.warning("Could not copy payment screenshot to LOG_CHANNEL: %s", exc)

    # Auto-approved payments are presented to admins in the same review format
    # as manual payments: report first, then the exact original screenshot. The
    # only difference is that approval already happened, so only Reject remains.
    tx_at = check.get("transaction_at")
    sender_name = " ".join(part for part in [sender.first_name, sender.last_name] if part) or "Unknown"
    sender_username = f"@{sender.username}" if sender.username else "none"
    amount_found = check.get("amount_found")
    amount_result = "Matched" if check.get("amount_match") is True else ("Not matched" if check.get("amount_match") is False else "Not confidently detected")
    success_result = "Detected" if check.get("success_signal") is True else ("Not detected" if check.get("success_signal") is False else "Not available")
    confidence = check.get("confidence")
    confidence_text = f"{confidence}%" if isinstance(confidence, (int, float)) else "N/A"

    detected_report = (
        "🟢 <b>Payment automatically approved</b>\n\n"
        f"👤 User: {escape(sender_name)}\n"
        f"🔗 Username: {escape(sender_username)}\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📦 Plan: {escape(str(order.get('plan_duration', 'N/A')))}\n"
        f"💰 Expected amount: {escape(str(order.get('plan_price', 'N/A')))}\n"
        f"🆔 Screenshot message: <code>{message.id}</code>\n\n"
        "<b>🔎 Automatic analysis report</b>\n"
        f"• OCR engine: {escape(str(ocr_status or 'unknown').replace('_', ' ').title())}\n"
        "• Analysis result: Automatically approved\n"
        f"• Amount detected: {escape(str(amount_found) if amount_found is not None else 'NOT DETECTED')}\n"
        f"• Amount comparison: {escape(amount_result)}\n"
        f"• Date detected: {escape(tx_at.strftime('%d %B %Y') if tx_at else 'NOT DETECTED')}\n"
        f"• Time detected: {escape(tx_at.strftime('%I:%M %p') if tx_at else 'NOT DETECTED')}\n"
        f"• Transaction time detected (informational): {escape(tx_at.strftime('%I:%M %p') if tx_at else 'NOT DETECTED')}\n"
        f"• Payment-success signal: {escape(success_result)}\n"
        "• Duplicate check: No duplicate detected\n"
        f"• Verification confidence: {escape(confidence_text)}\n"
        f"• OCR text read: <code>{escape((ocr_text[:900] if ocr_text else 'NO TEXT READ'))}</code>\n\n"
        "The selected Premium plan has already been activated automatically. The screenshot is shown below. You can still reject this payment if the screenshot is wrong."
    )
    auto_reject_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ REJECT PAYMENT", callback_data=f"payreject:{user_id}:{message.id}")]
    ])
    for admin_id in _admins():
        try:
            await payment_client.copy_message(
                admin_id,
                message.chat.id,
                message.id,
                caption=detected_report,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=auto_reject_buttons,
            )
        except Exception as exc:
            LOGGER.warning("Could not send auto-approved payment review to %s: %s", admin_id, exc)

    if processing_message:
        try:
            await processing_message.delete()
        except Exception:
            pass
    await _activate_order(payment_client, claimed, message.id)
    try:
        await message.delete()
    except Exception:
        pass


async def _grant_review_access(user_id, order, minutes=None):
    """Grant the exact selected plan during manual payment review.

    This is never lifetime access. Reject removes only this payment-created
    Premium record so normal PM/group verification can continue unchanged.
    """
    now = _now()
    plan_key = _plan_key(order.get("selected_plan"))
    if not plan_key:
        raise ValueError("Invalid selected Premium plan for payment review")
    expires = _expiry_from(now, plan_key)
    current = await db.get_user(int(user_id))
    previous_premium_state = {
        "expiry_time": current.get("expiry_time") if current else None,
        "premium_plan": current.get("premium_plan") if current else None,
        "premium_plan_name": current.get("premium_plan_name") if current else None,
        "premium_price": current.get("premium_price") if current else None,
    }
    await db.update_user({
        "id": int(user_id),
        "expiry_time": expires,
        "premium_plan": _plan_key(order.get("selected_plan")) or order.get("selected_plan"),
        "premium_plan_name": order.get("plan_duration", "Review"),
        "premium_price": order.get("plan_price"),
    })
    await db.premium_orders.update_one(
        {"user_id": int(user_id)},
        {"$set": {
            "premium_status": "active",
            "temporary_review_access": True,
            "temporary_review_expires_at": None,
            "payment_status": "manual_review_required",
            "expires_at": expires,
            "review_started_at": now,
            "payment_previous_premium_state": previous_premium_state,
        }},
    )
    return expires


async def _notify_admins(client, text):
    for admin_id in _admins():
        try:
            await client.send_message(admin_id, text, parse_mode=enums.ParseMode.HTML)
        except Exception as exc:
            LOGGER.warning("Could not notify admin %s: %s", admin_id, exc)


@Client.on_callback_query(filters.regex(r"^buyplan_"), group=1)
async def select_premium_plan(client, query):
    plan_key = _plan_key(query.data.split("_", 1)[1])
    if not plan_key:
        return await query.answer("Invalid Premium plan.", show_alert=True)

    plan = PREMIUM_PLANS[plan_key]
    user = query.from_user
    order = await db.create_or_update_premium_order(
        user.id,
        user.username,
        plan_key,
        plan["duration"],
        plan["price"],
    )

    lang = await _user_language(user.id, user)
    buttons = []
    if PAYMENT_BOT_USERNAME:
        buttons.append([
            InlineKeyboardButton(
                _premium_flow_text(lang, "send"),
                url=f"https://t.me/{PAYMENT_BOT_USERNAME}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(_premium_flow_text(lang, "back"), callback_data="free"),
        InlineKeyboardButton(_premium_flow_text(lang, "close"), callback_data="close_data"),
    ])

    payment_text = _premium_flow_text(
        lang, "order",
        plan=escape(plan["name"]), duration=escape(plan["duration"]), price=escape(plan["price"]),
    )
    await query.message.edit_text(
        payment_text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )
    await query.answer(_premium_flow_text(lang, "selected"))


async def _saved_language(user_id):
    try:
        data = await db.get_user(int(user_id))
        saved = (data or {}).get("language") or (data or {}).get("language_code")
        return saved if saved in LANGUAGES else None
    except Exception:
        return None


@Client.on_message(filters.command("plan") & filters.private & filters.incoming)
async def user_plan_command(client, message):
    user = message.from_user
    if not user:
        return
    if not await _saved_language(user.id):
        return await message.reply_text(
            _tr("en", "language_title") + "\n\n" + _tr("en", "language_body"),
            reply_markup=_language_markup(),
            parse_mode=enums.ParseMode.HTML,
        )
    lang = await _user_language(user.id, user)
    rows = []
    keys = list(PREMIUM_PLANS)
    for i in range(0, len(keys), 2):
        row = []
        for key in keys[i:i + 2]:
            plan = PREMIUM_PLANS[key]
            row.append(InlineKeyboardButton(f"💳 {plan['name']} ₹{plan['price']}", callback_data=f"buyplan_{key}"))
        rows.append(row)
    await message.reply_text(_premium_flow_text(lang, "plans"), reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("pending"))
async def pending_payments(client, message):
    if message.from_user.id not in _admins():
        return await _reply_temp(message, "You are not authorized to use this command.")

    rows = []
    async for order in db.get_pending_manual_verifications():
        rows.append(
            f"👤 <code>{order['user_id']}</code> | "
            f"{escape(order.get('username') or 'no username')}\n"
            f"📦 {escape(order.get('plan_duration', 'N/A'))} | "
            f"💰 {escape(order.get('plan_price', 'N/A'))}\n"
            f"💳 Status: <code>{escape(order.get('payment_status', 'N/A'))}</code>\n"
            f"🖼️ Screenshot: <code>{order.get('screenshot_message_id', 'N/A')}</code>\n"
            f"⏳ Expires: {_fmt_dt(order.get('expires_at'))}\n"
        )
    if not rows:
        return await _reply_temp(message, "No Premium payments are waiting for manual verification.")
    text = "🧾 <b>Pending Manual Payment Verification</b>\n\n" + "\n".join(rows)
    await _reply_temp(message, text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("premium"))
async def premium_details(client, message):
    if message.from_user.id not in _admins():
        return await _reply_temp(message, "You are not authorized to use this command.")
    if len(message.command) != 2:
        return await _reply_temp(message, "Usage: /premium USER_ID")

    try:
        user_id = int(message.command[1])
    except ValueError:
        return await _reply_temp(message, "USER_ID must be numeric.")

    order = await db.get_premium_order(user_id)
    user = await db.get_user(user_id)
    if not order and not user:
        return await _reply_temp(message, "User was not found.")

    expiry = _naive_utc(user.get("expiry_time")) if user else None
    active = isinstance(expiry, datetime.datetime) and expiry > _now()
    text = (
        "👤 <b>Premium Details</b>\n\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"👤 Username: @{escape((order or {}).get('username') or 'unknown')}\n"
        f"📦 Plan: {escape((order or {}).get('plan_duration') or user.get('premium_plan_name', 'N/A'))}\n"
        f"💰 Price: {escape((order or {}).get('plan_price') or str(user.get('premium_price', 'N/A')))}\n"
        f"🟢 Premium: {'Active' if active else 'Expired/Inactive'}\n"
        f"📅 Activated: {_fmt_dt((order or {}).get('activated_at'))}\n"
        f"⏳ Expires: {_fmt_dt(expiry)}\n"
        f"💳 Payment: {escape((order or {}).get('payment_status', 'N/A'))}\n"
        f"🔎 Manually verified: {bool((order or {}).get('manually_verified', False))}\n"
        f"🖼️ Screenshot message: {escape(str((order or {}).get('screenshot_message_id', 'N/A')))}"
    )
    await _reply_temp(message, text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("approve"))
async def approve_payment(client, message):
    if message.from_user.id not in _admins():
        return await _reply_temp(message, "You are not authorized to use this command.")
    if len(message.command) != 2:
        return await _reply_temp(message, "Usage: /approve USER_ID")
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await _reply_temp(message, "USER_ID must be numeric.")

    order = await db.get_premium_order(user_id)
    if not order:
        return await _reply_temp(message, "No Premium payment record found for this user.")

    screenshot_id = order.get("screenshot_message_id")
    if not screenshot_id:
        return await _reply_temp(message, "No payment screenshot is attached to this order.")
    approved = await db.approve_manual_payment(user_id, screenshot_id)
    if not approved.modified_count:
        return await _reply_temp(message, "This payment is not waiting for manual approval.")
    try:
        fresh = await db.get_premium_order(user_id)
        await _activate_order(client, fresh, screenshot_id)
    except Exception as exc:
        LOGGER.exception("Command approval activation failed for %s", user_id)
        await db.premium_orders.update_one({"user_id": user_id, "screenshot_message_id": screenshot_id}, {"$set": {"payment_status": "manual_review_required", "premium_status": "inactive"}})
        return await _reply_temp(message, "Premium activation failed; the review was restored to pending.")
    await _reply_temp(message, f"✅ Payment approved and Premium activated for <code>{user_id}</code>.", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("remove"))
async def remove_premium_payment(client, message):
    if message.from_user.id not in _admins():
        return await _reply_temp(message, "You are not authorized to use this command.")
    if len(message.command) != 2:
        return await _reply_temp(message, "Usage: /remove USER_ID")
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await _reply_temp(message, "USER_ID must be numeric.")

    result = await db.remove_premium_access(user_id)
    if not result:
        return await _reply_temp(message, "Premium user was not found.")
    await db.set_subscription_expired(user_id)
    await _reply_temp(message, f"❌ Premium access removed for <code>{user_id}</code>.", parse_mode=enums.ParseMode.HTML)
    try:
        await _send_user_temp(
            client,
            user_id,
            "❌ <b>Premium Plan Removed</b>\n\n"
            "Your Premium access has been removed by an administrator.\n"
            "If this was related to payment verification, please contact the admin.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        pass


@Client.on_message(filters.command("expire"))
async def expire_now(client, message):
    if message.from_user.id not in _admins():
        return await _reply_temp(message, "You are not authorized to use this command.")
    await run_expiry_check(client, notify=True)
    await _reply_temp(message, "✅ Premium expiry check completed.")


@Client.on_message(filters.command("renew"))
async def manual_renew(client, message):
    if message.from_user.id not in _admins():
        return await _reply_temp(message, "You are not authorized to use this command.")
    if len(message.command) != 3:
        return await _reply_temp(message, 
            "Usage: /renew USER_ID PLAN\n"
            "PLAN: week, month, 3month, 6month, year, lifetime"
        )
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await _reply_temp(message, "USER_ID must be numeric.")
    plan_key = _plan_key(message.command[2])
    if not plan_key:
        return await _reply_temp(message, "Unknown plan.")

    now = _now()
    user = await db.get_user(user_id)
    current = _naive_utc(user.get("expiry_time")) if user else None
    if not isinstance(current, datetime.datetime) or current <= now:
        base = now
    else:
        base = current
    new_expiry = _expiry_from(base, plan_key)
    plan = PREMIUM_PLANS[plan_key]

    await db.update_user({
        "id": user_id,
        "expiry_time": new_expiry,
        "premium_plan": plan_key,
        "premium_plan_name": plan["duration"],
        "premium_price": plan["price"],
    })
    await db.premium_orders.update_one(
        {"user_id": user_id},
        {"$set": {
            "selected_plan": plan_key,
            "plan_duration": plan["duration"],
            "plan_price": plan["price"],
            "premium_status": "active",
            "payment_status": "manually_renewed",
            "activated_at": now,
            "expires_at": new_expiry,
            "reminder_sent": False,
            "manually_verified": True,
            "manually_verified_at": now,
        }},
        upsert=True,
    )
    await _reply_temp(message, 
        f"♻️ Premium renewed for <code>{user_id}</code>.\n"
        f"📦 Plan: {escape(plan['name'])}\n"
        f"⏳ New expiry: {_fmt_dt(new_expiry)}",
        parse_mode=enums.ParseMode.HTML,
    )


async def run_expiry_check(client, notify=True):
    now = _now()

    # New payment/subscription records.
    cursor = db.premium_orders.find({
        "premium_status": "active",
        "expires_at": {"$lte": now},
    })
    async for order in cursor:
        user_id = int(order["user_id"])
        await db.remove_premium_access(user_id)
        await db.set_subscription_expired(user_id, order.get("expires_at") or now)
        if notify:
            try:
                await _send_user_temp(
                    client,
                    user_id,
                    (lambda lang: _tr(lang, "expired") + "\n\n"
                     f"📦 {_result_text(lang, 'plan')}: {escape(order.get('plan_duration', 'Premium'))}\n"
                     f"📅 {_result_text(lang, 'expires')}: {_fmt_dt(order.get('expires_at') or now)}\n"
                     f"🔴 {_expiry_label(lang, 'status')}"
                    )(await _user_language(user_id)),
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as exc:
                LOGGER.warning("Could not send expiry notice to %s: %s", user_id, exc)

    # Four-day reminder. On restart, a missed exact moment is recovered by
    # sending once while the subscription is still active and inside the window.
    four_days = datetime.timedelta(days=4)
    cursor = db.premium_orders.find({
        "premium_status": "active",
        "reminder_sent": {"$ne": True},
        "expires_at": {"$gt": now},
    })
    async for order in cursor:
        expires_at = _naive_utc(order.get("expires_at"))
        if not isinstance(expires_at, datetime.datetime):
            continue
        if expires_at - now <= four_days:
            user_id = int(order["user_id"])
            try:
                await _send_user_temp(
                    client,
                    user_id,
                    (lambda lang: _tr(lang, "expiring") + "\n\n"
                     f"📦 {_result_text(lang, 'plan')}: {escape(order.get('plan_duration', 'Premium'))}\n"
                     f"⏳ {_result_text(lang, 'duration')}: {_expiry_label(lang, 'remaining')}\n"
                     f"📅 {_result_text(lang, 'expires')}: {_fmt_dt(expires_at)}"
                    )(await _user_language(user_id)),
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as exc:
                LOGGER.warning("Could not send 4-day reminder to %s: %s", user_id, exc)
            await db.mark_reminder_sent(user_id)

    # Legacy/manual Premium records not created through the payment flow.
    # This keeps the existing /add_premium feature working exactly as before.
    legacy = await db.get_expired(now)
    for user in legacy:
        user_id = int(user["id"])
        current = await db.get_premium_order(user_id)
        # Don't overwrite a newer active payment subscription.
        if current and current.get("premium_status") == "active":
            continue
        expiry = _naive_utc(user.get("expiry_time"))
        await db.remove_premium_access(user_id)
        if notify:
            try:
                target = await client.get_users(user_id)
                await _send_user_temp(
                    client,
                    user_id,
                    (lambda lang: f"<b>{target.mention}</b>\n\n" + _tr(lang, "expired"))(await _user_language(user_id, target)),
                    parse_mode=enums.ParseMode.HTML,
                )
                await client.send_message(
                    LOG_CHANNEL,
                    f"<b>#Premium_Expire\n\nUser name: {target.mention}\n"
                    f"User id: <code>{user_id}</code></b>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as exc:
                LOGGER.warning("Could not send legacy expiry notice for %s: %s", user_id, exc)


async def premium_expiry_worker(client):
    while True:
        try:
            await run_expiry_check(client, notify=True)
        except Exception:
            LOGGER.exception("Premium expiry checker failed; retrying.")
        await asyncio.sleep(10)


def register_payment_bot_handlers(payment_client):
    @payment_client.on_callback_query(filters.regex(r"^pay(approve|reject):"))
    async def manual_payment_review_callback(client, query):
        if not query.from_user or query.from_user.id not in _admins():
            return await query.answer("You are not authorized.", show_alert=True)

        parts = query.data.split(":")
        try:
            action = parts[0]
            user_id = int(parts[1])
            screenshot_message_id = int(parts[2]) if len(parts) > 2 else None
        except (ValueError, IndexError):
            return await query.answer("Invalid payment request.", show_alert=True)

        submission = await db.get_payment_submission(user_id, screenshot_message_id)
        if not submission:
            return await query.answer("This payment screenshot was not found.", show_alert=True)

        if action == "payapprove":
            # Lock this exact screenshot while activation is running. Do NOT mark
            # it approved until Premium access and the user notification succeed.
            result = await db.claim_payment_review(user_id, screenshot_message_id, "processing")
            if not result.modified_count:
                current = await db.get_payment_submission(user_id, screenshot_message_id)
                status = ((current or {}).get("review_status") or "processed").replace("_", " ")
                return await query.answer(f"This screenshot is already {status}.", show_alert=True)

            order = await db.get_premium_order(user_id)
            if not order or int(order.get("screenshot_message_id") or -1) != screenshot_message_id:
                await db.update_payment_submission(
                    user_id, screenshot_message_id, {"review_status": "manual_review_required"}
                )
                return await query.answer("The matching payment order changed. Review was kept pending.", show_alert=True)

            # The exact screenshot must still belong to a pending manual-review
            # order. Activate Premium first; only then finalize the review as approved.
            approved = await db.approve_manual_payment(user_id, screenshot_message_id)
            if not approved.modified_count:
                await db.update_payment_submission(
                    user_id, screenshot_message_id, {"review_status": "manual_review_required"}
                )
                return await query.answer("The order could not be approved. Review is still pending.", show_alert=True)

            try:
                order = await db.get_premium_order(user_id)
                if not order:
                    raise RuntimeError("Premium order disappeared during approval")

                # The selected plan was already activated for this exact manual review.
                # Approval confirms the payment only; it must not restart or extend expiry.
                if not (
                    order.get("temporary_review_access") is True
                    and str(order.get("premium_status") or "").lower() == "active"
                ):
                    await _activate_order(client, order, screenshot_message_id)
                else:
                    await db.premium_orders.update_one(
                        {"user_id": user_id, "screenshot_message_id": screenshot_message_id},
                        {"$set": {
                            "payment_status": "manually_verified",
                            "temporary_review_access": False,
                            "temporary_review_expires_at": None,
                        }},
                    )
                    try:
                        await _send_payment_result_with_screenshot(
                            client,
                            user_id,
                            screenshot_message_id,
                            f"✅ <b>{_result_text(await _user_language(user_id), 'approved_title')}</b>\n\n"
                            f"{_result_text(await _user_language(user_id), 'approved_body')}",
                            order=order,
                        )
                    except Exception:
                        pass
            except Exception as exc:
                LOGGER.exception("Manual Premium activation failed for %s", user_id)
                # Never leave a review falsely approved when activation failed.
                await db.premium_orders.update_one(
                    {"user_id": user_id, "screenshot_message_id": screenshot_message_id},
                    {"$set": {
                        "payment_status": "manual_review_required",
                        "premium_status": "inactive",
                    }},
                )
                await db.update_payment_submission(
                    user_id, screenshot_message_id,
                    {"review_status": "manual_review_required", "approval_error": str(exc)[:500]},
                )
                return await query.answer("Premium activation failed. Review was restored to pending.", show_alert=True)

            await db.update_payment_submission(
                user_id, screenshot_message_id,
                {"review_status": "approved", "approval_error": None},
            )
            try:
                await client.delete_messages(user_id, screenshot_message_id)
            except Exception:
                pass
            text = (
                f"✅ <b>Payment approved</b>\n\n"
                f"User ID: <code>{user_id}</code>\n"
                "Premium has been activated successfully."
            )
        else:
            # Auto-approved screenshots can also be rejected later, so claim
            # both manual-review and auto-approved review states atomically.
            result = await db.payment_submissions.update_one(
                {
                    "user_id": user_id,
                    "payment_bot_message_id": screenshot_message_id,
                    "review_status": {"$in": ["pending", "manual_review_required", "auto_approved"]},
                },
                {"$set": {"review_status": "rejected", "reviewed_at": _now()}},
            )
            if not result.modified_count:
                current = await db.get_payment_submission(user_id, screenshot_message_id)
                status = ((current or submission).get("review_status") or "processed").replace("_", " ")
                return await query.answer(f"This screenshot was already {status}.", show_alert=True)

            # Reject only this exact payment/order and remove only the Premium
            # access created by this payment. No PM/group verification state,
            # user identity or normal bot access data is touched.
            order = await db.get_premium_order(user_id)
            if order and int(order.get("screenshot_message_id") or -1) == screenshot_message_id:
                await db.premium_orders.update_one(
                    {"user_id": user_id, "screenshot_message_id": screenshot_message_id},
                    {"$set": {
                        "payment_status": "manually_rejected",
                        "premium_status": "inactive",
                        "rejected_at": _now(),
                    }},
                )
                previous = order.get("payment_previous_premium_state") or {}
                # Remove only access created/changed by this exact payment.
                # If the user already had Premium before this payment, restore it.
                restore = {"id": user_id, "expiry_time": previous.get("expiry_time")}
                if previous.get("premium_plan") is not None:
                    restore["premium_plan"] = previous.get("premium_plan")
                if previous.get("premium_plan_name") is not None:
                    restore["premium_plan_name"] = previous.get("premium_plan_name")
                if previous.get("premium_price") is not None:
                    restore["premium_price"] = previous.get("premium_price")
                await db.update_user(restore)
            try:
                await _send_payment_result_with_screenshot(
                    client,
                    user_id,
                    screenshot_message_id,
                    f"❌ <b>{_result_text(await _user_language(user_id), 'rejected_title')}</b>\n\n"
                    f"{_result_text(await _user_language(user_id), 'rejected_body')}",
                    order=order,
                    reply_markup=_contact_admin_markup(await _user_language(user_id),),
                )
            except Exception:
                pass
            try:
                await client.delete_messages(user_id, screenshot_message_id)
            except Exception:
                pass
            text = (
                f"❌ <b>Payment rejected</b>\n\n"
                f"User ID: <code>{user_id}</code>"
            )

        await query.answer("Payment review completed.")
        try:
            await query.message.edit_text(
                text,
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

    @payment_client.on_message(
        filters.private & (filters.photo | filters.document)
    )
    async def payment_screenshot_handler(client, message):
        if message.document and not (
            (message.document.mime_type or "").lower().startswith("image/")
        ):
            return
        try:
            await process_payment_submission(client, message)
        except Exception:
            LOGGER.exception("Payment screenshot processing failed.")
            try:
                await _reply_temp(message,
                    _PROCESSING_ERROR_I18N.get(await _user_language(message.from_user.id, message.from_user), _PROCESSING_ERROR_I18N["en"])
                )
            except Exception:
                pass
