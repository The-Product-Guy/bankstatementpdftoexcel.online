#!/usr/bin/env python3
"""Deterministic backend-contract tests for PaddleOCRProcessor."""
from PIL import Image
import numpy as np


def _polygon(left=10, top=20, right=110, bottom=40):
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def test_rapidocr_is_primary_and_normalizes_result(monkeypatch):
    from parsers import paddleocr_processor as module

    calls = []

    class FakeRapidOCR:
        def __call__(self, image):
            calls.append(image)
            return (
                [
                    [np.asarray(_polygon()), "Statement Date", np.float32(0.97)],
                    [_polygon(130, 20, 220, 40), "06/01/2026", 0.91],
                    [_polygon(), "", 0.99],
                    [[1, 2], "malformed polygon", 0.99],
                ],
                {"total": 0.01},
            )

    monkeypatch.setattr(module, "get_rapidocr", lambda: FakeRapidOCR())
    monkeypatch.setattr(
        module,
        "get_paddleocr",
        lambda: (_ for _ in ()).throw(AssertionError("Paddle fallback was used")),
    )

    processor = module.PaddleOCRProcessor()
    results = processor.process_image(Image.new("L", (240, 80)))

    assert len(calls) == 1
    assert calls[0].shape == (80, 240, 3)
    assert results == [
        {
            "text": "Statement Date",
            "confidence": float(np.float32(0.97)),
            "bbox": (10.0, 20.0, 110.0, 40.0),
            "bbox_polygon": [
                [10.0, 20.0],
                [110.0, 20.0],
                [110.0, 40.0],
                [10.0, 40.0],
            ],
        },
        {
            "text": "06/01/2026",
            "confidence": 0.91,
            "bbox": (130.0, 20.0, 220.0, 40.0),
            "bbox_polygon": [
                [130.0, 20.0],
                [220.0, 20.0],
                [220.0, 40.0],
                [130.0, 40.0],
            ],
        },
    ]


def test_paddle_v3_predict_is_used_when_rapidocr_has_no_results(monkeypatch):
    from parsers import paddleocr_processor as module

    class EmptyRapidOCR:
        def __call__(self, image):
            return None, [0.01, 0.01, 0.01]

    class FakePaddleV3:
        def predict(self, image):
            assert image.shape == (60, 180, 3)
            return [{
                "rec_texts": ["Date", "Balance"],
                "rec_scores": [0.98, 0.87],
                "dt_polys": [_polygon(), _polygon(120, 20, 175, 40)],
            }]

    monkeypatch.setattr(module, "get_rapidocr", lambda: EmptyRapidOCR())
    monkeypatch.setattr(module, "get_paddleocr", lambda: FakePaddleV3())

    results = module.PaddleOCRProcessor().process_image(Image.new("RGB", (180, 60)))

    assert [item["text"] for item in results] == ["Date", "Balance"]
    assert [item["confidence"] for item in results] == [0.98, 0.87]
    assert [item["bbox"] for item in results] == [
        (10.0, 20.0, 110.0, 40.0),
        (120.0, 20.0, 175.0, 40.0),
    ]


def test_paddle_v2_ocr_is_used_when_rapidocr_fails(monkeypatch):
    from parsers import paddleocr_processor as module

    class BrokenRapidOCR:
        def __call__(self, image):
            raise RuntimeError("simulated ONNX failure")

    class FakePaddleV2:
        def __init__(self):
            self.cls_values = []

        def ocr(self, image, cls=True):
            self.cls_values.append(cls)
            return [[
                [_polygon(), ("Description", 0.96)],
                [_polygon(120, 20, 175, 40), ("Amount", 0.89)],
            ]]

    paddle = FakePaddleV2()
    monkeypatch.setattr(module, "get_rapidocr", lambda: BrokenRapidOCR())
    monkeypatch.setattr(module, "get_paddleocr", lambda: paddle)

    results = module.PaddleOCRProcessor().process_image(Image.new("RGB", (180, 60)))

    assert paddle.cls_values == [True]
    assert results == [
        {
            "text": "Description",
            "confidence": 0.96,
            "bbox": (10, 20, 110, 40),
            "bbox_polygon": _polygon(),
        },
        {
            "text": "Amount",
            "confidence": 0.89,
            "bbox": (120, 20, 175, 40),
            "bbox_polygon": _polygon(120, 20, 175, 40),
        },
    ]


def test_table_structure_engine_remains_independent_of_rapidocr(monkeypatch):
    from parsers import paddleocr_processor as module

    class FakeTableEngine:
        def __call__(self, image):
            return [{
                "type": "table",
                "bbox": [1, 2, 30, 40],
                "res": {"html": "<table></table>", "cell_bbox": [[1, 2, 3, 4]]},
            }]

    monkeypatch.setattr(module, "get_table_engine", lambda: FakeTableEngine())
    processor = module.PaddleOCRProcessor(use_table_structure=True)

    assert processor.detect_table_structure(Image.new("L", (40, 50))) == [{
        "bbox": [1, 2, 30, 40],
        "html": "<table></table>",
        "cells": [[1, 2, 3, 4]],
    }]
