#!/usr/bin/env python
import os
import numpy as np
from forces_simf import run_forces  # Sim func from current dir

# Import libEnsemble modules
from libensemble.libE import libE
from libensemble.tools import parse_args, save_libE_output, add_unique_random_streams
from libensemble import libE_logger

USE_BALSAM = False
PERSIS_GEN = False

if PERSIS_GEN:
    from libensemble.gen_funcs.persistent_uniform_sampling import persistent_uniform as gen_f
    from libensemble.alloc_funcs.start_only_persistent import only_persistent_gens as alloc_f
else:
    from libensemble.gen_funcs.sampling import uniform_random_sample as gen_f
    from libensemble.alloc_funcs.give_sim_work_first import give_sim_work_first as alloc_f

libE_logger.set_level('INFO')  # INFO is now default

nworkers, is_master, libE_specs, _ = parse_args()

if is_master:
    print('\nRunning with {} workers\n'.format(nworkers))

sim_app = os.path.join(os.getcwd(), 'forces.x')

# Normally would be pre-compiled
if not os.path.isfile('forces.x'):
    if os.path.isfile('build_forces.sh'):
        import subprocess
        subprocess.check_call(['./build_forces.sh'])

# Normally the sim_input_dir will exist with common input which is copied for each worker. Here it starts empty.
# Create if no ./sim dir. See libE_specs['sim_input_dir']
os.makedirs('./sim', exist_ok=True)

# Create executor and register sim to it.
if USE_BALSAM:
    from libensemble.executors.balsam_executor import BalsamMPIExecutor
    exctr = BalsamMPIExecutor()  # Use auto_resources=False to oversubscribe
else:
    from libensemble.executors.mpi_executor import MPIExecutor
    exctr = MPIExecutor()  # Use auto_resources=False to oversubscribe
exctr.register_calc(full_path=sim_app, calc_type='sim')

# Note: Attributes such as kill_rate are to control forces tests, this would not be a typical parameter.

# State the objective function, its arguments, output, and necessary parameters (and their sizes)
sim_specs = {'sim_f': run_forces,         # Function whose output is being minimized
             'in': ['x'],                 # Name of input for sim_f
             'out': [('energy', float)],  # Name, type of output from sim_f
             'user': {'simdir_basename': 'forces',
                      'keys': ['seed'],
                      'cores': 2,
                      'sim_particles': 1e3,
                      'sim_timesteps': 5,
                      'sim_kill_minutes': 10.0,
                      'particle_variance': 0.2,
                      'kill_rate': 0.5}
             }
# end_sim_specs_rst_tag

# State the generating function, its arguments, output, and necessary parameters.
gen_specs = {'gen_f': gen_f,                  # Generator function
             'in': ['sim_id'],                # Generator input
             'out': [('x', float, (1,))],     # Name, type and size of data produced (must match sim_specs 'in')
             'user': {'lb': np.array([0]),             # Lower bound for random sample array (1D)
                      'ub': np.array([32767]),         # Upper bound for random sample array (1D)
                      'gen_batch_size': 1000,          # How many random samples to generate in one call
                      }
             }

alloc_specs = {'alloc_f': alloc_f,
               'out': [('allocated', bool)],
               'user': {'batch_mode': True,    # If true wait for all sims to process before generate more
                        'num_active_gens': 1}  # Only one active generator at a time
               }

if PERSIS_GEN:
    alloc_specs = {'alloc_f': alloc_f, 'out': [('given_back', bool)]}
else:
    alloc_specs = {'alloc_f': alloc_f,
                   'out': [('allocated', bool)],
                   'user': {'batch_mode': True,    # If true wait for all sims to process before generate more
                            'num_active_gens': 1}  # Only one active generator at a time
                   }

libE_specs['save_every_k_gens'] = 1000  # Save every K steps
libE_specs['sim_input_dir'] = './sim'   # Sim dir to be copied for each worker
libE_specs['profile_worker'] = False    # Whether to have libE profile on (default False)
libE_specs['use_worker_dirs'] = False   # Whether to create separate worker directories (default False)

# Maximum number of simulations
sim_max = 8
exit_criteria = {'sim_max': sim_max}

# Create a different random number stream for each worker and the manager
persis_info = {}
persis_info = add_unique_random_streams(persis_info, nworkers + 1)

H, persis_info, flag = libE(sim_specs, gen_specs, exit_criteria,
                            persis_info=persis_info,
                            alloc_specs=alloc_specs,
                            libE_specs=libE_specs)

# Save results to numpy file
if is_master:
    save_libE_output(H, persis_info, __file__, nworkers)
