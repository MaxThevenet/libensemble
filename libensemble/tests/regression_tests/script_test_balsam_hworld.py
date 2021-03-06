# This script is submitted as an app and job to Balsam. The job submission is
#   via 'balsam launch' executed in the test_balsam_hworld.py script.

import os
import numpy as np
import mpi4py
from mpi4py import MPI

from libensemble.balsam_controller import BalsamJobController
from libensemble.message_numbers import WORKER_DONE, WORKER_KILL_ON_ERR, WORKER_KILL_ON_TIMEOUT, JOB_FAILED
from libensemble.libE import libE
from libensemble.sim_funcs.job_control_hworld import job_control_hworld
from libensemble.gen_funcs.sampling import uniform_random_sample
from libensemble.utils import add_unique_random_streams

mpi4py.rc.recv_mprobe = False  # Disable matching probes


# Slighty different due to working directory not being /regression_tests
def build_simfunc():
    import subprocess
    print('Balsam job launched in: {}'.format(os.getcwd()))
    buildstring = 'mpicc -o my_simjob.x libensemble/tests/unit_tests/simdir/my_simjob.c'
    subprocess.check_call(buildstring.split())


libE_specs = {'comm': MPI.COMM_WORLD,
              'comms': 'mpi',
              'save_every_k_sims': 400,
              'save_every_k_gens': 20,
              }

nworkers = MPI.COMM_WORLD.Get_size() - 1
is_master = MPI.COMM_WORLD.Get_rank() == 0

cores_per_job = 1

sim_app = './my_simjob.x'
if not os.path.isfile(sim_app):
    build_simfunc()

jobctrl = BalsamJobController(auto_resources=False)
jobctrl.register_calc(full_path=sim_app, calc_type='sim')

sim_specs = {'sim_f': job_control_hworld,
             'in': ['x'],
             'out': [('f', float), ('cstat', int)],
             'user': {'cores': cores_per_job}}

gen_specs = {'gen_f': uniform_random_sample,
             'in': ['sim_id'],
             'out': [('x', float, (2,))],
             'user': {'lb': np.array([-3, -2]),
                      'ub': np.array([3, 2]),
                      'gen_batch_size': nworkers,
                      'num_active_gens': 1}
             }

persis_info = add_unique_random_streams({}, nworkers + 1)

exit_criteria = {'elapsed_wallclock_time': 35}

# Perform the run
H, persis_info, flag = libE(sim_specs, gen_specs, exit_criteria,
                            persis_info, libE_specs=libE_specs)

if is_master:
    print('\nChecking expected job status against Workers ...\n')
    calc_status_list_in = np.asarray([WORKER_DONE, WORKER_KILL_ON_ERR,
                                      WORKER_KILL_ON_TIMEOUT,
                                      JOB_FAILED, 0])
    calc_status_list = np.repeat(calc_status_list_in, nworkers)

    print("Expecting: {}".format(calc_status_list))
    print("Received:  {}\n".format(H['cstat']))

    assert np.array_equal(H['cstat'], calc_status_list), "Error - unexpected calc status. Received: " + str(H['cstat'])

    # Check summary file:
    print('Checking expected job status against job summary file ...\n')

    calc_desc_list_in = ['Completed', 'Worker killed job on Error',
                         'Worker killed job on Timeout', 'Job Failed',
                         'Manager killed on finish']

    # Repeat N times for N workers and insert Completed at start for generator
    calc_desc_list = ['Completed'] + calc_desc_list_in*nworkers

    print("\n\n\nRun completed.")
