from pyfsr import FortiSOAR

# Initialize the client straight from config.toml (the [fortisoar] layout).
client = FortiSOAR.from_config_file("config.toml", suppress_insecure_warnings=True)
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
