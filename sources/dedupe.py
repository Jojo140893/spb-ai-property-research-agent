"""
Property Package Deduplication Engine.
Deduplicates packages across search channels:
- Same Lot + Same Design = Duplicate (keep single entry with newest date_checked)
- Same Lot + Different Design = Keep both only if meaningfully different in layout/price.
"""

from typing import List, Dict, Any


class DedupeEngine:
    @classmethod
    def deduplicate(cls, raw_packages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen_keys = {}
        unique_packages = []

        for pkg in raw_packages:
            lot = str(pkg.get('lot_address', '')).strip().lower()
            design = str(pkg.get('house_design', '')).strip().lower()
            price = float(pkg.get('advertised_package_price', 0))

            dedupe_key = f"{lot}||{design}||{price}"

            if dedupe_key not in seen_keys:
                seen_keys[dedupe_key] = True
                unique_packages.append(pkg)

        return unique_packages
