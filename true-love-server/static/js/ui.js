/**
 * 页面共用的小工具：转义、提示、弹窗、时间格式
 */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export const esc = (value) => String(value ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const PLATFORM_NAMES = { wechat: '微信', lark: '飞书' };
export const platformName = (platform) => PLATFORM_NAMES[platform] || platform;
export const platformTag = (platform) => `<span class="plat ${esc(platform)}">${esc(platformName(platform))}</span>`;

export function toast(text, type = '') {
    const el = $('#toast');
    el.textContent = text;
    el.className = `toast ${type}`;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, type === 'error' ? 4000 : 2000);
}

/** 执行 action，失败时弹出错误提示；返回 action 的结果，失败时返回 undefined */
export async function attempt(action, success) {
    try {
        const result = await action();
        if (success) toast(success);
        return result;
    } catch (e) {
        toast(e.message, 'error');
        return undefined;
    }
}

export function modal(html) {
    $('#modalRoot').innerHTML = `<div class="modal-back" id="modalBack"><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
    $('#modalBack').addEventListener('click', (e) => {
        if (e.target.id === 'modalBack' || e.target.hasAttribute('data-close')) closeModal();
    });
    const first = $('#modalRoot input, #modalRoot select, #modalRoot textarea');
    if (first) first.focus();
    return $('#modalRoot .modal');
}

export function closeModal() {
    $('#modalRoot').innerHTML = '';
}

/** 确认弹窗，确定后执行 onOk（可以是 async） */
export function confirmModal(title, text, onOk, okLabel = '确定') {
    modal(`<h3>${esc(title)}</h3><div class="muted">${esc(text)}</div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="confirmOk">${esc(okLabel)}</button></div>`);
    $('#confirmOk').onclick = async (e) => {
        e.target.disabled = true;
        closeModal();
        await onOk();
    };
}

const pad = (n) => String(n).padStart(2, '0');

/** ISO 时间 → 本地 "MM-DD HH:mm" */
export function shortTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** ISO 时间 → datetime-local 输入框的值（本地时区） */
export function isoToLocalInput(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** datetime-local 输入框的值 → 带浏览器时区的 ISO-8601 */
export function localInputToIso(value) {
    if (!value) return '';
    const offset = -new Date(value).getTimezoneOffset();
    const sign = offset >= 0 ? '+' : '-';
    return `${value}:00${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
}

/** 距现在多久，如 "3 分钟前" */
export function ago(iso) {
    if (!iso) return '—';
    const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (Number.isNaN(seconds)) return iso;
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return `${Math.floor(seconds / 86400)} 天前`;
}

/** 把 text 里的 keyword 高亮（先转义） */
export function highlight(text, keyword) {
    const safe = esc(text);
    if (!keyword) return safe;
    return safe.split(esc(keyword)).join(`<mark>${esc(keyword)}</mark>`);
}
