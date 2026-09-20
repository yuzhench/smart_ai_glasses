"""CLI bridge: ``python -m m3_adaptors.consolidation prepare|run|moss ...``.

Applies the Path 2 adaptor first; afterwards consolidation's
``m3_module('mmagent.character_identity')`` and friends resolve through
``sys.modules`` with zero changes to the consolidation package. ``sys.argv``
is preserved for consolidation's own argparse entry point.
"""
import m3_adaptors


def main():
    m3_adaptors.apply()
    from consolidation.__main__ import main as consolidation_main
    consolidation_main()


if __name__ == '__main__':
    main()
