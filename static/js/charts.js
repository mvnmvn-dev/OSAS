const CHART_COLORS = {
    indigo: '#4f46e5',
    emerald: '#059669',
    amber: '#d97706',
    red: '#dc2626',
    violet: '#7c3aed',
    cyan: '#0891b2',
    slate: '#64748b',
    pink: '#db2777',
};

const CHART_PALETTE = [
    CHART_COLORS.indigo,
    CHART_COLORS.emerald,
    CHART_COLORS.amber,
    CHART_COLORS.red,
    CHART_COLORS.violet,
    CHART_COLORS.cyan,
    CHART_COLORS.pink,
    CHART_COLORS.slate,
];

const chartInstances = [];

function destroyCharts() {
    chartInstances.forEach(chart => chart?.destroy());
    chartInstances.length = 0;
}

function trackChart(chart) {
    if (chart) chartInstances.push(chart);
    return chart;
}

function chartLabel(value) {
    if (!value) return 'Unknown';
    return String(value).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function rowsToChart(rows, labelKey, valueKey = 'count') {
    const labels = (rows || []).map(r => chartLabel(r[labelKey]));
    const data = (rows || []).map(r => Number(r[valueKey]) || 0);
    return { labels, data };
}

function createDoughnut(canvasId, labels, data, options = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !labels.length) return null;
    return trackChart(new Chart(el, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: CHART_PALETTE.slice(0, labels.length),
                borderWidth: 2,
                borderColor: '#fff',
                hoverOffset: 6,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '62%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { boxWidth: 10, padding: 12, font: { size: 11 } },
                },
            },
            ...options,
        },
    }));
}

function createBar(canvasId, labels, data, options = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !labels.length) return null;
    return trackChart(new Chart(el, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: labels.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length]),
                borderRadius: 6,
                borderSkipped: false,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 11 } } },
                y: { beginAtZero: true, ticks: { precision: 0, font: { size: 11 } } },
            },
            ...options,
        },
    }));
}

function createLine(canvasId, labels, data, options = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !labels.length) return null;
    return trackChart(new Chart(el, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                data,
                borderColor: CHART_COLORS.indigo,
                backgroundColor: 'rgba(79, 70, 229, 0.1)',
                fill: true,
                tension: 0.35,
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBackgroundColor: CHART_COLORS.indigo,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 11 } } },
                y: { beginAtZero: true, ticks: { precision: 0, font: { size: 11 } } },
            },
            ...options,
        },
    }));
}

function initModuleCharts(modules, prefix = '') {
    if (!modules) return;

    const p = prefix ? `${prefix}-` : '';

    const borrowing = rowsToChart(modules.borrowing?.by_status, 'status');
    createDoughnut(`${p}chart-borrowing`, borrowing.labels, borrowing.data);

    const monthly = modules.borrowing?.monthly || [];
    createLine(
        `${p}chart-borrowing-trend`,
        monthly.map(r => r.period),
        monthly.map(r => Number(r.count) || 0)
    );

    const reservations = rowsToChart(modules.reservations?.by_status, 'status');
    createDoughnut(`${p}chart-reservations`, reservations.labels, reservations.data);

    const fines = rowsToChart(modules.fines?.by_status, 'status', 'total');
    createBar(`${p}chart-fines`, fines.labels, fines.data);

    const revenue = modules.fines?.revenue_trend || [];
    createLine(
        `${p}chart-revenue`,
        revenue.map(r => String(r.day)),
        revenue.map(r => Number(r.revenue) || 0)
    );

    const inventory = rowsToChart(modules.inventory_status, 'status');
    createDoughnut(`${p}chart-inventory`, inventory.labels, inventory.data);

    const genres = rowsToChart(modules.genre_distribution, 'genre');
    createBar(`${p}chart-genres`, genres.labels, genres.data);

    const peak = modules.peak_hours || [];
    createBar(
        `${p}chart-peak`,
        peak.map(r => `${r.hour}:00`),
        peak.map(r => Number(r.events) || 0)
    );

    const ebooks = rowsToChart(modules.ebooks?.by_format, 'format');
    createBar(`${p}chart-ebooks`, ebooks.labels, ebooks.data);

    const clearance = rowsToChart(modules.clearance?.by_status, 'status');
    createDoughnut(`${p}chart-clearance`, clearance.labels, clearance.data);

    const lostDamaged = rowsToChart(modules.lost_damaged?.by_type, 'report_type');
    createBar(`${p}chart-lost-damaged`, lostDamaged.labels, lostDamaged.data);

    const events = rowsToChart(modules.module_events, 'event_type');
    createBar(`${p}chart-events`, events.labels, events.data);

    const users = rowsToChart(modules.users_by_role, 'role');
    createDoughnut(`${p}chart-users`, users.labels, users.data);
}

function createHorizontalBar(canvasId, labels, data, options = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !labels.length) return null;
    return trackChart(new Chart(el, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: labels.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length]),
                borderRadius: 6,
                borderSkipped: false,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, ticks: { precision: 0, font: { size: 11 } } },
                y: { grid: { display: false }, ticks: { font: { size: 11 } } },
            },
            ...options,
        },
    }));
}

function createMultiLine(canvasId, labels, datasets, options = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !labels.length || !datasets?.length) return null;
    const styles = [
        { borderColor: CHART_COLORS.emerald, backgroundColor: 'rgba(5, 150, 105, 0.12)', borderDash: [] },
        { borderColor: CHART_COLORS.red, backgroundColor: 'rgba(220, 38, 38, 0.08)', borderDash: [6, 4] },
        { borderColor: CHART_COLORS.indigo, backgroundColor: 'rgba(79, 70, 229, 0.1)', borderDash: [] },
    ];
    return trackChart(new Chart(el, {
        type: 'line',
        data: {
            labels,
            datasets: datasets.map((ds, i) => ({
                label: ds.label,
                data: ds.data,
                borderColor: styles[i % styles.length].borderColor,
                backgroundColor: styles[i % styles.length].backgroundColor,
                borderDash: styles[i % styles.length].borderDash,
                fill: true,
                tension: 0.35,
                pointRadius: 3,
                pointHoverRadius: 5,
                pointBackgroundColor: styles[i % styles.length].borderColor,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { boxWidth: 10, padding: 12, font: { size: 11 } },
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { font: { size: 11 } } },
                y: { beginAtZero: true, ticks: { precision: 0, font: { size: 11 } } },
            },
            ...options,
        },
    }));
}

function createRadar(canvasId, labels, data, targets = [], options = {}) {
    const el = document.getElementById(canvasId);
    if (!el || !labels.length) return null;
    const datasets = [{
        label: 'Current',
        data,
        borderColor: CHART_COLORS.indigo,
        backgroundColor: 'rgba(79, 70, 229, 0.18)',
        pointBackgroundColor: CHART_COLORS.indigo,
        pointRadius: 3,
        borderWidth: 2,
    }];
    if (targets?.length) {
        datasets.push({
            label: 'Target',
            data: targets,
            borderColor: CHART_COLORS.slate,
            backgroundColor: 'rgba(100, 116, 139, 0.06)',
            borderDash: [5, 4],
            pointRadius: 0,
            borderWidth: 1.5,
        });
    }
    return trackChart(new Chart(el, {
        type: 'radar',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { boxWidth: 10, padding: 12, font: { size: 11 } },
                },
            },
            scales: {
                r: {
                    beginAtZero: true,
                    max: 100,
                    ticks: { display: false, stepSize: 20 },
                    pointLabels: { font: { size: 11 } },
                    grid: { color: 'rgba(148, 163, 184, 0.35)' },
                },
            },
            ...options,
        },
    }));
}

function ensureChartSeries(payload, fallbackLabel = 'No data') {
    if (!payload) return { labels: [fallbackLabel], data: [0] };
    const labels = payload.labels || [];
    const data = payload.data || [];
    if (!labels.length) return { labels: [fallbackLabel], data: [0] };
    return { labels, data };
}

function initSafetyDashboardCharts(analytics) {
    if (!analytics || typeof Chart === 'undefined') return;
    destroyCharts();

    const trend = ensureChartSeries(analytics.incident_trend);
    createLine('chart-incident-trend', trend.labels, trend.data);

    const status = ensureChartSeries(analytics.incident_status);
    createDoughnut('chart-incident-status', status.labels, status.data);

    const priority = ensureChartSeries(analytics.incident_priority);
    createBar('chart-incident-priority', priority.labels, priority.data);

    const zones = ensureChartSeries(analytics.zone_risk);
    createHorizontalBar('chart-zone-risk', zones.labels, zones.data);

    const patrol = analytics.patrol_trend || {};
    if (patrol.labels?.length) {
        createMultiLine('chart-patrol-trend', patrol.labels, [
            { label: 'Valid scans', data: patrol.valid || [] },
            { label: 'Missed / late', data: patrol.missed || [] },
        ]);
    }

    const modules = ensureChartSeries(analytics.module_activity);
    createDoughnut('chart-module-activity', modules.labels, modules.data);

    const health = analytics.module_health || {};
    if (health.labels?.length) {
        createRadar('chart-module-health', health.labels, health.data || [], health.targets || []);
    }

    const inspections = ensureChartSeries(analytics.inspection_status);
    createBar('chart-inspection-status', inspections.labels, inspections.data);

    const duty = ensureChartSeries(analytics.team_duty);
    createDoughnut('chart-team-duty', duty.labels, duty.data);
}
