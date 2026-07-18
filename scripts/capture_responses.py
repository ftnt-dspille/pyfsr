# scripts/capture_responses.py
import json
from pathlib import Path

import tomllib

from pyfsr import FortiSOAR


class ResponseCapture:
    def __init__(self, config_path="tests/config.toml"):
        # Load config
        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        # Initialize FortiSOAR client
        self.client = FortiSOAR(
            base_url=config["fortisoar"]["base_url"],
            username=config["fortisoar"]["username"],
            password=config["fortisoar"]["password"],
            verify_ssl=config["fortisoar"].get("verify_ssl", True),
            suppress_insecure_warnings=True,
        )

        # Create output directory
        self.output_dir = Path(__file__).parent.parent / "tests" / "resources" / "mock_responses"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_response(self, filename, data):
        """Save response data as JSON file"""
        filepath = self.output_dir / filename
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
        print(f"Saved response to {filepath}")

    def capture_picklists(self):
        """Capture picklist responses"""
        # Get alert severity picklist
        severity_response = self.client.get("/api/3/picklists", params={"listName__name": "Severity"})
        self.save_response("alert_severity_picklist.json", severity_response)

        # Get alert status picklist
        status_response = self.client.get("/api/3/picklists", params={"listName__name": "AlertStatus"})
        self.save_response("alert_status_picklist.json", status_response)

    def capture_alert_responses(self):
        """Capture alert-related responses"""
        # Create test alert
        alert_data = {
            "name": "Response Capture Test Alert",
            "description": "Test alert for capturing responses",
            "severity": "/api/3/picklists/58d0753f-f7e4-403b-953c-b0f521eab759",  # High
        }

        # Capture create response
        create_response = self.client.alerts.create(**alert_data)
        self.save_response("alert_create_response.json", create_response)

        alert_id = create_response["@id"].split("/")[-1]

        # Capture get response
        get_response = self.client.alerts.get(alert_id)
        self.save_response("alert_get_response.json", get_response)

        # Capture list response
        list_response = self.client.alerts.list({"name": alert_data["name"]})
        self.save_response("alert_list_response.json", list_response)

        # Cleanup
        self.client.alerts.delete(alert_id)

    def capture_export_responses(self):
        """Capture export-related responses"""
        # Create export template (typed ExportTemplate back)
        template = self.client.export_templates.create(
            "Response Capture Template",
            options={
                "modules": ["alerts"],
                "picklistNames": ["/api/3/picklist_names/alert-severity"],
            },
        )
        self.save_response("export_template_response.json", template.to_dict(by_alias=True))

    def capture_module_admin_responses(self):
        """Capture module-admin (staging/publish) read-only REST envelopes.

        These back the doctested return examples for ``client.modules_admin``.
        Write-op shapes (``create_module`` / ``add_field`` / ``publish``) are
        appliance-wide or staging-mutating, so they stay illustrative in
        ``module-admin.md``; only read-only shapes are captured here. Trim each
        raw file into a ``*_RESPONSE`` constant in
        ``src/pyfsr/_testing/client_captures.py`` and register it in ``_FIXTURES``
        (``src/pyfsr/_testing/replay_http.py``); extend ``_path_and_match`` if a
        path has a volatile segment (module IRI / uuid) to collapse.
        """
        # Raw collection envelopes (dicts — json.dump-friendly).
        self.save_response(
            "module_admin_staging_list.json",
            self.client.get("/api/3/staging_model_metadatas"),
        )
        self.save_response(
            "module_admin_published_list.json",
            self.client.get("/api/3/model_metadatas"),
        )
        # /api/publish/error returns HTTP 400 + a usable body when there are
        # pending changes; capture whatever the box returns (best-effort).
        try:
            self.save_response(
                "module_admin_publish_error.json",
                self.client.get("/api/publish/error"),
            )
        except Exception as e:  # noqa: BLE001 - capture is best-effort
            print(f"  (skipped /api/publish/error: {e})")

    def capture_rbac_and_config_reads(self):
        """Capture read-only RBAC / config collection envelopes.

        All GET/POST-list reads (no mutation, no cleanup) for endpoints that have
        **no replay fixture yet**, so their ``list()``/``get()`` docstring examples
        can become real offline doctests. After running against a live box, trim
        each raw file into a ``*_RESPONSE`` constant in
        ``src/pyfsr/_testing/client_captures.py`` and register it in ``_FIXTURES``
        (``src/pyfsr/_testing/replay_http.py``); collapse any volatile uuid segment
        in ``_path_and_match``. Scrub member records to placeholder names — no real
        user emails, team names, or lab host data in tracked fixtures.

        File -> intended fixture constant / endpoint:
            roles_list.json                -> ROLES_LIST_RESPONSE          GET  /api/3/roles
            teams_list.json                -> TEAMS_LIST_RESPONSE          GET  /api/3/teams
            people_list.json               -> PEOPLE_LIST_RESPONSE         GET  /api/3/people
            tags_list.json                 -> TAGS_LIST_RESPONSE           GET  /api/3/tags
            comments_list.json             -> COMMENTS_LIST_RESPONSE       GET  /api/3/comments
            reports_list.json              -> REPORTS_LIST_RESPONSE        GET  /api/3/reporting
            routers_list.json              -> ROUTERS_LIST_RESPONSE        GET  /api/3/routers
            preprocessing_rules_list.json  -> PREPROCESSING_RULES_RESPONSE GET  /api/3/preprocessing_rules
            notifications_list.json        -> NOTIFICATIONS_LIST_RESPONSE
                POST /api/rule/api/system-notification/notifications/
        """
        reads: list[tuple[str, str, str, dict]] = [
            ("roles_list.json", "GET", "/api/3/roles", {}),
            ("teams_list.json", "GET", "/api/3/teams", {}),
            ("people_list.json", "GET", "/api/3/people", {"$limit": 5}),
            ("tags_list.json", "GET", "/api/3/tags", {"$limit": 20}),
            ("comments_list.json", "GET", "/api/3/comments", {"$limit": 5}),
            ("reports_list.json", "GET", "/api/3/reporting", {"$limit": 10}),
            ("routers_list.json", "GET", "/api/3/routers", {"$limit": 20}),
            ("preprocessing_rules_list.json", "GET", "/api/3/preprocessing_rules", {"$limit": 10}),
            (
                "notifications_list.json",
                "POST",
                "/api/rule/api/system-notification/notifications/",
                {"$limit": 5},
            ),
        ]
        for filename, method, endpoint, params in reads:
            try:
                if method == "GET":
                    body = self.client.get(endpoint, params=params)
                else:
                    body = self.client.post(endpoint, params=params)
                self.save_response(filename, body)
            except Exception as e:  # noqa: BLE001 - capture is best-effort per endpoint
                print(f"  (skipped {endpoint}: {e})")

    def capture_all(self):
        """Capture all response types"""
        print("Capturing picklist responses...")
        self.capture_picklists()

        print("\nCapturing alert responses...")
        self.capture_alert_responses()

        print("\nCapturing export responses...")
        self.capture_export_responses()

        print("\nCapturing module-admin responses...")
        self.capture_module_admin_responses()

        print("\nCapturing RBAC / config read-only responses...")
        self.capture_rbac_and_config_reads()


if __name__ == "__main__":
    capture = ResponseCapture()
    capture.capture_all()
