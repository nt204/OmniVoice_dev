# Audit notes for the original notebook

## Defects found in the uploaded notebook
1. `_find_first_existing_path()` is called but never defined. The notebook can fail when it tries to locate fallback voice preset files.
2. The notebook is Gradio-first and Colab-path-first. It is not structured as a Runpod Serverless worker.
3. Voice-clone reference audio is not normalized for production:
   - no mandatory mono conversion
   - no enforced sample-rate normalization
   - no silence trimming
   - no max-duration cap before `create_voice_clone_prompt()`
4. The notebook assumes local files or Google Drive paths. A serverless endpoint usually needs JSON inputs such as file path, URL, or base64.
5. There is no strict request validation layer before inference.
6. Cleanup is not designed for long-running workers that process many jobs.

## What the packaged version fixes
- Replaces the notebook UI with a Runpod `handler.py`
- Adds input validation via `runpod.serverless.utils.rp_validator.validate`
- Adds cleanup for temp files between requests
- Supports 3 reference-audio input modes:
  - mounted file path
  - downloadable URL
  - base64 payload
- Normalizes reference audio before cloning:
  - mono
  - 24 kHz
  - silence trim
  - peak normalize
  - minimum/maximum duration guard
- Keeps your Vietnamese/Myanmar emotion + ad prosody logic
- Supports `clone`, `design`, and `auto` modes
- Saves output wav to persistent storage and can optionally return base64 audio in the API response
