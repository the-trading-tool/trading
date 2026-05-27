import streamlit as st
from streamlit.components.v1 import html
from tradinglib import tools
from tradinglib import system_config
import base64
import os
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

class FileProvider:
    
    def __init__(self, access_token = "abcdef", basefile_path = "database"):

        # Statische Zugangsdaten (kann auch aus einer DB kommen)
        self.ts = tools.Tools()
        self.BASE_FILE_PATH = basefile_path
        self.sys_config = system_config.SystemConfig()
        self.token = self.sys_config.get_value('api_key',None)
        self.runner()
                
    def runner(self):
        # Query-Parameter abrufen
        query_params = st.query_params.to_dict()
        download_file = query_params.get("download", [None])
        access_token = query_params.get("access_token", [None])
        

        # Prüfen, ob Datei-Download angefordert wurde
        if download_file and access_token:
            # Prüfen, ob das Token gültig ist
            if not access_token == self.token:
                st.error("Invalid assess token!")
            else:
                file_path = self.ts.get_path( path = self.BASE_FILE_PATH, file_name=download_file)
#                st.write(file_path)
                # Prüfen, ob die Datei existiert
                if os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        binary_data = f.read()
                        encoded_data = base64.b64encode(binary_data).decode("utf-8")  # Base64-codieren
 
                    # JavaScript zum automatischen Starten des Downloads
                    html (f"""
                        <script>
                            function downloadFile() {{
                                var a = document.createElement('a');
                                a.href = 'data:application/octet-stream;base64,{encoded_data}';
                                a.download = '{download_file}';
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                            }}
                            window.onload = downloadFile;
                        </script>
                    """)
                else:
                    st.error(f"File {download_file} not found")

                    
class DownloadProvider:
    
    def __init__(self, base_url = 'https://trading.cloogidoo.com/?download=', path = 'database', file_name = 'ticker_data.db', token='abcdef', ignore_env=False):

        self.ts = tools.Tools()
        self.sys_config = system_config.SystemConfig()
        self.base_url = base_url
        self.ignore_env = ignore_env
        self.token = self.sys_config.get_value('api_key',token)
        self.set_download_dir(path)
        self.set_file_name(file_name)

        self.runner()

    def set_download_dir(self, path):
        self.download_dir = self.ts.get_path(path = path, file_name='', ignore_env=self.ignore_env)[:-1]

    def set_file_name(self, file_name):
        self.file_name = file_name
        self.local_file_name  = self.ts.get_path(path = self.download_dir, file_name=file_name, ignore_env=self.ignore_env)
        self.set_url()

    def set_url(self):
        # Server-URL
        self.url = f"{self.base_url}{self.file_name}&access_token={self.token}"
                
    def runner(self):
    
        # Chrome im Headless-Modus starten
        options = Options()
#        options.add_argument("--headless")  # Unsichtbarer Browser
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_experimental_option("prefs", {
            "download.default_directory": self.download_dir,  # Standard-Download-Ordner setzen
            "download.prompt_for_download": False,       # Keine Download-Bestätigung
            "download.directory_upgrade": True,          # Falls Ordner existiert, überschreiben
            "safebrowsing.enabled": True                 # Safe-Browsing erlauben
        })

        self.driver = webdriver.Chrome(options=options)


    def download(self):
        try:
            os.unlink(self.local_file_name)
        except Exception:
            pass
        self.driver.get(self.url)
        time.sleep(15)


    def close_driver(self):
        self.driver.quit()
