content = open('src/bot.py', 'r', encoding='utf-8').read()
old = '''async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Social Markhors Bot\\n\\n"
        "Platforms: Facebook, Instagram\\n\\n"
        "Modes:\\n"
        "1) COOKIE REWORK - /rework > cookie .txt bhejo\\n"
        "2) VERIFY - /verify > cookie zip bhejo\\n"
        "3) CAMPAIGN - /camp new > /camp start\\n\\n"
        "Admin: /db /proxy /netcheck\\n\\n"
        "Full commands: /help"
    )'''
new = '''async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not check_user_rate_limit(uid):
        await update.message.reply_text("Rate limit hit. Please wait before sending another command.")
        return
    await update.message.reply_text(
        f"Social Markhors Bot v{BOT_VERSION}\\n\\n"
        "Platforms: Facebook, Instagram\\n\\n"
        "Modes:\\n"
        "1) COOKIE REWORK - /rework > cookie .txt bhejo\\n"
        "2) VERIFY - /verify > cookie zip bhejo\\n"
        "3) CAMPAIGN - /camp new > /camp start\\n\\n"
        "Admin: /db /proxy /netcheck\\n\\n"
        "Full commands: /help"
    )'''
if old in content:
    content = content.replace(old, new)
    open('src/bot.py', 'w', encoding='utf-8').write(content)
    print('Replaced successfully')
else:
    print('Old string not found')
    lines = content.splitlines()
    for i, line in enumerate(lines[198:214], start=199):
        print(f'{i}: {repr(line)}')
