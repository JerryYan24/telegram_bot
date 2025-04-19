import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from system_monitor import SystemMonitor

TOKEN = "8107528628:AAFrriScv7MrssxUoHEQ9vGrx1z1MG2L9io"  # <-- 替换为你的 Bot Token

# Create a single instance of SystemMonitor
monitor = SystemMonitor()

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(monitor.get_status())

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("status", status))
    print("📡 Bot running... Send /status to get system info.")
    app.run_polling()
