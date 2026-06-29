"""Unit tests for the typed AttachmentsAPI and ExportTemplatesAPI (T3.4)."""

from pyfsr.api.attachments import AttachmentsAPI
from pyfsr.api.export_templates import ExportTemplatesAPI
from pyfsr.models import Attachment, ExportTemplate


class FakeFiles:
    def upload(self, path):
        return {"@id": "/api/3/files/file-uuid", "filename": "report.csv"}


class FakeClient:
    def __init__(self, *, post_resp=None, get_resp=None):
        self.posts = []
        self.gets = []
        self.deletes = []
        self._post_resp = post_resp or {}
        self._get_resp = get_resp or {}
        self.files = FakeFiles()

    def post(self, endpoint, data=None, params=None, **kw):
        self.posts.append((endpoint, data))
        return {"@id": "/api/3/attachments/att-1", "uuid": "att-1", **(data or {}), **self._post_resp}

    def get(self, endpoint, params=None, **kw):
        self.gets.append((endpoint, params))
        return self._get_resp.get(endpoint, self._get_resp.get("*", {}))

    def delete(self, endpoint, params=None, **kw):
        self.deletes.append(endpoint)


# -- attachments ------------------------------------------------------------
def test_attachment_create_returns_typed_model_and_posts_iri():
    c = FakeClient()
    api = AttachmentsAPI(c)
    att = api.create(name="r.csv", file="/api/3/files/f-1", description="d")
    assert isinstance(att, Attachment)
    assert att.name == "r.csv"
    endpoint, body = c.posts[0]
    assert endpoint == "/api/3/attachments"
    assert body["file"] == "/api/3/files/f-1"
    assert body["description"] == "d"


def test_attachment_create_accepts_file_record_dict():
    c = FakeClient()
    att = AttachmentsAPI(c).create(name="x", file={"@id": "/api/3/files/abc"})
    assert c.posts[0][1]["file"] == "/api/3/files/abc"
    assert isinstance(att, Attachment)


def test_attachment_create_from_file_uploads_then_links():
    c = FakeClient()
    att = AttachmentsAPI(c).create_from_file("report.csv", description="daily")
    # uploaded file IRI linked, name defaulted from the file record
    endpoint, body = c.posts[0]
    assert body["file"] == "/api/3/files/file-uuid"
    assert body["name"] == "report.csv"
    assert isinstance(att, Attachment)


def test_attachment_get_and_delete_resolve_uuid():
    c = FakeClient(get_resp={"*": {"@id": "/api/3/attachments/att-9", "name": "n"}})
    api = AttachmentsAPI(c)
    got = api.get("/api/3/attachments/att-9")
    assert isinstance(got, Attachment)
    assert c.gets[0][0] == "/api/3/attachments/att-9"
    api.delete("/api/3/attachments/att-9")
    assert c.deletes == ["/api/3/attachments/att-9"]


# -- export templates -------------------------------------------------------
def test_export_template_create_returns_typed_model():
    c = FakeClient()
    api = ExportTemplatesAPI(c)
    tmpl = api.create("Nightly", options={"modules": ["alerts"]})
    assert isinstance(tmpl, ExportTemplate)
    endpoint, body = c.posts[0]
    assert endpoint == "/api/3/export_templates"
    assert body["name"] == "Nightly"
    assert body["options"] == {"modules": ["alerts"]}


def test_export_template_list_returns_typed_list():
    c = FakeClient(get_resp={"/api/3/export_templates": {"hydra:member": [{"name": "a"}, {"name": "b"}]}})
    out = ExportTemplatesAPI(c).list()
    assert [t.name for t in out] == ["a", "b"]
    assert all(isinstance(t, ExportTemplate) for t in out)


def test_export_template_get_and_delete():
    c = FakeClient(get_resp={"*": {"@id": "/api/3/export_templates/t-1", "name": "n"}})
    api = ExportTemplatesAPI(c)
    assert isinstance(api.get("t-1"), ExportTemplate)
    api.delete("t-1")
    assert c.deletes == ["/api/3/export_templates/t-1"]


# -- typed sub-models (no list[Any]) ----------------------------------------
def test_export_options_models_connectors_and_coerces_empty():
    from pyfsr.models import ExportConnectorRef, ExportOptions

    # empty options come back as [] from FortiSOAR -> coerced to None on the template
    tmpl = ExportTemplate.model_validate({"name": "n", "options": []})
    assert tmpl.options is None
    # populated: connectors are typed ExportConnectorRef, unmodeled lists kept in extra
    opts = ExportOptions.model_validate(
        {"connectors": [{"name": "cyberark", "version": "2.1.0", "rpm": True, "configCount": 2}], "modules": ["alerts"]}
    )
    assert isinstance(opts.connectors[0], ExportConnectorRef)
    assert opts.connectors[0].name == "cyberark"
    assert opts.connectors[0].config_count == 2  # aliased configCount
    assert opts.to_dict(by_alias=True)["modules"] == ["alerts"]  # preserved via extra


def test_attachment_file_typed_and_empty_refs_coerced():
    from pyfsr.models import FileRecord

    att = Attachment.model_validate(
        {"name": "x", "file": {"@id": "/api/3/files/f", "filename": "x.csv"}, "assignee": "", "createUser": []}
    )
    assert isinstance(att.file, FileRecord)
    assert att.file.filename == "x.csv"
    assert att.assignee is None  # "" coerced
    assert att.createUser is None  # [] coerced
    # bare IRI string still accepted
    assert Attachment.model_validate({"file": "/api/3/files/g"}).file == "/api/3/files/g"


def test_execute_result_ok_property():
    from pyfsr.models import ExecuteResult

    assert ExecuteResult(status="Success").ok is True
    assert ExecuteResult(status="success").ok is True
    assert ExecuteResult(status="Failed").ok is False
    assert ExecuteResult(status=None).ok is False
