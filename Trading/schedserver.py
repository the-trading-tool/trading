import sys
import os
from tradinglib import tools as wd
from tradinglib.scheduler import Scheduler

if __name__ == "__main__":

    db_file = 'scheduler.db'
    enable_logging = False

    if len(sys.argv) > 1:
        pos = 0
        arg = ''
        for i in range(1, len(sys.argv)):
            try:
                pos = sys.argv[i].index('=')
            except Exception:
                pos = 0
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

    print('Schedserver running')
    os.chdir(wd.Tools().get_path(path='', file_name=''))

    scheduler = Scheduler(database=db_file, log_file_name='schedserver.log', enable_logging=enable_logging)
    scheduler.run_scheduler(scheduler.conn)
