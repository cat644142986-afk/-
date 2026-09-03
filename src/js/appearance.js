const THEME_PREFERENCES = new Set(['light', 'dark', 'system']);
const COLORWAYS = new Set(['warm', 'mono']);
const TEXT_SCALES = new Set(['standard', 'large']);
const CONTRASTS = new Set(['standard', 'high']);
const MOTION_PREFERENCES = new Set(['system', 'reduced']);

export const DEFAULT_APPEARANCE = Object.freeze({
  themePreference: 'light',
  colorway: 'warm',
  textScale: 'standard',
  contrast: 'standard',
  motionPreference: 'system',
});

function allowed(value, choices, fallback) {
  const normalized = String(value || '').trim().toLowerCase();
  return choices.has(normalized) ? normalized : fallback;
}

export function normalizeAppearancePreferences(raw = {}) {
  const colorway = allowed(raw.colorway, COLORWAYS, DEFAULT_APPEARANCE.colorway);
  const requestedTheme = allowed(
    raw.themePreference ?? raw.theme,
    THEME_PREFERENCES,
    DEFAULT_APPEARANCE.themePreference,
  );
  return {
    themePreference: colorway === 'mono' ? 'light' : requestedTheme,
    colorway,
    textScale: allowed(raw.textScale, TEXT_SCALES, DEFAULT_APPEARANCE.textScale),
    contrast: allowed(raw.contrast, CONTRASTS, DEFAULT_APPEARANCE.contrast),
    motionPreference: allowed(
      raw.motionPreference ?? raw.motion,
      MOTION_PREFERENCES,
      DEFAULT_APPEARANCE.motionPreference,
    ),
  };
}

export function resolveAppearancePreferences(raw = {}, environment = {}) {
  const preferences = normalizeAppearancePreferences(raw);
  const theme = preferences.themePreference === 'system'
    ? (environment.systemDark ? 'dark' : 'light')
    : preferences.themePreference;
  const reducedMotion = preferences.motionPreference === 'reduced'
    || (preferences.motionPreference === 'system' && Boolean(environment.systemReducedMotion));
  return { ...preferences, theme, reducedMotion };
}

export function readAppearancePreferences(storage) {
  const get = (key) => {
    try { return storage?.getItem?.(key) || ''; } catch (_) { return ''; }
  };
  return normalizeAppearancePreferences({
    themePreference: get('pa-theme-preference') || get('pa-theme'),
    colorway: get('pa-colorway'),
    textScale: get('pa-text-scale'),
    contrast: get('pa-contrast'),
    motionPreference: get('pa-motion'),
  });
}

export function appearanceStatusCopy(resolved = {}) {
  const theme = resolved.themePreference === 'system'
    ? `跟随系统（当前${resolved.theme === 'dark' ? '深色' : '浅色'}）`
    : (resolved.theme === 'dark' ? '石墨深色' : '暖白浅色');
  const colorway = resolved.colorway === 'mono' ? '黑白工作台' : '暖色工作台';
  const scale = resolved.textScale === 'large' ? '舒适字号' : '标准字号';
  const contrast = resolved.contrast === 'high' ? '高对比' : '标准对比';
  const motion = resolved.reducedMotion ? '减少动效' : '跟随系统动效';
  return `${theme} · ${colorway} · ${scale} · ${contrast} · ${motion}`;
}

export function explicitThemeAfterToggle(resolvedTheme) {
  return resolvedTheme === 'dark' ? 'light' : 'dark';
}

export function appearanceSettingsHtml() {
  const choice = (name, value, title, detail) => `<label><input type="radio" name="${name}" value="${value}" /><span><strong>${title}</strong><small>${detail}</small></span></label>`;
  return `<section class="settings-card settings-card--appearance">
    <div class="settings-card__heading"><span>05</span><div><h2>外观与可访问性</h2><p>只改变界面呈现，不影响任务、图片或生成参数。</p></div></div>
    <div class="appearance-settings">
      <fieldset><legend>界面主题</legend>${choice('appearance-theme', 'system', '跟随系统', '随 Windows 深浅色自动切换')}${choice('appearance-theme', 'light', '暖白浅色', '当前默认工作台外观')}${choice('appearance-theme', 'dark', '石墨深色', '降低暗环境亮度刺激')}</fieldset>
      <fieldset><legend>界面配色</legend>${choice('appearance-colorway', 'warm', '暖色工作台', '珊瑚强调与暖灰画布')}${choice('appearance-colorway', 'mono', '经典黑白', '黑色左栏、明亮面板与少量状态色')}</fieldset>
      <fieldset><legend>文字大小</legend>${choice('appearance-text-scale', 'standard', '标准', '保持当前信息密度')}${choice('appearance-text-scale', 'large', '舒适', '正文与控件文字更易读')}</fieldset>
      <fieldset><legend>文字对比</legend>${choice('appearance-contrast', 'standard', '标准', '柔和层级与留白')}${choice('appearance-contrast', 'high', '高对比', '加强次级文字与边界')}</fieldset>
      <fieldset><legend>界面动效</legend>${choice('appearance-motion', 'system', '跟随系统', '尊重 Windows 减少动画设置')}${choice('appearance-motion', 'reduced', '减少动效', '关闭非必要动画与平滑滚动')}</fieldset>
    </div>
    <div class="appearance-footer"><p id="appearance-status" role="status" aria-live="polite">正在读取外观偏好</p><button class="secondary-button" id="btn-reset-appearance" type="button">恢复默认</button></div>
  </section>`;
}
