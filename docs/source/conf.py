# Configuration file for the Sphinx documentation builder.
import importlib
import sys
from pathlib import Path

# Get the absolute path to the docs/source directory
current_dir = Path(__file__).parent.absolute()
project_root = current_dir.parent.parent
sys.path.insert(0, str(project_root / "src"))

# Debug: Print paths to verify
print(f"Project root: {project_root}")
print(f"Source dir: {project_root / 'src'}")
print(f"Python path: {sys.path}")

project = 'pyfsr'
copyright = '2024, Dylan Spille'
author = 'Dylan Spille'
release = '0.2.0'

# -- General configuration ---------------------------------------------------
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx_autodoc_typehints',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx.ext.doctest',  # For testable examples
]

# Intersphinx configuration
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
}

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = False
napoleon_use_admonition_for_notes = False
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_type_aliases = None

# Autodoc settings
autodoc_member_order = 'bysource'
autodoc_typehints = 'description'
add_module_names = False

templates_path = ['_templates']
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

# Theme options
html_theme_options = {
    'prev_next_buttons_location': 'bottom',
    'style_external_links': False,
    'style_nav_header_background': '#2980B9',
    'collapse_navigation': True,
    'sticky_navigation': True,
    'navigation_depth': 4,
    'includehidden': True,
    'titles_only': False,
}

# Add external links, including GitHub
html_context = {
    "display_github": True,
    "github_user": "ftnt-dspille",
    "github_repo": "pyfsr",
    "github_version": "main",
    "conf_py_path": "/docs/source/",
}

html_show_sourcelink = True


# -- Auto API Documentation Generation --------------------------------------

def generate_api_docs(app):
    """Generate RST files for all API modules"""
    try:
        from pyfsr.api import __path__ as api_path
    except ImportError as e:
        print(f"Warning: Could not import pyfsr.api: {e}")
        return

    api_dir = Path(api_path[0])
    docs_dir = current_dir
    api_docs_dir = docs_dir / 'api'
    api_docs_dir.mkdir(exist_ok=True)

    # Only generate index.rst if it doesn't exist
    index_path = api_docs_dir / 'index.rst'
    if not index_path.exists():
        with open(index_path, 'w') as f:
            f.write('''API Reference
============

.. toctree::
   :maxdepth: 2

''')

    # Process each API module
    for file in api_dir.glob('*.py'):
        if file.stem == '__init__':
            continue

        module_name = f'pyfsr.api.{file.stem}'
        rst_path = api_docs_dir / f'{file.stem}.rst'

        # Skip if RST file already exists
        if rst_path.exists():
            continue

        try:
            # Import module to ensure it exists
            importlib.import_module(module_name)

            # Write module documentation
            with open(rst_path, 'w') as f:
                title = file.stem.replace('_', ' ').title()
                f.write(f'''{title}
{'=' * len(title)}

.. automodule:: pyfsr.api.{file.stem}
   :members:
   :undoc-members:
   :show-inheritance:

''')

            # Update index only if module RST was newly created
            with open(index_path, 'a') as f:
                f.write(f'   {file.stem}\n')

        except ImportError as e:
            print(f"Warning: Could not import {module_name}: {e}")


def setup(app):
    """Setup Sphinx app with custom configurations"""
    from sphinx.ext.autodoc import ClassDocumenter

    class APIClassDocumenter(ClassDocumenter):
        """Custom class documenter for API classes"""
        objtype = 'apiclass'
        directivetype = 'class'
        priority = 10

        @classmethod
        def can_document_member(cls, member, membername, isattr, parent):
            return (isinstance(member, type) and
                    member.__module__.startswith('pyfsr.api'))

    app.add_autodocumenter(APIClassDocumenter)
    app.connect('builder-inited', generate_api_docs)

    return {
        'version': '1.0',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }


# Setup doctest
doctest_global_setup = '''
import os
import sys
sys.path.insert(0, os.path.abspath('../..'))
from pyfsr import FortiSOAR

# Mock client for examples
class MockFortiSOAR(FortiSOAR):
    def __init__(self):
        pass

client = MockFortiSOAR()
'''
