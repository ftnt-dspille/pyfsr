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
autosummary_generate = True
autosummary_imported_members = False  # Don't document imported members
add_module_names = False  # Remove module names from generated titles

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
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store', '**.ipynb_checkpoints']

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
html_theme_options = {
    'navigation_depth': 2,  # Limit the depth of the navigation tree
    'titles_only': True,  # Only show titles in the navigation
    'collapse_navigation': False,  # Keep the navigation expanded
    'sticky_navigation': True,
    'prev_next_buttons_location': 'bottom',
}

# Clean up the displayed module names
modindex_common_prefix = ['pyfsr.']


def setup(app):
    """Set up Sphinx app with customizations."""
    # Add custom CSS to clean up the layout
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

        /* Level 1 items */
        .wy-menu-vertical li.toctree-l1 > a {
            padding: 0.4045em 1em;
        }

        /* Level 2 items */
        .wy-menu-vertical li.toctree-l2 > a {
            padding: 0.4045em 1.2em;
        }

        /* Level 3 items */
        .wy-menu-vertical li.toctree-l3 > a {
            padding: 0.4045em 1.4em;
        }

        /* Selected/current item */
        .wy-menu-vertical li.current > a {
            background: #fcfcfc;
            padding: 0.4045em 1em;
        }

        .wy-menu-vertical li.current a {
            border: none;
        }

        /* Improve content width */
        .wy-nav-content {
            max-width: 1000px;
        }

        /* Better code block styling */
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
