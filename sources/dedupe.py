"""
Property Package Deduplication Engine.
Deduplicates packages across search channels:
- Same Lot + Same Design = Duplicate (keep single entry with newest date_checked)
- Same Lot + Different Design = Keep both only if meaningfully different in layout/price.
"""

from typing import Any, Callable, Dict, List, Optional


class DedupeEngine:
    @classmethod
    def deduplicate(cls, raw_packages: List[Dict[str, Any]],
                    lot_key: Optional[Callable[[Dict[str, Any]], Any]] = None,
                    prefer: Optional[Callable[[Dict[str, Any]], Any]] = None
                    ) -> List[Dict[str, Any]]:
        """The packages with each (lot_address, house_design, price) kept once.

        lot_key (7 Oct 2026): what makes two packages the same LOT, added to the key. The
        address a stored-stock card carries is only "Melbourne VIC" when its line names no
        street or lot, so on the address alone two units of one building at one price were
        merged and one of them was never shown. api/_candidates.dedupe_packages passes
        same_lot_key; a live crawl, whose packages carry real addresses, passes nothing and
        is deduplicated exactly as before.

        prefer (7 Oct 2026): which package of a key to keep -- the one it ranks highest, in
        the place where the key first appeared (the first one on a tie). Without it the
        FIRST is kept, whatever its date: on the 6 Oct database a lot read that night was
        merged into its own capture from August, which a guard then hid as stale, so
        neither was shown (Hermitage Springridge lot 1307, Tomorrow Homes 157 Hancock Way).
        api/_candidates.dedupe_packages keeps the freshest verified capture, as this
        module's own header always said.
        """
        seen_keys = {}
        unique_packages = []

        for pkg in raw_packages:
            lot = str(pkg.get('lot_address', '')).strip().lower()
            design = str(pkg.get('house_design', '')).strip().lower()
            price = float(pkg.get('advertised_package_price', 0))

            dedupe_key = f"{lot}||{design}||{price}"
            if lot_key is not None:
                dedupe_key = (dedupe_key, lot_key(pkg))

            if dedupe_key not in seen_keys:
                seen_keys[dedupe_key] = len(unique_packages)
                unique_packages.append(pkg)
            elif prefer is not None:
                at = seen_keys[dedupe_key]
                if prefer(pkg) > prefer(unique_packages[at]):
                    unique_packages[at] = pkg

        return unique_packages
