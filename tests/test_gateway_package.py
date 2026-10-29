"""Compatibility imports and project-resource roots; offline only."""
import importlib
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class PackageLayoutTests(unittest.TestCase):
    def test_legacy_modules_are_same_objects(self):
        names=['server','client_attachments','conversation_bridge','conversation_catalog','conversation_stream','gateway_log','openai_compat','send_content','source_identity','source_router','tool_audit','tool_prompt','tool_reference']
        for name in names:
            with self.subTest(name=name):self.assertIs(importlib.import_module(name),importlib.import_module('gateway.'+name))
    def test_gateway_resources_stay_at_project_root(self):
        import server
        self.assertEqual(server.ROOT,ROOT)
    def test_reference_manifest_uses_existing_root_resource(self):
        import tool_reference
        self.assertEqual(tool_reference.manifest()['version'],1)
        self.assertTrue((ROOT/'tool_reference_manifest.json').is_file())
    def test_cli_entries_are_available(self):
        self.assertIn('gateway.server',(ROOT/'server.py').read_text())
        self.assertIn('gateway.server',(ROOT/'gateway/__main__.py').read_text())
