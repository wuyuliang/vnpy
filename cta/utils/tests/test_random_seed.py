"""Tests for global random seed helper."""
from __future__ import annotations

import random
import unittest

import numpy as np

from cta.utils.random_seed import seed_all


class TestRandomSeed(unittest.TestCase):
    def test_seed_all_makes_numpy_and_random_deterministic(self) -> None:
        seed_all(20260512)
        a1 = np.random.rand(5).tolist()
        r1 = [random.random() for _ in range(5)]

        seed_all(20260512)
        a2 = np.random.rand(5).tolist()
        r2 = [random.random() for _ in range(5)]

        self.assertListEqual(a1, a2)
        self.assertListEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()

