# Voice Wrapper

OpenAI-compatible speech gateway for OmniVoice. LiteLLM routes `/v1/audio/speech`
requests here with `model: elina`; the wrapper resolves the requested `voice`
from `config.yml`, fetches and caches the static reference audio, calls
the internal OmniVoice inference service, and returns generated audio bytes.

Create `config.yml` from `config.yml.example` and set the real
SeaweedFS reference URL plus exact reference transcript.
