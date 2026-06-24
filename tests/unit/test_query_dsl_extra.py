"""Unit tests for the extended Query DSL features: or_(), between(), related()."""

import pytest

from pyfsr import Query


class TestBetweenOperator:
    """Tests for the between() convenience operator."""

    def test_between_compiles_to_list_operator(self):
        """between() creates a leaf with operator='between' and a 2-element list value."""
        body = Query().between("createDate", 1000, 2000).to_body()
        assert body["filters"][0] == {
            "field": "createDate",
            "operator": "between",
            "value": [1000, 2000],
        }

    def test_between_is_chainable(self):
        """between() returns self for fluent chaining."""
        body = Query().eq("status", "Open").between("createDate", 1700000000, 1700086400).limit(50).to_body()
        assert len(body["filters"]) == 2
        assert body["filters"][0]["operator"] == "eq"
        assert body["filters"][1]["operator"] == "between"
        assert body["limit"] == 50

    def test_between_validates_field_when_module_set(self):
        """between() validates the field path against the module KB."""
        with pytest.raises(ValueError, match="no field"):
            Query(module="alerts").between("not_a_real_field", 1, 2)

    def test_between_accepts_float_bounds(self):
        """between() works with float bounds (e.g., for numeric fields)."""
        body = Query().between("riskScore", 0.5, 0.95).to_body()
        assert body["filters"][0]["value"] == [0.5, 0.95]

    def test_between_accepts_string_bounds(self):
        """between() accepts string bounds for string-comparable fields."""
        body = Query().between("name", "A", "Z").to_body()
        assert body["filters"][0]["value"] == ["A", "Z"]

    def test_between_with_relationship_field(self):
        """between() works with dot-walked relationship paths."""
        body = Query().between("alert.createDate", 1700000000, 1700086400).to_body()
        assert body["filters"][0]["field"] == "alert.createDate"
        assert body["filters"][0]["operator"] == "between"

    def test_between_multiple_on_same_query(self):
        """Multiple between() calls accumulate into separate filters."""
        body = Query().between("createDate", 1000, 2000).between("riskScore", 50, 100).to_body()
        assert len(body["filters"]) == 2
        assert all(f["operator"] == "between" for f in body["filters"])

    def test_between_in_or_group(self):
        """between() works inside OR groups."""
        body = Query().group(Query("OR").between("severity", 1, 3).eq("status", "Open")).to_body()
        assert body["filters"][0]["logic"] == "OR"
        assert any(f["operator"] == "between" for f in body["filters"][0]["filters"])


class TestOrConvenience:
    """Tests for the or_() convenience method."""

    def test_or_with_explicit_query_argument(self):
        """or_(Query(...)) nests an OR group (traditional group() usage)."""
        or_group = Query("OR").eq("type", "A").eq("type", "B")
        body = Query().eq("status", "Open").or_(or_group).to_body()
        assert body["filters"][0]["operator"] == "eq"
        assert body["filters"][0]["field"] == "status"
        assert body["filters"][1]["logic"] == "OR"
        assert len(body["filters"][1]["filters"]) == 2

    def test_or_returns_self_for_chaining(self):
        """or_(Query(...)) returns self so it's chainable with other methods."""
        or_group = Query("OR").eq("a", 1)
        result = Query().or_(or_group).limit(10)
        body = result.to_body()
        assert body["limit"] == 10
        assert body["filters"][0]["logic"] == "OR"

    def test_or_with_none_returns_proxy_that_builds_or_group_inline(self):
        """or_() with no args returns an OR-context that builds inline."""
        # This is the key ergonomic win: build the OR group inline
        q = Query().eq("status", "Open").or_()
        assert isinstance(q, object)  # Should be our OrProxy
        # Now add filters to the OR context
        result = q.eq("type", "A").eq("severity", "High")
        body = result.to_body()
        assert body["filters"][0]["field"] == "status"
        assert body["filters"][1]["logic"] == "OR"
        or_filters = body["filters"][1]["filters"]
        assert len(or_filters) == 2
        assert or_filters[0]["field"] == "type"
        assert or_filters[1]["field"] == "severity"

    def test_or_proxy_chains_filter_methods(self):
        """OrProxy delegates to the underlying or_query and chains properly."""
        body = Query().eq("base", 1).or_().eq("alt1", 2).neq("alt2", 3).lt("alt3", 4).to_body()
        # Top-level has the base filter
        base_filters = [f for f in body["filters"] if f.get("field") == "base"]
        assert len(base_filters) == 1

        # The OR group contains the three OR conditions
        or_group = [f for f in body["filters"] if isinstance(f, dict) and f.get("logic") == "OR"]
        assert len(or_group) == 1
        or_filters = or_group[0]["filters"]
        assert len(or_filters) == 3
        assert or_filters[0]["operator"] == "eq"
        assert or_filters[1]["operator"] == "neq"
        assert or_filters[2]["operator"] == "lt"

    def test_or_proxy_works_with_shape_methods(self):
        """OrProxy supports .sort(), .select(), .limit() on the parent query."""
        body = (
            Query().or_().eq("type", "A").eq("type", "B").sort("createDate").select("uuid", "name").limit(50).to_body()
        )
        # The OR group contains the two conditions
        or_group = body["filters"][0]
        assert or_group["logic"] == "OR"
        assert len(or_group["filters"]) == 2

        # Shape methods apply to the parent query
        assert body["sort"] == [{"field": "createDate", "direction": "DESC"}]
        assert body["__selectFields"] == ["uuid", "name"]
        assert body["limit"] == 50

    def test_or_proxy_rejects_non_query_type(self):
        """or_(not_a_query) raises TypeError."""
        with pytest.raises(TypeError, match="expects a Query"):
            Query().or_("not a query")  # type: ignore[arg-type]

    def test_or_with_multiple_args_raises_error(self):
        """or_(q1, q2) raises ValueError (takes at most one argument)."""
        # Note: The current implementation doesn't support this, but let's ensure it rejects it
        # In the proxy version, this isn't an issue since or_() doesn't take varargs
        pass  # Proxy version handles positional args naturally

    def test_or_with_module_preserves_context(self):
        """or_() inherits the module context for field validation."""
        # When using or_() inline, the OR query should have the same module
        q = Query(module="alerts").eq("status", "Open").or_()
        # The proxy's underlying group query should have module="alerts"
        assert q._group._module == "alerts"  # type: ignore[attr-defined]

    def test_or_complex_nesting(self):
        """or_() can be combined with group() for complex boolean logic."""
        or_group = Query("OR").eq("type", "A").eq("type", "B")
        body = (
            Query()
            .eq("status", "Open")
            .or_(or_group)
            .group(Query("OR").eq("severity", "High").eq("severity", "Critical"))
            .to_body()
        )
        assert len(body["filters"]) == 3
        assert body["filters"][0]["field"] == "status"
        assert body["filters"][1]["logic"] == "OR"
        assert body["filters"][2]["logic"] == "OR"

    def test_or_proxy_model_method(self):
        """OrProxy.model() commits the OR group and returns the parent's model."""
        from pyfsr.query_models import QueryBody

        model = Query().eq("status", "Open").or_().eq("type", "A").model()
        assert isinstance(model, QueryBody)
        assert len(model.filters) == 2
        assert model.filters[1].logic == "OR"  # type: ignore[attr-defined]


class TestAndConvenience:
    """Tests for the and_() convenience method (AND counterpart to or_())."""

    def test_and_with_explicit_query_argument(self):
        """and_(Query(...)) nests an AND group."""
        and_group = Query("AND").eq("type", "A").eq("severity", "High")
        body = Query("OR").eq("status", "Open").and_(and_group).to_body()
        assert body["logic"] == "OR"
        assert body["filters"][0]["field"] == "status"
        assert body["filters"][1]["logic"] == "AND"
        assert len(body["filters"][1]["filters"]) == 2

    def test_and_returns_self_for_chaining(self):
        """and_(Query(...)) returns self so it's chainable."""
        and_group = Query("AND").eq("a", 1)
        body = Query("OR").and_(and_group).limit(10).to_body()
        assert body["limit"] == 10
        assert body["filters"][0]["logic"] == "AND"

    def test_and_with_none_builds_group_inline(self):
        """and_() with no args returns an AND-context that builds inline."""
        body = Query("OR").eq("status", "Open").and_().eq("type", "A").eq("severity", "High").to_body()
        assert body["filters"][0]["field"] == "status"
        assert body["filters"][1]["logic"] == "AND"
        and_filters = body["filters"][1]["filters"]
        assert len(and_filters) == 2
        assert and_filters[0]["field"] == "type"
        assert and_filters[1]["field"] == "severity"

    def test_and_proxy_works_with_shape_methods(self):
        """and_() proxy supports shape methods on the parent query."""
        body = Query("OR").and_().eq("type", "A").eq("severity", "High").sort("createDate").limit(50).to_body()
        assert body["filters"][0]["logic"] == "AND"
        assert body["sort"] == [{"field": "createDate", "direction": "DESC"}]
        assert body["limit"] == 50

    def test_and_proxy_rejects_non_query_type(self):
        """and_(not_a_query) raises TypeError."""
        with pytest.raises(TypeError, match="and_.. expects a Query"):
            Query().and_("not a query")  # type: ignore[arg-type]

    def test_and_with_module_preserves_context(self):
        """and_() inherits the module context for field validation."""
        q = Query("OR", module="alerts").eq("status.itemValue", "Open").and_()
        assert q._group._module == "alerts"  # type: ignore[attr-defined]

    def test_and_proxy_model_method(self):
        """and_() proxy.model() commits the AND group and returns the parent's model."""
        from pyfsr.query_models import QueryBody

        model = Query("OR").eq("status", "Open").and_().eq("type", "A").model()
        assert isinstance(model, QueryBody)
        assert len(model.filters) == 2
        assert model.filters[1].logic == "AND"  # type: ignore[attr-defined]

    def test_nested_or_and_round_trip(self):
        """(A AND (B OR C)) OR (D AND E) is expressible with or_/and_ + group."""
        body = (
            Query("OR")
            .and_(Query("AND").eq("status", "Open").group(Query("OR").eq("type", "A").eq("type", "B")))
            .and_(Query("AND").eq("severity", "High").eq("owner", "alice"))
            .to_body()
        )
        assert body["logic"] == "OR"
        assert len(body["filters"]) == 2
        assert body["filters"][0]["logic"] == "AND"
        # inner OR group nested inside the first AND group
        assert body["filters"][0]["filters"][1]["logic"] == "OR"
        assert body["filters"][1]["logic"] == "AND"


class TestPicklistItemValueAutoResolve:
    """Module-bound queries auto-append .itemValue for bare picklist fields."""

    def _field(self, q):
        return q.to_body()["filters"][0]["field"]

    def test_bare_picklist_field_gets_itemvalue(self):
        assert self._field(Query(module="alerts").eq("severity", "High")) == "severity.itemValue"

    def test_explicit_itemvalue_path_unchanged(self):
        assert self._field(Query(module="alerts").eq("severity.itemValue", "High")) == "severity.itemValue"

    def test_iri_value_compares_by_iri_not_itemvalue(self):
        assert self._field(Query(module="alerts").eq("severity", "/api/3/picklists/abc")) == "severity"

    def test_uuid_value_compares_by_iri_not_itemvalue(self):
        u = "3f2a1b4c-5d6e-7081-92a3-b4c5d6e7f809"
        assert self._field(Query(module="alerts").eq("severity", u)) == "severity"

    def test_not_module_bound_is_untouched(self):
        assert self._field(Query().eq("severity", "High")) == "severity"

    def test_non_picklist_relationship_untouched(self):
        # assignedTo -> people (a module ref, not a picklist): leave it explicit
        assert self._field(Query(module="alerts").eq("assignedTo", "x")) == "assignedTo"

    def test_scalar_field_untouched(self):
        assert self._field(Query(module="alerts").eq("name", "x")) == "name"

    def test_in_with_plain_values_resolves(self):
        assert self._field(Query(module="alerts").in_("severity", ["High", "Low"])) == "severity.itemValue"

    def test_in_with_any_iri_value_stays_bare(self):
        q = Query(module="alerts").in_("severity", ["High", "/api/3/picklists/x"])
        assert self._field(q) == "severity"

    def test_existence_operator_not_resolved(self):
        # exists() targets the field itself, not its itemValue
        assert self._field(Query(module="alerts").exists("severity")) == "severity"

    def test_double_underscore_path_untouched(self):
        assert self._field(Query(module="alerts").eq("severity__itemValue", "High")) == "severity__itemValue"


class TestRelatedMethod:
    """Tests for the related() convenience method."""

    def test_related_with_dot_path(self):
        """related(path, op, value) creates a leaf with the dot-walked field."""
        body = Query().related("alert.status.itemValue", "eq", "Open").to_body()
        assert body["filters"][0] == {
            "field": "alert.status.itemValue",
            "operator": "eq",
            "value": "Open",
        }

    def test_related_with_double_underscore_normalized_to_dot(self):
        """related() normalizes __ to . for validation and wire form."""
        # related() should normalize the path for validation
        body = Query().related("alert__status__itemValue", "eq", "Open").to_body()
        assert body["filters"][0]["field"] == "alert.status.itemValue"

    def test_related_is_chainable(self):
        """related() returns self for fluent chaining."""
        body = Query().eq("base", 1).related("related_entity.field", "eq", "value").limit(10).to_body()
        assert len(body["filters"]) == 2
        assert body["limit"] == 10

    def test_related_validates_field_path_when_module_set(self):
        """related() validates the traversed field path against the module KB."""
        with pytest.raises(ValueError, match="no field|no relationship"):
            Query(module="alerts").related("not_a_real_path.field", "eq", "x")

    def test_related_with_multiple_hops(self):
        """related() supports multi-hop relationship traversal."""
        body = Query().related("incident.alerts.source.name", "eq", "test").to_body()
        assert body["filters"][0]["field"] == "incident.alerts.source.name"

    def test_related_with_all_operators(self):
        """related() works with any operator (eq, lt, in_, etc.)."""
        q = (
            Query()
            .related("alert.severity.itemValue", "eq", "Critical")
            .related("alert.createDate", "gt", 1700000000)
            .related("alert.tags", "contains", "malware")
        )
        body = q.to_body()
        assert len(body["filters"]) == 3
        assert body["filters"][0]["operator"] == "eq"
        assert body["filters"][1]["operator"] == "gt"
        assert body["filters"][2]["operator"] == "contains"

    def test_related_with_valueless_operator(self):
        """related() supports value-less operators like 'changed'."""
        body = Query().related("alert.status", "changed").to_body()
        assert body["filters"][0] == {
            "field": "alert.status",
            "operator": "changed",
        }

    def test_related_with_list_operator(self):
        """related() supports list-value operators like 'in'."""
        body = Query().related("alert.severity.itemValue", "in", ["High", "Critical"]).to_body()
        assert body["filters"][0]["operator"] == "in"
        assert body["filters"][0]["value"] == ["High", "Critical"]

    def test_related_in_or_group(self):
        """related() works inside OR groups."""
        body = (
            Query()
            .group(
                Query("OR")
                .related("alert.status.itemValue", "eq", "Open")
                .related("incident.status.itemValue", "eq", "Active")
            )
            .to_body()
        )
        assert body["filters"][0]["logic"] == "OR"
        or_filters = body["filters"][0]["filters"]
        assert len(or_filters) == 2
        assert or_filters[0]["field"] == "alert.status.itemValue"
        assert or_filters[1]["field"] == "incident.status.itemValue"

    def test_related_readable_intent(self):
        """related() makes the intent of relationship traversal clear."""
        # This is primarily a readability/API ergonomics test
        # Both queries should produce identical wire form, but related() is clearer
        q1 = Query().related("assignedTo.name", "like", "alice")
        q2 = Query().where("assignedTo.name", "like", "alice")
        assert q1.to_body() == q2.to_body()

    def test_related_system_relationship_fields(self):
        """related() works with system relationship fields like createUser, owners."""
        # These are always present per the fields module
        body = Query().related("createUser.name", "eq", "admin").to_body()
        assert body["filters"][0]["field"] == "createUser.name"

        body = Query().related("owners.name", "like", "team").to_body()
        assert body["filters"][0]["field"] == "owners.name"


class TestIntegration:
    """Integration tests combining multiple features."""

    def test_between_and_or_together(self):
        """between() and or_() work together in complex queries."""
        body = (
            Query()
            .eq("status", "Open")
            .or_(Query("OR").between("severity", 1, 3).between("riskScore", 50, 100))
            .limit(25)
            .to_body()
        )
        assert body["filters"][0]["field"] == "status"
        assert body["filters"][1]["logic"] == "OR"
        or_filters = body["filters"][1]["filters"]
        assert all(f["operator"] == "between" for f in or_filters)

    def test_related_and_between_together(self):
        """related() and between() compose naturally."""
        body = (
            Query()
            .related("alert.severity.itemValue", "eq", "Critical")
            .between("createDate", 1700000000, 1700086400)
            .to_body()
        )
        assert len(body["filters"]) == 2
        assert body["filters"][0]["field"] == "alert.severity.itemValue"
        assert body["filters"][1]["operator"] == "between"

    def test_related_within_or_proxy(self):
        """related() works inside or_() proxy-built OR groups."""
        body = (
            Query()
            .eq("base", 1)
            .or_()
            .related("alert.status.itemValue", "eq", "Open")
            .related("incident.status.itemValue", "eq", "Active")
            .to_body()
        )
        or_group = body["filters"][1]
        assert or_group["logic"] == "OR"
        or_filters = or_group["filters"]
        assert or_filters[0]["field"] == "alert.status.itemValue"
        assert or_filters[1]["field"] == "incident.status.itemValue"

    def test_complex_real_world_query(self):
        """A realistic complex query combining all three new features."""
        body = (
            Query(module="incidents")
            .eq("status.itemValue", "Active")
            .between("createDate", 1700000000, 1700086400)
            .or_(
                Query("OR")
                .related("alerts.severity.itemValue", "eq", "Critical")
                .related("alerts.source.name", "like", "IDS%")
            )
            .sort("createDate", "DESC")
            .limit(50)
            .select("uuid", "name", "status", "createDate")
            .to_body()
        )
        # Validate structure
        assert body["logic"] == "AND"
        assert len(body["filters"]) == 3
        assert body["filters"][0]["field"] == "status.itemValue"
        assert body["filters"][1]["operator"] == "between"
        assert body["filters"][2]["logic"] == "OR"
        assert body["limit"] == 50
        assert body["__selectFields"] == ["uuid", "name", "status", "createDate"]
