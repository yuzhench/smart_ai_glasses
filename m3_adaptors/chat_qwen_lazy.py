"""Lazy stand-in injected as ``mmagent.utils.chat_qwen``.

The pristine ``mmagent/utils/chat_qwen.py`` imports torch, transformers and
qwen_omni_utils at module load, which makes the pristine
``mmagent.memory_processing_qwen`` unimportable on CPU-only readers. This
module is injected under the ``mmagent.utils.chat_qwen`` name so the pristine
import succeeds; the real module is loaded from the pristine tree on first
actual use (writer environments with the full dependencies).
"""
import importlib.util
import sys
from pathlib import Path


def _real():
    module = sys.modules.get('mmagent.utils._chat_qwen_real')
    if module is None:
        parent = sys.modules['mmagent.utils']
        path = Path(parent.__path__[0]) / 'chat_qwen.py'
        spec = importlib.util.spec_from_file_location('mmagent.utils._chat_qwen_real', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def generate_messages(*args, **kwargs):
    return _real().generate_messages(*args, **kwargs)


def get_response(*args, **kwargs):
    return _real().get_response(*args, **kwargs)
