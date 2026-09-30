/**
 * 技能权限：谁能用哪个技能，存在 AI 库里；请求经 server 转发给 AI 的管理接口
 *
 * 每个技能可以有「所有 bot 共用」和「这个 bot 自己」两条规则，这个 bot 自己的优先；都没有就所有人都能用。
 * 代码里写死权限的技能在这里改不了，只展示。
 */

import { botApi } from '../api.js';
import { $, $$, attempt, closeModal, esc, modal, toast } from '../ui.js';

const ALL_BOTS = '*';

export async function show(root, ctx) {
    const bot = ctx.bot;
    const api = botApi(bot.bot_id);
    const { rules, skills } = await api.permissions();
    const botName = bot.name || bot.bot_id;
    const reload = () => show(root, ctx);
    const ruleOf = (skill, botId) => rules.find((r) => r.skill === skill && r.bot_id === botId);
    const users = (rule) => rule ? `<span class="mono">${esc(rule.users.join('、'))}</span>` : '<span class="muted">—</span>';

    const configurable = skills.filter((s) => !s.code_permissions);
    // 有规则的排前面
    configurable.sort((a, b) => Number(Boolean(ruleOf(b.name, bot.bot_id) || ruleOf(b.name, ALL_BOTS)))
        - Number(Boolean(ruleOf(a.name, bot.bot_id) || ruleOf(a.name, ALL_BOTS))));

    root.innerHTML = `
        <div class="head"><h2 class="grow">技能权限 · ${esc(botName)}</h2></div>
        <div class="table-wrap"><table>
            <thead><tr><th>技能</th><th style="min-width:120px">所有 bot 共用</th><th style="min-width:120px">${esc(botName)} 自己</th><th>生效</th><th>操作</th></tr></thead>
            <tbody>${configurable.map((s, i) => {
                const own = ruleOf(s.name, bot.bot_id);
                const shared = ruleOf(s.name, ALL_BOTS);
                const effective = own || shared;
                return `<tr>
                    <td><span class="mono">${esc(s.name)}</span><div class="muted wrap" style="font-size:12px">${esc(s.description.length > 36 ? `${s.description.slice(0, 36)}…` : s.description)}</div></td>
                    <td class="wrap">${users(shared)}</td><td class="wrap">${users(own)}</td>
                    <td>${effective ? '<span class="pill bad">限制</span>' : '<span class="pill ok">所有人</span>'}</td>
                    <td><div class="actions"><button class="btn sm" data-edit="${i}">修改</button></div></td>
                </tr>`;
            }).join('')}</tbody></table></div>
        ${skills.some((s) => s.code_permissions) ? `<div class="note">代码里写死权限的技能：${skills.filter((s) => s.code_permissions)
            .map((s) => `<span class="mono">${esc(s.name)}</span>（${esc(s.code_permissions.join('、'))}）`).join('，')}</div>` : ''}
        <div class="note">按「平台:用户」写，一行一个，如 wechat:张三；wechat:* 是微信所有人，* 是所有人。微信的用户是昵称（base 读不到对方的 wxid）。
            动态技能的权限在「技能」页面单独设置。</div>`;

    $$('[data-edit]', root).forEach((el) => {
        const skill = configurable[el.dataset.edit];
        el.onclick = () => form(api, botName, skill, ruleOf(skill.name, bot.bot_id), ruleOf(skill.name, ALL_BOTS), reload);
    });
}

function form(api, botName, skill, own, shared, reload) {
    const lines = (rule) => esc(rule ? rule.users.join('\n') : '');
    modal(`<h3>技能权限 · <span class="mono">${esc(skill.name)}</span></h3>
        <div class="muted wrap">${esc(skill.description)}</div>
        <div class="field"><label for="fOwn">${esc(botName)} 自己（优先）</label>
            <textarea id="fOwn" rows="3" placeholder="不填就用所有 bot 共用的">${lines(own)}</textarea></div>
        <div class="field"><label for="fShared">所有 bot 共用</label>
            <textarea id="fShared" rows="3" placeholder="都不填就所有人都能用">${lines(shared)}</textarea></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);
    $('#save').onclick = async (e) => {
        const parse = (id) => $(id).value.split('\n').map((line) => line.trim()).filter(Boolean);
        const changes = [[{}, own, parse('#fOwn')], [{ all_bots: true }, shared, parse('#fShared')]]
            .filter(([, rule, users]) => (rule ? rule.users.join('\n') : '') !== users.join('\n'));
        e.target.disabled = true;
        let ok = true;
        for (const [scope, , users] of changes) {
            const action = users.length
                ? () => api.permissionSave({ ...scope, skill: skill.name, users })
                : () => api.permissionDelete({ ...scope, skill: skill.name });
            ok = (await attempt(action)) !== undefined && ok;
        }
        e.target.disabled = false;
        if (ok) { toast('技能权限已保存'); closeModal(); reload(); }
    };
}
