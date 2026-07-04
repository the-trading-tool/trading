#### key pair (user/passwd) and url lib

import os, sys
import json
from cryptography.fernet import Fernet

try:
	sys.path.insert(0, "../tradinglib")
except ImportError:
	pass
 
from tradinglib import tools

class Ksp(tools.Tools):

    def __init__(self, storage_path="database", secrets_path="database"):
        """Load or generate the Fernet encryption key and decrypt the credentials file."""
        self.storage_path = self.get_path( path=storage_path, file_name='') 
        self.secrets_path = self.get_path( path=secrets_path, file_name='') 
        self.key_file = os.path.join(self.secrets_path, "secret.key") 
        self.data_file = os.path.join(self.storage_path, "credentials.json") 

        # Load or generate key
        self.key = self._load_or_create_key()
        self.fernet = Fernet(self.key)

        # Load credentials
        self.credentials = self._load_credentials()

    def _load_or_create_key(self):
        """Load the Fernet key from secret.key, or generate and save a new one."""
        if os.path.exists(self.key_file):
            with open(self.key_file, "rb") as keyfile:
                return keyfile.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, "wb") as keyfile:
                keyfile.write(key)
            return key

    def _load_credentials(self):
        """Decrypt and return the credentials dict from credentials.json, or {} when absent."""
        if os.path.exists(self.data_file):
            with open(self.data_file, "rb") as file:
                encrypted_data = file.read()
                decrypted_data = self.fernet.decrypt(encrypted_data)
                return json.loads(decrypted_data)
        return {}

    def _get_all_credentials(self):
        """Return the full credentials dict (all stored API entries)."""
        return self.credentials

    def _save_credentials(self):
        """Encrypt the current credentials dict and write it to credentials.json."""
        encrypted_data = self.fernet.encrypt(json.dumps(self.credentials).encode())
        with open(self.data_file, "wb") as file:
            file.write(encrypted_data)

    def add_ksp(self, api, user, password, url):
        """Add or overwrite an API credential entry and persist the change."""
        self.credentials[api] = {"user": user, "password": password, "url": url}
        self._save_credentials()

    def get_ksp(self, api):
        """Return the stored credential dict for api, or (None, None, None) when not found."""
        return self.credentials.get(api, (None, None, None))

    def delete_ksp(self, api):
        """Remove the credential entry for api and persist the change."""
        if api in self.credentials:
            del self.credentials[api]
            self._save_credentials()

# Example usage:
if __name__ == "__main__":

    pass

