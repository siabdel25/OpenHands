"""Test cases for route_llm.py."""
import unittest

from scripts.route_llm import (
    COMPLEX_KEYWORDS,
    COMPLEX_PROFILE,
    CONFIDENTIAL_KEYWORDS,
    CONFIDENTIAL_PROFILE,
    DEFAULT_PROFILE,
    pick_profile,
)


class TestPickProfile(unittest.TestCase):
    """Test the pick_profile function."""

    def test_confidential_keywords(self):
        """Test that confidential keywords return the confidential profile."""
        for keyword in CONFIDENTIAL_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertEqual(pick_profile(f"This is a {keyword} task"), CONFIDENTIAL_PROFILE)

    def test_complex_keywords(self):
        """Test that complex keywords return the complex profile."""
        for keyword in COMPLEX_KEYWORDS:
            with self.subTest(keyword=keyword):
                self.assertEqual(pick_profile(f"This is a {keyword} task"), COMPLEX_PROFILE)

    def test_default_profile(self):
        """Test that tasks without matching keywords return the default profile."""
        self.assertEqual(pick_profile("This is a simple task"), DEFAULT_PROFILE)


if __name__ == "__main__":
    unittest.main()
