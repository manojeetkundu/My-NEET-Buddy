#!/usr/bin/env python3
"""
NEET Sathi Telegram Bot - OpenAI deployment script
For GitHub + Render.com. Supports text doubts, photo doubts, PDF-to-quiz,
NEET PYQs, study plan, and in-memory quiz statistics.

Environment variables:
  TELEGRAM_BOT_TOKEN  (or TELEGRAM_TOKEN)
  OPENAI_API_KEY
  OPENAI_MODEL        (optional; defaults to gpt-5.5)
  PORT                (provided by Render)
"""

import os
import sys
import io
import json
import base64
import random
import logging
import threading
from typing import Optional

from flask import Flask, jsonify
from openai import AsyncOpenAI
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)
from pypdf import PdfReader

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# -------------------- Logging & configuration --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("NEET_Bot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")

openai_client: Optional[AsyncOpenAI] = None


def get_openai_client() -> AsyncOpenAI:
    """Create the async OpenAI client once and reuse it."""
    global openai_client
    if openai_client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY environment variable is missing")
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return openai_client


# -------------------- Render health-check server --------------------
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return """
    <html>
      <head><title>NEET Sathi Bot</title></head>
      <body style="font-family:sans-serif;text-align:center;padding:40px;background:#0f172a;color:white;">
        <h1>🩺 NEET Sathi AI Telegram Bot</h1>
        <p>Health server is running.</p>
      </body>
    </html>
    """, 200


@web_app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": "neet-telegram-bot"}), 200


def start_health_server():
    port = int(os.getenv("PORT", "8080"))
    logger.info("Starting Flask health server on port %s", port)
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# -------------------- NEET tutor instructions --------------------
HINDI_NEET_SYSTEM_INSTRUCTION = """आप 'NEET साथी AI' (NEET Sathi AI) हैं — NEET UG के लिए एक धैर्यवान और सटीक AI ट्यूटर।
आपका उद्देश्य हिंदी माध्यम और Hinglish के छात्रों को Physics, Chemistry, Botany और Zoology समझने में मदद करना है।

महत्वपूर्ण निर्देश:
1. भाषा:
   - छात्र हिंदी में पूछे तो सरल हिंदी में समझाएँ। वैज्ञानिक शब्दों के साथ English नाम भी दें।
   - छात्र Hinglish में पूछे तो स्वाभाविक Hinglish में समझाएँ।
   - सकारात्मक, स्पष्ट और सम्मानजनक लहजा रखें।
2. प्रश्न/फोटो समाधान:
   - प्रश्न को पहचानें और जहाँ संभव हो NCERT कक्षा व अध्याय बताएं।
   - आवश्यक सूत्र, चरणबद्ध हल और सही विकल्प समझाएं।
   - सामान्य गलती या NEET Trap भी बताएं।
   - यदि फोटो अस्पष्ट हो या प्रश्न अधूरा हो, तो अनुमान लगाने के बजाय यह स्पष्ट बताएं।
3. उत्तर:
   - साफ headings, छोटे bullet points और उपयोगी emojis का प्रयोग करें।
   - वैज्ञानिक तथ्यों और गणनाओं में सटीकता को प्राथमिकता दें।
   - किसी प्रश्न का उत्तर निश्चित न हो तो अनिश्चितता स्पष्ट करें।"""


# -------------------- Sample NEET practice questions --------------------
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
        "explanation": "C4 पौधों में mesophyll कोशिकाओं में PEP प्राथमिक CO2 ग्राही होता है। एंजाइम PEP carboxylase है। (NCERT Class 11)",
    },
    {
        "subject": "Physics (भौतिक विज्ञान)",
        "chapter": "धारा विद्युत (Current Electricity)",
        "question": "एक तार को खींचकर उसकी लंबाई में 10% की वृद्धि की जाती है। उसके प्रतिरोध में लगभग कितने प्रतिशत की वृद्धि होगी?",
        "options": ["21%", "10%", "20%", "15%"],
        "correct_id": 0,
        "explanation": "आयतन नियत रहने पर R ∝ L²। नया प्रतिरोध = (1.1)²R = 1.21R, अतः 21% वृद्धि होगी।",
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
        "explanation": "बेंजेल्डिहाइड में α-hydrogen नहीं होता, इसलिए सांद्र क्षार की उपस्थिति में Cannizzaro reaction होती है।",
    },
    {
        "subject": "Zoology (प्राणी विज्ञान)",
        "chapter": "रासायनिक समन्वय तथा एकीकरण",
        "question": "कौन सा हार्मोन वृक्क में जल के पुनरावशोषण को बढ़ाता है?",
        "options": [
            "वेसोप्रेसिन (ADH)",
            "एल्डोस्टेरोन (Aldosterone)",
            "ऑक्सीटोसिन (Oxytocin)",
            "ANF",
        ],
        "correct_id": 0,
        "explanation": "ADH हाइपोथैलेमस में बनता है और posterior pituitary से release होकर collecting ducts में जल का पुनरावशोषण बढ़ाता है।",
    },
]

# In-memory quiz data. It resets whenever the bot process restarts.
user_stats: dict[int, dict[str, int]] = {}
poll_correct_answers: dict[str, int] = {}
user_poll_answers: dict[tuple[str, int], bool] = {}


# -------------------- Shared helpers --------------------
async def send_quiz_poll(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
    options: list[str],
    correct_id: int,
    explanation: str = "",
):
    message = await context.bot.send_poll(
        chat_id=chat_id,
        question=question[:300],
        options=[str(option)[:100] for option in options[:4]],
        type=Poll.QUIZ,
        correct_option_id=correct_id,
        explanation=explanation[:190],
        is_anonymous=False,
    )
    poll_correct_answers[message.poll.id] = correct_id


async def generate_openai_text(prompt: str) -> str:
    client = get_openai_client()
    response = await client.responses.create(
        model=OPENAI_MODEL,
        instructions=HINDI_NEET_SYSTEM_INSTRUCTION,
        input=prompt,
    )
    return response.output_text or "उत्तर प्राप्त नहीं हो सका।"


# -------------------- Telegram commands --------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name if user else "भावी डॉक्टर"

    welcome_text = f"""👋 **नमस्ते {name} जी! NEET साथी AI में आपका स्वागत है!** 🩺

मैं आपका NEET UG ट्यूटर हूँ। Physics के numericals, Chemistry की reactions और Biology के NCERT concepts को हिंदी/Hinglish में समझाने में मदद करूँगा।

✨ **आप क्या कर सकते हैं?**
📸 **फोटो डाउट:** किसी प्रश्न या डायग्राम की फोटो भेजें
📝 **टेक्स्ट डाउट:** सवाल हिंदी या Hinglish में टाइप करें
📄 **PDF से क्विज़:** अपने notes की PDF भेजें
🎯 **अभ्यास टेस्ट:** NEET-style MCQs हल करें

👇 नीचे दिए गए बटन्स चुनें:"""

    keyboard = [
        [
            InlineKeyboardButton("🎯 NEET अभ्यास टेस्ट", callback_data="btn_pyq"),
            InlineKeyboardButton("📊 मेरा प्रोग्रेस", callback_data="btn_stats"),
        ],
        [
            InlineKeyboardButton("📅 स्टडी प्लान", callback_data="btn_plan"),
            InlineKeyboardButton("💡 टॉपर टिप्स", callback_data="btn_tips"),
        ],
    ]
    await update.effective_message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """📖 **NEET साथी बॉट कमांड गाइड:**

/start - मुख्य मेनू खोलें
/pyq - NEET अभ्यास प्रश्न
/quiz - रैंडम अभ्यास प्रश्न
/studyplan - व्यक्तिगत स्टडी प्लान
/stats - अपने quiz results देखें
/help - सभी कमांड्स की सूची

आप text, photo या PDF भी भेज सकते हैं।"""
    await update.effective_message.reply_text(help_text, parse_mode="Markdown")


async def send_random_pyq(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    q_data = random.choice(SAMPLE_PYQS)
    question = f"[{q_data['subject']} | {q_data['chapter']}]\n{q_data['question']}"
    await send_quiz_poll(
        chat_id=chat_id,
        context=context,
        question=question,
        options=q_data["options"],
        correct_id=q_data["correct_id"],
        explanation=q_data["explanation"],
    )


async def pyq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_random_pyq(update.effective_chat.id, context)


async def send_user_stats(message, user_id: int):
    data = user_stats.get(user_id, {"attempted": 0, "correct": 0})
    attempted = data["attempted"]
    correct = data["correct"]
    wrong = attempted - correct
    accuracy = round((correct / attempted) * 100) if attempted else 0
    estimated_marks = (correct * 4) - wrong

    stats_msg = f"""📊 **आपकी NEET अभ्यास रिपोर्ट:**

🎯 हल किए गए प्रश्न: {attempted}
✅ सही उत्तर: {correct} (+{correct * 4} अंक)
❌ गलत उत्तर: {wrong} (-{wrong} अंक)
📈 Accuracy: {accuracy}%
🏆 अनुमानित स्कोर: {estimated_marks} अंक

_नोट: यह रिपोर्ट इसी bot process के दौरान दिए गए quiz answers पर आधारित है।_"""
    await message.reply_text(stats_msg, parse_mode="Markdown")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_user_stats(update.effective_message, update.effective_user.id)


async def send_study_plan(message):
    await message.reply_text("⏳ आपका NEET study plan तैयार किया जा रहा है...")
    try:
        prompt = "NEET UG के छात्र के लिए एक practical study timetable बनाएं। इसमें Physics, Chemistry, Botany, Zoology, NCERT revision, MCQ practice, mock tests और गलतियों के analysis के लिए समय दें। योजना realistic रखें; पर्याप्त breaks और sleep भी शामिल करें। सरल हिंदी/Hinglish में लिखें। 650+ score को लक्ष्य मानें, लेकिन score की guarantee न दें।"
        plan_text = await generate_openai_text(prompt)
        await message.reply_text(plan_text)
    except Exception:
        logger.exception("Study plan generation failed")
        await message.reply_text("⚠️ Study plan बनाते समय समस्या आई। API key, model name और billing/usage limits जाँचें।")


async def studyplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_study_plan(update.effective_message)


# -------------------- OpenAI-powered doubt handlers --------------------
async def text_doubt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.effective_message.text
    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        prompt = f"छात्र का NEET doubt:\n{user_text}\n\nNCERT के अनुसार सरल हिंदी/Hinglish में अवधारणा समझाएँ। गणना हो तो चरणबद्ध हल और अंतिम उत्तर दें।"
        reply = await generate_openai_text(prompt)
        # Plain text avoids Telegram Markdown parse errors from formulas/special characters.
        await update.effective_message.reply_text(reply)
    except Exception:
        logger.exception("Text doubt handling failed")
        await update.effective_message.reply_text("⚠️ Doubt हल करते समय समस्या आई। थोड़ी देर बाद फिर कोशिश करें।")


async def photo_doubt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = update.effective_message.photo
    if not photos:
        return

    photo = photos[-1]
    caption = update.effective_message.caption or "कृपया इस चित्र/प्रश्न को हल करें।"
    await update.effective_message.reply_text("📷 फोटो मिल गई है! उसका विश्लेषण कर रहा हूँ...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        telegram_file = await context.bot.get_file(photo.file_id)
        image_bytes = await telegram_file.download_as_bytearray()
        encoded_image = base64.b64encode(bytes(image_bytes)).decode("utf-8")

        client = get_openai_client()
        response = await client.responses.create(
            model=OPENAI_MODEL,
            instructions=HINDI_NEET_SYSTEM_INSTRUCTION,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"छात्र द्वारा भेजी गई NEET प्रश्न की फोटो। टिप्पणी: {caption}\n"
                                "प्रश्न पढ़कर NCERT अनुसार हल, आवश्यक सूत्र और सही विकल्प बताएं। "
                                "यदि फोटो में पाठ स्पष्ट नहीं है तो अनुमान न लगाएं।"
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{encoded_image}",
                        },
                    ],
                }
            ],
        )
        await update.effective_message.reply_text(
            response.output_text or "फोटो का विश्लेषण नहीं हो सका।"
        )
    except Exception:
        logger.exception("Photo doubt handling failed")
        await update.effective_message.reply_text("⚠️ फोटो का विश्लेषण करते समय समस्या आई। कृपया साफ फोटो भेजें और फिर कोशिश करें।")


# -------------------- PDF to quiz --------------------
async def document_pdf_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.effective_message.document
    if not doc or not (doc.file_name or "").lower().endswith(".pdf"):
        return

    await update.effective_message.reply_text(
        f"📄 '{doc.file_name}' से NEET quiz प्रश्न बनाए जा रहे हैं..."
    )

    try:
        telegram_file = await context.bot.get_file(doc.file_id)
        pdf_bytes = await telegram_file.download_as_bytearray()
        reader = PdfReader(io.BytesIO(bytes(pdf_bytes)))
        extracted_text = "\n".join(
            (page.extract_text() or "") for page in reader.pages[:6]
        ).strip()

        if not extracted_text:
            await update.effective_message.reply_text(
                "⚠️ इस PDF से selectable text नहीं मिला। संभव है कि यह scanned/image PDF हो। कृपया text वाली PDF भेजें।"
            )
            return

        client = get_openai_client()
        prompt = f"""नीचे दी गई NEET study material से ठीक 3 अच्छे MCQs बनाएं। प्रश्न सामग्री पर आधारित हों, चार options हों और केवल एक सही उत्तर हो। भाषा सामग्री के अनुसार हिंदी या Hinglish रखें।

केवल valid JSON object लौटाएं, इस format में:
{{
  "questions": [
    {{
      "question": "प्रश्न",
      "options": ["A", "B", "C", "D"],
      "correct_index": 0,
      "explanation": "व्याख्या"
    }}
  ]
}}

Study material:
{extracted_text[:12000]}"""

        response = await client.responses.create(
            model=OPENAI_MODEL,
            instructions=HINDI_NEET_SYSTEM_INSTRUCTION,
            input=prompt,
            text={"format": {"type": "json_object"}},
        )
        payload = json.loads(response.output_text or "{}")
        questions = payload.get("questions", [])

        sent_count = 0
        for question_data in questions[:3]:
            options = question_data.get("options", [])
            correct_index = int(question_data.get("correct_index", -1))
            question_text = str(question_data.get("question", "")).strip()
            if (
                not question_text
                or not isinstance(options, list)
                or len(options) != 4
                or correct_index not in range(4)
            ):
                continue

            await send_quiz_poll(
                chat_id=update.effective_chat.id,
                context=context,
                question=question_text,
                options=[str(option) for option in options],
                correct_id=correct_index,
                explanation=str(question_data.get("explanation", "")),
            )
            sent_count += 1

        if sent_count == 0:
            await update.effective_message.reply_text(
                "⚠️ इस PDF से valid MCQs नहीं बन पाए। कृपया किसी दूसरे PDF या छोटे chapter notes के साथ कोशिश करें।"
            )
    except Exception:
        logger.exception("PDF quiz generation failed")
        await update.effective_message.reply_text("⚠️ PDF process नहीं हो पाई। कृपया PDF और OpenAI API settings जाँचें।")


# -------------------- Quiz answer tracking --------------------
async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if not answer or not answer.user:
        return

    correct_id = poll_correct_answers.get(answer.poll_id)
    if correct_id is None:
        return

    user_id = answer.user.id
    key = (answer.poll_id, user_id)
    selected_correctly = bool(answer.option_ids) and answer.option_ids[0] == correct_id

    stats = user_stats.setdefault(user_id, {"attempted": 0, "correct": 0})

    # Replace a previous answer to the same poll rather than double-counting it.
    previous_answer = user_poll_answers.get(key)
    if previous_answer is None:
        stats["attempted"] += 1
    elif previous_answer:
        stats["correct"] -= 1

    if selected_correctly:
        stats["correct"] += 1
    user_poll_answers[key] = selected_correctly


# -------------------- Callback buttons --------------------
async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "btn_pyq":
        await send_random_pyq(query.message.chat_id, context)
    elif query.data == "btn_stats":
        await send_user_stats(query.message, query.from_user.id)
    elif query.data == "btn_plan":
        await send_study_plan(query.message)
    elif query.data == "btn_tips":
        await query.message.reply_text(
            "💡 टॉपर टिप्स:\n\n• NCERT Biology की नियमित revision करें।\n• हर दिन MCQs और PYQs हल करें।\n• गलत प्रश्नों की error notebook बनाएं।\n• नियमित mock test देकर उनका analysis करें।\n• पर्याप्त नींद और breaks लें।"
        )


# -------------------- Shutdown cleanup & application --------------------
async def close_openai_client(application):
    global openai_client
    if openai_client is not None:
        await openai_client.close()
        openai_client = None


def main():
    logger.info("Starting NEET Sathi AI Telegram Bot...")

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN (or TELEGRAM_TOKEN) is missing")
        sys.exit(1)
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY is missing")
        sys.exit(1)

    server_thread = threading.Thread(target=start_health_server, daemon=True)
    server_thread.start()

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_shutdown(close_openai_client)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("pyq", pyq_command))
    application.add_handler(CommandHandler("quiz", pyq_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("studyplan", studyplan_command))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    application.add_handler(PollAnswerHandler(poll_answer_handler))
    application.add_handler(MessageHandler(filters.PHOTO, photo_doubt_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, document_pdf_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_doubt_handler)
    )

    logger.info("NEET Sathi Telegram Bot is polling using model %s", OPENAI_MODEL)
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
