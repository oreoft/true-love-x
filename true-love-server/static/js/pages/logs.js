/**
 * 日志：查 Loki，从新到旧，往下滚自动加载更早的一页
 *
 * 可以按服务和按 bot 筛选。按 bot 只能筛 base 的日志：每个 base 只跑一个 bot，上报时带 bot_id 标签；
 * server 和 AI 同时服务所有 bot，日志里没有这个标签。
 */

import { platformApi } from '../api.js';
import { $, $$, esc, highlight } from '../ui.js';

const SERVICES = ['tl-base', 'tl-server', 'tl-ai'];
const PAGE_SIZE = 50;
// 离页面底部多少像素时加载更早的一页
const LOAD_THRESHOLD = 300;

const filters = { services: [...SERVICES], botId: '', keyword: '' };

export async function show(root, ctx) {
    const state = { nextBeforeNs: '', hasMore: true, loading: false, seq: 0, count: 0 };

    root.innerHTML = `
        <div class="head"><h2 class="grow">日志</h2><button class="btn" id="refresh">刷新</button></div>
        <div class="filters"><span class="muted">服务</span>
            ${SERVICES.map((svc) => `<button class="chip" data-svc="${svc}" aria-pressed="${filters.services.includes(svc)}">${svc}</button>`).join('')}</div>
        <div class="filters"><span class="muted">bot</span>
            <button class="chip" data-bot="" aria-pressed="${!filters.botId}">全部</button>
            ${ctx.bots.map((bot) => `<button class="chip" data-bot="${esc(bot.bot_id)}" aria-pressed="${filters.botId === bot.bot_id}">${esc(bot.name || bot.bot_id)}</button>`).join('')}
            <input class="chip-input" id="keyword" placeholder="关键词，回车搜索" value="${esc(filters.keyword)}"></div>
        ${filters.botId ? '<div class="note">按 bot 筛选时只显示 base 的日志，server 和 AI 的日志不带 bot 标签。</div>' : ''}
        <div class="logs" id="logs"></div>
        <div class="loader" id="loader"></div>`;

    const list = $('#logs', root);
    const loader = $('#loader', root);

    const load = async (fresh) => {
        if (state.loading || (!fresh && !state.hasMore)) return;
        if (fresh) {
            state.seq += 1;
            state.nextBeforeNs = '';
            state.count = 0;
            list.innerHTML = '';
        }
        const seq = state.seq;
        state.loading = true;
        loader.textContent = '加载中…';
        try {
            const page = await platformApi.logs({
                beforeNs: state.nextBeforeNs,
                services: filters.services.length === SERVICES.length ? '' : filters.services.join(','),
                keyword: filters.keyword,
                botId: filters.botId,
                limit: PAGE_SIZE,
            });
            if (seq !== state.seq) return;
            state.nextBeforeNs = page.next_before_ns;
            state.hasMore = page.has_more;
            state.count += page.logs.length;
            list.insertAdjacentHTML('beforeend', page.logs.map((log) => `
                <div class="log"><span>${esc(log.time_str)}</span><span class="lv-${esc(log.level)}">${esc(log.level)}</span>
                    <span>${esc(log.service)}</span><span>${highlight(log.content, filters.keyword)}</span></div>`).join(''));
            loader.textContent = state.hasMore ? '往下滚加载更早的日志' : (state.count ? '没有更早的日志了' : '没有符合条件的日志');
        } catch (e) {
            if (seq === state.seq) loader.textContent = `加载失败：${e.message}`;
        } finally {
            if (seq === state.seq) state.loading = false;
        }
    };

    const rerender = () => show(root, ctx);
    $$('[data-svc]', root).forEach((el) => {
        el.onclick = () => {
            const svc = el.dataset.svc;
            if (filters.services.includes(svc)) {
                if (filters.services.length === 1) return;
                filters.services = filters.services.filter((s) => s !== svc);
            } else {
                filters.services = [...filters.services, svc];
            }
            rerender();
        };
    });
    $$('[data-bot]', root).forEach((el) => { el.onclick = () => { filters.botId = el.dataset.bot; rerender(); }; });
    $('#keyword', root).onkeydown = (e) => {
        if (e.key === 'Enter') { filters.keyword = e.target.value.trim(); rerender(); }
    };
    $('#refresh', root).onclick = () => load(true);

    const onScroll = () => {
        if (document.body.scrollHeight - (window.scrollY + window.innerHeight) < LOAD_THRESHOLD) load(false);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    load(true);
    return () => window.removeEventListener('scroll', onScroll);
}
