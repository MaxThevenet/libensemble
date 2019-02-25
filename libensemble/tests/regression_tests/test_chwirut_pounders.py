# """
# Runs libEnsemble with a simple uniform random sample on one instance of the GKLS
# problem. # Execute via the following command:

# mpiexec -np 4 python3 test_chwirut_pounders.py

# """

from __future__ import division
from __future__ import absolute_import

import sys, os             # for adding to path
import numpy as np

from libensemble.libE import libE
from libensemble.tests.regression_tests.common import parse_args

# Parse args for test code
nworkers, is_master, libE_specs, _ = parse_args()
if libE_specs['comms'] == 'local':
    quit()

# Import sim_func
from libensemble.sim_funcs.chwirut1 import chwirut_eval, EvaluateJacobian

# Import gen_func
from libensemble.gen_funcs.aposmm import aposmm_logic

script_name = os.path.splitext(os.path.basename(__file__))[0]

### Declare the run parameters/functions
m = 214
n = 3
max_sim_budget = 10

sim_specs = {'sim_f': chwirut_eval,
             'in': ['x'],
             'out': [('f',float), ('fvec',float,m),
                     ],
             'combine_component_func': lambda x: np.sum(np.power(x,2)),
             }

gen_out = [('x',float,n),
      ('x_on_cube',float,n),
      ('sim_id',int),
      ('priority',float),
      ('local_pt',bool),
      ('known_to_aposmm',bool), # Mark known points so fewer updates are needed.
      ('dist_to_unit_bounds',float),
      ('dist_to_better_l',float),
      ('dist_to_better_s',float),
      ('ind_of_better_l',int),
      ('ind_of_better_s',int),
      ('started_run',bool),
      ('num_active_runs',int), # Number of active runs point is involved in
      ('local_min',bool),
      ('pt_id',int), # To be used by APOSMM to identify points evaluated by different simulations
      ]

gen_specs = {'gen_f': aposmm_logic,
             'in': [o[0] for o in gen_out] + ['f', 'fvec', 'returned'],
             'out': gen_out,
             'lb': -2*np.ones(3),
             'ub':  2*np.ones(3),
             'initial_sample_size': 5, # All 214 residuals must be done
             'localopt_method': 'pounders',
             'dist_to_bound_multiple': 0.5,
             'grtol': 1e-4,
             'gatol': 1e-4,
             'frtol': 1e-15,
             'fatol': 1e-15,
             'components': m,
             'num_active_gens': 1,
             'batch_mode': True,
             }

exit_criteria = {'sim_max': max_sim_budget, # must be provided
                 'elapsed_wallclock_time': 300
                  }

np.random.seed(1)
persis_info = {}
for i in range(1,nworkers+1):
    persis_info[i] = {'rand_stream': np.random.RandomState(i)}

persis_info[1] = {'run_order': {},
                  'old_runs': {},
                  'total_runs': 0,
                  'rand_stream': np.random.RandomState(1)}
# Perform the run
H, persis_info, flag = libE(sim_specs, gen_specs, exit_criteria, persis_info)

if is_master:
    assert flag == 0
    assert len(H) >= max_sim_budget
    short_name = script_name.split("test_", 1).pop()
    filename = short_name + '_results_after_evals=' + str(max_sim_budget) + '_ranks=' + str(nworkers+1)
    print("\n\n\nRun completed.\nSaving results to file: " + filename)
    np.save(filename, H)

    # Calculating the Jacobian at the best point (though this information was not used by pounders)
    J = EvaluateJacobian(H['x'][np.argmin(H['f'])])
    assert np.linalg.norm(J) < 2000

