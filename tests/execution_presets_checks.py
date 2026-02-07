#!/usr/bin/env python3
"""
Regression checks for Phase 2 execution presets.
Run with:
    python -m unittest tests.execution_presets_checks -v
"""
import unittest

from parsers.universal_parser import create_universal_parser


class TestExecutionPresets(unittest.TestCase):
    def test_local_low_mem_preset(self):
        parser = create_universal_parser(execution_preset="local-low-mem")
        cfg = parser.config
        self.assertEqual(cfg.execution_preset, "local-low-mem")
        self.assertFalse(cfg.use_paddleocr)
        self.assertFalse(cfg.use_img2table)
        self.assertFalse(cfg.use_llm)
        self.assertEqual(cfg.dpi, 120)
        self.assertFalse(cfg.preprocess_images)
        self.assertFalse(cfg.adaptive_preprocess)

    def test_prod_high_accuracy_preset(self):
        parser = create_universal_parser(execution_preset="prod-high-accuracy")
        cfg = parser.config
        self.assertEqual(cfg.execution_preset, "prod-high-accuracy")
        self.assertTrue(cfg.use_paddleocr)
        self.assertTrue(cfg.use_img2table)
        self.assertEqual(cfg.dpi, 200)
        self.assertTrue(cfg.preprocess_images)
        self.assertTrue(cfg.adaptive_preprocess)

    def test_explicit_overrides_take_precedence(self):
        parser = create_universal_parser(
            execution_preset="local-low-mem",
            use_paddleocr=True,
            dpi=180,
        )
        cfg = parser.config
        self.assertEqual(cfg.execution_preset, "local-low-mem")
        self.assertTrue(cfg.use_paddleocr)
        self.assertEqual(cfg.dpi, 180)


if __name__ == "__main__":
    unittest.main()
