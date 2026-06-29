"""Unit tests for the stable system-entity models (workflows + content hub)."""

from pyfsr import (
    BaseRecord,
    ContentHubConnector,
    ContentHubItem,
    FeaturedTag,
    ImportJob,
    RecordSet,
    SolutionPack,
    SolutionPackInstallResponse,
    Widget,
    Workflow,
    WorkflowCollection,
    model_for,
)
from pyfsr.api.content_hub import ContentHubSearch
from pyfsr.api.playbooks import PlaybooksAPI
from pyfsr.models import MODEL_REGISTRY


class FakeClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def get(self, endpoint, params=None, **kwargs):
        self.calls.append(("GET", endpoint, params, None))
        return self.responses.get(endpoint, {})

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("POST", endpoint, params, data))
        return self.responses.get(endpoint, {})


# -- registry ---------------------------------------------------------------
def test_registry_covers_workflow_entities():
    assert MODEL_REGISTRY["workflows"] is Workflow
    assert MODEL_REGISTRY["workflow_collections"] is WorkflowCollection


def test_model_for_workflows():
    assert model_for("workflows") is Workflow
    assert model_for("workflow_collections") is WorkflowCollection


# -- EmailTemplate model + Team.actors (live shapes from an 8.0 box) ---------
def test_email_template_model_and_registry():
    from pyfsr.models import EmailTemplate

    assert model_for("email_templates") is EmailTemplate
    assert MODEL_REGISTRY["email_templates"] is EmailTemplate
    tpl = EmailTemplate.model_validate(
        {
            "@id": "/api/3/email_templates/12f23d8d",
            "@type": "EmailTemplate",
            "name": "System: Send email to new user",
            "subject": "FortiSOAR password reset",
            "content": "<p>Hi {{user}}</p>",
            "visible": True,
        }
    )
    assert tpl.name == "System: Send email to new user"
    assert tpl.subject == "FortiSOAR password reset"
    assert tpl.iri == "/api/3/email_templates/12f23d8d"
    assert tpl["content"] == "<p>Hi {{user}}</p>"  # dict access compat


def test_team_actors_expand_to_users_with_relationships():
    from pyfsr.models import Team

    team = Team.model_validate(
        {
            "@id": "/api/3/teams/t-1",
            "@type": "Team",
            "name": "SOC Team",
            "actors": [
                {"@id": "/api/3/people/u-1", "@type": "Person", "email": "admin@example.com"},
                "/api/3/people/u-2",  # IRI string when relationships not expanded
            ],
        }
    )
    assert team.name == "SOC Team"
    expanded, ref = team.actors
    assert expanded.email == "admin@example.com"  # parsed into a User
    assert ref == "/api/3/people/u-2"  # bare IRI kept as str


# -- Workflow / WorkflowCollection models -----------------------------------
def test_workflow_typed_fields_and_dict_compat():
    wf = Workflow.model_validate(
        {
            "@id": "/api/3/workflows/wf-1",
            "uuid": "wf-1",
            "name": "Block IP",
            "isActive": True,
            "collection": "/api/3/workflow_collections/c-1",
            "createUser": {"@id": "/api/3/people/u-1", "name": "Ann"},  # expanded dict kept (Any)
        }
    )
    assert wf.name == "Block IP"
    assert wf.isActive is True
    assert wf.collection == "/api/3/workflow_collections/c-1"
    assert wf.iri == "/api/3/workflows/wf-1"
    assert wf["createUser"]["name"] == "Ann"  # dict access + Any kept expanded


def test_workflow_preserves_unknown_fields():
    wf = Workflow.model_validate({"uuid": "wf-1", "somethingNew": 42})
    assert wf["somethingNew"] == 42


def test_workflow_collection_fields():
    wc = WorkflowCollection.model_validate({"uuid": "c-1", "name": "Phishing", "visible": True})
    assert wc.name == "Phishing"
    assert wc.visible is True
    assert isinstance(wc, BaseRecord)


def test_recordset_returns_workflow_model():
    client = FakeClient({"/api/3/workflows/wf-1": {"uuid": "wf-1", "name": "Block IP"}})
    rec = RecordSet(client, "workflows").get("wf-1")
    assert isinstance(rec, Workflow)
    assert rec.name == "Block IP"


def test_recordset_returns_workflow_collection_model():
    client = FakeClient({"/api/3/workflow_collections/c-1": {"uuid": "c-1", "name": "Phishing"}})
    rec = RecordSet(client, "workflow_collections").get("c-1")
    assert isinstance(rec, WorkflowCollection)


# -- Content Hub models -----------------------------------------------------
def test_content_hub_item_hierarchy():
    assert issubclass(SolutionPack, ContentHubItem)
    assert issubclass(ContentHubConnector, ContentHubItem)
    assert issubclass(Widget, ContentHubItem)


def test_solution_pack_fields():
    sp = SolutionPack.model_validate(
        {"uuid": "p1", "name": "soar-framework", "label": "SOAR", "installed": True, "version": "9"}
    )
    assert sp.label == "SOAR"
    assert sp.installed is True
    assert sp.version == "9"


def test_content_hub_search_returns_models():
    members = {"hydra:member": [{"name": "openai", "label": "OpenAI", "type": "connector"}]}
    client = FakeClient({"/api/query/solutionpacks?$limit=30&$page=1&$search=": members})
    ch = ContentHubSearch(client)
    out = ch.search_installed_connectors()
    assert len(out) == 1
    assert isinstance(out[0], ContentHubConnector)
    assert out[0].label == "OpenAI"
    assert out[0]["label"] == "OpenAI"  # dict-compatible


def test_content_hub_find_single_returns_model():
    members = {"hydra:member": [{"name": "stats", "label": "Stats", "type": "widget"}]}
    client = FakeClient({"/api/query/solutionpacks?$limit=1&$page=1&$search=Stats": members})
    ch = ContentHubSearch(client)
    hit = ch.find_installed_widget("Stats")
    assert isinstance(hit, Widget)
    assert hit.label == "Stats"


def test_content_hub_featured_tags_are_typed():
    # live-verified shape: [{"tag": "preview", "color": "#2d87e3"}]
    members = {
        "hydra:member": [
            {
                "name": "x",
                "type": "connector",
                "featuredTags": [{"tag": "preview", "color": "#2d87e3"}],
                "infoPath": "/content-hub/x-1.0.0/9000",
            }
        ]
    }
    client = FakeClient({"/api/query/solutionpacks?$limit=30&$page=1&$search=": members})
    out = ContentHubSearch(client).search_installed_connectors()
    tag = out[0].featuredTags[0]
    assert isinstance(tag, FeaturedTag)
    assert tag.tag == "preview" and tag.color == "#2d87e3"
    assert tag["color"] == "#2d87e3"  # dict-compatible
    assert out[0].infoPath == "/content-hub/x-1.0.0/9000"  # promoted off extra


def test_solution_pack_install_response_job_id_from_typed_import_job():
    resp = SolutionPackInstallResponse(
        name="p", importJob={"@id": "/api/3/import_jobs/abc", "uuid": "abc", "status": "running"}
    )
    assert isinstance(resp.importJob, ImportJob)
    assert resp.importJob.status == "running"
    assert resp.job_id == "abc"
    # falls back to the @id tail when uuid is absent
    assert SolutionPackInstallResponse(name="p", importJob={"@id": "/api/3/import_jobs/zzz"}).job_id == "zzz"


# -- WorkflowRun via PlaybooksAPI -------------------------------------------
_RUN = {
    "@id": "/api/wf/api/workflows/run-1/",
    "name": "Block IP",
    "status": "finished",
    "modified": "2026-06-08T00:00:00Z",
    "node_name": "node-a",
}


def test_playbooks_runs_expose_full_record_in_extra():
    # Typing is native + forced: execution_history always returns RunSummary, and
    # the full WorkflowRun-style fields (node_name, …) survive in extra.
    from pyfsr.models import RunSummary

    client = FakeClient(
        {
            "/api/wf/api/workflows/?format=json&limit=20&ordering=-modified&parent_wf__isnull=True": {
                "hydra:member": [_RUN]
            },
        }
    )
    runs = PlaybooksAPI(client).execution_history()
    assert len(runs) == 1
    assert isinstance(runs[0], RunSummary)
    assert runs[0].status == "finished"
    assert runs[0]["node_name"] == "node-a"


def test_playbooks_runs_default_returns_typed_run_summary():
    from pyfsr.models import RunSummary

    client = FakeClient(
        {
            "/api/wf/api/workflows/?format=json&limit=20&ordering=-modified&parent_wf__isnull=True": {
                "hydra:member": [_RUN]
            },
        }
    )
    runs = PlaybooksAPI(client).execution_history()
    # Typed by default, but still dict-compatible (ApiResult __getitem__/get/in).
    assert isinstance(runs[0], RunSummary)
    for key in ("task_id", "name", "status", "error_message", "modified", "uuid", "pk", "source"):
        assert key in runs[0]
    assert runs[0]["status"] == runs[0].status


def test_playbooks_get_execution_returns_run_summary():
    from pyfsr.models import RunSummary

    client = FakeClient({"/api/wf/api/workflows/run-1/?format=json": _RUN})
    run = PlaybooksAPI(client).get_execution("run-1")
    assert isinstance(run, RunSummary)
    assert run.name == "Block IP"
    assert run.pk == "run-1"
