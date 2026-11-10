"""Grid helpers for Open-Meteo .om files (regular lat/lon and ECMWF O1280)."""
import numpy as np, re

class RegularGrid:
    def __init__(self, ny, nx, lat0, lon0, lat1, lon1):
        self.ny, self.nx = ny, nx
        self.lat0, self.lon0 = lat0, lon0
        self.dlat = (lat1 - lat0) / (ny - 1)
        self.dlon = (lon1 - lon0) / (nx - 1)
    def candidates(self, lat, lon):
        """2x2 enclosing grid cells -> list of (index_tuple, lat, lon)."""
        fy = (lat - self.lat0) / self.dlat
        fx = (lon - self.lon0) / self.dlon
        y0, x0 = int(np.floor(fy)), int(np.floor(fx))
        out = []
        for y in (y0, y0 + 1):
            for x in (x0, x0 + 1):
                if 0 <= y < self.ny and 0 <= x < self.nx:
                    out.append(((y, x), self.lat0 + y * self.dlat, self.lon0 + x * self.dlon))
        return out

class O1280Grid:
    """ECMWF octahedral reduced Gaussian grid, rows north->south, lon from 0 eastward."""
    N = 1280
    def __init__(self):
        n = self.N
        rows = np.arange(1, 2 * n + 1)
        nlon = np.where(rows <= n, 4 * rows + 16, 4 * (2 * n + 1 - rows) + 16)
        self.nlon = nlon
        self.offset = np.concatenate([[0], np.cumsum(nlon)[:-1]])
        self.lats = 90 - (rows - 0.5) * (180.0 / (2 * n))
    def candidates(self, lat, lon):
        # nearest two rows
        fr = (90 - lat) / (180.0 / (2 * self.N)) - 0.5
        r0 = int(np.floor(fr))
        out = []
        for r in (r0, r0 + 1):
            nl = self.nlon[r]
            dl = 360.0 / nl
            l = lon % 360
            x0 = int(np.floor(l / dl))
            for x in (x0, (x0 + 1) % nl):
                out.append(((0, int(self.offset[r] + x)), self.lats[r], x * dl))
        return out

def grid_from_reader(reader):
    wkt = reader.get_child_by_name("crs_wkt").read_scalar()
    shape = reader.shape
    if "O1280" in wkt or shape[0] == 1:
        return O1280Grid()
    b = [float(v) for v in re.search(r"BBOX\[([^\]]*)\]", wkt).group(1).split(",")]
    return RegularGrid(shape[0], shape[1], b[0], b[1], b[2], b[3])
