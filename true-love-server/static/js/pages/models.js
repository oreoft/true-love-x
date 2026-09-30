/**
 * 模型：所有 bot 共用一套，存在 AI 库里；请求经 server 转发给 AI 的管理接口
 *
 * 每个类别一个主力模型，部分有备用模型（主力失败时用）。清空保存就恢复代码里的默认值。
 */

import { platformApi } from '../api.js';
import { $, $$, attempt, closeModal, esc, modal } from '../ui.js';

const LABELS = {
    chat: '聊天', compress: '压缩会话', vision: '识图', image: '生图',
    image_edit: '改图', video: '生视频', tts: '语音合成',
};

export async function show(root) {
    const { models } = await platformApi.models();
    const reload = () => show(root);
    const cell = (value, builtin) => value
        ? `<span class="mono">${esc(value)}</span>${value !== builtin ? ' <span class="tag">改过</span>' : ''}`
        : '<span class="muted">—</span>';

    root.innerHTML = `
        <div class="head"><h2 class="grow">模型</h2><span class="tag">所有 bot 共用</span></div>
        <div class="table-wrap"><table>
            <thead><tr><th>类别</th><th>主力</th><th>备用</th><th>操作</th></tr></thead>
            <tbody>${models.map((m, i) => `<tr>
                <td>${esc(LABELS[m.category] || m.category)} <span class="mono muted">${esc(m.category)}</span></td>
                <td class="wrap">${cell(m.default, m.builtin_default)}</td>
                <td class="wrap">${cell(m.fallback, m.builtin_fallback)}</td>
                <td><button class="btn sm" data-edit="${i}">修改</button></td>
            </tr>`).join('')}</tbody></table></div>
        <div class="note">写完整的 LiteLLM 模型名，如 openai/gpt-5.5。改完立即生效；清空保存会恢复默认值。群里的 set_model 技能改的也是这里。</div>`;

    $$('[data-edit]', root).forEach((el) => { el.onclick = () => form(models[el.dataset.edit], reload); });
}

function form(model, reload) {
    modal(`<h3>修改模型 · ${esc(LABELS[model.category] || model.category)}</h3>
        <div class="field"><label for="fDefault">主力</label>
            <input id="fDefault" class="mono" value="${esc(model.default)}" placeholder="默认：${esc(model.builtin_default)}"></div>
        <div class="field"><label for="fFallback">备用（可选）</label>
            <input id="fFallback" class="mono" value="${esc(model.fallback)}" placeholder="${model.builtin_fallback ? `默认：${esc(model.builtin_fallback)}` : '不填就没有备用'}"></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);
    $('#save').onclick = async (e) => {
        e.target.disabled = true;
        const changes = [['default', $('#fDefault').value.trim()], ['fallback', $('#fFallback').value.trim()]]
            .filter(([key, value]) => value !== (model[key] || ''));
        let ok = true;
        for (const [key, value] of changes) {
            ok = (await attempt(() => platformApi.modelSave(model.category, key, value))) !== undefined && ok;
        }
        e.target.disabled = false;
        if (ok) { closeModal(); reload(); }
    };
}
