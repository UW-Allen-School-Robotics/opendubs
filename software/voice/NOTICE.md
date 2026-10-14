# Voice: credits and licences of the speech models and libraries

The voice service's own code is the repository's code, under the repository's licence. It uses the
third-party models and libraries below. **None of their weights are in this repository or in any
image built from it:** `fetch_models.py` downloads the pinned files (`models.lock.json`) onto each
robot at provision time, and copies this file beside them.

**English only, no GPL.** The project team decided on 2026-10-14 that the voice ships no GPL
component. Kokoro's other languages take their pronunciation from espeak-ng (GPL-3.0-or-later)
through phonemizer (GPL-3.0-or-later), so neither is installed, and `image/check_no_gpl.py`
refuses any image that contains them. Adding a language other than English is a separate,
explicit decision to ship GPL code (`notes/workplan-voice.md`, V-O4).

Licences were read from each project's model card, repository or package metadata on 2026-10-14.
This is a record, not legal advice.

## Models (downloaded at provision time)

### Parakeet-TDT-0.6B-v2, speech-to-text

- **By** NVIDIA. Model card: <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2>
- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0),
  <https://creativecommons.org/licenses/by/4.0/legalcode.en>. The card states: "GOVERNING TERMS:
  Use of this model is governed by the CC-BY-4.0 license", and "ready for commercial/non-commercial
  use".
- **Language:** English.
- **Modified:** yes. The files used are an **int8-quantised ONNX conversion** of NVIDIA's model,
  made by the k2-fsa project for sherpa-onnx and published at
  <https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8>, tagged CC BY 4.0
  (pinned at commit `1ab9323565ddb038682214b292f588070a538ce2`). This project uses that
  conversion unmodified.

### Kokoro-82M v1.0, text-to-speech

- **By** hexgrad. Model card: <https://huggingface.co/hexgrad/Kokoro-82M> (pinned at commit
  `f3ff3571791e39611d31c381e3a41a3af07b4987`). Its architecture is StyleTTS 2 (MIT,
  <https://github.com/yl4579/StyleTTS2>) with an ISTFTNet decoder; base model
  `yl4579/StyleTTS2-LJSpeech`.
- **Licence:** Apache License 2.0, for the weights and the voices.
- **Language used here:** American English (`a`), voices `af_heart` and `am_michael`.
- **Training data under CC BY**, as listed on the card in its "Creative Commons Attribution"
  section:
  - Koniwa `tnc` (<https://github.com/koniwa/koniwa>), under 1 hour, CC BY 3.0
    (<https://creativecommons.org/licenses/by/3.0/deed.ja>).
  - SIWIS (<https://datashare.ed.ac.uk/handle/10283/2353>), under 11 hours, CC BY 4.0
    (<https://datashare.ed.ac.uk/bitstream/handle/10283/2353/license_text>).

### graphemes_to_phonemes_en_us, the English pronunciation fallback

- **By** PeterReid: <https://huggingface.co/PeterReid/graphemes_to_phonemes_en_us> (pinned at commit
  `a5631b285d18d59483c32c0c3379cb9fac924f4b`). misaki uses it for words that are not in its
  dictionary, in place of espeak-ng.
- **Licence:** Apache License 2.0.

## Libraries (installed in the image, pinned in `image/requirements-*.txt`)

| library | used for | licence |
|---|---|---|
| sherpa-onnx | runs Parakeet | Apache-2.0, <https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE> |
| onnxruntime (inside sherpa-onnx) | ONNX inference | MIT, <https://github.com/microsoft/onnxruntime/blob/main/LICENSE> |
| kokoro | runs Kokoro | Apache-2.0, <https://github.com/hexgrad/kokoro/blob/main/LICENSE> |
| misaki | English grapheme-to-phoneme (G2P) | Apache-2.0, <https://github.com/hexgrad/misaki/blob/main/LICENSE> |
| spaCy, en_core_web_sm 3.8.0 | misaki's tokenizer and tagger | MIT, <https://github.com/explosion/spaCy/blob/master/LICENSE> |
| num2words | misaki spells out numbers | **LGPL-2.1**, <https://github.com/savoirfairelinux/num2words/blob/master/COPYING>. Used unmodified as its own installed package, with its licence; reviewed and allowed in `check_no_gpl.py` |
| transformers, huggingface-hub, tokenizers, safetensors | model loading | Apache-2.0 |
| PyTorch (CPU build) | Kokoro and the G2P fallback | BSD-3-Clause-style, <https://github.com/pytorch/pytorch/blob/main/LICENSE> |

## Deliberately excluded

| component | licence | why it would have come in |
|---|---|---|
| espeak-ng | GPL-3.0-or-later, <https://github.com/espeak-ng/espeak-ng> | the out-of-dictionary fallback, and every Kokoro language except English, Japanese and Mandarin |
| phonemizer, phonemizer-fork | GPL-3.0-or-later, <https://github.com/bootphon/phonemizer> | misaki's `[en]` extra, and kokoro-onnx |
| espeakng-loader | MIT wrapper; the wheel bundles the espeak-ng library (GPL) and its data, and declares no licence | misaki's `[en]` extra, and kokoro-onnx |
