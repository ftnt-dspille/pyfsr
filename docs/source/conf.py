# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information
import sys
from pathlib import Path

# Get the absolute path to the docs/source directory
current_dir = Path(__file__).parent.absolute()

# Go up two levels to reach the project root (from docs/source to project root)
project_root = current_dir.parent.parent

# Add the project root and src directories to Python path
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

# Debug: Print paths to verify
print(f"Project root: {project_root}")
print(f"Source dir: {project_root / 'src'}")
print(f"Python path: {sys.path}")

# Try to import the package to verify it's findable
try:
    import pyfsr

    print(f"Successfully imported pyfsr from {pyfsr.__file__}")
except ImportError as e:
    print(f"Warning: Could not import pyfsr: {e}")

# Project information
project = 'pyfsr'
copyright = '2024, Dylan Spille'
author = 'Dylan Spille'
release = '0.2.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx_autodoc_typehints',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',  # Add this for external references
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
    # Toc options
    'collapse_navigation': True,
    'sticky_navigation': True,
    'navigation_depth': 4,
    'includehidden': True,
    'titles_only': False
}

# If true, links to the reST sources are added to the pages.
html_show_sourcelink = True

# GitHub Pages settings
html_baseurl = '/pyFSR/'
html_copy_source = True
html_use_index = True