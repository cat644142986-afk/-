export const PAGE_CONFIG = {
  process: { eyebrow: 'PRODUCT ATELIER', title: '创作工作台', subtitle: '素材、任务与判断持续留在现场' },
  compare: { eyebrow: 'QUALITY REVIEW', title: '版本对比', subtitle: '检查轮廓、材质、颜色与构图偏差' },
  history: { eyebrow: 'CREATION LEDGER', title: '创作会话', subtitle: '每次生成都有来源、理由和版本' },
  memory: { eyebrow: 'DESIGN DNA', title: '成长中心', subtitle: '把反复出现的判断沉淀为可审核的偏好' },
  settings: { eyebrow: 'SYSTEM & KNOWLEDGE', title: '应用设置', subtitle: '管理模型、知识库与本地存储' },
};

export const MODE_CONFIG = {
  single: {
    label: '单产品商业精修', badge: 'SINGLE', action: '开始生成', multiple: false, maxFiles: 1,
    title: '导入素材，再选一张作为输入', eyebrow: 'PRODUCT ASSETS', limit: 'SELECT 1 · 20 MB / FILE',
    description: '与多文件共享产品素材；当前选择、描述和结果独立保存。',
    note: '一张主图，保真生成与透明底同步输出', outputKind: 'ecommerce-main', collection: 'product',
  },
  'multi-file': {
    label: '多文件独立批量', badge: 'BATCH', action: '运行批量队列', multiple: true, maxFiles: 12,
    title: '从产品素材选择一组独立商品', eyebrow: 'PRODUCT ASSETS', limit: 'SELECT UP TO 12',
    description: '与单产品共享素材；每张图片独立并发并保留逐项进度。',
    note: '多张源图逐一生成，不把它们误当成同一画面', outputKind: 'ecommerce-main', collection: 'product',
  },
  'group-split': {
    label: '组合图智能拆分', badge: 'GROUP SPLIT', action: '识别并拆分', multiple: false, maxFiles: 1,
    title: '导入或选择一张产品合照', eyebrow: 'GROUP ASSETS', limit: 'SELECT 1 GROUP IMAGE',
    description: '合照素材与产品、抠图素材分开保存，返回时恢复原任务现场。',
    note: '一张合照识别多个主体，再分别生成交付图', outputKind: 'group-split', collection: 'group',
  },
  'cutout-batch': {
    label: '本地批量抠图', badge: 'LOCAL CUTOUT', action: '开始批量抠图', multiple: true, maxFiles: 24,
    title: '导入或选择待抠图图片', eyebrow: 'CUTOUT ASSETS', limit: 'SELECT UP TO 24',
    description: '抠图素材、描述、进度和预览独立保留；切换工作流不会停止任务。',
    note: '本地快速去背景；语义选物将在智能抠图工作流接入', outputKind: 'cutout', collection: 'cutout',
  },
};

export const JOB_STATUS = {
  queued: { label: '排队中', tone: 'queued' },
  running: { label: '运行中', tone: 'running' },
  paused: { label: '已暂停', tone: 'paused' },
  completed: { label: '已完成', tone: 'completed' },
  partial: { label: '部分完成', tone: 'partial' },
  failed: { label: '失败', tone: 'failed' },
  canceling: { label: '正在取消', tone: 'canceling' },
  canceled: { label: '已取消', tone: 'canceled' },
  interrupted: { label: '已中断', tone: 'interrupted' },
};

export const MODE_IDS = Object.freeze(Object.keys(MODE_CONFIG));
export const STAGE_IDS = Object.freeze({
  empty: 'canvas-empty',
  ready: 'canvas-image',
  processing: 'canvas-processing',
  success: 'canvas-results',
  error: 'canvas-error',
});
