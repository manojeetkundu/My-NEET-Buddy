
#!/usr/bin/env python3
"""
NEET Sathi Telegram Bot
GitHub + Render deployment
OpenAI API version
"""

import os
import sys
import io
import json
import random
import logging
import threading
import base64
from typing import Optional

from flask import Flask, jsonify

from telegram import (
    Update,
    Poll,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# OpenAI SDK
from openai import OpenAI

# PDF parser
from pypdf import PdfReader

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("NEET_Bot")


# ==========================================
# 1. FLASK HEALTH SERVER
# ==========================================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return """
    <html>
      <head><title>NEET Sathi Bot</title></head>
      <body style="font-family: sans-serif; text-align: center; padding: 40px; background: #0f172a; color: white;">
        <h1>🩺 NEET Sathi AI Telegram Bot is Running Live!</h1>
        <p>Bot Status: <strong>Online</strong></p>
        <p>Render Health Check: <strong>Passed (200 OK)</strong></p>
      </body>
    </html>
    """, 200


@web_app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "neet-telegram-bot"}), 200


def start_dummy_server():
    """Run Flask in a background thread for Render health checks."""
    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting Flask health server on port %s...", port)
    web_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# ==========================================
# 2. OPENAI CONFIGURATION
# ==========================================

TELEGRAM_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN")
    or os.environ.get("TELEGRAM_TOKEN")
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

openai_client = None

if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)


HINDI_NEET_SYSTEM_INSTRUCTION = """आप 'NEET साथी AI' (NEET Sathi AI) हैं — भारत के सर्वश्रेष्ठ AIIMS-ग्रेड NEET UG पर्सनल ट्यूटर और मेंटर।
आपका उद्देश्य हिंदी माध्यम और हिंग्लिश के छात्रों को फिजिक्स, केमिस्ट्री, बॉटनी और जूलॉजी में NEET UG की तैयारी में मदद करना है।

महत्वपूर्ण निर्देश:

1. भाषा शैली:
   - छात्र हिंदी में पूछे तो सरल हिंदी में समझाएं।
   - वैज्ञानिक और चिकित्सीय शब्दों के साथ अंग्रेजी नाम भी लिखें।
   - छात्र Hinglish में पूछे तो मित्रवत Hinglish में उत्तर दें।
   - सकारात्मक और उत्साहवर्धक लहजा रखें।

2. फोटो और डायग्राम:
   - प्रश्न को पहचानें और संभव हो तो NCERT कक्षा व अध्याय बताएं।
   - मुख्य सूत्र और चरणबद्ध हल समझाएं।
   - सही विकल्प स्पष्ट रूप से बताएं।
   - सामान्य गलती या NEET Trap की चेतावनी दें।

3. Telegram formatting:
   - साफ बुलेट पॉइंट्स, बोल्ड हेडिंग्स और उपयोगी इमोजी प्रयोग करें।
   - Telegram Markdown के अनुकूल उत्तर दें।
"""


def ask_openai(
    prompt: str,
    system_instruction: str = HINDI_NEET_SYSTEM_INSTRUCTION,
    max_tokens: int = 1800,
) -> str:
    """Generate a text response using OpenAI."""
    if openai_client is None:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    response = openai_client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
    )

    return response.choices[0].message.content or ""


# ==========================================
# 3. SAMPLE NEET PYQ BANK
# ==========================================

SAMPLE_PYQS = [
    {
        "subject": "Botany (वनस्पति विज्ञान)",
        "chapter": "उच्च पादपों में प्रकाश-संश्लेषण",
        "question": "C4 पौधों में प्राथमिक CO2 ग्राही (Primary CO2 acceptor) कौन सा अणु होता है?",
        "options": [
            "फॉस्फोइनॉल पाइरूवेट (PEP)",
            "राइबुलोज 1,5-बिसफॉस्फेट (RuBP)",
            "ऑक्सेलोएसिटिक अम्ल (OAA)",
            "फॉस्फोग्लिसरिक अम्ल (PGA)",
        ],
        "correct_id": 0,
        "explanation": "C4 पौधों में मीसोफिल कोशिकाओं में 3-कार्बन युक्त अणु PEP प्राथमिक CO2 ग्राही होता है। एंजाइम: PEP carboxylase। (NCERT Class 11, Ch-13)",
    },
    {
        "subject": "Physics (भौतिक विज्ञान)",
        "chapter": "धारा विद्युत (Current Electricity)",
        "question": "एक तार को खींचकर उसकी लंबाई में 10% की वृद्धि की जाती है। इसके प्रतिरोध में लगभग कितने प्रतिशत की वृद्धि होगी?",
        "options": ["21%", "10%", "20%", "15%"],
        "correct_id": 0,
        "explanation": "आयतन नियत रहता है, इसलिए R ∝ L²। नया प्रतिरोध R' = (1.1)²R = 1.21R। अतः 21% वृद्धि होगी। (NCERT Class 12, Ch-3)",
    },
    {
        "subject": "Chemistry (रसायन विज्ञान)",
        "chapter": "एल्डिहाइड, कीटोन एवं कार्बोक्सिलिक अम्ल",
        "question": "बेंजेल्डिहाइड को सांद्र जलीय NaOH के साथ गर्म करने पर कौन सी अभिक्रिया होती है?",
        "options": [
            "कैनिजारो अभिक्रिया (Cannizzaro Reaction)",
            "एल्डोल संघनन (Aldol Condensation)",
            "क्लीमेंसन अपचयन (Clemmensen Reduction)",
            "HVZ अभिक्रिया",
        ],
        "correct_id": 0,
        "explanation": "बेंजेल्डिहाइड में α-हाइड्रोजन नहीं होता। इसलिए सांद्र क्षार की उपस्थिति में Cannizzaro Reaction होती है। (NCERT Class 12, Ch-12)",
    },
    {
        "subject": "Zoology (प्राणी विज्ञान)",
        "chapter": "रासायनिक समन्वय तथा एकीकरण",
        "question": "न्यूरोहाइपोफाइसिस (पश्च पीयूष ग्रंथि) से कौन सा हार्मोन स्रावित होता है जो वृक्क में जल के पुनरावशोषण को प्रेरित करता है?",
        "options": [
            "वेसोप्रेसिन (ADH)",
            "एल्डोस्टेरोन (Aldosterone)",
            "ऑक्सीटोसिन (Oxytocin)",
            "ANF",
        ],
        "correct_id": 0,
        "explanation": "ADH हाइपोथैलेमस में संश्लेषित होता है और पश्च पीयूष ग्रंथि से स्रावित होकर वृक्क में जल का पुनरावशोषण बढ़ाता है। (NCERT Class 11, Ch-22)",
    },
]

user_stats = {}


# ==========================================
# 4. TELEGRAM BOT HANDLERS
# ==========================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    name = user.first_name if user else "भावी डॉक्टर"

    welcome_text = f"""👋 **नमस्ते {name} जी! NEET साथी AI में आपका स्वागत है!** 🩺

मैं आपका 24/7 समर्पित **NEET UG ट्यूटर** हूँ। फिजिक्स के कठिन न्यूमेरिकल्स हों, केमिस्ट्री की रिएक्शन्स या बायोलॉजी के NCERT डायग्राम्स — मैं हर डाउट को हिंदी और हिंग्लिश में आसान बनाकर समझाऊंगा।

✨ **आप क्या कर सकते हैं?**
📸 **फोटो डाउट:** किसी भी प्रश्न या डायग्राम की फोटो भेजें
📝 **टेक्स्ट डाउट:** कोई भी सवाल हिंदी या Hinglish में टाइप करें
📄 **PDF से क्विज़:** अपने नोट्स की PDF भेजें, मैं QuizBot बना दूंगा
🎯 **PYQ टेस्ट:** पिछले वर्षों के प्रश्न हल करें

👇 **नीचे दिए गए बटन्स पर क्लिक करें:**"""

    keyboard = [
        [
            InlineKeyboardButton(
                "🎯 NEET PYQ टेस्ट",
                callback_data="btn_pyq",
            ),
            InlineKeyboardButton(
                "📊 मेरा प्रोग्रेस (Stats)",
                callback_data="btn_stats",
            ),
        ],
        [
            InlineKeyboardButton(
                "📅 650+ स्टडी प्लान",
                callback_data="btn_plan",
            ),
            InlineKeyboardButton(
                "💡 टॉपर टिप्स",
                callback_data="btn_tips",
            ),
        ],
    ]

    await update.effective_message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    help_text = """📖 **NEET साथी बॉट कमांड गाइड:**

/start - बॉट का मुख्य मेनू खोलें
/pyq - NEET के पिछले वर्षों के प्रश्नों का लाइव क्विज़
/quiz - रैंडम अभ्यास प्रश्न (Poll Quiz)
/studyplan - 650+ अंक के लिए व्यक्तिगत टाइम-टेबल
/stats - अपने सही और गलत प्रश्नों का रिकॉर्ड देखें
/help - सभी कमांड्स की सूची"""

    await update.effective_message.reply_text(
        help_text,
        parse_mode="Markdown",
    )


async def pyq_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    q_data = random.choice(SAMPLE_PYQS)
    short_expl = q_data["explanation"][:190]

    await context.bot.send_poll(
        chat_id=update.effective_chat.id,
        question=f"[{q_data['subject']}]\n{q_data['question']}",
        options=q_data["options"],
        type=Poll.QUIZ,
        correct_option_id=q_data["correct_id"],
        explanation=short_expl,
        is_anonymous=False,
    )


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    user_id = user.id if user else 0

    data = user_stats.get(
        user_id,
        {"attempted": 12, "correct": 10},
    )

    attempted = data["attempted"]
    correct = data["correct"]
    accuracy = round((correct / attempted) * 100) if attempted > 0 else 0
    estimated_marks = (correct * 4) - ((attempted - correct) * 1)

    stats_msg = f"""📊 **आपकी NEET तैयारी रिपोर्ट:**

🎯 **कुल हल किए गए प्रश्न:** {attempted}
✅ **सही उत्तर:** {correct} (+{correct * 4} अंक)
❌ **गलत उत्तर:** {attempted - correct} (-{attempted - correct} माइनस मार्किंग)
📈 **सटीकता (Accuracy):** {accuracy}%
🏆 **अनुमानित स्कोर:** {estimated_marks} अंक"""

    await update.effective_message.reply_text(
        stats_msg,
        parse_mode="Markdown",
    )


async def studyplan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "⏳ आपके लिए हिंदी माध्यम का आदर्श NEET स्टडी प्लान तैयार हो रहा है..."
    )

    try:
        prompt = (
            "NEET UG के हिंदी माध्यम छात्र के लिए 650+ स्कोर का "
            "प्रैक्टिकल और प्रभावी टाइमटेबल तथा हाई-वेटेज अध्यायों "
            "की सूची तैयार करें। सरल हिंदी और बुलेट पॉइंट्स में लिखें।"
        )

        plan_text = ask_openai(prompt, max_tokens=2200)

        await update.effective_message.reply_text(
            plan_text or "प्लान तैयार करने में असमर्थ।",
            parse_mode="Markdown",
        )

    except Exception:
        logger.exception("Study plan generation failed")
        await update.effective_message.reply_text(
            "⚠️ स्टडी प्लान बनाने में समस्या आई। कृपया बाद में प्रयास करें।"
        )


async def text_doubt_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    user_text = message.text if message else None

    if not user_text:
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )

    try:
        prompt = f"""छात्र का NEET डाउट:
"{user_text}"

कृपया NCERT अनुसार चरणबद्ध हल और सही उत्तर दें।
सरल हिंदी या Hinglish में उत्तर दें."""

        reply = ask_openai(prompt)

        await message.reply_text(
            reply or "उत्तर प्राप्त नहीं हो सका।",
            parse_mode="Markdown",
        )

    except Exception:
        logger.exception("Text doubt generation failed")
        await message.reply_text(
            "⚠️ डाउट हल करने में समस्या आई। कृपया बाद में प्रयास करें।"
        )


async def photo_doubt_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    photos = message.photo if message else None

    if not photos:
        return

    photo = photos[-1]
    caption = message.caption or "कृपया इस चित्र/प्रश्न को हल करें।"

    await message.reply_text(
        "📷 फोटो मिल गई है! विश्लेषण किया जा रहा है..."
    )

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing",
    )

    try:
        file_obj = await context.bot.get_file(photo.file_id)
        image_bytes = await file_obj.download_as_bytearray()

        encoded_image = base64.b64encode(
            bytes(image_bytes)
        ).decode("utf-8")

        image_url = f"data:image/jpeg;base64,{encoded_image}"

        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": HINDI_NEET_SYSTEM_INSTRUCTION,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "छात्र द्वारा भेजी गई NEET प्रश्न की फोटो। "
                                f"टिप्पणी: {caption}\n"
                                "कृपया NCERT अनुसार हल और सही विकल्प बताएं।"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                },
            ],
            max_tokens=2200,
        )

        answer = response.choices[0].message.content or "विश्लेषण विफल रहा।"

        await message.reply_text(
            answer,
            parse_mode="Markdown",
        )

    except Exception:
        logger.exception("Photo analysis failed")
        await message.reply_text(
            "⚠️ फोटो विश्लेषण में त्रुटि आई। "
            "कृपया स्पष्ट फोटो भेजकर फिर प्रयास करें।"
        )


async def document_pdf_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    doc = message.document if message else None

    if not doc or not doc.file_name.lower().endswith(".pdf"):
        return

    await message.reply_text(
        f"📄 '{doc.file_name}' से QuizBot प्रश्न बनाए जा रहे हैं..."
    )

    try:
        file_obj = await context.bot.get_file(doc.file_id)
        pdf_bytes = await file_obj.download_as_bytearray()

        reader = PdfReader(io.BytesIO(bytes(pdf_bytes)))
        extracted_text = ""

        for page in reader.pages[:4]:
            extracted_text += page.extract_text() or ""

        if not extracted_text.strip():
            await message.reply_text(
                "PDF से टेक्स्ट नहीं मिला। यह scanned PDF हो सकती है।"
            )
            return

        prompt = f"""इस सामग्री से 3 NEET MCQs बनाएं।

सामग्री:
{extracted_text[:4000]}

JSON object के रूप में उत्तर दें:
{{
  "questions": [
    {{
      "question": "प्रश्न",
      "options": ["विकल्प A", "विकल्प B", "विकल्प C", "विकल्प D"],
      "correct_index": 0,
      "explanation": "व्याख्या"
    }}
  ]
}}"""

        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        HINDI_NEET_SYSTEM_INSTRUCTION
                        + "\nReturn valid JSON only. Do not include Markdown fences."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=2200,
        )

        response_text = response.choices[0].message.content or "{}"
        result = json.loads(response_text)
        questions = result.get("questions", [])

        if not questions:
            await message.reply_text(
                "PDF से quiz questions नहीं बन पाए।"
            )
            return

        for q in questions[:3]:
            options = q.get("options", [])

            if len(options) < 2:
                continue

            correct_index = int(q.get("correct_index", 0))
            correct_index = max(
                0,
                min(correct_index, len(options[:4]) - 1),
            )

            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=q["question"][:300],
                options=options[:4],
                type=Poll.QUIZ,
                correct_option_id=correct_index,
                explanation=q.get("explanation", "")[:190],
                is_anonymous=False,
            )

    except Exception:
        logger.exception("PDF quiz generation failed")
        await message.reply_text(
            "⚠️ PDF से quiz बनाने में समस्या आई। "
            "कृपया text-based PDF भेजकर फिर प्रयास करें।"
        )


async def button_callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "btn_pyq":
        await pyq_command(update, context)

    elif data == "btn_stats":
        await stats_command(update, context)

    elif data == "btn_plan":
        await studyplan_command(update, context)

    elif data == "btn_tips":
        await query.message.reply_text(
            "💡 NCERT की हर लाइन पढ़ें और प्रतिदिन 50+ PYQ हल करें।",
            parse_mode="Markdown",
        )


# ==========================================
# 5. MAIN
# ==========================================

def main():
    logger.info("Starting NEET Sathi AI Telegram Bot...")

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing!")
        sys.exit(1)

    if not OPENAI_API_KEY or openai_client is None:
        logger.error("OPENAI_API_KEY is missing!")
        sys.exit(1)

    server_thread = threading.Thread(
        target=start_dummy_server,
        daemon=True,
    )
    server_thread.start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("pyq", pyq_command))
    app.add_handler(CommandHandler("quiz", pyq_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("studyplan", studyplan_command))

    app.add_handler(CallbackQueryHandler(button_callback_handler))

    app.add_handler(
        MessageHandler(filters.PHOTO, photo_doubt_handler)
    )

    app.add_handler(
        MessageHandler(filters.Document.ALL, document_pdf_handler)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_doubt_handler,
        )
    )

    logger.info("NEET Sathi Telegram Bot is polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
