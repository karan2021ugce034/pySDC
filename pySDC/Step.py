from pySDC import Level as levclass
from pySDC import Hooks as hookclass
from pySDC.Plugins.pysdc_helper import FrozenClass

import sys


class step(FrozenClass):
    """
    Step class, referencing most of the structure needed for the time-stepping

    This class bundles multiple levels and the corresponding transfer operators and is used by the methods
    (e.g. SDC and MLSDC). Status variables like the current time are hidden via properties and setters methods.

    Attributes:
        __t: current time (property time)
        __dt: current step size (property dt)
        __k: current iteration (property iter)
        __transfer_dict: data structure to couple levels and transfer operators
        levels: list of levels
        params: parameters given by the user
    """

    def __init__(self, description):
        """
        Initialization routine

        Args:
            description: parameters given by the user, will be added as attributes
        """

        # short helper class to add params as attributes
        class pars(FrozenClass):
            def __init__(self,params):

                self.maxiter = None

                for k,v in params.items():
                    setattr(self,k,v)

                self._freeze()

        # short helper class to bundle all status variables
        class status(FrozenClass):
            def __init__(self):
                self.iter = None
                self.stage = None
                self.slot = None
                self.first = None
                self.last = None
                self.pred_cnt = None
                self.done = None
                self.prev_done = None
                self._freeze()

        if 'step_params' in description:
            step_params = description['step_params']
        else:
            step_params = {}

        # set params and status
        self.params = pars(step_params)
        self.status = status()

        # empty attributes
        self.__transfer_dict = {}
        self.levels = []
        self.__prev = None
        self.__next = None

        self._freeze()

        self.__generate_hierarchy(description)


    def __generate_hierarchy(self,descr):
        """
        Routine to generate the level hierarchy for a single step

        This makes the explicit generation of levels in the frontend obsolete and hides a few dirty hacks here and
        there.

        Args:
            descr: dictionary containing the description of the levels as list per key
        """
        # assert the existence of all the keys we need to set up at least on level
        assert 'problem_class' in descr
        assert 'problem_params' in descr
        assert 'dtype_u' in descr
        assert 'dtype_f' in descr
        assert 'sweeper_class' in descr
        assert 'sweeper_params' in descr
        assert 'level_params' in descr

        # convert problem-dependent parameters consisting of dictionary of lists to a list of dictionaries with only a
        # single entry per key, one dict per level
        pparams_list = self.__dict_to_list(descr['problem_params'])
        lparams_list = self.__dict_to_list(descr['level_params'])
        swparams_list = self.__dict_to_list(descr['sweeper_params'])
        # put this newly generated list into the description dictionary (copy to avoid changing the original one)
        descr_new = descr.copy()
        descr_new['problem_params'] = pparams_list
        descr_new['level_params'] = lparams_list
        descr_new['sweeper_params'] = swparams_list
        # generate list of dictionaries out of the description
        descr_list = self.__dict_to_list(descr_new)

        # sanity check: is there a transfer class? is there one even if only a single level is specified?
        if len(descr_list) > 1:
            assert 'transfer_class' in descr_new
            assert 'transfer_params' in descr_new
        elif 'transfer_class' in descr_new:
            print('WARNING: you have specified transfer classes, but only a single level...')

        # generate levels, register and connect if needed
        for l in range(len(descr_list)):

            # check if we have a hook on this list. if not, use default class.
            if 'hook_class' in descr_list[l]:
                hook = descr_list[l]['hook_class']
            else:
                hook = hookclass.hooks

            # check if transfer parameters are needed
            if 'transfer_params' in descr_list[l]:
                tparams = descr_list[l]['transfer_params']
            else:
                tparams = {}

            if 'problem_params' in descr_list[l]:
                pparams = descr_list[l]['problem_params']
            else:
                pparams = {}

            L = levclass.level(problem_class      =   descr_list[l]['problem_class'],
                               problem_params     =   pparams,
                               dtype_u            =   descr_list[l]['dtype_u'],
                               dtype_f            =   descr_list[l]['dtype_f'],
                               sweeper_class      =   descr_list[l]['sweeper_class'],
                               sweeper_params     =   descr_list[l]['sweeper_params'],
                               level_params       =   descr_list[l]['level_params'],
                               hook_class         =   hook,
                               id                 =   'L'+str(l))

            self.register_level(L)

            if l > 0:
                self.connect_levels(transfer_class  = descr_list[l]['transfer_class'],
                                    transfer_params = tparams,
                                    fine_level      = self.levels[l-1],
                                    coarse_level    = self.levels[l])


    @staticmethod
    def __dict_to_list(dict):
        """
        Straightforward helper function to convert dictionary of list to list of dictionaries

        Args:
            dict: dictionary of lists
        Returns:
            list of dictionaries
        """

        max_val = 1
        for k,v in dict.items():
            if type(v) is list:
                if len(v) > 1 and (max_val > 1 and len(v) is not max_val):
                    #FIXME: get a real error here
                    sys.exit('All lists in cparams need to be of length 1 or %i.. key %s has this list: %s' %(max_val,k,v))
                max_val = max(max_val,len(v))

        ld = [{} for l in range(max_val)]
        for d in range(len(ld)):
            for k,v in dict.items():
                if type(v) is not list:
                    ld[d][k] = v
                else:
                    if len(v) == 1:
                        ld[d][k] = v[0]
                    else:
                        ld[d][k] = v[d]
        return ld


    def register_level(self,L):
        """
        Routine to register levels

        This routine will append levels to the level list of the step instance and link the step to the newly
        registered level (Level 0 will be considered as the finest level). It will also allocate the tau correction,
        if this level is not the finest one.

        Args:
            L: level to be registered
        """

        assert isinstance(L,levclass.level)
        # add level to level list
        self.levels.append(L)
        # if this is not the finest level, allocate tau correction
        if len(self.levels) > 1:
            L._level__add_tau()


    def connect_levels(self, transfer_class, transfer_params, fine_level, coarse_level):
        """
        Routine to couple levels with transfer operators

        Args:
            transfer_class: the class which can transfer between the two levels
            transfer_params: parameters for the transfer class
            fine_level: the fine level
            coarse_level: the coarse level
        """

        # create new instance of the specific transfer class
        T = transfer_class(fine_level,coarse_level,transfer_params)
        # use transfer dictionary twice to set restrict and prolong operator
        self.__transfer_dict[tuple([fine_level,coarse_level])] = T.restrict

        if T.params.finter:
            self.__transfer_dict[tuple([coarse_level,fine_level])] = T.prolong_f
        else:
            self.__transfer_dict[tuple([coarse_level,fine_level])] = T.prolong



    def transfer(self,source,target):
        """
        Wrapper routine to ease the call of the transfer functions

        This function can be called in the multilevel stepper (e.g. MLSDC), passing a source and a target level.
        Using the transfer dictionary, the calling stepper does not need to specify whether to use restrict of prolong.

        Args:
            source: source level
            target: target level
        """
        self.__transfer_dict[tuple([source,target])]()


    def reset_step(self):
        """
        Routine so clean-up step structure and the corresp. levels for further uses
        """
        # reset all levels
        for l in self.levels:
            l.reset_level()


    def init_step(self,u0):
        """
        Initialization routine for a new step.

        This routine uses initial values u0 to set up the u[0] values at the finest level

        Args:
            u0: initial values
        """

        assert len(self.levels) >=1
        assert len(self.levels[0].u) >=1

        # pass u0 to u[0] on the finest level 0
        P = self.levels[0].prob
        self.levels[0].u[0] = P.dtype_u(u0)

    @property
    def prev(self):
        """
        Getter for previous step
        Returns:
            prev
        """
        return self.__prev


    @prev.setter
    def prev(self,p):
        """
        Setter for previous step
        Args:
            p: new previous step
        """
        self.__prev = p

    @property
    def next(self):
        """
        Getter for previous step
        Returns:
            prev
        """
        return self.__prev

    @next.setter
    def next(self, p):
        """
        Setter for previous step
        Args:
            p: new previous step
        """
        self.__next = p

    @property
    def dt(self):
        """
        Getter for current time-step size
        Returns:
            dt
        """
        return self.levels[0].dt

    @property
    def time(self):
        """
        Getter for current time-step size
        Returns:
            dt
        """
        return self.levels[0].time
