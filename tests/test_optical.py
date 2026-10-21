import numpy as np
from monitor import optical


def test_cloud_fraction_counts_only_valid_pixels():
    flags = np.array([[1, 0], [1, 1]])
    valid = np.array([[1, 1], [1, 0]])
    assert optical.cloud_fraction(flags, valid) == 2 / 3      # the invalid cloud pixel is ignored
    assert optical.cloud_fraction(flags, np.zeros((2, 2))) is None


def test_parse_s2_name():
    info = optical.parse_s2_name("S2B_MSIL2A_20261021T165329_N0511_R069_T14SPG_20261021T200012.SAFE")
    assert info["scene_id"] == "S2B_20261021T165329_T14SPG" and info["tile"] == "T14SPG"
    assert optical.parse_s2_name("S2B_MSIL1C_20261021T165329_N0511_R069_T14SPG_x.SAFE") is None
