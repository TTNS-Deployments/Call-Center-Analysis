"""Production-like settings for QA only: the app's own settings, with STATIC_ROOT moved out of the
source tree so `collectstatic` does not write into Code/webapp/staticfiles."""
import os
from pathlib import Path

from config.settings import *  # noqa: F401,F403

STATIC_ROOT = Path(os.environ["QA_STATIC_ROOT"])

# The app decides static-file storage from its own STATIC_ROOT; mirror a deployed app (after collectstatic).
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
WHITENOISE_USE_FINDERS = WHITENOISE_AUTOREFRESH = False
