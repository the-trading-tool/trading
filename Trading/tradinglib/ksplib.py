#### key pair (user/passwd) and url lib

import os, sys
import json
from cryptography.fernet import Fernet

try:
	sys.path.insert(0, "../tradinglib")
except ImportError:
	print('No Import')
 
from tradinglib import tools

class Ksp(tools.Tools):

    def __init__(self, storage_path="database", secrets_path = "database"):
        """Initialisiert den Speicherort und lädt den Verschlüsselungsschlüssel."""
        self.storage_path = self.get_path( path=storage_path, file_name='') 
        self.secrets_path = self.get_path( path=secrets_path, file_name='') 
#        print(self.secrets_path)
#        self.secrets_path = './' 
        self.key_file = os.path.join(self.secrets_path, "secret.key") 
        self.data_file = os.path.join(self.storage_path, "credentials.json") 

        # Schlüssel laden oder generieren
        self.key = self._load_or_create_key()
        self.fernet = Fernet(self.key)

        # Zugangsdaten laden
        self.credentials = self._load_credentials()

    def _load_or_create_key(self):
        """Lädt den Verschlüsselungsschlüssel oder erstellt ihn neu."""
        if os.path.exists(self.key_file):
            with open(self.key_file, "rb") as keyfile:
                return keyfile.read()
        else:
            key = Fernet.generate_key()
            with open(self.key_file, "wb") as keyfile:
                keyfile.write(key)
            return key

    def _load_credentials(self):
        """Lädt verschlüsselte Zugangsdaten aus einer Datei."""
        if os.path.exists(self.data_file):
            with open(self.data_file, "rb") as file:
                encrypted_data = file.read()
                decrypted_data = self.fernet.decrypt(encrypted_data)
                return json.loads(decrypted_data)
        return {}

    def _get_all_credentials(self):
        return self.credentials

    def _save_credentials(self):
        """Speichert die verschlüsselten Zugangsdaten in einer Datei."""
        encrypted_data = self.fernet.encrypt(json.dumps(self.credentials).encode())
        with open(self.data_file, "wb") as file:
            file.write(encrypted_data)

    def add_ksp(self, api, user, password, url):
        """Fügt neue Zugangsdaten hinzu."""
        self.credentials[api] = {"user": user, "password": password, "url": url}
        self._save_credentials()

    def get_ksp(self, api):
        """Gibt die Zugangsdaten für eine API zurück."""
        return self.credentials.get(api, (None, None, None))

    def delete_ksp(self, api):
        """Entfernt einen Eintrag und speichert."""
        if api in self.credentials:
            del self.credentials[api]
            self._save_credentials()

# Beispiel-Nutzung:
if __name__ == "__main__":

    pass
