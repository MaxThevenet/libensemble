"""
Wrapper for MOP-MOD
"""
import numpy as np
from scipy.io import FortranFile  # for reading/writing unformatted binary data
from os import system  # for issuing batch commands


def mop_mod_wrapper(H, persis_info, gen_specs, _):
    """
    Generates ``gen_specs['gen_batch_size']`` points uniformly over the domain
    defined by ``gen_specs['ub']`` and ``gen_specs['lb']``.

    :See:
        ``libensemble/tests/regression_tests/test_6-hump_camel_uniform_sampling.py``
    """
    # First get the problem dimensions and data
    ub = gen_specs['ub']  # upper bounds
    lb = gen_specs['lb']  # lower bounds
    d = len(lb)  # design dimension
    p = gen_specs['num_obj']  # objective dimension
    nb = gen_specs['gen_batch_size']  # preferred batch size
    n = np.size(H['f'][:, 0])  # size of database in the history array

    if len(H) == 0:
        # Write initialization data to the mop.io file for MOP_INIT
        fp1 = FortranFile('mop.io', 'w')
        fp1.write_record(np.int32(d), np.int32(p), np.int32(nb))
        fp1.write_record(np.array(lb, dtype=np.float64),
                         np.array(ub, dtype=np.float64))
        fp1.close()
        system("mop_initializer")
    else:
        # Write unformatted problem dimensions to the mop.io file
        fp1 = FortranFile('mop.io', 'w')
        fp1.write_record(np.int32(d), np.int32(p), np.int32(n), np.int32(nb))
        fp1.write_record(np.array(lb, dtype=np.float64),
                         np.array(ub, dtype=np.float64))
        fp1.close()
        # Write unformatted history to the mop.dat file, to be read by MOP_MOD
        fp2 = FortranFile('mop.dat', 'w')
        fp2.write_record(np.int32(d), np.int32(p))
        for i in range(n):
            fp2.write_record(np.float64(H['x'][i, :]), np.float64(H['f'][i, :]))
        fp2.close()
        # Call MOP_MOD from command line
        system("mop_generator")

    # Read unformatted list of candidates from mop.io file
    fp1 = FortranFile('mop.io', 'r')
    cand_pts = fp1.read_record(np.float64)
    fp1.close()

    # Get the true batch size
    b = cand_pts.size // d

    # Read record
    O = np.zeros(b, dtype=gen_specs['out'])
    for i in range(0, b):
        O['x'][i] = cand_pts[d*i:d*(i+1)]
    # O['x'] = persis_info['rand_stream'].uniform(lb, ub, (nb, d))

    return O, persis_info