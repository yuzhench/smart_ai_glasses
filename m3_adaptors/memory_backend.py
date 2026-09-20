"""Select the native memory-construction implementation explicitly.

Injected as ``mmagent.memory_backend``. Unlike the EDITED module's top-level
``from .memory_processing_qwen import ...``, the names are resolved at call
time so runtime patches applied to ``mmagent.memory_processing_qwen`` are
always honored. The terra/gemini environment branches of the EDITED module
are intentionally dropped.
"""


def generate_memories(*args, **kwargs):
    from mmagent.memory_processing_qwen import generate_memories as impl
    return impl(*args, **kwargs)


def process_memories(*args, **kwargs):
    from mmagent.memory_processing_qwen import process_memories as impl
    return impl(*args, **kwargs)
