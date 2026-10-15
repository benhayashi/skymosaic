"""
Automated Obstacle Detection (Trees, Light Poles, Antennas, Buildings)
Analyzes camera frames to identify vertical and contrasting foreground obstructions
against the sky dome and outputs normalized polygon coordinates.
"""

from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np


def detect_sky_obstacles(
    img: np.ndarray,
    min_area_px: int = 400,
    max_area_fraction: float = 0.75,
    approx_epsilon_px: float = 3.5,
    max_obstacles: int = 12
) -> List[Dict[str, Any]]:
    """
    Detects static foreground obstacles (trees, foliage, poles, structures) standing in front of the sky.
    
    Args:
        img: Input BGR image (H, W, 3)
        min_area_px: Minimum contour area in pixels to qualify as an obstacle
        max_area_fraction: Maximum fraction of total image area (prevents masking entire sky)
        approx_epsilon_px: Epsilon for cv2.approxPolyDP contour polygon simplification
        max_obstacles: Maximum number of distinct obstacles to return
        
    Returns:
        List of dicts: [
            {
                "id": str,
                "label": str,  # e.g. "Light Pole / Vertical Object" or "Tree / Foliage Structure"
                "polygon": List[[x_norm, y_norm], ...],
                "area_fraction": float,
                "aspect_ratio": float,
                "bbox": [x, y, w, h]
            }, ...
        ]
    """
    if img is None or img.size == 0:
        return []

    h, w = img.shape[:2]
    total_area = float(w * h)

    # 1. Convert to grayscale and blur slightly to suppress fine camera sensor noise
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (7, 7), 2.0)

    # 2. Extract dark structures (typical of trees, poles against daylight/twilight sky)
    # Using Otsu thresholding + adaptive Gaussian thresholding
    _, otsu_thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adapt_thresh = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 5
    )

    # 3. Detect high-frequency texture (tree foliage, pole edges) via Sobel gradients
    sobelx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    sobely = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = cv2.magnitude(sobelx, sobely)
    grad_norm = cv2.normalize(grad_mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, grad_thresh = cv2.threshold(grad_norm, 35, 255, cv2.THRESH_BINARY)

    # Combine dark foreground with edge gradients
    combined = cv2.bitwise_or(otsu_thresh, grad_thresh)
    combined = cv2.bitwise_and(combined, adapt_thresh)

    # Morphological closing to connect tree branches and thin poles into solid components
    kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel_close, iterations=2)

    # Morphological opening to eliminate tiny salt-and-pepper noise
    kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel_open, iterations=1)

    # 4. Find external contours
    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    obstacles: List[Dict[str, Any]] = []

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area_px or (area / total_area) > max_area_fraction:
            continue

        # Get bounding box and aspect ratio
        bx, by, bw, bh = cv2.boundingRect(cnt)
        aspect_ratio = float(bh) / max(1.0, float(bw))

        # Simplify contour to a smooth polygon
        poly_pts = cv2.approxPolyDP(cnt, approx_epsilon_px, closed=True)
        if len(poly_pts) < 3:
            continue

        # Normalize coordinates between 0.0 and 1.0
        normalized_poly: List[List[float]] = []
        for pt in poly_pts:
            px = float(pt[0][0]) / float(w)
            py = float(pt[0][1]) / float(h)
            normalized_poly.append([round(max(0.0, min(1.0, px)), 4), round(max(0.0, min(1.0, py)), 4)])

        # Classify structure type
        if aspect_ratio >= 2.5:
            label = "Vertical Pole / Mast"
        elif aspect_ratio <= 0.4 and by + bh >= int(h * 0.85):
            label = "Ground / Horizon Horizon"
        else:
            label = "Tree / Foliage Obstacle"

        obstacles.append({
            "id": f"obs_{len(obstacles) + 1}",
            "label": f"{label} ({round(area / total_area * 100, 1)}% FOV)",
            "polygon": normalized_poly,
            "area_fraction": round(area / total_area, 4),
            "aspect_ratio": round(aspect_ratio, 2),
            "bbox": [bx, by, bw, bh]
        })

    # Sort largest obstacle first, limit to max_obstacles
    obstacles.sort(key=lambda o: o["area_fraction"], reverse=True)
    return obstacles[:max_obstacles]
