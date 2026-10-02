# Quiz bot (Telegram)

Telegram'da test topshirish boti. Python va aiogram 3 da yozilgan, interfeys o'zbek tilida.
Natijalar SQLite bazasida saqlanadi.

## Imkoniyatlar

- Savollar `savolar.json` fayldan o'qiladi, tartibi va variantlar tartibi aralashtiriladi
- Test oxirida foiz va baho ko'rsatiladi
- Oxirgi 5 ta natijani ko'rish (`/natijalarim`)
- Testni to'xtatish (`/stop`)
- Xatolar ushlanadi va `bot.log` ga yoziladi, bot to'xtab qolmaydi

## O'rnatish

1. Python 3.10 yoki yangiroq o'rnating.
2. Kutubxonalarni o'rnating:
   ```bash
   pip install -r requirements.txt
   ```
3. Telegramda `@BotFather` orqali bot yarating va tokenni oling.
4. `.env.example` faylidan nusxa olib, nomini `.env` ga o'zgartiring va tokenni yozing:
   ```
   BOT_TOKEN=1234567890:AAExampleTokenFromBotFather
   ```
5. Botni ishga tushiring:
   ```bash
   python bot.py
   ```

## Savollar formati (`savolar.json`)

```json
[
  {
    "id": 1,
    "question": "Savol matni",
    "options": ["Variant 1", "Variant 2", "Variant 3"],
    "answer": "Variant 1"
  }
]
```

`answer` qiymati `options` ichida aynan bo'lishi kerak, aks holda savol o'tkazib yuboriladi.

## Muhim

`.env` fayli GitHub'ga yuklanmaydi (`.gitignore` da yozilgan). Tokenni hech kimga bermang.
