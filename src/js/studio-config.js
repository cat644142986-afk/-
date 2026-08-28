export const PAGE_CONFIG = {
  process: { eyebrow: 'PRODUCT ATELIER', title: '创作', subtitle: '素材与任务现场' },
  compare: { eyebrow: 'QUALITY REVIEW', title: '评审', subtitle: '版本对比与设计判断' },
  history: { eyebrow: 'CREATION LEDGER', title: '会话', subtitle: '恢复项目现场与历史结果' },
  memory: { eyebrow: 'DESIGN DNA', title: '成长', subtitle: '设计偏好与知识审核' },
  settings: { eyebrow: 'SYSTEM & KNOWLEDGE', title: '设置', subtitle: '模型、知识与交付' },
};

export const MODE_CONFIG = {
  single: {
    label: '单产品商业精修', badge: '单产品', action: '开始生成', multiple: false, maxFiles: 1,
    title: '导入素材，再选一张作为输入', eyebrow: 'PRODUCT ASSETS', limit: '选择 1 张 · 20 MB / 张',
    description: '与多文件共享产品素材；当前选择、描述和结果独立保存。',
    note: '一张主图，保真生成与透明底同步输出', outputKind: 'ecommerce-main', collection: 'product',
  },
  'multi-file': {
    label: '多文件独立批量', badge: '多文件', action: '运行批量队列', multiple: true, maxFiles: 20,
    title: '从产品素材选择一组独立商品', eyebrow: 'PRODUCT ASSETS', limit: '每次最多选择 20 张',
    description: '与单产品共享素材；每张图片独立并发并保留逐项进度。',
    note: '多张源图逐一生成，不把它们误当成同一画面', outputKind: 'ecommerce-main', collection: 'product',
  },
  'group-split': {
    label: '组合图智能拆分', badge: '合照', action: '识别并拆分', multiple: false, maxFiles: 1,
    title: '导入或选择一张产品合照', eyebrow: 'GROUP ASSETS', limit: '选择 1 张合照',
    description: '合照素材与产品、抠图素材分开保存，返回时恢复原任务现场。',
    note: '一张合照识别多个主体，再分别生成交付图', outputKind: 'group-split', collection: 'group',
  },
  'cutout-batch': {
    label: '本地批量抠图', badge: '抠图', action: '开始批量抠图', multiple: true, maxFiles: 24,
    title: '导入或选择待抠图图片', eyebrow: 'CUTOUT ASSETS', limit: '每次最多选择 24 张',
    description: '抠图素材、描述、进度和预览独立保留；切换工作流不会停止任务。',
    note: '快速去背景支持批量；智能选物先确认名称、数量与目标框', outputKind: 'cutout', collection: 'cutout',
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
