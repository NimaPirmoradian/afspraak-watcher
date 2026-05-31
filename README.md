# Afspraak Watcher (v2 — Telegram-controlled)

سیستم خودکار چک وقت ملاقات (afspraak) برای سایت‌های شهرداری هلند با کنترل کامل از تلگرام.
روی **GitHub Actions** اجرا می‌شه — یعنی ۲۴/۷ کار می‌کنه و به روشن بودن لپتاپ نیاز نداره.

---

## همه‌چی از تلگرام

دیگه نیاز نیست برای تغییر تنظیمات به GitHub بری. همه از طریق بات تلگرام:

| دستور | کاربرد |
|---|---|
| `/list` | لیست همه سایت‌ها + وضعیتشون |
| `/info <site>` | جزئیات یک سایت |
| `/watch <site>` | شروع چک کردن (مثلاً `/watch ridderkerk`) |
| `/pause <site>` | توقف موقت |
| `/resume <site>` | ادامه چک کردن |
| `/booked <site>` | قرار رو گرفتم → با تأیید خاموش می‌شه |
| `/deadline <site> <YYYY-MM-DD>` | تاریخ سقف برای notif |
| `/deadline <site> clear` | حذف تاریخ سقف |
| `/check <site>` | درخواست چک فوری (در ران بعدی) |
| `/help` | راهنما |

## دکمه‌های inline

روی هر نوتیف **تاریخ جدید پیدا شد**، این دکمه‌ها میان:

- **🌐 برو به سایت** — مستقیم به صفحه بوکینگ
- **✅ گرفتم** — با تأیید، چک رو خاموش می‌کنه
- **🔄 الان دوباره چک کن** — درخواست چک فوری

---

## معماری

```
GitHub Actions cron (هر ۱۰ دقیقه)
       │
       ▼
   main.py
       │
       ├── bot.py            ← پیام‌های جدید تلگرام رو می‌گیره و دستورات رو پردازش می‌کنه
       │     │
       │     ├── /watch ridderkerk → state.active = True
       │     ├── /deadline ...     → state.deadline_override = ...
       │     └── /booked ...       → سؤال تأیید + خاموش
       │
       └── checker.py        ← برای هر سایت active، scrape و notify
             │
             └── send_message با دکمه‌های inline
                   │
                   └── کاربر دکمه‌ای می‌زنه → callback_query در ران بعدی پردازش می‌شه
```

### فایل‌ها

| فایل | کاربرد |
|---|---|
| `main.py` | entry point — ۲ فاز: bot → check |
| `bot.py` | command + callback handlers |
| `checker.py` | site scraping + notification logic |
| `telegram_io.py` | wrapper نازک برای Telegram API |
| `state.py` | I/O فایل‌های state |
| `sites.py` | لود YAML سایت‌ها |
| `sites/*.yaml` | تعریف سایت‌ها (URL + steps + extract) |
| `state/*.json` | state runtime برای هر سایت (commit می‌شه توسط worker) |
| `state/_telegram.json` | offset پیام‌های تلگرام |
| `.github/workflows/check.yml` | تنظیمات cron |
| `requirements.txt` | dependency Python |
| `ADDING_NEW_SITE.md` | راهنما برای AI آینده وقتی می‌خوای سایت اضافه کنی |

---

## State و تنظیمات

YAML سایت **فقط تعریف سایته** (URL، مراحل، روش استخراج). تنظیمات شخصی کاربر (active, deadline) در state ذخیره می‌شه و از تلگرام کنترل می‌شه. این جداسازی باعث می‌شه:

- اضافه‌کردن سایت = یه YAML کوچک، بدون تنظیمات شخصی
- شروع/توقف چک = یه پیام تلگرام، بدون commit
- تغییر deadline = یه پیام تلگرام، بدون commit

## منطق notification

سه حالت:

1. **Baseline** (اولین نوتیف بعد از /watch): **همیشه** ارسال می‌شه، حتی اگه > deadline. این بهت می‌گه baseline چیه.
2. **Improvement**: فقط وقتی تاریخ پیداشده زودتر از **min(deadline, last_notified)** باشه.
3. **Silent**: تاریخ پیداشده بدتر از قبلی → سکوت.

این یعنی هر چقدر هم cron زیاد اجرا بشه (هر ۱۰ دقیقه)، تو فقط **هنگام پیشرفت واقعی** خبر می‌گیری.

---

## امنیت

- فقط `TELEGRAM_CHAT_ID` تعیین‌شده می‌تونه دستور بفرسته. بقیه silently ignored.
- توکن از GitHub Secrets خونده می‌شه (نه در کد).
- callback_data شامل site_id هست، Telegram خودش signature می‌کنه (نمی‌شه از خارج callback جعلی فرستاد).

---

## اضافه‌کردن سایت جدید

`ADDING_NEW_SITE.md` رو ببین. خلاصه:

1. در یه چت با AI (مثل Claude، ChatGPT) بگو: "می‌خوام سایت X رو به پروژه afspraak-watcher اضافه کنم. این فایلش رو بخون: [لینک به ADDING_NEW_SITE.md در GitHub]"
2. URL سایت جدید رو بده. AI یه YAML برات می‌سازه.
3. YAML رو در `sites/<site_id>.yaml` کامیت کن.
4. در تلگرام: `/watch <site_id>` بزن. تموم.

---

## مهاجرت از v1

اگه v1 رو راه انداخته بودی و الان داری v2 رو می‌ذاری:

1. همه فایل‌های Python و workflow رو با نسخه v2 جایگزین کن.
2. فایل YAML سایت (مثلاً `sites/ridderkerk.yaml`) رو با نسخه v2 جایگزین کن (فیلدهای `notify_if_before` و `message_template` حذف شدن).
3. فایل state موجود (`state/ridderkerk.json`) رو پاک کن — schema جدیده.
4. در تلگرام `/watch ridderkerk` بزن تا فعال بشه.
5. `/deadline ridderkerk 2026-06-09` برای تنظیم تاریخ سقف.

---

## اجرای محلی (debug)

```bash
pip install -r requirements.txt
python -m playwright install chromium
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."

python main.py                     # کل چرخه
python main.py --skip-bot          # فقط چک (بدون پردازش پیام‌ها)
python main.py --skip-checks       # فقط پیام‌ها (بدون چک سایت)
python main.py --only ridderkerk   # فقط یک سایت
```
