from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


@dataclass
class QualityMetrics:
    sharpness: float
    glare_ratio: float
    dark_ratio: float
    edge_signal: float
    mean: float


def _calc_sharpness(gray: np.ndarray) -> float:
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    return float(lap.var())


def _calc_edge_signal(gray: np.ndarray) -> float:
    h, w = gray.shape[:2]
    x0 = int(w * 0.55)
    if x0 >= w - 2:
        x0 = 0
    region = gray[:, x0:]
    gx = cv2.Sobel(region, cv2.CV_32F, 1, 0, ksize=3)
    return float(np.mean(np.abs(gx)))


def measure_quality(img: np.ndarray) -> QualityMetrics:
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    total = max(1, gray.size)
    mean = float(np.mean(gray))
    glare_ratio = float(np.count_nonzero(gray > 240) / total)
    dark_ratio = float(np.count_nonzero(gray < 20) / total)
    sharpness = _calc_sharpness(gray)
    edge_signal = _calc_edge_signal(gray)
    return QualityMetrics(
        sharpness=sharpness,
        glare_ratio=glare_ratio,
        dark_ratio=dark_ratio,
        edge_signal=edge_signal,
        mean=mean,
    )


def measure_quality_from_path(path: Path) -> Optional[QualityMetrics]:
    if not path.exists():
        return None
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    return measure_quality(img)

