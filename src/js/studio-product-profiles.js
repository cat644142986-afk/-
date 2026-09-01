const PROFILE_ID_PATTERN = /^[a-z][a-z0-9._:-]{2,127}$/;
const HEX_COLOR_PATTERN = /^#[0-9a-f]{6}$/i;
const SELECTION_UI_KEY = 'product_profile_selection';

const COMPONENT_ROLES = {
  core: '核心主体',
  container: '容器 / 盘子',
  cap: '瓶盖 / 封口',
  label: '标签 / 包装',
  accessory: '配件',
  shadow: '阴影',
  background: '背景',
  other: '其他',
};

const COMPONENT_POLICIES = {
  must_preserve: '必须保留',
  optional_preserve: '可选保留',
  allow_modify: '允许修改',
  forbid_modify: '禁止修改',
};

const SELECTION_MODES = {
  core_only: '只选核心主体',
  core_with_container: '主体 + 容器 / 盘子',
  full_composition: '保留整个组合',
  separate_all: '全部拆出',
};

function cloneJson(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function localId(prefix) {
  const suffix = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID().replaceAll('-', '').slice(0, 20)
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}:${suffix}`;
}

function text(value) {
  return String(value ?? '').trim();
}

function numberValue(value, fallback = 0) {
  const candidate = Number(value);
  return Number.isFinite(candidate) ? candidate : fallback;
}

function optionMarkup(items, selected, escapeHtml) {
  return Object.entries(items).map(([value, label]) => (
    `<option value="${escapeHtml(value)}"${value === selected ? ' selected' : ''}>${escapeHtml(label)}</option>`
  )).join('');
}

function validationToken(path) {
  return String(path || 'form').replace(/[^a-z0-9_-]+/gi, '-').replace(/^-|-$/g, '');
}

function validationTargetId(path) {
  const fixed = {
    sku: 'product-profile-sku',
    name: 'product-profile-name',
    category: 'product-profile-category',
    'specification.display': 'product-profile-display',
    'specification.unit_count': 'product-profile-unit-count',
    selection_mode: 'product-profile-selection-mode',
    components: 'product-profile-components-section',
    platform_specs: 'product-profile-platforms-section',
    approved_reference_ids: 'product-profile-references-section',
  };
  return fixed[path] || `product-profile-field-${validationToken(path)}`;
}

function validationErrorId(path) {
  return `product-profile-error-${validationToken(path)}`;
}

export function createEmptyProductProfile(now = new Date().toISOString()) {
  const componentId = localId('component');
  return {
    id: localId('profile'),
    schema_version: 1,
    sku: '',
    name: '',
    revision: 0,
    category: '',
    specification: {
      display: '',
      net_content: '',
      unit_count: 1,
      attributes: [],
    },
    components: [{
      id: componentId,
      name: '核心商品',
      role: 'core',
      policy: 'must_preserve',
      quantity: 1,
    }],
    materials: [],
    brand_colors: [],
    packaging_texts: [],
    logos: [],
    platform_specs: [{
      platform: '通用电商',
      role: '主图',
      pixel_width: 2048,
      pixel_height: 2048,
      format: 'png',
      safe_area_percent: 5,
    }],
    selection_mode: 'full_composition',
    approved_reference_ids: [],
    created_at: now,
    updated_at: now,
  };
}

export function productProfileSelectionFromUiState(uiState) {
  const value = uiState?.[SELECTION_UI_KEY];
  const id = text(value?.product_profile_id);
  const revision = Number(value?.expected_product_profile_revision);
  if (!id || !Number.isInteger(revision) || revision < 1) return null;
  return { id, revision };
}

export function uiStateWithProductProfileSelection(uiState, selection) {
  const next = cloneJson(uiState || {}) || {};
  if (!selection?.id) {
    delete next[SELECTION_UI_KEY];
    return next;
  }
  next[SELECTION_UI_KEY] = {
    product_profile_id: String(selection.id),
    expected_product_profile_revision: Number(selection.revision),
  };
  return next;
}

export function validateProductProfile(profile) {
  const errors = [];
  const add = (path, message) => errors.push({ path, message });
  const required = (path, value, label) => {
    if (!text(value)) add(path, `请填写${label}`);
  };

  if (!PROFILE_ID_PATTERN.test(text(profile?.id))) add('id', '商品档案标识无效');
  required('sku', profile?.sku, 'SKU');
  required('name', profile?.name, '商品名称');
  required('category', profile?.category, '商品类目');
  required('specification.display', profile?.specification?.display, '规格展示');
  if (!Number.isInteger(Number(profile?.specification?.unit_count)) || Number(profile?.specification?.unit_count) < 1) {
    add('specification.unit_count', '件数必须是大于 0 的整数');
  }

  const attributes = Array.from(profile?.specification?.attributes || []);
  const attributeKeys = new Set();
  attributes.forEach((item, index) => {
    const key = text(item?.key);
    required(`specification.attributes.${index}.key`, key, '属性名');
    required(`specification.attributes.${index}.value`, item?.value, '属性值');
    if (key && attributeKeys.has(key.toLocaleLowerCase('zh-CN'))) {
      add(`specification.attributes.${index}.key`, '属性名不能重复');
    }
    attributeKeys.add(key.toLocaleLowerCase('zh-CN'));
  });

  const components = Array.from(profile?.components || []);
  if (!components.length) add('components', '至少保留一个商品组件');
  const componentIds = new Set();
  components.forEach((item, index) => {
    const prefix = `components.${index}`;
    if (!PROFILE_ID_PATTERN.test(text(item?.id))) add(`${prefix}.id`, '组件标识无效');
    if (componentIds.has(item?.id)) add(`${prefix}.id`, '组件标识不能重复');
    componentIds.add(item?.id);
    required(`${prefix}.name`, item?.name, '组件名称');
    if (!Object.hasOwn(COMPONENT_ROLES, item?.role)) add(`${prefix}.role`, '请选择组件角色');
    if (!Object.hasOwn(COMPONENT_POLICIES, item?.policy)) add(`${prefix}.policy`, '请选择保护策略');
    if (!Number.isInteger(Number(item?.quantity)) || Number(item?.quantity) < 1) {
      add(`${prefix}.quantity`, '数量必须是大于 0 的整数');
    }
  });

  const materialComponents = new Set();
  Array.from(profile?.materials || []).forEach((item, index) => {
    const prefix = `materials.${index}`;
    if (!componentIds.has(item?.component_id)) add(`${prefix}.component_id`, '材质必须绑定现有组件');
    if (materialComponents.has(item?.component_id)) add(`${prefix}.component_id`, '每个组件只能填写一组材质');
    materialComponents.add(item?.component_id);
    required(`${prefix}.material`, item?.material, '材质');
    if (typeof item?.transparent !== 'boolean') add(`${prefix}.transparent`, '请选择是否透明');
  });

  const colorNames = new Set();
  Array.from(profile?.brand_colors || []).forEach((item, index) => {
    const prefix = `brand_colors.${index}`;
    const name = text(item?.name);
    required(`${prefix}.name`, name, '品牌色名称');
    if (name && colorNames.has(name.toLocaleLowerCase('zh-CN'))) add(`${prefix}.name`, '品牌色名称不能重复');
    colorNames.add(name.toLocaleLowerCase('zh-CN'));
    if (!HEX_COLOR_PATTERN.test(text(item?.value))) add(`${prefix}.value`, '品牌色必须是六位色值');
  });

  const validateAnnotations = (items, field, label) => {
    const ids = new Set();
    Array.from(items || []).forEach((item, index) => {
      const prefix = `${field}.${index}`;
      if (!PROFILE_ID_PATTERN.test(text(item?.id))) add(`${prefix}.id`, `${label}标识无效`);
      if (ids.has(item?.id)) add(`${prefix}.id`, `${label}标识不能重复`);
      ids.add(item?.id);
      if (!componentIds.has(item?.component_id)) add(`${prefix}.component_id`, `${label}必须绑定现有组件`);
      required(`${prefix}.${field === 'logos' ? 'name' : 'content'}`, field === 'logos' ? item?.name : item?.content, label);
    });
  };
  validateAnnotations(profile?.packaging_texts, 'packaging_texts', '包装文字');
  validateAnnotations(profile?.logos, 'logos', 'Logo');

  const platformKeys = new Set();
  const platformSpecs = Array.from(profile?.platform_specs || []);
  if (!platformSpecs.length) add('platform_specs', '至少填写一项平台规格');
  platformSpecs.forEach((item, index) => {
    const prefix = `platform_specs.${index}`;
    required(`${prefix}.platform`, item?.platform, '平台');
    required(`${prefix}.role`, item?.role, '用途');
    const key = `${text(item?.platform).toLocaleLowerCase('zh-CN')}::${text(item?.role).toLocaleLowerCase('zh-CN')}`;
    if (platformKeys.has(key)) add(`${prefix}.platform`, '平台和用途组合不能重复');
    platformKeys.add(key);
    for (const field of ['pixel_width', 'pixel_height']) {
      const value = Number(item?.[field]);
      if (!Number.isInteger(value) || value < 1 || value > 32768) add(`${prefix}.${field}`, '像素必须是 1–32768 的整数');
    }
    const safeArea = Number(item?.safe_area_percent);
    if (!Number.isFinite(safeArea) || safeArea < 0 || safeArea > 45) add(`${prefix}.safe_area_percent`, '安全区必须在 0–45% 之间');
  });

  if (!Object.hasOwn(SELECTION_MODES, profile?.selection_mode)) add('selection_mode', '请选择可见选物模式');
  const references = Array.from(profile?.approved_reference_ids || []);
  if (!references.length) add('approved_reference_ids', '至少选择一张批准参考图');
  if (references.length !== new Set(references).size) add('approved_reference_ids', '批准参考图不能重复');
  return errors;
}

export function createProductProfileController({
  api,
  state,
  query,
  modeIds,
  assetUrl,
  hydrateAssetUrls,
  escapeHtml,
  toast,
  onSelectionChange,
  createRequestId,
  formatApiError,
}) {
  let bound = false;
  let busy = false;
  let returnFocus = null;
  let editingRecord = null;
  let draft = null;
  let history = [];
  let historyReadOnly = false;
  let referenceAssets = [];
  let modalError = '';
  let conflictCurrent = null;

  const profiles = () => Array.from(state.productProfiles || []);
  const selection = (mode = state.currentMode) => state.modeProductProfileSelections[mode] || null;
  const profileById = (id) => profiles().find((item) => String(item?.id) === String(id));
  const modalOpen = () => !query('#product-profile-modal')?.hidden;
  const latestRevision = (id) => Number(profileById(id)?.current_revision || 0);
  const activeMaterial = (componentId) => draft?.materials?.find((item) => item.component_id === componentId) || null;

  function setSelection(mode, value, notify = true) {
    state.modeProductProfileSelections[mode] = value?.id
      ? { id: String(value.id), revision: Number(value.revision) }
      : null;
    const current = state.modeProductProfileSelections[mode];
    const latest = current ? latestRevision(current.id) : 0;
    if (current && latest && latest !== current.revision) state.productProfileConflicts.add(mode);
    else state.productProfileConflicts.delete(mode);
    if (notify) onSelectionChange?.(mode);
    if (mode === state.currentMode) renderPicker();
  }

  function restore(mode, uiState) {
    setSelection(mode, productProfileSelectionFromUiState(uiState), false);
    if (mode === state.currentMode) renderPicker();
  }

  function captureUiState(uiState, mode = state.currentMode) {
    return uiStateWithProductProfileSelection(uiState, selection(mode));
  }

  function selectionForSubmission(mode = state.currentMode) {
    const value = selection(mode);
    if (!value) return { productProfileId: null, expectedProductProfileRevision: null };
    return {
      productProfileId: value.id,
      expectedProductProfileRevision: value.revision,
    };
  }

  function hasConflict(mode = state.currentMode) {
    return state.productProfileConflicts.has(mode);
  }

  function renderPicker() {
    const select = query('#product-profile-select');
    if (!select) return;
    const current = selection();
    const options = ['<option value="">不绑定商品档案</option>'];
    profiles().forEach((item) => {
      const label = `${item.profile?.name || item.sku} · ${item.sku} · v${item.current_revision}`;
      options.push(`<option value="${escapeHtml(item.id)}">${escapeHtml(label)}</option>`);
    });
    if (current?.id && !profileById(current.id)) {
      options.push(`<option value="${escapeHtml(current.id)}">已绑定档案暂不可读取 · v${current.revision}</option>`);
    }
    select.innerHTML = options.join('');
    select.value = current?.id || '';
    select.disabled = !state.productProfilesAvailable;
    const status = query('#product-profile-picker-status');
    const latestButton = query('#btn-product-profile-latest');
    const selectedProfile = current ? profileById(current.id) : null;
    if (!state.productProfilesAvailable) {
      status.textContent = '商品档案暂不可用，任务不会静默绑定';
      status.dataset.tone = 'error';
      latestButton.hidden = true;
    } else if (!current) {
      status.textContent = profiles().length ? '本次任务不绑定 SKU 事实' : '尚未建立商品档案';
      status.dataset.tone = 'idle';
      latestButton.hidden = true;
    } else if (hasConflict()) {
      status.textContent = `当前现场锁定 v${current.revision}，最新为 v${selectedProfile?.current_revision || '?'}；确认后才能提交`;
      status.dataset.tone = 'warning';
      latestButton.hidden = false;
    } else {
      status.textContent = `${selectedProfile?.profile?.selection_mode ? SELECTION_MODES[selectedProfile.profile.selection_mode] : '商品事实已绑定'} · v${current.revision}`;
      status.dataset.tone = 'ready';
      latestButton.hidden = true;
    }
  }

  function reconcileSelections() {
    for (const mode of modeIds) {
      const current = selection(mode);
      const latest = current ? latestRevision(current.id) : 0;
      if (current && latest && latest !== current.revision) state.productProfileConflicts.add(mode);
      else state.productProfileConflicts.delete(mode);
    }
  }

  async function load({ silent = false } = {}) {
    try {
      const response = await api.getProductProfiles(200, { timeoutMs: 12000 });
      state.productProfiles = Array.isArray(response?.profiles) ? response.profiles : [];
      state.productProfilesAvailable = true;
      reconcileSelections();
      renderPicker();
      if (modalOpen()) renderList();
      return true;
    } catch (error) {
      state.productProfilesAvailable = false;
      renderPicker();
      if (!silent) toast(`商品档案读取失败：${formatApiError(error, '本地档案接口不可用')}`, 'error', 5200);
      return false;
    }
  }

  async function loadReferenceAssets() {
    try {
      const workspace = await api.getWorkspace('single', { timeoutMs: 12000 });
      referenceAssets = Array.isArray(workspace?.assets) ? workspace.assets : [];
      await hydrateAssetUrls(referenceAssets);
    } catch (_) {
      referenceAssets = Array.from(state.assetsByCollection?.product || []);
    }
  }

  function renderList() {
    const host = query('#product-profile-list');
    if (!host) return;
    if (!profiles().length) {
      host.innerHTML = '<div class="product-profile-empty"><strong>还没有商品档案</strong><p>建立后可在不同任务中复用同一套 SKU、材质和品牌保护。</p></div>';
      return;
    }
    host.innerHTML = profiles().map((item) => {
      const active = editingRecord?.id === item.id && !historyReadOnly;
      return `<button class="product-profile-list-item${active ? ' is-active' : ''}" type="button" data-profile-open="${escapeHtml(item.id)}" aria-pressed="${active}">
        <span><strong>${escapeHtml(item.profile?.name || item.sku)}</strong><small>${escapeHtml(item.sku)}</small></span>
        <b>v${Number(item.current_revision || 0)}</b>
      </button>`;
    }).join('');
  }

  function renderHistory() {
    const host = query('#product-profile-history');
    if (!host) return;
    if (!editingRecord || !history.length) {
      host.innerHTML = '<p>保存后会在这里保留不可变版本。</p>';
      return;
    }
    host.innerHTML = history.map((item) => {
      const current = Number(item.revision) === Number(editingRecord.current_revision);
      return `<button type="button" data-profile-version="${escapeHtml(item.id)}"${historyReadOnly && Number(draft?.revision) === Number(item.revision) ? ' class="is-active"' : ''}>
        <span><strong>版本 ${Number(item.revision)}</strong><small>${escapeHtml(new Date(item.created_at).toLocaleString('zh-CN', { hour12: false }))}</small></span>
        <b>${current ? '当前' : '查看'}</b>
      </button>`;
    }).join('');
  }

  function errorMap() {
    const mapped = new Map();
    validateProductProfile(draft || {}).forEach((item) => {
      if (!mapped.has(item.path)) mapped.set(item.path, item.message);
    });
    return mapped;
  }

  function fieldError(errors, path) {
    const message = errors.get(path);
    return `<small class="product-profile-field-error" id="${escapeHtml(validationErrorId(path))}" data-profile-error-for="${escapeHtml(path)}"${message ? '' : ' hidden'}>${escapeHtml(message || '')}</small>`;
  }

  function validationTarget(path) {
    const direct = query(`[data-profile-path="${CSS.escape(path)}"]`);
    if (direct) return direct;
    const arrayMatch = path.match(/^(.+)\.(\d+)\.([^.]+)$/);
    if (arrayMatch) {
      const [, array, index, field] = arrayMatch;
      if (array === 'materials') {
        const material = draft?.materials?.[Number(index)];
        if (material?.component_id) {
          const target = query(`[data-profile-material="${CSS.escape(material.component_id)}"][data-profile-field="${CSS.escape(field)}"]`);
          if (target) return target;
        }
      }
      const target = query(`[data-profile-array="${CSS.escape(array)}"][data-profile-index="${index}"][data-profile-field="${CSS.escape(field)}"]`);
      if (target) return target;
    }
    if (path === 'components') return query('#product-profile-components-section');
    if (path === 'platform_specs') return query('#product-profile-platforms-section');
    if (path === 'approved_reference_ids') return query('#product-profile-references-section');
    return null;
  }

  function applyValidationMetadata(errors) {
    for (const [path] of errors) {
      const target = validationTarget(path);
      if (!target) continue;
      if (!target.id) target.id = validationTargetId(path);
      target.setAttribute('aria-invalid', 'true');
      const errorId = validationErrorId(path);
      const describedBy = new Set(String(target.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
      describedBy.add(errorId);
      target.setAttribute('aria-describedby', [...describedBy].join(' '));
      if (!target.matches('input, select, textarea, button, [tabindex]')) target.tabIndex = -1;
    }
  }

  function componentOptions(selectedId) {
    return Array.from(draft?.components || []).map((item) => (
      `<option value="${escapeHtml(item.id)}"${item.id === selectedId ? ' selected' : ''}>${escapeHtml(item.name || '未命名组件')}</option>`
    )).join('');
  }

  function renderForm({ showErrors = false } = {}) {
    const host = query('#product-profile-form-body');
    if (!host || !draft) return;
    const errors = showErrors ? errorMap() : new Map();
    const readonly = historyReadOnly;
    const disabled = readonly ? ' disabled' : '';
    const revision = Number(draft.revision || 0);
    query('#product-profile-modal-title').textContent = readonly
      ? `${draft.name || '商品档案'} · 历史版本 ${revision}`
      : editingRecord ? `编辑 ${draft.name || editingRecord.sku}` : '建立商品档案';
    query('#product-profile-modal-subtitle').textContent = readonly
      ? '历史版本只读，当前任务和 trace 仍可精确追溯它。'
      : editingRecord ? `SKU ${editingRecord.sku} · 保存后生成不可变 v${revision + 1}` : '先填写稳定商品事实，再选择至少一张批准参考图。';
    query('#btn-product-profile-save').hidden = readonly;
    query('#btn-product-profile-return-current').hidden = !readonly;
    query('#btn-product-profile-reload').hidden = !conflictCurrent;

    const components = Array.from(draft.components || []);
    const attributes = Array.from(draft.specification?.attributes || []);
    const colors = Array.from(draft.brand_colors || []);
    const packagingTexts = Array.from(draft.packaging_texts || []);
    const logos = Array.from(draft.logos || []);
    const platforms = Array.from(draft.platform_specs || []);
    const references = new Set(draft.approved_reference_ids || []);
    const summaryItems = [...errors].map(([path, message]) => (
      `<li><a href="#${escapeHtml(validationTargetId(path))}" data-profile-error-link="${escapeHtml(validationTargetId(path))}">${escapeHtml(message)}</a></li>`
    )).join('');

    host.innerHTML = `
      <div class="product-profile-error-summary" id="product-profile-error-summary" role="alert" aria-labelledby="product-profile-error-summary-title" tabindex="-1"${modalError || errors.size ? '' : ' hidden'}>
        <strong id="product-profile-error-summary-title">${escapeHtml(modalError || `有 ${errors.size} 项需要检查`)}</strong>
        <p>${escapeHtml(modalError ? '当前内容没有被覆盖，请按提示处理后重试。' : '错误已标在对应字段旁，修改后可以继续保存。')}</p>
        ${summaryItems ? `<ul>${summaryItems}</ul>` : ''}
      </div>
      ${readonly ? '<div class="product-profile-history-banner"><strong>正在查看历史事实</strong><span>此版本不能修改，也不会替换当前版本。</span></div>' : ''}
      <fieldset class="product-profile-section">
        <legend><span>01</span><strong>基础信息</strong></legend>
        <div class="product-profile-grid product-profile-grid--three">
          <label for="product-profile-sku"><span>SKU</span><input id="product-profile-sku" data-profile-path="sku" value="${escapeHtml(draft.sku)}" maxlength="120"${disabled}${editingRecord ? ' readonly aria-readonly="true"' : ''} />${fieldError(errors, 'sku')}</label>
          <label for="product-profile-name"><span>商品名称</span><input id="product-profile-name" data-profile-path="name" value="${escapeHtml(draft.name)}" maxlength="160"${disabled} />${fieldError(errors, 'name')}</label>
          <label for="product-profile-category"><span>商品类目</span><input id="product-profile-category" data-profile-path="category" value="${escapeHtml(draft.category)}" maxlength="120"${disabled} />${fieldError(errors, 'category')}</label>
          <label for="product-profile-display"><span>规格展示</span><input id="product-profile-display" data-profile-path="specification.display" value="${escapeHtml(draft.specification?.display)}" maxlength="160" placeholder="例如：500 ml × 2 瓶"${disabled} />${fieldError(errors, 'specification.display')}</label>
          <label for="product-profile-net-content"><span>净含量</span><input id="product-profile-net-content" data-profile-path="specification.net_content" value="${escapeHtml(draft.specification?.net_content)}" maxlength="120" placeholder="例如：500 ml"${disabled} /></label>
          <label for="product-profile-unit-count"><span>件数</span><input id="product-profile-unit-count" data-profile-path="specification.unit_count" type="number" min="1" max="10000" value="${Number(draft.specification?.unit_count || 1)}"${disabled} />${fieldError(errors, 'specification.unit_count')}</label>
        </div>
        <div class="product-profile-repeater-head"><strong>补充规格</strong>${readonly ? '' : '<button type="button" data-profile-action="add-attribute">添加属性</button>'}</div>
        <div class="product-profile-rows">${attributes.length ? attributes.map((item, index) => `
          <div class="product-profile-row product-profile-row--attribute">
            <label><span>属性名</span><input data-profile-array="specification.attributes" data-profile-index="${index}" data-profile-field="key" value="${escapeHtml(item.key)}" maxlength="80"${disabled} />${fieldError(errors, `specification.attributes.${index}.key`)}</label>
            <label><span>属性值</span><input data-profile-array="specification.attributes" data-profile-index="${index}" data-profile-field="value" value="${escapeHtml(item.value)}" maxlength="160"${disabled} />${fieldError(errors, `specification.attributes.${index}.value`)}</label>
            ${readonly ? '' : `<button class="product-profile-remove" type="button" data-profile-action="remove-attribute" data-profile-index="${index}" aria-label="删除规格属性">删除</button>`}
          </div>`).join('') : '<p class="product-profile-empty-row">没有补充规格。</p>'}</div>
      </fieldset>

      <fieldset class="product-profile-section" id="product-profile-components-section">
        <legend><span>02</span><strong>组件与材质</strong></legend>
        <div class="product-profile-repeater-head"><p>每个实际组成部分都有自己的数量、保护策略和材质。</p>${readonly ? '' : '<button type="button" data-profile-action="add-component">添加组件</button>'}</div>
        <div class="product-profile-rows">${components.map((item, index) => {
          const material = activeMaterial(item.id) || { material: '', finish: '', transparent: false };
          return `<article class="product-component-row">
            <header><strong>组件 ${index + 1}</strong><small>${escapeHtml(item.id)}</small>${readonly || components.length === 1 ? '' : `<button class="product-profile-remove" type="button" data-profile-action="remove-component" data-profile-index="${index}">删除</button>`}</header>
            <div class="product-profile-grid product-profile-grid--four">
              <label><span>名称</span><input data-profile-array="components" data-profile-index="${index}" data-profile-field="name" value="${escapeHtml(item.name)}" maxlength="120"${disabled} />${fieldError(errors, `components.${index}.name`)}</label>
              <label><span>角色</span><select data-profile-array="components" data-profile-index="${index}" data-profile-field="role"${disabled}>${optionMarkup(COMPONENT_ROLES, item.role, escapeHtml)}</select></label>
              <label><span>保护策略</span><select data-profile-array="components" data-profile-index="${index}" data-profile-field="policy"${disabled}>${optionMarkup(COMPONENT_POLICIES, item.policy, escapeHtml)}</select></label>
              <label><span>数量</span><input type="number" min="1" max="10000" data-profile-array="components" data-profile-index="${index}" data-profile-field="quantity" value="${Number(item.quantity || 1)}"${disabled} />${fieldError(errors, `components.${index}.quantity`)}</label>
              <label><span>材质</span><input data-profile-material="${escapeHtml(item.id)}" data-profile-field="material" value="${escapeHtml(material.material)}" maxlength="120" placeholder="例如：PET、玻璃、陶瓷"${disabled} /></label>
              <label><span>表面质感</span><input data-profile-material="${escapeHtml(item.id)}" data-profile-field="finish" value="${escapeHtml(material.finish)}" maxlength="120" placeholder="例如：高光、哑光"${disabled} /></label>
              <label class="product-profile-check"><input type="checkbox" data-profile-material="${escapeHtml(item.id)}" data-profile-field="transparent"${material.transparent ? ' checked' : ''}${disabled} /><span>透明 / 半透明</span></label>
            </div>
          </article>`;
        }).join('')}</div>
        ${fieldError(errors, 'components')}
      </fieldset>

      <fieldset class="product-profile-section">
        <legend><span>03</span><strong>品牌保护</strong></legend>
        <div class="product-profile-repeater-head"><strong>品牌色</strong>${readonly ? '' : '<button type="button" data-profile-action="add-color">添加颜色</button>'}</div>
        <div class="product-profile-rows product-profile-rows--compact">${colors.length ? colors.map((item, index) => `
          <div class="product-profile-row product-profile-row--color">
            <label><span>名称</span><input data-profile-array="brand_colors" data-profile-index="${index}" data-profile-field="name" value="${escapeHtml(item.name)}" maxlength="80"${disabled} />${fieldError(errors, `brand_colors.${index}.name`)}</label>
            <label class="product-profile-color"><span>色值</span><input type="color" data-profile-array="brand_colors" data-profile-index="${index}" data-profile-field="value" value="${HEX_COLOR_PATTERN.test(item.value) ? item.value : '#ff6b43'}"${disabled} /><output>${escapeHtml(item.value)}</output></label>
            ${readonly ? '' : `<button class="product-profile-remove" type="button" data-profile-action="remove-color" data-profile-index="${index}">删除</button>`}
          </div>`).join('') : '<p class="product-profile-empty-row">没有锁定品牌色。</p>'}</div>

        <div class="product-profile-repeater-head"><strong>包装文字</strong>${readonly ? '' : '<button type="button" data-profile-action="add-text">添加文字</button>'}</div>
        <div class="product-profile-rows product-profile-rows--compact">${packagingTexts.length ? packagingTexts.map((item, index) => `
          <div class="product-profile-row product-profile-row--annotation">
            <label><span>所属组件</span><select data-profile-array="packaging_texts" data-profile-index="${index}" data-profile-field="component_id"${disabled}>${componentOptions(item.component_id)}</select></label>
            <label><span>必须保护的文字</span><input data-profile-array="packaging_texts" data-profile-index="${index}" data-profile-field="content" value="${escapeHtml(item.content)}" maxlength="500"${disabled} /></label>
            <label><span>保护方式</span><select data-profile-array="packaging_texts" data-profile-index="${index}" data-profile-field="policy"${disabled}>${optionMarkup({ exact_preserve: '严格不变', readable_preserve: '保持可读', allow_modify: '允许修改' }, item.policy, escapeHtml)}</select></label>
            ${readonly ? '' : `<button class="product-profile-remove" type="button" data-profile-action="remove-text" data-profile-index="${index}">删除</button>`}
          </div>`).join('') : '<p class="product-profile-empty-row">没有登记包装文字。</p>'}</div>

        <div class="product-profile-repeater-head"><strong>Logo</strong>${readonly ? '' : '<button type="button" data-profile-action="add-logo">添加 Logo</button>'}</div>
        <div class="product-profile-rows product-profile-rows--compact">${logos.length ? logos.map((item, index) => `
          <div class="product-profile-row product-profile-row--annotation">
            <label><span>所属组件</span><select data-profile-array="logos" data-profile-index="${index}" data-profile-field="component_id"${disabled}>${componentOptions(item.component_id)}</select></label>
            <label><span>Logo 名称</span><input data-profile-array="logos" data-profile-index="${index}" data-profile-field="name" value="${escapeHtml(item.name)}" maxlength="120"${disabled} /></label>
            <label><span>保护方式</span><select data-profile-array="logos" data-profile-index="${index}" data-profile-field="policy"${disabled}>${optionMarkup({ exact_preserve: '严格不变', allow_reposition: '允许移动', allow_modify: '允许修改' }, item.policy, escapeHtml)}</select></label>
            ${readonly ? '' : `<button class="product-profile-remove" type="button" data-profile-action="remove-logo" data-profile-index="${index}">删除</button>`}
          </div>`).join('') : '<p class="product-profile-empty-row">没有登记 Logo。</p>'}</div>
      </fieldset>

      <fieldset class="product-profile-section" id="product-profile-platforms-section">
        <legend><span>04</span><strong>平台规格与选物</strong></legend>
        <label class="product-profile-selection-mode" for="product-profile-selection-mode"><span>默认选物范围</span><select id="product-profile-selection-mode" data-profile-path="selection_mode"${disabled}>${optionMarkup(SELECTION_MODES, draft.selection_mode, escapeHtml)}</select>${fieldError(errors, 'selection_mode')}</label>
        <div class="product-profile-repeater-head"><strong>平台输出规格</strong>${readonly ? '' : '<button type="button" data-profile-action="add-platform">添加规格</button>'}</div>
        <div class="product-profile-rows">${platforms.map((item, index) => `
          <div class="product-profile-row product-profile-row--platform">
            <label><span>平台</span><input data-profile-array="platform_specs" data-profile-index="${index}" data-profile-field="platform" value="${escapeHtml(item.platform)}" maxlength="80"${disabled} /></label>
            <label><span>用途</span><input data-profile-array="platform_specs" data-profile-index="${index}" data-profile-field="role" value="${escapeHtml(item.role)}" maxlength="80"${disabled} /></label>
            <label><span>宽</span><input type="number" min="1" max="32768" data-profile-array="platform_specs" data-profile-index="${index}" data-profile-field="pixel_width" value="${Number(item.pixel_width || 1)}"${disabled} /></label>
            <label><span>高</span><input type="number" min="1" max="32768" data-profile-array="platform_specs" data-profile-index="${index}" data-profile-field="pixel_height" value="${Number(item.pixel_height || 1)}"${disabled} /></label>
            <label><span>格式</span><select data-profile-array="platform_specs" data-profile-index="${index}" data-profile-field="format"${disabled}>${optionMarkup({ jpeg: 'JPEG', png: 'PNG', webp: 'WebP' }, item.format, escapeHtml)}</select></label>
            <label><span>安全区 %</span><input type="number" min="0" max="45" step="0.5" data-profile-array="platform_specs" data-profile-index="${index}" data-profile-field="safe_area_percent" value="${Number(item.safe_area_percent || 0)}"${disabled} /></label>
            ${readonly || platforms.length === 1 ? '' : `<button class="product-profile-remove" type="button" data-profile-action="remove-platform" data-profile-index="${index}">删除</button>`}
          </div>`).join('')}</div>
        ${fieldError(errors, 'platform_specs')}
      </fieldset>

      <fieldset class="product-profile-section" id="product-profile-references-section">
        <legend><span>05</span><strong>批准参考图</strong></legend>
        <p class="product-profile-reference-note">只显示产品素材域中的原始素材；至少选择一张经过确认的实物参考图。</p>
        <div class="product-profile-reference-grid">${referenceAssets.length ? referenceAssets.map((asset) => `
          <label class="product-profile-reference-card${references.has(asset.id) ? ' is-selected' : ''}">
            <input type="checkbox" data-profile-reference="${escapeHtml(asset.id)}"${references.has(asset.id) ? ' checked' : ''}${disabled} />
            <img src="${escapeHtml(assetUrl(asset))}" alt="" loading="lazy" decoding="async" />
            <span><strong>${escapeHtml(asset.name || '产品素材')}</strong><small>${asset.width && asset.height ? `${asset.width}×${asset.height}` : '原始素材'}</small></span>
          </label>`).join('') : '<div class="product-profile-empty product-profile-empty--wide"><strong>产品素材域还没有参考图</strong><p>先关闭窗口，在“单产品”或“多文件”工作流导入实物图，再回来建立档案。</p></div>'}</div>
        ${fieldError(errors, 'approved_reference_ids')}
      </fieldset>`;
    applyValidationMetadata(errors);
  }

  function setPath(path, value) {
    if (!draft) return;
    const parts = path.split('.');
    let target = draft;
    while (parts.length > 1) target = target[parts.shift()];
    const field = parts[0];
    target[field] = field === 'unit_count' ? Math.round(numberValue(value, 1)) : value;
  }

  function updateArrayInput(input) {
    const field = input.dataset.profileArray;
    const index = Number(input.dataset.profileIndex);
    const key = input.dataset.profileField;
    const row = field.split('.').reduce((value, part) => value?.[part], draft)?.[index];
    if (!row || !key) return;
    const numeric = ['quantity', 'pixel_width', 'pixel_height', 'safe_area_percent'].includes(key);
    row[key] = input.type === 'checkbox' ? input.checked : numeric ? numberValue(input.value) : input.value;
    if (field === 'brand_colors' && key === 'value') input.closest('label')?.querySelector('output')?.replaceChildren(input.value);
  }

  function updateMaterialInput(input) {
    const componentId = input.dataset.profileMaterial;
    let material = activeMaterial(componentId);
    if (!material) {
      material = { component_id: componentId, material: '', finish: '', transparent: false };
      draft.materials.push(material);
    }
    const field = input.dataset.profileField;
    material[field] = input.type === 'checkbox' ? input.checked : input.value;
    if (!text(material.material) && !text(material.finish) && !material.transparent) {
      draft.materials = draft.materials.filter((item) => item !== material);
    }
  }

  function removeComponent(index) {
    const [component] = draft.components.splice(index, 1);
    if (!component) return;
    draft.materials = draft.materials.filter((item) => item.component_id !== component.id);
    draft.packaging_texts = draft.packaging_texts.filter((item) => item.component_id !== component.id);
    draft.logos = draft.logos.filter((item) => item.component_id !== component.id);
  }

  function performAction(action, index) {
    const firstComponent = draft.components[0]?.id || '';
    if (action === 'add-attribute') draft.specification.attributes.push({ key: '', value: '' });
    if (action === 'remove-attribute') draft.specification.attributes.splice(index, 1);
    if (action === 'add-component') draft.components.push({ id: localId('component'), name: '', role: 'accessory', policy: 'optional_preserve', quantity: 1 });
    if (action === 'remove-component') removeComponent(index);
    if (action === 'add-color') draft.brand_colors.push({ name: '', value: '#ff6b43' });
    if (action === 'remove-color') draft.brand_colors.splice(index, 1);
    if (action === 'add-text') draft.packaging_texts.push({ id: localId('text'), component_id: firstComponent, content: '', policy: 'exact_preserve' });
    if (action === 'remove-text') draft.packaging_texts.splice(index, 1);
    if (action === 'add-logo') draft.logos.push({ id: localId('logo'), component_id: firstComponent, name: '', policy: 'exact_preserve' });
    if (action === 'remove-logo') draft.logos.splice(index, 1);
    if (action === 'add-platform') draft.platform_specs.push({ platform: '', role: '', pixel_width: 2048, pixel_height: 2048, format: 'png', safe_area_percent: 5 });
    if (action === 'remove-platform') draft.platform_specs.splice(index, 1);
    modalError = '';
    renderForm();
  }

  async function loadHistory(profileId) {
    try {
      const response = await api.getProductProfileVersions(profileId, 100, { timeoutMs: 12000 });
      history = Array.isArray(response?.versions) ? response.versions : [];
    } catch (_) {
      history = [];
    }
    renderHistory();
  }

  async function openProfile(profileId) {
    if (busy) return;
    busy = true;
    modalError = '';
    conflictCurrent = null;
    try {
      editingRecord = await api.getProductProfile(profileId, { timeoutMs: 12000 });
      draft = cloneJson(editingRecord.profile);
      historyReadOnly = false;
      await loadHistory(profileId);
      renderList();
      renderForm();
    } catch (error) {
      modalError = formatApiError(error, '商品档案读取失败');
      toast(modalError, 'error', 5200);
    } finally {
      busy = false;
    }
  }

  function newProfile() {
    editingRecord = null;
    draft = createEmptyProductProfile();
    history = [];
    historyReadOnly = false;
    modalError = '';
    conflictCurrent = null;
    renderList();
    renderHistory();
    renderForm();
    query('#product-profile-sku')?.focus();
  }

  async function viewVersion(versionId) {
    if (busy) return;
    busy = true;
    try {
      const version = await api.getProductProfileVersion(versionId, { timeoutMs: 12000 });
      draft = cloneJson(version.profile);
      historyReadOnly = true;
      modalError = '';
      renderHistory();
      renderForm();
    } catch (error) {
      toast(`历史版本读取失败：${formatApiError(error)}`, 'error', 5200);
    } finally {
      busy = false;
    }
  }

  async function returnCurrent() {
    if (editingRecord?.id) await openProfile(editingRecord.id);
  }

  async function save() {
    if (busy || !draft || historyReadOnly) return false;
    modalError = '';
    const errors = validateProductProfile(draft);
    if (errors.length) {
      renderForm({ showErrors: true });
      query('#product-profile-error-summary')?.focus();
      return false;
    }
    busy = true;
    const button = query('#btn-product-profile-save');
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    const expectedRevision = Number(draft.revision || 0);
    draft.updated_at = new Date().toISOString();
    try {
      const saved = await api.saveProductProfile(draft.id, {
        expected_revision: expectedRevision,
        client_request_id: createRequestId(),
        profile: draft,
      }, { timeoutMs: 15000 });
      await load({ silent: true });
      editingRecord = saved;
      draft = cloneJson(saved.profile);
      historyReadOnly = false;
      setSelection(state.currentMode, { id: saved.id, revision: saved.current_revision });
      await loadHistory(saved.id);
      renderList();
      renderForm();
      toast(`商品档案已保存为 v${saved.current_revision}，当前任务已明确绑定`, 'success', 3600);
      return true;
    } catch (error) {
      if (error?.detail?.code === 'PRODUCT_PROFILE_REVISION_CONFLICT') {
        conflictCurrent = error.detail.current || null;
        modalError = `档案已在其他位置更新到 v${Number(conflictCurrent?.revision || 0)}，当前编辑内容尚未覆盖`;
        await load({ silent: true });
      } else {
        modalError = formatApiError(error, '商品档案保存失败');
      }
      renderForm({ showErrors: false });
      query('#product-profile-error-summary')?.focus();
      return false;
    } finally {
      busy = false;
      button.disabled = false;
      button.setAttribute('aria-busy', 'false');
    }
  }

  async function reloadConflictCurrent() {
    if (!conflictCurrent?.id && !editingRecord?.id) return;
    await openProfile(conflictCurrent?.id || editingRecord.id);
  }

  async function open() {
    returnFocus = document.activeElement;
    query('#product-profile-modal').hidden = false;
    query('#product-profile-modal-close').focus();
    await Promise.all([load({ silent: true }), loadReferenceAssets()]);
    const current = selection();
    if (current?.id && profileById(current.id)) await openProfile(current.id);
    else if (profiles().length) await openProfile(profiles()[0].id);
    else newProfile();
  }

  function close() {
    query('#product-profile-modal').hidden = true;
    modalError = '';
    conflictCurrent = null;
    if (returnFocus instanceof HTMLElement) returnFocus.focus();
    returnFocus = null;
  }

  function adoptLatest() {
    const current = selection();
    const latest = current ? latestRevision(current.id) : 0;
    if (!current || !latest) return;
    setSelection(state.currentMode, { id: current.id, revision: latest });
    toast(`当前工作流已明确采用商品档案 v${latest}`, 'success');
  }

  async function handleSubmissionConflict(error, mode = state.currentMode) {
    if (error?.detail?.code !== 'PRODUCT_PROFILE_REVISION_CONFLICT') return false;
    await load({ silent: true });
    state.productProfileConflicts.add(mode);
    if (mode === state.currentMode) renderPicker();
    toast('商品档案已有新版本；请确认采用最新版后再提交任务', 'error', 6500);
    if (mode === state.currentMode) query('#btn-product-profile-latest')?.focus();
    return true;
  }

  function bind() {
    if (bound) return;
    bound = true;
    query('#product-profile-select').addEventListener('change', (event) => {
      const id = event.target.value;
      const profile = profileById(id);
      setSelection(state.currentMode, profile ? { id, revision: profile.current_revision } : null);
    });
    query('#btn-product-profile-manage').addEventListener('click', open);
    query('#btn-product-profile-latest').addEventListener('click', adoptLatest);
    query('#product-profile-modal-close').addEventListener('click', close);
    query('#product-profile-modal-backdrop').addEventListener('click', close);
    query('#btn-product-profile-cancel').addEventListener('click', close);
    query('#btn-product-profile-new').addEventListener('click', newProfile);
    query('#btn-product-profile-return-current').addEventListener('click', returnCurrent);
    query('#btn-product-profile-reload').addEventListener('click', reloadConflictCurrent);
    query('#btn-product-profile-save').addEventListener('click', save);
    query('#product-profile-list').addEventListener('click', (event) => {
      const button = event.target.closest('[data-profile-open]');
      if (button) openProfile(button.dataset.profileOpen);
    });
    query('#product-profile-history').addEventListener('click', (event) => {
      const button = event.target.closest('[data-profile-version]');
      if (button) viewVersion(button.dataset.profileVersion);
    });
    const form = query('#product-profile-form-body');
    const update = (event) => {
      const input = event.target;
      if (historyReadOnly || !draft) return;
      if (input.dataset.profilePath) {
        const field = input.dataset.profilePath.split('.').at(-1);
        const numeric = ['unit_count'].includes(field);
        setPath(input.dataset.profilePath, input.type === 'checkbox' ? input.checked : numeric ? numberValue(input.value) : input.value);
      }
      if (input.dataset.profileArray) updateArrayInput(input);
      if (input.dataset.profileMaterial) updateMaterialInput(input);
      if (input.dataset.profileReference) {
        const selected = new Set(draft.approved_reference_ids || []);
        if (input.checked) selected.add(input.dataset.profileReference);
        else selected.delete(input.dataset.profileReference);
        draft.approved_reference_ids = [...selected];
        input.closest('.product-profile-reference-card')?.classList.toggle('is-selected', input.checked);
      }
      modalError = '';
    };
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    form.addEventListener('click', (event) => {
      const errorLink = event.target.closest('[data-profile-error-link]');
      if (errorLink) {
        event.preventDefault();
        const target = query(`#${CSS.escape(errorLink.dataset.profileErrorLink)}`);
        target?.scrollIntoView({ block: 'center' });
        target?.focus({ preventScroll: true });
        return;
      }
      const button = event.target.closest('[data-profile-action]');
      if (!button || historyReadOnly) return;
      performAction(button.dataset.profileAction, Number(button.dataset.profileIndex));
    });
  }

  return {
    bind,
    load,
    open,
    close,
    restore,
    captureUiState,
    selectionForSubmission,
    hasConflict,
    handleSubmissionConflict,
    renderPicker,
  };
}
