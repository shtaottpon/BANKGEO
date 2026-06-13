# Авторизація через Google · БанкГео Поділля

Доступ до даних обмежено: лише працівники з корпоративним акаунтом
**@ukrsibbank.com**, **@ukrsibbank.ua** або **@bnpparibas.com**.

## Файли

| Файл | Призначення |
|---|---|
| `login.html` | Сторінка входу: Google Sign-In + демо-вхід |
| `auth-guard.js` | Захист `sait_osnova.html` — 1 рядок інтеграції |
| `README_auth.md` | Цей файл — інструкція |

## Як працює (1 хвилина читання)

1. Користувач відкриває `sait_osnova.html`.
2. `auth-guard.js` (підключений у `<head>`) перевіряє `sessionStorage`.
3. Якщо сесії немає / прострочена / email не з whitelist → редирект на `login.html`.
4. У `login.html` користувач тисне **Sign in with Google** → Google повертає підписаний JWT.
5. Скрипт перевіряє `email_verified` і домен → зберігає сесію у `sessionStorage` → редирект на `sait_osnova.html`.
6. У верхньому правому куті основного сайту зʼявляється бейдж із іменем і кнопкою **Вихід**.

**Чому `sessionStorage`, а не `localStorage`:** сесія знищується, коли користувач закриває вкладку. Менший ризик при роботі на спільному компʼютері у відділенні.

**Тривалість сесії:** 8 годин (один робочий день). Після цього — повторний вхід.

## Інтеграція в існуючий сайт (1 рядок)

У `sait_osnova.html`, у блоці `<head>`, додати найпершим скриптом:

```html
<script src="auth-guard.js"></script>
```

Усе. Цей скрипт або редиректить на login, або тихо рендерить бейдж юзера.

## Налаштування Google OAuth (5 хвилин)

Для **реального** Google Sign-In треба зареєструвати OAuth Client ID.
**Для презентації цього робити НЕ обовʼязково** — є демо-вхід (форма з email).

### Кроки:

1. Відкрити [console.cloud.google.com](https://console.cloud.google.com)
2. Створити проєкт (наприклад, "БанкГео Поділля")
3. **APIs & Services → OAuth consent screen:**
   - User type: **Internal** (якщо є Google Workspace банку) або **External** + додати тестових юзерів
   - App name: БанкГео Поділля
   - User support email: ваш корпоративний
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID:**
   - Application type: **Web application**
   - Authorized JavaScript origins: `http://localhost:8000` (для тесту) та фактичний домен для продакшну
5. Скопіювати Client ID
6. У `login.html` знайти рядок:
   ```js
   const GOOGLE_CLIENT_ID = "YOUR_CLIENT_ID.apps.googleusercontent.com";
   ```
   замінити на свій ID.

## Запуск локально

Google Sign-In **не працює з `file://`** (вимагає http/https origin). Запустити локальний сервер:

```bash
cd ~/Desktop/хакатон
python3 -m http.server 8000
```

Відкрити `http://localhost:8000/login.html` у браузері.

> Демо-вхід (форма з email) працює і з `file://`, і з `localhost`.

## Безпека: що захищено, а що ні

✅ **Захищено:**
- Доступ до інтерфейсу — без корпоративного email сторінка не відкривається.
- JWT від Google підписаний Google — підробити його неможливо.
- Сесія обмежена 8 годинами, зберігається лише до закриття вкладки.
- Whitelist доменів — навіть з валідним Google-акаунтом сторонній не зайде.

⚠️ **НЕ захищено (потрібен backend у продакшні):**
- Сам файл `sait_osnova.html` можна завантажити обхідним шляхом, якщо віддавати статикою.
- Дані з vkursi.pro у продакшні мають віддаватися лише авторизованому JWT через backend API.
- Демо-режим обходиться будь-яким email потрібного домену — він **тільки для презентації**.

**Шлях до production-безпеки** (наступний крок після хакатону):
1. Винести `sait_osnova.html` за backend (Node/FastAPI), який вимагає Bearer JWT.
2. Backend перевіряє JWT через Google tokeninfo або публічні ключі.
3. Дані з vkursi.pro фетчаться backend-ом, а не з браузера.
4. CSP-заголовки, HTTPS only, SameSite cookies.

## Whitelist доменів — як змінити

У двох місцях має бути синхронний список:
- `login.html` → `const ALLOWED_DOMAINS = [...]`
- `auth-guard.js` → `const ALLOWED_DOMAINS = [...]`

## Демо-сценарій для журі

1. Відкриваєте `sait_osnova.html` напряму → автоматично кидає на `login.html`.
2. Намагаєтесь увійти з `test@gmail.com` → бачите помилку "Доступ лише з корпоративних доменів".
3. Вводите `ivan.petrenko@ukrsibbank.com` → потрапляєте на основний сайт із бейджем "Іван Петренко · Demo".
4. Тиснете **Вихід** → знову на `login.html`.

Це показує: безпека продумана, обмеження прозоре, а під капотом — справжня логіка whitelisting, JWT-валідація і sessionStorage isolation.
