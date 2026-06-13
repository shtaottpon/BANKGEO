/**
 * auth-guard.js — захист сторінок БанкГео від несанкціонованого доступу.
 *
 * Інтеграція в index.html (основна сторінка): додати у <head> один рядок:
 *     <script src="auth-guard.js"></script>
 * (бажано перед db.js — guard сам додасть посилання на нього за потреби)
 *
 * Що робить:
 *   1. Шукає активну сесію спочатку в sessionStorage, потім у localStorage
 *      (для випадку "Запамʼятати мене" — 30 днів).
 *   2. Перевіряє: не прострочена, домен у whitelist.
 *   3. Якщо ОК — рендерить бейдж юзера з кнопкою "Профіль" і "Вихід".
 *   4. Якщо ні — редирект на login.html.
 *
 * Безпека: client-side guard для прототипу. У продакшні JWT валідується backend-ом.
 */
(function () {
  "use strict";

  const ALLOWED_DOMAINS = ["ukrsibbank.com", "ukrsibbank.ua", "bnpparibas.com"];
  const LOGIN_URL = "login.html";
  const ACCOUNT_URL = "account.html";
  const STORAGE_KEY = "ukrsib_auth";              // sessionStorage — основна сесія
  const PERSISTENT_KEY = "ukrsib_auth_persistent"; // localStorage — "запамʼятати мене"

  // ─── 1. Знаходимо валідну сесію ────────────────────────────────────────
  function redirect(reason) {
    sessionStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(PERSISTENT_KEY);
    location.replace(LOGIN_URL + "?" + reason + "=1");
  }

  function loadSession() {
    // Спершу sessionStorage (поточна вкладка)
    let raw = sessionStorage.getItem(STORAGE_KEY);
    let fromPersistent = false;
    if (!raw) {
      // Якщо немає — пробуємо localStorage (persistent "Remember me")
      raw = localStorage.getItem(PERSISTENT_KEY);
      fromPersistent = true;
    }
    if (!raw) return null;
    try {
      const s = JSON.parse(raw);
      if (!s || !s.email || !s.exp) return null;
      // Якщо відновили з persistent — піднімаємо в session
      if (fromPersistent) sessionStorage.setItem(STORAGE_KEY, raw);
      return s;
    } catch { return null; }
  }

  const session = loadSession();
  if (!session)                       { redirect("denied");  return; }
  if (Date.now() > session.exp)       { redirect("expired"); return; }
  const domain = (session.email || "").toLowerCase().split("@")[1];
  if (!ALLOWED_DOMAINS.includes(domain)) { redirect("denied"); return; }

  // ─── 2. Завантажуємо db.js якщо ще не завантажено (для сторінок, де він не підключений напряму) ──
  function ensureDb(cb) {
    if (window.Db) return cb();
    const s = document.createElement("script");
    s.src = "db.js";
    s.onload = cb;
    s.onerror = cb;  // продовжуємо без БД — бейдж буде працювати з даних сесії
    document.head.appendChild(s);
  }

  // ─── 3. Малюємо бейдж юзера ───────────────────────────────────────────
  function injectAuthBar() {
    if (document.getElementById("authBar")) return;

    const css = `
      #authBar{
        position:fixed;top:14px;right:14px;z-index:9999;
        display:flex;align-items:center;gap:8px;
        background:rgba(255,255,255,.96);backdrop-filter:blur(8px);
        border:1px solid #D7E4DD;border-radius:14px;
        padding:6px 8px 6px 6px;box-shadow:0 6px 20px rgba(0,88,60,.16);
        font-family:'Manrope','Segoe UI',sans-serif;font-size:13px;color:#12211B;
      }
      #authBar .avatar{
        width:32px;height:32px;border-radius:50%;flex:none;
        background:linear-gradient(135deg,#00583C,#0FB89B);color:#fff;
        display:flex;align-items:center;justify-content:center;
        font-weight:800;font-size:12.5px;letter-spacing:.5px;overflow:hidden;
      }
      #authBar .avatar img{width:100%;height:100%;object-fit:cover}
      #authBar .who{display:flex;flex-direction:column;line-height:1.2;padding-right:2px;min-width:0}
      #authBar .who b{font-size:12.5px;font-weight:800;color:#00583C;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:160px}
      #authBar .who small{font-size:10.5px;color:#5C6E66;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:160px}
      #authBar .src{
        font-size:9px;font-weight:800;letter-spacing:.6px;text-transform:uppercase;
        padding:2px 5px;border-radius:999px;
      }
      #authBar .src.google{background:#E8F1FE;color:#1A56DB}
      #authBar .src.password{background:#EAF6F0;color:#00583C}
      #authBar .src.demo{background:#FFF6E5;color:#8C5E0A}
      #authBar a.account{
        background:#fff;border:1px solid #D7E4DD;border-radius:9px;
        padding:6px 9px;font:inherit;font-weight:700;font-size:12px;
        color:#00583C;cursor:pointer;text-decoration:none;transition:.15s;
        display:inline-flex;align-items:center;gap:5px;
      }
      #authBar a.account:hover{background:#F0F7F3;border-color:#00915A}
      #authBar button.logout{
        background:#fff;border:1px solid #D7E4DD;border-radius:9px;
        padding:6px 9px;font:inherit;font-weight:700;font-size:12px;
        color:#C0392B;cursor:pointer;transition:.15s;
      }
      #authBar button.logout:hover{background:#FDECEA;border-color:#F1B0AA}
      @media(max-width:720px){
        #authBar{top:8px;right:8px;padding:5px 6px 5px 5px;gap:6px}
        #authBar .who small{display:none}
        #authBar a.account span,#authBar button.logout span{display:none}
      }
    `;
    const style = document.createElement("style");
    style.textContent = css;
    document.head.appendChild(style);

    const fullName = [session.firstName, session.lastName].filter(Boolean).join(" ")
                     || session.name || session.email;
    const initials = fullName.split(/\s+/).slice(0,2)
      .map(s => s.charAt(0).toUpperCase()).join("") || "U";
    const avatar = session.picture
      ? `<img src="${escapeHTML(session.picture)}" alt="" referrerpolicy="no-referrer">`
      : initials;

    const srcLabel = session.source === "google" ? "Google" : "Email";

    const bar = document.createElement("div");
    bar.id = "authBar";
    bar.innerHTML = `
      <div class="avatar">${avatar}</div>
      <div class="who">
        <b>${escapeHTML(fullName)}</b>
        <small>${escapeHTML(session.email)}</small>
      </div>
      <span class="src ${session.source}" title="Джерело авторизації">${srcLabel}</span>
      <a class="account" href="${ACCOUNT_URL}" title="Профіль">👤<span> Профіль</span></a>
      <button class="logout" type="button" id="authLogout" title="Вийти">⎋<span> Вихід</span></button>
    `;
    document.body.appendChild(bar);

    document.getElementById("authLogout").addEventListener("click", logout);
  }

  function logout() {
    if (window.google && google.accounts && google.accounts.id) {
      try { google.accounts.id.disableAutoSelect(); } catch (e) {}
    }
    sessionStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(PERSISTENT_KEY);
    location.replace(LOGIN_URL + "?logout=1");
  }

  function escapeHTML(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"
    })[c]);
  }

  ensureDb(() => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", injectAuthBar);
    } else {
      injectAuthBar();
    }
  });

  // ─── 4. Експорт API ───────────────────────────────────────────────────
  window.UkrSibAuth = Object.freeze({
    user: Object.freeze({
      email: session.email,
      name: session.name,
      firstName: session.firstName || "",
      lastName: session.lastName || "",
      source: session.source,
      issuedAt: new Date(session.issued).toISOString(),
      expiresAt: new Date(session.exp).toISOString(),
      remembered: !!session.remember,
    }),
    remainingMs: () => Math.max(0, session.exp - Date.now()),
    logout,
  });

  // ─── 5. Авто-логаут при закінченні сесії (якщо вкладка лишилась відкритою) ──
  const remaining = session.exp - Date.now();
  if (remaining > 0 && remaining < 24 * 60 * 60 * 1000) {
    setTimeout(() => {
      alert("Сесія БанкГео завершилась. Будь ласка, увійдіть знову.");
      sessionStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(PERSISTENT_KEY);
      location.replace(LOGIN_URL + "?expired=1");
    }, remaining);
  }
})();
