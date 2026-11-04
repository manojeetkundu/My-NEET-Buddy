import os
import logging
import threading
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, PollAnswerHandler,
    ContextTypes, filters
)
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

app = Flask(__name__)

@app.get("/")
def health():
    return jsonify({"status": "NEET Sathi is running"}), 200

@app.get("/health")
def health_check():
    return jsonify({"status": "healthy"}), 200


def get_user_data(context):
    return context.user_data


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Namaste! 👋 Main NEET Sathi hoon.\n\n"
        "Mujhse NEET ke doubts poochho, topic quiz banao, "
        "ya PDF/DOCX/TXT bhejkar usse quiz banwao.\n\n"
        "Commands:\n"
        "/help - Help\n"
        "/quiz <topic> - Topic quiz\n"
        "/progress - Apni progress dekho"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "NEET Sathi ka use:\n"
        "• Text mein doubt bhejo\n"
        "• Photo bhejo, main question samjhaunga\n"
        "• /quiz Biology - topic quiz\n"
        "• PDF, DOCX ya TXT bhejo - document se quiz\n"
        "• /progress - quiz progress"
    )


async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_user_data(context)
    await update.message.reply_text(
        f"📊 Tumhari progress:\n"
        f"Answered: {data.get('answered', 0)}\n"
        f"Correct: {data.get('correct', 0)}"
    )


async def ask_ai(prompt: str) -> str:
    if not client:
        return "OpenAI API key configured nahi hai. Render environment variables check karo."

    response = client.responses.create(
        model="gpt-4.1-mini",
        instructions=(
            "You are NEET Sathi, a helpful NEET UG tutor. "
            "Explain in simple Hindi or Hinglish. "
            "Be accurate, concise, and educational."
        ),
        input=prompt
    )
    return response.output_text


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    await message.chat.send_action("typing")
    try:
        answer = await ask_ai(message.text)
        await message.reply_text(answer[:4000])
    except Exception:
        logger.exception("AI request failed")
        await message.reply_text("Abhi jawab dene mein dikkat aa rahi hai. Thodi der baad try karo.")


async def photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.photo:
        return

    if not client:
        await message.reply_text("OpenAI API key configured nahi hai.")
        return

    await message.chat.send_action("typing")
    try:
        photo = message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        import base64
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{encoded}"

        response = client.responses.create(
            model="gpt-4.1-mini",
            instructions=(
                "You are NEET Sathi, a NEET tutor. "
                "Solve the question shown in the image step by step "
                "in simple Hindi/Hinglish."
            ),
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": message.caption or "Solve this question."},
                    {"type": "input_image", "image_url": data_url}
                ]
            }]
        )
        await message.reply_text(response.output_text[:4000])
    except Exception:
        logger.exception("Image request failed")
        await message.reply_text("Image solve karne mein dikkat aayi. Clear photo bhejkar dobara try karo.")


async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("Use: /quiz Human Physiology")
        return

    prompt = (
        f"Create 5 NEET UG multiple-choice questions about {topic}. "
        "For each question, give 4 options and clearly mark the correct answer "
        "and a short explanation. Use this exact format:\n"
        "Q: question\nA) option\nB) option\nC) option\nD) option\n"
        "ANSWER: A\nEXPLANATION: explanation"
    )

    await update.message.reply_text("Quiz bana raha hoon... ⏳")
    try:
        quiz = await ask_ai(prompt)
        await update.message.reply_text(quiz[:4000])
    except Exception:
        logger.exception("Quiz generation failed")
        await update.message.reply_text("Quiz banane mein dikkat aayi.")


async def document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document

    if not document:
        return

    filename = (document.file_name or "").lower()
    allowed = filename.endswith((".txt", ".pdf", ".docx"))

    if not allowed:
        await message.reply_text("Sirf TXT, PDF aur DOCX files supported hain.")
        return

    await message.reply_text(
        "File mil gayi! Document-based quiz extraction abhi is starter version mein enabled nahi hai. "
        "Topic ke liye /quiz <topic> use karo."
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not ADMIN_ID or user_id != ADMIN_ID:
        await update.message.reply_text("Ye command sirf bot admin ke liye hai.")
        return

    await update.message.reply_text(
        "Admin access verified.\n"
        "Bot configuration Render environment variables se manage hoti hai."
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Telegram error: %s", context.error)


def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable missing")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("progress", progress))
    application.add_handler(CommandHandler("quiz", quiz_command))
    application.add_handler(CommandHandler("admin", admin_command))

    application.add_handler(MessageHandler(filters.PHOTO, photo_message))
    application.add_handler(MessageHandler(filters.Document.ALL, document_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

    application.add_error_handler(error_handler)

    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Starting NEET Sathi bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
