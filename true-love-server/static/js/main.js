/**
 * tl-admin 入口：机器人切换、导航和按 URL 渲染页面
 *
 * 页面分三类：
 * - 全部 bot：总览
 * - 当前 bot：聊天记录、提醒与定时任务、监听（只有能力列表里有 listen 的机器人才显示）
 * - 平台：技能、日志
 */

import { platformApi } from './api.js';
import { $, closeModal, esc, platformTag, toast } from './ui.js';
import * as overview from './pages/overview.js';
import * as messages from './pages/messages.js';
import * as schedule from './pages/schedule.js';
import * as listen from './pages/listen.js';
import * as skills from './pages/skills.js';
import * as logs from './pages/logs.js';

const BOT_PAGES = {
    messages: { module: messages, label: '聊天记录' },
    schedule: { module: schedule, label: '提醒与定时任务' },
    listen: { module: listen, label: '监听管理', capability: 'listen', tag: '微信' },
};
const PLATFORM_PAGES = {
    skills: { module: skills, label: '技能' },
    logs: { module: logs, label: '日志' },
};
const LAST_BOT = 'tl-admin.bot';

const state = { bots: [], botId: '', page: 'overview', cleanup: null };

const currentBot = () => state.bots.find((bot) => bot.bot_id === state.botId);
const isOnline = (bot) => Boolean(bot && bot.status && bot.status.online);

function rememberBot(botId) {
    try { localStorage.setItem(LAST_BOT, botId); } catch (e) { /* 浏览器不让存就算了 */ }
}

function recalledBot() {
    try { return localStorage.getItem(LAST_BOT) || ''; } catch (e) { return ''; }
}

// ==================== URL ====================

function parseHash() {
    const parts = (location.hash || '').replace(/^#\/?/, '').split('/').map(decodeURIComponent);
    if (parts[0] === 'bot' && parts[1]) return { botId: parts[1], page: parts[2] || 'messages' };
    if (PLATFORM_PAGES[parts[0]]) return { page: parts[0] };
    return { page: 'overview' };
}

export function go(page, botId = state.botId) {
    const hash = BOT_PAGES[page] ? `#/bot/${encodeURIComponent(botId)}/${page}` : `#/${page}`;
    if (location.hash === hash) render(); else location.hash = hash;
}

// ==================== 切换器和导航 ====================

function renderSwitcher() {
    const bot = currentBot();
    $('#swBtn').innerHTML = bot
        ? `<span class="dot ${isOnline(bot) ? 'on' : 'off'}"></span><b>${esc(bot.name || bot.bot_id)}</b>${platformTag(bot.platform)}<span class="muted">▾</span>`
        : '<span class="muted">还没有机器人登记</span>';
    $('#swMenu').innerHTML = state.bots.map((item) => `
        <button class="sw-item" data-bot="${esc(item.bot_id)}" aria-current="${item.bot_id === state.botId}">
            <span class="dot ${isOnline(item) ? 'on' : 'off'}"></span>
            <span style="flex:1;min-width:0"><b>${esc(item.name || item.bot_id)}</b><br><span class="mono muted">${esc(item.bot_id)}</span></span>
            ${platformTag(item.platform)}
        </button>`).join('');
}

function renderNav() {
    const bot = currentBot();
    const link = (page, label, extra = '') =>
        `<a class="nav-item" href="#" data-page="${page}" aria-current="${state.page === page ? 'page' : 'false'}"><span>${label}</span>${extra}</a>`;
    let html = `<div class="nav-group">全部 bot</div>${link('overview', '总览', `<span class="tag">${state.bots.length}</span>`)}`;
    if (bot) {
        html += `<div class="nav-group">当前 bot · ${esc(bot.name || bot.bot_id)}</div>`;
        for (const [page, info] of Object.entries(BOT_PAGES)) {
            if (info.capability && !bot.capabilities.includes(info.capability)) continue;
            html += link(page, info.label, info.tag ? `<span class="tag">${info.tag}</span>` : '');
        }
    }
    html += '<div class="nav-group">平台</div>';
    for (const [page, info] of Object.entries(PLATFORM_PAGES)) html += link(page, info.label);
    $('#nav').innerHTML = html;
}

// ==================== 渲染 ====================

async function render() {
    if (state.cleanup) { state.cleanup(); state.cleanup = null; }
    closeModal();
    const route = parseHash();
    if (route.botId) {
        state.botId = route.botId;
        rememberBot(route.botId);
    }
    const bot = currentBot();
    let page = route.page;
    const botPage = BOT_PAGES[page];
    // 地址里的机器人不存在，或者这个机器人没有这个功能，都退回合适的页面
    if (botPage && !bot) page = 'overview';
    if (botPage && bot && botPage.capability && !bot.capabilities.includes(botPage.capability)) page = 'messages';
    state.page = page;
    if (page !== route.page) history.replaceState(null, '', BOT_PAGES[page] ? `#/bot/${encodeURIComponent(state.botId)}/${page}` : `#/${page}`);

    renderSwitcher();
    renderNav();
    const module = page === 'overview' ? overview : (BOT_PAGES[page] || PLATFORM_PAGES[page]).module;
    const main = $('#main');
    main.innerHTML = '<div class="loader">加载中…</div>';
    const ctx = { bot, bots: state.bots, go, refreshBots, isOnline, selectBot };
    try {
        state.cleanup = (await module.show(main, ctx)) || null;
    } catch (e) {
        main.innerHTML = `<div class="banner">${esc(e.message)}</div>`;
    }
}

async function refreshBots() {
    const data = await platformApi.bots();
    state.bots = data.bots;
    if (!currentBot()) {
        const recalled = recalledBot();
        const fallback = state.bots.find((bot) => bot.bot_id === recalled)
            || state.bots.find((bot) => bot.bot_id === data.default_bot_id) || state.bots[0];
        state.botId = fallback ? fallback.bot_id : '';
    }
    renderSwitcher();
    renderNav();
    return state.bots;
}

function selectBot(botId) {
    const bot = state.bots.find((item) => item.bot_id === botId);
    if (!bot) return;
    state.botId = botId;
    rememberBot(botId);
    $('#swMenu').hidden = true;
    // 平台页和总览切过去看聊天记录；机器人页留在同一页（没有这个功能时 render 会退回）
    go(BOT_PAGES[state.page] ? state.page : 'messages', botId);
    toast(`已切换到 ${bot.name || bot.bot_id}`);
}

// ==================== 事件 ====================

$('#swBtn').addEventListener('click', (e) => {
    const menu = $('#swMenu');
    menu.hidden = !menu.hidden;
    $('#swBtn').setAttribute('aria-expanded', String(!menu.hidden));
    e.stopPropagation();
});
document.addEventListener('click', (e) => {
    const item = e.target.closest('.sw-item');
    if (item) selectBot(item.dataset.bot);
    if (!e.target.closest('.switcher')) $('#swMenu').hidden = true;
    const nav = e.target.closest('.nav-item');
    if (nav) {
        e.preventDefault();
        go(nav.dataset.page);
    }
});
window.addEventListener('hashchange', render);

refreshBots()
    .catch((e) => toast(e.message, 'error'))
    .finally(render);
