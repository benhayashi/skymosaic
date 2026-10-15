import os
import tempfile

# Keep API tests away from the user's real data/config.json
os.environ.setdefault("SKYMOSAIC_CONFIG", os.path.join(tempfile.mkdtemp(prefix="skymosaic_test_"), "config.json"))
