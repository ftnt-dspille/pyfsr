"""Unit tests for FileOperations (``client.files``)."""

import pytest

from pyfsr.models import FileRecord
from pyfsr.utils.file_operations import FileOperations


class FakeClient:
    def __init__(self, *, post_resp=None, raise_exc=None):
        self.posts = []
        self._post_resp = (
            post_resp
            if post_resp is not None
            else {
                "@id": "/api/3/files/f-1",
                "filename": "report.csv",
            }
        )
        self._raise_exc = raise_exc

    def post(self, endpoint, files=None, headers=None, **kw):
        self.posts.append((endpoint, files, headers))
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._post_resp


def test_upload_posts_multipart_and_returns_typed_record(tmp_path):
    f = tmp_path / "report.csv"
    f.write_text("a,b\n1,2\n")
    c = FakeClient()

    rec = FileOperations(c).upload(str(f))

    assert isinstance(rec, FileRecord)
    assert rec["@id"] == "/api/3/files/f-1"
    endpoint, files, headers = c.posts[0]
    assert endpoint == "/api/3/files"
    # Content-Type nulled so requests sets the multipart boundary itself
    assert headers == {"Content-Type": None}
    name, fh, mime = files["file"]
    assert name == "report.csv"
    assert mime == "text/csv"


def test_upload_falls_back_to_octet_stream_for_unknown_type(tmp_path):
    f = tmp_path / "blob.unknownext"
    f.write_bytes(b"\x00\x01")
    c = FakeClient()

    FileOperations(c).upload(str(f))

    _, files, _ = c.posts[0]
    assert files["file"][2] == "application/octet-stream"


def test_upload_missing_file_raises_before_posting():
    c = FakeClient()
    with pytest.raises(FileNotFoundError):
        FileOperations(c).upload("/nope/does-not-exist.txt")
    assert c.posts == []


def test_upload_reraises_post_errors(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    boom = RuntimeError("upstream 500")
    c = FakeClient(raise_exc=boom)

    with pytest.raises(RuntimeError, match="upstream 500"):
        FileOperations(c).upload(str(f))


def test_upload_logs_http_response_detail_then_reraises(tmp_path, caplog):
    f = tmp_path / "x.txt"
    f.write_text("hi")

    class FakeResponse:
        status_code = 413
        text = "payload too large"

    err = RuntimeError("boom")
    err.response = FakeResponse()
    c = FakeClient(raise_exc=err)

    with caplog.at_level("DEBUG", logger="pyfsr"):
        with pytest.raises(RuntimeError):
            FileOperations(c).upload(str(f))
    assert "413" in caplog.text


def test_upload_many_uploads_each(tmp_path):
    files = []
    for i in range(3):
        p = tmp_path / f"f{i}.txt"
        p.write_text(str(i))
        files.append(str(p))
    c = FakeClient()

    recs = FileOperations(c).upload_many(files)

    assert len(recs) == 3
    assert len(c.posts) == 3
    assert all(isinstance(r, FileRecord) for r in recs)
