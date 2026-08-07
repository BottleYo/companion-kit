"""可移植的虚拟陪伴核心。"""

from .config import ConfigError, PersonaProfile, VisualProfile, load_profile
from .contracts import Decision, DecisionKind, HostCapabilities, HostClass, RequestEnvelope
from .image_provider import ImageProviderRoute, ProviderSelection, select_image_provider
from .kernel import CompanionKernel
from .relationship import (
    RelationshipEvent,
    RelationshipPolicy,
    RelationshipProjection,
    RelationshipState,
    project_relationship,
)
from .state_store import RelationshipStore

__version__ = "0.7.0-dev.6"

__all__ = [
    "CompanionKernel",
    "ConfigError",
    "Decision",
    "DecisionKind",
    "HostCapabilities",
    "HostClass",
    "ImageProviderRoute",
    "PersonaProfile",
    "ProviderSelection",
    "RelationshipEvent",
    "RelationshipPolicy",
    "RelationshipProjection",
    "RelationshipState",
    "RelationshipStore",
    "RequestEnvelope",
    "VisualProfile",
    "__version__",
    "load_profile",
    "project_relationship",
    "select_image_provider",
]
