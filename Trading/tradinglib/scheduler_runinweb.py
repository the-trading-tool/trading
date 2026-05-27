import streamlit as st
import pandas as pd
import schedule
import time
from datetime import datetime
import sqlite3
import subprocess
import psutil
import shlex
from threading import Thread, Lock, enumerate
from tradinglib import tools as wd
from streamlit.runtime.scriptrunner import add_script_run_ctx
import logging
import os, sys

class Scheduler:

    def __init__(self, database='scheduler.db', runserverinweb = False, log_file_name = 'scheduler_web.log', enable_logging=False):

        self.database = database
        self.conn = self.init_db()
        self.lock = Lock()  # Lock für die Synchronisation erstellen
        self.runserverinweb = runserverinweb
        self.enable_logging = enable_logging

        if enable_logging:                
            self.logger = logging.getLogger()
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')

            file_handler = logging.FileHandler(wd.Tools().get_path(path = '', file_name=log_file_name))
                
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)

            self.logger.addHandler(file_handler)

        self.write(f'Scheduler log initilaized {log_file_name}', stdout=True)
        # if we run this in a browser an can ensure the browser is up 24/7
        # then we can use streamlit connection to run the scheduler for us
        # otherwise we need to start schedserver.py as a background process
        self.server()

    # Scheduler-Server
    def server(self):

        if self.runserverinweb:
            self.write(f'In web scheduler', stdout=True)
            # Scheduler in einem separaten Thread starten
            if enumerate == 0:
                self.scheduler_thread = Thread(target=self.run_scheduler, args=(self.conn,), daemon=True)
                add_script_run_ctx(self.scheduler_thread)
                self.scheduler_thread.start()
        else:
#            if enumerate() == 0:
#               self.scheduler_thread = Thread(target='', daemon=True)
#                add_script_run_ctx(self.scheduler_thread)
#                self.scheduler_thread.start()
            pass
        
    def write(self, data, stdout = False, printonly=False):
        if stdout:
            print(data)
            if not printonly and not self.runserverinweb:
                if self.enable_logging:
                    self.logger.info(data)
                    self.logger.handlers[0].flush()
        else:
            st.write(data)
        
    def init_db(self):
        conn = sqlite3.connect(self.database, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS jobs
                     (id INTEGER PRIMARY KEY, time TEXT, command TEXT, frequency TEXT, job_name TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS processes
                     (id INTEGER PRIMARY KEY, task_id INTEGER, pid INTEGER, job_name TEXT)''')
        conn.commit()
        return conn

    def load_schedule_from_db(self, conn):
        c = conn.cursor()
        c.execute('SELECT id, time, command, frequency, job_name FROM jobs')
        jobs = c.fetchall()
        df = pd.DataFrame(jobs, columns=['id', 'Time', 'Command', 'Frequency', 'Name'])
        return df

    def save_schedule_to_db(self, conn, df):
        self.clear_jobs_in_db(conn)
        c = conn.cursor()
        for _, row in df.iterrows():
            c.execute('INSERT INTO jobs (time, command, frequency, job_name) VALUES (?, ?, ?, ?)',
                      (row['Time'], row['Command'], row['Frequency'], row['Name']))
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

    def run_task(self, command, task_id, conn, job_name):
        if self.runserverinweb:
            with self.lock:  # Synchronisation, um doppelte Ausführungen zu verhindern
                if self.check_process_exists_db(conn, job_name=job_name) == 0:
                    try:
                        self.write(f"Running command: {command}")
                        process = subprocess.Popen(shlex.split(command), shell=False)
                        self.save_process_to_db(conn, task_id, process.pid, job_name)
                    except Exception:
                        pass
                else:
                    self.write(f"Job already started: {command}")

        else:
            if self.check_process_exists_db(conn, job_name=job_name) == 0:
                try:
                    self.write(f"Running command: {command}")
                    process = subprocess.Popen(shlex.split(command), shell=False)
                    self.save_process_to_db(conn, task_id, process.pid, job_name)
                except Exception:
                    pass
            else:
                self.write(f"Job already started: {command}")

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
        
        with self.lock:  # Synchronisation der Überwachung
        
            processes = self.load_processes_from_db(conn)
            for _, task_id, pid, _ in processes:
                try:
                    p = psutil.Process(pid)
                    if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
                        self.delete_process_from_db(conn, task_id)
                except psutil.NoSuchProcess:
                    self.delete_process_from_db(conn, task_id)

    def schedule_tasks(self, edited_df, conn):

        with self.lock:  # Synchronisation beim Planen von Aufgaben

            self.write('Replanning jobs', stdout=True)
            schedule.clear()

#            if 1:
            try:
                for index, row in edited_df.iterrows():
                    task_time = row['Time']
                    command = row['Command']
                    self.write(f'replanning {task_time}, {command}', stdout=True)
                    try:
                        frequency = row['Frequency'].lower()
                    except Exception:
                        frequency = ''
                        pass
                    job_name = row['Name']

                    task_id = index
                    if frequency in ['days', 'hours', 'minutes', 'seconds', 'weeks']:
                        getattr(schedule.every(int(task_time)), frequency).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name)
                    elif frequency in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
                        getattr(schedule.every(), frequency).at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name)
                    elif frequency == 'daily':
                        schedule.every().day.at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name)
                    elif frequency == 'hourly':
                        schedule.every().hour.at(task_time).do(self.run_task, command=command, task_id=task_id, conn=conn, job_name=job_name)
            except Exception:
                pass
            
            self.write('My new jobs', stdout=True)
            for job in schedule.jobs:
                self.write(job, stdout=True)


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


    # Modifizierte run_pending Funktion
    def run_next_n_jobs(self, n):
        # Hole den aktuellen Zeitpunkt als datetime-Objekt
        now = datetime.now()

        # Hole alle anstehenden Jobs
        pending_jobs = [job for job in schedule.jobs if job.next_run <= now]
    
        # Sortiere sie nach dem nächst fälligen Laufzeitpunkt
        pending_jobs.sort(key=lambda job: job.next_run)
    
        # Führe nur die ersten n Jobs aus
        for job in pending_jobs[:n]:
            job.run()
            job.last_run = datetime.now()  # Update last_run nach der Ausführung
            job._schedule_next_run()  # Plane den nächsten Lauf des Jobs


    def run_scheduler(self, conn):

        self.write('Starting scheduler core', stdout=True)
        # ensure jobs are loaded on start
        df_jobs = self.load_schedule_from_db(conn)
        self.schedule_tasks(df_jobs, conn)
        
        while True:
            
#            if 1:
            try:            
                with self.lock:
                    if len(self.load_processes_from_db(conn)) == 0:
                        self.run_next_n_jobs(1)
                self.monitor_tasks(conn)
            except Exception:
                pass

            # then reload jobs each 5 minutes
            if ( int(time.perf_counter()) % 60) == 1:
                df_jobs = self.load_schedule_from_db(conn)
                self.schedule_tasks(df_jobs, conn)

                if not self.runserverinweb:
                    self.write('Standalone mode', stdout=True)
                    self.write("Idle until: " + schedule.next_run().strftime("%d.%m.%Y %H:%M"), stdout=True )
                    self.write('Current jobs planned', stdout=True)
                    for job in schedule.jobs:
                        self.write(job, stdout=True)

            time.sleep(1)

    def jobs(self, conn):
        
        # Laden und Bearbeiten der Daten aus der Datenbank
        st.header("Current scheduled jobs")

        df = self.load_schedule_from_db(conn)
        edited_df = st.data_editor(df, num_rows="dynamic")

        if st.button("Save"):
            self.save_schedule_to_db(conn, edited_df)
            self.schedule_tasks(edited_df, conn)
            st.success("Jobs saved.")


    @st.fragment(run_every='60s')
    def running_jobs(self, conn):

        # Prozesse abrufen
        st.header("Running Jobs")
    
        processes = self.load_processes_from_db(conn)
        if processes:
            # Anzeige von Jobname und Task-ID
            process_display = [f"Job: {job_name} (Task ID: {task_id}, PID: {pid})" for _, task_id, pid, job_name in processes]
            task_to_terminate = st.selectbox("Choose a Job to terminate", process_display)
            if self.enable_logging:                
                self.logger.info(process_display)
        
            if st.button("Terminate job"):
                selected_task = process_display.index(task_to_terminate)
                _, task_id, pid, job_name = processes[selected_task]
                self.terminate_process(pid)
                self.delete_process_from_db(conn, task_id)
                msg = f"Process {task_to_terminate} terminated."
                if self.enable_logging:                
                    self.logger.info(msg)
                st.success(msg)
        else:
            self.write("No running jobs found.")


    @st.fragment(run_every="120s")
    def running_processes(self):
        # Prozesse abrufen

        st.header("All running processes")

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

    # Streamlit-Seite
    def render(self):

        self.jobs(self.conn) 
        self.running_jobs(self.conn)
#        self.write("Pending job@ " + schedule.next_run().strftime("%d.%m.%Y %H:%M") )


    # Streamlit-Seite
    def render_processes(self):
               
        self.running_processes()


    # Streamlit-Seite
    def render_tabs(self):

        scheduler_tabs = st.empty()
        
        tabs = scheduler_tabs.tabs(["Schedules", "Running jobs", "Running processes"])

        tab_scheduler = tabs[0]
        tab_running_jobs = tabs[1]
        tab_running_procs = tabs[2]

        with tab_scheduler:
            self.jobs(self.conn)
 
        with tab_running_jobs:
            self.running_jobs(self.conn)
               
        with tab_running_procs:
            self.running_processes()


if __name__ == "__main__":
    
    # default db to use
    db_file = 'scheduler.db'
     
    # or load a different db
    if len(sys.argv) > 1:
        db_file = sys.argv[1]

    print('Schedserver running')
    
    os.chdir(wd.Tools().get_path(path = '', file_name=''))
    database = wd.Tools().get_path(path = '', file_name=db_file)

    scheduler = Scheduler(database=db_file, log_file_name='schedserver.log')
    scheduler.run_scheduler(scheduler.conn)

    pass