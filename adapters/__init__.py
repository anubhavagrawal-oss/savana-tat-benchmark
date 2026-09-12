"""Adapter registry. Add new brands here."""
from .base import Adapter, ParseError, parse_window   # noqa: F401
from .savana import Savana

# brand key (as used in config.json and on the CLI) -> adapter class
REGISTRY = {
    "savana": Savana,
    # --- competitor brands: implement then uncomment ---------------------
    # Copy adapters/_template.py, read DISCOVERY.md, and confirm the ToS /
    # legal sign-off before enabling any of these on a schedule.
    # "myntra":  Myntra,
    # "nykaa":   Nykaa,          # Nykaa and Nykaa Fashion are SEPARATE
    # "nykaafashion": NykaaFashion,
    # "ajio":    Ajio,
    # "meesho":  Meesho,
}


def get(name: str) -> Adapter:
    try:
        return REGISTRY[name]()
    except KeyError:
        raise SystemExit(
            f"no adapter registered for '{name}'.\n"
            f"registered: {', '.join(sorted(REGISTRY)) or '(none)'}\n"
            f"to add one: copy adapters/_template.py, read DISCOVERY.md, "
            f"then register it in adapters/__init__.py"
        )
