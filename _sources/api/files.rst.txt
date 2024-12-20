Files API
=========

Overview
--------
The Files API provides methods for managing file operations in FortiSOAR including uploading single and multiple files.

Files are used by other modules such as Attachments, Import Wizard, and War rooms. After you upload a file, you need to pass the file records @id to the relevant module field to link the file to the record

An full example of using this API is found here `Files API Example <https://github.com/ftnt-dspille/pyfsr/blob/4ea266826534c008faa448f94566198f1ae42578/examples/upload_attachment_record.py>`_

Examples
--------

Uploading a Single File
~~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: python

    from pyfsr import FortiSOAR

    client = FortiSOAR("your-server", "your-token")

    # Upload a single file
    result = client.files.upload("path/to/file.pdf")
    print(f"Uploaded file ID: {result['id']}")

Uploading Multiple Files
~~~~~~~~~~~~~~~~~~~~~~~~
.. code-block:: python

    # Upload multiple files
    files = [
        "path/to/file1.pdf",
        "path/to/file2.jpg",
        "path/to/file3.doc"
    ]

    results = client.files.upload_many(files)
    for result in results:
        print(f"Uploaded file ID: {result['id']}")

Error Handling
~~~~~~~~~~~~~~
.. code-block:: python

    try:
        result = client.files.upload("nonexistent.pdf")
    except FileNotFoundError as e:
        print(f"File not found error: {e}")
    except Exception as e:
        print(f"Upload failed: {e}")

API Reference
-------------
.. autoclass:: pyfsr.utils.file_operations.FileOperations
   :members:
   :undoc-members:
   :show-inheritance:
   :special-members: __init__

Notes
-----
- Files are uploaded using multipart/form-data encoding
- MIME types are automatically detected for uploaded files
- If MIME type cannot be determined, defaults to 'application/octet-stream'
- Binary files should be handled properly with automatic encoding