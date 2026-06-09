"""Unit tests for the stable system-entity models (workflows + content hub)."""

from pyfsr import (
    BaseRecord,
    ContentHubConnector,
    ContentHubItem,
    RecordSet,
    SolutionPack,
    Widget,
    Workflow,
    WorkflowCollection,
    WorkflowRun,
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


def test_content_hub_search_typed_returns_models():
    members = {"hydra:member": [{"name": "openai", "label": "OpenAI", "type": "connector"}]}
    client = FakeClient({"/api/query/solutionpacks?$limit=30&$page=1&$search=": members})
    ch = ContentHubSearch(client)
    out = ch.search_installed_connectors(typed=True)
    assert len(out) == 1
    assert isinstance(out[0], ContentHubConnector)
    assert out[0].label == "OpenAI"


def test_content_hub_search_default_returns_dicts():
    members = {"hydra:member": [{"name": "openai", "label": "OpenAI", "type": "connector"}]}
    client = FakeClient({"/api/query/solutionpacks?$limit=30&$page=1&$search=": members})
    ch = ContentHubSearch(client)
    out = ch.search_installed_connectors()
    assert out == members["hydra:member"]  # plain dicts, unchanged default


def test_content_hub_find_typed_single():
    members = {"hydra:member": [{"name": "stats", "label": "Stats", "type": "widget"}]}
    client = FakeClient({"/api/query/solutionpacks?$limit=1&$page=1&$search=Stats": members})
    ch = ContentHubSearch(client)
    hit = ch.find_installed_widget("Stats", typed=True)
    assert isinstance(hit, Widget)
    assert hit.label == "Stats"


# -- WorkflowRun via PlaybooksAPI -------------------------------------------
_RUN = {
    "@id": "/api/wf/api/workflows/run-1/",
    "name": "Block IP",
    "status": "finished",
    "modified": "2026-06-08T00:00:00Z",
    "node_name": "node-a",
}


def test_playbooks_runs_typed_returns_workflowrun():
    client = FakeClient(
        {
            "/api/wf/api/workflows/?format=json&limit=20&ordering=-modified"
            "&parent_wf__isnull=True": {"hydra:member": [_RUN]},
        }
    )
    runs = PlaybooksAPI(client).runs(typed=True)
    assert len(runs) == 1
    assert isinstance(runs[0], WorkflowRun)
    assert runs[0].status == "finished"
    assert runs[0].node_name == "node-a"


def test_playbooks_runs_default_returns_shaped_dict():
    client = FakeClient(
        {
            "/api/wf/api/workflows/?format=json&limit=20&ordering=-modified"
            "&parent_wf__isnull=True": {"hydra:member": [_RUN]},
        }
    )
    runs = PlaybooksAPI(client).runs()
    assert isinstance(runs[0], dict)
    assert set(runs[0]) == {
        "task_id",
        "name",
        "status",
        "error_message",
        "modified",
        "uuid",
        "pk",
        "source",
    }


def test_playbooks_get_typed():
    client = FakeClient({"/api/wf/api/workflows/run-1/?format=json": _RUN})
    run = PlaybooksAPI(client).get("run-1", typed=True)
    assert isinstance(run, WorkflowRun)
    assert run.name == "Block IP"
