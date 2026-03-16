"""
Run this once to generate .streamlit/secrets.toml from your credentials JSON.
  python setup_secrets.py
"""
import json
from pathlib import Path

BASE = Path(__file__).parent

# Find credentials JSON (any .json file that isn't secrets)
json_files = [f for f in BASE.glob("*.json") if "secret" not in f.name.lower()]
if not json_files:
    print("❌ No credentials JSON file found in this folder.")
    exit(1)

creds_file = json_files[0]
print(f"Reading: {creds_file.name}")

with open(creds_file) as f:
    creds = json.load(f)

# Escape newlines in private key for TOML
private_key = creds.get("private_key", "").replace("\n", "\\n")

secrets_content = f'''[auth]
username = "REDACTED"
password = "REDACTED"

[gcp_service_account]
type = "{creds.get('type', 'service_account')}"
project_id = "{creds.get('project_id', '')}"
private_key_id = "{creds.get('private_key_id', '')}"
private_key = "{private_key}"
client_email = "{creds.get('client_email', '')}"
client_id = "{creds.get('client_id', '')}"
auth_uri = "{creds.get('auth_uri', 'https://accounts.google.com/o/oauth2/auth')}"
token_uri = "{creds.get('token_uri', 'https://oauth2.googleapis.com/token')}"
auth_provider_x509_cert_url = "{creds.get('auth_provider_x509_cert_url', 'https://www.googleapis.com/oauth2/v1/certs')}"
client_x509_cert_url = "{creds.get('client_x509_cert_url', '')}"
'''

secrets_dir = BASE / ".streamlit"
secrets_dir.mkdir(exist_ok=True)
secrets_path = secrets_dir / "secrets.toml"
secrets_path.write_text(secrets_content)

print(f"Done. Created: {secrets_path}")
print()
print("Next steps:")
print("  1. Run the app locally:  streamlit run app.py")
print("  2. To deploy online: paste the same secrets into Streamlit Cloud > App settings > Secrets")
