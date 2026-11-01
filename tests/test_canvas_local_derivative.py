from __future__ import annotations
import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from python.asset_store import AssetStore
from python.atelier_ledger import AtelierLedger, IdempotencyConflictError
from python.canvas_local_derivative import apply_canvas_derivative
from tests.test_node_scene_contract import scene


def png(color, size=(64, 32)):
    output = io.BytesIO(); Image.new('RGBA', size, color).save(output, 'PNG'); output.seek(0); return output


class CanvasLocalDerivativeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = AtelierLedger(self.root / 'ledger.db')
        self.store = AssetStore(self.root / 'assets', self.ledger)
        self.source = self.store.import_stream(png('#fecc00'), 'source.png')
        self.canvas = self.ledger.create_spatial_canvas(name='test', client_request_id='create', scene=scene(self.source['id']))
        self.payload = dict(ledger=self.ledger, store=self.store, output_root=self.root / 'output',
            canvas_id=self.canvas['id'], version_id=self.canvas['current_version_id'], element_id='source',
            source_asset_id=self.source['id'], request_id='apply-1', operation='crop')

    def test_exact_parent_original_unchanged_idempotent_and_no_task(self):
        first = apply_canvas_derivative(**self.payload, stream=png('#ffffff', (32, 16)))
        replay = apply_canvas_derivative(**self.payload, stream=png('#ffffff', (32, 16)))
        self.assertEqual(first['asset']['id'], replay['asset']['id'])
        self.assertEqual(first['asset']['parent_asset_id'], self.source['id'])
        self.assertEqual(first['receipt']['provider_calls'], 0)
        self.assertEqual(self.ledger.get_asset(self.source['id'])['sha256'], self.source['sha256'])
        self.assertEqual(self.canvas['current_revision'], self.ledger.get_spatial_canvas(self.canvas['id'])['current_revision'])
        self.assertEqual(self.ledger.list_jobs(), [])
        self.assertTrue(Path(first['asset']['path']).is_file())
        self.assertEqual(AtelierLedger(self.root / 'ledger.db').get_asset(first['asset']['id'])['parent_asset_id'], self.source['id'])

    def test_wrong_source_and_stale_scene_are_rejected(self):
        with self.assertRaises(ValueError): apply_canvas_derivative(**{**self.payload, 'element_id': 'prompt'}, stream=png('#ffffff'))
        with self.assertRaises(ValueError): apply_canvas_derivative(**{**self.payload, 'version_id': 'wrong'}, stream=png('#ffffff'))

    def test_replay_with_changed_pixels_or_operation_is_not_overwritten(self):
        first = apply_canvas_derivative(**self.payload, stream=png('#ffffff'))
        for patch in ({}, {'operation': 'brush'}):
            with self.assertRaises(IdempotencyConflictError): apply_canvas_derivative(**{**self.payload, **patch}, stream=png('#000000'))
        self.assertEqual(self.ledger.get_asset(first['asset']['id'])['sha256'], first['asset']['sha256'])

    def test_unrelated_scene_changes_and_lost_response_replay_preserve_exact_input(self):
        first = apply_canvas_derivative(**self.payload, stream=png('#ffffff'))
        changed = scene(self.source['id']); changed['nodes'][0]['position']['x'] += 100
        self.ledger.save_spatial_canvas_scene(self.canvas['id'], expected_revision=1, client_request_id='geometry', scene=changed)
        replay = apply_canvas_derivative(**self.payload, stream=png('#ffffff'))
        self.assertEqual(first['asset']['id'], replay['asset']['id'])
        second = apply_canvas_derivative(**{**self.payload, 'request_id': 'apply-2'}, stream=png('#ff0000'))
        self.assertEqual(second['asset']['parent_asset_id'], self.source['id'])


if __name__ == '__main__': unittest.main()
