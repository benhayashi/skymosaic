from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import numpy as np


class BaseVisionPlugin(ABC):
    """Abstract base class for SkyView AI vision and environmental plugins."""

    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    def process_frame(self, frame: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyzes an input frame and returns structured detection/classification output.
        metadata contains observer location, UTC time, sun altitude, and sky state.
        """
        pass
