# Магазин изделий ручной работы для Telegram и MAX

Готовый магазин с единым каталогом:

- покупательский бот Telegram;
- покупательский бот MAX;
- отдельный закрытый Telegram-бот администратора;
- товары с фотографией, описанием, ценой и остатком;
- корзина и оформление заказа;
- уведомление администраторов о новых заказах;
- SQLite — отдельный сервер базы данных не нужен;
- polling для быстрого старта и HTTPS webhook MAX для production;
- запуск через Python или Docker.

Добавленный в админ-боте товар сразу виден покупателям и в Telegram, и в MAX. Онлайн-эквайринг намеренно не включён: после заказа мастер получает контакты и согласует оплату/доставку с покупателем.

## 1. Получите токены

### Telegram

В [@BotFather](https://t.me/BotFather) создайте два разных бота командой `/newbot`:

1. магазин для покупателей;
2. бот управления товарами.

Сохраните оба токена. Свой числовой Telegram ID можно узнать, например, у [@userinfobot](https://t.me/userinfobot). Доступ в админ-бот будет только у ID из настроек.

### MAX

Создайте и отправьте на модерацию бота на [платформе MAX для партнёров](https://business.max.ru/). По требованиям MAX создание API-бота доступно верифицированным юрлицам, ИП и самозанятым — резидентам РФ. Токен находится в расширенных настройках бота.

## 2. Настройте проект

Скопируйте `.env.example` в `.env` и заполните:

```env
TELEGRAM_SHOP_TOKEN=токен_покупательского_бота
TELEGRAM_ADMIN_TOKEN=токен_админ_бота
ADMIN_TELEGRAM_IDS=ваш_числовой_id
MAX_BOT_TOKEN=токен_MAX
SHOP_NAME=Моя мастерская
SHOP_CONTACT=@имя_для_связи
```

Для нескольких администраторов перечислите ID через запятую: `123,456,789`. Файл `.env` нельзя публиковать или отправлять посторонним.

## 3. Запустите

### Вариант A — Docker

Установите Docker Desktop и выполните в папке проекта:

```bash
docker compose up -d --build
docker compose logs -f
```

Остановка: `docker compose down`. Каталог, заказы и фотографии сохраняются в папке `data` и не исчезают при пересборке контейнера.

### Вариант B — Python 3.11+

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m src.main
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.main
```

Если `MAX_BOT_TOKEN` пуст, Telegram-боты всё равно запустятся.

## 4. Добавьте первый товар

1. Откройте отдельный Telegram админ-бот.
2. Нажмите «Запустить», затем «➕ Добавить товар».
3. По очереди отправьте название, описание, цену, остаток и фотографию.
4. Откройте покупательский бот Telegram или MAX и нажмите «Каталог».

В админ-боте также доступны:

- «📦 Товары» — показать, скрыть, снова опубликовать или удалить товар;
- «🧾 Заказы» — последние заказы;
- `/cancel` — отменить текущий ввод товара.

## Production webhook для MAX

Без дополнительных настроек MAX работает через long polling — это удобно для проверки. Для постоянной работы MAX требует webhook: публичный HTTPS URL на внешнем порту 443 с доверенным сертификатом. Приложение умеет автоматически зарегистрировать такой URL.

Направьте запросы reverse proxy с `https://shop.example.ru/max-webhook` на порт `8080` контейнера и заполните:

```env
MAX_WEBHOOK_URL=https://shop.example.ru/max-webhook
MAX_WEBHOOK_SECRET=длинная_случайная_строка_без_пробелов
WEBHOOK_PORT=8080
```

Допустимы латинские буквы, цифры, `_` и `-`; длина секрета 5–256 символов. При старте приложение регистрирует подписку и проверяет заголовок `X-Max-Bot-Api-Secret`. Если у бота уже есть webhook на другом URL, удалите старую подписку в настройках/API MAX — одновременно webhook и polling не работают.

## Проверка

Тесты базы, цен и остатков не обращаются к Telegram/MAX:

```bash
python -m unittest discover -v
```

Проверка синтаксиса всех модулей:

```bash
python -m compileall -q src tests
```

## Перед публикацией

- Заполните реквизиты продавца, оплату, доставку и возвраты.
- Отредактируйте `PRIVACY.md` и `TERMS.md`; это шаблоны, не юридическая консультация.
- Разместите эти документы по публичным HTTPS-ссылкам и укажите их в карточках/описаниях ботов.
- Делайте резервную копию `data/shop.db` и `data/products`.
- Не запускайте два экземпляра приложения с одной базой и одними токенами одновременно.

## Структура

```text
src/main.py            запуск трёх ботов
src/telegram_shop.py   магазин Telegram
src/max_shop.py        магазин MAX
src/admin_bot.py       управление через отдельный Telegram-бот
src/db.py              каталог, корзины, заказы и остатки
data/shop.db           база (создаётся автоматически)
data/products/         фотографии товаров
```

Официальная документация: [Telegram Bot API](https://core.telegram.org/bots/api), [MAX Bot API](https://dev.max.ru/docs-api), [создание бота MAX](https://dev.max.ru/docs/chatbots/bots-create/create).

## Публикация в GitHub

Проект уже содержит `.gitignore`, GitHub Actions CI, Dependabot и шаблоны Issues/PR. Перед первым push убедитесь, что `.env` не отображается в `git status`:

```bash
git status --short
git add .
git commit -m "Initial release: Telegram and MAX handmade shop bots"
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/handmade-shop-bots.git
git push -u origin main
```

После публикации откройте вкладку **Actions**: workflow `CI` должен пройти на Python 3.11 и 3.13. Затем включите в настройках GitHub репозитория **Private vulnerability reporting**. По умолчанию проект публикуется без открытой лицензии (все права сохраняются); если хотите разрешить другим копирование и модификацию, отдельно выберите подходящую лицензию на GitHub.
