"""Run the normal bench entry point while archiving construction requests/results.

Recording only: delegates model calls and pipeline behavior unchanged. Full
multimodal requests are gzip-compressed; Markdown omits image bytes for readability.
"""
import gzip
import json
import os
from pathlib import Path


def main():
    from bench import backends
    from bench.__main__ import main as bench_main

    output = Path(os.environ['BENCH_RECORD_DIR']).resolve()
    calls = output / 'construction_calls'
    calls.mkdir(parents=True, exist_ok=True)
    report = output / 'memory_construction_calls.md'
    report.write_text('# Memory construction calls\n\nActual request text and returned outputs, in call order. '
                      'Image bytes are retained in the compressed request files. '
                      'A clip can have multiple calls if parsing retries.\n')
    original = backends.chat_completion
    count = 0

    def recorded(backend, messages, **kwargs):
        nonlocal count
        count += 1
        stem = f'call_{count:04d}'
        payload = {'model': backend['model'], 'messages': messages}
        if backend.get('temperature') is not None:
            payload['temperature'] = backend['temperature']
        with gzip.open(calls / (stem + '_request.json.gz'), 'wt', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False)
        with report.open('a') as handle:
            handle.write(f'\n## {stem}\n\n### Request\n\n')
            for message in messages:
                handle.write(f"**{message['role']}**\n\n")
                content = message['content']
                if isinstance(content, str):
                    handle.write(content + '\n\n')
                else:
                    for part in content:
                        if part['type'] == 'text':
                            handle.write(part['text'] + '\n\n')
                        else:
                            handle.write('[Image retained in compressed request]\n\n')
        result = original(backend, messages, **kwargs)
        (calls / (stem + '_response.json')).write_text(json.dumps(
            {'model': backend['model'], 'content': result[0], 'total_tokens': result[1]},
            ensure_ascii=False, indent=2) + '\n')
        with report.open('a') as handle:
            handle.write('### Response\n\n```json\n' + result[0] + '\n```\n')
        return result

    backends.chat_completion = recorded
    bench_main()


if __name__ == '__main__':
    main()
