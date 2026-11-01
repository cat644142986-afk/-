import React, { useState } from 'react';
import { Button } from 'react-aria-components';
import { BUILTIN_TEMPLATES, savePromptTemplate, removePromptTemplate } from './prompt-templates.js';
import { nextPrototypeId } from './canvas-model.js';

export function PromptTemplatePanel({ metadata, onMetadata, text = '', onApply }) {
  const [search, setSearch] = useState(''), [category, setCategory] = useState(''), [draft, setDraft] = useState(null), [error, setError] = useState('');
  const templates = [...BUILTIN_TEMPLATES, ...(metadata.templates || [])];
  const categories = [...new Set(templates.map(item => item.category).filter(Boolean))];
  return <div className="pa-template-manager">
    <div className="pa-template-filters"><input aria-label="搜索模板" placeholder="搜索模板" value={search} onChange={event => setSearch(event.target.value)} />
      <select aria-label="模板分类" value={category} onChange={event => setCategory(event.target.value)}><option value="">全部分类</option>{categories.map(item => <option key={item}>{item}</option>)}</select>
      <Button onPress={() => { setError(''); setDraft({ id: nextPrototypeId('template'), name: '', category: '', text }); }}>存为新模板</Button>
    </div>
    <small>个人模板随当前画布保存与导出；不会自动加入其他任务。</small>
    {error && <p role="alert">{error}</p>}
    {draft && <form className="pa-template-editor" onSubmit={event => {
      event.preventDefault(); try { onMetadata(savePromptTemplate(metadata, draft)); setDraft(null); setError(''); } catch (reason) { setError(reason.message); }
    }}>
      <label>名称<input aria-label="模板名称" maxLength={120} value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} autoFocus /></label>
      <label>分类<input aria-label="模板分类名称" maxLength={60} value={draft.category} onChange={event => setDraft({ ...draft, category: event.target.value })} /></label>
      <label>内容<textarea aria-label="模板内容" maxLength={1200} value={draft.text} onChange={event => setDraft({ ...draft, text: event.target.value })} /></label>
      <div><Button onPress={() => { setDraft(null); setError(''); }}>取消编辑</Button><button type="submit">保存模板</button></div>
    </form>}
    <div className="pa-template-list">{templates.filter(item => (!category || category === item.category) && `${item.name} ${item.text}`.toLowerCase().includes(search.toLowerCase())).map(item => <article key={item.id}>
      <strong>{item.name}</strong><small>{item.readonly ? '内置 · 只读' : '个人'}{item.category && ` · ${item.category}`}</small><pre>{item.text}</pre>
      <div><Button onPress={() => onApply(item.text)}>应用</Button>
        {!item.readonly && <><Button onPress={() => { setDraft({ ...item }); setError(''); }}>修改 / 重命名</Button><Button onPress={() => {
          if (window.confirm(`删除模板“${item.name}”？已应用的提示词不会改变。`)) onMetadata(removePromptTemplate(metadata, item.id));
        }}>删除模板</Button></>}
      </div>
    </article>)}</div>
  </div>;
}
