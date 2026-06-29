from pyfsr import FortiSOAR

# Initialize the client straight from config.toml (the [fortisoar] layout).
client = FortiSOAR.from_config_file("config.toml", suppress_insecure_warnings=True)

alerts = client.alerts.list()
print(alerts)
