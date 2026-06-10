import os
import time
import sqlite3
import subprocess
import streamlit as st
import psutil  # Neu hinzugefügt für saubere Prozessverwaltung
from multiprocessing import Process
from tradinglib.tools import open_db

DB_PATH = "tasks.db"
CLEANUP_INTERVAL = 60  # Sekunden, nach denen alte Tasks aus der DB gelöscht werden

class TaskManager:
    def __init__(self):
        """Initialize the task manager and ensure the SQLite tasks table exists."""
        self.init_db()

    def init_db(self):
        """Create the SQLite tasks table if it doesn't exist."""
        with open_db(DB_PATH) as conn:
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
        """Launch a Python script as an independent subprocess if it isn't already running."""
        running_task = self.get_running_task(script_path)
        if running_task:
            return f"Task {script_path} läuft bereits mit PID {running_task[2]}."

        # Starte das Skript als unabhängigen Prozess
        process = subprocess.Popen(["/home/cloogidoo/.venv/bin/python3", script_path], 
                                   stdout=subprocess.DEVNULL, 
                                   stderr=subprocess.DEVNULL,
                                   start_new_session=True)  # Trennt Kindprozess vom Elternprozess

        with open_db(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO tasks (script, pid, status) VALUES (?, ?, ?)",
                      (script_path, process.pid, "running"))
            conn.commit()

        return f"Task {script_path} gestartet mit PID {process.pid}."

    def get_running_task(self, script_path):
        """Return the database row for script_path if it is currently marked as running."""
        with open_db(DB_PATH, readonly=True) as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM tasks WHERE script = ? AND status = 'running'", (script_path,))
            return c.fetchone()

    def check_and_restart_tasks(self):
        """Check all running tasks and restart any whose process is no longer alive."""
        with open_db(DB_PATH, readonly=True) as conn:
            c = conn.cursor()
            c.execute("SELECT script, pid FROM tasks WHERE status = 'running'")
            tasks = c.fetchall()

            for script, pid in tasks:
                if not self.is_process_running(pid):
                    self.start_task(script)

    def is_process_running(self, pid):
        """Return True when a process with the given PID is alive according to psutil."""
        try:
            proc = psutil.Process(pid)
            return proc.is_running()
        except psutil.NoSuchProcess:
            return False

    def stop_task(self, script_path):
        """Send SIGKILL to the running task process and mark it as stopped in the database."""
        running_task = self.get_running_task(script_path)
        if running_task:
            pid = running_task[2]
            try:
                os.kill(pid, 9)  # Sofortiges Beenden des Prozesses
                with open_db(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("UPDATE tasks SET status = 'stopped' WHERE script = ?", (script_path,))
                    conn.commit()
                return f"Task {script_path} mit PID {pid} gestoppt."
            except OSError:
                return f"Fehler beim Stoppen des Tasks {script_path}."
        return f"Task {script_path} läuft nicht."

    def cleanup_old_tasks(self):
        """Delete stopped task entries that have been inactive longer than CLEANUP_INTERVAL seconds."""
        with open_db(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(f"DELETE FROM tasks WHERE status = 'stopped' AND last_checked < datetime('now', '-{CLEANUP_INTERVAL} seconds')")
            conn.commit()

    def get_status(self):
        """Return a list of (script, pid, status) tuples for all tasks in the database."""
        with open_db(DB_PATH, readonly=True) as conn:
            c = conn.cursor()
            c.execute("SELECT script, pid, status FROM tasks")
            return c.fetchall()

# Hintergrund-Überwachung starten
def start_watcher():
    """Run the task monitoring loop: checks and restarts tasks every 10 seconds indefinitely."""
    manager = TaskManager()
    while True:
        manager.check_and_restart_tasks()
        manager.cleanup_old_tasks()  # Bereinige alte Tasks regelmäßig
        time.sleep(10)  # Alle 10 Sekunden prüfen

# Startet den Watcher-Prozess
def start_background_watcher():
    """Start start_watcher as a daemon background process and return the Process object."""
    watcher = Process(target=start_watcher, daemon=True)
    watcher.start()
    return watcher