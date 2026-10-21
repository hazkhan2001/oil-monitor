"""
landmask.py: which pixels are sea?

Coastlines, port cranes and buildings are radar-bright and would swamp the
ship detector, so we only look at open water. The global-land-mask package
ships a 1 km world land/sea grid inside the library, so it works offline.

1 km is coarse, so we also add a buffer: a detection must be at least
land_buffer_m from the coast. The cost is that ships moored right at a quay
are invisible; for strait transits and anchorages that's fine.

LESSON: np.broadcast_arrays turns a column of latitudes and a row of
longitudes into two full 2-D grids, so a function that works on points
(is_land) runs on every pixel at once.

LESSON: to grow land by N metres we use a distance transform: for every sea
pixel it computes the distance to the nearest land pixel in one pass. Then
"sea = distance > buffer". That's far faster than repeatedly dilating.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def sea_mask(raster, land_buffer_m: int, pixel_m: float) -> np.ndarray:
    from global_land_mask import globe

    h, w = raster.db.shape
    lon, lat = raster.pixel_to_lonlat(np.arange(h)[:, None], np.arange(w)[None, :])
    lon_grid, lat_grid = np.broadcast_arrays(lon, lat)
    land = globe.is_land(lat_grid, lon_grid)
    if not land.any():
        return np.ones_like(land, dtype=bool)
    dist_to_land_m = ndimage.distance_transform_edt(~land) * pixel_m
    return dist_to_land_m > land_buffer_m
