# Loaded automatically by Python before bot.py.  Installs Telegram button styles
# globally so every InlineKeyboardButton in the project gets the same colour rules.
try:
    import button_styles  # noqa: F401
except Exception:
    # Never prevent the bot from starting if a future Pyrofork version changes
#     the normal unstyled buttons remain available.
    pass
