import os
import sys
import argparse
import logging
import numpy as np
if sys.version_info.major >= 3:
    from configparser import ConfigParser, NoSectionError
else:
    from ConfigParser import ConfigParser, NoSectionError

from smoderp2d.core.general import Globals
from smoderp2d.providers.base import BaseProvider, Logger, CompType, BaseWritter
from smoderp2d.exceptions import ConfigError

class CmdWritter(BaseWritter):
    def __init__(self):
        super(CmdWritter, self).__init__()

    def write_raster(self, array, output_name, directory='core'):
        """Write raster (numpy array) to ASCII file.

        :param array: numpy array
        :param output_name: output filename
        :param directory: directory where to write output file
        """
        file_output = self._raster_output_path(output_name, directory)

        np.savetxt(file_output, array, fmt='%.6e')

        self._print_array_stats(
            array, file_output
        )

class NoGisProvider(BaseProvider):
    def __init__(self):
        """Create argument parser."""
        super(NoGisProvider, self).__init__()
        
        # define CLI parser
        parser = argparse.ArgumentParser(description='Run NoGis Smoderp2D.')

        # data file (only required for runoff)
        parser.add_argument(
            '-cfg',
            help='file with configuration',
            type=str
        )

        self.args = parser.parse_args()

        # no gis has only roff comp type
        self.args.typecomp = 'roff'
        self.args.typecomp = CompType()[self.args.typecomp]

        # load configuration
        self._config = ConfigParser()
        if self.args.typecomp == CompType.roff:
            if not self.args.cfg:
                parser.error('-cfg required')
            if not os.path.exists(self.args.cfg):
                raise ConfigError("{} does not exist".format(
                    self.args.cfg
                ))
            self._config.read(self.args.cfg)

        try:
            # set logging level
            Logger.setLevel(self._config.get('general', 'logging'))
            # sys.stderr logging
            self._add_logging_handler(
                logging.StreamHandler(stream=sys.stderr)
            )

            # must be defined for _cleanup() method
            Globals.outdir = self._config.get('general', 'outdir')
        except NoSectionError as e:
            raise ConfigError('Config file {}: {}'.format(
                self.args.cfg, e
            ))

        # define storage writter
        self.storage = CmdWritter()

    def _load_nogis(self, indata):
        """Load configuration data from roff computation procedure.

        :param str indata: configuration filename

        :return dict: loaded data
        """
        from smoderp2d.processes import rainfall

        # TOTO NAKONEC V NOGIS SMAZEM
        try:
            data = self._load_data(indata)
            if isinstance(data, list):
                raise ProviderError(
                    'Saved data out-dated. Please use '
                    'utils/convert-saved-data.py for update.'
                )
        except IOError as e:
            raise ProviderError('{}'.format(e))

        # DEFAULTS for NOGIS provider
        #  type of computing =  1 sheet and rill flow
        data['type_of_computing'] = 1
        data['mfda'] = False

        # TIME setting
        data['end_time'] = self._config.getfloat('time', 'endtime') * 60.0
        data['maxdt'] = self._config.getfloat('time', 'maxdt')

        #  rainfall data can be saved
        try:
            data['sr'], data['itera'] = rainfall.load_precipitation(
                self._config.get('rainfall', 'file')
            )
        except TypeError:
            raise ProviderError('Invalid file in [rainfall] section')

        # general settings
        # output directory is always set
        data['outdir'] = self._config.get('general', 'outdir')
        data['temp'] = '{}{}{}'.format(data['outdir'],os.sep,'temp')
        # some self._configs are not in pickle.dump
        data['extraOut'] = self._config.getboolean('general', 'extraout')
        # rainfall data can be saved
        data['prtTimes'] = self._config.get('general', 'printtimes')

        
        data['r'] = self._config.getint('domain', 'nr')
        data['c'] = self._config.getint('domain', 'nc')
        # set mask i and j must be set after 'r' and 'c'
        data['rr'], data['rc'] = self._construct_rr_rc(data)

        # other geometrical properties
        data['yllcorner'] = 0.
        data['xllcorner'] = 0.

        # set cell sizes
        print (data['vpix'])
        print (data['pixel_area'])
        print (data['spix'])


        # allocate matrices
        self._alloc_matrices(data)

        # topography
        data['mat_slope'].fill(self._config.getfloat('topography', 'slope')) 
        # TODO can mat boundary stay zero?
        # data['mat_boundary'] = np.zeros((data['r'],data['c']), float)
        # TODO can mat dem needs to be recunstructed from input data
        # data['mat_dem'] = np.zeros((data['r'],data['c']), float)
        data['mat_efect_cont'] = 'stejne jako dx'
        # flow direction is always to the south
        data['mat_fd'].fill(4)

        # set values to parameter matrics
        data['mat_b'].fill(self._config.getfloat('parameters', 'b'))
        data['mat_a'].fill(self._config.getfloat('parameters', 'X'))
        data['mat_n'].fill(self._config.getfloat('parameters', 'n'))
        data['mat_hcrit'].fill(self._config.getfloat('parameters', 'hcrit'))
        data['mat_aa'] = data['mat_a']*data['mat_slope']**(
            self._config.getfloat('parameters','Y')
            )
        # retention is converted from mm to m in _set_globals function
        data['mat_reten'].fill(self._config.getfloat('parameters', 'ret'))
        data['mat_pi'].fill(self._config.getfloat('parameters', 'pi'))
        data['mat_ppl'].fill(self._config.getfloat('parameters', 'ppl'))

        data['mat_nan'] = np.nan
        data['mat_inf_index'].fill(1) # 1 = philips infiltration 

        # TODO set infiltration values
        # needs to be constructed from input data
        self._set_combinatIndex(data)

        # TODO set points to hydrographs
        self._set_hydrographs(data)

        # set no data value, likely used in nogis provider
        data['NoDataValue'] = -9999
        # and other unused variables
        self._set_unused(data)

        return data

    def _alloc_matrices(self, data):
        # allocate matrices
        data['mat_b'] = np.zeros((data['r'],data['c']), float)
        data['mat_stream_reach'] = np.zeros((data['r'],data['c']), float)
        data['mat_a'] = np.zeros((data['r'],data['c']), float)
        data['mat_slope'] = np.zeros((data['r'],data['c']), float)
        data['mat_n'] = np.zeros((data['r'],data['c']), float)
        data['mat_dem'] = np.zeros((data['r'],data['c']), float)
        data['mat_inf_index'] = np.zeros((data['r'],data['c']), float)
        data['mat_fd'] = np.zeros((data['r'],data['c']), float)
        data['mat_hcrit'] = np.zeros((data['r'],data['c']), float)
        data['mat_aa'] = np.zeros((data['r'],data['c']), float)
        data['mat_reten'] = np.zeros((data['r'],data['c']), float)
        data['mat_nan'] = np.zeros((data['r'],data['c']), float)
        data['mat_efect_cont'] = np.zeros((data['r'],data['c']), float)
        data['mat_pi'] = np.zeros((data['r'],data['c']), float)
        data['mat_boundary'] = np.zeros((data['r'],data['c']), float)
        data['mat_ppl'] = np.zeros((data['r'],data['c']), float)

    def _construct_rr_rc(self, data):
        """ creates list rr and list of lists rc
        which contain i and j index of elements inside
        the compuation domain"""

        rr = range(data['r'])
        rc = [range(data['c'])]*data['r']

        return rr, rc


    def _set_combinatIndex(self, data):
        pass

    def _set_unused(self, data):
        data['cell_stream'] = None
        data['state_cell'] = None
        data['outletCells'] = None
        data['STREAM_RATIO'] = None
        data['bc'] = None
        data['br'] = None
        data['streams_loc'] = None
        data['streams'] = None
        data['poradi'] = None
        # path to the input git layer which does 
        # not exists in no gis provider
        data['points'] = None

    def _set_hydrographs(self,data):
        pass

    def load(self):
        """Load configuration data.
        from the config data

        Only roff procedure supported.
        """

        # cleanup output directory first
        self._cleanup()

        data = self._load_nogis(
            self._config.get('Other', 'indata')
        )

        #TODO
        print ('')
        print ('')
        print ('NO GIS PROVIDER')
        print ('')
        for key in data:
            print(key)
        print ('')
        print ('in progress stop in {}'.format(os.path.join(os.path.dirname(__file__))))
        print ('next step: make poirts to print hydrograms, set combinatIndex  and set cell sizes')

        self._set_globals(data)
        sys.exit()
