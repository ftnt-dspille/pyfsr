import os
import sys
from pathlib import Path

# -- Path setup --------------------------------------------------------------
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / 'src'))

# -- Project information -----------------------------------------------------
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
    'sphinx.ext.githubpages',
    'sphinx.ext.autosummary',
]

# Autosummary settings
autosummary_generate = True  # Generate stub pages for all documented items
autosummary_imported_members = True  # Document imported members

# Autodoc settings
autodoc_member_order = 'bysource'
autodoc_typehints = 'description'
autodoc_default_options = {
    'members': True,
    'member-order': 'bysource',
    'special-members': '__init__',
    'undoc-members': True,
    'exclude-members': '__weakref__'
}

# Napoleon settings
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_rtype = True

# Intersphinx mapping
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'requests': ('https://requests.readthedocs.io/en/latest/', None),
}

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
html_theme_options = {
    'navigation_depth': 4,
    'titles_only': False,
}


def setup(app):
    """Set up Sphinx app with automatic API documentation generation."""
    from sphinx.ext.apidoc import main as sphinx_apidoc

    # Clean existing API docs
    api_dir = Path('api')
    if api_dir.exists():
        for f in api_dir.glob('*.rst'):
            f.unlink()
        for f in api_dir.glob('*.py'):
            f.unlink()

    # Auto-generate API documentation
    module_dir = project_root / 'src' / 'pyfsr'
    output_dir = Path(__file__).parent / 'api'
    output_dir.mkdir(exist_ok=True)

    # Call sphinx-apidoc with positional arguments
    sphinx_apidoc([
        '--force',  # Overwrite existing files
        '--separate',  # Create a file for each module
        '--module-first',  # Module documentation before submodule
        '--tocfile', 'index',  # Name of the main file
        '-o', str(output_dir),  # Output directory
        str(module_dir),  # Module directory
        str(module_dir / '*/__pycache__'),  # Exclude patterns
    ])

    # Create simple index.rst if it doesn't exist
    index_path = Path(__file__).parent / 'index.rst'
    if not index_path.exists():
        with open(index_path, 'w') as f:
            f.write('''pyfsr Documentation
=================

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   api/index

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
''')

    return {
        'version': '1.0',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
