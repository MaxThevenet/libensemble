# """
# Runs libEnsemble on the 6-hump camel problem. Documented here:
#    https://www.sfu.ca/~ssurjano/camel6.html
#
# Execute via one of the following commands (e.g. 3 workers):
#    mpiexec -np 4 python3 test_6-hump_camel_uniform_sampling.py
#    python3 test_6-hump_camel_uniform_sampling.py --nworkers 3 --comms local
#    python3 test_6-hump_camel_uniform_sampling.py --nworkers 3 --comms tcp
#
# The number of concurrent evaluations of the objective function will be 4-1=3.
# """

# Do not change these lines - they are parsed by run-tests.sh
# TESTSUITE_COMMS: mpi local tcp
# TESTSUITE_NPROCS: 2 4

import numpy as np

# Import libEnsemble items for this test
from libensemble.libE import libE
from libensemble.sim_funcs.six_hump_camel import six_hump_camel as sim_f
from libensemble.gen_funcs.sampling import uniform_random_sample as gen_f
from libensemble.tests.regression_tests.common import parse_args, save_libE_output, per_worker_stream
from libensemble.tests.regression_tests.support import six_hump_camel_minima as minima

nworkers, is_master, libE_specs, _ = parse_args()

sim_specs = {'sim_f': sim_f,         # Function whose output is being minimized
             'in': ['x'],            # Keys to be given to the above function
             'out': [('f', float)],  # Output from the function being minimized
             'save_every_k': 400,    # Want progress saved every 400 evals
             }
# end_sim_specs_rst_tag

gen_specs = {'gen_f': gen_f,               # Tell libE function generating sim_f input
             'out': [('x', float, (2,))],  # Tell libE gen_f output, type, size
             'save_every_k': 300,          # Tell libE to save every 300 gen entries
             'user': {'gen_batch_size': 500,        # Tell gen_f how much to generate per call
                      'lb': np.array([-3, -2]),     # Tell gen_f lower bounds
                      'ub': np.array([3, 2]),       # Tell gen_f upper bounds
                     }
             }
# end_gen_specs_rst_tag

persis_info = per_worker_stream({}, nworkers + 1)

exit_criteria = {'gen_max': 501, 'elapsed_wallclock_time': 300}

# Perform the run
H, persis_info, flag = libE(sim_specs, gen_specs, exit_criteria, persis_info,
                            libE_specs=libE_specs)

if is_master:
    assert flag == 0

    tol = 0.1
    for m in minima:
        assert np.min(np.sum((H['x'] - m)**2, 1)) < tol

    print("\nlibEnsemble found the 6 minima within a tolerance " + str(tol))
    save_libE_output(H, persis_info, __file__, nworkers)
