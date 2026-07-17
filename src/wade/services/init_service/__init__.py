"""Init service — project initialization, update, and removal.

Split into focused submodules. All public names (and the underscore helpers /
constants the test-suite and CLI import from the package root) are re-exported
here via each submodule's ``__all__`` for full backward compatibility. Existing
imports like ``from wade.services.init_service import init`` keep working.
"""

from wade.services.init_service.auth import *  # noqa: F403
from wade.services.init_service.commands import *  # noqa: F403
from wade.services.init_service.config_io import *  # noqa: F403
from wade.services.init_service.manifest import *  # noqa: F403
from wade.services.init_service.migrations import *  # noqa: F403
from wade.services.init_service.prompts_ai import *  # noqa: F403
from wade.services.init_service.prompts_setup import *  # noqa: F403
from wade.services.init_service.shell import *  # noqa: F403
