# scripts/capture_responses.py
import json
import tomllib
from pathlib import Path

from pyfsr import FortiSOAR


class ResponseCapture:
    def __init__(self, config_path="tests/config.toml"):
        # Load config
        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        # Initialize FortiSOAR client
        self.client = FortiSOAR(
            base_url=config["fortisoar"]["base_url"],
            auth=(
                config["fortisoar"]["username"],
                config["fortisoar"]["password"]
            ),
            verify_ssl=config["fortisoar"].get("verify_ssl", True),
            suppress_insecure_warnings=True
        )

        # Create output directory
        self.output_dir = Path(__file__).parent.parent / "tests" / "resources" / "mock_responses"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_response(self, filename, data):
        """Save response data as JSON file"""
        filepath = self.output_dir / filename
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"Saved response to {filepath}")

    def capture_picklists(self):
        """Capture picklist responses"""
        # Get alert severity picklist
        severity_response = self.client.get('/api/3/picklists', params={
            'listName__name': 'Severity'
        })
        self.save_response('alert_severity_picklist.json', severity_response)

        # Get alert status picklist
        status_response = self.client.get('/api/3/picklists', params={
            'listName__name': 'AlertStatus'
        })
        self.save_response('alert_status_picklist.json', status_response)

    def capture_alert_responses(self):
        """Capture alert-related responses"""
        # Create test alert
        alert_data = {
            "name": "Response Capture Test Alert",
            "description": "Test alert for capturing responses",
            "severity": "/api/3/picklists/58d0753f-f7e4-403b-953c-b0f521eab759"  # High
        }

        # Capture create response
        create_response = self.client.alerts.create(**alert_data)
        self.save_response('alert_create_response.json', create_response)

        alert_id = create_response["@id"].split("/")[-1]

        # Capture get response
        get_response = self.client.alerts.get(alert_id)
        self.save_response('alert_get_response.json', get_response)

        # Capture list response
        list_response = self.client.alerts.list({"name": alert_data["name"]})
        self.save_response('alert_list_response.json', list_response)

        # Cleanup
        self.client.alerts.delete(alert_id)

    def capture_export_responses(self):
        """Capture export-related responses"""
        # Create export template
        template_data = {
            "name": "Response Capture Template",
            "options": {
                "modules": ["alerts"],
                "picklistNames": ["/api/3/picklist_names/alert-severity"]
            }
        }
        template_response = self.client.post('/api/3/export_templates', data=template_data)
        self.save_response('export_template_response.json', template_response)

    def capture_all(self):
        """Capture all response types"""
        print("Capturing picklist responses...")
        self.capture_picklists()

        print("\nCapturing alert responses...")
        self.capture_alert_responses()

        print("\nCapturing export responses...")
        self.capture_export_responses()


if __name__ == "__main__":
    capture = ResponseCapture()
    capture.capture_all()
