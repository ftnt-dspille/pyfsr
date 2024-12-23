# scripts/manage_docs.py

import os
import shutil
from pathlib import Path


def setup_docs():
    """Set up initial documentation structure"""
    docs_dir = Path('docs')
    source_dir = docs_dir / 'source'

    # Create directory structure
    for dir_path in [
        docs_dir,
        source_dir,
        source_dir / '_static',
        source_dir / '_templates',
        source_dir / 'api',
    ]:
        dir_path.mkdir(exist_ok=True)

    # Copy conf.py if it doesn't exist
    conf_path = source_dir / 'conf.py'
    if not conf_path.exists():
        shutil.copy('scripts/templates/conf.py', conf_path)


def build_docs():
    """Build documentation using sphinx-build"""
    os.system('sphinx-build -b html docs/source docs/build/html')


def clean_docs():
    """Clean generated documentation files"""
    shutil.rmtree('docs/build', ignore_errors=True)

    # Clean generated RST files but keep structure
    api_dir = Path('docs/source/api')
    for rst_file in api_dir.glob('*.rst'):
        if rst_file.name != 'index.rst':
            rst_file.unlink()


if __name__ == '__main__':
    import sys

    commands = {
        'setup': setup_docs,
        'build': build_docs,
        'clean': clean_docs,
    }

    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print(f"Usage: python manage_docs.py [{' | '.join(commands.keys())}]")
        sys.exit(1)

    commands[sys.argv[1]]()
