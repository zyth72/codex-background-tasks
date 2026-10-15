"""Installer tests: temporary mock CODEX_HOME only, never a real installation.

Run from the repository root with:

    python3 -m unittest discover -s tests -v
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import install  # noqa: E402


class InstallTests(unittest.TestCase):
    def make_home(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name) / 'mock' / '.codex'
        home.mkdir(parents=True)
        return home

    def write_marketplace(self, home, catalog):
        path = home.parent / '.agents/plugins/marketplace.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
        return path

    def test_fresh_install_preserves_other_settings_and_filters_dev_files(self):
        home = self.make_home()
        config_path = home / 'config.toml'
        original = ('[settings]\nmodel = "gpt-test"\n\n'
                    '[plugins."other-tool@other-market"]\nenabled = true\n\n'
                    '[marketplaces.other-market]\nsource_type = "local"\nsource = "/somewhere"\n')
        config_path.write_text(original)
        catalog = {'name': 'codex-tools', 'interface': {'displayName': 'Codex Tools'},
                   'plugins': [{'name': 'other-tool', 'source': {'source': 'local', 'path': './other'}}]}
        self.write_marketplace(home, catalog)

        result = install.install(codex_home=home)

        destination = home / 'plugins/local/background-tasks'
        self.assertEqual(result['installed'], str(destination))
        self.assertTrue(result['reload_required'])
        self.assertTrue((destination / 'scripts/server.py').is_file())
        self.assertTrue((destination / '.codex-plugin/plugin.json').is_file())
        self.assertTrue((destination / 'skills/background-tasks/SKILL.md').is_file())

        # Development-only files from the source repository are not installed.
        self.assertFalse((destination / 'tests').exists())
        self.assertFalse((destination / '.git').exists())
        self.assertFalse((destination / '__pycache__').exists())

        # The installed MCP config gets an absolute path; the repository file stays a template.
        mcp = json.loads((destination / '.mcp.json').read_text())
        self.assertEqual(mcp['mcpServers']['background_tasks']['args'],
                         [str(destination / 'scripts/server.py')])
        self.assertTrue(Path(mcp['mcpServers']['background_tasks']['args'][0]).is_absolute())
        template = json.loads((ROOT / '.mcp.json').read_text())
        self.assertEqual(template['mcpServers']['background_tasks']['args'], ['scripts/server.py'])

        config = tomllib.loads(config_path.read_text())
        self.assertEqual(config['settings']['model'], 'gpt-test')
        self.assertTrue(config['plugins']['other-tool@other-market']['enabled'])
        self.assertEqual(config['marketplaces']['other-market']['source'], '/somewhere')
        self.assertEqual(config['marketplaces']['codex-tools']['source'], str(home.parent))
        self.assertTrue(config['plugins']['background-tasks@codex-tools']['enabled'])

        updated = json.loads((home.parent / '.agents/plugins/marketplace.json').read_text())
        self.assertEqual(updated['name'], 'codex-tools')
        self.assertEqual(updated['interface'], {'displayName': 'Codex Tools'})
        self.assertEqual([plugin['name'] for plugin in updated['plugins']], ['other-tool', 'background-tasks'])
        backup = home.parent / '.agents/plugins/marketplace.json.before-background-tasks'
        self.assertEqual(json.loads(backup.read_text()), catalog)
        self.assertEqual((home / 'config.toml.before-background-tasks').read_text(), original)

    def test_custom_home_and_catalog_without_plugins(self):
        home = self.make_home().with_name('custom-codex')
        home.mkdir()
        self.write_marketplace(home, {'name': 'codex-tools'})
        install.install(codex_home=home)
        catalog = json.loads((home.parent / '.agents/plugins/marketplace.json').read_text())
        self.assertEqual(catalog['plugins'][0]['source']['path'],
                         './custom-codex/plugins/local/background-tasks')

    def test_install_is_idempotent_and_does_not_rewrite_unchanged_files(self):
        home = self.make_home()
        config_path = home / 'config.toml'
        config_path.write_text('[settings]\nmodel = "gpt-test"\n')
        install.install(codex_home=home)
        first_config = config_path.read_text()
        first_mtime = config_path.stat().st_mtime_ns
        marketplace_path = home.parent / '.agents/plugins/marketplace.json'
        first_marketplace = marketplace_path.read_text()
        first_marketplace_mtime = marketplace_path.stat().st_mtime_ns

        install.install(codex_home=home)

        self.assertEqual(config_path.read_text(), first_config)
        self.assertEqual(config_path.stat().st_mtime_ns, first_mtime)
        self.assertEqual(marketplace_path.read_text(), first_marketplace)
        self.assertEqual(marketplace_path.stat().st_mtime_ns, first_marketplace_mtime)
        catalog = json.loads(marketplace_path.read_text())
        self.assertEqual([plugin['name'] for plugin in catalog['plugins']].count('background-tasks'), 1)
        config = tomllib.loads(config_path.read_text())
        self.assertIn('background-tasks@codex-tools', config['plugins'])

    def test_conflicting_marketplace_identity_does_not_write_anything(self):
        home = self.make_home()
        config_path = home / 'config.toml'
        config_path.write_text('[settings]\nmodel = "gpt-test"\n')
        marketplace_path = self.write_marketplace(home, {'name': 'someone-else', 'plugins': []})

        with self.assertRaises(RuntimeError):
            install.install(codex_home=home)

        self.assertEqual(config_path.read_text(), '[settings]\nmodel = "gpt-test"\n')
        self.assertEqual(json.loads(marketplace_path.read_text())['name'], 'someone-else')
        self.assertFalse((home / 'plugins/local/background-tasks').exists())
        self.assertFalse((home / 'config.toml.before-background-tasks').exists())

    def test_conflicting_plugin_source_does_not_write_anything(self):
        home = self.make_home()
        config_path = home / 'config.toml'
        config_path.write_text('')
        marketplace_path = self.write_marketplace(home, {
            'name': 'codex-tools',
            'plugins': [{'name': 'background-tasks', 'source': {'source': 'local', 'path': './elsewhere'}}],
        })

        with self.assertRaises(RuntimeError):
            install.install(codex_home=home)

        self.assertEqual(config_path.read_text(), '')
        self.assertEqual(json.loads(marketplace_path.read_text())['plugins'][0]['source']['path'], './elsewhere')
        self.assertFalse((home / 'plugins/local/background-tasks').exists())

    def test_disabled_plugin_is_preserved_and_install_refuses_to_change_it(self):
        home = self.make_home()
        config_path = home / 'config.toml'
        original = '[plugins."background-tasks@codex-tools"]\nenabled = false\n'
        config_path.write_text(original)

        with self.assertRaises(RuntimeError):
            install.install(codex_home=home)

        self.assertEqual(config_path.read_text(), original)
        self.assertFalse((home / 'plugins/local/background-tasks').exists())

    def test_invalid_marketplace_file_is_rejected(self):
        home = self.make_home()
        marketplace_path = home.parent / '.agents/plugins/marketplace.json'
        marketplace_path.parent.mkdir(parents=True, exist_ok=True)
        marketplace_path.write_text('{not json')

        with self.assertRaises(RuntimeError):
            install.install(codex_home=home)

        self.assertEqual(marketplace_path.read_text(), '{not json')
        self.assertFalse((home / 'plugins/local/background-tasks').exists())

    def test_environment_path_outside_mnt_is_rejected(self):
        home = self.make_home()
        with mock.patch.dict(os.environ, {'CODEX_HOME': str(home)}):
            with self.assertRaises(RuntimeError):
                install.install()
        self.assertFalse((home / 'plugins/local/background-tasks').exists())


if __name__ == '__main__':
    unittest.main()
