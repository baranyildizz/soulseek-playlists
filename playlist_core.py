"""Compatibility entry point: the engine lives in matching / slskd_api / audio / store / engine."""
from audio import audio_metadata, digest, validate_audio, write_track_number
from engine import Engine
from matching import (Candidate, Track, build_spec, detect_layout, parse_tracks, quality, quality_allowed,
                      quality_label, queries, score_candidate)
from slskd_api import SlskdApi as Api
from store import ACTIVE, Store, safe_name

__all__ = ['ACTIVE', 'Api', 'Candidate', 'Engine', 'Store', 'Track', 'audio_metadata', 'build_spec',
           'detect_layout', 'digest', 'parse_tracks', 'quality', 'quality_allowed', 'quality_label', 'queries',
           'safe_name', 'score_candidate', 'validate_audio', 'write_track_number']
