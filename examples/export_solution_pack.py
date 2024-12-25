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
    suppress_insecure_warnings=True

)
#
# # Find installed solution pack
# pack = client.solution_packs.find_installed_pack("SOAR Framework")
# if pack:
#     print(f"Found installed pack: {pack['label']}")
# else:
#     print("Solution pack not found")
#

# Export solution pack
output_path = client.solution_packs.export_pack("FortiManager ZTP Flow", "ztp_framework_export.zip")