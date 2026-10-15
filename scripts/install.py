"""Install into the current Windows-backed CODEX_HOME, preserving other settings.

Run from the cloned repository in WSL (Python >= 3.11 for tomllib):

    CODEX_HOME=/mnt/c/Users/<you>/.codex python3 scripts/install.py

Tests may pass ``codex_home`` explicitly to target a temporary mock directory.
The repository ``.mcp.json`` is a template only; the installed copy gets an
absolute ``scripts/server.py`` path so no host-side variable interpolation is
required.
"""
import json
import os
from pathlib import Path
import shutil
try:
    import tomllib
except ImportError:  # Python < 3.11
    raise SystemExit('background-tasks installer requires Python 3.11+ (tomllib)') from None

# Development-only files must never be copied into the installed plugin.
COPY_IGNORE = shutil.ignore_patterns('.git', 'tests', '__pycache__', '*.pyc', '*.pyo', '.pytest_cache')
PLUGIN_NAME = 'background-tasks'
MARKETPLACE_NAME = 'codex-tools'
PLUGIN_KEY = f'{PLUGIN_NAME}@{MARKETPLACE_NAME}'


def resolve_home(codex_home=None):
    """Return the target CODEX_HOME.

    The environment path keeps the Windows/WSL safety constraint: the desktop
    app's CODEX_HOME is a Windows directory mounted under /mnt. Tests pass an
    explicit temporary path so they never touch a real installation.
    """
    if codex_home is not None:
        return Path(codex_home)
    home = Path(os.environ['CODEX_HOME'])
    if not str(home).startswith('/mnt/'):
        raise RuntimeError('Expected the desktop app Windows-backed CODEX_HOME in WSL')
    return home


def install(codex_home=None):
    home = resolve_home(codex_home)
    source = Path(__file__).resolve().parents[1]
    destination = home / 'plugins/local/background-tasks'
    config_path = home / 'config.toml'
    config_existed = config_path.exists()
    original_config = config_path.read_text() if config_existed else ''
    config = tomllib.loads(original_config)

    marketplace_path = home.parent / '.agents/plugins/marketplace.json'
    original_catalog = marketplace_path.read_text() if marketplace_path.exists() else None
    try:
        catalog = (json.loads(original_catalog) if original_catalog is not None
                   else {'name': MARKETPLACE_NAME, 'interface': {'displayName': 'Codex Tools'}, 'plugins': []})
    except ValueError:
        raise RuntimeError('Existing marketplace file is invalid; refusing to overwrite') from None
    if not isinstance(catalog, dict) or not isinstance(catalog.get('plugins', []), list) or \
            not all(isinstance(plugin, dict) for plugin in catalog.get('plugins', [])):
        raise RuntimeError('Existing marketplace file is invalid; refusing to overwrite')
    if catalog.get('name') != MARKETPLACE_NAME:
        raise RuntimeError('Personal marketplace already has a different identity; refusing to overwrite')
    marketplace = config.get('marketplaces', {}).get(MARKETPLACE_NAME)
    if marketplace and (marketplace.get('source_type') != 'local' or marketplace.get('source') != str(home.parent)):
        raise RuntimeError('Existing codex-tools marketplace points elsewhere; refusing to overwrite')
    plugins = catalog.setdefault('plugins', [])
    existing = next((plugin for plugin in plugins if plugin.get('name') == PLUGIN_NAME), None)
    entry = {'name': PLUGIN_NAME, 'source': {'source': 'local', 'path': './' + home.name + '/plugins/local/background-tasks'},
             'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Productivity'}
    if existing and existing.get('source') != entry['source']:
        raise RuntimeError('Existing plugin source differs; refusing to overwrite')

    text = original_config
    if not existing:
        plugins.append(entry)
    if not marketplace:
        text += '\n[marketplaces.codex-tools]\nsource_type = "local"\nsource = ' + json.dumps(str(home.parent)) + '\n'
    current = config.get('plugins', {}).get(PLUGIN_KEY)
    if current is not None and current.get('enabled') is not True:
        raise RuntimeError('Plugin was previously disabled; preserving that setting')
    if current is None:
        text += '\n[plugins."background-tasks@codex-tools"]\nenabled = true\n'
    tomllib.loads(text)
    catalog_text = json.dumps(catalog, ensure_ascii=False, indent=2) + '\n'

    destination.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=COPY_IGNORE)
    # Pin an absolute script path in the installed MCP config; avoids host-specific interpolation.
    mcp = json.loads((destination / '.mcp.json').read_text())
    mcp['mcpServers']['background_tasks']['args'] = [str(destination / 'scripts/server.py')]
    (destination / '.mcp.json').write_text(json.dumps(mcp, ensure_ascii=False, indent=2) + '\n')

    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    if catalog_text != original_catalog:
        if original_catalog is not None:
            backup = marketplace_path.with_name('marketplace.json.before-background-tasks')
            if not backup.exists():
                shutil.copy2(marketplace_path, backup)
        marketplace_path.write_text(catalog_text)
    if text != original_config:
        if config_existed:
            backup = config_path.with_name('config.toml.before-background-tasks')
            if not backup.exists():
                shutil.copy2(config_path, backup)
        config_path.write_text(text)
    return {'installed': str(destination), 'marketplace': str(marketplace_path),
            'enabled': PLUGIN_KEY, 'reload_required': True}


if __name__ == '__main__':
    print(json.dumps(install(), ensure_ascii=False, indent=2))
