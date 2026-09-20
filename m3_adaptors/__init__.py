"""Path 2 interface layer between ``consolidation`` and a pristine StreamMeCo tree.

This package delivers all consolidation-adaptor behavior by monkey-patching
the pristine StreamMeCo/M3-agent classes and modules at runtime; no file under
``StreamMeCo/`` or ``consolidation/`` is modified.

``apply()`` (reader-side, CPU-safe):

* stubs the ``mmagent`` and ``mmagent.utils`` packages so the pristine eager
  ``__init__.py`` files (torch/insightface/cv2 bound) never execute;
* injects the standalone adaptor modules into ``sys.modules`` under their
  ``mmagent`` names, loaded with ``importlib.util.spec_from_file_location`` so
  their own relative imports resolve under the stubbed namespace unchanged;
* extends the pristine ``mmagent.utils.chat_api`` with the batched-embedding
  and transcription functions the adaptor modules need;
* monkey-patches ``videograph``, ``retrieve``, ``memory_processing`` and
  ``memory_processing_qwen`` (numpy/sklearn/matplotlib/PIL/openai only).

``apply_writer()`` additionally patches ``voice_processing``,
``m3_agent.memorization_memory_graphs`` and ``streammeco``; those import
torch-dependent modules and only apply where the full writer dependencies
(and model checkpoints) are installed.
"""
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

_PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _PACKAGE_DIR.parent
PRISTINE = PROJECT_ROOT / 'StreamMeCo'

_applied = False
_writer_applied = False


def _stub_package(name, path):
    module = ModuleType(name)
    module.__path__ = [str(path)]
    module.__package__ = name
    module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    module.__spec__.submodule_search_locations = [str(path)]
    sys.modules[name] = module
    return module


def _inject(name, filename):
    """Load an adaptor module under its pristine ``mmagent`` name."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _PACKAGE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE executing: the module's own top-level relative imports
    # (e.g. asr_cache -> .utils.asr_selection/.utils.chat_api) resolve through
    # sys.modules against the stubbed namespace.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        del sys.modules[name]
        raise
    parent_name, _, child = name.rpartition('.')
    if parent_name and parent_name in sys.modules:
        setattr(sys.modules[parent_name], child, module)
    return module


def apply():
    """Install the reader-side adaptor. CPU-safe and idempotent."""
    global _applied
    if _applied:
        return
    if 'mmagent' not in sys.modules:
        _stub_package('mmagent', PRISTINE / 'mmagent')
    if 'mmagent.utils' not in sys.modules:
        _stub_package('mmagent.utils', PRISTINE / 'mmagent' / 'utils')
    if str(PRISTINE) not in sys.path:
        sys.path.insert(0, str(PRISTINE))
    previous = Path.cwd()
    try:
        # Pristine modules read configs/*.json relative to the CWD at import
        # time (same behavior as consolidation.native.m3_module).
        os.chdir(PRISTINE)
        _inject('cloud_http', 'cloud_http.py')
        _inject('mmagent.utils.asr_selection', 'asr_selection.py')
        import mmagent.utils.chat_api as chat_api
        from . import chat_api_ext
        chat_api_ext.apply(chat_api)
        _inject('mmagent.utils.asr_resilience', 'asr_resilience.py')
        _inject('mmagent.asr_cache', 'asr_cache.py')
        _inject('mmagent.speaker_mapping', 'speaker_mapping.py')
        _inject('mmagent.character_identity', 'identity.py')
        _inject('mmagent.clip_audit', 'audit.py')
        _inject('mmagent.consolidation_evidence', 'evidence.py')
        # Lazy stand-in so pristine memory_processing_qwen imports without torch.
        _inject('mmagent.utils.chat_qwen', 'chat_qwen_lazy.py')
        # Reader-side patches (numpy/sklearn/matplotlib/PIL/openai only).
        from .patches import videograph, retrieve, memory_processing, memory_processing_qwen
        videograph.apply()
        retrieve.apply()
        memory_processing.apply()
        memory_processing_qwen.apply()
        _inject('mmagent.memory_backend', 'memory_backend.py')
    finally:
        os.chdir(previous)
    _applied = True


def apply_writer():
    """Install the writer-side adaptor (requires the full model dependencies)."""
    global _writer_applied
    if _writer_applied:
        return
    apply()
    previous = Path.cwd()
    try:
        os.chdir(PRISTINE)
        from .patches import voice_processing, memorization, streammeco
        voice_processing.apply()
        memorization.apply()
        try:
            streammeco.apply()
        except Exception as exc:
            import warnings
            warnings.warn(f'm3_adaptors: streammeco patch skipped: {exc}')
    finally:
        os.chdir(previous)
    _writer_applied = True
