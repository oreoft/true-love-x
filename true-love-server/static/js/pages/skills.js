/**
 * 技能：AI 的动态技能，所有 bot 共用；请求经 server 转发给 AI 的管理接口
 */

import { platformApi } from '../api.js';
import { $, $$, attempt, closeModal, confirmModal, esc, modal, shortTime, toast } from '../ui.js';

export async function show(root) {
    const { skills } = await platformApi.skills();
    const reload = () => show(root);

    root.innerHTML = `
        <div class="head"><h2 class="grow">技能</h2><span class="tag">所有 bot 共用</span>
            <button class="btn primary" id="add">添加技能</button></div>
        ${skills.length ? `<div class="table-wrap"><table>
            <thead><tr><th>ID</th><th>名称</th><th>描述</th><th>命令</th><th>谁能用</th><th>调用</th><th>最近使用</th><th>操作</th></tr></thead>
            <tbody>${skills.map((s, i) => `<tr>
                <td class="mono">${esc(s.id)}</td><td>${esc(s.name)}</td><td class="wrap">${esc(s.description)}</td>
                <td class="mono wrap">${esc(s.command)}</td><td class="mono wrap">${esc(s.permissions || '所有人')}</td>
                <td>${esc(s.usage_count ?? 0)}</td><td class="mono">${esc(shortTime(s.last_used_at))}</td>
                <td><div class="actions"><button class="btn sm" data-edit="${i}">修改</button>
                    <button class="btn sm danger" data-delete="${i}">删除</button></div></td>
            </tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">还没有动态技能。</div>'}
        <div class="note">权限按「平台:用户」写，比如 ["wechat:*"]、["lark:*"]，平台由消息所在的 bot 决定。</div>`;

    $('#add', root).onclick = () => form(null, reload);
    $$('[data-edit]', root).forEach((el) => { el.onclick = () => form(skills[el.dataset.edit], reload); });
    $$('[data-delete]', root).forEach((el) => {
        const skill = skills[el.dataset.delete];
        el.onclick = () => confirmModal('删除技能', `确定要删除技能「${skill.name || skill.id}」吗？`, async () => {
            if (await attempt(() => platformApi.skillDelete(skill.id), '技能已删除') !== undefined) reload();
        }, '删除');
    });
}

const isJson = (text) => {
    if (!text) return true;
    try { JSON.parse(text); return true; } catch (e) { return false; }
};

function form(skill, reload) {
    const value = (field) => esc(skill ? skill[field] || '' : '');
    modal(`<h3>${skill ? '修改' : '添加'}技能</h3>
        <div class="field"><label for="fId">ID</label>
            ${skill ? `<div class="mono">${esc(skill.id)}</div>` : '<input id="fId" placeholder="小写英文加下划线，如 query_pypi">'}</div>
        <div class="field"><label for="fName">名称</label><input id="fName" value="${value('name')}" placeholder="如：查询PyPI包版本"></div>
        <div class="field"><label for="fDesc">描述</label><textarea id="fDesc" placeholder="注入 LLM 上下文，描述触发场景">${value('description')}</textarea></div>
        <div class="field"><label for="fCommand">命令</label><textarea id="fCommand" placeholder="shell 命令，支持 {param} 占位符">${value('command')}</textarea></div>
        <div class="field"><label for="fParams">参数（JSON，可选）</label>
            <textarea id="fParams" placeholder='如：{"pkg":{"default":"requests","desc":"包名"}}'>${value('parameters')}</textarea></div>
        <div class="field"><label for="fPerms">权限（JSON，可选）</label>
            <textarea id="fPerms" placeholder='如：["wechat:admin123"] 或 ["lark:*"]'>${value('permissions')}</textarea></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);
    $('#save').onclick = async (e) => {
        const payload = {
            id: skill ? skill.id : $('#fId').value.trim(),
            name: $('#fName').value.trim(),
            description: $('#fDesc').value.trim(),
            command: $('#fCommand').value.trim(),
            parameters: $('#fParams').value.trim() || null,
            permissions: $('#fPerms').value.trim() || null,
        };
        if (!payload.id || !payload.name || !payload.description || !payload.command) {
            toast('ID、名称、描述、命令不能为空', 'error');
            return;
        }
        if (!isJson(payload.parameters) || !isJson(payload.permissions)) {
            toast('参数和权限必须是合法的 JSON', 'error');
            return;
        }
        e.target.disabled = true;
        const done = await attempt(() => platformApi.skillSave(payload), skill ? '技能已修改' : '技能已添加');
        e.target.disabled = false;
        if (done !== undefined) { closeModal(); reload(); }
    };
}
