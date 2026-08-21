# Finding Playbooks

Answering "what runs when an asset is created?" or "which playbooks block an IP
on FortiGate?" against an appliance carrying a couple of thousand playbooks.

Two layers do this, and picking the right one is most of the work:

| | Where it runs | Use it for |
|---|---|---|
| {meth}`~pyfsr.api.playbooks.PlaybooksAPI.find` | Server | Trigger kind, module, step type, connector, tag, collection |
| {mod}`pyfsr.playbook_match` | Client | Same-step precision, counts, parent/child joins |

```{tip}
Reach for `find()` first. Pulling every playbook with `$relationships=true` costs
about **29 MB and 100 s** on a 1.8k-playbook appliance; the equivalent `find()`
call answers in well under a second. Only fall through to `playbook_match` for
the questions the filter language genuinely cannot express -- and even then, pass
a `prefilter` so the server still does the narrowing.
```

## Server-side search

{meth}`~pyfsr.api.playbooks.PlaybooksAPI.find` maps onto FortiSOAR's
deep-relationship filter params. Every argument is optional and all are ANDed.

```python
from pyfsr import FortiSOAR

client = FortiSOAR("https://your-fsr", token="...")

# What fires when an asset is created?
client.playbooks.find(trigger_type="on_create", trigger_module="assets")

# Playbooks that both set a variable and call FortiGate
client.playbooks.find(step_type="set_variable", uses_connector="fortigate")

# Everything on a collection, active only
client.playbooks.find(collection="<uuid>", active=True)
```

### Triggers

`trigger_type` takes a friendly alias from `TRIGGER_TYPE_NAMES` -- `manual`,
`on_create`, `on_update`, `referenced`, `api_endpoint` -- or a raw
`cybersponse.*` name. The trigger kind *is* a step type on the start step, so
this filters exactly.

`trigger_module` scopes it to the module the trigger is bound to:

```python
client.playbooks.find(trigger_type="on_update", trigger_module="alerts")
```

The bound module lives in the trigger step's own `arguments.resources`, which is
a different JSON column from `steps.arguments` -- so `trigger_module` composes
freely with `uses_connector` and friends. The value is matched quoted, so
`assets` does not also match `asset_change_activities`.

For manual triggers specifically,
{meth}`~pyfsr.api.playbooks.PlaybooksAPI.manual_on_module` additionally returns
each playbook's Execute-menu button label:

```python
for pb in client.playbooks.manual_on_module("alerts"):
    print(pb["label"], "-", pb["name"])
```

### Step content

`step_type` accepts a friendly alias from `STEP_TYPE_NAMES` (`connector`,
`set_variable`, `decision`, `code_snippet`, `manual_input`, `approval`,
`reference`, …) or a raw engine name.

```{warning}
`uses_connector`, `uses_operation`, `route`, and `references` all match a
**substring of the whole `steps.arguments` JSON**, so they over-match. Measured
on one appliance, `uses_connector="fortigate"` returned 85 playbooks where only
58 had a step actually invoking a FortiGate connector -- a 32% false-positive
rate, from things like HTML in a description, sample event data
(`"eventType": "FortiGate-traffic-forward"`), and text inside a Jinja comment.

They also share one JSON column, so passing more than one per call raises
`ValueError`. When precision matters, filter server-side then refine with
`match()` -- see below.
```

## Client-side structural matching

{mod}`pyfsr.playbook_match` parses each playbook into a `ParsedPlaybook` and
evaluates composable predicates. Use it for the four things `find()` cannot
express:

* **Same-step precision** -- "one step that is *both* FortiGate *and* `block_ip`".
  `find(uses_connector=..., uses_operation=...)` is not even allowed in one call,
  and would match the two facets landing on different steps.
* **Quantities** -- "exactly two set-variable steps".
* **Parent/child joins** -- "a manual playbook whose referenced child blocks an IP".
* **Trigger metadata** -- button labels and bound resources.

Always pass a `prefilter`: it is a `find()`-style param dict applied server-side
before anything is fetched.

```python
from pyfsr.playbook_match import step, has, count, all_of

# Precise: connector AND operation on the SAME step
client.playbooks.match(
    has(step(connector="fortigate", operation="block_ip")),
    prefilter={"steps.arguments$like": "%fortigate%"},
)

# Exactly 2 set-variable steps and at least 1 code snippet
client.playbooks.match(
    all_of(count(step(step_type="set_variable"), n=2),
           count(step(step_type="code_snippet"), min=1)),
)
```

Predicates compose with `all_of`, `any_of`, and `none_of`. Note that
`step(connector="fortigate")` matches case-insensitively as a substring, so it
finds the installed package name `fortigate-firewall`.

### Parent/child joins

{meth}`~pyfsr.api.playbooks.PlaybooksAPI.match_across` finds parents whose
referenced child satisfies a second predicate -- a reference step targets its
child by IRI, not by name:

```python
from pyfsr.playbook_match import trigger

client.playbooks.match_across(
    trigger("manual"),
    has(step(connector="fortigate", operation="block_ip")),
)
```

## Recipes

```python
# Everything bound to a module, by trigger kind
{kind: len(client.playbooks.find(trigger_type=kind, trigger_module="alerts"))
 for kind in ("on_create", "on_update", "manual")}

# Disabled playbooks still wired to a trigger
client.playbooks.find(trigger_type="on_create", active=False)

# Which playbooks reference this one as a child
client.playbooks.find(references="> get outbound traffic report")

# API-triggered playbooks and their routes
client.playbooks.find(trigger_type="api_endpoint")
```

```{seealso}
{doc}`playbook-authoring` for creating playbooks, and {doc}`querying` for the
underlying query builder used by `prefilter`.
```
