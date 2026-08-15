"""Services package for multi-asset beta."""

from services.multiasset.desk_service import MultiAssetDeskService
from services.multiasset.desks import DESKS, get_desk

__all__ = ["MultiAssetDeskService", "DESKS", "get_desk"]
