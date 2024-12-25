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
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'sphinx.ext.viewcode',
    'sphinx_autodoc_typehints',
    'autoapi.extension',
]

# AutoAPI settings
autoapi_type = 'python'
autoapi_dirs = [str(project_root / 'src' / 'pyfsr')]
autoapi_options = [
    'members',
    'undoc-members',
    'show-inheritance',
    'show-module-summary',
    'special-members',
]
autoapi_add_toctree_entry = False
autoapi_python_use_implicit_namespaces = True
autoapi_python_class_content = 'init'  # Changed from 'both' to 'init'
autoapi_member_order = 'groupwise'
autoapi_template_dir = '_templates/autoapi'  # Custom template directory

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
    'titles_only': True,
    'prev_next_buttons_location': 'bottom',
    'style_external_links': True,
    'collapse_navigation': False,
    'sticky_navigation': True,
}


def setup(app):
    """Set up Sphinx app with customizations."""
    app.add_css_file('custom.css')

    # Create custom CSS file
    static_dir = Path(__file__).parent / '_static'
    static_dir.mkdir(exist_ok=True)

    with open(static_dir / 'custom.css', 'w') as f:
        f.write("""
/* Improve overall navigation appearance */
.wy-nav-side {
    background-color: #2c2c2c;
}

.wy-side-nav-search {
    background-color: #2980B9;
}

/* Fix TOC tree padding */
.wy-menu-vertical li {
    margin: 0;
}

.wy-menu-vertical a {
    padding: 0.4045em 1.618em;
}

.wy-menu-vertical li.toctree-l1 > a {
    padding: 0.4045em 1em;
}

.wy-menu-vertical li.toctree-l2 > a {
    padding: 0.4045em 1.2em;
}

.wy-menu-vertical li.current > a {
    background: #fcfcfc;
    padding: 0.4045em 1em;
}

.wy-menu-vertical li.current a {
    border: none;
}

.wy-nav-content {
    max-width: 1000px;
}

div[class^='highlight'] {
    border: none;
    border-radius: 3px;
}

.highlight {
    background: #f8f8f8;
}
""")

    return {
        'version': '1.0',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
