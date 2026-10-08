/* Presentation and accessibility only. API/authentication logic stays in each page. */
(() => {
    'use strict';
    const body = document.body;
    const page = body.dataset.page;
    const mobile = matchMedia('(max-width: 1023px)');
    const focusable = 'a[href],button:not(:disabled),input:not(:disabled):not([type="hidden"]),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]';
    const visible = el => el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
    const focusFirst = root => [...root.querySelectorAll(focusable)].find(visible)?.focus();
    let drawer = null;
    let openDialog = null;
    let dialogTrigger = null;

    function setDrawer(open, restore = true) {
        if (!drawer) return;
        const { sidebar, overlay, toggle, main } = drawer;
        open = open && mobile.matches;
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('open', open);
        body.classList.toggle('drawer-open', open);
        sidebar.inert = mobile.matches && !open;
        sidebar.setAttribute('aria-hidden', String(mobile.matches && !open));
        main.inert = open;
        toggle.setAttribute('aria-expanded', String(open));
        toggle.setAttribute('aria-label', open ? 'إغلاق قائمة التنقل' : 'فتح قائمة التنقل');
        if (open) focusFirst(sidebar);
        else if (restore && mobile.matches) toggle.focus();
    }

    window.WebVitasUI = {
        overviewState(state) {
            const summary = document.getElementById('overview-summary');
            const message = document.getElementById('overview-message');
            if (!summary || !message) return;
            summary.setAttribute('aria-busy', String(state === 'loading'));
            message.hidden = state === 'ready';
            message.classList.toggle('is-error', state === 'error');
            message.textContent = state === 'error' ? 'تعذّر تحميل ملخص المتجر. حاول مرة أخرى.' : 'جاري تحميل ملخص المتجر…';
            if (state === 'error') {
                summary.querySelectorAll('.stat-value, #stat-revenue').forEach(value => value.textContent = '—');
                document.getElementById('platform-breakdown').textContent = 'تظهر بيانات القنوات بعد تحميل الملخص.';
                const retry = document.createElement('button');
                retry.type = 'button'; retry.className = 'btn btn-secondary btn-sm';
                retry.textContent = 'إعادة المحاولة';
                retry.addEventListener('click', () => window.loadOverview());
                message.append(' ', retry);
            }
        },
        renderOverview(overview, profile) {
            const storeName = document.getElementById('workspace-store-name');
            if (storeName) storeName.textContent = profile.store.name;
            const container = document.getElementById('platform-breakdown');
            if (!container) return;
            const platforms = {
                whatsapp: ['واتساب', 'fab fa-whatsapp'], messenger: ['ماسنجر', 'fab fa-facebook-messenger'],
                instagram: ['انستجرام', 'fab fa-instagram'], web: ['ويب', 'fas fa-globe']
            };
            const entries = Object.entries(overview.platform_breakdown || {})
                .map(([name, count]) => [name, Number.isFinite(Number(count)) ? Math.max(0, Number(count)) : 0])
                .sort((a, b) => b[1] - a[1]);
            const total = entries.reduce((sum, [, count]) => sum + count, 0);
            container.replaceChildren();
            if (!total) {
                const empty = document.createElement('p'); empty.className = 'empty-state';
                empty.textContent = 'لا توجد محادثات بعد. ستظهر قنوات عملائك هنا بمجرد بدء المحادثات.';
                container.append(empty);
            } else {
                for (const [name, count] of entries) {
                    const [label, iconClass] = platforms[name] || [name, 'fas fa-globe'];
                    const row = document.createElement('div'); row.className = 'platform-row';
                    const iconBox = document.createElement('span'); iconBox.className = 'platform-icon';
                    const icon = document.createElement('i'); icon.className = iconClass; icon.setAttribute('aria-hidden', 'true'); iconBox.append(icon);
                    const title = document.createElement('span'); title.className = 'platform-name'; title.textContent = label;
                    const value = document.createElement('span'); value.className = 'platform-count'; value.textContent = count.toLocaleString();
                    const track = document.createElement('span'); track.className = 'platform-track'; track.setAttribute('aria-hidden', 'true');
                    const fill = document.createElement('span'); fill.style.setProperty('--share', (count / total * 100) + '%'); track.append(fill);
                    const share = document.createElement('span'); share.className = 'platform-share'; share.textContent = (count / total * 100).toFixed(1).replace(/\.0$/, '') + '%';
                    row.append(iconBox, title, value, track, share); container.append(row);
                }
            }
            window.WebVitasUI.overviewState('ready');
        },
        showError(message = 'تعذّر إتمام الطلب. تحقق من الاتصال وحاول مرة أخرى.') {
            let alert = document.getElementById('wv-request-error');
            if (!alert) {
                alert = document.createElement('div'); alert.id = 'wv-request-error';
                alert.className = 'wv-request-error'; alert.setAttribute('role', 'alert');
                const text = document.createElement('span');
                const close = document.createElement('button'); close.type = 'button';
                close.textContent = '×'; close.setAttribute('aria-label', 'إغلاق التنبيه');
                close.addEventListener('click', () => alert.remove());
                alert.append(text, close); body.append(alert);
            }
            alert.querySelector('span').textContent = message;
        },
        toggleSidebar() { if (drawer) setDrawer(!drawer.sidebar.classList.contains('open')); },
        sectionChanged(name) {
            document.querySelectorAll('.nav-item').forEach(item => {
                if (item.id === 'nav-' + name) item.setAttribute('aria-current', 'page');
                else item.removeAttribute('aria-current');
            });
            const title = document.getElementById('workspace-section-title');
            const activeItem = document.getElementById('nav-' + name)?.cloneNode(true);
            activeItem?.querySelectorAll('.nav-badge').forEach(badge => badge.remove());
            if (title && activeItem) title.textContent = activeItem.textContent.trim();
            setDrawer(false, false);
            const section = document.getElementById('section-' + name);
            const target = section?.querySelector('h1') || section;
            if (target) { target.tabIndex = -1; target.focus({ preventScroll: true }); }
            window.scrollTo({ top: 0 });
        },
        openConversation() {
            body.classList.add('conversation-open');
            const heading = document.querySelector('.chat-header h3');
            if (heading) {
                heading.tabIndex = -1;
                requestAnimationFrame(() => heading.focus({ preventScroll: true }));
            }
        }
    };

    if (window.self !== window.top) body.classList.add('wv-embedded');

    if (page === 'store_dashboard') {
        drawer = {
            sidebar: document.getElementById('workspace-sidebar'),
            overlay: document.querySelector('.sidebar-overlay'),
            toggle: document.querySelector('.mobile-toggle'),
            main: document.querySelector('.main-content')
        };
        const close = document.createElement('button');
        close.type = 'button'; close.className = 'drawer-close';
        close.textContent = 'إغلاق القائمة ×';
        close.addEventListener('click', () => setDrawer(false));
        drawer.sidebar.prepend(close);
        document.getElementById('nav-overview').setAttribute('aria-current', 'page');
        const resize = () => {
            const needsFocus = drawer.sidebar.contains(document.activeElement) && mobile.matches;
            setDrawer(false, needsFocus);
        };
        mobile.addEventListener('change', resize);
        resize();
    }

    document.querySelector('.conversation-back')?.addEventListener('click', () => {
        body.classList.remove('conversation-open');
        document.querySelector('.conv-item.active')?.focus();
    });

    const dialogClosers = {
        'faq-modal': 'closeFaqModal', 'post-rule-modal': 'closePostRuleModal',
        'edit-product-modal': 'closeEditModal', 'order-modal': 'closeModal'
    };
    const dialogs = Object.keys(dialogClosers).map(id => document.getElementById(id)).filter(Boolean);
    dialogs.forEach(dialog => {
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        const title = dialog.querySelector('h2');
        if (title) {
            title.id ||= dialog.id + '-heading';
            dialog.setAttribute('aria-labelledby', title.id);
        }
    });
    function syncDialogs() {
        const current = dialogs.find(visible) || null;
        if (current === openDialog) return;
        if (current) {
            dialogTrigger = document.activeElement;
            openDialog = current;
            body.classList.add('has-dialog');
            focusFirst(current);
        } else {
            openDialog = null;
            body.classList.remove('has-dialog');
            if (visible(dialogTrigger)) dialogTrigger.focus();
        }
    }
    dialogs.forEach(dialog => new MutationObserver(syncDialogs).observe(dialog, { attributes: true, attributeFilter: ['style', 'class'] }));

    function trapFocus(event, root) {
        const nodes = [...root.querySelectorAll(focusable)].filter(visible);
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (!first) { event.preventDefault(); return; }
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            if (openDialog) { window[dialogClosers[openDialog.id]]?.(); event.preventDefault(); }
            else if (drawer?.sidebar.classList.contains('open')) { setDrawer(false); event.preventDefault(); }
        }
        if (event.key === 'Tab') {
            if (openDialog) trapFocus(event, openDialog);
            else if (drawer?.sidebar.classList.contains('open')) trapFocus(event, drawer.sidebar);
        }
        if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.conv-item, tr[tabindex="0"], #uploadZone')) {
            event.preventDefault(); event.target.click();
        }
    });

    let controlId = 0;
    function enhance() {
        document.querySelectorAll('label:not([for])').forEach(label => {
            const control = label.parentElement.querySelector('input,textarea,select');
            if (!control) return;
            control.id ||= 'wv-control-' + (++controlId);
            label.htmlFor = control.id;
        });
        document.querySelectorAll('input,textarea,select').forEach(control => {
            if (control.type === 'hidden' || control.getAttribute('aria-label') || control.labels?.length) return;
            const label = control.classList.contains('var-price') ? 'سعر الحجم' : control.placeholder || control.title;
            control.setAttribute('aria-label', label || (control.disabled ? 'الحجم' : 'القيمة'));
        });
        document.querySelectorAll('.btn-send,#send-btn').forEach(button => button.setAttribute('aria-label', 'إرسال الرسالة'));
        document.querySelectorAll('.modal-close').forEach(button => button.setAttribute('aria-label', 'إغلاق تفاصيل الطلب'));
        document.querySelectorAll('button').forEach(button => {
            if (button.textContent.trim() || button.hasAttribute('aria-label')) return;
            const icon = button.querySelector('i');
            const label = icon?.className.includes('trash') ? 'حذف' : icon?.className.includes('edit') ? 'تعديل' : 'تنفيذ الإجراء';
            button.setAttribute('aria-label', label);
        });
        document.querySelectorAll('i[class*="fa-"]').forEach(icon => icon.setAttribute('aria-hidden', 'true'));
        document.querySelectorAll('img:not([alt])').forEach(img => img.alt = 'مرفق المحادثة');
        document.querySelectorAll('.chart-card canvas').forEach(canvas => {
            canvas.setAttribute('role', 'img');
            canvas.setAttribute('aria-label', canvas.closest('.chart-card').querySelector('h3')?.textContent.trim() || 'رسم بياني');
        });
        document.querySelectorAll('.error-msg,.login-error').forEach(el => el.setAttribute('role', 'alert'));
        document.querySelectorAll('.success-msg').forEach(el => el.setAttribute('role', 'status'));
    }
    const upload = document.getElementById('uploadZone');
    if (upload) { upload.tabIndex = 0; upload.setAttribute('role', 'button'); upload.setAttribute('aria-label', 'اختيار ملف Excel لرفع المنتجات'); }
    let scheduled = false;
    new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => { scheduled = false; enhance(); });
    }).observe(body, { childList: true, subtree: true });
    enhance();
})();
