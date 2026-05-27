import os
import time
import sqlite3
import subprocess
import streamlit as st
import psutil  # Neu hinzugefügt für saubere Prozessverwaltung
from multiprocessing import Process

DB_PATH = "tasks.db"
CLEANUP_INTERVAL = 60  # Sekunden, nach denen alte Tasks aus der DB gelöscht werden

class TaskManager:
    def __init__(self):
        self.init_db()

    def init_db(self):
        """Erstellt die SQLite-Datenbank für die Task-Überwachung."""
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    script TEXT UNIQUE,
                    pid INTEGER,
                    status TEXT,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def start_task(self, script_path):
        """Startet ein Python-Skript als Subprozess, falls es noch nicht läuft."""
        running_task = self.get_running_task(script_path)
        if running_task:
            return f"Task {script_path} läuft bereits mit PID {running_task[2]}."

        # Starte das Skript als unabhängigen Prozess
        process = subprocess.Popen(["/home/cloogidoo/.venv/bin/python3", script_path], 
                                   stdout=subprocess.DEVNULL, 
                                   stderr=subprocess.DEVNULL,
                                   start_new_session=True)  # Trennt Kindprozess vom Elternprozess

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO tasks (script, pid, status) VALUES (?, ?, ?)", 
                      (script_path, process.pid, "running"))
            conn.commit()

        return f"Task {script_path} gestartet mit PID {process.pid}."

    def get_running_task(self, script_path):
        """Prüft, ob ein Task bereits läuft."""
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM tasks WHERE script = ? AND status = 'running'", (script_path,))
            return c.fetchone()

    def check_and_restart_tasks(self):
        """Überprüft und startet Tasks neu, falls sie gestoppt wurden."""
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT script, pid FROM tasks WHERE status = 'running'")
            tasks = c.fetchall()

            for script, pid in tasks:
                if not self.is_process_running(pid):
                    self.start_task(script)

    def is_process_running(self, pid):
        """Prüft mit psutil, ob ein Prozess mit der angegebenen PID noch läuft."""
        try:
            proc = psutil.Process(pid)
            return proc.is_running()
        except psutil.NoSuchProcess:
            return False

    def stop_task(self, script_path):
        """Stoppt einen laufenden Task."""
        running_task = self.get_running_task(script_path)
        if running_task:
            pid = running_task[2]
            try:
                os.kill(pid, 9)  # Sofortiges Beenden des Prozesses
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("UPDATE tasks SET status = 'stopped' WHERE script = ?", (script_path,))
                    conn.commit()
                return f"Task {script_path} mit PID {pid} gestoppt."
            except OSError:
                return f"Fehler beim Stoppen des Tasks {script_path}."
        return f"Task {script_path} läuft nicht."

    def cleanup_old_tasks(self):
        """Entfernt gestoppte Tasks, die länger als CLEANUP_INTERVAL Sekunden inaktiv sind."""
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(f"DELETE FROM tasks WHERE status = 'stopped' AND last_checked < datetime('now', '-{CLEANUP_INTERVAL} seconds')")
            conn.commit()

    def get_status(self):
        """Gibt den Status aller Tasks zurück."""
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT script, pid, status FROM tasks")
            return c.fetchall()

# Hintergrund-Überwachung starten
def start_watcher():
    manager = TaskManager()
    while True:
        manager.check_and_restart_tasks()
        manager.cleanup_old_tasks()  # Bereinige alte Tasks regelmäßig
        time.sleep(10)  # Alle 10 Sekunden prüfen

# Startet den Watcher-Prozess
def start_background_watcher():
    watcher = Process(target=start_watcher, daemon=True)
    watcher.start()
    return watcher