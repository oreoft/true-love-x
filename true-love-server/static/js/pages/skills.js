/**
 * 技能：内置技能（代码里的）和安装的技能（聊天里或后台保存的 shell 命令），所有 bot 共用；
 * 请求经 server 转发给 AI 的管理接口
 *
 * 每个技能有一组权限点，一行一个，匹配上任意一个就能用：
 *   *:*  所有人 · wechat:*  所有微信号 · wechat:<号>:*  这个号 · wechat:<号>:<昵称>  这个号里的这个人（号写 * 就是所有号）
 *   wechat:<号>:<群名>:*  只在这个群（最后一段写昵称就是群里的这个人）
 */

import { platformApi } from '../api.js';
import { $, $$, attempt, closeModal, confirmModal, esc, modal, shortTime, toast } from '../ui.js';

const HELP = `一行一个权限点，匹配上任意一行就能用：
<span class="mono">*:*</span> 所有人 · <span class="mono">wechat:*</span> 所有微信号 · <span class="mono">wechat:号:*</span> 这个号 ·
<span class="mono">wechat:号:昵称</span> 这个号里的这个人（号写 <span class="mono">*</span> 就是所有号） ·
<span class="mono">wechat:号:群名:*</span> 只在这个群。号是 bot_id，人和群按微信里显示的昵称、群名写。`;

const points = (list) => (list || []).map((p) => `<span class="mono" style="white-space:nowrap">${esc(p)}</span>`).join('<br>') || '<span class="muted">—</span>';
const isOpen = (list) => (list || []).includes('*:*');
// 列表里只放一行预览，完整内容点「修改」看
const clip = (text, max) => {
    const flat = String(text || '').replace(/\s+/g, ' ').trim();
    return flat.length > max ? `${flat.slice(0, max)}…` : flat;
};
const lines = (text) => text.split('\n').map((line) => line.trim()).filter(Boolean);

let tab = 'builtin';

export async function show(root) {
    render(root, await platformApi.skills());
}

/** 重新拉数据：AI 那边比较慢，拉的时候页面变灰、按钮点不了 */
async function reload(root) {
    root.style.opacity = '0.5';
    root.style.pointerEvents = 'none';
    try {
        render(root, await platformApi.skills());
    } catch (e) {
        toast(e.message, 'error');
    } finally {
        root.style.opacity = '';
        root.style.pointerEvents = '';
    }
}

/** 切 tab 只重画，不重新拉数据 */
function render(root, data) {
    const { builtin, installed, default_permissions: defaults } = data;
    const refresh = () => reload(root);
    // 有限制的排前面，方便看谁被管着
    builtin.sort((a, b) => Number(isOpen(a.permissions)) - Number(isOpen(b.permissions)) || a.name.localeCompare(b.name));

    const restricted = builtin.filter((s) => !isOpen(s.permissions)).length;
    const builtinTab = () => `
        <div class="table-wrap"><table>
            <thead><tr><th>技能</th><th>谁能用</th><th>操作</th></tr></thead>
            <tbody>${builtin.map((s, i) => `<tr>
                <td><span class="mono">${esc(s.name)}</span>
                    <div class="muted wrap" style="font-size:12px">${esc(s.description.length > 50 ? `${s.description.slice(0, 50)}…` : s.description)}</div></td>
                <td class="wrap">${points(s.permissions)}</td>
                <td><button class="btn sm" data-builtin="${i}">改权限</button></td>
            </tr>`).join('')}</tbody></table></div>
        <div class="note">代码里新加的内置技能，第一次加载时给这些权限点：${points(defaults)}
            <button class="btn sm" id="defaults" style="margin-left:8px">修改</button></div>`;
    const installedTab = () => `
        ${installed.length ? `<div class="table-wrap"><table>
            <thead><tr style="white-space:nowrap"><th>ID</th><th>名称</th><th>命令</th><th>谁能用</th><th>使用</th><th>操作</th></tr></thead>
            <tbody>${installed.map((s, i) => `<tr>
                <td class="mono" style="white-space:nowrap">${esc(s.id)}</td>
                <td style="min-width:140px">${esc(s.name)}<div class="muted" style="font-size:12px">${esc(clip(s.description, 24))}</div></td>
                <td class="mono muted" title="${esc(s.command)}" style="white-space:nowrap">${esc(clip(s.command, 24))}</td>
                <td class="wrap">${points(s.permissions)}</td>
                <td style="white-space:nowrap">${esc(s.usage_count ?? 0)} 次<div class="muted mono" style="font-size:12px">${esc(shortTime(s.last_used_at))}</div></td>
                <td><div class="actions" style="flex-wrap:nowrap"><button class="btn sm" data-edit="${i}">修改</button>
                    <button class="btn sm danger" data-delete="${i}">删除</button></div></td>
            </tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">还没有安装的技能。在聊天里让机器人保存一段命令，或者点「添加技能」。</div>'}
        <div class="note">聊天里安装的技能：群里装的默认只在这个群能用，私聊装的默认在这个号能用。</div>`;

    root.innerHTML = `
        <div class="head"><h2 class="grow">技能</h2><span class="tag">所有 bot 共用</span>
            ${tab === 'installed' ? '<button class="btn primary" id="add">添加技能</button>' : ''}</div>
        <div class="tabs" role="tablist">
            <button class="tab" role="tab" data-tab="builtin" aria-selected="${tab === 'builtin'}">内置 ${builtin.length}${restricted ? `（${restricted} 个有限制）` : ''}</button>
            <button class="tab" role="tab" data-tab="installed" aria-selected="${tab === 'installed'}">安装的 ${installed.length}</button>
        </div>
        ${tab === 'builtin' ? builtinTab() : installedTab()}
        <div class="note">${HELP}</div>`;

    $$('[data-tab]', root).forEach((el) => { el.onclick = () => { tab = el.dataset.tab; render(root, data); }; });
    if ($('#add', root)) $('#add', root).onclick = () => form(null, defaults, refresh);
    if ($('#defaults', root)) $('#defaults', root).onclick = () => pointsForm('新内置技能的默认权限点', defaults,
        (list) => platformApi.defaultPermissionsSave(list), refresh);
    $$('[data-builtin]', root).forEach((el) => {
        const skill = builtin[el.dataset.builtin];
        el.onclick = () => pointsForm(`谁能用 · ${skill.name}`, skill.permissions,
            (list) => platformApi.skillPermissionsSave(skill.name, list), refresh, skill.description);
    });
    $$('[data-edit]', root).forEach((el) => { el.onclick = () => form(installed[el.dataset.edit], defaults, refresh); });
    $$('[data-delete]', root).forEach((el) => {
        const skill = installed[el.dataset.delete];
        el.onclick = () => confirmModal('删除技能', `确定要删除技能「${skill.name || skill.id}」吗？`, async () => {
            if (await attempt(() => platformApi.skillDelete(skill.id), '技能已删除') !== undefined) refresh();
        }, '删除');
    });
}

/** 只改权限点的弹窗 */
function pointsForm(title, current, save, reload, description = '') {
    modal(`<h3>${esc(title)}</h3>
        ${description ? `<div class="muted wrap">${esc(description)}</div>` : ''}
        <div class="field"><label for="fPoints">权限点（一行一个）</label>
            <textarea id="fPoints" class="mono" rows="5">${esc((current || []).join('\n'))}</textarea></div>
        <div class="muted" style="font-size:12px">${HELP}</div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);
    $('#save').onclick = async (e) => {
        const list = lines($('#fPoints').value);
        if (!list.length) { toast('至少写一个权限点；所有人都能用就写 *:*', 'error'); return; }
        e.target.disabled = true;
        e.target.textContent = '保存中…';
        const done = await attempt(() => save(list), '权限已保存');
        e.target.disabled = false;
        e.target.textContent = '保存';
        if (done !== undefined) { closeModal(); reload(); }
    };
}

const isJson = (text) => {
    if (!text) return true;
    try { JSON.parse(text); return true; } catch (e) { return false; }
};

function form(skill, defaults, reload) {
    const value = (field) => esc(skill ? skill[field] || '' : '');
    modal(`<h3>${skill ? '修改' : '添加'}技能</h3>
        <div class="field"><label for="fId">ID</label>
            ${skill ? `<div class="mono">${esc(skill.id)}</div>` : '<input id="fId" placeholder="小写英文加下划线，如 query_pypi">'}</div>
        <div class="field"><label for="fName">名称</label><input id="fName" value="${value('name')}" placeholder="如：查询PyPI包版本"></div>
        <div class="field"><label for="fDesc">描述</label><textarea id="fDesc" placeholder="注入 LLM 上下文，描述触发场景">${value('description')}</textarea></div>
        <div class="field"><label for="fCommand">命令</label><textarea id="fCommand" placeholder="shell 命令，支持 {param} 占位符">${value('command')}</textarea></div>
        <div class="field"><label for="fParams">参数（JSON，可选）</label>
            <textarea id="fParams" placeholder='如：{"pkg":{"default":"requests","desc":"包名"}}'>${value('parameters')}</textarea></div>
        <div class="field"><label for="fPoints">谁能用（权限点，一行一个）</label>
            <textarea id="fPoints" class="mono" rows="3">${esc((skill ? skill.permissions : defaults).join('\n'))}</textarea></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);
    $('#save').onclick = async (e) => {
        const payload = {
            id: skill ? skill.id : $('#fId').value.trim(),
            name: $('#fName').value.trim(),
            description: $('#fDesc').value.trim(),
            command: $('#fCommand').value.trim(),
            parameters: $('#fParams').value.trim() || null,
            permissions: lines($('#fPoints').value),
        };
        if (!payload.id || !payload.name || !payload.description || !payload.command) {
            toast('ID、名称、描述、命令不能为空', 'error');
            return;
        }
        if (!isJson(payload.parameters)) {
            toast('参数必须是合法的 JSON', 'error');
            return;
        }
        if (!payload.permissions.length) { toast('至少写一个权限点；所有人都能用就写 *:*', 'error'); return; }
        e.target.disabled = true;
        e.target.textContent = '保存中…';
        const done = await attempt(() => platformApi.skillSave(payload), skill ? '技能已修改' : '技能已添加');
        e.target.disabled = false;
        e.target.textContent = '保存';
        if (done !== undefined) { closeModal(); reload(); }
    };
}
