import os
import io
import json
import asyncio
import logging
import threading

from flask import Flask, jsonify
from waitress import serve
from pypdf import PdfReader
from docx import Document
from openai import OpenAI

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- Configuration ----------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
PORT = int(os.getenv("PORT", "10000"))

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_TEXT_CHARS = 20000
MAX_QUESTIONS = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("neet_sathi")

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")

if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- Dummy health server ----------

web_app = Flask(__name__)


@web_app.get("/")
def home():
    return jsonify(status="ok", service="NEET Sathi Bot")


@web_app.get("/health")
def health():
    return jsonify(status="healthy"), 200


# ---------- Document text extraction ----------

def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    pages = []

    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text.strip())

    return "\n".join(pages)[:MAX_TEXT_CHARS]


def extract_docx_text(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    paragraphs = [
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    ]
    return "\n".join(paragraphs)[:MAX_TEXT_CHARS]


def extract_document_text(filename: str, data: bytes) -> str:
    filename = filename.lower()

    if filename.endswith(".pdf"):
        return extract_pdf_text(data)

    if filename.endswith(".docx"):
        return extract_docx_text(data)

    if filename.endswith(".txt"):
        return data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]

    raise ValueError("Unsupported file type")


# ---------- AI question generation ----------

def generate_questions(source_text: str, topic: str) -> list[dict]:
    source_text = source_text[:MAX_TEXT_CHARS]

    prompt = f"""
Create {MAX_QUESTIONS} NEET-level multiple-choice questions.

Topic: {topic}

Study material:
{source_text}

Return valid JSON only, in this format:
{{
  "questions": [
    {{
      "question": "Question text",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_index": 0,
      "explanation": "Short explanation"
    }}
  ]
}}

Requirements:
- Exactly 4 options per question.
- correct_index must be an integer from 0 to 3.
- Base questions on the supplied material.
- Avoid duplicate questions.
- Keep the questions and options concise.
"""

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You create accurate educational MCQs. "
                    "Return valid JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    questions = parsed.get("questions", [])

    valid_questions = []

    for question in questions:
        options = question.get("options")
        correct_index = question.get("correct_index")

        if (
            isinstance(question.get("question"), str)
            and isinstance(options, list)
            and len(options) == 4
            and all(isinstance(option, str) for option in options)
            and isinstance(correct_index, int)
            and not isinstance(correct_index, bool)
            and 0 <= correct_index < 4
        ):
            valid_questions.append(question)

    return valid_questions[:MAX_QUESTIONS]


# ---------- Quiz sending ----------

async def send_quiz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    questions: list[dict],
):
    chat_id = update.effective_chat.id

    if not questions:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Quiz generate nahi ho paaya. Dobara try karo.",
        )
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Quiz ready! Total questions: {len(questions)}",
    )

    for number, item in enumerate(questions, start=1):
        try:
            question_text = f"{number}. {item['question']}"[:300]
            options = [option[:100] for option in item["options"]]
            explanation = str(item.get("explanation", ""))[:200]

            await context.bot.send_poll(
                chat_id=chat_id,
                question=question_text,
                options=options,
                type="quiz",
                is_anonymous=False,
                correct_option_id=item["correct_index"],
                explanation=explanation or None,
            )

            await asyncio.sleep(0.4)

        except Exception:
            logger.exception("Failed to send poll %s", number)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Question {number} send nahi ho paaya.",
            )


async def process_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    topic: str,
):
    status = await update.effective_message.reply_text(
        "MCQs bana raha hoon..."
    )

    try:
        questions = await asyncio.to_thread(
            generate_questions,
            text,
            topic,
        )

        await status.delete()
        await send_quiz(update, context, questions)

    except Exception:
        logger.exception("Question generation failed")
        await status.edit_text(
            "Quiz generate nahi ho paaya. Thodi der baad try karo."
        )


# ---------- Telegram commands ----------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "Namaste! Main NEET Sathi hoon.\n\n"
        "PDF, DOCX ya TXT file bhejo. Main usse quiz banaunga.\n\n"
        "Commands:\n"
        "/start - Start bot\n"
        "/help - Help\n"
        "/quiz <topic> - Topic-based quiz\n\n"
        "Note: PDF mein selectable text hona chahiye. "
        "Scanned PDFs aur images supported nahi hain."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await start(update, context)


async def quiz_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    topic = " ".join(context.args).strip()

    if not topic:
        await update.effective_message.reply_text(
            "Topic likho, example:\n/quiz Human Physiology"
        )
        return

    await process_text(
        update,
        context,
        text=f"Create NEET questions about: {topic}",
        topic=topic,
    )


# ---------- File and text handlers ----------

async def document_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    document = message.document

    if not document:
        return

    filename = document.file_name or "file"
    allowed_extensions = (".pdf", ".docx", ".txt")

    if not filename.lower().endswith(allowed_extensions):
        await message.reply_text(
            "Supported files: PDF, DOCX, TXT.\n"
            "Scanned PDFs aur images supported nahi hain."
        )
        return

    if document.file_size and document.file_size > MAX_FILE_BYTES:
        await message.reply_text("File 15 MB se chhoti honi chahiye.")
        return

    status = await message.reply_text("File read kar raha hoon...")

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        data = bytes(await telegram_file.download_as_bytearray())

        if len(data) > MAX_FILE_BYTES:
            await status.edit_text("File 15 MB se badi hai.")
            return

        text = await asyncio.to_thread(
            extract_document_text,
            filename,
            data,
        )

        if len(text.strip()) < 40:
            await status.edit_text(
                "File se readable text nahi mila. "
                "PDF selectable-text wali honi chahiye."
            )
            return

        questions = await asyncio.to_thread(
            generate_questions,
            text,
            filename,
        )

        await status.delete()
        await send_quiz(update, context, questions)

    except Exception:
        logger.exception("Document processing failed")
        await status.edit_text(
            "File process nahi ho paayi. "
            "PDF, DOCX ya TXT file dobara try karo."
        )


async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.effective_message.text.strip()

    if len(text) < 20:
        await update.effective_message.reply_text(
            "Quiz ke liye thoda aur text bhejo, "
            "ya /quiz <topic> use karo."
        )
        return

    await process_text(
        update,
        context,
        text,
        "User-provided study text",
    )


async def image_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "Image reading disabled hai. "
        "Please text ya selectable-text PDF bhejo."
    )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error("Unhandled Telegram error: %s", context.error)


# ---------- Bot startup ----------

def run_bot():
    asyncio.set_event_loop(asyncio.new_event_loop())

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("quiz", quiz_command))

    application.add_handler(
        MessageHandler(filters.Document.ALL, document_handler)
    )
    application.add_handler(
        MessageHandler(filters.PHOTO, image_handler)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    application.add_error_handler(error_handler)

    logger.info("Starting Telegram bot polling")
    application.run_polling()


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    logger.info("Starting health server on port %s", PORT)
    serve(web_app, host="0.0.0.0", port=PORT)
