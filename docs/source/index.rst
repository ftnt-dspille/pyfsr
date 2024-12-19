Welcome to pyfsr documentation
==============================

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   api/client
   api/auth
   api/alerts

Installation
------------
.. code-block:: bash

   pip install pyfsr

Quick Start
-----------
.. code-block:: python

   from pyfsr import FortiSOAR
   
   client = FortiSOAR('your-server', 'your-token')