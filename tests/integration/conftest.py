"""Integration test configuration.

Patches Path.rename to fall back to shutil.move when a cross-device rename is
attempted.  This is needed when the project lives on a FUSE/sshfs mount (a
different device from the native VM filesystem where pytest stores its tmp
directory).
"""

import errno
import shutil
from pathlib import Path

_original_rename = Path.rename


def _rename_with_cross_device_fallback(self: Path, target) -> Path:
    try:
        return _original_rename(self, target)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            dest = Path(target)
            shutil.copy2(str(self), str(dest))
            self.unlink()
            return dest
        raise


Path.rename = _rename_with_cross_device_fallback  # type: ignore[method-assign]
