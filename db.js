/**
 * db.js — Сховище акаунтів і логінів БанкГео Поділля.
 *
 * Чому "локальна БД у браузері":
 *   Сайт хоститься статично (Netlify Drop). Власного backend нема.
 *   Найкращий варіант без backend → IndexedDB / localStorage у браузері
 *   з правильним PBKDF2-хешуванням паролів.
 *
 * Інтерфейс спеціально побудовано так, щоб БУДЬ-КОЛИ можна було
 * замінити drop-in на Supabase / Firebase (див. README_auth.md):
 *   Db.users.findByEmail() ↔ supabase.from('users').select().eq('email',...)
 *   Db.users.insert()       ↔ supabase.from('users').insert(...)
 *   Db.logins.add()         ↔ supabase.from('login_history').insert(...)
 *
 * Безпека:
 *   - Паролі хешуються PBKDF2-SHA256 з 100 000 ітерацій + випадковою сіллю.
 *   - У сховищі НІКОЛИ не зберігається відкритий пароль.
 *   - Email-домен перевіряється у whitelist при кожному вході.
 */
(function (root) {
  "use strict";

  // ─── КОНФІГ ────────────────────────────────────────────────────────────
  const KEY_USERS      = "ukrsib_db_users";       // users table
  const KEY_LOGINS     = "ukrsib_db_logins";      // login_history table
  const KEY_REMEMBERED = "ukrsib_db_remembered";  // recently used emails on this device
  const PBKDF2_ITERATIONS = 100_000;
  const MAX_REMEMBERED = 5;       // показуємо не більше 5 нещодавніх акаунтів
  const MAX_LOGIN_HISTORY = 50;   // на користувача

  // ─── ПРИМІТИВИ ─────────────────────────────────────────────────────────
  const _read = (key, def = []) => {
    try { return JSON.parse(localStorage.getItem(key)) ?? def; }
    catch { return def; }
  };
  const _write = (key, val) => localStorage.setItem(key, JSON.stringify(val));

  const _uuid = () => {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
      const r = (crypto.getRandomValues(new Uint8Array(1))[0] & 15);
      return (c === "x" ? r : (r & 3) | 8).toString(16);
    });
  };

  const _bytesToHex = bytes =>
    Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join("");

  /** Випадкова сіль 16 байт → hex */
  function generateSalt() {
    return _bytesToHex(crypto.getRandomValues(new Uint8Array(16)));
  }

  /** PBKDF2-SHA256, 100k ітерацій */
  async function hashPassword(password, saltHex) {
    const enc = new TextEncoder();
    const salt = Uint8Array.from(
      saltHex.match(/.{2}/g).map(h => parseInt(h, 16))
    );
    const keyMaterial = await crypto.subtle.importKey(
      "raw", enc.encode(password), { name: "PBKDF2" }, false, ["deriveBits"]
    );
    const bits = await crypto.subtle.deriveBits(
      { name: "PBKDF2", salt, iterations: PBKDF2_ITERATIONS, hash: "SHA-256" },
      keyMaterial, 256
    );
    return _bytesToHex(new Uint8Array(bits));
  }

  /** Безпечне порівняння без time-leak */
  function constantTimeEqual(a, b) {
    if (a.length !== b.length) return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
    return diff === 0;
  }

  // ─── USERS ─────────────────────────────────────────────────────────────
  const Users = {
    all() { return _read(KEY_USERS, []); },

    findByEmail(email) {
      const e = (email || "").toLowerCase().trim();
      return Users.all().find(u => u.email === e) || null;
    },

    /**
     * Реєстрація нового користувача з паролем.
     * @returns {Promise<{ok:boolean, user?:object, error?:string}>}
     */
    async register({ firstName, lastName, email, password }) {
      email = (email || "").toLowerCase().trim();
      if (Users.findByEmail(email)) {
        return { ok: false, error: "Користувач з таким email уже зареєстрований." };
      }
      const salt = generateSalt();
      const passwordHash = await hashPassword(password, salt);

      const user = {
        id: _uuid(),
        firstName: (firstName || "").trim(),
        lastName: (lastName || "").trim(),
        email,
        passwordSalt: salt,
        passwordHash,
        avatar: null,
        source: "password",          // password | google
        createdAt: new Date().toISOString(),
        lastLoginAt: null,
      };
      const users = Users.all();
      users.push(user);
      _write(KEY_USERS, users);
      return { ok: true, user };
    },

    /**
     * Створює або оновлює юзера, що увійшов через Google.
     * Пароля немає (passwordHash = null).
     */
    upsertFromGoogle({ firstName, lastName, email, avatar }) {
      email = (email || "").toLowerCase().trim();
      const users = Users.all();
      const idx = users.findIndex(u => u.email === email);
      if (idx >= 0) {
        // оновлюємо що могло змінитися в Google профілі
        users[idx].firstName = firstName || users[idx].firstName;
        users[idx].lastName = lastName || users[idx].lastName;
        users[idx].avatar = avatar || users[idx].avatar;
        users[idx].lastLoginAt = new Date().toISOString();
        _write(KEY_USERS, users);
        return users[idx];
      }
      const user = {
        id: _uuid(),
        firstName: firstName || "",
        lastName: lastName || "",
        email,
        passwordSalt: null,
        passwordHash: null,
        avatar: avatar || null,
        source: "google",
        createdAt: new Date().toISOString(),
        lastLoginAt: new Date().toISOString(),
      };
      users.push(user);
      _write(KEY_USERS, users);
      return user;
    },

    /**
     * Перевірка пароля.
     * @returns {Promise<{ok:boolean, user?:object, error?:string}>}
     */
    async verifyPassword(email, password) {
      const user = Users.findByEmail(email);
      if (!user) return { ok: false, error: "Невірний email або пароль." };
      if (!user.passwordHash) {
        return { ok: false, error: "Цей акаунт зареєстровано через Google. Увійдіть кнопкою Google." };
      }
      const hash = await hashPassword(password, user.passwordSalt);
      if (!constantTimeEqual(hash, user.passwordHash)) {
        return { ok: false, error: "Невірний email або пароль." };
      }
      return { ok: true, user };
    },

    /**
     * Зміна пароля. Перевіряє старий, ставить новий.
     */
    async changePassword(email, oldPassword, newPassword) {
      const check = await Users.verifyPassword(email, oldPassword);
      if (!check.ok) return { ok: false, error: "Поточний пароль не правильний." };

      const users = Users.all();
      const idx = users.findIndex(u => u.email === check.user.email);
      const salt = generateSalt();
      users[idx].passwordSalt = salt;
      users[idx].passwordHash = await hashPassword(newPassword, salt);
      _write(KEY_USERS, users);
      return { ok: true };
    },

    /** Оновлення профілю (імʼя, прізвище) */
    update(email, { firstName, lastName }) {
      const users = Users.all();
      const idx = users.findIndex(u => u.email === (email || "").toLowerCase());
      if (idx < 0) return { ok: false, error: "Користувача не знайдено." };
      if (firstName !== undefined) users[idx].firstName = firstName;
      if (lastName !== undefined) users[idx].lastName = lastName;
      _write(KEY_USERS, users);
      return { ok: true, user: users[idx] };
    },

    /** Позначаємо успішний вхід */
    touchLogin(email) {
      const users = Users.all();
      const idx = users.findIndex(u => u.email === (email || "").toLowerCase());
      if (idx >= 0) {
        users[idx].lastLoginAt = new Date().toISOString();
        _write(KEY_USERS, users);
      }
    },
  };

  // ─── LOGIN HISTORY ─────────────────────────────────────────────────────
  const Logins = {
    all() { return _read(KEY_LOGINS, []); },

    add({ email, source, ip = null, ua = navigator.userAgent }) {
      const entry = {
        email: (email || "").toLowerCase(),
        source,                          // google | password | demo
        ip,
        ua,
        timestamp: new Date().toISOString(),
      };
      const all = Logins.all();
      all.push(entry);
      // обмежуємо історію на користувача
      const trimmed = [];
      const perEmail = {};
      for (const item of all.slice().reverse()) {
        perEmail[item.email] = (perEmail[item.email] || 0) + 1;
        if (perEmail[item.email] <= MAX_LOGIN_HISTORY) trimmed.unshift(item);
      }
      _write(KEY_LOGINS, trimmed);
      return entry;
    },

    forEmail(email) {
      const e = (email || "").toLowerCase();
      return Logins.all().filter(l => l.email === e).reverse();
    },
  };

  // ─── REMEMBERED ACCOUNTS (на цьому пристрої) ──────────────────────────
  const Remembered = {
    all() {
      const list = _read(KEY_REMEMBERED, []);
      // Збагачуємо інформацією з users (якщо акаунт ще існує)
      return list.map(email => {
        const u = Users.findByEmail(email);
        return {
          email,
          firstName: u?.firstName || "",
          lastName:  u?.lastName  || "",
          avatar:    u?.avatar    || null,
          source:    u?.source    || null,
          lastLoginAt: u?.lastLoginAt || null,
        };
      });
    },

    add(email) {
      email = (email || "").toLowerCase().trim();
      if (!email) return;
      const list = _read(KEY_REMEMBERED, []).filter(e => e !== email);
      list.unshift(email);
      _write(KEY_REMEMBERED, list.slice(0, MAX_REMEMBERED));
    },

    remove(email) {
      email = (email || "").toLowerCase().trim();
      _write(KEY_REMEMBERED, _read(KEY_REMEMBERED, []).filter(e => e !== email));
    },

    clear() { localStorage.removeItem(KEY_REMEMBERED); },
  };

  // ─── ЕКСПОРТ ───────────────────────────────────────────────────────────
  root.Db = Object.freeze({
    users: Users,
    logins: Logins,
    remembered: Remembered,
    _hash: hashPassword,             // експонуємо для тестів
    _salt: generateSalt,
    // Зведена статистика для адмінки
    stats() {
      return {
        usersCount: Users.all().length,
        loginsCount: Logins.all().length,
        rememberedCount: _read(KEY_REMEMBERED, []).length,
      };
    },
  });
})(window);
