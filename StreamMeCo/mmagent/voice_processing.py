# Copyright (2025) Bytedance Ltd. and/or its affiliates

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import base64
import struct
import json
import os
import logging
import math
import urllib.parse
import urllib.request
import torchaudio
import torch
from io import BytesIO
from speakerlab.process.processor import FBank

from pydub import AudioSegment
from .prompts import prompt_audio_segmentation
from .utils import chat_api
from .utils.chat_api import generate_messages, get_response
from .utils.general import validate_and_fix_json, normalize_embedding
import io

processing_config = json.load(open("configs/processing_config.json"))

MAX_RETRIES = processing_config["max_retries"]

# Speaker embeddings are CAM++ only. The checkpoint loads lazily on first use
# so importing this module stays cheap and does not require a GPU or the file.
feature_extractor = FBank(80, sample_rate=16000, mean_nor=True)
embedding_model = None


def _get_embedding_model():
    global embedding_model
    if embedding_model is None:
        from speakerlab.models.campplus.DTDNN import CAMPPlus
        checkpoint = os.environ.get(
            "CAMPLUS_CHECKPOINT",
            processing_config.get(
                "speaker_embedding_checkpoint",
                "models/camplus/campplus_cn_en_common.pt",
            ),
        )
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        embedding_model = CAMPPlus(feat_dim=80, embedding_size=192)
        embedding_model.load_state_dict(state)
        embedding_model.to(torch.device('cuda'))
        embedding_model.eval()
    return embedding_model

def _asr_timestamp(seconds, round_up=False):
    seconds = max(0, float(seconds or 0))
    total_seconds = math.ceil(seconds) if round_up else math.floor(seconds)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _asr_segments_from_words(words):
    segments = []
    current = None
    for word in words or []:
        text = (word.get("punctuated_word") or word.get("word") or word.get("text") or "").strip()
        if not text:
            continue
        speaker = word.get("speaker")
        start = float(word.get("start", 0))
        end = float(word.get("end", start))
        if current is None or (speaker is not None and speaker != current["speaker"]):
            if current is not None:
                segments.append(current)
            current = {"speaker": speaker, "start": start, "end": end, "words": [text]}
        else:
            current["end"] = end
            current["words"].append(text)
    if current is not None:
        segments.append(current)
    return [
        {
            "start_time": _asr_timestamp(segment["start"]),
            "end_time": _asr_timestamp(segment["end"], round_up=True),
            "asr": " ".join(segment["words"]),
            "speaker": segment["speaker"],
        }
        for segment in segments
    ]


def _normalize_transcription(response):
    """Normalize Deepgram or OpenRouter ASR into timed speaker segments."""
    utterances = response.get("results", {}).get("utterances", [])
    if utterances:
        return [
            {
                "start_time": _asr_timestamp(item.get("start")),
                "end_time": _asr_timestamp(item.get("end"), round_up=True),
                "asr": (item.get("transcript") or item.get("text") or "").strip(),
                "speaker": item.get("speaker"),
            }
            for item in utterances
            if (item.get("transcript") or item.get("text") or "").strip()
        ]
    segments = response.get("segments", [])
    if segments:
        return [
            {
                "start_time": _asr_timestamp(item.get("start")),
                "end_time": _asr_timestamp(item.get("end"), round_up=True),
                "asr": (item.get("text") or item.get("transcript") or "").strip(),
                "speaker": item.get("speaker"),
            }
            for item in segments
            if (item.get("text") or item.get("transcript") or "").strip()
        ]
    if response.get("words"):
        return _asr_segments_from_words(response["words"])
    channels = response.get("results", {}).get("channels", [])
    words = []
    if channels:
        alternatives = channels[0].get("alternatives", [])
        if alternatives:
            words = alternatives[0].get("words", [])
    return _asr_segments_from_words(words)


def transcribe_with_asr_provider(audio_data, audio_format="wav", timeout=180):
    """Transcribe + diarize audio bytes with processing_config["asr_provider"].
    """
    provider_name = processing_config.get("asr_provider")
    if not provider_name:
        raise ValueError("processing_config['asr_provider'] is not set")
    model_config = chat_api.config[provider_name]
    api_key = os.environ.get(model_config.get("api_key_env", "")) or model_config.get("api_key")
    if not api_key:
        raise ValueError(f"missing API key for ASR provider '{provider_name}'")
    base_url = model_config["base_url"].rstrip("/")
    provider = model_config.get("provider")
    if provider == "deepgram":
        params = {
            "model": model_config.get("model", "nova-3"),
            "smart_format": "true",
            "utterances": "true",
            "language": model_config.get("language", "multi"),
            "diarize_model": model_config.get("diarize_model", "latest"),
        }
        request = urllib.request.Request(
            base_url + "/v1/listen?" + urllib.parse.urlencode(params),
            data=audio_data,
            headers={"Authorization": f"Token {api_key}",
                     "Content-Type": f"audio/{audio_format}"},
        )
    elif provider == "openrouter":
        payload = {
            "model": model_config.get("model", "microsoft/mai-transcribe-2"),
            "input_audio": {
                "data": base64.b64encode(audio_data).decode("ascii"),
                "format": audio_format,
            },
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
            "provider": {
                "only": [model_config.get("upstream_provider", "azure")],
                "options": {"azure": {"diarization": {"enabled": True}}},
            },
        }
        request = urllib.request.Request(
            base_url + "/audio/transcriptions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
    else:
        raise ValueError(f"unsupported ASR provider: {provider_name}")
    retries = int(model_config.get("retries", 2))
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return _normalize_transcription(json.load(response))
        except Exception as error:
            last_error = error
            logger.warning(f"ASR provider '{provider_name}' attempt {attempt + 1} failed: {error}")
    raise Exception(f"ASR provider '{provider_name}' failed: {last_error}")


def get_embedding(wav):

    def load_wav(wav_file, obj_fs=16000):
        wav, fs = torchaudio.load(wav_file)
        if fs != obj_fs:
            wav, fs = torchaudio.sox_effects.apply_effects_tensor(
                wav, fs, effects=[['rate', str(obj_fs)]]
            )
        if wav.shape[0] > 1:
            wav = wav[0, :].unsqueeze(0)
        return wav
    
    def compute_embedding(wav_file, save=True):
        wav = load_wav(wav_file)
        feat = feature_extractor(wav).unsqueeze(0).to(torch.device('cuda'))
        with torch.no_grad():
            embedding = _get_embedding_model()(feat).detach().squeeze(0).cpu().numpy()
        return embedding

    return compute_embedding(wav)

@torch.no_grad()
def generate(wav):
    wav = base64.b64decode(wav)
    wav_file = BytesIO(wav)
    emb = get_embedding(wav_file)
    return emb

@torch.no_grad()
def get_audio_embeddings(audio_segments):
    res = []
    for wav in audio_segments:
        completion = generate(wav.decode("utf-8"))
        bytes_data = struct.pack('f' * len(completion), *completion)
        res.append(bytes_data)
        
    return res


# Configure logging
logger = logging.getLogger(__name__)


def process_voices(video_graph, base64_audio, base64_video, save_path, preprocessing=[]):
    def get_audio_segments(base64_audio, dialogs):
        # Decode base64 audio into bytes
        audio_data = base64.b64decode(base64_audio)
        
        # Create BytesIO object to hold audio data
        audio_io = io.BytesIO(audio_data)
        audio = AudioSegment.from_wav(audio_io)
        
        audio_segments = []
        for start_time, end_time in dialogs: 
            try:
                start_min, start_sec = map(int, start_time.split(':'))
                end_min, end_sec = map(int, end_time.split(':'))
            except ValueError:
                audio_segments.append(None)
                continue

            if (start_min < 0 or start_sec < 0 or start_sec >= 60) or (end_min < 0 or end_sec < 0 or end_sec >= 60):
                audio_segments.append(None)
                continue

            start_time_msec = (start_min * 60 + start_sec) * 1000
            end_time_msec = (end_min * 60 + end_sec) * 1000

            if start_time_msec >= end_time_msec:
                audio_segments.append(None)
                continue

            # Extract segment
            if end_time_msec > len(audio):  # AudioSegment uses milliseconds
                audio_segments.append(None)
                continue
            
            segment = audio[start_time_msec:end_time_msec]
        
            # Export segment to bytes buffer
            with io.BytesIO() as segment_buffer:
                segment.export(segment_buffer, format='wav')
                segment_buffer.seek(0)
                audio_segments.append(base64.b64encode(segment_buffer.getvalue()))
        
        return audio_segments

    def diarize_audio(base64_video, filter=None, base64_audio=None):
        if processing_config.get("asr_provider"):
            # Configured ASR provider (e.g. deepgram) instead of VLM segmentation.
            asrs = transcribe_with_asr_provider(base64.b64decode(base64_audio))
        else:
            input = [
                {
                    "type": "video_base64/mp4",
                    "content": base64_video.decode("utf-8"),
                },
                {
                    "type": "text",
                    "content": prompt_audio_segmentation,
                },
            ]
            messages = generate_messages(input)
            model = processing_config.get("diarization_model", "gemini-3.8-flash")
            asrs = None
            for i in range(MAX_RETRIES):
                # response, _ = get_response_with_retry(model, messages, timeout=30)
                response, _ = get_response(model, messages, timeout=30)
                asrs = validate_and_fix_json(response)
                if asrs is not None:
                    break
            if asrs is None:
                raise Exception("Failed to diarize audio")

        for asr in asrs:
            start_min, start_sec = map(int, asr["start_time"].split(':'))
            end_min, end_sec = map(int, asr["end_time"].split(':'))
            asr["duration"] = (end_min * 60 + end_sec) - (start_min * 60 + start_sec)
            
        asrs = [asr for asr in asrs if filter(asr)]

        return asrs

    def get_normed_audio_embeddings(audios):
        """
        Get normalized audio embeddings for a list of base64 audio strings
        
        Args:
            base64_audios (list): List of base64 encoded audio strings
            
        Returns:
            list: List of normalized audio embeddings
        """
        audio_segments = [audio["audio_segment"] for audio in audios]
        embeddings = get_audio_embeddings(audio_segments)
        normed_embeddings = [normalize_embedding(embedding) for embedding in embeddings]
        for audio, embedding in zip(audios, normed_embeddings):
            audio["embedding"] = embedding
        return audios

    def create_audio_segments(base64_audio, asrs):
        dialogs = [(asr["start_time"], asr["end_time"]) for asr in asrs]
        audio_segments = get_audio_segments(base64_audio, dialogs)
        for asr, audio_segment in zip(asrs, audio_segments):
            asr["audio_segment"] = audio_segment

        return asrs

    def filter_duration_based(audio):
        min_duration = processing_config["min_duration_for_audio"]
        return audio["duration"] >= min_duration
    
    def update_videograph(video_graph, audios):
        id2audios = {}
        
        for audio in audios:
            audio_info = {
                "embeddings": [audio["embedding"]],
                "contents": [audio["asr"]]
            }
            matched_nodes = video_graph.search_voice_nodes(audio_info)
            if len(matched_nodes) > 0:
                matched_node = matched_nodes[0][0]
                video_graph.update_node(matched_node, audio_info)
                audio["matched_node"] = matched_node
            else:
                matched_node = video_graph.add_voice_node(audio_info)
                audio["matched_node"] = matched_node
                
            if matched_node not in id2audios:
                id2audios[matched_node] = []
            id2audios[matched_node].append(audio)

        return id2audios

    if not base64_audio:
        return {}

    # Check if intermediate results exist
    try:
        with open(save_path, "r") as f:
            audios = json.load(f)
        for audio in audios:
            audio["audio_segment"] = audio["audio_segment"].encode("utf-8")
    except Exception as e:
        asrs = diarize_audio(base64_video, filter=filter_duration_based, base64_audio=base64_audio)
        audios = create_audio_segments(base64_audio, asrs)
        audios = [audio for audio in audios if audio["audio_segment"] is not None]

        if len(audios) > 0:
            audios = get_normed_audio_embeddings(audios)

        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        with open(save_path, "w") as f:
            for audio in audios:
                audio["audio_segment"] = audio["audio_segment"].decode("utf-8")
            json.dump(audios, f)
            for audio in audios:
                audio["audio_segment"] = audio["audio_segment"].encode("utf-8")
        
        logger.info(f"Write voice detection results to {save_path}")
    
    if "voice" in preprocessing:
        return
    
    if len(audios) == 0:
        return {}

    id2audios = update_videograph(video_graph, audios)

    return id2audios
if __name__ == "__main__":
    pass
