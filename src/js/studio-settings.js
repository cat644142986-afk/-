export function normalizeSettingsPayload(values = {}, includeKey = false) {
  const payload = {
    default_model: values.defaultModel || 'gpt-image-2',
    default_platter: values.defaultPlatter || 'auto',
    default_angle: values.defaultAngle || 'auto',
    default_fidelity: Number(values.defaultFidelity ?? 40),
    knowledge_base_path: String(values.knowledgeBasePath || '').trim(),
  };
  const apiKey = String(values.apiKey || '').trim();
  if (includeKey && apiKey) payload.api_key = apiKey;
  return payload;
}

export function knowledgeStatusCopy(status) {
  if (!status?.available) {
    return {
      pill: '知识库未连接',
      title: '未找到知识库',
      detail: status?.design_path || '—',
    };
  }
  return {
    pill: `${Number(status.document_count || 0)} 份文档 · ${Number(status.rule_count || 0)} 条规则`,
    title: '只读连接正常',
    detail: `${Number(status.document_count || 0)} 份文档 · ${Number(status.rule_count || 0)} 条规则`,
  };
}

export function outputRootStatusCopy(status) {
  if (!status?.available) {
    return {
      text: status?.message || '交付目录当前不可用，请重新选择',
      error: true,
    };
  }
  return {
    text: status?.message || '新任务将保存到这里；运行中任务保持原目录',
    error: false,
  };
}

export function groundingPackStatusCopy(status) {
  if (status?.available && status?.verified) {
    return {
      title: '完整验证通过',
      detail: status.message || '本地智能选物扩展可以使用',
      tone: 'ready',
    };
  }
  if (status?.available) {
    return {
      title: '扩展已就绪',
      detail: '首次使用前建议执行一次完整验证',
      tone: 'ready',
    };
  }
  const notConfigured = ['RUNTIME_NOT_CONFIGURED', 'MODEL_NOT_CONFIGURED'].includes(status?.code);
  return {
    title: notConfigured ? '未启用（当前使用手动框选）' : '扩展不可用',
    detail: status?.message || '分别选择运行时和模型包后再验证',
    tone: notConfigured ? 'idle' : 'error',
  };
}

export function createSettingsController({
  api,
  state,
  query,
  toast,
  updateQuickControls,
  compileKnowledgePreview,
}) {
  let bound = false;

  function renderKnowledgeStatus(status) {
    if (!status) return;
    const copy = knowledgeStatusCopy(status);
    state.knowledgeStatus = status;
    query('#knowledge-pill-text').textContent = copy.pill;
    query('#setting-knowledge-status').textContent = copy.title;
    query('#setting-knowledge-detail').textContent = copy.detail;
  }

  function renderOutputRoot(settings) {
    const path = String(settings?.output_root || settings?.output_dir || '').trim();
    const status = outputRootStatusCopy(settings?.output_root_status);
    query('#setting-output-root').value = path;
    const statusNode = query('#output-root-status');
    statusNode.textContent = status.text;
    statusNode.classList.toggle('is-error', status.error);
    query('#btn-open-output-root').disabled = !path || status.error;
  }

  function renderGroundingPack(settings) {
    const runtimeRoot = String(settings?.grounding_runtime_root || '').trim();
    const modelRoot = String(settings?.grounding_model_root || '').trim();
    const copy = groundingPackStatusCopy(settings?.grounding_pack);
    query('#setting-grounding-runtime-root').value = runtimeRoot;
    query('#setting-grounding-model-root').value = modelRoot;
    const statusNode = query('#grounding-pack-status');
    statusNode.classList.toggle('is-ready', copy.tone === 'ready');
    statusNode.classList.toggle('is-error', copy.tone === 'error');
    query('#grounding-pack-title').textContent = copy.title;
    query('#grounding-pack-detail').textContent = copy.detail;
    query('#btn-verify-grounding-pack').disabled = !runtimeRoot || !modelRoot;
    query('#btn-disable-grounding-pack').disabled = !runtimeRoot && !modelRoot;
  }

  async function load({ silent = false } = {}) {
    try {
      const settings = await api.getSettings();
      state.settings = settings;
      const hasModeSnapshot = state.restoredModes.has(state.currentMode);
      if (settings.default_model) {
        query('#setting-model').value = settings.default_model;
        if (!hasModeSnapshot) query('#param-model').value = settings.default_model;
      }
      if (settings.default_platter) {
        if (!hasModeSnapshot) {
          const radio = query(`input[name="platter"][value="${settings.default_platter}"]`);
          if (radio) radio.checked = true;
        }
        query('#setting-platter').value = settings.default_platter;
      }
      if (settings.default_angle) {
        query('#setting-angle').value = settings.default_angle;
        if (!hasModeSnapshot) query('#param-angle').value = settings.default_angle;
      }
      if (settings.default_fidelity) {
        query('#setting-fidelity').value = settings.default_fidelity;
        if (!hasModeSnapshot) query('#param-fidelity').value = settings.default_fidelity;
      }
      query('#setting-fid-val').textContent = `${query('#setting-fidelity').value}%`;
      renderOutputRoot(settings);
      renderGroundingPack(settings);
      query('#setting-api-key').placeholder = settings.api_key_set ? '已配置（留空不修改）' : '输入 API Key';
      query('#setting-knowledge-path').value = settings.knowledge_base_path || '';
      renderKnowledgeStatus(settings.knowledge);
      updateQuickControls();
      return true;
    } catch (error) {
      if (!silent) toast(`读取设置失败：${error}`, 'error');
      return false;
    }
  }

  function readPayload(includeKey) {
    return normalizeSettingsPayload({
      defaultModel: query('#setting-model').value,
      defaultPlatter: query('#setting-platter').value,
      defaultAngle: query('#setting-angle').value,
      defaultFidelity: query('#setting-fidelity').value,
      knowledgeBasePath: query('#setting-knowledge-path').value,
      apiKey: query('#setting-api-key').value,
    }, includeKey);
  }

  async function save(includeKey = false) {
    try {
      const result = await api.saveSettings(readPayload(includeKey));
      state.settings = result;
      query('#setting-api-key').value = '';
      renderKnowledgeStatus(result.knowledge);
      renderOutputRoot(result);
      renderGroundingPack(result);
      toast('设置已保存', 'success');
    } catch (error) {
      toast(`保存失败：${error}`, 'error');
    }
  }

  async function reloadKnowledge() {
    try {
      const status = await api.reloadKnowledge(query('#setting-knowledge-path').value.trim());
      renderKnowledgeStatus(status);
      toast('知识库已重新编译', 'success');
      await compileKnowledgePreview();
    } catch (error) {
      toast(`知识编译失败：${error}`, 'error');
    }
  }

  async function checkBalance() {
    query('#balance-display').textContent = '正在查询…';
    try {
      const result = await api.checkBalance();
      query('#balance-display').textContent = result.error ? result.error : `当前余额：${result.balance}`;
    } catch (error) {
      query('#balance-display').textContent = `查询失败：${error}`;
    }
  }

  async function selectOutputRoot() {
    const button = query('#btn-select-output-root');
    button.disabled = true;
    try {
      const selected = await api.selectFolder();
      if (!selected) return;
      const result = await api.saveSettings({ output_root: selected });
      state.settings = result;
      renderOutputRoot(result);
      toast('交付目录已更新，仅影响之后创建的任务', 'success');
    } catch (error) {
      toast(`目录设置失败：${error}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async function openOutputRoot() {
    const path = query('#setting-output-root').value.trim();
    if (!path) return;
    try {
      await api.openInFolder(path);
    } catch (error) {
      toast(`无法打开交付目录：${error}`, 'error');
    }
  }

  async function selectGroundingRoot(kind) {
    const isRuntime = kind === 'runtime';
    const button = query(isRuntime ? '#btn-select-grounding-runtime' : '#btn-select-grounding-model');
    button.disabled = true;
    try {
      const selected = await api.selectFolder();
      if (!selected) return;
      const field = isRuntime ? 'grounding_runtime_root' : 'grounding_model_root';
      const result = await api.saveSettings({ [field]: selected });
      state.settings = result;
      renderGroundingPack(result);
      toast(isRuntime ? '本地识别运行时已选择' : '本地识别模型包已选择', 'success');
    } catch (error) {
      toast(`本地扩展设置失败：${error}`, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async function verifyGroundingPack() {
    const button = query('#btn-verify-grounding-pack');
    button.disabled = true;
    query('#grounding-pack-title').textContent = '正在完整验证…';
    query('#grounding-pack-detail').textContent = '正在校验全部文件并探测本地运行环境';
    try {
      const groundingPack = await api.verifyGroundingPack();
      state.settings = { ...state.settings, grounding_pack: groundingPack };
      renderGroundingPack(state.settings);
      toast(groundingPack.available ? '本地智能选物扩展验证通过' : groundingPack.message, groundingPack.available ? 'success' : 'error');
    } catch (error) {
      toast(`完整验证失败：${error}`, 'error');
      await load();
    } finally {
      button.disabled = !state.settings?.grounding_runtime_root || !state.settings?.grounding_model_root;
    }
  }

  async function disableGroundingPack() {
    const button = query('#btn-disable-grounding-pack');
    button.disabled = true;
    try {
      const result = await api.saveSettings({
        grounding_runtime_root: '',
        grounding_model_root: '',
      });
      state.settings = result;
      renderGroundingPack(result);
      toast('已关闭本地智能选物扩展；仍可继续手动框选', 'success');
    } catch (error) {
      toast(`关闭本地扩展失败：${error}`, 'error');
    } finally {
      button.disabled = !state.settings?.grounding_runtime_root && !state.settings?.grounding_model_root;
    }
  }

  function bind() {
    if (bound) return;
    query('#btn-save-key').addEventListener('click', () => save(true));
    query('#btn-save-settings').addEventListener('click', () => save(false));
    query('#btn-reload-knowledge').addEventListener('click', reloadKnowledge);
    query('#btn-check-balance').addEventListener('click', checkBalance);
    query('#btn-select-output-root').addEventListener('click', selectOutputRoot);
    query('#btn-open-output-root').addEventListener('click', openOutputRoot);
    query('#btn-select-grounding-runtime').addEventListener('click', () => selectGroundingRoot('runtime'));
    query('#btn-select-grounding-model').addEventListener('click', () => selectGroundingRoot('model'));
    query('#btn-verify-grounding-pack').addEventListener('click', verifyGroundingPack);
    query('#btn-disable-grounding-pack').addEventListener('click', disableGroundingPack);
    query('#setting-fidelity').addEventListener('input', () => {
      query('#setting-fid-val').textContent = `${query('#setting-fidelity').value}%`;
    });
    bound = true;
  }

  return {
    bind,
    checkBalance,
    load,
    reloadKnowledge,
    renderKnowledgeStatus,
    renderGroundingPack,
    renderOutputRoot,
    save,
  };
}
