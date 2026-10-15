"""The scheduler's "Pending job@" line names the job(s) that are due next."""
import os
import sys

import schedule

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradinglib.scheduler import Scheduler


def _run_task(**kwargs):
    pass


def test_naechster_job_mit_namen():
    s = schedule.Scheduler()
    s.every().day.at('22:00').do(_run_task, command='x', job_name='ASSET_PERFORMANCE_ETP')
    s.every().day.at('18:30').do(_run_task, command='y', job_name='YAHOO_D_DATA_ETP')
    label = Scheduler.next_job_label(s.jobs)
    assert label.endswith(' YAHOO_D_DATA_ETP') and '18:30' in label


def test_gleichzeitige_jobs_werden_zusammen_genannt():
    s = schedule.Scheduler()
    s.every().day.at('22:00').do(_run_task, job_name='ASSET_PERFORMANCE_2')
    s.every().day.at('22:00').do(_run_task, job_name='ASSET_PERFORMANCE_ETP')
    s.every().day.at('22:35').do(_run_task, job_name='CORRELATION_INDEX')
    assert Scheduler.next_job_label(s.jobs).endswith('22:00 ASSET_PERFORMANCE_2, ASSET_PERFORMANCE_ETP')


def test_ohne_jobs_leer():
    assert Scheduler.next_job_label([]) == ''
