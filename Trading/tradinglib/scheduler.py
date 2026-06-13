import streamlit as st
import pandas as pd
import schedule
import time
from datetime import datetime
import sqlite3
import subprocess
import psutil
import shlex
from threading import RLock, Event
from tradinglib import tools as ts
from tradinglib.tools import open_db
#from streamlit.runtime.scriptrunner import add_script_run_ctx
import logging
import os, sys

class Scheduler:

    def __init__(self, database='scheduler.db', run_in_browser=False, log_file_name='scheduler_web.log', enable_logging=False):
        """Initialize the scheduler, open the SQLite backend, and set up optional file logging.

        run_in_browser=True: scheduler runs as a background thread inside Streamlit (no cron needed).
        run_in_browser=False: blocking daemon mode, called from schedserver.py.
        enable_logging=True writes all write() calls to log_file_name.
        """
        self.database = ts.Tools().get_path(path="database", file_name=database)
        self.conn = self.init_db()
        self.lock = RLock()  # RLock erlaubt rekursive Akquisition vom selben Thread
        self.enable_logging = enable_logging
        self.run_in_browser = run_in_browser
        self.logger = None
        self._stop_event = Event()
        
        if enable_logging:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
            file_handler = logging.FileHandler(ts.Tools().get_path(path='', file_name=log_file_name))
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

        self.write(f'Scheduler initialized ({log_file_name})', stdout=True)


    def is_running(self) -> bool:
        """Return True when the background scheduler thread is alive."""
        from threading import enumerate as list_threads
        return any(t.name == 'trading-scheduler' for t in list_threads())

    def start_background_thread(self):
        """Start the scheduler loop in a daemon thread; no-op if one is already running."""
        from threading import enumerate as list_threads, Thread
        if self.is_running():
            return
        self._stop_event.clear()
        Thread(target=self.run_scheduler, args=(self.conn,), daemon=True, name='trading-scheduler').start()

    def stop_background_thread(self):
        """Signal the background scheduler thread to stop; it exits within ~1 s."""
        self._stop_event.set()


    def write(self, data, stdout=False, printonly=False):
        """Print to stdout and optionally write to the file logger."""
        if stdout:
            print(data)
        if not printonly and self.logger and self.logger.handlers:
            self.logger.info(data)
            self.logger.handlers[0].flush()
        

    def init_db(self):
        """Create the jobs and processes tables if they don't exist and return the connection."""
        conn = open_db(self.database, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id INTEGER PRIMARY KEY, time TEXT, command TEXT, frequency TEXT, job_name TEXT,
                      end_time TEXT, time_range TEXT, allowed_days TEXT,
                      enabled INTEGER DEFAULT 1, last_run TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS processes
                     (id INTEGER PRIMARY KEY, task_id INTEGER, pid INTEGER, job_name TEXT)''')
        # migrations: add new columns to existing databases
        for ddl in (
            'ALTER TABLE jobs ADD COLUMN enabled INTEGER DEFAULT 1',
            'ALTER TABLE jobs ADD COLUMN last_run TEXT',
        ):
            try:
                c.execute(ddl)
            except Exception:
                pass  # column already exists
        conn.commit()
        return conn


    def load_schedule_from_db(self, conn):
        """Load all scheduled jobs from the database and return them as a DataFrame."""
        with self.lock:
            c = conn.cursor()
            c.execute('SELECT id, time, command, frequency, job_name, end_time, time_range, allowed_days, enabled, last_run FROM jobs')
            jobs = c.fetchall()
            df = pd.DataFrame(jobs, columns=['id', 'time', 'command', 'frequency', 'job_name', 'end_time', 'time_range', 'allowed_days', 'enabled', 'last_run'])
            df['enabled'] = df['enabled'].fillna(1).astype(bool)
            return df


    def within_time_window(self, job_time_range):
        """Return True when the current time falls within the 'HH:MM-HH:MM' range string."""
        now = datetime.now().strftime("%H:%M")
        if job_time_range:
            start, end = job_time_range.split('-')
            return start <= now <= end
        return True  # No restriction
		
    def save_schedule_to_db(self, conn, df):
        """Replace all jobs in the database with the rows from df (full overwrite)."""
        with self.lock:
            self.clear_jobs_in_db(conn)
            c = conn.cursor()
            for _, row in df.iterrows():
                enabled = int(bool(row.get('enabled', True)))
                last_run = row.get('last_run') or None
                c.execute('INSERT INTO jobs (time, command, frequency, job_name, end_time, time_range, allowed_days, enabled, last_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                          (row['time'], row['command'], row['frequency'], row['job_name'], row['end_time'], row['time_range'], row.get('allowed_days', ''), enabled, last_run))
            conn.commit()


    def clear_jobs_in_db(self, conn):
        """Delete all rows from the jobs table."""
        c = conn.cursor()
        c.execute('DELETE FROM jobs')
        conn.commit()


    def save_process_to_db(self, conn, task_id, pid, job_name):
        """Record a running subprocess (task_id, PID, job_name) in the processes table."""
        c = conn.cursor()
        c.execute('INSERT INTO processes (task_id, pid, job_name) VALUES (?, ?, ?)', (task_id, pid, job_name))
        conn.commit()


    def load_processes_from_db(self, conn):
        """Return all rows from the processes table as a list of tuples."""
        c = conn.cursor()
        c.execute('SELECT * FROM processes')
        processes = c.fetchall()
        return processes


    def check_process_exists_db(self, conn, job_name):
        """Return all process rows registered under job_name."""
        c = conn.cursor()
        c.execute("SELECT * FROM processes WHERE job_name = ?", (job_name,))
        return c.fetchall()


    def delete_process_from_db(self, conn, task_id):
        """Remove a process entry by task_id from the processes table."""
        c = conn.cursor()
        c.execute('DELETE FROM processes WHERE task_id = ?', (task_id,))
        conn.commit()


    def delete_process_from_db_by_pid(self, conn, pid):
        """Remove a process entry by PID from the processes table."""
        c = conn.cursor()
        c.execute('DELETE FROM processes WHERE pid = ?', (pid,))
        conn.commit()    


    def should_run_now(self, job_time_range: str, allowed_days: str) -> bool:
        """Return True when the current time and weekday match the job's constraints.

        job_time_range: 'HH:MM-HH:MM' window or empty string (= always allowed).
        allowed_days: comma-separated weekday names (e.g. 'monday,wednesday') or empty.
        """
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_day = now.strftime("%A").lower()

        if job_time_range:
            try:
                start, end = job_time_range.split('-')
                if not (start <= current_time <= end):
                    return False
            except ValueError:
                self.write(f"Invalid time range format: {job_time_range}", stdout=True)
                return False

        if allowed_days:
            day_list = [day.strip().lower() for day in allowed_days.split(',')]
            if current_day not in day_list:
                return False

        return True

    
    def run_task(self, command, task_id, conn, job_name, end_time, job_time_range, allowed_days=None, force=False):
        """Launch a job subprocess if the time window is valid and no instance is already running.

        force=True bypasses the time-window / allowed-days check (manual trigger from UI).
        Skips execution when should_run_now() is False or when the process table shows
        an active PID for this job_name. Records the new PID in the processes table.
        """
        if not force and not self.should_run_now(job_time_range, allowed_days):
            self.write(f"Skipping {job_name}, not in allowed day/time window", stdout=True)
            return

        try:
            pids = self.check_process_exists_db(conn, job_name=job_name)
            active = False

            if pids:
                for (_, _, pid, _) in pids:
                    try:
                        p = psutil.Process(pid)
                        if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                            self.write(f"Skipping {job_name}: still running (PID {pid})", stdout=True)
                            active = True
                            break
                        else:
                            self.delete_process_from_db_by_pid(conn, pid)
                    except psutil.NoSuchProcess:
                        self.delete_process_from_db_by_pid(conn, pid)

            if not active:
                self.write(f"Running command: {command}", stdout=True)
                process = subprocess.Popen(shlex.split(command), shell=False)
                self.save_process_to_db(conn, task_id, process.pid, job_name)
                try:
                    conn.execute('UPDATE jobs SET last_run = ? WHERE job_name = ?',
                                 (datetime.now().strftime('%d.%m.%Y %H:%M'), job_name))
                    conn.commit()
                except Exception:
                    pass
        except Exception as e:
            self.write(f"Error running job {job_name}: {str(e)}", stdout=True)


    def terminate_process(self, pid):
        """Send SIGTERM to PID and wait up to 3 seconds before escalating to SIGKILL."""
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            p.kill()


    def monitor_tasks(self, conn):
        """Clean up finished or zombie processes from the processes table."""
        with self.lock:
            processes = self.load_processes_from_db(conn)
            for _, task_id, pid, _ in processes:
                try:
                    p = psutil.Process(pid)
                    if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                        self.delete_process_from_db(conn, task_id)
                        self.write(f"Job cleanup for pid: {pid}, task id {task_id}", stdout=True)
                except psutil.NoSuchProcess:
                    self.delete_process_from_db(conn, task_id)
                    self.write(f"Process {pid} not found. Cleaned up task id {task_id}", stdout=True)
                except Exception as e:
                    self.write(f"Monitor error for PID {pid}: {str(e)}", stdout=True)


    def schedule_tasks(self, edited_df, conn):
        """Register all jobs from edited_df with the `schedule` library.

        Supports daily, hourly, weekday, and interval-based frequencies. Skips
        duplicate jobs that are already registered with the same signature.
        """
        with self.lock:
            self.write('Replanning jobs', stdout=True)
            try:
                for index, row in edited_df.iterrows():
                    task_time = row['time']
                    command = row['command']
                    frequency = row['frequency'].lower()
                    job_name = row['job_name']
                    end_time = row['end_time']
                    time_range = row['time_range']
                    allowed_days = row.get('allowed_days', '')
                    task_id = index
                    current_day = datetime.today().strftime('%A').lower()

                    if not bool(row.get('enabled', True)):
                        self.write(f"Skipping disabled job: {job_name}", stdout=True)
                        continue

                    job_signature = f"{job_name}:{command}:{frequency}:{task_time}"
                    if any(job.job_func.__name__ == 'run_task' and job.job_func.args == () and job.job_func.keywords and f"{job.job_func.keywords.get('job_name')}:{job.job_func.keywords.get('command')}:{frequency}:{task_time}" == job_signature for job in schedule.jobs):
                        self.write(f"Skipping duplicate job: {job_signature}", stdout=True)
                        continue

                    self.write(f"Run interval: {task_time}, {command}", stdout=True)

                    if ',' in frequency:
                        days = [x.strip() for x in frequency.split(',')]
                        if current_day in days:
                            getattr(schedule.every(), current_day).at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name, end_time=end_time, job_time_range=time_range, allowed_days=allowed_days)
                    elif frequency in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'] and frequency == current_day:
                            getattr(schedule.every(), current_day).at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name, end_time=end_time, job_time_range=time_range, allowed_days=allowed_days)    
                    elif current_day == frequency:
                        getattr(schedule.every(), current_day).at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name, end_time=end_time, job_time_range=time_range, allowed_days=allowed_days)
                    elif frequency in ['days', 'hours', 'minutes', 'seconds', 'weeks']:
                        getattr(schedule.every(int(task_time)), frequency).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name, end_time=end_time, job_time_range=time_range, allowed_days=allowed_days)
                    elif frequency == 'daily':
                        schedule.every().day.at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name, end_time=end_time, job_time_range=time_range, allowed_days=allowed_days)
                    elif frequency == 'hourly':
                        schedule.every().hour.at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name, end_time=end_time, job_time_range=time_range, allowed_days=allowed_days)
                    else:
                        self.write(f'Nothing planned, check schedule frequency: {frequency}:{task_time}.', stdout=True)
            except Exception as e:
                self.write(f"Error planning job: {e}", stdout=True)

            self.write('Current jobs:', stdout=True)
            for job in schedule.jobs:
                self.write(str(job), stdout=True)

    def run_next_n_jobs(self, conn, n):
        """Run up to n overdue jobs (sorted by next_run) when the process table has capacity."""
        now = datetime.now()
        pending_jobs = [job for job in schedule.jobs if job.next_run <= now]
        pending_jobs.sort(key=lambda job: job.next_run)
        for job in pending_jobs[:n]:
            if len(self.load_processes_from_db(conn)) <= n:
                job.run()
                job.last_run = datetime.now()
                job._schedule_next_run()


    def cleanup_stale_processes(self, conn) -> int:
        """Remove process entries whose PIDs are no longer alive. Returns number of entries removed."""
        with self.lock:
            removed = 0
            for _, task_id, pid, job_name in self.load_processes_from_db(conn):
                try:
                    p = psutil.Process(pid)
                    if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                        self.delete_process_from_db(conn, task_id)
                        removed += 1
                except psutil.NoSuchProcess:
                    self.delete_process_from_db(conn, task_id)
                    removed += 1
            if removed:
                self.write(f'Startup cleanup: {removed} stale process entry/entries removed.', stdout=True)
            return removed

    def run_scheduler(self, conn):
        """Start the blocking scheduler loop: loads jobs, runs overdue ones, reloads every 60 s.

        Clears all jobs at midnight to allow a clean daily reload. Intended to run as a
        standalone daemon process (see schedserver.py).
        """
        self.write('Starting scheduler core', stdout=True)
        schedule.clear()                   # drop leftover jobs from a previous run
        self.cleanup_stale_processes(conn) # remove dead PIDs from the processes table
        df_jobs = self.load_schedule_from_db(conn)
        self.schedule_tasks(df_jobs, conn)
        while not self._stop_event.is_set():
            try:
                with self.lock:
                    self.write("Pending job@ " + schedule.next_run().strftime("%d.%m.%Y %H:%M"), stdout=True, printonly=True)
                    max_jobs_to_run = 5
                    if len(self.load_processes_from_db(conn)) < max_jobs_to_run:
                        self.run_next_n_jobs(conn, max_jobs_to_run)
                self.monitor_tasks(conn)
            except Exception:
                pass

            if (int(time.perf_counter()) % 60) == 1:
                df_jobs = self.load_schedule_from_db(conn)
                self.schedule_tasks(df_jobs, conn)
                if not self.run_in_browser:
                    self.write('Standalone mode', stdout=True)
                    if schedule.next_run():
                        self.write("Idle until: " + schedule.next_run().strftime("%d.%m.%Y %H:%M"), stdout=True)
                    self.write('Current jobs planned', stdout=True)
                    for job in schedule.jobs:
                        self.write(job, stdout=True)

            if datetime.now().strftime("%H:%M") == "00:00":
                schedule.clear()
                time.sleep(60)

            time.sleep(1)


    def get_running_processes(self):
        """Return a list of dicts describing all running system processes (up to depth 2).

        Each dict contains PID, Parent PID, Is Child, Name, User, Level, Command Line.
        Inaccessible or zombie processes are silently skipped.
        """
        processes = []

        def add_process_and_children(proc, is_child=False, level=0):
            """Fügt einen Prozess und seine Subprozesse rekursiv der Prozessliste hinzu."""
            try:
                processes.append({
                    'PID': proc.pid,
                    'Parent PID': proc.ppid(),  # Übergeordnete PID
                    'Is Child': is_child,  # Kennzeichnung, ob es sich um einen Subprozess handelt
                    'Name': proc.name(),
                    'User': proc.username(),
                    'Level': level,  # Tiefe in der Prozesshierarchie
                    'Command Line': ' '.join(proc.cmdline())  # Aufrufende Kommandozeile
                })

                if level <= 2:            
                    # Rekursiv alle Subprozesse dieses Prozesses hinzufügen
                    children = proc.children()
                    for child in children:
                        add_process_and_children(child, is_child=True, level=level + 1)
                
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    
        # Starten mit allen Hauptprozessen
        for proc in psutil.process_iter(['pid', 'name', 'username']):
            add_process_and_children(proc, is_child=False, level=0)
        
        return processes

    @st.fragment(run_every='30s')
    def jobs_status(self, conn):
        """Coloured status overview — auto-refreshes every 30 s without touching the editor."""
        running_names = {row[3] for row in self.load_processes_from_db(conn)}
        df = self.load_schedule_from_db(conn)
        df_status = df[['job_name', 'enabled', 'time', 'frequency', 'last_run']].copy()
        df_status.insert(0, 'Status', df_status['job_name'].map(
            lambda n: '🟢 läuft' if n in running_names else '⚫'
        ))
        df_status = df_status.rename(columns={
            'job_name': 'Job', 'enabled': 'Aktiv',
            'time': 'Zeit', 'frequency': 'Frequenz', 'last_run': 'Zuletzt gelaufen',
        })

        def _row_color(row):
            if row['Status'] == '🟢 läuft':
                return ['background-color:#1a3d28; color:#6ee89a'] * len(row)
            if not row['Aktiv']:
                return ['color:#666666'] * len(row)
            return [''] * len(row)

        st.dataframe(
            df_status.style.apply(_row_color, axis=1),
            use_container_width=True,
            hide_index=True,
            column_config={'Aktiv': st.column_config.CheckboxColumn(disabled=True)},
        )

    @st.fragment  # no run_every — isolated from the 30s status auto-refresh
    def _jobs_editor(self, conn):
        """Editable jobs table + manual-start controls.

        Wrapped in its own fragment so the periodic jobs_status() refresh
        does NOT cause this widget to re-render (and lose unsaved edits).
        The data_editor uses a fixed key so the background scheduler thread
        updating `last_run` doesn't change the auto-generated widget key and
        reset in-progress edits.
        """
        # ── Manuell starten ──────────────────────────────────────────────────
        df = self.load_schedule_from_db(conn)
        col_sel, col_btn = st.columns([4, 1])
        with col_sel:
            job_names = df['job_name'].tolist()
            selected_job = st.selectbox(
                "Job manuell starten:",
                ["— auswählen —"] + job_names,
                key="_manual_job_sel",
                label_visibility="collapsed",
            )
        with col_btn:
            if st.button("▶ Jetzt starten",
                         disabled=(selected_job == "— auswählen —"),
                         use_container_width=True):
                row = df[df['job_name'] == selected_job].iloc[0]
                self.run_task(
                    command=str(row['command']),
                    task_id=int(row['id']),
                    conn=conn,
                    job_name=str(row['job_name']),
                    end_time=str(row['end_time']),
                    job_time_range='',
                    allowed_days='',
                    force=True,
                )
                st.success(f"▶ **{selected_job}** gestartet.")

        # ── Editierbare Tabelle ───────────────────────────────────────────────
        st.caption("Änderungen unten vornehmen, dann **Save** klicken.")
        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            height=500,
            key="_jobs_editor_table",
            column_config={
                "enabled":  st.column_config.CheckboxColumn("Aktiv", default=True, width="small"),
                "id":       st.column_config.NumberColumn("ID", disabled=True, width="small"),
                "last_run": st.column_config.TextColumn("Zuletzt gelaufen", disabled=True),
                "job_name":     st.column_config.TextColumn("Job Name"),
                "time":         st.column_config.TextColumn("Zeit / Interval"),
                "frequency":    st.column_config.TextColumn("Frequenz"),
                "command":      st.column_config.TextColumn("Kommando"),
                "end_time":     st.column_config.TextColumn("End-Zeit"),
                "time_range":   st.column_config.TextColumn("Zeitfenster"),
                "allowed_days": st.column_config.TextColumn("Erlaubte Tage"),
            },
            column_order=["enabled", "id", "job_name", "time", "frequency",
                          "command", "end_time", "time_range", "allowed_days", "last_run"],
        )
        if st.button("Save"):
            self.save_schedule_to_db(conn, edited_df)
            self.schedule_tasks(edited_df, conn)
            del st.session_state["_jobs_editor_table"]
            st.success("Jobs saved.")
            st.rerun()

    def jobs(self, conn):
        """Render a coloured status overview (auto-refresh) and an editable jobs table."""
        st.header("Current scheduled jobs")
        self.jobs_status(conn)    # fragment – auto-refreshes every 30 s
        self._jobs_editor(conn)   # fragment – only reruns on user interaction


    def running_jobs(self, conn):
        """Render the running-jobs list, Terminate button, and stale-cleanup button."""
        st.header("Running Jobs")
        processes = self.load_processes_from_db(conn)
        if processes:
            process_display = [f"Job: {job_name} (Task ID: {task_id}, PID: {pid})" for _, task_id, pid, job_name in processes]
            task_to_terminate = st.selectbox("Choose a Job to terminate", process_display)
            if self.enable_logging and self.logger:
                self.logger.info(process_display)
            col_term, col_clean = st.columns([2, 1])
            with col_term:
                if st.button("Terminate job"):
                    selected_task = process_display.index(task_to_terminate)
                    _, task_id, pid, job_name = processes[selected_task]
                    self.terminate_process(pid)
                    self.delete_process_from_db(conn, task_id)
                    msg = f"Process {task_to_terminate} terminated."
                    if self.enable_logging and self.logger:
                        self.logger.info(msg)
                    st.success(msg)
            with col_clean:
                if st.button("🧹 Stale bereinigen", help="Entfernt Einträge deren PID nicht mehr existiert."):
                    removed = self.cleanup_stale_processes(conn)
                    st.success(f"{removed} stale Eintrag/Einträge entfernt." if removed else "Keine stale Einträge gefunden.")
                    st.rerun()
        else:
            st.write("No running jobs found.")
            if st.button("🧹 Stale bereinigen", key="_clean_empty", help="Entfernt Einträge deren PID nicht mehr existiert."):
                removed = self.cleanup_stale_processes(conn)
                st.success(f"{removed} stale Eintrag/Einträge entfernt." if removed else "Tabelle bereits leer.")
            
#        self.write("Pending job@ " + schedule.next_run().strftime("%d.%m.%Y %H:%M") )
#        self.write("Idle until: " + schedule.next_run().strftime("%d.%m.%Y %H:%M") )

    def running_processes(self):
        """Render the full system process list with multi-select Terminate functionality."""
        st.header("Running processes")

        processes = self.get_running_processes()
        df_processes = pd.DataFrame(processes)
        df_processes = df_processes.drop_duplicates(subset=['PID'], keep='first')
        df_processes = df_processes.reset_index(drop=True)
    
        # Prozesse in einer Tabelle anzeigen
        if not df_processes.empty:

            st.session_state.selected_processes = st.dataframe(
                    df_processes,
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="multi-row",
                )
        
            if st.button("Terminate process"):
                for selected in st.session_state.selected_processes.selection.rows:
                    if self.terminate_process(df_processes.loc[selected,'PID']):
                        self.write(f"Process terminated: {df_processes.loc[selected,'Name']}")
                else:
                    self.write("No running jobs found.")

    @st.fragment(run_every='120s')
    def render(self):
        """Render the full scheduler UI (jobs + running jobs + processes), refreshed every 120 s."""
        col_sb, _, col_main = st.columns([1, 0.1, 4])
        with col_sb:
            self.running_jobs(self.conn)
        with col_main:
            self.jobs(self.conn)
        self.running_processes()

    def render_tabs(self):
        """Render the scheduler UI: enable/disable toggle + three tabs."""
        running = self.is_running()
        enabled = st.toggle(
            "Web-Scheduler aktiv",
            value=running,
            help="Scheduler als Hintergrund-Thread in dieser Browser-Session starten (für Rechner ohne cron / schedserver).",
        )
        if enabled and not running:
            self.start_background_thread()
            st.success("Scheduler gestartet.")
        elif not enabled and running:
            self.stop_background_thread()
            st.info("Scheduler wird gestoppt…")

        if self.is_running():
            st.caption("🟢 Web-Scheduler läuft")
        else:
            st.caption("⚫ Web-Scheduler inaktiv")

        tabs = st.tabs(["Schedules", "Running Jobs", "Running Processes"])
        with tabs[0]:
            self.jobs(self.conn)
        with tabs[1]:
            self.running_jobs(self.conn)
        with tabs[2]:
            with st.expander("Systemprozesse anzeigen", expanded=False):
                if st.button("🔄 Prozesse laden", key="_sched_load_procs"):
                    st.session_state['_sched_procs_loaded'] = True
                if st.session_state.get('_sched_procs_loaded', False):
                    self.running_processes()
                else:
                    st.caption("Klick auf 'Prozesse laden' um alle laufenden Systemprozesse abzurufen.")


if __name__ == "__main__":
    
    # default db to use
    db_file = 'scheduler.db'
     
    # or load a different db
    if len(sys.argv) > 1:
        db_file = sys.argv[1]

    print('Schedserver running')
    
    os.chdir(ts.Tools().get_path(path = '', file_name=''))
    database = ts.Tools().get_path(path = '', file_name=db_file)

    scheduler = Scheduler(database=db_file, log_file_name='schedserver.log')
    scheduler.run_scheduler(scheduler.conn)

    pass