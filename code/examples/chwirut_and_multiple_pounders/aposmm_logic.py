from __future__ import division
from __future__ import absolute_import

import sys, os
import numpy as np
import scipy as sp
from scipy import spatial
from mpi4py import MPI

from numpy.lib.recfunctions import merge_arrays

from math import log

from petsc4py import PETSc
import nlopt

def aposmm_logic(H,gen_out,params):

    """
    Receives the following data from H:
        'x_on_cube', 'fvec', 'f', 'local_pt', 'iter_plus_1_in_run_id',
        'dist_to_unit_bounds', 'dist_to_better_l', 'dist_to_better_s',
        'ind_of_better_l', 'ind_of_better_s', 'started_run', 'num_active_runs', 'local_min'

    Most are self-explanatory. The columns of 'iter_plus_1_in_run_id'
    corresponding to each run. Rows of 'iter_plus_1_in_run_id' contain the
    iteration number (plus 1) of a point in a given run

    import IPython; IPython.embed()
    import ipdb; ipdb.set_trace() 

    When using libEnsemble to do individual component evaluations, APOSMM will
    return num_components copies of each point, but each component=0 version of
    the point will only be considered when 
        - deciding where to start a run, 
        - best nearby point, 
        - storing the order of the points is the run
        - storing the combined objective function value
        - etc

    Description of intermediate variables in aposmm_logic:

    n:                domain dimension
    c_flag:           True if giving libEnsemble individual components of fvec to evaluate. (Note if c_flag is True, APOSMM will only use the com
    n_s:              the number of complete evaluations (not just component evaluations)
    updated_inds:     indices of H that have been updated (and so all their information must be sent back to libE manager to update) 
    O:                new points to be sent back to the history
                     
                     
    x_new:            when re-running a local opt method to get the next point: stores the first new point requested by a local optimization method
    pt_in_run:        when re-running a local opt method to get the next point: counts function evaluations to know when a new point is given
    total_pts_in_run: when re-running a local opt method to get the next point: total evaluations in run to be incremented

    starting_inds:    indices where a runs should be started.
    active_runs:      indices of active local optimization runs (currently saved to disk between calls to APOSMM)
    sorted_run_inds:  indices of the considered run (in the order they were requested by the localopt method)
    x_opt:            the reported minimum from a localopt run (disregarded unless exit_code isn't 0)
    exit_code:        0 if a new localopt point has been found, otherwise it's the NLopt/POUNDERS code 
    samples_needed:   counts the number of additional uniformly drawn samples needed
    """

    n = len(params['ub'])

    if 'single_component_at_a_time' in params and params['single_component_at_a_time']:
        c_flag = True
    else:
        c_flag = False


    if c_flag:
        n_s = np.sum(np.logical_and.reduce((~H['local_pt'], H['returned'], H['obj_component']==0))) # Number of returned sampled points
    else:
        n_s = np.sum(np.logical_and(~H['local_pt'], H['returned'])) # Number of returned sampled points

    # Rather than build up a large output, we will just make changes in the 
    # given H, and then send back the rows corresponding to updated H entries. 
    
    updated_inds = np.array([])

    O = np.empty(0,dtype=gen_out)

    # np.save('H_after_' + str(n_s) + '_complete_evals',H)
    # np.save('H_len_' + str(len(H)), H)
    # sys.exit('a')
    # import ipdb; ipdb.set_trace()
    if n_s >= params['initial_sample']:
        # sys.exit('a')

        # These are used to find a next point from the local optimization algorithm
        global x_new, pt_in_run, total_pts_in_run

        # Update distances for any new points that have been evaluated
        updated_inds = update_history_dist(H, params, c_flag)        

        # Find any indices that haven't started runs yet but should
        starting_inds = decide_where_to_start_localopt(H, n_s, params['rk_const'])        
        updated_inds = np.unique(np.append(updated_inds,starting_inds))
                
        active_runs = get_active_run_inds(H)
        
        for ind in starting_inds:
            # Find the run number 
            if np.max(H['iter_plus_1_in_run_id']) == 0:
                new_run_col = 0
            else:
                new_run_col = np.max(np.where(np.sum(H['iter_plus_1_in_run_id'],axis=0))[0])+1
        
            H['started_run'][ind] = 1
            H['num_active_runs'][ind] += 1
            H['iter_plus_1_in_run_id'][ind,new_run_col] = 1
            active_runs.update([new_run_col])
            
        # Find the next point for any uncompleted runs. I currently save this
        # information to file and re-load. (Given a history of points, I don't
        # know how to tell if the run is finished. Just looking for a point
        # with a declared local_min is not always going to work, when a point
        # is in a minimum in one run, but an evaluated point in another run)
                
        inactive_runs = set([])

        for run in active_runs:
            sorted_run_inds = np.where(H['iter_plus_1_in_run_id'][:,run])[0]
            sorted_run_inds.sort()
                        
            if all(H['returned'][sorted_run_inds]):

                x_new = np.ones((1,n))*np.inf; pt_in_run = 0; total_pts_in_run = len(sorted_run_inds)
                x_opt, exit_code = advance_localopt_method(H, params, sorted_run_inds, c_flag)

                if np.isinf(x_new).all():
                    # No new point was added. Hopefully at a minimum 
                    if exit_code > 0:
                        update_history_optimal(x_opt, H, sorted_run_inds)
                        inactive_runs.add(run)
                    else:
                        sys.exit("No new point requested by localopt method, but not declared optimal")
                else: 
                    add_points_to_O(O, x_new, len(H), updated_inds, params, c_flag, local_flag=1, sorted_run_inds=sorted_run_inds, run=run)

        for i in inactive_runs:
            active_runs.remove(i)

        update_existing_runs_file(active_runs)

    samples_needed = params['min_batch_size'] - len(O)
    if samples_needed > 0:
        x_new = np.random.uniform(0,1,(samples_needed,n))

        add_points_to_O(O, x_new, len(H), updated_inds, params, c_flag)

    O = np.append(H[[o[0] for o in gen_out]][updated_inds.astype('int')],O)

    return O

def add_points_to_O(O, pts, len_H, updated_inds, params, c_flag, local_flag=0, sorted_run_inds=[], run=[]):
    assert(not local_flag or len(pts) == 1), "add_points_to_O does not support this functionality"

    ub = params['ub']
    lb = params['lb']
    if c_flag:
        m = params['components']

        assert (len_H % m == 0), "Number of points in len_H not congruent to 0 mod 'components'"
        pt_ids = np.sort(np.tile(np.arange(len_H/m,len_H/m + len(pts)),(1,len(pts))))
        pts = np.tile(pts,(m,1))

    num_pts = len(pts)
    original_len_O = len(O)

    O.resize(len(O)+num_pts,refcheck=False) # Adds (num_pts) rows of zeros to O 

    O['x_on_cube'][-num_pts:] = pts
    O['x'][-num_pts:] = pts*(ub-lb)+lb
    O['sim_id'][-num_pts:] = np.arange(len_H+original_len_O,len_H+original_len_O+num_pts)
    O['local_pt'][-num_pts:] = local_flag

    O['dist_to_unit_bounds'][-num_pts:] = np.inf
    O['dist_to_better_l'][-num_pts:] = np.inf
    O['dist_to_better_s'][-num_pts:] = np.inf
    O['ind_of_better_l'][-num_pts:] = -1
    O['ind_of_better_s'][-num_pts:] = -1

    if c_flag:
        O['obj_component'][-num_pts:] = np.tile(range(0,m),(num_pts//m,1))
        O['pt_id'][-num_pts:] = pt_ids
    
    if local_flag:
        O['iter_plus_1_in_run_id'][-num_pts,run] = len(sorted_run_inds)+1
        O['num_active_runs'][-num_pts] += 1
        O['priority'][-num_pts:] = 1
    else:
        if c_flag:
            # p_tmp = np.sort(np.tile(np.random.uniform(0,1,num_pts/m),(m,1))) # If you want all "duplicate points" to have the same priority (meaning LibE gives them all at once)
            p_tmp = np.random.uniform(0,1,num_pts)
        else:
            p_tmp = np.random.uniform(0,1,num_pts)
        O['priority'][-num_pts:] = p_tmp

def get_active_run_inds(H):
    filename = 'active_runs.txt'
    if os.path.exists(filename) and os.stat(filename).st_size > 0:
        if np.max(H['iter_plus_1_in_run_id']) == 0:
            print('Removing old active runs file')
            os.remove(filename)
            return set([])
        else:
            a = np.loadtxt(filename,dtype='int')
            return set(np.atleast_1d(a))
    else:
        return set([])
    
def update_existing_runs_file(active_runs):    
    filename = 'active_runs.txt'    
    np.savetxt(filename,np.array(list(active_runs),dtype='int'), fmt='%i')

def update_history_dist(H, params, c_flag):

    n = len(H['x_on_cube'][0])

    updated_inds = np.array([],dtype='int')

    new_inds = np.where(~H['known_to_aposmm'])[0]

    if c_flag:
        for v in np.unique(H['pt_id'][new_inds]):
            inds = H['pt_id']==v
            H['f'][inds] = np.inf
            H['f'][np.where(inds)[0][0]] = params['combine_component_func'](H['f_i'][inds])

        p = np.logical_and(H['returned'],H['obj_component']==0)
    else:
        p = H['returned']

    H['known_to_aposmm'][new_inds] = True # These points are now known to APOSMM

    # Loop over new returned points and update their distances
    for new_ind in new_inds:
        if p[new_ind]:
            # Compute distance to boundary
            H['dist_to_unit_bounds'][new_ind] = min(min(np.ones(n) - H['x_on_cube'][new_ind]),min(H['x_on_cube'][new_ind] - np.zeros(n)))

            dist_to_all = sp.spatial.distance.cdist(np.atleast_2d(H['x_on_cube'][new_ind]), H['x_on_cube'][p], 'euclidean').flatten()
            new_better_than = H['f'][new_ind] < H['f'][p]

            # Update any other points if new_ind is closer and better
            if H['local_pt'][new_ind]:
                updates = np.where(np.logical_and(dist_to_all < H['dist_to_better_l'][p], new_better_than))[0]
                H['dist_to_better_l'][updates] = dist_to_all[updates]
                H['ind_of_better_l'][updates] = new_ind
            else:
                updates = np.where(np.logical_and(dist_to_all < H['dist_to_better_s'][p], new_better_than))[0]
                H['dist_to_better_s'][updates] = dist_to_all[updates]
                H['ind_of_better_s'][updates] = new_ind
            updated_inds = np.append(updated_inds, updates)

            # If we allow equality, have to prevent new_ind from being its own "better point"
            better_than_new_l = np.logical_and.reduce((H['f'][new_ind] >= H['f'][p],  H['local_pt'][p], H['sim_id'][p] != new_ind))
            better_than_new_s = np.logical_and.reduce((H['f'][new_ind] >= H['f'][p], ~H['local_pt'][p], H['sim_id'][p] != new_ind))

            # Who is closest to ind and better 
            if np.any(better_than_new_l):
                H['dist_to_better_l'][new_ind] = dist_to_all[better_than_new_l].min()
                H['ind_of_better_l'][new_ind] = H['sim_id'][p][np.ix_(better_than_new_l)[0][dist_to_all[better_than_new_l].argmin()]]

            if np.any(better_than_new_s):
                H['dist_to_better_s'][new_ind] = dist_to_all[better_than_new_s].min()
                H['ind_of_better_s'][new_ind] = H['sim_id'][p][np.ix_(better_than_new_s)[0][dist_to_all[better_than_new_s].argmin()]]

            # if not ignore_L8:
            #     r_k = calc_rk(H, len(H['x_on_cube'][0]), n_s, rk_const, lhs_divisions)
            #     H['worse_within_rk'][new_ind][p] = np.logical_and.reduce((H['f'][new_ind] <= H['f'][p], dist_to_all <= r_k))

            #     # Add trues if new point is 'worse_within_rk' 
            #     inds_to_change = np.logical_and.reduce((H['dist_to_all'][p,new_ind] <= r_k, H['f'][new_ind] >= H['f'][p], H['sim_id'][p] != new_ind))
            #     H['worse_within_rk'][inds_to_change,new_ind] = True

            #     if not H['local_pt'][new_ind]:
            #         H['worse_within_rk'][H['dist_to_all'] > r_k] = False 


    return np.unique(np.append(new_inds, updated_inds))



def update_history_optimal(x_opt, H, run_inds):

    opt_ind = np.where(np.equal(x_opt,H['x_on_cube']).all(1))[0]
    assert len(opt_ind) == 1, "Why not one optimal point?"

    H['local_min'][opt_ind] = 1
    H['num_active_runs'][run_inds] -= 1



def advance_localopt_method(H, params, sorted_run_inds, c_flag):
    global x_new

    while 1: 
        if c_flag:
            # import ipdb; ipdb.set_trace()
            # Run_H = H[['x_on_cube','f','fvec']][sorted_run_inds] 
            Run_H = H[['x_on_cube','f']][sorted_run_inds] 

            Run_H_F = np.zeros(len(Run_H),dtype=[('fvec','float',params['components'])])

            for i in range(len(Run_H)):
                pt_id = H['pt_id'][sorted_run_inds[i]] 
                for j in range(params['components']):
                    Run_H_F['fvec'][i][j] = H['f_i'][np.logical_and(H['pt_id']==pt_id, H['obj_component']==j)]

            Run_H = merge_arrays([Run_H,Run_H_F],flatten=True)

        else:
            Run_H = H[['x_on_cube','f','fvec']][sorted_run_inds] 
        if params['localopt_method'] in ['LN_SBPLX', 'LN_BOBYQA', 'LN_NELDERMEAD', 'LD_MMA']:
            try:
                x_opt, exit_code = set_up_and_run_nlopt(Run_H, params)
            except Exception as e:
                exit_code = 0
                print(e.__doc__)
                print(e.args)
                print(Run_H['x_on_cube'])

    
            if exit_code == 4:
                # NLopt gives the same point twice at an optimum, just set x_new back to inf.
                x_new = np.ones(np.shape(H['x_on_cube'][0]))*np.inf; 

        elif params['localopt_method'] in ['pounders']:
            try: 
                x_opt, exit_code = set_up_and_run_tao(Run_H, params)
            except Exception as e:
                exit_code = 0
                print(e.__doc__)
                print(e.args)

        else:
            sys.exit("Unknown localopt method")

        if np.equal(x_new,H['x_on_cube']).all(1).any():
            # import ipdb; ipdb.set_trace()
            sys.exit("Generated an already evaluated point")
        else:
            break

    return x_opt, exit_code




def set_up_and_run_nlopt(Run_H, params):
    """ Set up objective and runs nlopt

    Declares the appropriate syntax for our special objective function to read
    through Run_H, sets the parameters and starting points for the run.
    """

    def nlopt_obj_fun(x, grad, Run_H):
        if params['localopt_method'] in ['LN_SBPLX', 'LN_BOBYQA', 'LN_NELDERMEAD']:
            return look_in_history(x, Run_H)
        elif params['localopt_method'] in ['LD_MMA']:
            (f,g) = look_in_history_true_grad(x, Run_H)
            grad[:] = g;
            # print(x,f,grad)
            return f

    n = len(params['ub'])

    opt = nlopt.opt(getattr(nlopt,params['localopt_method']), n)

    lb = np.zeros(n)
    ub = np.ones(n)
    opt.set_lower_bounds(lb)
    opt.set_upper_bounds(ub)
    x0 = Run_H['x_on_cube'][0]

    # Care must be taken here because a too-large initial step causes nlopt to move the starting point!
    dist_to_bound = min(min(ub-x0),min(x0-lb))
    opt.set_initial_step(dist_to_bound)
    opt.set_maxeval(len(Run_H)+1) # evaluate one more point
    opt.set_min_objective(lambda x, grad: nlopt_obj_fun(x, grad, Run_H))
    opt.set_xtol_rel(params['xtol_rel'])
    
    x_opt = opt.optimize(x0)
    exit_code = opt.last_optimize_result()

    if exit_code == 5: # NLOPT code for exhausting budget of evaluations, so not at a minimum
        exit_code = 0

    return x_opt, exit_code


def set_up_and_run_tao(Run_H, params):
    """ Set up objective and runs PETSc on the comm_self communicator

    Declares the appropriate syntax for our special objective function to read
    through Run_H, sets the parameters and starting points for the run.
    """
    tao_comm = MPI.COMM_SELF
    n = len(params['ub'])
    m = len(Run_H['fvec'][0])

    def pounders_obj_func(tao, X, F, Run_H):
        F.array = look_in_history(X.array, Run_H, vector_return=True)
        return F

    def blmvm_obj_func(tao, X, G, Run_H):
        (f, grad) = look_in_history_fd_grad(X.array, Run_H)
        G.array = grad
        return f

    # Create starting point, bounds, and tao object
    x = PETSc.Vec().create(tao_comm)
    x.setSizes(n)
    x.setFromOptions()
    x.array = Run_H['x_on_cube'][0]
    lb = x.duplicate()
    ub = x.duplicate()
    lb.array = 0*np.ones(n)
    ub.array = 1*np.ones(n)
    tao = PETSc.TAO().create(tao_comm)
    tao.setType(params['localopt_method'])

    if params['localopt_method'] == 'pounders':
        f = PETSc.Vec().create(tao_comm)
        f.setSizes(m)
        f.setFromOptions()

        delta_0 = params['delta_0_mult']*np.min([np.min(ub.array-x.array), np.min(x.array-lb.array)])

        PETSc.Options().setValue('-tao_pounders_delta',str(delta_0))
        PETSc.Options().setValue('-pounders_subsolver_tao_type','bqpip')
        tao.setSeparableObjective(lambda tao, x, f: pounders_obj_func(tao, x, f, Run_H), f)
    elif params['localopt_method'] == 'blmvm':
        g = PETSc.Vec().create(tao_comm)
        g.setSizes(n)
        g.setFromOptions()
        tao.setObjectiveGradient(lambda tao, x, g: blmvm_obj_func(tao, x, g, Run_H))

    # Set everything for tao before solving
    PETSc.Options().setValue('-tao_max_funcs',str(total_pts_in_run+1))
    tao.setFromOptions()
    tao.setVariableBounds((lb,ub))
    # tao.setObjectiveTolerances(fatol=params['fatol'], frtol=params['frtol'])
    # tao.setGradientTolerances(grtol=params['grtol'], gatol=params['gatol'])
    tao.setTolerances(grtol=params['grtol'], gatol=params['gatol'])
    tao.setInitial(x)

    tao.solve(x)

    x_opt = tao.getSolution().getArray()
    exit_code = tao.getConvergedReason()
    # print(exit_code)
    # print(tao.view())
    # print(x_opt)

    if params['localopt_method'] == 'pounders':
        f.destroy()
    elif params['localopt_method'] == 'blmvm':
        g.destroy()

    lb.destroy()
    ub.destroy()
    x.destroy()
    tao.destroy()

    return x_opt, exit_code












def decide_where_to_start_localopt(H, n_s, rk_const, lhs_divisions=0, mu=0, nu=0, gamma_quantile=1):
    """
    Decide where to start a LocalOpt run

    Finds points in the history that satisfy the conditions (S1-S5 and L1-L8) in
    Table 1 of the paper: "A Batch, Derivative-free Algorithm for Finding
    Multiple Local Minima". To do this, we first identify sample points
    satisfying S2-S5. We then identify all localopt points that satisfy L1-L7.
    We then start from any sample point also satisfying S1. For L8 we use the
    pairwise distances from all Local to Local points and from all Sample to
    Local points to search through Local points to attempt to travel on an
    rk-ascent path to some Sample point

    We don't consider points in the history without function values. Also, note
    that mu and nu implicitly depend on the scaling that is happening with the
    domain. That is, adjusting lb/ub can make mu/nu start (resp. not start) at a
    point that didn't (resp. did) satisfy the mu/nu test prviously. 

    Parameters
    ----------
    H: numpy structured array
        History array storing rows for each point. 
    mu: nonnegative float
        Distance from the boundary that all starting points must satisfy
    nu: nonnegative float
        Distance from identified minima that all starting points must satisfy
    gamma_quantile: float in (0,1] 
        Only sample points whose function values are in the lower gamma_quantile can start localopt runs

    Returns
    ----------
    start_inds: list
        Indices where a local opt run should be started
    """

    n = len(H['x_on_cube'][0])
    r_k = calc_rk(H, n, n_s, rk_const)

    if nu > 0:
        test_2_through_5 = np.logical_and.reduce((
                H['returned'] == 1,          # have a returned function value
                H['dist_to_better_s'] > r_k, # no better sample point within r_k (L2)
               ~H['started_run'],            # have not started a run (L3)
                H['dist_to_unit_bounds'] >= mu, # have all components at least mu away from ub (L4)
                np.all(sp.spatial.distance.cdist(H['x_on_cube'], H['x_on_cube'][H['local_min']]) >= nu,axis=1) # distance nu away from known local mins (L5)
            ))
    else:
        test_2_through_5 = np.logical_and.reduce((
                H['returned'] == 1,          # have a returned function value
                H['dist_to_better_s'] > r_k, # no better sample point within r_k (L2)
               ~H['started_run'],            # have not started a run (L3)
                H['dist_to_unit_bounds'] >= mu, # have all components at least mu away from ub (L4)
            )) # (L5) is always true when nu = 0

    if gamma_quantile < 1:
        cut_off_value = np.sort(H['f'][~H['local_pt']])[np.floor(gamma_quantile*(sum(~H['local_pt'])-1)).astype('int')]
    else:
        cut_off_value = np.inf

    ### Find the indices of points that...
    sample_seeds = np.logical_and.reduce((
           ~H['local_pt'],               # are not localopt points
           H['f'] <= cut_off_value,      # have a small enough objective value
           ~np.isinf(H['f']),            # have a non-infinity objective value
           test_2_through_5,             # satisfy tests 2 through 5
         ))

    # Uncomment the following to test the effect of ignorning LocalOpt points
    # in APOSMM. This allows us to test a parallel MLSL.
    # return list(np.ix_(sample_seeds)[0])
           
    those_satisfying_S1 = H['dist_to_better_l'][sample_seeds] > r_k # no better localopt point within r_k
    sample_start_inds = np.ix_(sample_seeds)[0][those_satisfying_S1]

    ### Find the indices of points that...
    local_seeds = np.logical_and.reduce((
            H['local_pt'],               # are localopt points
            H['dist_to_better_l'] > r_k, # no better local point within r_k (L1)
           ~np.isinf(H['f']),            # have a non-infinity objective value
            test_2_through_5,
            H['num_active_runs'] == 0,   # are not in an active run (L6)
           ~H['local_min'] # are not a local min (L7)
         ))

    # if ignore_L8:
    if True:
        local_start_inds2 = list(np.ix_(local_seeds)[0])
    else:
        # ### For L8, search for an rk-ascent path for a sample point
        # lb = np.zeros(n)
        # ub = np.ones(n)
        # local_start_inds = []
        # for i in np.ix_(local_seeds)[0]:
        #     old_local_on_rk_ascent = np.array(np.zeros(len(H)), dtype=bool)
        #     local_on_rk_ascent = np.array(np.eye(len(H))[i,:], dtype=bool)

        #     done_with_i = False
        #     while not done_with_i and not np.array_equiv(old_local_on_rk_ascent, local_on_rk_ascent):
        #         old_local_on_rk_ascent = local_on_rk_ascent.copy()
        #         to_add = np.array(np.zeros(len(H)),dtype=bool)
        #         for j in np.ix_(local_on_rk_ascent)[0]:
        #             if keep_pdist: 
        #                 samples_on_rk_ascent_from_j = np.logical_and.reduce((H['f'][j] <= H['f'], ~H['local_pt'], H['dist_to_all'][:,j] <= r_k))
        #             else: 
        #                 ind_of_last = np.max(np.ix_(H['returned']))
        #                 pdist_vec = sp.spatial.distance.cdist([H['x_on_cube'][j]], H['x_on_cube'][:ind_of_last+1], 'euclidean').flatten()
        #                 pdist_vec = np.append(pdist_vec, np.zeros(len(H)-ind_of_last-1))
        #                 samples_on_rk_ascent_from_j = np.logical_and.reduce((H['f'][j] <= H['f'], ~H['local_pt'], pdist_vec <= r_k))

        #             if np.any(np.logical_and(samples_on_rk_ascent_from_j, sample_seeds)):
        #                 done_with_i = True
        #                 local_start_inds.append(i)
        #                 break

        #             if keep_pdist: 
        #                 feasible_locals_on_rk_ascent_from_j = np.logical_and.reduce((H['f'][j] <= H['f'], 
        #                                                                              np.all(ub - H['x_on_cube'] >= 0, axis=1),
        #                                                                              np.all(H['x_on_cube'] - lb >= 0, axis=1),
        #                                                                              H['local_pt'], 
        #                                                                              H['dist_to_all'][:,j] <= r_k
        #                                                                            ))
        #             else: 
        #                 feasible_locals_on_rk_ascent_from_j = np.logical_and.reduce((H['f'][j] <= H['f'], 
        #                                                                              np.all(ub - H['x_on_cube'] >= 0, axis=1),
        #                                                                              np.all(H['x_on_cube'] - lb >= 0, axis=1),
        #                                                                              H['local_pt'], 
        #                                                                              pdist_vec <= r_k
        #                                                                            ))

        #             to_add = np.logical_or(to_add, feasible_locals_on_rk_ascent_from_j)
        #         local_on_rk_ascent = to_add.copy()

        #     if not done_with_i: 
        #         # sys.exit("We have an i satisfying (L1-L7) but failing L8")
        #         print("\n\n We have ind %d satisfying (L1-L7) but failing L8 \n\n" % i)

        local_start_inds2 = []
        for i in np.ix_(local_seeds)[0]:
            old_pts_on_rk_ascent = np.array(np.zeros(len(H)), dtype=bool)
            pts_on_rk_ascent = H['worse_within_rk'][i]

            done_with_i = False
            while not done_with_i and not np.array_equiv(old_pts_on_rk_ascent, pts_on_rk_ascent):
                old_pts_on_rk_ascent = pts_on_rk_ascent.copy()
                to_add = np.array(np.zeros(len(H)),dtype=bool)
                for j in np.ix_(pts_on_rk_ascent)[0]:
                    to_add = np.logical_or(to_add, H['worse_within_rk'][i])
                pts_on_rk_ascent = to_add
                if np.any(np.logical_and(to_add, sample_seeds)):
                    done_with_i = True
                    local_start_inds2.append(i)
                    break
            if not done_with_i:
                print("Again, we have ind %d satisfying (L1-L7) but failing L8\n" % i)

        # assert local_start_inds.sort() == local_start_inds2.sort(), "Something didn't match up"
    # start_inds = list(sample_start_inds) + local_start_inds
    start_inds = list(sample_start_inds) + local_start_inds2
    return start_inds

def look_in_history(x, Run_H, vector_return=False):
    """ See if Run['x_on_cube'][pt_in_run] matches x, returning f or fvec, or saves x to
    x_new if every point in Run_H has been checked.
    """
    
    global pt_in_run, total_pts_in_run, x_new

    if vector_return:
        to_return = 'fvec'
    else:
        to_return = 'f'

    if pt_in_run < total_pts_in_run:
        # Return the value in history to the localopt algorithm. 
        if not np.allclose(x, Run_H['x_on_cube'][pt_in_run], rtol=1e-08, atol=1e-08):
            print(x,Run_H['x_on_cube'])
        assert np.allclose(x, Run_H['x_on_cube'][pt_in_run], rtol=1e-08, atol=1e-08), \
            "History point does not match Localopt point"
        f_out = Run_H[to_return][pt_in_run]
    elif pt_in_run == total_pts_in_run:
        # The history of points is exhausted. Save the requested point x to
        # x_new. x_new will be returned to the manager.
        x_new[:] = x
        f_out = 0.0
    else:
        # Just in case the local opt method requests more points after a new
        # point has been identified.
        f_out = 0.0

    pt_in_run += 1

    return f_out



def calc_rk(H, n, n_s, rk_const, lhs_divisions=0):
    """ Calculate the critical distance r_k """ 

    if lhs_divisions == 0:
        r_k = rk_const*(log(n_s)/n_s)**(1/n)
    else:
        k = np.floor(n_s/lhs_divisions).astype('int')
        if k <= 1: # to prevent r_k=0
            r_k = np.inf
        else:
            r_k = rk_const*(log(k)/k)**(1/n)

    return r_k