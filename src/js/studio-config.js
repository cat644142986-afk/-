import { TASK_STATUS } from './task-status.js';

export const PAGE_CONFIG = {
  process: { eyebrow: 'PRODUCT ATELIER', title: '生产', subtitle: '批量与结构化任务' },
  canvas: { eyebrow: 'SPATIAL WORKSPACE', title: '画布', subtitle: '视觉创作现场' },
  compare: { eyebrow: 'QUALITY REVIEW', title: '评审', subtitle: '版本对比与设计判断' },
  history: { eyebrow: 'CREATION LEDGER', title: '历史', subtitle: '任务、结果与恢复' },
  memory: { eyebrow: 'DESIGN DNA', title: '成长', subtitle: '偏好与知识审核' },
  settings: { eyebrow: 'SYSTEM & KNOWLEDGE', title: '设置', subtitle: '连接、知识与交付' },
};

export const MODE_CONFIG = {
  single: {
    label: '单产品商业精修', badge: '单产品', action: '开始生成', multiple: false, maxFiles: 1,
    title: '导入或选择一张图片', eyebrow: 'PRODUCT ASSETS', limit: 'JPG · PNG · WEBP · 20 MB',
    description: '拖入、粘贴或选择图片。',
    note: '一张主图，保真生成与透明底同步输出', outputKind: 'ecommerce-main', collection: 'product',
  },
  'multi-file': {
    label: '多文件独立批量', badge: '多文件', action: '运行批量队列', multiple: true, maxFiles: 20,
    title: '选择要批量处理的图片', eyebrow: 'PRODUCT ASSETS', limit: '最多 20 张',
    description: '每张图片独立处理。',
    note: '多张源图逐一生成，不把它们误当成同一画面', outputKind: 'ecommerce-main', collection: 'product',
  },
  'group-split': {
    label: '组合图智能拆分', badge: '合照', action: '识别并拆分', multiple: false, maxFiles: 1,
    title: '导入或选择一张合照', eyebrow: 'GROUP ASSETS', limit: '选择 1 张',
    description: '识别主体后分别输出。',
    note: '一张合照识别多个主体，再分别生成交付图', outputKind: 'group-split', collection: 'group',
  },
  'cutout-batch': {
    label: '本地批量抠图', badge: '抠图', action: '开始批量抠图', multiple: true, maxFiles: 24,
    title: '选择要抠图的图片', eyebrow: 'CUTOUT ASSETS', limit: '最多 24 张',
    description: '支持批量去背景。',
    note: '快速去背景支持批量；智能选物先确认名称、数量与目标框', outputKind: 'cutout', collection: 'cutout',
  },
};

export const JOB_STATUS = TASK_STATUS;

export const MODE_IDS = Object.freeze(Object.keys(MODE_CONFIG));
export const STAGE_IDS = Object.freeze({
  empty: 'canvas-empty',
  ready: 'canvas-image',
  processing: 'canvas-processing',
  success: 'canvas-results',
  error: 'canvas-error',
});
