import numpy as np
import numpy.ma as ma
from smoderp2d.exceptions import NegativeWaterLevel

# combinatIndex muze byt tady jako globalni
# primenna, main loop bude pro infiltraci volat
# stejnou funkci, ale az taky bude jina globalni
# promenna nastavena. mene ifu v main loop
combinatIndex = []

# set error level of numpy float underflow only to warning instead of errors
np.seterr(under='warn')


def set_combinatIndex(newCombinatIndex):
    global combinatIndex
    combinatIndex = newCombinatIndex


def philip_infiltration(soil, bil):
    # print 'bil v infiltraci', bil
    infiltration = combinatIndex[0][3]
    for z in combinatIndex:
        if ma.all(bil < 0):
            raise NegativeWaterLevel()

        infilt_bil_cond = z[3] > bil

        infiltration = ma.where(
            soil == z[0],
            ma.where(infilt_bil_cond, bil, z[3]),
            infiltration
        )
        bil = ma.where(
            soil == z[0],
            ma.where(infilt_bil_cond, 0, bil - z[3]),
            bil
        )
    # print 'bil a inf v infiltraci\n', bil, infiltration
    return bil, infiltration


def phlilip(k, s, deltaT, totalT, NoDataValue):
    if k and s == NoDataValue:
        infiltration = NoDataValue
    # elif totalT == 0:
        # infiltration = k*deltaT
        # toto je chyba, infiltrace se rovna k az po ustaleni.
        # Na zacatku je teoreticky nekonecno
    # else:
        # try:
    else:
        infiltration = (0.5 * ma.divide(s, ma.sqrt(totalT + deltaT)) + k) * \
                       deltaT
        # except ValueError:
    # print k, s
    return infiltration
