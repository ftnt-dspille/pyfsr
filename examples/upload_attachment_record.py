#### Example create attachgment Record with a File in FortiSOAR ####
# This script uploads an attachment to a record in FortiSOAR.
# First we need to upload the file to SOAR, then we can link the file to the attachment.


from pyfsr import FortiSOAR

# Initialize the client straight from config.toml (the [fortisoar] layout).
client = FortiSOAR.from_config_file("config.toml", suppress_insecure_warnings=True)
file_name = "sample_csv.csv"

# Upload the file AND create its attachment record in one call.
attachment_record = client.attachments.create_from_file(file_name, description="Sample CSV file")

print(attachment_record)
