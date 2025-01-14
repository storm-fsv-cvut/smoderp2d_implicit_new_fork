from __future__ import print_function

import os
import sys
import glob
import shutil
import math
import pickle
import logging
import numpy as np
import numpy.ma as ma
from configparser import ConfigParser, NoSectionError, NoOptionError
from abc import abstractmethod

from smoderp2d.core import CompType
from smoderp2d.core.general import GridGlobals, DataGlobals, Globals
from smoderp2d.exceptions import ProviderError, ConfigError, GlobalsNotSet, SmoderpError
from smoderp2d.providers import Logger
from smoderp2d.providers.base.exceptions import DataPreparationError

class Args:
    # type of computation (CompType)
    workflow_mode = None
    # path to pickle data file
    # used by 'dpre' for output and 'roff' for input
    data_file = None
    # config file
    config_file = None

class WorkflowMode:
    # type of computation
    dpre = 0 # data preparation only
    roff = 1 # runoff calculation only
    full = 2 # dpre + roff

    @classmethod
    def __getitem__(cls, key):
        if key == 'dpre':
            return cls.dpre
        elif key == 'roff':
            return cls.roff
        else:
            return cls.full
    
class BaseWriter(object):
    def __init__(self):
        self._data_target = None

    def set_data_layers(self, data):
        """Set data layers dictionary.

        :param data: data dictionary to be set
        """
        self._data_target = data

    @staticmethod
    def _raster_output_path(output, directory='core'):
        """Get output raster path.

        :param output: raster output name
        :param directory: target directory (temp, control)
        """
        dir_name = os.path.join(Globals.outdir, directory) if directory != 'core' else Globals.outdir

        if not os.path.exists(dir_name):
           os.makedirs(dir_name)

        return os.path.join(
            dir_name,
            output + '.asc'
        )

    @staticmethod
    def _print_array_stats(arr, file_output):
        """Print array stats.
        """

        Logger.info("Raster ASCII output file <{}> saved".format(
            file_output
        ))
        if not isinstance(arr, np.ma.MaskedArray):
            na_arr = arr[arr != GridGlobals.NoDataValue]
        else:
            na_arr = arr
        Logger.info("\tArray stats: min={0:.3f} max={1:.3f} mean={2:.3f}".format(
            na_arr.min(), na_arr.max(), na_arr.mean()
        ))

    @abstractmethod
    def write_raster(self, array, output_name, data_type='core'):
        """Write raster (numpy array) to ASCII file.

        :param array: numpy array
        :param output_name: output filename
        :param date_type: directory where to write output file
        """
        file_output = self._raster_output_path(output_name, data_type)

        self._print_array_stats(
            array, file_output
        )

        self._write_raster(array, file_output)


    def create_storage(self, outdir):
        pass

    @abstractmethod
    def _write_raster(self, array, file_output):
        """Write array into file.

        :param array: numpy array to be saved
        :param file_output: path to output file
        """
        pass

    @staticmethod
    def _check_globals():
        """Check globals to prevent call globals before values assigned.

        Raise GlobalsNotSet on failure.
        """
        if GridGlobals.xllcorner is None or \
            GridGlobals.yllcorner is None or \
            GridGlobals.dx is None or \
            GridGlobals.dy is None:
            raise GlobalsNotSet()

class BaseProvider(object):
    def __init__(self):
        self.args = Args()

        self._print_fn = print
        self._print_logo_fn = print

        # default logging level (can be modified by provider)
        Logger.setLevel(logging.INFO)

        # storage writter must be defined
        self.storage = None
        self._hidden_config = self.__load_hidden_config()

    @property
    def workflow_mode(self):
        return self.args.workflow_mode

    @staticmethod
    def add_logging_handler(handler, formatter=None):
        """Register new logging handler.

        :param handler: logging handler to be registered
        :param formatter: logging handler formatting
        """
        if not formatter:
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(module)s:%(lineno)s]"
            )
        handler.setFormatter(formatter)
        if sys.version_info.major >= 3:
            if len(Logger.handlers) == 0:
                # avoid duplicated handlers (eg. in case of ArcGIS)
                Logger.addHandler(handler)

    def __load_hidden_config(self):
        # load hidden configuration with advanced settings
        _path = os.path.join(os.path.dirname(__file__), '..', '..', '.config.ini')
        if not os.path.exists(_path):
            raise ConfigError("{} does not exist".format(
                _path
            ))

        config = ConfigParser()
        config.read(_path)

        if not config.has_option('outputs', 'extraout'):
            raise ConfigError('Section "outputs" or option "extraout" is not set properly in file {}'.format( _path))

        return config


    def _load_config(self):
        # load configuration
        if not os.path.exists(self.args.config_file):
            raise ConfigError("{} does not exist".format(
                self.args.config_file
            ))

        config = ConfigParser()
        config.read(self.args.config_file)

        try:
            # set logging level
            Logger.setLevel(config.get('logging', 'level', fallback=logging.INFO))
            # sys.stderr logging
            self.add_logging_handler(
                logging.StreamHandler(stream=sys.stderr)
            )

            # must be defined for _cleanup() method
            Globals.outdir = config.get('output', 'outdir')
        except (NoSectionError, NoOptionError) as e:
            raise ConfigError('Config file {}: {}'.format(
                self.args.config_file, e
            ))

        return config


    def _load_dpre(self):
        """Run data preparation procedure.

        See ArcGisProvider and GrassGisProvider for implementation issues.

        :return dict: loaded data
        """
        raise NotImplementedError()

    def _load_roff(self):
        """Load configuration data from roff computation procedure.

        :return dict: loaded data
        """
        from smoderp2d.processes import rainfall

        # the data are loaded from a pickle file
        try:
            data = self._load_data(self.args.data_file)
            if isinstance(data, list):
                raise ProviderError(
                    'Saved data out-dated. Please use '
                    'utils/convert-saved-data.py for update.'
                )
        except IOError as e:
            raise ProviderError('{}'.format(e))

        # some variables configs can be changes after loading from
        # pickle.dump such as end time of simulation

        if self._config.get('time', 'endtime'):
            data['end_time'] = self._config.getfloat('time', 'endtime')
        #  time of flow algorithm
        data['mfda'] = self._config.getboolean('processes', 'mfda', fallback=False)

        #  type of computing
        data['type_of_computing'] = CompType()[self._config.get('processes', 'typecomp', fallback='stream_rill')]
        
        #  rainfall data can be saved
        if self._config.get('data', 'rainfall'):
            try:
                data['sr'], data['itera'] = rainfall.load_precipitation(
                    self._config.get('data', 'rainfall')
                )
            except TypeError:
                raise ProviderError('Invalid rainfall file')

        # some self._configs are not in pickle.dump
        data['extraOut'] = self._config.getboolean('output', 'extraout', fallback=False)
        # rainfall data can be saved
        data['prtTimes'] = self._config.get('output', 'printtimes', fallback=None)

        data['maxdt'] = self._config.getfloat('time', 'maxdt')

        # ensure that dx and dy are defined
        data['dx'] = data['dy'] = math.sqrt(data['pixel_area'])

        return data

    def load(self):
        """Load configuration data."""
        # cleanup output directory first
        self._cleanup()

        data = None
        if self.args.workflow_mode in (WorkflowMode.dpre, WorkflowMode.full):
            try:
                data = self._load_dpre()
            except DataPreparationError as e:
                raise ProviderError('{}'.format(e))
            if self.args.workflow_mode == WorkflowMode.dpre:
                # data preparation requested only
                # add also related information from GridGlobals
                for k in ('NoDataValue', 'bc', 'br', 'c', 'dx', 'dy',
                          'pixel_area', 'r', 'rc', 'rr', 'xllcorner', 'yllcorner'):
                    data[k] = getattr(GridGlobals, k)
                self._save_data(data, self.args.data_file)
                return

        if self.args.workflow_mode == WorkflowMode.roff:
            data = self._load_roff()

        # roff || full
        self._set_globals(data)

    def _set_globals(self, data):
        """Set global variables.

        :param dict data: data to be set
        """
        for item in data.keys():
            if hasattr(Globals, item):
                if getattr(Globals, item) is None:
                    setattr(Globals, item, data[item])
            elif hasattr(GridGlobals, item):
                setattr(GridGlobals, item, data[item])
            elif hasattr(DataGlobals, item):
                setattr(DataGlobals, item, data[item])

        Globals.mat_reten = -1.0 * data['mat_reten'] / 1000 # converts mm to m
        comp_type = self._comp_type(data['type_of_computing'])
        Globals.diffuse = False # not implemented yet
        Globals.subflow = comp_type['subflow_rill']
        Globals.isRill = comp_type['rill']
        Globals.isStream = comp_type['stream_rill']
        Globals.prtTimes = data.get('prtTimes', None)
        Globals.extraOut = self._hidden_config.getboolean('outputs','extraout')
        Globals.end_time *= 60 # convert min to sec

        # If profile1d provider is used the values
        # should be set in the loop at the beginning
        # of this method since it is part of the
        # data dict (only in profile1d provider).
        # Otherwise is has to be set to 1.
        if Globals.slope_width is None:
            Globals.slope_width = 1

        # set masks of the area of interest
        GridGlobals.masks = [[True] * GridGlobals.c for _ in range(GridGlobals.r)]
        rr, rc = GridGlobals.get_region_dim()
        for r in rr:
            for c in rc[r]:
                GridGlobals.masks[r][c] = False

    @staticmethod
    def _cleanup():
        """Clean-up output directory.

        :param output_dir: output directory to clean up
        """
        output_dir = Globals.outdir
        if not output_dir:
            # no output directory defined
            return
        if os.path.exists(output_dir):
            for filename in os.listdir(output_dir):
                file_path = os.path.join(output_dir, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
        else:
            os.makedirs(output_dir)

    @staticmethod
    def _comp_type(itc):
        """Returns boolean information about the components of the computation.

        Return true/values for rill, subflow, stream,
        presence/non-presence.

        :param CompType tc: type of computation
        
        :return dict:

        """
        ret = {}
        for item in ('sheet_only',
                     'rill',
                     'sheet_stream',
                     'stream_rill',
                     'subflow_rill',
                     'stream_subflow_rill'):
            ret[item] = False

        if itc == CompType.sheet_only:
            ret['sheet_only'] = True
        elif itc == CompType.rill:
            ret['rill'] = True
        elif itc == CompType.stream_rill:
            ret['stream'] = True
            ret['rill'] = True
        elif itc == CompType.subflow_rill:
            ret['subflow'] = True
            ret['rill'] = True
        elif itc == CompType.stream_subflow_rill:
            ret['stream'] = True
            ret['subflow'] = True
            ret['rill'] = True

        return ret
            
    def logo(self):
        """Print Smoderp2d ascii-style logo."""
        logo_file = os.path.join(os.path.dirname(__file__), 'txtlogo.txt')
        with open(logo_file, 'r') as fd:
            self._print_logo_fn(fd.read())
        self._print_logo_fn('') # extra line

    @staticmethod
    def _save_data(data, filename):
        """Save data into pickle.
        """
        if filename is None:
            raise ProviderError('Output file for saving data not defined')
        dirname = os.path.dirname(filename)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        with open(filename, 'wb') as fd:
            pickle.dump(data, fd, protocol=2)
        Logger.info('Data preparation results stored in <{}> ({} bytes)'.format(
            filename, sys.getsizeof(data)
        ))

    @staticmethod
    def _load_data(filename):
        """Load data from pickle.

        :param str filename: file to be loaded
        """
        if filename is None:
            raise ProviderError('Input file for loading data not defined')
        with open(filename, 'rb') as fd:
            if sys.version_info > (3, 0):
                data = {
                    key.decode() if isinstance(key, bytes) else key:
                    val.decode() if isinstance(val, bytes) else val
                    for key, val in pickle.load(fd, encoding='bytes').items()
                }
            else:
                data = pickle.load(fd)
        Logger.debug('Size of loaded data is {} bytes'.format(
            sys.getsizeof(data))
        )

        return data

    def postprocessing(self, cumulative, surface_array, stream):

        rrows = GridGlobals.rr
        rcols = GridGlobals.rc
        dx = GridGlobals.get_size()[0]

        # compute maximum shear stress and velocity
        cumulative.calculate_vsheet_sheerstress()

        # define output data to be produced
        data_output = [
            'infiltration',
            'precipitation',
            'v_sheet',
            'shear_sheet',
            'q_sur_tot',
            'vol_sur_tot'
        ]

        # extra outputs from cumulative class are printed by
        # default to temp dir
        # if Globals.extraOut:
        data_output_extras = [
                'h_sur_tot',
                'q_sheet_tot',
                'vol_sheet',
                'h_rill',
                'q_rill_tot',
                'vol_rill',
                'b_rill',
                'inflow_sur',
                'sur_ret',
        ]

        if Globals.subflow:
            # Not implemented yet
            pass
            # data_output += [
            # ]

        # make rasters from cumulative class
        for item in data_output:
            self.storage.write_raster(
                self._make_mask(getattr(cumulative, item)),
                cumulative.data[item].file_name,
                cumulative.data[item].data_type
            )

        # make extra rasters from cumulative clasess into temp dir
        for item in data_output_extras:
            self.storage.write_raster(
                self._make_mask(getattr(cumulative, item)),
                cumulative.data[item].file_name,
                cumulative.data[item].data_type
            )

        finState = np.zeros(np.shape(surface_array.state), np.float32)
        # TODO: Maybe should be filled with NoDataInt
        finState.fill(GridGlobals.NoDataValue)
        vRest = np.zeros(np.shape(surface_array.state), np.float32)
        vRest.fill(GridGlobals.NoDataValue)
        totalBil = cumulative.infiltration.copy()
        totalBil.fill(0.0)

        for i in rrows:
            for j in rcols[i]:
                finState[i][j] = int(surface_array.state.data[i, j])
                if finState[i][j] >= Globals.streams_flow_inc:
                    vRest[i][j] = GridGlobals.NoDataValue
                else:
                    vRest[i][j] = surface_array.h_total_new.data[i, j] * \
                                  GridGlobals.pixel_area

        totalBil = (cumulative.precipitation + cumulative.inflow_sur) - \
            (cumulative.infiltration + cumulative.vol_sur_tot) - \
            cumulative.sur_ret - vRest

        for i in rrows:
            for j in rcols[i]:
                if  int(surface_array.state.data[i, j]) >= \
                        Globals.streams_flow_inc :
                    totalBil[i][j] = GridGlobals.NoDataValue

        self.storage.write_raster(self._make_mask(totalBil), 'massbalance', 'control')
        self.storage.write_raster(self._make_mask(vRest), 'volrest_m3', 'control')
        self.storage.write_raster(self._make_mask(finState), 'surfacestate', 'control')

        # store stream reaches results to a table
        # if stream is calculated
        if stream:
            n = len(stream)
            m = 7
            outputtable = np.zeros([n,m])
            fid = list(stream.keys())
            for i in range(n):
                outputtable[i][0] = stream[fid[i]].segment_id
                outputtable[i][1] = stream[fid[i]].b
                outputtable[i][2] = stream[fid[i]].m
                outputtable[i][3] = stream[fid[i]].roughness
                outputtable[i][4] = stream[fid[i]].q365
                # TODO: The following should probably be made scalars already
                #       before in the code
                #       The following conditions are here meanwhile to be sure
                #       nothing went wrong
                if len(ma.unique(stream[fid[i]].V_out_cum)) > 2:
                    raise SmoderpError(
                        'Too many values in V_out_cum - More than one for one '
                        'stream'
                    )
                if len(ma.unique(stream[fid[i]].Q_max)) > 2:
                    raise SmoderpError(
                        'Too many values in Q_max - More than one for one '
                        'stream'
                    )
                outputtable[i][5] = ma.unique(stream[fid[i]].V_out_cum)[0]
                outputtable[i][6] = ma.unique(stream[fid[i]].Q_max)[0]

            temp_dir = os.path.join(Globals.outdir, 'temp')
            if not os.path.isdir(temp_dir):
                os.makedirs(temp_dir)
            path_ = os.path.join(temp_dir, 'stream.csv')
            np.savetxt(path_, outputtable, delimiter=';',fmt = '%.3e',
                       header='FID{sep}b_m{sep}m__{sep}rough_s_m1_3{sep}q365_m3_s{sep}V_out_cum_m3{sep}Q_max_m3_s'.format(sep=';'))

    def _make_mask(self, arr):
        """ Assure that the no data value is outside the
        computation region.
        Works only for type float.

        :param arrr: numpy array
        """

        rrows = GridGlobals.rr
        rcols = GridGlobals.rc

        copy_arr = arr.copy()
        arr.fill(GridGlobals.NoDataValue)

        for i in rrows:
            for j in rcols[i]:
                arr[i, j] = copy_arr[i, j]

        return arr


        # TODO
        # if not Globals.extraOut:
        #     if os.path.exists(output + os.sep + 'temp'):
        #         shutil.rmtree(output + os.sep + 'temp')
        #     if os.path.exists(output + os.sep + 'temp_dp'):
        #         shutil.rmtree(output + os.sep + 'temp_dp')
        #     return 1
