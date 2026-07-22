"""
Abstract PropertySource Base Interface.
All search channels (E-Agent, Builder Portals, Drive Ingest, Proxima, REA/Domain) inherit from this interface.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class PropertySource(ABC):
    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Name of the property source channel."""
        pass

    @abstractmethod
    def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Executes property search based on client brief filters.
        Returns a list of raw candidate package dictionaries.
        """
        pass

    @abstractmethod
    def verify(self, package: Dict[str, Any]) -> Dict[str, Any]:
        """
        Verifies live listing availability & price accuracy.
        Returns verification result status.
        """
        pass
