import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createEmptyProductProfile,
  productProfileSelectionFromUiState,
  uiStateWithProductProfileSelection,
  validateProductProfile,
} from '../../src/js/studio-product-profiles.js';

test('product profile selection survives draft ui state without replacing unrelated state', () => {
  const uiState = uiStateWithProductProfileSelection({
    folder_batch: { batch_id: 'folder-1' },
  }, {
    id: 'profile:sku-001',
    revision: 4,
  });

  assert.deepEqual(productProfileSelectionFromUiState(uiState), {
    id: 'profile:sku-001',
    revision: 4,
  });
  assert.equal(uiState.folder_batch.batch_id, 'folder-1');
  assert.deepEqual(
    uiStateWithProductProfileSelection(uiState, null),
    { folder_batch: { batch_id: 'folder-1' } },
  );
  assert.equal(productProfileSelectionFromUiState({
    product_profile_selection: {
      product_profile_id: 'profile:sku-001',
      expected_product_profile_revision: 0,
    },
  }), null);
});

test('new product profile becomes valid only after commercial facts and one approved reference are explicit', () => {
  const profile = createEmptyProductProfile('2026-09-02T00:00:00.000Z');
  assert.ok(validateProductProfile(profile).some((item) => item.path === 'approved_reference_ids'));

  profile.sku = 'SKU-001';
  profile.name = '透明瓶饮料';
  profile.category = '饮料';
  profile.specification.display = '500 ml × 1 瓶';
  profile.specification.net_content = '500 ml';
  profile.approved_reference_ids = ['asset:reference-001'];
  profile.materials = [{
    component_id: profile.components[0].id,
    material: 'PET',
    finish: '高光',
    transparent: true,
  }];
  profile.packaging_texts = [{
    id: 'text:brand-001',
    component_id: profile.components[0].id,
    content: 'BRAND',
    policy: 'exact_preserve',
  }];

  assert.deepEqual(validateProductProfile(profile), []);
});

test('product profile validation blocks dangling protection facts and unsafe platform specs', () => {
  const profile = createEmptyProductProfile('2026-09-02T00:00:00.000Z');
  profile.sku = 'SKU-002';
  profile.name = '包装商品';
  profile.category = '食品';
  profile.specification.display = '2 包';
  profile.approved_reference_ids = ['asset:reference-002'];
  profile.brand_colors = [{ name: '品牌红', value: '#xyzxyz' }];
  profile.logos = [{
    id: 'logo:primary-002',
    component_id: 'component:missing',
    name: '主 Logo',
    policy: 'exact_preserve',
  }];
  profile.platform_specs[0].safe_area_percent = 80;

  const errors = validateProductProfile(profile);
  assert.ok(errors.some((item) => item.path === 'brand_colors.0.value'));
  assert.ok(errors.some((item) => item.path === 'logos.0.component_id'));
  assert.ok(errors.some((item) => item.path === 'platform_specs.0.safe_area_percent'));
});
