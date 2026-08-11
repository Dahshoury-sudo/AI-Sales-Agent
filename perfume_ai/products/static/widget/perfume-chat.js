(function () {
  "use strict";

  // ─── Configuration ───────────────────────────────────────────────
  // محاولة جلب السكريبت الحالي، ولو فشلت بنبحث عن أي سكريبت معاه data-api-key
  const scriptTag = document.currentScript || document.querySelector('script[data-api-key]');
  const API_KEY = scriptTag?.getAttribute("data-api-key") || "";
  const API_BASE =
    scriptTag?.getAttribute("data-api-base") ||
    scriptTag?.src.replace(/\/static\/widget\/perfume-chat.*\.js.*$/, "") ||
    "";
  const WELCOME_MESSAGE =
    scriptTag?.getAttribute("data-welcome") ||
    "اهلا وسهلا بحضرتك يا فندم انا مساعد Perfamix الذكي. اقدر اساعدك ازاي";
  // لو اللوجو مش مبعوت، ممكن نحط لوجو Perfamix كاحتياطي دائم
  const BRAND_LOGO = scriptTag?.getAttribute("data-logo") || "https://res.cloudinary.com/dtssxxfra/image/upload/v1786119236/Perfamix_lerbcn.svg";
  const POSITION = scriptTag?.getAttribute("data-position") || "right"; // left or right
  const STORAGE_KEY = "pfx_widget_conv_id";
  const STORAGE_KEY_OPEN = "pfx_widget_open";

  if (!API_KEY) {
    console.warn("[Perfamix Widget] Missing data-api-key attribute on the script tag.");
    return;
  }

  // ─── State ───────────────────────────────────────────────────────
  let conversationId = localStorage.getItem(STORAGE_KEY) || null;
  let isOpen = false;
  let unreadCount = 0;

  // ─── Create Host Element ─────────────────────────────────────────
  //
  // The host is position:fixed, covering the full layout viewport at all times.
  // Its inline style is set once and NEVER changed by the keyboard handler.
  //
  // Why position:fixed for the host:
  //   - Takes it out of normal document flow → never contributes to
  //     document.body.scrollWidth → page can never become horizontally
  //     scrollable because of the widget.
  //   - overflow:hidden keeps any accidental child bleed inside the host
  //     bounds (though fixed-positioned children still escape per CSS spec;
  //     we handle that by explicitly sizing the chat window to the viewport).
  //   - pointer-events:none passes all clicks through transparent host areas;
  //     interactive children individually restore pointer-events:auto.
  //
  const host = document.createElement("div");
  host.id = "perfamix-chat-widget";
  host.style.cssText = [
    "position:fixed",
    "top:0",
    "left:0",
    "width:100%",
    "height:100%",
    "overflow:hidden",
    "pointer-events:none",
    "z-index:2147483640",
  ].join(";");
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: "closed" });

  // ─── Styles ──────────────────────────────────────────────────────
  const styles = document.createElement("style");
  styles.textContent = `
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap');

    :host {
      all: initial;
      font-family: 'Tajawal', 'Segoe UI', sans-serif;
      direction: rtl;
      display: block;
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      pointer-events: none;
      z-index: 2147483640;
    }

    *, *::before, *::after {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    /* ── Variables ─────────────────────────────────────────────── */
    :host {
      /* Brand Green Palette */
      --pfx-primary:        #264e36;          /* Power Green */
      --pfx-primary-dark:   #152c1e;          /* deep forest green */
      --pfx-primary-hover:  #326647;
      --pfx-primary-muted:  #43855d;
      --pfx-primary-light:  rgba(38, 78, 54, 0.20);
      --pfx-primary-glow:   rgba(38, 78, 54, 0.35);

      /* Green Background Palette — Power Green Theme */
      --pfx-bg-dark:        #264e36;          /* header/footer bg — Power Green */
      --pfx-bg-glass:       rgba(38, 78, 54, 0.98);
      --pfx-bg-glass-light: #152c1e;          /* AI bubble / input bg */
      --pfx-border:         #152c1e;          /* subtle border */
      --pfx-border-light:   #326647;          /* slightly lighter border */
      --pfx-text:           #ffffff;          /* white text */
      --pfx-text-muted:     #8ab29a;          /* muted green text */

      /* Shadow */
      --pfx-shadow: 0 20px 60px rgba(0,0,0,0.60), 0 0 0 1px rgba(38, 78, 54, 0.12);

      /* Layout */
      --pfx-bubble-size: 65px;
      --pfx-window-width: 400px;
      --pfx-window-height: 580px;
      --pfx-radius: 20px;
      --pfx-z: 2147483640;
    }

    /* ── Bubble ────────────────────────────────────────────────── */
    .pfx-bubble {
      position: fixed;
      bottom: 24px;
      ${POSITION === "right" ? "right: 24px;" : "left: 24px;"}
      width: var(--pfx-bubble-size);
      height: var(--pfx-bubble-size);
      border-radius: 50%;
      background: var(--pfx-primary);
      cursor: pointer;
      z-index: var(--pfx-z);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 8px 32px var(--pfx-primary-glow), 0 0 0 0 var(--pfx-primary-glow);
      transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s ease;
      animation: pfx-pulse 2.5s infinite;
      pointer-events: auto; /* restore interaction — host is pointer-events:none */
    }

    .pfx-bubble:hover {
      transform: scale(1.08);
      box-shadow: 0 12px 40px var(--pfx-primary-glow);
      animation: none;
    }

    .pfx-bubble.open {
      animation: none;
      transform: rotate(0deg);
    }

    .pfx-bubble svg {
      width: 28px;
      height: 28px;
      fill: white;
      transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.3s ease;
    }

    .pfx-bubble .pfx-icon-close {
      position: absolute;
      opacity: 0;
      transform: rotate(-90deg) scale(0.5);
    }

    .pfx-bubble.open .pfx-icon-chat {
      opacity: 0;
      transform: rotate(90deg) scale(0.5);
    }

    .pfx-bubble.open .pfx-icon-close {
      opacity: 1;
      transform: rotate(0deg) scale(1);
    }

    /* Badge */
    .pfx-badge {
      position: absolute;
      top: -4px;
      ${POSITION === "right" ? "left: -4px;" : "right: -4px;"}
      background: #ef4444;
      color: white;
      font-size: 11px;
      font-weight: 700;
      min-width: 22px;
      height: 22px;
      border-radius: 11px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 0 5px;
      box-shadow: 0 2px 8px rgba(239, 68, 68, 0.5);
      transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
      transform: scale(0);
    }

    .pfx-badge.show {
      transform: scale(1);
    }

    @keyframes pfx-pulse {
      0% { box-shadow: 0 8px 32px rgba(38, 78, 54, 0.5), 0 0 0 0 rgba(38, 78, 54, 0.4); }
      70% { box-shadow: 0 8px 32px rgba(38, 78, 54, 0.5), 0 0 0 18px rgba(38, 78, 54, 0); }
      100% { box-shadow: 0 8px 32px rgba(38, 78, 54, 0.5), 0 0 0 0 rgba(38, 78, 54, 0); }
    }

    /* ── Chat Window ──────────────────────────────────────────── */
    .pfx-window {
      position: fixed;
      bottom: calc(24px + var(--pfx-bubble-size) + 16px);
      ${POSITION === "right" ? "right: 24px;" : "left: 24px;"}
      width: var(--pfx-window-width);
      /* Never let the window overflow off the left/right edge of the screen */
      max-width: min(var(--pfx-window-width), calc(100vw - 48px));
      height: var(--pfx-window-height);
      max-height: calc(100vh - 140px);
      background: var(--pfx-bg-dark);
      border-radius: var(--pfx-radius);
      box-shadow: var(--pfx-shadow);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      z-index: var(--pfx-z);
      opacity: 0;
      transform: translateY(20px) scale(0.95);
      pointer-events: none;
      transition: opacity 0.35s cubic-bezier(0.16, 1, 0.3, 1),
                  transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
    }

    .pfx-window.open {
      opacity: 1;
      transform: translateY(0) scale(1);
      pointer-events: auto; /* restore interaction — host is pointer-events:none */
    }

    /* Gradient overlay at top */
    .pfx-window::before {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 120px;
      background: linear-gradient(180deg, var(--pfx-primary-light) 0%, transparent 100%);
      pointer-events: none;
      z-index: 0;
    }

    /* ── Header ───────────────────────────────────────────────── */
    .pfx-header {
      position: relative;
      z-index: 1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      background: var(--pfx-bg-dark);
      border-bottom: 1px solid var(--pfx-border);
    }

    .pfx-header-info {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .pfx-avatar {
      width: 40px;
      height: 40px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--pfx-primary) 0%, #1a3826 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      flex-shrink: 0;
    }

    .pfx-header-text h3 {
      color: var(--pfx-text);
      font-size: 15px;
      font-weight: 700;
      margin: 0;
      line-height: 1.3;
    }

    .pfx-header-text span {
      color: var(--pfx-text-muted);
      font-size: 12px;
      display: flex;
      align-items: center;
      gap: 5px;
    }

    .pfx-header-text span::before {
      content: '';
      width: 7px;
      height: 7px;
      background: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 8px #10b981;
      display: inline-block;
    }

    .pfx-header-actions {
      display: flex;
      gap: 4px;
    }

    .pfx-header-btn {
      height: 34px;
      padding: 0 14px;
      border-radius: 20px;
      border: 1px solid var(--pfx-border-light);
      background: var(--pfx-bg-glass-light);
      color: var(--pfx-text);
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 6px;
      justify-content: center;
      transition: all 0.2s ease;
      font-size: 13px;
      font-family: inherit;
      font-weight: 600;
    }

    .pfx-header-btn:hover {
      background: var(--pfx-border);
      border-color: var(--pfx-text-muted);
    }

    /* ── Messages ─────────────────────────────────────────────── */
    .pfx-messages {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      scroll-behavior: smooth;
      position: relative;
      z-index: 1;
      background-color: #f5f1f1ff;
    }

    .pfx-messages::-webkit-scrollbar { width: 4px; }
    .pfx-messages::-webkit-scrollbar-track { background: transparent; }
    .pfx-messages::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 4px; }
    .pfx-messages::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.15); }

    .pfx-msg {
      max-width: 82%;
      padding: 12px 16px;
      border-radius: 18px;
      font-size: 14px;
      line-height: 1.65;
      color: var(--pfx-text);
      animation: pfx-msgIn 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards;
      opacity: 0;
      white-space: pre-wrap;
      word-wrap: break-word;
    }

    .pfx-msg.user {
      background: var(--pfx-primary);
      align-self: flex-start;
      border-bottom-right-radius: 6px;
      color: #ffffff;
      box-shadow: 0 4px 12px var(--pfx-primary-light);
    }

    .pfx-msg.ai {
      background: #1b3725;
      align-self: flex-end;
      border-bottom-left-radius: 6px;
      border: 1px solid var(--pfx-border-light);
    }

    .pfx-msg img {
      max-width: 100%;
      border-radius: 12px;
      margin-top: 8px;
      display: block;
    }

    @keyframes pfx-msgIn {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* ── Typing Indicator ─────────────────────────────────────── */
    .pfx-typing {
      align-self: flex-end;
      background: var(--pfx-bg-glass-light);
      border: 1px solid var(--pfx-border-light);
      border-radius: 18px;
      border-bottom-left-radius: 6px;
      padding: 14px 20px;
      display: none;
      gap: 5px;
      width: fit-content;
    }

    .pfx-typing.show { display: flex; }

    .pfx-typing span {
      width: 7px;
      height: 7px;
      background: var(--pfx-text-muted);
      border-radius: 50%;
      animation: pfx-bounce 1.4s infinite ease-in-out both;
    }

    .pfx-typing span:nth-child(1) { animation-delay: -0.32s; }
    .pfx-typing span:nth-child(2) { animation-delay: -0.16s; }

    @keyframes pfx-bounce {
      0%, 80%, 100% { transform: scale(0); }
      40% { transform: scale(1); }
    }

    /* ── Input ────────────────────────────────────────────────── */
    .pfx-input-area {
      position: relative;
      z-index: 1;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 14px 16px;
      border-top: 1px solid var(--pfx-border);
      background: var(--pfx-bg-dark);
    }

    .pfx-input {
      flex: 1;
      border: 1px solid var(--pfx-border);
      border-radius: 50px;
      padding: 12px 20px;
      /*
       * CRITICAL iOS FIX: font-size must be >= 16px.
       * iOS Safari auto-zooms (scales) the visual viewport when a focused
       * input has font-size < 16px. That zoom changes visualViewport.scale,
       * shrinks visualViewport.width, and shifts visualViewport.offsetLeft —
       * which is the root cause of the iOS horizontal drift bug on iPhone.
       * Keeping font-size at 16px prevents the zoom from ever triggering.
       */
      font-size: 16px;
      color: var(--pfx-text);
      background: var(--pfx-bg-glass-light);
      outline: none;
      font-family: 'Tajawal', 'Segoe UI', sans-serif;
      direction: rtl;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }

    .pfx-input::placeholder {
      color: var(--pfx-text-muted);
      opacity: 0.7;
    }

    .pfx-input:focus {
      border-color: var(--pfx-primary);
      box-shadow: 0 0 0 3px rgba(38, 78, 54, 0.2);
    }

    .pfx-send {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      border: none;
      background: linear-gradient(135deg, #1b3725 0%, #152c1e 100%);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }

    .pfx-send:hover {
      transform: scale(1.06);
      box-shadow: 0 4px 16px rgba(38, 78, 54, 0.5);
    }

    .pfx-send:active { transform: scale(0.95); }

    .pfx-send svg {
      width: 18px;
      height: 18px;
      fill: white;
      transform: scaleX(-1);
    }

    .pfx-send:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }

    /* ── Powered By ─────────────────────────────────────────────── */
    .pfx-powered {
      text-align: center;
      padding: 10px 16px 12px;
      font-size: 11px;
      color: var(--pfx-text-muted);
      background: var(--pfx-bg-dark);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      letter-spacing: 0.02em;
      position: relative;
      direction: ltr;
    }

    /* gradient line at top */
    .pfx-powered::before {
      content: '';
      position: absolute;
      top: 0;
      left: 20%;
      right: 20%;
      height: 1px;
      background: linear-gradient(
        90deg,
        transparent,
        var(--pfx-border-light) 40%,
        var(--pfx-primary-muted) 50%,
        var(--pfx-border-light) 60%,
        transparent
      );
    }

    .pfx-powered .pfx-powered-icon {
      font-size: 12px;
      opacity: 0.6;
      animation: pfx-sparkle 3s ease-in-out infinite;
      display: inline-block;
    }

    @keyframes pfx-sparkle {
      0%, 100% { opacity: 0.4; transform: scale(1) rotate(0deg); }
      50%       { opacity: 0.9; transform: scale(1.2) rotate(20deg); }
    }

    .pfx-powered a {
      color: #ffffff;
      text-decoration: none;
      font-weight: 700;
      font-size: 11.5px;
      letter-spacing: 0.04em;
      background: linear-gradient(
        90deg,
        #a8d5ba 0%,
        #ffffff 50%,
        #a8d5ba 100%
      );
      background-size: 200% auto;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      transition: background-position 0.5s ease;
    }

    .pfx-powered a:hover {
      background-position: right center;
    }


    /* ── Mobile Responsive ────────────────────────────────────── */
    @media (max-width: 480px) {
      :host {
        --pfx-bubble-size: 55px;
      }
      .pfx-window {
        width: auto;
        max-width: none;
        height: auto;
        max-height: none;
        top: env(safe-area-inset-top, 48px);
        bottom: calc(24px + var(--pfx-bubble-size) + 16px);
        /* Margins from screen edge — JS overrides these with pixel values
           because env()/max() can be unreliable inside Shadow DOM on iOS. */
        left: max(16px, env(safe-area-inset-left));
        right: max(16px, env(safe-area-inset-right));
        border-radius: 16px;
      }

      /*
       * ── keyboard-open state (both iOS and Android) ──────────────
       *
       * When the keyboard opens, JS sets inline styles on .pfx-window that
       * override the CSS rules above. The key design decisions:
       *
       * 1. position:fixed — we keep position:fixed (not absolute) so the
       *    containing block is the layout viewport, not the shadow host.
       *    This avoids complex shadow-DOM containing-block interactions.
       *
       * 2. left:0 / right:0 — BOTH sides are explicitly set to 0 as inline
       *    styles by JS. This is critical:
       *
       *    The CSS media-query above sets right:max(16px,...). If JS only
       *    clears the inline right style (right=""), the CSS rule becomes
       *    active again. With direction:rtl on the host AND <html dir="rtl">
       *    on the customer page, CSS over-constraint resolution gives PRIORITY
       *    to the "right" property. Result: a 16px gap appears on the right
       *    side of the chat window because right:16px offsets the element's
       *    right edge inward by 16px.
       *
       *    Fix: set left:0 AND right:0 as inline styles. Without a width
       *    constraint, the element stretches to fill the space between left:0
       *    and right:0 — exactly the full viewport width — in any direction.
       *
       * 3. No width — we deliberately do NOT set width when both left:0 and
       *    right:0 are set. This eliminates any CSS over-constraint resolution
       *    ambiguity regardless of direction (LTR or RTL).
       *
       * 4. transform:none — Safari creates a new fixed-element containing block
       *    when a transform is applied to a position:fixed element, breaking
       *    its viewport-anchored geometry. transform:none prevents this.
       */
      .pfx-window.keyboard-open {
        border-radius: 0;
        transform: none !important;
      }

      /* Hide the bubble while keyboard is open — it sits behind the full-screen
         chat window and its fixed position becomes unpredictable during viewport
         transitions. JS adds/removes this class on the bubble element. */
      .pfx-bubble.pfx-bubble-hidden {
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.15s ease;
      }
    }

    /* ── Scroll containment (prevent page scroll bleed) ────────── */
    .pfx-messages {
      overscroll-behavior: contain;       /* Chrome/Firefox + Safari 16+ */
      -webkit-overflow-scrolling: touch;  /* older iOS */
    }
  `;
  shadow.appendChild(styles);

  // ─── Build HTML ──────────────────────────────────────────────────
  const container = document.createElement("div");
  container.innerHTML = `
    <!-- Floating Bubble -->
    <div class="pfx-bubble" id="pfxBubble" aria-label="فتح المحادثة">
      <svg class="pfx-icon-chat" viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>
      <svg class="pfx-icon-close" viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
      <div class="pfx-badge" id="pfxBadge">0</div>
    </div>

    <!-- Chat Window -->
    <div class="pfx-window" id="pfxWindow">
      <!-- Header -->
      <div class="pfx-header">
        <div class="pfx-header-info">
          <div class="pfx-avatar" style="${BRAND_LOGO ? 'background: #fff; border-radius: 50%; overflow: hidden;' : ''}">
            ${BRAND_LOGO ? `<img src="${BRAND_LOGO}" alt="Logo" style="width:100%; height:100%; object-fit:contain; padding:4px; box-sizing:border-box;">` : '🤖'}
          </div>
          <div class="pfx-header-text">
            <h3>Perfamix AI</h3>
            <span>متصل الآن</span>
          </div>
        </div>
        <div class="pfx-header-actions">
          <button class="pfx-header-btn" id="pfxNewChat" title="محادثة جديدة">
            <span>محادثة جديدة</span>
            <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round" width="14" height="14" stroke="currentColor" stroke-width="2" fill="none"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
          </button>
        </div>
      </div>

      <!-- Messages -->
      <div class="pfx-messages" id="pfxMessages"></div>

      <!-- Typing Indicator -->
      <div class="pfx-typing" id="pfxTyping">
        <span></span><span></span><span></span>
      </div>

      <!-- Input -->
      <div class="pfx-input-area">
        <input class="pfx-input" id="pfxInput" type="text" placeholder="اكتب رسالتك هنا..." autocomplete="off" />
        <button class="pfx-send" id="pfxSend" aria-label="إرسال">
          <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
        </button>
      </div>

      <!-- Powered By -->
      <div class="pfx-powered">
        <span class="pfx-powered-icon">✶</span>
        <span>Powered by</span>
        <a href="https://webvitas.com" target="_blank" rel="noopener noreferrer">WebVitas AI</a>
      </div>
    </div>
  `;
  shadow.appendChild(container);

  // ─── DOM References ──────────────────────────────────────────────────
  const bubble = shadow.getElementById("pfxBubble");
  const badge = shadow.getElementById("pfxBadge");
  const chatWindow = shadow.getElementById("pfxWindow");
  const messagesContainer = shadow.getElementById("pfxMessages");
  const typingIndicator = shadow.getElementById("pfxTyping");
  const input = shadow.getElementById("pfxInput");
  const sendBtn = shadow.getElementById("pfxSend");
  const newChatBtn = shadow.getElementById("pfxNewChat");

  // ─── Helpers ─────────────────────────────────────────────────────
  function scrollToBottom() {
    requestAnimationFrame(() => {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    });
  }

  function appendMessage(role, text, imageUrl) {
    const msg = document.createElement("div");
    msg.className = `pfx-msg ${role}`;
    msg.textContent = text;

    if (imageUrl) {
      const img = document.createElement("img");
      img.src = imageUrl;
      img.alt = "صورة المنتج";
      img.loading = "lazy";
      msg.appendChild(img);
    }

    messagesContainer.appendChild(msg);
    scrollToBottom();
  }

  function showTyping() {
    typingIndicator.classList.add("show");
    messagesContainer.appendChild(typingIndicator);
    scrollToBottom();
  }

  function hideTyping() {
    typingIndicator.classList.remove("show");
  }

  function updateBadge(count) {
    unreadCount = count;
    badge.textContent = count;
    badge.classList.toggle("show", count > 0);
  }

  function showWelcomeMessage() {
    appendMessage("ai", WELCOME_MESSAGE);
  }

  // ─── Toggle Chat ─────────────────────────────────────────────────
  let hasOpened = false;

  function toggleChat() {
    isOpen = !isOpen;
    bubble.classList.toggle("open", isOpen);
    chatWindow.classList.toggle("open", isOpen);

    if (isOpen) {
      updateBadge(0);
      // Show welcome message on first open
      if (!hasOpened) {
        hasOpened = true;
        showWelcomeMessage();
      }
      setTimeout(() => input.focus(), 400);
    }
  }

  bubble.addEventListener("click", toggleChat);

  // ─── Send Message ────────────────────────────────────────────────
  async function sendMessage() {
    const text = input.value.trim();
    if (!text) return;

    appendMessage("user", text);
    input.value = "";
    sendBtn.disabled = true;

    showTyping();

    try {
      const payload = { message: text };
      if (conversationId) payload.conversation_id = conversationId;

      const res = await fetch(`${API_BASE}/api/chat/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": API_KEY,
        },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      hideTyping();

      if (res.ok) {
        if (data.conversation_id) {
          conversationId = data.conversation_id;
          localStorage.setItem(STORAGE_KEY, conversationId);
        }

        if (data.needs_human) {
          appendMessage("ai", "⚠️ " + (data.info || "تم تحويل المحادثة لخدمة العملاء. سيتم الرد عليك في أقرب وقت."));
        } else {
          appendMessage("ai", data.reply, data.image_url);
        }

        // Badge for unread if window is closed
        if (!isOpen) {
          updateBadge(unreadCount + 1);
        }
      } else {
        appendMessage(
          "ai",
          "❌ " + (data.error || "حدث خطأ غير متوقع. حاول مرة أخرى.")
        );
      }
    } catch (err) {
      hideTyping();
      appendMessage("ai", "❌ خطأ في الاتصال. تأكد من اتصالك بالإنترنت وحاول مرة أخرى.");
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  }

  sendBtn.addEventListener("click", sendMessage);

  input.addEventListener("keypress", (e) => {
    if (e.key === "Enter") sendMessage();
  });

  // ─── New Chat ────────────────────────────────────────────────────
  newChatBtn.addEventListener("click", () => {
    conversationId = null;
    localStorage.removeItem(STORAGE_KEY);
    messagesContainer.innerHTML = "";
    showWelcomeMessage();
    input.focus();
  });

  // ─── Mobile Keyboard Handler ─────────────────────────────────────
  //
  // Coordinate systems:
  //
  //   Layout viewport (window.innerWidth / window.innerHeight):
  //     The full page canvas. position:fixed elements anchor here.
  //     On iOS, innerWidth NEVER changes when the keyboard opens.
  //     On Android, innerHeight shrinks when the keyboard opens.
  //
  //   Visual viewport (window.visualViewport):
  //     The slice of the layout viewport actually visible to the user.
  //     .height   → shrinks when keyboard is open (iOS + Android).
  //     .offsetTop → how far the visual viewport is pushed down from the
  //                  top of the layout viewport (usually 0; non-zero when
  //                  the page is scrolled and the keyboard is open on iOS).
  //     .offsetLeft → non-zero only if the page is zoomed horizontally.
  //                   With font-size:16px on inputs this never fires.
  //     .width    → we intentionally ignore this. It can differ from
  //                  innerWidth on some Android builds, and the keyboard
  //                  never changes horizontal space anyway.
  //
  // Keyboard-open strategy:
  //
  //   Horizontal: set left:0 AND right:0 (both as explicit inline styles).
  //     - "0" overrides any CSS value (e.g. right:16px from the media query).
  //     - Specifying BOTH without a width avoids over-constraint entirely:
  //       the element simply stretches from left:0 to right:0 = full viewport
  //       width, regardless of direction (LTR or RTL).
  //     - This is the key fix for the "right-side gap" bug: previously
  //       right was cleared to "" which re-enabled the CSS right:16px rule.
  //       With direction:rtl, CSS over-constraint resolution prioritises
  //       "right", so right:16px + left:0 + width:100% pushed the element
  //       16px short of the right edge.
  //
  //   Vertical: set top = visualViewport.offsetTop and height = visualViewport.height.
  //     - This positions the chat window to exactly fill the space above the keyboard.
  //
  //   position: stays as "fixed" — the viewport is always the containing block.
  //
  //   The host element is NEVER changed. It stays at its initial
  //   position:fixed; top:0; left:0; width:100%; height:100%.
  //
  if (window.visualViewport) {
    function handleViewportResize() {
      const vp       = window.visualViewport;
      const isMobile = window.innerWidth <= 480;
      if (!isMobile) return;

      // ── Diagnostic log ──────────────────────────────────────────────────
      // Uncomment on a real device to inspect values:
      // console.log("[pfx viewport]", {
      //   innerWidth:   window.innerWidth,
      //   innerHeight:  window.innerHeight,
      //   clientWidth:  document.documentElement.clientWidth,
      //   clientHeight: document.documentElement.clientHeight,
      //   scrollWidth:  document.documentElement.scrollWidth,
      //   vv: { width: vp.width, height: vp.height,
      //         offsetLeft: vp.offsetLeft, offsetTop: vp.offsetTop,
      //         scale: vp.scale }
      // });

      const vvHeight = vp.height;
      const vvTop    = vp.offsetTop || 0;

      // Keyboard is open when the visual viewport is significantly shorter
      // than the full window height. Works on both iOS and Android.
      const isKeyboardOpen = window.innerHeight - vvHeight > 100;

      if (isKeyboardOpen) {
        chatWindow.classList.add("keyboard-open");
        bubble.classList.add("pfx-bubble-hidden");

        // ── Vertical: pin the window above the keyboard ────────────────
        chatWindow.style.top    = vvTop + "px";
        chatWindow.style.height = vvHeight + "px";

        // ── Horizontal: stretch to FULL viewport width ─────────────────
        //
        // IMPORTANT: set right to "0", not "".
        //   Setting right="" removes the inline style and re-exposes the
        //   CSS rule  right: max(16px, env(safe-area-inset-right)).
        //   With direction:rtl, that 16px right offset causes a visible
        //   gap on the right side of the chat window.
        //   Setting right:"0" keeps the inline override active, forcing
        //   the element's right edge flush to the viewport right edge.
        //
        //   We do NOT set width — with both left:0 and right:0 explicitly
        //   set and no width, there is no CSS over-constraint, and the
        //   element spans the full viewport width in any direction.
        //
        chatWindow.style.left     = "0";
        chatWindow.style.right    = "0";
        chatWindow.style.width    = "";       // let left+right determine width
        chatWindow.style.maxWidth = "none";

        // ── Clear conflicting CSS properties ──────────────────────────
        chatWindow.style.bottom    = "";
        chatWindow.style.maxHeight = "none";

        // ── Keep position:fixed (already the CSS default) ─────────────
        chatWindow.style.position = "fixed";

        requestAnimationFrame(() => {
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        });

      } else {
        // ── Keyboard closed: restore all inline styles ─────────────────
        //
        // Clear EVERY property that was set in the keyboard-open branch
        // so that the CSS media-query rules take full control.
        // left/right are restored to the JS-applied 16px fallback values
        // (matching the CSS rule) for iOS where env() can be unreliable
        // inside Shadow DOM.
        //
        chatWindow.classList.remove("keyboard-open");
        chatWindow.style.position  = "";
        chatWindow.style.top       = "";
        chatWindow.style.bottom    = "";
        chatWindow.style.left      = "16px";
        chatWindow.style.right     = "16px";
        chatWindow.style.width     = "auto";
        chatWindow.style.maxWidth  = "";
        chatWindow.style.height    = "";
        chatWindow.style.maxHeight = "";

        bubble.classList.remove("pfx-bubble-hidden");
      }
    }

    window.visualViewport.addEventListener("resize", handleViewportResize);
    window.visualViewport.addEventListener("scroll", handleViewportResize);
  }

  // ─── Mobile position init ─────────────────────────────────────────
  // Apply JS-side left/right once on load as a robust fallback for iOS,
  // where CSS env()/max() values inside Shadow DOM can be unreliable.
  // The keyboard handler overwrites these when the keyboard is open and
  // restores them when it closes.
  if (window.innerWidth <= 480) {
    chatWindow.style.left  = "16px";
    chatWindow.style.right = "16px";
    chatWindow.style.width = "auto";
  }

  // ─── Prevent scroll bleed from chat messages to page ────────────
  // iOS Safari ignores overscroll-behavior on older versions, so we
  // use touch event boundary detection to block page scroll manually.
  let pfxTouchStartY = 0;

  messagesContainer.addEventListener("touchstart", function (e) {
    pfxTouchStartY = e.touches[0].clientY;
  }, { passive: true });

  messagesContainer.addEventListener("touchmove", function (e) {
    const el = messagesContainer;
    const touchY = e.touches[0].clientY;
    const delta = pfxTouchStartY - touchY; // positive = swiping up (scrolling down toward bottom)
    const isAtTop    = el.scrollTop <= 0;
    const isAtBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;

    // At boundaries: block the OS from scrolling the page behind the widget
    if ((isAtTop && delta < 0) || (isAtBottom && delta > 0)) {
      e.preventDefault();
    }
    // Always stop the event from reaching the page
    e.stopPropagation();
  }, { passive: false }); // passive:false required to allow preventDefault()
})();
