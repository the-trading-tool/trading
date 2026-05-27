import streamlit as st
import pandas as pd
import schedule
import time
from datetime import datetime
import sqlite3
import subprocess
import psutil
import shlex
from threading import Lock
from tradinglib import tools as ts
#from streamlit.runtime.scriptrunner import add_script_run_ctx
import logging
import os, sys

class Scheduler:

    def __init__(self, database='scheduler.db', log_file_name = 'scheduler_web.log', enable_logging=False):

        self.database = ts.Tools().get_path(path="database", file_name= database)
        self.conn = self.init_db()
        self.lock = Lock()  # Lock für die Synchronisation erstellen
        self.enable_logging = enable_logging
        self.run_in_browser = False
        
        if enable_logging:                
            self.logger = logging.getLogger()
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')

            file_handler = logging.FileHandler(ts.Tools().get_path(path = '', file_name=log_file_name))
                
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)

            self.logger.addHandler(file_handler)

        self.write(f'Scheduler log initilaized {log_file_name}', stdout=True)


    def write(self, data, stdout=False, printonly=False):
        if stdout:
            if self.enable_logging:
                st.info(data)
            if not printonly and not self.run_in_browser and self.enable_logging:
                self.logger.info(data)
                self.logger.handlers[0].flush()
        

    def init_db(self):
        conn = sqlite3.connect(self.database, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id INTEGER PRIMARY KEY, time TEXT, command TEXT, frequency TEXT, job_name TEXT, end_time TEXT, time_range TEXT, allowed_days TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS processes
                     (id INTEGER PRIMARY KEY, task_id INTEGER, pid INTEGER, job_name TEXT)''')
        conn.commit()
        return conn


    def load_schedule_from_db(self, conn):
        c = conn.cursor()
        c.execute('SELECT id, time, command, frequency, job_name, end_time, time_range, allowed_days FROM jobs')
        jobs = c.fetchall()
        df = pd.DataFrame(jobs, columns=['id', 'time', 'command', 'frequency', 'job_name', 'end_time', 'time_range', 'allowed_days'])
        return df


    def within_time_window(self, job_time_range):
        now = datetime.now().strftime("%H:%M")
        if job_time_range:
            start, end = job_time_range.split('-')
            return start <= now <= end
        return True  # No restriction
		
    def save_schedule_to_db(self, conn, df):
        self.clear_jobs_in_db(conn)
        c = conn.cursor()
        for _, row in df.iterrows():
            c.execute('INSERT INTO jobs (time, command, frequency, job_name, end_time, time_range, allowed_days) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (row['time'], row['command'], row['frequency'], row['job_name'], row['end_time'], row['time_range'], row.get('allowed_days', '')))
        conn.commit()


    def clear_jobs_in_db(self, conn):
        c = conn.cursor()
        c.execute('DELETE FROM jobs')
        conn.commit()


    def save_process_to_db(self, conn, task_id, pid, job_name):
        c = conn.cursor()
        c.execute('INSERT INTO processes (task_id, pid, job_name) VALUES (?, ?, ?)', (task_id, pid, job_name))
        conn.commit()


    def load_processes_from_db(self, conn):
        c = conn.cursor()
        c.execute('SELECT * FROM processes')
        processes = c.fetchall()
        return processes


    def check_process_exists_db(self, conn, job_name):
        c = conn.cursor()
        c.execute("SELECT job_name FROM processes WHERE job_name = ?", (job_name,))
        processes = c.fetchall()
        return len(processes)


    def delete_process_from_db(self, conn, task_id):
        c = conn.cursor()
        c.execute('DELETE FROM processes WHERE task_id = ?', (task_id,))
        conn.commit()


    def delete_process_from_db_by_pid(self, conn, pid):
        c = conn.cursor()
        c.execute('DELETE FROM processes WHERE pid = ?', (pid,))
        conn.commit()    


    def should_run_now(self, job_time_range: str, allowed_days: str) -> bool:
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

    
    def run_task(self, command, task_id, conn, job_name, end_time, job_time_range, allowed_days=None):
        if not self.should_run_now(job_time_range, allowed_days):
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
        except Exception as e:
            self.write(f"Error running job {job_name}: {str(e)}", stdout=True)


    def terminate_process(self, pid):
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass
        except psutil.TimeoutExpired:
            p.kill()


    def monitor_tasks(self, conn):
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
        now = datetime.now()
        pending_jobs = [job for job in schedule.jobs if job.next_run <= now]
        pending_jobs.sort(key=lambda job: job.next_run)
        for job in pending_jobs[:n]:
            if len(self.load_processes_from_db(conn)) <= n:
                job.run()
                job.last_run = datetime.now()
                job._schedule_next_run()


    def run_scheduler(self, conn):
        self.write('Starting scheduler core', stdout=True)
        df_jobs = self.load_schedule_from_db(conn)
        self.schedule_tasks(df_jobs, conn)
        while True:
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

    def jobs(self, conn):
        
        # Laden und Bearbeiten der Daten aus der Datenbank
        self.main.header("Current scheduled jobs")

        df = self.load_schedule_from_db(conn)
        edited_df = self.main.data_editor(df, num_rows="dynamic",height=615)

        if self.main.button("Save"):
            self.save_schedule_to_db(conn, edited_df)
            self.schedule_tasks(edited_df, conn)
            self.main.success("Jobs saved.")


    def running_jobs(self, conn):

        # Prozesse abrufen
        self.sb.header("Running Jobs")
    
        processes = self.load_processes_from_db(conn)
        if processes:
            # Anzeige von Jobname und Task-ID
            process_display = [f"Job: {job_name} (Task ID: {task_id}, PID: {pid})" for _, task_id, pid, job_name in processes]
            task_to_terminate = self.sb.selectbox("Choose a Job to terminate", process_display)
            if self.enable_logging:                
                self.logger.info(process_display)
        
            if self.sb.button("Terminate job"):
                selected_task = process_display.index(task_to_terminate)
                _, task_id, pid, job_name = processes[selected_task]
                self.terminate_process(pid)
                self.delete_process_from_db(conn, task_id)
                msg = f"Process {task_to_terminate} terminated."
                if self.enable_logging:                
                    self.logger.info(msg)
                self.sb.success(msg)
        else:
            self.sb.write("No running jobs found.")
            
#        self.write("Pending job@ " + schedule.next_run().strftime("%d.%m.%Y %H:%M") )
#        self.write("Idle until: " + schedule.next_run().strftime("%d.%m.%Y %H:%M") )

    def running_processes(self):
        # Prozesse abrufen

        st.header("Running processes")

        processes = self.get_running_processes()
        df_processes = pd.DataFrame(processes)
        df_processes = df_processes.drop_duplicates(subset=['PID'], keep='first')
        df_processes.reset_index(inplace = True, drop = True)
    
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
    # Streamlit-Seite
    def render(self):

        frame = st.empty()
        (self.sb, _, self.main) = frame.columns([1,0.1, 4])

        self.jobs(self.conn) 
        self.running_jobs(self.conn)
        self.running_processes()


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