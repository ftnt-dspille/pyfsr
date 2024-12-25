#### Example create attachgment Record with a File in FortiSOAR ####
# This script uploads an attachment to a record in FortiSOAR.
# First we need to upload the file to SOAR, then we can link the file to the attachment.



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
    suppress_insecure_warnings=True

)
file_name = "sample_csv.csv"

file_record = client.files.upload(file_name)

print(file_record)

attachment_data = {
    "name": file_name,
    "file": file_record["@id"],
    "description": "Sample CSV file"
}

attachment_record = client.post("/api/3/attachments", data=attachment_data)

print(attachment_record)

