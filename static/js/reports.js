const REPORTS_REFRESH_MS = 30000;

function formatReportDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
    });
}

function formatEventLabel(value) {
    if (!value) return '';
    return String(value).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function updateReportStats(dashboard) {
    const map = {
        'stat-total-books': dashboard.total_books ?? 0,
        'stat-active-loans': dashboard.active_loans ?? 0,
        'stat-overdue-loans': dashboard.overdue_loans ?? 0,
        'stat-unpaid-fines': `₱${Number(dashboard.unpaid_fines_total || 0).toFixed(2)}`,
    };
    Object.entries(map).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    });
}

function updateInsights(analysis) {
    const grid = document.getElementById('insights-grid');
    if (!grid) return;
    const items = analysis || [];
    if (!items.length) {
        grid.innerHTML = '<p class="notif-empty">No insights available yet.</p>';
        return;
    }
    grid.innerHTML = items.map(insight => `
        <div class="insight-card">
            <span class="insight-module-tag">${escapeHtml(insight.module)}</span>
            <h3 class="insight-title">${escapeHtml(insight.title)}</h3>
            <p class="insight-text">${escapeHtml(insight.text)}</p>
        </div>
    `).join('');
}

function updateMostBorrowed(rows) {
    const tbody = document.getElementById('most-borrowed-body');
    if (!tbody) return;
    const items = rows || [];
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="2" class="empty-cell compact"><p>No data yet</p></td></tr>';
        return;
    }
    tbody.innerHTML = items.map((item, i) => `
        <tr>
            <td>
                <div class="table-title-cell">
                    <span class="rank-badge">${i + 1}</span>
                    <span>${escapeHtml(item.title)}</span>
                </div>
            </td>
            <td class="text-right"><span class="count-pill">${item.borrow_count ?? 0}</span></td>
        </tr>
    `).join('');
}

function updateRecentActivity(rows) {
    const tbody = document.getElementById('recent-activity-body');
    if (!tbody) return;
    const items = rows || [];
    if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="2" class="empty-cell compact"><p>No activity yet</p></td></tr>';
        return;
    }
    tbody.innerHTML = items.map(act => `
        <tr>
            <td>
                <div class="activity-cell">
                    <span class="activity-dot"></span>
                    <span class="activity-event">${escapeHtml(formatEventLabel(act.event_type))}</span>
                </div>
            </td>
            <td class="text-muted activity-time">${escapeHtml(formatReportDate(act.created_at))}</td>
        </tr>
    `).join('');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str ?? '';
    return div.innerHTML;
}

function setReportsStatus(text, loading = false) {
    const el = document.getElementById('reports-live-status');
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('is-loading', loading);
}

async function refreshReportsData() {
    const btn = document.getElementById('reports-refresh-btn');
    if (btn) btn.disabled = true;
    setReportsStatus('Refreshing…', true);

    try {
        const res = await fetch('/reports/api/data');
        const payload = await res.json();
        if (!payload.success || !payload.data) {
            setReportsStatus('Refresh failed');
            return;
        }

        const summary = payload.data;
        updateReportStats(summary.dashboard || {});
        updateInsights(summary.analysis || []);
        updateMostBorrowed(summary.dashboard?.most_borrowed || []);
        updateRecentActivity(summary.dashboard?.recent_activity || []);

        destroyCharts();
        initModuleCharts(summary.modules || {});

        setReportsStatus(`Live data · Updated ${formatReportDate(summary.generated_at)}`);
    } catch {
        setReportsStatus('Refresh failed');
    } finally {
        if (btn) btn.disabled = false;
    }
}

function initReportsLive() {
    const refreshBtn = document.getElementById('reports-refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', refreshReportsData);
    }

    refreshReportsData();
    setInterval(refreshReportsData, REPORTS_REFRESH_MS);

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            refreshReportsData();
        }
    });
}

document.addEventListener('DOMContentLoaded', initReportsLive);
