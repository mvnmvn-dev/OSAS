// CSRF token injection for POST forms
(function injectCsrfTokens() {
    const token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (!token) return;
    document.querySelectorAll('form[method="POST"], form[method="post"]').forEach((form) => {
        if (!form.querySelector('input[name="csrf_token"]')) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'csrf_token';
            input.value = token;
            form.prepend(input);
        }
    });
})();

// Sidebar toggle
const menuToggle = document.getElementById('menu-toggle');
const sidebar = document.getElementById('sidebar');
const overlay = document.getElementById('sidebar-overlay');

function isDesktop() {
    return window.innerWidth >= 1024;
}

function openSidebar() {
    if (isDesktop()) {
        document.body.classList.remove('sidebar-collapsed');
        localStorage.setItem('sidebar-collapsed', 'false');
    } else {
        sidebar.classList.add('open');
        overlay.classList.add('visible');
    }
}

function closeSidebar() {
    if (isDesktop()) {
        document.body.classList.add('sidebar-collapsed');
        localStorage.setItem('sidebar-collapsed', 'true');
    } else {
        sidebar.classList.remove('open');
        overlay.classList.remove('visible');
    }
}

function toggleSidebar() {
    if (isDesktop()) {
        const collapsed = document.body.classList.toggle('sidebar-collapsed');
        localStorage.setItem('sidebar-collapsed', collapsed ? 'true' : 'false');
    } else {
        if (sidebar.classList.contains('open')) {
            closeSidebar();
        } else {
            openSidebar();
        }
    }
}

if (menuToggle) {
    menuToggle.addEventListener('click', toggleSidebar);
}

if (overlay) {
    overlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        overlay.classList.remove('visible');
    });
}

if (localStorage.getItem('sidebar-collapsed') === 'true' && isDesktop()) {
    document.body.classList.add('sidebar-collapsed');
}

window.addEventListener('resize', () => {
    if (isDesktop()) {
        sidebar.classList.remove('open');
        overlay.classList.remove('visible');
    } else {
        document.body.classList.remove('sidebar-collapsed');
    }
});

// Clock
function updateClock() {
    const el = document.getElementById('clock');
    if (el) {
        const now = new Date();
        el.textContent = now.toLocaleTimeString('en-US', {
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true
        }).toUpperCase();
    }
}
setInterval(updateClock, 1000);
updateClock();

// Fullscreen
function toggleFullscreen() {
    const icon = document.getElementById('fullscreen-icon');
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
        if (icon) icon.classList.replace('fa-expand', 'fa-compress');
    } else {
        document.exitFullscreen();
        if (icon) icon.classList.replace('fa-compress', 'fa-expand');
    }
}

document.addEventListener('fullscreenchange', () => {
    const icon = document.getElementById('fullscreen-icon');
    if (!icon) return;
    if (document.fullscreenElement) {
        icon.classList.replace('fa-expand', 'fa-compress');
    } else {
        icon.classList.replace('fa-compress', 'fa-expand');
    }
});

// Quick search overlay
const searchToggle = document.getElementById('search-toggle');
const searchOverlay = document.getElementById('search-overlay');
const quickSearchInput = document.getElementById('quick-search-input');

if (searchToggle && searchOverlay) {
    searchToggle.addEventListener('click', () => {
        searchOverlay.classList.add('open');
        setTimeout(() => quickSearchInput?.focus(), 100);
    });

    searchOverlay.addEventListener('click', (e) => {
        if (e.target === searchOverlay) {
            searchOverlay.classList.remove('open');
        }
    });
}

async function apiCall(endpoint, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(`/api/v1${endpoint}`, opts);
    return res.json();
}

// Modal system
function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.add('open');
        document.body.style.overflow = 'hidden';
        const firstInput = modal.querySelector('input, select, textarea');
        if (firstInput) setTimeout(() => firstInput.focus(), 100);
    }
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.remove('open');
        document.body.style.overflow = '';
    }
}

function closeAllModals() {
    document.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'));
    document.body.style.overflow = '';
}

document.querySelectorAll('[data-open-modal]').forEach(btn => {
    btn.addEventListener('click', () => openModal(btn.dataset.openModal));
});

document.querySelectorAll('[data-close-modal]').forEach(el => {
    el.addEventListener('click', () => {
        const modal = el.closest('.modal');
        if (modal) closeModal(modal.id);
        else closeAllModals();
    });
});

document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
    backdrop.addEventListener('click', () => {
        const modal = backdrop.closest('.modal');
        if (modal) closeModal(modal.id);
    });
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        if (searchOverlay?.classList.contains('open')) {
            searchOverlay.classList.remove('open');
        } else {
            closeAllModals();
        }
    }
});

// Confirm delete via modal
document.querySelectorAll('[data-confirm-modal]').forEach(btn => {
    btn.addEventListener('click', () => {
        const modalId = btn.dataset.confirmModal;
        const formId = btn.dataset.confirmForm;
        openModal(modalId);
        const confirmBtn = document.querySelector(`#${modalId} [data-submit-form]`);
        if (confirmBtn && formId) {
            confirmBtn.onclick = () => document.getElementById(formId)?.submit();
        }
    });
});

// Book detail modal
const bookDetailModal = document.getElementById('book-detail-modal');
const bookDetailBody = document.getElementById('book-detail-body');
const bookDetailFooter = document.getElementById('book-detail-footer');
const bookDetailTitle = document.getElementById('book-detail-title');

function bookDetailLoading() {
    if (!bookDetailBody) return;
    bookDetailBody.innerHTML = `
        <div class="book-detail-loading">
            <div class="spinner"></div>
            <p>Loading book details…</p>
        </div>`;
    if (bookDetailFooter) bookDetailFooter.style.display = 'none';
}

function renderBookDetail(book) {
    if (!bookDetailBody || !book) return;

    const avail = book.availability || {};
    const inStock = avail.in_stock;
    const ebooks = book.ebooks || [];
    const ebookFormats = book.ebook_formats || ebooks.map(e => e.format || e);
    const hasEbook = ebookFormats.length > 0;
    const ebooksUrl = window.LMS_URLS?.ebooks || '/ebooks/';

    const meta = [book.authors, book.publisher, book.genre].filter(Boolean).join(' · ');

        bookDetailBody.innerHTML = `
        <div class="book-detail-content">
            <div class="book-detail-hero">
                <div class="book-detail-cover">
                    <i class="fas fa-book"></i>
                </div>
                <div class="book-detail-meta">
                    <h2 class="book-detail-name">${escapeHtml(book.title)}</h2>
                    ${meta ? `<p class="book-detail-subtitle">${escapeHtml(meta)}</p>` : ''}
                    ${book.description ? `<p class="book-detail-desc">${escapeHtml(book.description)}</p>` : ''}
                </div>
            </div>
            <div class="book-detail-grid">
                <div class="detail-stat-card">
                    <span class="detail-stat-label"><i class="fas fa-barcode"></i> ISBN</span>
                    <span class="detail-stat-value">${escapeHtml(book.isbn || 'N/A')}</span>
                </div>
                <div class="detail-stat-card ${inStock ? 'available' : 'unavailable'}">
                    <span class="detail-stat-label"><i class="fas fa-layer-group"></i> Availability</span>
                    <span class="detail-stat-value">
                        ${inStock
                            ? `<span class="status-badge available"><i class="fas fa-circle-check"></i> ${avail.available} of ${avail.total} available</span>`
                            : `<span class="status-badge unavailable"><i class="fas fa-circle-xmark"></i> Currently unavailable</span>`}
                    </span>
                </div>
                ${hasEbook ? `
                <div class="detail-stat-card ebook">
                    <span class="detail-stat-label"><i class="fas fa-tablet-screen-button"></i> E-Book</span>
                    <span class="detail-stat-value">${escapeHtml(ebookFormats.join(', '))}</span>
                </div>` : ''}
            </div>
            ${book.similar && book.similar.length ? `
            <div class="similar-books-section">
                <h4 class="similar-books-title"><i class="fas fa-layer-group"></i> Similar Books</h4>
                <div class="similar-books-grid">
                    ${book.similar.map(s => `
                        <button type="button" class="similar-book-card" data-book-detail="${s.id}">
                            <strong>${escapeHtml(s.title)}</strong>
                            <span>${escapeHtml(s.authors || s.genre || '')}</span>
                        </button>
                    `).join('')}
                </div>
            </div>` : ''}
        </div>`;

    if (bookDetailTitle) bookDetailTitle.textContent = book.title;

    if (bookDetailFooter) {
        let footerHtml = '';
        if (inStock) {
            footerHtml += `<span class="status-badge available detail-desk-badge"><i class="fas fa-building"></i> Available at library desk</span>`;
        } else {
            const reserveBase = window.LMS_URLS?.reserve || '/search';
            footerHtml += `
                <form method="POST" action="${reserveBase}/${book.id}/reserve" class="inline-form">
                    <button type="submit" class="btn-primary-square"><i class="fas fa-bookmark"></i> Place Reservation</button>
                </form>`;
        }
        if (hasEbook) {
            footerHtml += `<a href="${ebooksUrl}" class="btn-secondary"><i class="fas fa-tablet-screen-button"></i> View E-Books</a>`;
        }
        footerHtml += `<button type="button" class="btn-secondary" data-close-modal>Close</button>`;
        bookDetailFooter.innerHTML = footerHtml;
        bookDetailFooter.style.display = 'flex';
        bookDetailFooter.querySelectorAll('[data-close-modal]').forEach(el => {
            el.addEventListener('click', () => closeModal('book-detail-modal'));
        });
        bookDetailBody.querySelectorAll('[data-book-detail]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                openBookDetail(btn.dataset.bookDetail);
            });
        });
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

async function openBookDetail(bookId) {
    if (!bookDetailModal) return;
    bookDetailLoading();
    openModal('book-detail-modal');

    try {
        const res = await apiCall(`/books/${bookId}`);
        if (res.success && res.data) {
            renderBookDetail(res.data);
        } else {
            bookDetailBody.innerHTML = `
                <div class="book-detail-error">
                    <i class="fas fa-circle-exclamation"></i>
                    <p>${escapeHtml(res.message || 'Could not load book details.')}</p>
                </div>`;
        }
    } catch {
        bookDetailBody.innerHTML = `
            <div class="book-detail-error">
                <i class="fas fa-circle-exclamation"></i>
                <p>Something went wrong. Please try again.</p>
            </div>`;
    }
}

document.querySelectorAll('[data-book-detail]').forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        openBookDetail(btn.dataset.bookDetail);
    });
});

window.openBookDetail = openBookDetail;
window.openModal = openModal;
window.closeModal = closeModal;

window.bindSearchInteractions = function bindSearchInteractions(root = document) {
    root.querySelectorAll('[data-open-modal]').forEach((btn) => {
        if (btn.dataset.searchBound) return;
        btn.dataset.searchBound = '1';
        btn.addEventListener('click', () => openModal(btn.dataset.openModal));
    });

    root.querySelectorAll('[data-close-modal]').forEach((el) => {
        if (el.dataset.searchBound) return;
        el.dataset.searchBound = '1';
        el.addEventListener('click', () => {
            const modal = el.closest('.modal');
            if (modal) closeModal(modal.id);
            else closeAllModals();
        });
    });

    root.querySelectorAll('.modal-backdrop').forEach((backdrop) => {
        if (backdrop.dataset.searchBound) return;
        backdrop.dataset.searchBound = '1';
        backdrop.addEventListener('click', () => {
            const modal = backdrop.closest('.modal');
            if (modal) closeModal(modal.id);
        });
    });

    root.querySelectorAll('[data-book-detail]').forEach((btn) => {
        if (btn.dataset.searchBound) return;
        btn.dataset.searchBound = '1';
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            openBookDetail(btn.dataset.bookDetail);
        });
    });

    root.querySelectorAll('.table-row-clickable').forEach((row) => {
        if (row.dataset.searchBound) return;
        row.dataset.searchBound = '1';
        row.addEventListener('click', (e) => {
            if (e.target.closest('button, a, form')) return;
            const id = row.dataset.bookId;
            if (id) openBookDetail(id);
        });
    });
};

document.querySelectorAll('.table-row-clickable').forEach(row => {
    row.addEventListener('click', (e) => {
        if (e.target.closest('button, a, form')) return;
        const id = row.dataset.bookId;
        if (id) openBookDetail(id);
    });
});

if (window.LMS_AUTO_OPEN_BOOK) {
    openBookDetail(window.LMS_AUTO_OPEN_BOOK);
}

// Notifications dropdown
const notifToggle = document.getElementById('notif-toggle');
const notifDropdown = document.getElementById('notif-dropdown');
const notifReadAll = document.getElementById('notif-read-all');
const notifBadge = document.getElementById('notif-badge');

function updateNotifBadge(count) {
    if (!notifBadge) return;
    if (count > 0) {
        notifBadge.textContent = count;
        notifBadge.style.display = '';
    } else {
        notifBadge.style.display = 'none';
    }
}

if (notifToggle && notifDropdown) {
    notifToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        notifDropdown.classList.toggle('open');
    });

    document.addEventListener('click', (e) => {
        if (!notifDropdown.contains(e.target) && !notifToggle.contains(e.target)) {
            notifDropdown.classList.remove('open');
        }
    });
}

document.querySelectorAll('.notif-item[data-id]').forEach(item => {
    item.addEventListener('click', async (e) => {
        const id = item.dataset.id;
        if (!id || !item.classList.contains('unread')) return;
        e.preventDefault();
        try {
            const res = await fetch(`/notifications/${id}/read`, { method: 'POST' });
            const data = await res.json();
            item.classList.remove('unread');
            if (data.data?.unread_count !== undefined) {
                updateNotifBadge(data.data.unread_count);
            }
        } catch { /* ignore */ }
        const href = item.getAttribute('href');
        if (href && href !== '#') window.location.href = href;
    });
});

if (notifReadAll) {
    notifReadAll.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        try {
            const res = await fetch('/notifications/read-all', { method: 'POST' });
            const data = await res.json();
            document.querySelectorAll('.notif-item.unread').forEach(el => el.classList.remove('unread'));
            updateNotifBadge(0);
            notifReadAll.style.display = 'none';
        } catch { /* ignore */ }
    });
}

// Profile menu dropdown (same pattern as notifications — no backdrop)
const profileToggle = document.getElementById('profile-toggle');
const profileMenu = document.getElementById('profile-menu');
const sidebarProfileTrigger = document.getElementById('sidebar-profile-trigger');

function setProfileMenuOpen(open) {
    if (!profileMenu) return;
    profileMenu.classList.toggle('open', open);
    profileToggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
    sidebarProfileTrigger?.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) notifDropdown?.classList.remove('open');
}

function closeProfileMenu() {
    setProfileMenuOpen(false);
}

function toggleProfileMenu() {
    setProfileMenuOpen(!profileMenu?.classList.contains('open'));
}

if (profileToggle && profileMenu) {
    profileToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleProfileMenu();
    });
}

if (sidebarProfileTrigger && profileMenu) {
    sidebarProfileTrigger.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleProfileMenu();
        if (window.innerWidth < 1024) closeSidebar();
    });
}

document.addEventListener('click', (e) => {
    if (!profileMenu?.classList.contains('open')) return;
    const inMenu = profileMenu.contains(e.target);
    const inToggle = profileToggle?.contains(e.target);
    const inSidebar = sidebarProfileTrigger?.contains(e.target);
    if (!inMenu && !inToggle && !inSidebar) {
        closeProfileMenu();
    }
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeProfileMenu();
});

// ─── Interactive UI enhancements ─────────────────────────────

function getTimeGreeting() {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
}

function initHeaderGreeting() {
    const el = document.getElementById('greeting-text');
    if (el) el.textContent = getTimeGreeting();
}

function getToastContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    return container;
}

function showToast({ message, type = 'info', title = null, icon = null, duration = 6000 }) {
    const icons = {
        success: 'fa-circle-check',
        error: 'fa-circle-xmark',
        info: 'fa-circle-info',
        tip: 'fa-lightbulb',
        trivia: 'fa-star',
    };

    const container = getToastContainer();
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const iconClass = icon || icons[type] || icons.info;
    const bodyContent = title
        ? `<strong class="toast-title">${title}</strong><p class="toast-message">${message}</p>`
        : `<div class="toast-body">${message}</div>`;

    toast.innerHTML = `
        <div class="toast-icon"><i class="fas ${iconClass}"></i></div>
        <div class="toast-content">${bodyContent}</div>
        <button type="button" class="toast-close" aria-label="Dismiss">&times;</button>`;

    const dismiss = () => {
        toast.classList.add('toast-out');
        setTimeout(() => toast.remove(), 300);
    };

    toast.querySelector('.toast-close').addEventListener('click', dismiss);
    container.appendChild(toast);

    if (duration > 0) {
        setTimeout(dismiss, duration);
    }

    return toast;
}

function initToasts() {
    const flashList = document.querySelector('.flash-list');
    if (!flashList) return;

    flashList.querySelectorAll('.flash-message').forEach((msg, i) => {
        const category = [...msg.classList].find(c => c.startsWith('flash-'))?.replace('flash-', '') || 'info';
        const toastType = category === 'error' ? 'error' : category === 'success' ? 'success' : 'info';
        setTimeout(() => {
            showToast({ message: msg.textContent.trim(), type: toastType, duration: 5000 });
        }, i * 120);
    });

    flashList.style.display = 'none';
}

function initRevealAnimations() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const targets = document.querySelectorAll(
        '.stat-card, .info-card, .chart-card, .page-card, .welcome-hero, .quick-action-tile, .reminder-box'
    );

    targets.forEach((el, i) => {
        el.classList.add('reveal-item');
        el.style.transitionDelay = `${(i % 6) * 0.07}s`;
    });

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.08, rootMargin: '0px 0px -20px 0px' });

    targets.forEach(el => observer.observe(el));
}

function initRippleEffect() {
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-primary-square, .btn-primary, .quick-action-tile');
        if (!btn) return;

        const rect = btn.getBoundingClientRect();
        const size = Math.max(rect.width, rect.height);
        const ripple = document.createElement('span');
        ripple.className = 'ripple';
        ripple.style.width = ripple.style.height = `${size}px`;
        ripple.style.left = `${e.clientX - rect.left - size / 2}px`;
        ripple.style.top = `${e.clientY - rect.top - size / 2}px`;
        btn.appendChild(ripple);
        setTimeout(() => ripple.remove(), 600);
    });
}

function initSearchShortcuts() {
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            if (searchOverlay) {
                searchOverlay.classList.add('open');
                setTimeout(() => quickSearchInput?.focus(), 100);
            }
        }
    });

    document.querySelectorAll('.search-hint-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            if (quickSearchInput) {
                quickSearchInput.value = chip.dataset.query || chip.textContent;
                quickSearchInput.focus();
            }
        });
    });
}

const LIBRARY_TIPS = [
    'You can press Ctrl+K anywhere to open quick book search.',
    'Return books on time to avoid overdue fines — check your dashboard for due dates.',
    'Can\'t find a book on the shelf? Place a reservation to join the hold queue.',
    'Browse our e-book collection for digital reading on any device.',
    'Your library card must be active to borrow — visit the desk if you need help.',
    'Renew eligible loans from the Borrowing Logs page before the due date.',
    'Use genre filters in search to discover books in your favorite categories.',
];

function initDashboardToasts() {
    if (!window.LMS_DASHBOARD_TOASTS) return;

    const tips = window.LMS_TIPS && window.LMS_TIPS.length ? window.LMS_TIPS : LIBRARY_TIPS;
    const triviaPool = window.LMS_TRIVIA && window.LMS_TRIVIA.length
        ? window.LMS_TRIVIA
        : ['Libraries are gateways to knowledge!'];

    const tip = tips[Math.floor(Math.random() * tips.length)];
    const trivia = triviaPool[Math.floor(Math.random() * triviaPool.length)];

    setTimeout(() => {
        showToast({
            title: 'Library Tip',
            message: tip,
            type: 'tip',
            duration: 8000,
        });
    }, 900);

    setTimeout(() => {
        showToast({
            title: 'Did You Know?',
            message: trivia,
            type: 'trivia',
            duration: 8000,
        });
    }, 2200);
}

function initRelativeTimestamps() {
    document.querySelectorAll('[data-timestamp]').forEach(el => {
        const ts = el.dataset.timestamp;
        if (!ts) return;
        const diff = Date.now() - new Date(ts).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) el.textContent = 'Just now';
        else if (mins < 60) el.textContent = `${mins}m ago`;
        else if (mins < 1440) el.textContent = `${Math.floor(mins / 60)}h ago`;
        else el.textContent = `${Math.floor(mins / 1440)}d ago`;
    });
}

function initDarkMode() {
    const toggle = document.getElementById('theme-toggle');
    const icon = document.getElementById('theme-icon');
    const saved = localStorage.getItem('lms-theme');
    if (saved === 'dark') document.body.classList.add('dark-mode');
    if (icon) icon.className = document.body.classList.contains('dark-mode') ? 'fas fa-sun' : 'fas fa-moon';

    toggle?.addEventListener('click', () => {
        document.body.classList.toggle('dark-mode');
        const dark = document.body.classList.contains('dark-mode');
        localStorage.setItem('lms-theme', dark ? 'dark' : 'light');
        if (icon) icon.className = dark ? 'fas fa-sun' : 'fas fa-moon';
    });
}

const COMMAND_MODULES = [
    { label: 'Dashboard', url: '/dashboard/', icon: 'fa-house', keywords: 'home' },
    { label: 'Search Books', url: '/search/', icon: 'fa-search', keywords: 'find catalog' },
    { label: 'Borrowing Logs', url: '/borrowing/', icon: 'fa-right-left', keywords: 'loans renew' },
    { label: 'Fines', url: '/fines/', icon: 'fa-clock', keywords: 'pay overdue' },
    { label: 'Reservations', url: '/reservations/', icon: 'fa-bookmark', keywords: 'hold queue' },
    { label: 'E-Books', url: '/ebooks/', icon: 'fa-tablet-screen-button', keywords: 'digital read' },
    { label: 'Inventory', url: '/inventory/', icon: 'fa-boxes-stacked', keywords: 'books manage' },
    { label: 'Clearance', url: '/clearance/', icon: 'fa-clipboard-check', keywords: 'graduate exit' },
    { label: 'Reports', url: '/reports/', icon: 'fa-chart-line', keywords: 'analytics' },
];

function initCommandPalette() {
    const palette = document.getElementById('command-palette');
    const input = document.getElementById('command-palette-input');
    const results = document.getElementById('command-palette-results');
    if (!palette || !input) return;

    const backdrop = palette.querySelector('.command-palette-backdrop');

    function openPalette() {
        palette.classList.add('open');
        palette.removeAttribute('hidden');
        input.value = '';
        renderResults('');
        setTimeout(() => input.focus(), 50);
    }

    function closePalette() {
        palette.classList.remove('open');
        palette.setAttribute('hidden', '');
    }

    function renderResults(q) {
        const term = q.toLowerCase();
        const items = COMMAND_MODULES.filter(m =>
            m.label.toLowerCase().includes(term) || m.keywords.includes(term)
        );
        results.innerHTML = items.map(m => `
            <a href="${m.url}" class="command-result-item">
                <i class="fas ${m.icon}"></i>
                <span>${m.label}</span>
            </a>`).join('') || '<p class="command-empty">No matches</p>';
    }

    input.addEventListener('input', () => renderResults(input.value));

    backdrop?.addEventListener('click', closePalette);

    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'K') {
            e.preventDefault();
            palette.classList.contains('open') ? closePalette() : openPalette();
        }
        if (e.key === 'Escape' && palette.classList.contains('open')) closePalette();
    });
}

let suggestTimer;
function initSearchAutocomplete() {
    const input = quickSearchInput;
    const list = document.getElementById('search-suggest-list');
    if (!input || !list) return;

    input.addEventListener('input', () => {
        clearTimeout(suggestTimer);
        const q = input.value.trim();
        if (q.length < 2) { list.innerHTML = ''; list.classList.remove('open'); return; }
        suggestTimer = setTimeout(async () => {
            try {
                const res = await apiCall(`/search/suggest?q=${encodeURIComponent(q)}`);
                const items = res.data || [];
                if (!items.length) { list.innerHTML = ''; list.classList.remove('open'); return; }
                list.innerHTML = items.map(b => `
                    <button type="button" class="suggest-item" data-book-id="${b.id}">
                        <strong>${escapeHtml(b.title)}</strong>
                        <span>${escapeHtml(b.authors || b.genre || '')}</span>
                    </button>`).join('');
                list.classList.add('open');
                list.querySelectorAll('.suggest-item').forEach(btn => {
                    btn.addEventListener('click', () => {
                        searchOverlay?.classList.remove('open');
                        openBookDetail(btn.dataset.bookId);
                    });
                });
            } catch { list.innerHTML = ''; }
        }, 250);
    });
}

function initOnboardingTour() {
    if (localStorage.getItem('lms-tour-done')) return;
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    const overlay = document.createElement('div');
    overlay.className = 'tour-overlay';
    overlay.innerHTML = `
        <div class="tour-card">
            <h3>Welcome to the Library System!</h3>
            <p>Use the sidebar to navigate modules. Press <kbd>Ctrl+K</kbd> to search books or <kbd>Ctrl+Shift+K</kbd> for the command palette.</p>
            <button type="button" class="btn-primary-square" id="tour-dismiss">Got it!</button>
        </div>`;
    document.body.appendChild(overlay);
    document.getElementById('tour-dismiss')?.addEventListener('click', () => {
        overlay.remove();
        localStorage.setItem('lms-tour-done', '1');
    });
}

function initLoginEffects() {
    const tagline = document.getElementById('login-tagline');
    if (!tagline) return;

    const phrases = [
        'Your gateway to knowledge',
        'Discover. Borrow. Learn.',
        'Bestlink College Library',
    ];
    let phraseIdx = 0;
    let charIdx = 0;
    let deleting = false;

    function typeLoop() {
        const current = phrases[phraseIdx];
        if (!deleting) {
            tagline.innerHTML = current.slice(0, charIdx + 1) + '<span class="cursor-blink"></span>';
            charIdx++;
            if (charIdx === current.length) {
                setTimeout(() => { deleting = true; typeLoop(); }, 2000);
                return;
            }
        } else {
            tagline.innerHTML = current.slice(0, charIdx - 1) + '<span class="cursor-blink"></span>';
            charIdx--;
            if (charIdx === 0) {
                deleting = false;
                phraseIdx = (phraseIdx + 1) % phrases.length;
            }
        }
        setTimeout(typeLoop, deleting ? 40 : 80);
    }

    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        typeLoop();
    } else {
        tagline.textContent = phrases[0];
    }

    const card = document.querySelector('.login-card');
    if (card && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        document.addEventListener('mousemove', (e) => {
            const x = (e.clientX / window.innerWidth - 0.5) * 8;
            const y = (e.clientY / window.innerHeight - 0.5) * 8;
            card.style.transform = `perspective(1000px) rotateY(${x * 0.3}deg) rotateX(${-y * 0.3}deg)`;
        });
        document.addEventListener('mouseleave', () => {
            card.style.transform = '';
        });
    }
}

document.addEventListener('DOMContentLoaded', () => {
    initHeaderGreeting();
    initToasts();
    initRevealAnimations();
    initRippleEffect();
    initSearchShortcuts();
    initSearchAutocomplete();
    initDashboardToasts();
    initLoginEffects();
    initRelativeTimestamps();
    initDarkMode();
    initCommandPalette();
    initOnboardingTour();
});
