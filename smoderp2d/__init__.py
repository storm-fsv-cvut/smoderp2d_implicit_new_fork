"""
Documentation of Smoderp, distributed event-based model for surface and
subsurface runoff and erosion.

.. moduleauthor:: Petr Kavka, Karel Vrana and Jakub Jerabek
                  model was build in cooperation with eng. students
                  (Jan Zajicek, Nikola Nemcova, Tomas Edlman, Martin Neumann)

The computational options are as follows:
 - Type of flow
  - surface
   - subsurface
   - surface + subsurface
 - Flow direction algorithm
   - D8 (default)
   - multi-flow direction
 - Erosion
   - none
   - sheet erosion
   - sheet erosion + rill erosion
 - Stream
   - yes
   - no
"""

import os

from smoderp2d.core.general import Globals
from smoderp2d.providers import Logger
from smoderp2d.providers.base import WorkflowMode
from smoderp2d.providers.base.exceptions import DataPreparationInvalidInput
from smoderp2d.exceptions import SmoderpError, ProviderError

__version__ = "2.0.dev"


class Runner(object):
    def __init__(self):
        self._provider = self._provider_factory()

    def _provider_factory(self):
        # initialize provider
        if isinstance(self, ArcGisRunner):
            from smoderp2d.providers.arcgis import ArcGisProvider
            provider_class = ArcGisProvider()
        elif isinstance(self, QGISRunner):
            from smoderp2d.providers.grass import GrassGisProvider
            from smoderp2d.providers.grass.logger import QGisLogHandler
            QGisLogHandler.progress_reporter = self.progress_reporter
            provider_class = GrassGisProvider(QGisLogHandler)
        elif isinstance(self, GrassGisRunner):
            from smoderp2d.providers.grass import GrassGisProvider
            provider_class = GrassGisProvider()
        elif os.getenv('SMODERP2D_PROFILE1D'):
            from smoderp2d.providers.profile1d import Profile1DProvider
            provider_class = Profile1DProvider()
        else:
            from smoderp2d.providers.cmd import CmdProvider
            provider_class = CmdProvider()

        return provider_class

    @property
    def workflow_mode(self):
        return self._provider.args.workflow_mode

    @workflow_mode.setter
    def workflow_mode(self, workflow_mode):
        """Set computation type.

        :param WorkflowMode workflow_mode: workflow mode
        """
        self._provider.args.workflow_mode = workflow_mode
        if workflow_mode in (WorkflowMode.dpre, WorkflowMode.roff):
            self._provider.args.data_file = os.path.join(
                Globals.outdir, "dpre.save"
            )

    def run(self):
        # print logo
        self._provider.logo()

        # check workflow_mode consistency
        modes = (WorkflowMode.dpre, WorkflowMode.roff, WorkflowMode.full)
        if self._provider.workflow_mode not in modes:
            raise ProviderError('Unsupported partial computing: {}'.format(
                self._provider.workflow_mode
            ))

        # set percentage counter
        if self._provider.workflow_mode == WorkflowMode.dpre:
            Logger.set_progress(100)
        elif self._provider.workflow_mode == WorkflowMode.full:
            Logger.set_progress(40)
        else:
            Logger.set_progress(10)

        # load configuration (set global variables)
        try:
            self._provider.load()
        except DataPreparationInvalidInput:
            return 1

        if self._provider.args.workflow_mode == WorkflowMode.dpre:
            # data prepararation only requested
            return

        # must be called after initialization (!)
        from smoderp2d.runoff import Runoff

        # set percentage counter for counter
        Logger.set_progress(95)

        # run computation
        runoff = Runoff(self._provider)
        runoff.run()

        # save result data
        Logger.set_progress(100)
        runoff.save_output()

        # resets
        Logger.reset()

        return 0

    def set_options(self, options):
        self._provider.set_options(options)


class ArcGisRunner(Runner):

    def __init__(self):
        os.environ['ESRIACTIVEINSTALLATION'] = '1'
        super(ArcGisRunner, self).__init__()


class GrassGisRunner(Runner):
    pass


class QGISRunner(GrassGisRunner):
    def __init__(self, progress_reporter, grass_bin_path='grass'):
        self.progress_reporter = progress_reporter

        # create temp GRASS location
        import subprocess
        import tempfile
        import binascii
        from grass.script import setup as gsetup
        from grass.pygrass.gis import Mapset
        from qgis.core import QgsProject

        epsg = QgsProject.instance().crs().authid()

        # path to temp location
        gisdb = os.path.join(tempfile.gettempdir(), 'grassdata')
        if not os.path.isdir(gisdb):
            os.mkdir(gisdb)

        # location: use random names for batch jobs
        string_length = 16
        location = binascii.hexlify(os.urandom(string_length)).decode("utf-8")

        subprocess.call(
            [grass_bin_path, '-e', f'-c {epsg}', os.path.join(gisdb, location)]
        )

        # # create location
        # try:
        #     gs.create_location(gisdb, location, epsg='5514', overwrite=True)
        # except SmoderpError as e:
        #     raise SmoderpError('{}'.format(e))

        # initialize GRASS session
        gsetup.init(gisdb, location, 'PERMANENT', os.environ['GISBASE'])
        # calling gsetup.init() is not enough for PyGRASS
        Mapset('PERMANENT', location, gisdb).current()

        # test GRASS env varible
        if not os.getenv('GISRC'):
            raise SmoderpError('GRASS not found.')

        super().__init__()

    @staticmethod
    def import_data(options):
        """
        Import files to grass

        :param options: dictionary of input data
        """
        from grass.pygrass.modules import Module

        for key in options:
            try:
                # import rasters
                if key == "elevation":
                    from osgeo import gdal, osr
                    from qgis.core import QgsProject

                    ds = gdal.Open('tests/data/dem10m.tif')
                    proj = osr.SpatialReference(wkt=ds.GetProjection())
                    srs = proj.GetAttrValue('AUTHORITY',1)

                    project_projection = QgsProject.instance().crs().authid()

                    if srs == project_projection.split(':')[1]:
                        Module(
                            "r.import", input=options[key], output=key,
                            flags='o'
                        )
                    else:
                        Module("r.import", input=options[key], output=key)
                # import vectors
                elif key in ["soil", "vegetation", "points", "streams"]:
                    Module(
                        "v.import", input=options[key], output=key
                    )
                # import tables
                elif key in ["table_soil_vegetation",
                             "channel_properties_table"]:
                    Module("db.in.ogr", input=options[key], output=key)
            except SmoderpError as e:
                raise SmoderpError('{}'.format(e))

    @staticmethod
    def show_results():
        import glob
        from PyQt5.QtGui import QColor
        from qgis.core import (
            QgsProject, QgsRasterLayer, QgsRasterShader,
            QgsSingleBandPseudoColorRenderer, QgsColorRampShader
        )

        # get colour definitions
        color_ramp = QgsColorRampShader()
        color_ramp.setColorRampType(QgsColorRampShader.Interpolated)
        lst2 = [
            QgsColorRampShader.ColorRampItem(0, QColor('#0000ff'), '0'),
            QgsColorRampShader.ColorRampItem(20, QColor('#efefff'), '20')
        ]
        color_ramp.setColorRampItemList(lst2)
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(color_ramp)

        for map_path in glob.glob(os.path.join(Globals.outdir, '*.asc')):
            layer = QgsRasterLayer(
                map_path, os.path.basename(os.path.splitext(map_path)[0])
            )

            # set colours
            renderer = QgsSingleBandPseudoColorRenderer(
                layer.dataProvider(), 1, shader
            )
            layer.setRenderer(renderer)

            QgsProject.instance().addMapLayer(layer)

    def export_data(self):
        pass

    def __del__(self):
        pass


class WpsRunner(Runner):
    def __init__(self, **args):
        provider_class = self._provider_factory()
        self._provider = provider_class(**args)
