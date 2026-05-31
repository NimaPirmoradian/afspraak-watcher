# Afspraak Watcher

یک سیستم چک خودکار وقت ملاقات (afspraak) برای سایت‌های شهرداری هلند با اعلان تلگرام.
روی **GitHub Actions** اجرا میشه — یعنی ۲۴/۷ کار میکنه و به روشن بودن لپتاپت نیاز نداره.

---

## چرا این بهتر از روش قبلیه؟

| موضوع | قبلی (Windows Task Scheduler) | این (GitHub Actions) |
|---|---|---|
| اجرا وقتی لپتاپ خوابه/خاموشه | ❌ نه | ✅ بله |
| اضافه‌کردن سایت جدید | کپی کل کد پایتون | یک فایل YAML کوچک |
| مدیریت توکن تلگرام | داخل کد commit شده | GitHub Secret (امن) |
| جلوگیری از نوتیف تکراری | ❌ نداره | ✅ state در رپو ذخیره میشه |
| اعلان فقط برای تاریخ زودتر | ❌ deadline هاردکد و باگ‌دار | ✅ هر تاریخ زودتر از قبلی |
| ریست بعد از کرش مرورگر | ❌ یک کرش = یک فقدان | ✅ ۳ بار retry خودکار |

---

## ۱. ساخت ریپو در GitHub

1. برو [github.com/new](https://github.com/new)
2. اسم: `afspraak-watcher` (یا هرچی دوست داری)
3. **مهم**: میتونه Public یا Private باشه. توکن‌ها داخل Secrets ذخیره میشن و حتی در ریپوی Public هم قابل دیدن نیستن.
   - **Public** پیشنهاد میشه چون GitHub Actions روی ریپوی Public **رایگان و بدون محدودیت** هست.
   - **Private** هم کار میکنه ولی محدودیت ۲۰۰۰ دقیقه در ماه داره (با cron هر ۱۰ دقیقه ممکنه از این حد بگذری).
4. بدون README/gitignore بساز (داریمش).

## ۲. آپلود این پوشه

از ترمینال (Git Bash یا PowerShell):

```bash
cd <مسیر-این-پوشه>/afspraak-watcher
git init
git add .
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/<USERNAME>/afspraak-watcher.git
git push -u origin main
```

## ۳. تنظیم Secrets

برو در ریپو: **Settings → Secrets and variables → Actions → New repository secret**

دو تا secret اضافه کن:

| نام | مقدار |
|---|---|
| `TELEGRAM_BOT_TOKEN` | توکن بات تلگرامت |
| `TELEGRAM_CHAT_ID` | `108046309` (chat id خودت از bot.py) |

> ⚠️ **توصیه امنیتی**: توکن قبلی (`8247214633:AAEYZRfkswijwUP52gucrUAAkZlq3StASNQ`) داخل bot.py کامیت شده بود. اگر اون فایل رو در جای عمومی گذاشتی، توکن جدید بساز:
> در تلگرام به [@BotFather](https://t.me/BotFather) برو → `/revoke` → بات رو انتخاب کن → توکن جدید بگیر → در Secret بالا قرار بده.

## ۴. فعال‌کردن Actions

برو در ریپو → تب **Actions** → اگر پرسید "Workflows aren't being run on this repository" → دکمه **I understand my workflows, go ahead and enable them** رو بزن.

## ۵. تست دستی

- در ریپو → **Actions** → سمت چپ روی **Check appointments** کلیک کن
- دکمه **Run workflow** سمت راست → **Run workflow** سبز
- بعد ۳۰-۶۰ ثانیه باید سبز بشه. لاگ‌ها رو ببین.
- اگر تاریخی زودتر از `notify_if_before` پیدا شد، باید تلگرام بگیری.

از این به بعد هر ۱۰ دقیقه خودکار اجرا میشه.

---

## اضافه‌کردن سایت جدید

فقط یک فایل YAML جدید در `sites/` بساز. مثلاً برای شهرداری دیگه:

```yaml
# sites/rotterdam.yaml
name: "Rotterdam Paspoort"
url: "https://example-rotterdam.nl/..."
max_retries: 3

steps:
  - action: goto
    wait_until: domcontentloaded
  - action: click
    selector: "button:has-text('Volgende stap')"
  - action: wait_load
  - action: wait
    duration: 2000

extract:
  - type: input_dutch_date
  - type: text_regex_dutch_date

notify_if_before: "2026-07-01"

message_template: |
  🟢 Eerder slot bij {name}!
  📅 {date}
  👉 {url}
```

سپس:
```bash
git add sites/rotterdam.yaml
git commit -m "add rotterdam"
git push
```

تموم! Workflow بعدی این رو هم چک میکنه.

### اکشن‌های موجود در `steps`

| action | پارامترها | توضیح |
|---|---|---|
| `goto` | `wait_until`, `timeout` | میره به `url` سایت |
| `click` | `selector`, `timeout` | کلیک روی دکمه (CSS/Playwright selector) |
| `wait` | `duration` (ms) | صبر ثابت |
| `wait_load` | `state` (default: networkidle) | صبر برای بارگذاری |
| `fill` | `selector`, `value` | پر کردن فیلد |

### استراتژی‌های `extract`

| type | توضیح |
|---|---|
| `input_dutch_date` | دنبال `<input>` با مقدار حاوی نام ماه هلندی |
| `text_regex_dutch_date` | regex روی متن کل صفحه دنبال «روز ماه سال» |
| `selector_text` (با `selector`) | متن یک selector خاص رو میخونه |

---

## تغییر `notify_if_before`

وقت ملاقاتت زودتر شد یا سایت جدید گرفتی؟ کافیه فایل YAML سایت رو ادیت کنی:

```yaml
notify_if_before: "2026-05-25"   # تاریخ جدید
```

و commit/push کنی. دفعه بعد چک خودکار با مقدار جدید کار میکنه.

> راهنما: `notify_if_before` رو روی **تاریخ بوکینگ فعلیت** بذار. هر تاریخ زودتر از این نوتیف میده.

---

## بقای از اسپم نوتیف

سیستم در `state/<site>.json` آخرین تاریخ پیداشده و آخرین تاریخ نوتیف‌شده رو ذخیره میکنه. منطقش:

- اگر اولین تاریخ آزاد **زودتر از `notify_if_before`** هست **و**
- اولین بار است یا **زودتر از آخرین تاریخی است که نوتیف شد** → نوتیف میده.

بنابراین اگر بات یک بار June 5 رو پیدا کرد و نوتیف داد، تا وقتی تاریخی زودتر از June 5 پیدا نکنه دوباره نوتیف نمیده. این مهمه چون GitHub Actions هر ۱۰ دقیقه چک میکنه ولی نمیخوای ۱۴۴ پیغام در روز بگیری.

---

## محدودیت‌های GitHub Actions

- **زمان دقیق cron**: GitHub Actions cron با تأخیر ۵-۳۰ دقیقه ممکنه اجرا بشه در ساعت‌های شلوغ. این محدودیت پلتفرمه و راه‌حلی نداره مگر سرور اختصاصی.
- **محدودیت ماهانه**: ریپوی Public = نامحدود. ریپوی Private = ۲۰۰۰ دقیقه/ماه. هر چک ~۳۰ ثانیه طول میکشه، پس هر ۱۰ دقیقه = ۲۱۶۰ دقیقه/ماه که از حد رد میشه. اگر Private میخوای، cron رو روی `*/15` بذار.

---

## اجرای محلی (برای دیباگ)

```bash
pip install -r requirements.txt
python -m playwright install chromium
export TELEGRAM_BOT_TOKEN="..."   # یا set در Windows
export TELEGRAM_CHAT_ID="..."
python checker.py
python checker.py --only ridderkerk   # فقط یک سایت
```

---

## فایل‌ها

| فایل | کاربرد |
|---|---|
| `checker.py` | موتور اصلی — همه YAMLها رو میخونه و چک میکنه |
| `sites/*.yaml` | یک فایل برای هر سایت |
| `state/*.json` | حالت ذخیره‌شده (commit میشه توسط worker) |
| `.github/workflows/check.yml` | تنظیمات GitHub Actions |
| `requirements.txt` | وابستگی‌های Python |
