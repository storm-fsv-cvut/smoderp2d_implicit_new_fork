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
import math
from smoderp2d.providers.base import BaseProvider, Logger, CompType, BaseWritter, CmdArgumentParser
from smoderp2d.exceptions import ConfigError, ProviderError

class CmdWritter(BaseWritter):
    # TODO: avoid duplication (cmd.CmdWritter)
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

class NoGisProvider(BaseProvider, CmdArgumentParser):
    def __init__(self):
        """Create argument parser."""
        super(NoGisProvider, self).__init__()

        # define CLI parser
        parser = argparse.ArgumentParser(description='Run NoGis Smoderp2D.')

        # data file (only required for runoff)
        parser.add_argument(
            '-cfg', # TODO: config
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

    def _load_input_data(self, filename_indata, filename_soil_types):
        """Load configuration data from roff computation procedure.

        :param str filename_indata: input CSV file
        :param str filename_soil_types: soil types CSV file

        :return: loaded data in numpy structured array
        """
        indata = self._load_csv_data(filename_indata)
        soil_types = self._load_csv_data(filename_soil_types)

        return self._join_indata_soils(indata, soil_types)

    @staticmethod
    def _load_csv_data(filename):
        """Get data from a CSV file in a dict-like form.

        :param filename: Path to the CSV file
        :return: numpy structured array
        """
        return np.genfromtxt(filename, delimiter=';', names=True, dtype=None,
                             encoding='utf-8-sig', deletechars='')

    @staticmethod
    def _join_indata_soils(indata, soil_types):
        """Join data with slopes with corresponding parameters from soil types.

        :param indata: Data with slope attributes from input CSV file
        :param soil_types: Data with soil type attributes from input CSV file
        :return: joint and filtered data in numpy structured array
        """
        from numpy.lib.recfunctions import append_fields

        filtered_soilvegs = None
        soil_types_soilveg = soil_types['soilveg']

        for index in range(len(indata)):
            soilveg = indata['puda'][index] + indata['povrch'][index]
            # a = soil_types[np.in1d(soil_types['soilveg'], ('PXOP', 'HXGEO'))]
            soilveg_line = soil_types[np.where(soil_types_soilveg == soilveg)]

            if filtered_soilvegs is not None:
                filtered_soilvegs = np.concatenate((filtered_soilvegs,
                                                    soilveg_line))
            else:
                filtered_soilvegs = soilveg_line

        soil_types_fields = filtered_soilvegs.dtype.names

        result = append_fields(
            indata,
            filtered_soilvegs.dtype.names,
            [filtered_soilvegs[name] for name in soil_types_fields],
            usemask=False
        )

        return result

    def _load_nogis(self, filename_indata, filename_soil_types):
        """Load configuration data from roff computation procedure.

        :param str filename_indata: input CSV file
        :param str filename_soil_types: soil types CSV file

        :return dict: loaded data
        """
        from smoderp2d.processes import rainfall

        # read input csv files
        try:
            joint_data = self._load_input_data(filename_indata,
                                               filename_soil_types)
        except IOError as e:
            raise ProviderError('{}'.format(e))

        # defaults for nogis provider
        #  type of computing =  1 sheet and rill flow
        data = {}
        data['type_of_computing'] = 1
        data['mfda'] = False

        # time settings
        data['end_time'] = self._config.getfloat('time', 'endtime') * 60.0
        data['maxdt'] = self._config.getfloat('time', 'maxdt')

        # load precipitation input file
        try:
            data['sr'], data['itera'] = rainfall.load_precipitation(
                self._config.get('rainfall', 'file')
            )
        except TypeError:
            raise ProviderError('Invalid file in [rainfall] section')

        # general settings
        # output directory is always set
        data['outdir'] = self._config.get('general', 'outdir')
        data['temp'] = os.path.join(data['outdir'], 'temp')
        # some self._configs are not in pickle.dump
        data['extraOut'] = self._config.getboolean('general', 'extraout')
        # rainfall data can be saved
        data['prtTimes'] = self._config.get('general', 'printtimes')

        resolution = self._config.getfloat('domain', 'res')
        # TODO: Change stah -> svah (ha ha) after being changed in the CSV
        data['r'] = self._compute_rows(joint_data['vodorovny_prumet_stahu[m]'],
                                       joint_data['prevyseni[m]'],
                                       resolution)
        data['c'] = 1
        # set mask i and j must be set after 'r' and 'c'
        data['rr'], data['rc'] = self._construct_rr_rc(data)

        # set cell sizes
        data['vpix'] = data['spix'] = self._config.getfloat('domain', 'res')
        data['pixel_area'] = data['vpix'] * data['spix']

        # divide joint data slope into rows corresponding with data['r']
        parsed_data = self._divide_joint_data(joint_data, data['r'],
                                              data['vpix'])

        # allocate matrices
        self._alloc_matrices(data)

        # set no data value, likely used in nogis provider
        data['NoDataValue'] = -9999

        # topography
        data['mat_slope'] = parsed_data['len'].reshape((data['r'], data['c']))
        # TODO can be probably removed (?) or stay zero
        # data['mat_boundary'] = np.zeros((data['r'],data['c']), float)
        data['mat_efect_cont'] = data['spix'] # x-axis (EW) resolution
        # flow direction is always to the south
        data['mat_fd'].fill(4)

        # set values to parameter matrics
        data['mat_n'] = parsed_data['n'].reshape((data['r'], data['c']))
        data['mat_b'] = parsed_data['b'].reshape((data['r'], data['c']))
        data['mat_a'], data['mat_aa'] = self._get_a(
            data['mat_n'],
            parsed_data['x'].reshape((data['r'], data['c'])),
            parsed_data['y'].reshape((data['r'], data['c'])),
            data['r'],
            data['c'],
            data['NoDataValue'],
            data['mat_slope'])
        data['mat_hcrit'] = self._get_crit_water(
            data['mat_b'],
            parsed_data['tau'].reshape((data['r'], data['c'])),
            parsed_data['v'].reshape((data['r'], data['c'])),
            data['r'],
            data['c'],
            data['mat_slope'],
            data['NoDataValue'],
            data['mat_aa']
        )

        data['mat_reten'] = parsed_data['ret'].reshape((data['r'], data['c']))
        data['mat_pi'] = parsed_data['pi'].reshape((data['r'], data['c']))
        data['mat_ppl'] = parsed_data['ppl'].reshape((data['r'], data['c']))

        data['mat_nan'] = np.nan
        data['mat_inf_index'].fill(1)  # 1 = philips infiltration

        data['mat_nan'], data['mat_inf_index'], data['combinatIndex'], data['mat_slope'], data['mat_dem'] = \
            self._set_combinatIndex(
                data['r'],
                data['c'],
                parsed_data['k'].reshape((data['r'], data['c'])),
                parsed_data['s'].reshape((data['r'], data['c'])),
                data['NoDataValue'],
                data['mat_dem'],
                data['mat_slope'])

        data['array_points'], data['points'] = self._set_hydrographs(data['r'] - 1)
        # and other unused variables
        self._set_unused(data)

        return data

    def _compute_rows(self, lengths, heights, resolution):
        """Compute number of pixels the slope will be divided into.

        :param lengths: np array containing all lengths
        :param heights: np array containing all heights
        :param resolution: intended resolution of one pixel
        :return: number of pixels
        """
        slope_length = self._compute_slope_length(lengths, heights)

        nr_of_rows = round(slope_length / resolution)

        return nr_of_rows

    @staticmethod
    def _compute_slope_length(lengths, heights):
        """Compute the slope length from lengths and heights.

        :param lengths: np array containing horizontal lengths
        :param heights: np array containing heights
        :return: length of slope
        """
        slope_lengths = np.sqrt(np.power(lengths, 2) + np.power(heights, 2))
        slope_length = np.sum(slope_lengths)

        return slope_length

    def _divide_joint_data(self, joint_data, r, res):
        """Divide joint data into corresponding number of rows.

        :param joint_data: np structurred array with the joint data
        :param r: number of rows
        :param res: pixel resolution
        :return: divided, parsed joint data
        """
        from numpy.lib.recfunctions import merge_arrays
        parsed_data = None
        subsegment_unseen = 0

        slope_length = self._compute_slope_length(
            joint_data['vodorovny_prumet_stahu[m]'],
            joint_data['prevyseni[m]']
        )
        mod = slope_length % r
        addition = mod / r
        one_pix_len = res + addition

        for slope_segment in joint_data:
            segment_length = self._compute_slope_length(
                slope_segment['vodorovny_prumet_stahu[m]'],
                slope_segment['prevyseni[m]']
            )

            seg_len_arr = np.array([segment_length / r], dtype=[('len', 'f4')])
            data_entry = merge_arrays((slope_segment, seg_len_arr),
                                      flatten=True)

            subsegment_unseen += segment_length

            while subsegment_unseen >= (one_pix_len / 2):
                if parsed_data is not None:
                    parsed_data = np.concatenate((parsed_data,
                                                  data_entry[True]))
                else:
                    parsed_data = data_entry[True]

                subsegment_unseen -= one_pix_len

        return parsed_data

    def _get_a(self, mat_n, mat_x, mat_y, r, c, no_data_value, mat_slope):
        """
        Build 'a' array.

        :param all_attrib: list of attributes (numpy arrays)
        """
        mat_a = np.zeros(
            [r, c], float
        )
        mat_aa = np.zeros(
            [r, c], float
        )

        nv = no_data_value
        # calculating the "a" parameter
        for i in range(r):
            for j in range(c):
                slope = mat_slope[i][j]
                par_x = mat_x[i][j]
                par_y = mat_y[i][j]

                if par_x == nv or par_y == nv or slope == nv:
                    par_a = nv
                    par_aa = nv
                elif par_x == nv or par_y == nv or slope == 0.0:
                    par_a = 0.0001
                    par_aa = par_a / 100 / mat_n[i][j]
                else:
                    exp = np.power(slope, par_y)
                    par_a = par_x * exp
                    par_aa = par_a / 100 / mat_n[i][j]

                mat_a[i][j] = par_a
                mat_aa[i][j] = par_aa

        return mat_a, mat_aa

    def _get_crit_water(self, mat_b, mat_tau, mat_v, r, c, mat_slope,
                        no_data_value, mat_aa):
        """

        :param all_attrib:
        """
        # critical water level
        mat_hcrit_tau = np.zeros([r, c], float)
        mat_hcrit_v = np.zeros([r, c], float)
        mat_hcrit_flux = np.zeros([r, c], float)
        mat_hcrit = np.zeros([r, c], float)

        for i in range(r):
            for j in range(c):
                if mat_slope[i][j] != no_data_value \
                   and mat_tau[i][j] != no_data_value:
                    slope = mat_slope[i][j]
                    tau_crit = mat_tau[i][j]
                    v_crit = mat_v[i][j]
                    b = mat_b[i][j]
                    aa = mat_aa[i][j]
                    flux_crit = tau_crit * v_crit
                    exp = 1 / (b - 1)

                    if slope == 0.0:
                        hcrit_tau = hcrit_v = hcrit_flux = 1000

                    else:
                        hcrit_v = np.power((v_crit / aa), exp)  # h critical from v
                        hcrit_tau = tau_crit / 98.07 / slope  # h critical from tau
                        hcrit_flux = np.power((flux_crit / slope / 98.07 / aa),(1 / mat_b[i][j]))  # kontrola jednotek

                    mat_hcrit_tau[i][j] = hcrit_tau
                    mat_hcrit_v[i][j] = hcrit_v
                    mat_hcrit_flux[i][j] = hcrit_flux
                    hcrit = min(hcrit_tau, hcrit_v, hcrit_flux)
                    mat_hcrit[i][j] = hcrit
                else:
                    mat_hcrit_tau[i][j] = no_data_value
                    mat_hcrit_v[i][j] = no_data_value
                    mat_hcrit_flux[i][j] = no_data_value
                    mat_hcrit[i][j] = no_data_value

        for out, arr in (("hcrit_tau", mat_hcrit_tau),
                         ("hcrit_flux", mat_hcrit_flux),
                         ("hcrit_v", mat_hcrit_v)):
            self.storage.write_raster(
                arr, out, 'temp'
            )
        self.storage.write_raster(
            mat_hcrit, 'mat_hcrit', ''
        )

        return mat_hcrit

    def _alloc_matrices(self, data):
        # TODO: use loop (check base provider)
        # allocate matrices
        data['mat_b'] = np.zeros((data['r'],data['c']), float)
        data['mat_stream_reach'] = np.zeros((data['r'],data['c']), float)
        data['mat_a'] = np.zeros((data['r'],data['c']), float)
        data['mat_slope'] = np.zeros((data['r'],data['c']), float)
        data['mat_n'] = np.zeros((data['r'],data['c']), float)
        # dem is not needed for computation
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
        """Create list rr and list of lists rc which contain i and j index of
        elements inside the compuation domain.

        :return: rr, rc
        """

        rr = range(data['r'])
        rc = [range(data['c'])]*data['r']

        return rr, rc

    def _set_combinatIndex(self, r, c, mat_k, mat_s, no_data_value,
                           mat_dem, mat_slope):
        mat_nan = np.zeros(
            [r, c], float
        )

        mat_inf_index = None
        combinatIndex = None

        infiltration_type = 0  # "Phillip"
        if infiltration_type == 0:
            # to se rovna vzdycky ne? nechapu tuhle podminku 23.05.2018 MK
            mat_inf_index = np.zeros(
                [r, c], int
            )
            combinat = []
            combinatIndex = []
            for i in range(r):
                for j in range(c):
                    kkk = mat_k[i][j]
                    sss = mat_s[i][j]
                    ccc = [kkk, sss]
                    try:
                        if combinat.index(ccc):
                            mat_inf_index[i][j] = combinat.index(ccc)
                    except:
                        combinat.append(ccc)
                        combinatIndex.append(
                            [combinat.index(ccc), kkk, sss, 0]
                        )
                        mat_inf_index[i][j] = combinat.index(
                            ccc
                        )

        # vyrezani krajnich bunek, kde byly chyby, je to vyrazeno u
        # sklonu a acc
        i = j = 0

        # data value vector intersection
        # TODO: no loop needed
        nv = no_data_value
        for i in range(r):
            for j in range(c):
                x_mat_dem = mat_dem[i][j]
                slp = mat_slope[i][j]
                if x_mat_dem == nv or slp == nv:
                    mat_nan[i][j] = nv
                    mat_slope[i][j] = nv
                    mat_dem[i][j] = nv
                else:
                    mat_nan[i][j] = 0

        self.storage.write_raster(
            mat_nan,
            'mat_nan',
            'temp'
        )

        return mat_nan, mat_inf_index, combinatIndex, mat_slope, mat_dem

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

    def _set_hydrographs(self, max_row):
        """Get array_points and points for the data dictionary.

        These keys are needed to force the run to compute hydrographs.
        Hydrograph is computed only for the last row, therefore preset values.

        :param max_row: index of the last cell
        """
        array_points = np.array([1, max_row, 0, 0, 0]).reshape((1, 5))
        points = 'test'

        return array_points, points

    def load(self):
        """Load configuration data.
        from the config data

        Only roff procedure supported.
        """
        # TODO: don't override load() - use base.load()
        # cleanup output directory first
        self._cleanup()

        # TODO: _load_nogis -> _load_roff(config)
        data = self._load_nogis(
            self._config.get('Other', 'data1d'),
            self._config.get('Other', 'data1d_soil_types'),
        )

        print ('NO GIS PROVIDER')
        print ('')
        for key in data:
            print(key)

        self._set_globals(data)
