"""Auto-apply the Path 2 adaptor in Python subprocesses of adaptor-aware runs.

The root ``conftest.py`` prepends this directory and the project root to
``PYTHONPATH`` so fresh interpreters (e.g. consolidation's native-runtime
independence probe, which imports ``mmagent.videograph`` in a subprocess)
get the stubbed ``mmagent`` namespace instead of the pristine eager
``mmagent/__init__.py`` (which imports torch/insightface/cv2).
"""
try:
    import m3_adaptors

    m3_adaptors.apply()
except Exception:
    pass
