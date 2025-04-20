import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from system_monitor import SystemMonitor
from training_monitor import TrainingMonitor

TOKEN = "8107528628:AAFrriScv7MrssxUoHEQ9vGrx1z1MG2L9io"  # <-- 替换为你的 Bot Token

# Create instances of monitors
system_monitor = SystemMonitor()
training_monitor = TrainingMonitor()

async def server_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(system_monitor.get_status())

async def training_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(training_monitor.get_status())

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("server_status", server_status))
    app.add_handler(CommandHandler("training_status", training_status))
    print("📡 Bot running... Send /server_status to get system info or /training_status to get training info.")
    app.run_polling()
