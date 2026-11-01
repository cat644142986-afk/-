export const BUILTIN_TEMPLATES = Object.freeze([
  { id: 'builtin_exact_edit', name: '只修改指定内容', category: '精确修改', text: '只修改：\n保持其余文字、构图、比例、配色、材质与细节不变。不要重新设计或添加元素。', readonly: true },
  { id: 'builtin_reference', name: '沿参考继续创作', category: '参考创作', text: '沿用参考中的视觉语言与主体特征，创作新的方案：\n需要改变：\n必须保留：', readonly: true },
]);

export function savePromptTemplate(metadata, value) {
  const template = { id: value.id, name: value.name.trim(), category: value.category.trim(), text: value.text };
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(template.id) || template.id.startsWith('builtin_')) throw new Error('模板身份无效');
  if (!template.name || template.name.length > 120 || template.category.length > 60 || !template.text.trim() || template.text.length > 1200) throw new Error('请填写模板名称和内容（最多 1200 字）');
  const current = metadata.templates || [];
  const exists = current.some(item => item.id === template.id);
  if (!exists && current.length >= 100) throw new Error('当前画布最多保留 100 个模板');
  return { ...metadata, templates: exists ? current.map(item => item.id === template.id ? template : item) : [...current, template] };
}

export function removePromptTemplate(metadata, id) {
  if (id.startsWith('builtin_')) throw new Error('内置模板只读');
  return { ...metadata, templates: (metadata.templates || []).filter(item => item.id !== id) };
}
