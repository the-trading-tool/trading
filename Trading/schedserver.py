import pandas as pd
import time
from datetime import datetime
import sqlite3
import subprocess
import psutil
import shlex
from threading import Thread, Lock, Semaphore
from tradinglib import tools as wd
from tradinglib import system_config as sysconf
import logging
import sys
import os

import schedule

class Scheduler:

    def __init__(self, database="scheduler.db", run_in_browser=False, log_file_name='scheduler_standalone.log', enable_logging=False, username='admin', is_admin=False):
        self.database = wd.Tools().get_path(path='database', file_name=database)
        self.conn = self.init_db()
        self.lock = Lock()
        self.run_in_browser = run_in_browser
        self.username = username
        self.is_admin = is_admin
        self.sysconf = sysconf.SystemConfig(username=username, is_admin=is_admin)
        self.enable_logging = self.sysconf.get_value('logging', enable_logging)

        self.max_parallel_jobs = 4
        self.running_jobs = set()
        self.thread_limiter = Semaphore(self.max_parallel_jobs)

        if self.enable_logging:
            self.logger = logging.getLogger()
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')

            file_handler = logging.FileHandler(wd.Tools().get_path(path='', file_name=log_file_name))
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)

            self.logger.addHandler(file_handler)
            self.write(f'Scheduler log initialized {log_file_name}', stdout=True)

    def write(self, data, stdout=False, printonly=False):
        if stdout:
            print(data)
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
        return c.fetchall()

    def check_process_exists_db(self, conn, job_name):
        c = conn.cursor()
        c.execute("SELECT * FROM processes WHERE job_name = ?", (job_name,))
        return c.fetchall()

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
            
            
if __name__ == "__main__":
    
#    os.environ["TradingDB"]=r'C:\\Users\\Kurt\\Documents\\Trading2\\database'

    # default db to use
    db_file = 'scheduler.db'
    enable_logging = False     
    if len(sys.argv) > 1:
        intervals = []
        periods = []
        pos = 0
        arg = ''
        for i in range(1,len(sys.argv)):
            try:
                pos = sys.argv[i].index('=')
            except Exception:
                pass
            if pos > 0:
                try:
                    (arg, val) = sys.argv[i].split('=')
                    if arg.strip().lower() == 'db':
                        db_file = val.strip()
                except Exception:
                    pass
            if sys.argv[i][:1] == '/':
                arg = sys.argv[i][1:]
                if arg.lower() == 'logging':
                    enable_logging = True    
        if pos == '' and arg == '':
            print("Wrong arguments. Use pairs of 'arg=value', e.g. 'db=scheduler.db'")
            exit()

    print('Schedserver running')
    os.chdir(wd.Tools().get_path(path = '', file_name=''))
    database = wd.Tools().get_path(path = '', file_name=db_file)

#    cmdline = '"/home/cloogidoo/.venv/bin/python3" "/home/cloogidoo/public_html/cloud/data/Kurt/files/Kurt_NB/Trading/asset_scanner.py" "DAX"'
#    command = shlex.split(cmdline)
#    print(command)
#    process = subprocess.Popen(command, shell=False)
#    while True:
#        time.sleep(1)
    scheduler = Scheduler(database=db_file, log_file_name='schedserver.log', enable_logging=enable_logging)
    scheduler.run_scheduler(scheduler.conn)
