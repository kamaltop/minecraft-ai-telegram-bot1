# Minecraft AI Telegram Bot

بوت Telegram متعدد الوظائف للبحث عن Mods وModpacks، تنزيل نسخ سيرفرات محلية، تشغيل AFK Client على Minecraft Java، الدردشة الاختيارية بالذكاء الاصطناعي، والهدايا الترحيبية.

## المتطلبات

يحتاج المشروع إلى Python 3.10 أو أحدث، Node.js 20 أو أحدث، Java إذا كنت ستشغل سيرفرات محلية، وحساب Telegram Bot. ميزات Voice Chat تحتاج حساب Telegram مساعداً وبيانات MTProto منفصلة.

## الإعداد المحلي

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
cp env.example .env
python3 bot.py
```

ضع الأسرار داخل `.env` فقط. لا ترفع `.env` أو `bot.db` أو ملفات الجلسات إلى GitHub.

## أسرار GitHub Actions أو الاستضافة

أضف `TELEGRAM_TOKEN` و`GEMINI_KEY` كـ Secrets في بيئة الاستضافة. إذا احتجت Voice Chat أضف `TELEGRAM_API_ID` و`TELEGRAM_API_HASH`، ولا تضع `VOICE_ACCOUNT_SESSION` داخل المستودع.

## إعداد سيرفر Minecraft

الإعداد الافتراضي الموجود في `env.example` هو `ARBICSMP.aternos.me:16503`، لكن Aternos قد يغير المنفذ أو يمنع الاتصال عندما يكون السيرفر متوقفاً. عدّل `MC_HOST` و`MC_PORT` إلى القيم الظاهرة في لوحة Aternos عند التشغيل.

هذا المشروع لا يقلد لوحة Aternos ولا يحاول تجاوز نظامها. أوامر `/serverstart` و`/serverstop` مخصصة للسيرفرات المحلية التي ينشئها المشروع، بينما Aternos يُشغّل من لوحته الرسمية. يمكن استعمال `/afkstart` بعد تشغيل السيرفر للاتصال بسيرفر Java المسموح به.

## أوامر مهمة

`/start` يعرض لوحة الأزرار. ` /afkstart host port username version` يشغّل Java AFK Client مع إعادة اتصال تلقائية. `/afkstop` يوقفه، و`/afkstatus` يعرض حالته. أمر `/serveradd` يحفظ بيانات سيرفر في قاعدة البيانات، بينما البحث عن Mods وModpacks وخدمات AI متاحة من الأزرار.

## الهدايا والترحيب

يستعمل AFK Client أوامر `/give` للهدايا بعد وقت عشوائي بين ساعة وساعتين، ويمنح 32 Diamond و45 Iron و60 Emerald و80 Coal و100 Gold للاعب الموجود وقت الاستحقاق. يجب أن يكون حساب AFK لديه صلاحية مناسبة، وإلا سيرفض السيرفر الأوامر. استعمل هذه الميزات فقط في سيرفر تملكه أو عندك إذن بإدارته.

## ملاحظات مهمة

دعم Bedrock موجود كسكريبت مستقل، لكنه ليس موصولاً بعد بكل أزرار البوت. كذلك لا يوجد ضمان مطلق لكل إصدار أو مود أو Proxy؛ يجب اختبار الإصدار الفعلي. الذكاء الاصطناعي اختياري لكنه يحتاج `GEMINI_KEY`.
