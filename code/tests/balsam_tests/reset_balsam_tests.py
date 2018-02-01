#!/usr/bin/env python

import balsam.launcher.dag as dag

dag.BalsamJob.objects.filter(name__contains='outfile').delete()

for job in dag.BalsamJob.objects.filter(name__contains='job_test_balsam'):
    job.update_state('READY')
    job.save()
