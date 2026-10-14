import datetime
from pathlib import Path
from typing import List, Optional
import cv2
import imageio
import numpy as np


class MediaGenerator:
    """
    Generates astronomical products (Keograms, Startrails, and Timelapses)
    from sequences of stitched AllSky frames.
    """

    @staticmethod
    def generate_keogram(
        image_paths: List[str],
        output_path: str,
        slice_width: int = 2
    ) -> Optional[str]:
        """
        Creates a Keogram by taking a vertical slice through Zenith for each image in chronological order.
        """
        if not image_paths:
            return None

        slices = []
        for p in image_paths:
            img = cv2.imread(str(p))
            if img is None:
                continue

            h, w = img.shape[:2]
            mid_x = w // 2
            half_w = max(1, slice_width // 2)
            col = img[:, mid_x - half_w : mid_x + half_w + (slice_width % 2)]
            # Average across the slice width to reduce noise
            avg_col = np.mean(col, axis=1, keepdims=True).astype(np.uint8)
            slices.append(avg_col)

        if not slices:
            return None

        keogram = np.concatenate(slices, axis=1)

        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_file), keogram)
        return str(out_file)

    @staticmethod
    def generate_startrails(
        image_paths: List[str],
        output_path: str,
        threshold: float = 0.05
    ) -> Optional[str]:
        """
        Generates a startrails image by computing the maximum pixel intensity across all frames.
        """
        if not image_paths:
            return None

        trails: Optional[np.ndarray] = None
        min_thresh_val = int(threshold * 255)

        for p in image_paths:
            img = cv2.imread(str(p))
            if img is None:
                continue

            if trails is None:
                trails = img.copy()
            else:
                # Maximum intensity projection
                trails = np.maximum(trails, img)

        if trails is None:
            return None

        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_file), trails)
        return str(out_file)

    @staticmethod
    def generate_timelapse(
        image_paths: List[str],
        output_path: str,
        fps: int = 25,
        target_size: Optional[tuple] = None
    ) -> Optional[str]:
        """
        Assembles an MP4 timelapse video from a list of image paths using imageio-ffmpeg.
        """
        if not image_paths:
            return None

        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        # Use imageio to write mp4 video
        writer = imageio.get_writer(
            str(out_file),
            fps=fps,
            codec="libx264",
            format="FFMPEG",
            quality=8
        )

        frame_count = 0
        try:
            for p in image_paths:
                img = cv2.imread(str(p))
                if img is None:
                    continue

                if target_size is not None and (img.shape[1], img.shape[0]) != target_size:
                    img = cv2.resize(img, target_size, interpolation=cv2.INTER_AREA)

                # cv2 is BGR, imageio expects RGB
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                writer.append_data(rgb)
                frame_count += 1
        finally:
            writer.close()

        if frame_count == 0:
            if out_file.exists():
                out_file.unlink()
            return None

        return str(out_file)
