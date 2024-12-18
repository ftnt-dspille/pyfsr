# noinspection PyCompatibility
import tomllib
from pyfsr import FortiSOAR

# Load config
with open("config.toml", "rb") as f:
    config = tomllib.load(f)

# Initialize client with config settings
client = FortiSOAR(
    base_url=config["fortisoar"]["base_url"],
    auth=(
        config["fortisoar"]["auth"]["username"],
        config["fortisoar"]["auth"]["password"]
    ),
    verify_ssl=config["fortisoar"].get("verify_ssl", True),
    supress_insecure_warnings=True

)

alerts = client.alerts.get_all()
print(alerts)
