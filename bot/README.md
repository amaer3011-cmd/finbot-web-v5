# بوت الثقافة المالية (finbot v4.1)

## التشغيل بعد فك الضغط

### Windows (PowerShell)
```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

افتح `.env` وأضف قيمة `BOT_TOKEN`، ثم شغّل:
```powershell
.\.venv\Scripts\python.exe bot.py
```

### Linux / macOS
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env
```

أضف `BOT_TOKEN` إلى `.env` (و`OWNER_CHAT_ID` لو عايز تقارير الأعطال)، ثم شغّل:
```bash
.venv/bin/python bot.py
```

## نقل المشروع بملف ZIP
استخرج الأرشيف إلى مجلد جديد وافتحه في VS Code أو محررك المفضل. يحتوي الأرشيف على الكود والاختبارات والأصول وملف الإجابات، لكنه يستبعد `.env` و`bot.db` و`.venv` وملفات التخزين المؤقت. أنشئ `.env` محليًا من `.env.example` وثبّت المتطلبات بعد كل استخراج؛ لا تضع توكن البوت أو بيانات الدخول في الأرشيف.

## الفكرة
الطالب يرسل الـ ID والباسورد للبوت، فيدخل البوت على المنصة بالحساب (حتى لو الطالب لم يدخلها من قبل)
ويحل الأنشطة المتاحة من بنك الإجابات `answers.txt`، ويتخطى المكتمل والمقفول، ويكمل من أول نشاط غير محلول.

## ملاحظات
- لا يخمّن: لو لا يوجد تطابق مؤكد لسؤال، يتوقف ويخبرك بالأسئلة المعنية.
- لو حدث انقطاع أو مهلة يعيد تشغيل المتصفح ويكمل (التقدم محفوظ على المنصة).
- لا تضع `.env` في أي أرشيف تشاركه، ولو سبق أن شاركت التوكن فاعمل له Revoke من BotFather.
- يقبل `ANSWERS_FILE` بنك الإجابات النصي الحالي أو ملف Markdown المنظم بصيغة `**السؤال:**` و`**الإجابة:**`. لملف خارجي، ضع مساره الكامل في `.env` وتأكد أن الملف متاح عند تشغيل البوت.
- مراجع واجهة المنصة: [التوثيق الحالي](./SPRIX_Learning_Platform_Documentation.md) و[ملاحظات البنية](./sprix_platform_architecture.md).

---

## بنية المشروع

```
bot.py          معالجات تيليجرام وتشغيل المهام
ui.py           الأرقام العربية والأشرطة والرسائل والأزرار
progress.py     بطاقة التقدم الحية والتحديث الدوري
db.py           SQLite (المستخدمون، عدّاد الصلاة، سجل المهام)
core/           محرك حل المنصة (Playwright)
  config.py       الإعدادات والثوابت والاستثناءات
  runtime.py      الإبلاغ والإلغاء والتشخيص والقياس الزمني
  answers.py      بنك الإجابات والمطابقة
  page.py         أدوات الصفحة والانتظار
  quiz.py         الاختبارات المقيّمة
  questions.py    حل الأسئلة والنقاط المهمة
  curriculum.py   بيانات المنهج والخطة
  session.py      الدخول وفتح المنهج
  navigation.py   الرجوع للمنهج وفتح النشاط التالي
  flow.py         دورة الحل الكاملة وتشغيل المهمة
tests/          اختبارات pytest بلا شبكة
```

تشغيل محلي بدون بوت: `python -m core`.
للاختبارات: `pip install -r requirements-dev.txt && pytest tests`.
سجل التغييرات الكامل: [CHANGELOG.md](./CHANGELOG.md).

---

## النشر على Railway

1. ارفع مجلد المشروع على مستودع GitHub خاص (الملفات `Dockerfile` و`railway.json` في الجذر).
2. في Railway: **New Project ← Deploy from GitHub repo** واختار المستودع. هيتعرف على الـ Dockerfile لوحده.
3. من **Variables** ضيف:
   - `BOT_TOKEN` = توكن البوت من BotFather (إجباري).
   - `MAX_JOBS` = `1` للخطة الصغيرة (كل حساب بيشغّل متصفح بياكل ~300–500MB رام). ارفعه لـ `2` لو الرام 1.5GB أو أكتر.
4. **اربط Volume** على المسار `/data` (Settings ← Volumes). من غيره قاعدة البيانات (اليوزرات المحفوظة وعدّاد الصلاة) بتتمسح مع كل نشر.
5. دوس Deploy. وقت البناء الأول 3–6 دقايق.

### إزاي تتأكد إن النشر نجح
في لوج البناء لازم تلاقي `🚀 كل شيء جاهز` بعد `✅ متصفح Chromium`، وفي لوج التشغيل `loaded 654 answers`. لو أي فحص فشل بيتكتب سببه بالعربي وبيوقف بدل ما البوت يقع بعد النشر:

| الرسالة | الحل |
|---|---|
| `BOT_TOKEN فاضي / شكله غلط` | انسخ التوكن كامل من BotFather في Variables |
| `Executable doesn't exist` | رقم `playwright==` في requirements.txt لازم يطابق رقم الصورة في Dockerfile (`v1.48.0`) |
| `مفيش Volume مربوط` | اربط Volume على `/data` |
| `Conflict: terminated by other getUpdates` | فيه نسخة تانية بنفس التوكن شغالة (على جهازك مثلًا) — اقفلها. وبتظهر ثواني وقت النشر وبتختفي لوحدها |

### ملاحظات
- البوت بيشتغل بالـ polling فمفيش Port ولا Domain مطلوب (Worker عادي).
- فحص الجاهزية يدويًا: `python selfcheck.py`.
