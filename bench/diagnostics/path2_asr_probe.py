"""Read-only ASR reproduction; preserve raw output and every filtering decision."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import time
import wave

import httpx
import numpy as np
from moviepy import VideoFileClip

ROOT = Path('/opt/streammeco/run/StreamMeCo-consolidation')
OUT = ROOT / 'bench/results/path2_voice_diagnostic'
OUT.mkdir(exist_ok=True)
config = json.loads((ROOT / 'StreamMeCo/configs/api_config.json').read_text())['deepgram-asr']
key = os.environ.get(config.get('api_key_env', '')) or config.get('api_key')
assert key, 'Missing configured ASR credential'
source = ast.parse((ROOT / 'm3_adaptors/chat_api_ext.py').read_text())
functions = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in {'_timestamp','_segments_from_words','_normalize_transcription'}]
import math
ns = {'math': math}
exec(compile(ast.Module(body=functions, type_ignores=[]), '<normalizer>', 'exec'), ns)
clips = [
 ('new_71040', '/opt/streammeco/data/jake/media/DAY1_A1_JAKE_19440000.mp4'),
 ('new_71100', '/opt/streammeco/data/jake/media/DAY1_A1_JAKE_19450000.mp4'),
 ('new_71820', '/opt/streammeco/data/jake/media/DAY1_A1_JAKE_19570000.mp4'),
 ('old_first', '/opt/streammeco/data/egolife_day1/DAY1_A1_JAKE_11094208.mp4'),
]
summary = []
for name, path in clips:
 target = OUT / (name + '.wav')
 with VideoFileClip(path) as video:
  video.audio.write_audiofile(str(target), codec='pcm_s16le', fps=16000, logger=None)
 data = target.read_bytes()
 with wave.open(io.BytesIO(data)) as w:
  duration = w.getnframes()/w.getframerate()
  pcm = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(float).reshape(-1,w.getnchannels())
  audio_info = {'duration':duration,'channels':w.getnchannels(),'sample_rate':w.getframerate(), 'rms_by_channel':np.sqrt(np.mean(pcm**2,axis=0)).tolist(),'sha256':hashlib.sha256(data).hexdigest()}
 for version in ['latest','v2']:
  started = time.perf_counter()
  with httpx.Client(timeout=180) as client:
   response = client.post(config['base_url'].rstrip('/')+'/v1/listen', params={'model':config.get('model','nova-3'),'smart_format':'true','utterances':'true','language':config.get('language','multi'),'diarize_model':version}, headers={'Authorization':'Token '+key,'Content-Type':'audio/wav'},content=data)
  raw = response.json()
  (OUT / f'{name}_{version}_raw.json').write_text(json.dumps(raw,ensure_ascii=False,indent=2))
  response.raise_for_status()
  segments = ns['_normalize_transcription'](raw)
  rows = []
  for s in segments:
   def seconds(t):
    m,s = map(int,t.split(':')); return 60*m+s
   start,end = seconds(s['start_time']),seconds(s['end_time'])
   reason = 'duration_below_2s' if end-start<2 else 'invalid_audio_bounds' if not 0<=start<end<=duration else 'retained'
   rows.append(dict(s,normalized_duration=end-start,decision=reason))
  record = {'clip':name,'path':path,'diarize_model':version,'audio':audio_info,'http_status':response.status_code,'elapsed_s':time.perf_counter()-started,'metadata':raw.get('metadata'),'raw_utterances':len(raw.get('results',{}).get('utterances',[])),'segments':rows}
  summary.append(record)
  (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
  print(json.dumps({'clip':name,'version':version,'audio':audio_info,'raw_utterances':record['raw_utterances'],'segments':rows},ensure_ascii=False),flush=True)
print('PROBE_COMPLETE',flush=True)
